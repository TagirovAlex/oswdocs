# Хранилище справочников маршрута согласования (службы, профили, этапы,
# состав этапа, бланки): таблицы ad_services/route_profiles/approval_stages/
# stage_assignees/route_profile_steps из миграции 0008 и blanks/blank_steps из
# миграций 0012/0014. Слой доступа — raw SQL
# (SQLAlchemy text()), без ORM-моделей; приложение отдаёт маршрут наружу
# функциями app.routing (подбор профиля и этапов), этот модуль только БД.
# Запись только в свои таблицы и audit_log; в AD/1С не пишем, users — только
# на чтение (карточка сотрудника и руководитель для owner_kind=manager_ad).
# Падение БД — RoutingUnavailable (роутер отвечает 503, не 500), как
# LinksUnavailable в link_store.py и GroupsCacheUnavailable в ad_groups_cache.py.
# Ошибка запроса (неизвестный/отключённый этап в set_profile_steps) — сразу
# HTTPException 422: это ошибка данных, а не хранилища, и текст должен быть
# один и тот же независимо от вызывающего эндпоинта.

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from fastapi import Depends, HTTPException
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings


class RoutingUnavailable(Exception):
    """Хранилище справочников маршрута (БД) недоступно — роутер отвечает 503, не 500."""


# Белые списки колонок для UPDATE: в SET попадает только то, что перечислено
# здесь (payload целиком в SQL не подставляется).
_SERVICE_FIELDS = ("dept_name", "status", "blank_kind", "route_profile_id")
_PROFILE_FIELDS = ("code", "name", "service_id", "active")
# Бланки (миграции 0012/0014): код уникален, макет — пресет печати office|line,
# header_html/footer_lines — шапка и подвал бланка (текст печати).
_BLANK_FIELDS = (
    "code",
    "name",
    "doc_type_code",
    "description",
    "layout",
    "active",
    "header_html",
    "footer_lines",
)
_STAGE_FIELDS = (
    "code",
    "title",
    "stage_lines",
    "owner_kind",
    "owner_group",
    "optional",
    "print_assignee",
    "require_comment",
    "active",
)
# Колонки jsonb: в UPDATE требуют явного CAST (:stage_lines).
_JSONB_FIELDS = ("stage_lines", "footer_lines")


def _int_or_none(value: object) -> int | None:
    """Целое из значения payload; нечисло/пусто — None (id этапа/порядок шага)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _approval_mode_or_default(value: object) -> str:
    """Режим шага бланка: parallel — любой из ответственных, иначе sequential
    («все ответственные»). Неизвестное значение и None — sequential: режим по
    умолчанию безопаснее (шаг не закроется чужой отметкой)."""
    text_value = str(value or "").strip().casefold()
    return "parallel" if text_value == "parallel" else "sequential"


def _executor_kind_or_default(value: object) -> str:
    """Вид исполнителя шага бланка: people/ad_group/manager_ad, всё прочее и пустое
    — people (согласующие назначает человек списком логинов)."""
    text_value = str(value or "").strip().casefold()
    if text_value in ("ad_group", "manager_ad"):
        return text_value
    return "people"


def _override_or_none(value: object) -> bool | None:
    """Переопределение флага шага профиля: None — брать значение из этапа."""
    if value is None:
        return None
    return bool(value)


def _stage_lines(value: object) -> list:
    """jsonb-список строк из БД: jsonb приходит списком, из строки JSON — разбираем,
    мусор — пустой список (колонка бланка не должна ронять маршрут).

    Используется для любых списков-колонок справочника: stage_lines этапа и шага
    бланка, footer_lines бланка, assignees шага бланка."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _logins(value: object) -> list[str]:
    """Логины согласующих из jsonb: строки без пустых и повторов (порядок прежний)."""
    logins: list[str] = []
    for item in _stage_lines(value):
        login = str(item).strip()
        if login and login not in logins:
            logins.append(login)
    return logins


def _rows_to_dicts(rows) -> list[dict]:
    """Строки БД -> список словарей (контракт наружу: list[dict])."""
    return [dict(row._mapping) for row in rows]


class DbRoutingStore:
    """Справочники маршрута в Postgres (миграция 0008). Ошибки БД оборачиваются
    в RoutingUnavailable (503). Транзакции: set_stage_assignees,
    set_profile_steps и правки справочников с аудитом — одной сессией с commit
    в конце."""

    _LIST_SERVICES = text(
        """
        SELECT id, dept_name, status, active_count, last_seen_at, blank_kind,
               route_profile_id
        FROM ad_services
        WHERE (CAST(:active_only AS BOOLEAN) = FALSE OR status = 'active')
        ORDER BY dept_name, id
        """
    )
    _UPSERT_SERVICE = text(
        """
        INSERT INTO ad_services (dept_name, status, active_count, last_seen_at, updated_at)
        VALUES (:dept_name, 'active', :active_count, :last_seen_at, now())
        ON CONFLICT (dept_name) DO UPDATE SET
            status = 'active',
            active_count = EXCLUDED.active_count,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = now()
        RETURNING id
        """
    )
    _INSERT_SERVICE = text(
        """
        INSERT INTO ad_services (dept_name, status, blank_kind, route_profile_id,
                                 updated_at, updated_by)
        VALUES (:dept_name, COALESCE(:status, 'active'), :blank_kind, :route_profile_id,
                now(), :actor)
        RETURNING id
        """
    )
    _SERVICE_BY_DEPT = text(
        """
        SELECT id, dept_name, status, active_count, last_seen_at, blank_kind,
               route_profile_id
        FROM ad_services
        WHERE lower(btrim(dept_name)) = lower(btrim(:dept_name))
        ORDER BY id
        LIMIT 1
        """
    )

    _LIST_PROFILES = text(
        """
        SELECT id, code, name, service_id, active
        FROM route_profiles
        WHERE (CAST(:active_only AS BOOLEAN) = FALSE OR active = TRUE)
        ORDER BY id
        """
    )
    _INSERT_PROFILE = text(
        """
        INSERT INTO route_profiles (code, name, service_id, active, updated_at, updated_by)
        VALUES (:code, :name, :service_id, COALESCE(:active, TRUE), now(), :actor)
        RETURNING id
        """
    )

    _LIST_STAGES = text(
        """
        SELECT id, code, title, stage_lines, owner_kind, owner_group, optional,
               print_assignee, require_comment, active, version
        FROM approval_stages
        WHERE (CAST(:active_only AS BOOLEAN) = FALSE OR active = TRUE)
        ORDER BY code, id
        """
    )
    _INSERT_STAGE = text(
        """
        INSERT INTO approval_stages (code, title, stage_lines, owner_kind, owner_group,
                                     optional, print_assignee, require_comment, active,
                                     updated_at, updated_by)
        VALUES (:code, :title, CAST(:stage_lines AS jsonb), COALESCE(:owner_kind, 'ad_group'),
                :owner_group, COALESCE(:optional, TRUE), COALESCE(:print_assignee, FALSE),
                COALESCE(:require_comment, FALSE), COALESCE(:active, TRUE), now(), :actor)
        RETURNING id
        """
    )

    # Шаги профиля вместе с этапом: pick_profile (app.routing) получает готовые
# записи этапов и не ходит в БД за справочником сам.
    _LIST_PROFILE_STEPS = text(
        """
        SELECT ps.id AS profile_step_id, ps.profile_id, ps.step_order,
               ps.optional_override, ps.require_comment_override,
               s.id AS stage_id, s.code AS stage_code, s.title AS stage_title,
               s.stage_lines, s.owner_kind, s.owner_group, s.optional,
               s.print_assignee, s.require_comment, s.active AS stage_active
        FROM route_profile_steps ps
        JOIN approval_stages s ON s.id = ps.stage_id
        WHERE ps.profile_id = :profile_id
        ORDER BY ps.step_order, s.id
        """
    )
    # Шаги ВСЕХ профилей одним запросом: админка грузит справочник целиком, а не
    # по запросу на профиль (был N+1 в GET /settings/routing/catalogs).
    _LIST_ALL_PROFILE_STEPS = text(
        """
        SELECT id AS profile_step_id, profile_id, stage_id, step_order,
               optional_override, require_comment_override
        FROM route_profile_steps
        ORDER BY profile_id, step_order, stage_id
        """
    )
    # Активные этапы из переданного списка — один SELECT на проверку состава
    # шагов профиля (этап вне маршрутов или несуществующий — 422).
    _ACTIVE_STAGE_IDS = text(
        "SELECT id FROM approval_stages WHERE active = TRUE AND id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    _DELETE_PROFILE_STEPS = text(
        "DELETE FROM route_profile_steps WHERE profile_id = :profile_id"
    )
    _INSERT_PROFILE_STEP = text(
        """
        INSERT INTO route_profile_steps (profile_id, stage_id, step_order,
                                         optional_override, require_comment_override,
                                         updated_at, created_by)
        VALUES (:profile_id, :stage_id, :step_order, :optional_override,
                :require_comment_override, now(), :actor)
        """
    )

    # Бланки (миграция 0012, поля шапки/подвала — 0014): step_count — счётчик из
    # подзапроса, а не join с blank_steps (одна выборка справочника, а не запрос
    # на каждый бланк).
    _LIST_BLANKS = text(
        """
        SELECT b.id, b.code, b.name, b.doc_type_code, b.description, b.layout,
               b.active, b.version, b.updated_at, b.updated_by,
               b.header_html, b.footer_lines,
               (SELECT count(*) FROM blank_steps bs WHERE bs.blank_id = b.id) AS step_count
        FROM blanks b
        WHERE (CAST(:active_only AS BOOLEAN) = FALSE OR b.active = TRUE)
        ORDER BY b.code, b.id
        """
    )
    # Бланк по id: step_count нужен выдаче заявки — пустой бланк выбрать нельзя.
    _BLANK_BY_ID = text(
        """
        SELECT id, code, name, doc_type_code, description, layout, active, version,
               updated_at, updated_by, header_html, footer_lines,
               (SELECT count(*) FROM blank_steps bs WHERE bs.blank_id = blanks.id)
                 AS step_count
        FROM blanks
        WHERE id = :blank_id
        """
    )
    _INSERT_BLANK = text(
        """
        INSERT INTO blanks (code, name, doc_type_code, description, layout, active,
                            header_html, footer_lines, updated_at, updated_by)
        VALUES (:code, :name, :doc_type_code, :description,
                COALESCE(:layout, 'office'), COALESCE(:active, TRUE), :header_html,
                CAST(:footer_lines AS jsonb), now(), :actor)
        RETURNING id
        """
    )
    # Шаги бланка — самостоятельные шаги (миграция 0014): свой текст и свой
    # исполнитель, join с этапом больше не нужен (stage_id — наследие, NULL).
    _LIST_BLANK_STEPS = text(
        """
        SELECT blank_id, step_order, title, stage_lines, executor_kind,
               assignees, owner_group, optional, require_comment, approval_mode
        FROM blank_steps
        WHERE blank_id = :blank_id
        ORDER BY step_order
        """
    )
    _DELETE_BLANK_STEPS = text("DELETE FROM blank_steps WHERE blank_id = :blank_id")
    _INSERT_BLANK_STEP = text(
        """
        INSERT INTO blank_steps (blank_id, step_order, title, stage_lines,
                                 executor_kind, assignees, owner_group, optional,
                                 require_comment, approval_mode, updated_at)
        VALUES (:blank_id, :step_order, :title, CAST(:stage_lines AS jsonb),
                :executor_kind, CAST(:assignees AS jsonb), :owner_group, :optional,
                :require_comment, :approval_mode, now())
        """
    )
    # Версия бланка растёт при замене состава шагов: blank_version из снимка
    # заявки должен отличать выданные составы.
    _BUMP_BLANK_VERSION = text(
        "UPDATE blanks SET version = version + 1, updated_at = now(), updated_by = :actor "
        "WHERE id = :blank_id"
    )

    _LIST_STAGE_ASSIGNEES = text(
        """
        SELECT sam, position_title, active
        FROM stage_assignees
        WHERE stage_id = :stage_id
          AND (CAST(:active_only AS BOOLEAN) = FALSE OR active = TRUE)
        ORDER BY sam
        """
    )
    _STAGE_ASSIGNEES_SAMS = text("SELECT sam FROM stage_assignees WHERE stage_id = :stage_id")
    _UPSERT_STAGE_ASSIGNEE = text(
        """
        INSERT INTO stage_assignees (stage_id, sam, position_title, active, updated_at, updated_by)
        VALUES (:stage_id, :sam, :position_title, TRUE, now(), :actor)
        ON CONFLICT (stage_id, sam) DO UPDATE SET
            position_title = EXCLUDED.position_title,
            active = TRUE,
            updated_at = now(),
            updated_by = EXCLUDED.updated_by
        """
    )
    _DEACTIVATE_STAGE_ASSIGNEE = text(
        """
        UPDATE stage_assignees
        SET active = FALSE, updated_at = now(), updated_by = :actor
        WHERE stage_id = :stage_id AND sam = :sam
        """
    )

    _USER_CARD = text(
        """
        SELECT sam, fio_full, dept_ad, title_ad, manager_dn
        FROM users
        WHERE lower(sam) = lower(:sam)
        LIMIT 1
        """
    )
    _HAS_MANAGER = text(
        """
        SELECT EXISTS (
            SELECT 1 FROM users
            WHERE lower(sam) = lower(:sam)
              AND manager_dn IS NOT NULL AND btrim(manager_dn) <> ''
        )
        """
    )
    _MANAGER_SAM_BY_DN = text(
        """
        SELECT sam
        FROM users
        WHERE lower(manager_dn) = lower(:manager_dn)
        ORDER BY sam
        LIMIT 1
        """
    )

    # Аудит справочников (append-only, как audit.py): только коды и счётчики,
    # без ПДн (логины сотрудников в details не пишем).
    _AUDIT_INSERT = text(
        """
        INSERT INTO audit_log (actor, action, entity, entity_id, details)
        VALUES (:actor, :action, :entity, :entity_id, CAST(:details AS jsonb))
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    @contextmanager
    def _session(self) -> Iterator[object]:
        """Сессия с обёрткой ошибок БД в RoutingUnavailable (503)."""
        try:
            with self._session_factory() as session:
                yield session
        except SQLAlchemyError as exc:
            raise RoutingUnavailable(
                f"Хранилище справочников маршрута недоступно: {exc}"
            ) from exc

    @staticmethod
    def _update_sql(table: str, fields: tuple[str, ...], extra: str = ""):
        """UPDATE из белого списка колонок (SET собирается по names, не по payload)."""
        sets = ", ".join(
            f"{col} = CAST(:{col} AS jsonb)" if col in _JSONB_FIELDS else f"{col} = :{col}"
            for col in fields
        )
        return text(
            f"UPDATE {table} SET {sets}, updated_at = now(), updated_by = :actor "
            f"WHERE id = :id{extra}"
        )

    @staticmethod
    def _payload(data: dict, fields: tuple[str, ...]) -> dict:
        """Значения полей из payload: jsonb — строкой JSON, булевы — как есть."""
        params: dict = {}
        for col in fields:
            value = data.get(col)
            params[col] = json.dumps(value, ensure_ascii=False) if col in _JSONB_FIELDS else value
        return params

    @staticmethod
    def _audit(session, actor: str, action: str, entity: str, entity_id: str, detail: str,
               extra: dict | None = None) -> None:
        """INSERT события в audit_log (append-only; без ПДн в details)."""
        details: dict = {"detail": detail}
        if extra:
            details.update(extra)
        session.execute(
            DbRoutingStore._AUDIT_INSERT,
            {
                "actor": actor,
                "action": action,
                "entity": entity,
                "entity_id": str(entity_id),
                "details": json.dumps(details, ensure_ascii=False),
            },
        )

    # --- Службы ---

    def list_services(self, active_only: bool = False) -> list[dict]:
        """Службы из справочника (по умолчанию — все, включая отключённые)."""
        with self._session() as session:
            rows = session.execute(self._LIST_SERVICES, {"active_only": active_only}).all()
        return _rows_to_dicts(rows)

    def upsert_service(
        self, dept_name: str, active_count: int, last_seen_at: datetime | None
    ) -> int:
        """Отметить службу как встреченную при синке (upsert по dept_name).

        Служба снова становится active: пришёл состав — значит служба живая.
        Возвращает id службы."""
        with self._session() as session:
            row = session.execute(
                self._UPSERT_SERVICE,
                {
                    "dept_name": dept_name,
                    "active_count": active_count,
                    "last_seen_at": last_seen_at,
                },
            ).first()
            session.commit()
        return int(row[0]) if row is not None else 0

    def service_by_dept(self, dept_name: str) -> dict | None:
        """Служба по имени подразделения (регистр/пробелы не различаются)."""
        with self._session() as session:
            row = session.execute(self._SERVICE_BY_DEPT, {"dept_name": dept_name}).first()
        return dict(row._mapping) if row is not None else None

    def create_service(self, data: dict) -> int:
        """Создать службу (справочник вручную) с аудитом. Возвращает id."""
        actor = str(data.get("actor") or "")
        params = self._payload(data, _SERVICE_FIELDS)
        with self._session() as session:
            row = session.execute(
                self._INSERT_SERVICE,
                {
                    "dept_name": params["dept_name"],
                    "status": params["status"],
                    "blank_kind": params["blank_kind"],
                    "route_profile_id": params["route_profile_id"],
                    "actor": actor,
                },
            ).first()
            service_id = int(row[0]) if row is not None else 0
            self._audit(
                session, actor, "service.create", "service", service_id,
                "служба создана", {"fields": sorted(_SERVICE_FIELDS)},
            )
            session.commit()
        return service_id

    def update_service(self, service_id: int, data: dict) -> None:
        """Правка службы: в SET только поля из белого списка (пустой payload — no-op)."""
        fields = tuple(col for col in _SERVICE_FIELDS if col in data)
        if not fields:
            return
        actor = str(data.get("actor") or "")
        params = self._payload(data, fields)
        params.update({"id": service_id, "actor": actor})
        with self._session() as session:
            session.execute(self._update_sql("ad_services", fields), params)
            self._audit(
                session, actor, "service.update", "service", service_id,
                "служба изменена", {"fields": sorted(fields)},
            )
            session.commit()

    # --- Профили маршрута ---

    def list_profiles(self, active_only: bool = False) -> list[dict]:
        """Профили маршрута; service_id is None — профиль по умолчанию."""
        with self._session() as session:
            rows = session.execute(self._LIST_PROFILES, {"active_only": active_only}).all()
        return _rows_to_dicts(rows)

    def create_profile(self, data: dict) -> int:
        """Создать профиль маршрута с аудитом. Возвращает id."""
        actor = str(data.get("actor") or "")
        params = self._payload(data, _PROFILE_FIELDS)
        with self._session() as session:
            row = session.execute(
                self._INSERT_PROFILE,
                {
                    "code": params["code"],
                    "name": params["name"],
                    "service_id": params["service_id"],
                    "active": params["active"],
                    "actor": actor,
                },
            ).first()
            profile_id = int(row[0]) if row is not None else 0
            self._audit(
                session, actor, "profile.create", "profile", profile_id,
                "профиль маршрута создан", {"fields": sorted(_PROFILE_FIELDS)},
            )
            session.commit()
        return profile_id

    def update_profile(self, profile_id: int, data: dict) -> None:
        """Правка профиля: в SET только поля из белого списка (пустой payload — no-op)."""
        fields = tuple(col for col in _PROFILE_FIELDS if col in data)
        if not fields:
            return
        actor = str(data.get("actor") or "")
        params = self._payload(data, fields)
        params.update({"id": profile_id, "actor": actor})
        with self._session() as session:
            session.execute(self._update_sql("route_profiles", fields), params)
            self._audit(
                session, actor, "profile.update", "profile", profile_id,
                "профиль маршрута изменён", {"fields": sorted(fields)},
            )
            session.commit()

    # --- Этапы маршрута ---

    def list_stages(self, active_only: bool = False) -> list[dict]:
        """Этапы из справочника; stage_lines всегда списком."""
        with self._session() as session:
            rows = session.execute(self._LIST_STAGES, {"active_only": active_only}).all()
        items = _rows_to_dicts(rows)
        for item in items:
            item["stage_lines"] = _stage_lines(item.get("stage_lines"))
        return items

    def create_stage(self, data: dict) -> int:
        """Создать этап с аудитом. Возвращает id."""
        actor = str(data.get("actor") or "")
        params = self._payload(data, _STAGE_FIELDS)
        with self._session() as session:
            row = session.execute(
                self._INSERT_STAGE,
                {
                    "code": params["code"],
                    "title": params["title"],
                    "stage_lines": params["stage_lines"],
                    "owner_kind": params["owner_kind"],
                    "owner_group": params["owner_group"],
                    "optional": params["optional"],
                    "print_assignee": params["print_assignee"],
                    "require_comment": params["require_comment"],
                    "active": params["active"],
                    "actor": actor,
                },
            ).first()
            stage_id = int(row[0]) if row is not None else 0
            self._audit(
                session, actor, "stage.create", "stage", stage_id,
                "этап создан", {"fields": sorted(_STAGE_FIELDS)},
            )
            session.commit()
        return stage_id

    def update_stage(self, stage_id: int, data: dict) -> None:
        """Правка этапа с bumps версии (version = version + 1) и аудитом."""
        fields = tuple(col for col in _STAGE_FIELDS if col in data)
        if not fields:
            return
        actor = str(data.get("actor") or "")
        params = self._payload(data, fields)
        params.update({"id": stage_id, "actor": actor})
        with self._session() as session:
            session.execute(
                self._update_sql("approval_stages", fields, extra=", version = version + 1"),
                params,
            )
            self._audit(
                session, actor, "stage.update", "stage", stage_id,
                "этап изменён", {"fields": sorted(fields)},
            )
            session.commit()

    def list_profile_steps(self, profile_id: int) -> list[dict]:
        """Шаги профиля вместе с этапами (для pick_profile): по step_order, id этапа."""
        with self._session() as session:
            rows = session.execute(
                self._LIST_PROFILE_STEPS, {"profile_id": profile_id}
            ).all()
        items = _rows_to_dicts(rows)
        for item in items:
            item["stage_lines"] = _stage_lines(item.get("stage_lines"))
        return items

    def list_all_profile_steps(self) -> list[dict]:
        """Шаги всех профилей одним запросом (справочник админки, без N+1).

        Строки формы route_profile_steps без колонок этапа: админке достаточно
        profile_id/stage_id/step_order и переопределений; join с этапом нужен
        только подбору маршрута (list_profile_steps по конкретному профилю)."""
        with self._session() as session:
            rows = session.execute(self._LIST_ALL_PROFILE_STEPS).all()
        return _rows_to_dicts(rows)

    def set_profile_steps(self, profile_id: int, items: list[dict], actor: str) -> None:
        """Замена состава шагов профиля одной транзакцией: прежние шаги удаляются,
        переданные — вставляются в порядке step_order (аудит profile.steps.update).

        items — список {stage_id, step_order, optional_override,
        require_comment_override}; optional_override/require_comment_override = None
        означают «взять из этапа». Этап вне маршрутов (active = FALSE) или
        несуществующий — 422 с перечнем: такой этап не попал бы ни в один
        маршрут. Уникальность step_order в профиле (UNIQUE в БД) проверяет
        схема запроса на границе — дубль порядка до сюда не доходит.
        """
        wanted: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            stage_id = _int_or_none(item.get("stage_id"))
            step_order = _int_or_none(item.get("step_order"))
            if not stage_id or not step_order or step_order < 1:
                continue
            wanted.append(
                {
                    "stage_id": stage_id,
                    "step_order": step_order,
                    "optional_override": _override_or_none(item.get("optional_override")),
                    "require_comment_override": _override_or_none(
                        item.get("require_comment_override")
                    ),
                }
            )
        stage_ids = sorted({item["stage_id"] for item in wanted})
        actor = str(actor or "")
        with self._session() as session:
            known: set[int] = set()
            if stage_ids:
                rows = session.execute(
                    self._ACTIVE_STAGE_IDS, {"ids": stage_ids}
                ).all()
                known = {int(row[0]) for row in rows}
            unknown = [stage_id for stage_id in stage_ids if stage_id not in known]
            if unknown:
                # 422 до DELETE: состав профиля остаётся прежним.
                raise HTTPException(
                    status_code=422,
                    detail="Шаги профиля: этапы не найдены или отключены: %s"
                    % ", ".join(str(stage_id) for stage_id in unknown),
                )
            session.execute(self._DELETE_PROFILE_STEPS, {"profile_id": profile_id})
            for item in sorted(wanted, key=lambda row: row["step_order"]):
                session.execute(
                    self._INSERT_PROFILE_STEP,
                    {"profile_id": profile_id, "actor": actor, **item},
                )
            self._audit(
                session, actor, "profile.steps.update", "profile", profile_id,
                "шаги профиля обновлены",
                {"steps": len(wanted), "stage_ids": stage_ids},
            )
            session.commit()

    # --- Бланки (миграция 0012: blanks/blank_steps) ---

    def list_blanks(self, active_only: bool = False) -> list[dict]:
        """Бланки из справочника (по умолчанию — все, включая отключённые);
        step_count — число шагов бланка, footer_lines всегда списком."""
        with self._session() as session:
            rows = session.execute(self._LIST_BLANKS, {"active_only": active_only}).all()
        items = _rows_to_dicts(rows)
        for item in items:
            item["footer_lines"] = _stage_lines(item.get("footer_lines"))
        return items

    def blank_by_id(self, blank_id: int) -> dict | None:
        """Бланк по id со счётчиком шагов (step_count); отсутствует — None."""
        with self._session() as session:
            row = session.execute(self._BLANK_BY_ID, {"blank_id": blank_id}).first()
        if row is None:
            return None
        blank = dict(row._mapping)
        blank["footer_lines"] = _stage_lines(blank.get("footer_lines"))
        return blank

    def create_blank(self, data: dict) -> int:
        """Создать бланк с аудитом (blank.create). Возвращает id.

        Дубль code — 409 на границе (роутер сверяется со списком бланков), здесь
        нарушение UNIQUE приходит обёрнутым в RoutingUnavailable (503)."""
        actor = str(data.get("actor") or "")
        params = self._payload(data, _BLANK_FIELDS)
        with self._session() as session:
            row = session.execute(
                self._INSERT_BLANK,
                {
                    "code": params["code"],
                    "name": params["name"],
                    "doc_type_code": params["doc_type_code"],
                    "description": params["description"],
                    "layout": params["layout"],
                    "active": params["active"],
                    "header_html": params["header_html"],
                    "footer_lines": params["footer_lines"],
                    "actor": actor,
                },
            ).first()
            blank_id = int(row[0]) if row is not None else 0
            self._audit(
                session, actor, "blank.create", "blank", blank_id,
                "бланк создан", {"fields": sorted(_BLANK_FIELDS)},
            )
            session.commit()
        return blank_id

    def update_blank(self, blank_id: int, data: dict) -> None:
        """Правка бланка: в SET только поля из белого списка (пустой payload — no-op)."""
        fields = tuple(col for col in _BLANK_FIELDS if col in data)
        if not fields:
            return
        actor = str(data.get("actor") or "")
        params = self._payload(data, fields)
        params.update({"id": blank_id, "actor": actor})
        with self._session() as session:
            session.execute(self._update_sql("blanks", fields), params)
            self._audit(
                session, actor, "blank.update", "blank", blank_id,
                "бланк изменён", {"fields": sorted(fields)},
            )
            session.commit()

    def list_blank_steps(self, blank_id: int) -> list[dict]:
        """Собственные шаги бланка по step_order (без join с этапом): выдаче
        заявке нужен текст шага и вид исполнителя, stage_lines и assignees всегда
        списками."""
        with self._session() as session:
            rows = session.execute(
                self._LIST_BLANK_STEPS, {"blank_id": blank_id}
            ).all()
        items = _rows_to_dicts(rows)
        for item in items:
            item["stage_lines"] = _stage_lines(item.get("stage_lines"))
            item["assignees"] = _logins(item.get("assignees"))
        return items

    def set_blank_steps(self, blank_id: int, items: list[dict], actor: str) -> None:
        """Замена состава шагов бланка одной транзакцией: прежние шаги удаляются,
        переданные — вставляются в порядке step_order, версия бланка растёт
        (аудит blank.steps.update).

        items — список {step_order, title, stage_lines, executor_kind, assignees,
        owner_group, optional, require_comment, approval_mode}; approval_mode =
        None — «все ответственные» (sequential, как до разделения режимов).
        Шаг самостоятельный: этап не проверяется (его у шага нет), а состав
        согласующих и группа AD проверяет схема запроса на границе — здесь 422
        не бывает. Битые элементы (без порядка или названия) пропускаются.
        Уникальность порядка в бланке (PK (blank_id, step_order)) проверяет
        схема запроса на границе — дубль порядка до сюда не доходит.
        """
        wanted: list[dict] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            step_order = _int_or_none(item.get("step_order"))
            title = str(item.get("title") or "").strip()
            if not step_order or step_order < 1 or not title:
                continue
            wanted.append(
                {
                    "step_order": step_order,
                    "title": title,
                    # jsonb-колонки — строкой JSON (драйвер отдаёт в БД список, а
                    # не ARRAY: CAST(:x AS jsonb) ждёт текст).
                    "stage_lines": json.dumps(
                        _stage_lines(item.get("stage_lines")), ensure_ascii=False
                    ),
                    "executor_kind": _executor_kind_or_default(item.get("executor_kind")),
                    "assignees": json.dumps(
                        _logins(item.get("assignees")), ensure_ascii=False
                    ),
                    "owner_group": str(item.get("owner_group") or "").strip() or None,
                    "optional": bool(item.get("optional")),
                    "require_comment": bool(item.get("require_comment")),
                    "approval_mode": _approval_mode_or_default(item.get("approval_mode")),
                }
            )
        actor = str(actor or "")
        with self._session() as session:
            session.execute(self._DELETE_BLANK_STEPS, {"blank_id": blank_id})
            for item in sorted(wanted, key=lambda row: row["step_order"]):
                session.execute(
                    self._INSERT_BLANK_STEP, {"blank_id": blank_id, **item}
                )
            session.execute(
                self._BUMP_BLANK_VERSION, {"blank_id": blank_id, "actor": actor}
            )
            self._audit(
                session, actor, "blank.steps.update", "blank", blank_id,
                "шаги бланка обновлены", {"steps": len(wanted)},
            )
            session.commit()

    # --- Состав этапа (owner_kind = stage_roster) ---

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        """Состав этапа: по умолчанию только активные (can_user_act/печать)."""
        with self._session() as session:
            rows = session.execute(
                self._LIST_STAGE_ASSIGNEES,
                {"stage_id": stage_id, "active_only": active_only},
            ).all()
        return _rows_to_dicts(rows)

    def set_stage_assignees(self, stage_id: int, items: list[dict], actor: str) -> None:
        """Замена состава этапа одной транзакцией: отсутствующие деактивируются,
        переданные — добавляются/реактивируются (sam без ПДн в аудите)."""
        wanted: dict[str, str | None] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            sam = str(item.get("sam") or "").strip()
            if not sam:
                continue
            title = item.get("position_title")
            wanted[sam] = str(title).strip() if title and str(title).strip() else None
        actor = str(actor or "")
        with self._session() as session:
            current = [
                str(row[0])
                for row in session.execute(
                    self._STAGE_ASSIGNEES_SAMS, {"stage_id": stage_id}
                ).all()
            ]
            deactivated = 0
            for sam in current:
                if sam not in wanted:
                    session.execute(
                        self._DEACTIVATE_STAGE_ASSIGNEE,
                        {"stage_id": stage_id, "sam": sam, "actor": actor},
                    )
                    deactivated += 1
            activated = 0
            for sam, title in wanted.items():
                session.execute(
                    self._UPSERT_STAGE_ASSIGNEE,
                    {
                        "stage_id": stage_id,
                        "sam": sam,
                        "position_title": title,
                        "actor": actor,
                    },
                )
                activated += 1
            self._audit(
                session, actor, "stage.assignees.update", "stage", stage_id,
                "состав этапа обновлён",
                {"activated": activated, "deactivated": deactivated},
            )
            session.commit()

    # --- Карточка сотрудника и руководитель (только чтение users) ---

    def user_card(self, sam: str) -> dict | None:
        """Карточка сотрудника из зеркала users (sam, ФИО, отдел, должность, DN
        руководителя). Отсутствует — None."""
        with self._session() as session:
            row = session.execute(self._USER_CARD, {"sam": sam}).first()
        return dict(row._mapping) if row is not None else None

    def is_manager(self, sam: str) -> bool:
        """Зафиксирован ли за сотрудником руководитель (нужен этапам
        owner_kind=manager_ad: без manager_dn этап не назначается)."""
        with self._session() as session:
            value = session.execute(self._HAS_MANAGER, {"sam": sam}).scalar()
        return bool(value)

    def manager_sam_by_dn(self, manager_dn: str) -> str | None:
        """Логин сотрудника, у которого этот DN записан руководителем.

        Отдельной колонки dn в users нет, поэтому точного отображения DN -> sam
        не существует; метод отдаёт локальный best-effort из зеркала users, а
        точный резолв DN -> sam делает ридер AD (get_user_by_dn)."""
        if not manager_dn or not manager_dn.strip():
            return None
        with self._session() as session:
            row = session.execute(
                self._MANAGER_SAM_BY_DN, {"manager_dn": manager_dn.strip()}
            ).first()
        return str(row[0]) if row is not None else None


_db_routing_store: DbRoutingStore | None = None


def get_routing_store(settings: Settings = Depends(get_settings)) -> DbRoutingStore:
    """Боевое хранилище справочников маршрута (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется подменой сессии (см. api/tests/test_routing_store.py),
    как get_links_store/get_groups_cache_store.
    """
    global _db_routing_store
    if _db_routing_store is None:
        _db_routing_store = DbRoutingStore(settings.DATABASE_URL)
    return _db_routing_store
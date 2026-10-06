# Хранилище справочников маршрута согласования (службы, профили, этапы,
# состав этапа): таблицы ad_services/route_profiles/approval_stages/
# stage_assignees/route_profile_steps из миграции 0008. Слой доступа — raw SQL
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
_JSONB_FIELDS = ("stage_lines",)


def _int_or_none(value: object) -> int | None:
    """Целое из значения payload; нечисло/пусто — None (id этапа/порядок шага)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _override_or_none(value: object) -> bool | None:
    """Переопределение флага шага профиля: None — брать значение из этапа."""
    if value is None:
        return None
    return bool(value)


def _stage_lines(value: object) -> list:
    """stage_lines из БД: jsonb приходит списком, из строки JSON — разбираем,
    мусор — пустой список (колонка бланка не должна ронять маршрут)."""
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
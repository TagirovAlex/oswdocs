# Хранилище связок 1С-AD (волна 4): интерфейс LinksStore + in-memory реализация
# для офлайн-тестов/локали и DbLinksStore (Postgres, таблица link_1c_ad из 0001)
# для стенда. Эндпоинты link.py получают хранилище зависимостью get_links_store()
# (как get_settings_store в settings_routes.py); падение БД — LinksUnavailable
# -> роутер отвечает 503 (не 500); офлайн-тесты переопределяют InMemoryLinksStore.

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings

if TYPE_CHECKING:
    from .link import LinkRecord


class LinksUnavailable(Exception):
    """Хранилище связок (БД) недоступно — роутер отвечает 503, а не 500."""


class LinksStore(Protocol):
    """Интерфейс хранилища связок: единый для in-memory и Postgres."""

    def find(self, key: str) -> LinkRecord | None:
        """Связка по составному ключу enterprise|base_code|tab_num либо None."""
        ...

    def find_by_sam(self, sam: str) -> list[LinkRecord]:
        """Все связки логина (для добора поиска по логину)."""
        ...

    def save(self, record: LinkRecord) -> None:
        """Сохранить/перезаписать факт связки (upsert по составному ключу)."""
        ...

    def replace_discrepancies(self, rows: list[dict]) -> int:
        """Перезаписать выдачу расхождений последним проходом (миграция 0010).

        Полная замена таблицы: проход знает всю картину (в том числе, что ранее
        было расхождением и стало связанным), поэтому накопление строк вводило бы
        в заблуждение. Возврат — число записанных строк."""
        ...

    def list_discrepancies(
        self,
        reason: str | None = None,
        query: str = "",
        only_open: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Страница расхождений (фильтры: причина, подстрока ФИО/таб.№/логина).

        Каждая строка: key/enterprise/base_code/tab_num/fio/reason/ad_sam/ad_fio/
        ad_dept/ad_title/one_c_dept/one_c_position/recommended/detected_at/
        resolved_at. Порядок — сначала те, что можно подтвердить (recommended), и
        свежие по времени обнаружения."""
        ...

    def count_discrepancies(
        self, reason: str | None = None, query: str = "", only_open: bool = True
    ) -> int:
        """Сколько расхождений подходит под фильтры (те же, что у list_discrepancies)."""
        ...

    def counts_by_reason(self) -> dict:
        """Счётчики расхождений по причине: {reason: кол-во} (включая закрытые)."""
        ...

    def get_discrepancies(self, keys: list[str]) -> list[dict]:
        """Строки расхождений по ключам (порядок и состав — как сохранили)."""
        ...

    def resolve_discrepancies(self, keys: list[str]) -> int:
        """Пометить расхождения закрытыми (resolved_at=now()) после подтверждения."""
        ...

    def ensure_targets(self, base: dict | None, employee: dict | None, user: dict | None) -> None:
        """Зеркало ссылок связки в НАШИХ таблицах (one_c_bases/employee_base_map/users).

        Нужно для внешних ключей link_1c_ad: sam -> users(sam), (enterprise,
        base_code, tab_num) -> employee_base_map, employee_base_map.base_code ->
        one_c_bases(code). Связка может ссылаться на любого сотрудника 1С и
        учётку AD, а не только на уже занесённых в таблицы. В AD/1С при этом
        НЕ пишется — строки создаются у нас (только своя БД)."""
        ...


class InMemoryLinksStore:
    """Офлайн-хранилище связок (словарь процесса), интерфейс LinksStore."""

    def __init__(self) -> None:
        self._links: dict[str, LinkRecord] = {}
        # Зеркала ensure_targets (для проверок тестов): ключи как в БД.
        self._bases: dict[str, dict] = {}  # код базы
        self._employees: dict[str, dict] = {}  # enterprise|base_code|tab_num
        self._users: dict[str, dict] = {}  # sam в нижнем регистре
        self._discrepancies: dict[str, dict] = {}  # key -> расхождение

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest/локального запуска."""
        self._links.clear()
        self._bases.clear()
        self._employees.clear()
        self._users.clear()
        self._discrepancies.clear()

    def find(self, key: str) -> LinkRecord | None:
        return self._links.get(key)

    def find_by_sam(self, sam: str) -> list[LinkRecord]:
        """Все связки логина: регистр и пробелы логина не различаются."""
        wanted = sam.strip().lower()
        return [rec for rec in self._links.values() if rec.sam.strip().lower() == wanted]

    def save(self, record: LinkRecord) -> None:
        self._links[record.key] = record

    def _discrepancy_key(self, row: dict) -> str:
        return "%s|%s|%s" % (row["enterprise"], row["base_code"], row["tab_num"])

    def replace_discrepancies(self, rows: list[dict]) -> int:
        """Та же нормализация, что у Postgres: detail-объект из плоских полей,
        иначе офлайн-прогоны видели бы другой контракт выдачи, чем боевая БД."""
        self._discrepancies = {}
        for row in rows:
            item = dict(row)
            item["detail"] = {
                "candidates": list(row.get("candidates") or []),
                "sibling_tabs": list(row.get("sibling_tabs") or []),
            }
            item.pop("candidates", None)
            item.pop("sibling_tabs", None)
            item.setdefault("resolved_at", None)
            item.setdefault("detected_at", None)
            self._discrepancies[self._discrepancy_key(item)] = item
        return len(self._discrepancies)

    def list_discrepancies(
        self,
        reason: str | None = None,
        query: str = "",
        only_open: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        needle = (query or "").strip().lower()
        hits = []
        for row in self._discrepancies.values():
            if reason and row.get("reason") != reason:
                continue
            if only_open and row.get("resolved_at"):
                continue
            if needle and not any(
                needle in str(row.get(field) or "").lower()
                for field in ("fio", "tab_num", "ad_sam", "ad_fio")
            ):
                continue
            hits.append(dict(row))
        hits.sort(key=lambda r: (not r.get("recommended"), r.get("tab_num") or ""))
        return [
            self._with_key(self._discrepancy_key(row), row)
            for row in hits[offset : offset + limit]
        ]

    def _with_key(self, key: str, row: dict) -> dict:
        """key — колонка БД; в памяти он ключ словаря, отдаём тем же полем."""
        item = dict(row)
        item["key"] = key
        return item

    def _match_discrepancy(
        self,
        row: dict,
        reason: str | None,
        needle: str,
        only_open: bool,
    ) -> bool:
        if reason and row.get("reason") != reason:
            return False
        if only_open and row.get("resolved_at"):
            return False
        if needle and not any(
            needle in str(row.get(field) or "").lower()
            for field in ("fio", "tab_num", "ad_sam", "ad_fio")
        ):
            return False
        return True

    def count_discrepancies(
        self, reason: str | None = None, query: str = "", only_open: bool = True
    ) -> int:
        needle = (query or "").strip().lower()
        return sum(
            1
            for row in self._discrepancies.values()
            if self._match_discrepancy(row, reason, needle, only_open)
        )

    def counts_by_reason(self) -> dict:
        counts: dict[str, int] = {}
        for row in self._discrepancies.values():
            reason = str(row.get("reason") or "")
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def get_discrepancies(self, keys: list[str]) -> list[dict]:
        return [
            self._with_key(key, self._discrepancies[key])
            for key in keys
            if key in self._discrepancies
        ]

    def resolve_discrepancies(self, keys: list[str]) -> int:
        marked = 0
        for key in keys:
            row = self._discrepancies.get(key)
            if row is None or row.get("resolved_at"):
                continue
            row["resolved_at"] = "now"
            marked += 1
        return marked

    def ensure_targets(self, base: dict | None, employee: dict | None, user: dict | None) -> None:
        """Запомнить зеркала (в памяти FK нет — только для проверок тестов)."""
        if base and base.get("code"):
            self._bases[str(base["code"]).strip().lower()] = dict(base)
        if employee and employee.get("tab_num"):
            key = "%s|%s|%s" % (
                employee.get("enterprise"),
                employee.get("base_code"),
                employee.get("tab_num"),
            )
            self._employees[key] = dict(employee)
        if user and user.get("sam"):
            self._users[str(user["sam"]).strip().lower()] = dict(user)


class DbLinksStore:
    """Хранилище связок в Postgres (таблица link_1c_ad; миграция 0002 добавила
    колонки diverged / needs_manual_review / truth_source — поля модели LinkRecord).
    Ошибки БД оборачиваются в LinksUnavailable (503), как DbSettingsStore
    в settings_routes.py. Полный round-trip проверяется на стенде (qa-sed).
    """

    _FIND_SQL = text(
        """
        SELECT enterprise, base_code, tab_num, sam, linked_by, linked_at, verified,
               diverged, needs_manual_review, truth_source
        FROM link_1c_ad
        WHERE enterprise = :enterprise AND base_code = :base_code AND tab_num = :tab_num
        """
    )
    _FIND_BY_SAM_SQL = text(
        """
        SELECT enterprise, base_code, tab_num, sam, linked_by, linked_at, verified,
               diverged, needs_manual_review, truth_source
        FROM link_1c_ad
        WHERE lower(sam) = lower(:sam)
        """
    )
    _SAVE_SQL = text(
        """
        INSERT INTO link_1c_ad (enterprise, base_code, tab_num, sam, linked_by,
                                linked_at, verified, diverged, needs_manual_review, truth_source)
        VALUES (:enterprise, :base_code, :tab_num, :sam, :linked_by, :at, :verified,
                :diverged, :needs_manual_review, :truth_source)
        ON CONFLICT (enterprise, base_code, tab_num)
        DO UPDATE SET sam = EXCLUDED.sam,
                      linked_by = EXCLUDED.linked_by,
                      linked_at = EXCLUDED.linked_at,
                      verified = EXCLUDED.verified,
                      diverged = EXCLUDED.diverged,
                      needs_manual_review = EXCLUDED.needs_manual_review,
                      truth_source = EXCLUDED.truth_source
        """
    )
    # Зеркало пользователя в НАШЕЙ таблице users: нужно для FK link_1c_ad.sam
    # -> users(sam). Только своя БД, в AD/1С не пишем. Upsert по sam — зеркало
    # обновляется при повторной связке (AD — первоисточник).
    _ENSURE_USER_SQL = text(
        """
        INSERT INTO users (sam, fio_full, dept_ad, title_ad, manager_dn, mail)
        VALUES (:sam, :fio_full, :dept_ad, :title_ad, :manager_dn, :mail)
        ON CONFLICT (sam) DO UPDATE SET
            fio_full = EXCLUDED.fio_full,
            dept_ad = EXCLUDED.dept_ad,
            title_ad = EXCLUDED.title_ad,
            manager_dn = EXCLUDED.manager_dn,
            mail = EXCLUDED.mail,
            updated_at = now()
        """
    )
    # Зеркало базы 1С в НАШЕЙ таблице one_c_bases: нужно для FK
    # employee_base_map.base_code -> one_c_bases(code). Имя/URL — код базы,
    # если в настройках нет названия (реальный источник — settings.onec_bases).
    _ENSURE_BASE_SQL = text(
        """
        INSERT INTO one_c_bases (code, enterprise, name, odata_url)
        VALUES (:code, :enterprise, :name, :odata_url)
        ON CONFLICT (code) DO UPDATE SET
            enterprise = EXCLUDED.enterprise,
            name = EXCLUDED.name,
            odata_url = EXCLUDED.odata_url
        """
    )
    # Зеркало сотрудника 1С в НАШЕЙ таблице employee_base_map: нужно для FK
    # link_1c_ad(enterprise, base_code, tab_num) -> employee_base_map(...).
    # Только своя БД, в 1С не пишем (истина — живой OData).
    _ENSURE_EMPLOYEE_SQL = text(
        """
        INSERT INTO employee_base_map
            (enterprise, base_code, tab_num, fio, dept_1c, position_1c, employment_type, hire_date)
        VALUES (:enterprise, :base_code, :tab_num, :fio, :dept_1c, :position_1c,
                :employment_type, CAST(:hire_date AS DATE))
        ON CONFLICT (enterprise, base_code, tab_num) DO UPDATE SET
            fio = EXCLUDED.fio,
            dept_1c = EXCLUDED.dept_1c,
            position_1c = EXCLUDED.position_1c,
            employment_type = EXCLUDED.employment_type,
            hire_date = EXCLUDED.hire_date,
            updated_at = now()
        """
    )

    # Расхождения автосопоставления (миграция 0010). Полная замена таблицы на
    # каждом проходе: проход знает и что стало связанным, поэтому накопления нет.
    _REPLACE_DISCREPANCIES_SQL = text(
        "DELETE FROM link_discrepancies"
    )
    _INSERT_DISCREPANCY_SQL = text(
        """
        INSERT INTO link_discrepancies
            (key, enterprise, base_code, tab_num, fio, reason, ad_sam, ad_fio,
             ad_dept, ad_title, one_c_dept, one_c_position, recommended, detail)
        VALUES (:key, :enterprise, :base_code, :tab_num, :fio, :reason, :ad_sam,
                :ad_fio, :ad_dept, :ad_title, :one_c_dept, :one_c_position,
                :recommended, CAST(:detail AS JSONB))
        ON CONFLICT (key) DO UPDATE SET
            fio = EXCLUDED.fio,
            reason = EXCLUDED.reason,
            ad_sam = EXCLUDED.ad_sam,
            ad_fio = EXCLUDED.ad_fio,
            ad_dept = EXCLUDED.ad_dept,
            ad_title = EXCLUDED.ad_title,
            one_c_dept = EXCLUDED.one_c_dept,
            one_c_position = EXCLUDED.one_c_position,
            recommended = EXCLUDED.recommended,
            detail = EXCLUDED.detail,
            detected_at = now(),
            resolved_at = NULL
        """
    )
    _LIST_DISCREPANCIES_SQL = text(
        """
        SELECT key, enterprise, base_code, tab_num, fio, reason, ad_sam, ad_fio,
               ad_dept, ad_title, one_c_dept, one_c_position, recommended,
               detail, detected_at, resolved_at
        FROM link_discrepancies
        WHERE (:reason = '' OR reason = :reason)
          AND (:only_open = false OR resolved_at IS NULL)
          AND (:q = ''
               OR fio ILIKE '%' || :q || '%'
               OR tab_num ILIKE '%' || :q || '%'
               OR COALESCE(ad_sam, '') ILIKE '%' || :q || '%'
               OR COALESCE(ad_fio, '') ILIKE '%' || :q || '%')
        ORDER BY recommended DESC, fio, tab_num
        LIMIT :limit OFFSET :offset
        """
    )
    _COUNT_DISCREPANCIES_SQL = text(
        "SELECT reason, count(*) FROM link_discrepancies GROUP BY reason"
    )
    _GET_DISCREPANCIES_SQL = text(
        """
        SELECT key, enterprise, base_code, tab_num, fio, reason, ad_sam, ad_fio,
               ad_dept, ad_title, one_c_dept, one_c_position, recommended,
               detail, detected_at, resolved_at
        FROM link_discrepancies
        WHERE key = ANY(CAST(:keys AS text[]))
        """
    )
    _RESOLVE_DISCREPANCIES_SQL = text(
        "UPDATE link_discrepancies SET resolved_at = now() "
        "WHERE key = ANY(CAST(:keys AS text[])) AND resolved_at IS NULL"
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    @staticmethod
    def _split_key(key: str) -> tuple[str, str, str] | None:
        """Составной ключ 'enterprise|base_code|tab_num' на части (битый — None)."""
        parts = key.split("|", 2)
        if len(parts) != 3:
            return None
        return parts[0], parts[1], parts[2]

    def _to_record(self, row) -> LinkRecord:
        """Строка БД -> модель LinkRecord (миграция 0002 добавила колонки
        diverged/needs_manual_review/truth_source). Импорт модели — ленивый,
        чтобы не зациклить link.py <-> link_store."""
        from .link import LinkRecord, link_key

        return LinkRecord(
            enterprise=row.enterprise,
            base_code=row.base_code,
            tab_num=row.tab_num,
            key=link_key(row.enterprise, row.base_code, row.tab_num),
            sam=row.sam or "",
            by=row.linked_by or "",
            at=row.linked_at.isoformat() if row.linked_at else "",
            verified=bool(row.verified),
            diverged=bool(row.diverged),
            needs_manual_review=bool(row.needs_manual_review),
            truth_source=row.truth_source or "1c",
        )

    def find(self, key: str) -> LinkRecord | None:
        """Связка по составному ключу либо None."""
        parts = self._split_key(key)
        if parts is None:
            return None
        try:
            with self._session_factory() as session:
                row = session.execute(
                    self._FIND_SQL,
                    {
                        "enterprise": parts[0],
                        "base_code": parts[1],
                        "tab_num": parts[2],
                    },
                ).first()
        except SQLAlchemyError as exc:
            raise LinksUnavailable(f"Хранилище связок недоступно: {exc}") from exc
        return self._to_record(row) if row is not None else None

    def find_by_sam(self, sam: str) -> list[LinkRecord]:
        """Все связки логина (lower-сравнение, как в InMemory)."""
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    self._FIND_BY_SAM_SQL, {"sam": sam.strip()}
                ).all()
        except SQLAlchemyError as exc:
            raise LinksUnavailable(f"Хранилище связок недоступно: {exc}") from exc
        return [self._to_record(row) for row in rows]

    def save(self, record: LinkRecord) -> None:
        """Записать связку (upsert по UNIQUE enterprise/base_code/tab_num)."""
        try:
            with self._session_factory() as session:
                session.execute(
                    self._SAVE_SQL,
                    {
                        "enterprise": record.enterprise,
                        "base_code": record.base_code,
                        "tab_num": record.tab_num,
                        "sam": record.sam,
                        "linked_by": record.by,
                        "at": record.at,
                        "verified": record.verified,
                        "diverged": record.diverged,
                        "needs_manual_review": record.needs_manual_review,
                        "truth_source": record.truth_source,
                    },
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise LinksUnavailable(f"Хранилище связок недоступно: {exc}") from exc

    @staticmethod
    def _discrepancy_dict(row) -> dict:
        return {
            "key": row[0],
            "enterprise": row[1],
            "base_code": row[2],
            "tab_num": row[3],
            "fio": row[4],
            "reason": row[5],
            "ad_sam": row[6],
            "ad_fio": row[7],
            "ad_dept": row[8],
            "ad_title": row[9],
            "one_c_dept": row[10],
            "one_c_position": row[11],
            "recommended": bool(row[12]),
            "detail": row[13] or {},
            "detected_at": row[14].isoformat() if row[14] else None,
            "resolved_at": row[15].isoformat() if row[15] else None,
        }

    def replace_discrepancies(self, rows: list[dict]) -> int:
        params = [
            {
                "key": "%s|%s|%s" % (row["enterprise"], row["base_code"], row["tab_num"]),
                "enterprise": row["enterprise"],
                "base_code": row["base_code"],
                "tab_num": row["tab_num"],
                "fio": row["fio"],
                "reason": row["reason"],
                "ad_sam": row.get("ad_sam"),
                "ad_fio": row.get("ad_fio"),
                "ad_dept": row.get("ad_dept"),
                "ad_title": row.get("ad_title"),
                "one_c_dept": row.get("one_c_dept"),
                "one_c_position": row.get("one_c_position"),
                "recommended": bool(row.get("recommended")),
                # Кандидаты AD (при дубле ФИО в AD) и табельные номера остальных
                # карточек 1С этой группы — нужны админу для выбора.
                # JSON-строкой: psycopg3 не адаптирует dict в параметр
                # («cannot adapt type 'dict'»), приведение к jsonb — в SQL.
                "detail": json.dumps(
                    {
                        "candidates": list(row.get("candidates") or []),
                        "sibling_tabs": list(row.get("sibling_tabs") or []),
                    },
                    ensure_ascii=False,
                ),
            }
            for row in rows
        ]
        try:
            with self._session_factory() as session:
                session.execute(self._REPLACE_DISCREPANCIES_SQL)
                if params:
                    session.execute(self._INSERT_DISCREPANCY_SQL, params)
                session.commit()
        except SQLAlchemyError as exc:
            raise LinksUnavailable("Расхождения сопоставления недоступны: %s" % exc) from exc
        return len(params)

    def list_discrepancies(
        self,
        reason: str | None = None,
        query: str = "",
        only_open: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    self._LIST_DISCREPANCIES_SQL,
                    {
                        "reason": (reason or "").strip(),
                        "q": (query or "").strip(),
                        "only_open": bool(only_open),
                        "limit": int(limit),
                        "offset": int(offset),
                    },
                ).all()
        except SQLAlchemyError as exc:
            raise LinksUnavailable("Расхождения сопоставления недоступны: %s" % exc) from exc
        return [self._discrepancy_dict(row) for row in rows]

    _COUNT_DISCREPANCIES_TOTAL_SQL = text(
        """
        SELECT count(*) FROM link_discrepancies
        WHERE (:reason = '' OR reason = :reason)
          AND (:only_open = false OR resolved_at IS NULL)
          AND (:q = ''
               OR fio ILIKE '%' || :q || '%'
               OR tab_num ILIKE '%' || :q || '%'
               OR COALESCE(ad_sam, '') ILIKE '%' || :q || '%'
               OR COALESCE(ad_fio, '') ILIKE '%' || :q || '%')
        """
    )

    def count_discrepancies(
        self, reason: str | None = None, query: str = "", only_open: bool = True
    ) -> int:
        try:
            with self._session_factory() as session:
                value = session.execute(
                    self._COUNT_DISCREPANCIES_TOTAL_SQL,
                    {
                        "reason": (reason or "").strip(),
                        "q": (query or "").strip(),
                        "only_open": bool(only_open),
                    },
                ).scalar()
        except SQLAlchemyError as exc:
            raise LinksUnavailable("Расхождения сопоставления недоступны: %s" % exc) from exc
        return int(value or 0)

    def counts_by_reason(self) -> dict:
        try:
            with self._session_factory() as session:
                rows = session.execute(self._COUNT_DISCREPANCIES_SQL).all()
        except SQLAlchemyError as exc:
            raise LinksUnavailable("Расхождения сопоставления недоступны: %s" % exc) from exc
        return {str(row[0]): int(row[1]) for row in rows}

    def get_discrepancies(self, keys: list[str]) -> list[dict]:
        if not keys:
            return []
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    self._GET_DISCREPANCIES_SQL, {"keys": list(keys)}
                ).all()
        except SQLAlchemyError as exc:
            raise LinksUnavailable("Расхождения сопоставления недоступны: %s" % exc) from exc
        return [self._discrepancy_dict(row) for row in rows]

    def resolve_discrepancies(self, keys: list[str]) -> int:
        if not keys:
            return 0
        try:
            with self._session_factory() as session:
                result = session.execute(
                    self._RESOLVE_DISCREPANCIES_SQL, {"keys": list(keys)}
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise LinksUnavailable("Расхождения сопоставления недоступны: %s" % exc) from exc
        return int(result.rowcount or 0)
    def ensure_targets(self, base: dict | None, employee: dict | None, user: dict | None) -> None:
        """Зеркала ссылок связки (одной транзакцией): база/сотрудник/пользователь.

        Только своя БД (one_c_bases/employee_base_map/users), в AD/1С не пишем.
        Каждое зеркало опционально — создаём только переданные."""
        try:
            with self._session_factory() as session:
                if base and base.get("code"):
                    session.execute(
                        self._ENSURE_BASE_SQL,
                        {
                            "code": str(base.get("code") or ""),
                            "enterprise": str(base.get("enterprise") or ""),
                            "name": str(base.get("name") or base.get("code") or ""),
                            "odata_url": str(base.get("odata_url") or base.get("code") or ""),
                        },
                    )
                if employee and employee.get("tab_num"):
                    session.execute(
                        self._ENSURE_EMPLOYEE_SQL,
                        {
                            "enterprise": str(employee.get("enterprise") or ""),
                            "base_code": str(employee.get("base_code") or ""),
                            "tab_num": str(employee.get("tab_num") or ""),
                            "fio": str(employee.get("fio") or employee.get("tab_num") or ""),
                            "dept_1c": employee.get("dept_1c"),
                            "position_1c": employee.get("position_1c"),
                            "employment_type": employee.get("employment_type"),
                            "hire_date": employee.get("hire_date") or None,
                        },
                    )
                if user and user.get("sam"):
                    session.execute(
                        self._ENSURE_USER_SQL,
                        {
                            "sam": str(user.get("sam") or ""),
                            # fio_full NOT NULL: пустое displayName — подставляем sam.
                            "fio_full": str(user.get("fio_full") or user.get("sam") or ""),
                            "dept_ad": user.get("dept_ad"),
                            "title_ad": user.get("title_ad"),
                            "manager_dn": user.get("manager_dn"),
                            "mail": user.get("mail"),
                        },
                    )
                session.commit()
        except SQLAlchemyError as exc:
            raise LinksUnavailable(f"Хранилище связок недоступно: {exc}") from exc


_db_links_store: DbLinksStore | None = None


def get_links_store(settings: Settings = Depends(get_settings)) -> LinksStore:
    """Боевое хранилище связок (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется InMemoryLinksStore через dependency_overrides
    (как get_settings_store в settings_routes.py).
    """
    global _db_links_store
    if _db_links_store is None:
        _db_links_store = DbLinksStore(settings.DATABASE_URL)
    return _db_links_store
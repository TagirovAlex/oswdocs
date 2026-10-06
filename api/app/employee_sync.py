# Локальный справочник сотрудников (решение человека): синхронизация 1С + AD-связка
# в таблицу employees (миграция 0004) и переключение поиска /employees на неё.
# Живой поиск 1С ограничен $top=50 на базу — в справочнике «не все сотрудники»,
# поэтому полная выгрузка идёт постранично (list_employees $skip/$top, LIST_PAGE=500),
# как в ad_sync.
# Правила: 1С — ТОЛЬКО чтение (OneCClient.list_employees), связка 1С↔AD — из нашего
# LinksStore; запись — только в нашу таблицу employees. Падение одной базы 1С не
# валит синхронизацию: ошибки копятся в result.errors, толерантность — как у
# maybe_sync_weekly (onec_sync.py).

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional, Protocol

from fastapi import Depends
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings
from .settings_routes import read_setting_value

# Размер страницы выгрузки сотрудников (лимит одной OData-выдачи), как в ad_sync.
LIST_PAGE = 500


class EmployeeSyncUnavailable(Exception):
    """Локальный справочник сотрудников недоступен (нет баз/предприятий/БД) — 503."""


def _norm_people(values: list[str] | None) -> list[str]:
    """Нормализация логинов/табельных номеров для точного сравнения (lower, без пустых).

    Единый вид значения для in-memory и Postgres: обрезка пробелов и регистр,
    иначе один и тот же человек искался бы по-разному в зависимости от того,
    как его прислали (из 1С или вручную в заявке)."""
    return [value.strip().lower() for value in (values or []) if value and value.strip()]


class EmployeeSyncStore(Protocol):
    """Интерфейс хранилища локального справочника: единый для in-memory и Postgres."""

    def count(self) -> int:
        """Всего строк в таблице (0 = синк ещё не прошёл, нужен фолбэк на 1С)."""
        ...

    def search(
        self, enterprise: str, q: str, limit: int, offset: int = 0
    ) -> list[dict]:
        """Строки предприятия по подстроке fio/tab_num/position/ad_sam (без регистра).

        Каждая строка: enterprise/base_code/tab_num/fio/department/position/ad_sam/
        ad_status; limit записей, начиная с offset (порядок — как в хранилище)."""
        ...

    def count_matching(self, enterprise: str, q: str) -> int:
        """Сколько строк предприятия отвечают подстроке fio/tab_num/position/ad_sam
        (те же условия, что в search — для total серверной пагинации)."""
        ...

    def find_by_people(
        self, enterprise: str, sam_list: list[str], tab_list: list[str]
    ) -> list[dict]:
        """Точный поиск сотрудников предприятия по спискам (без ILIKE, пакетно).

        Условие — предприятие И (lower(ad_sam) IN (:sam_list) ИЛИ
        lower(tab_num) IN (:tab_list)); сравнение регистронезависимое, только
        точные совпадения. Пустые списки в запрос не подставляются, а если оба
        списка пусты — хранилище не запрашивается вовсе и возвращает [].
        Строки — как в search (enterprise/base_code/tab_num/fio/department/
        position/ad_sam/ad_status)."""
        ...

    def upsert_many(self, rows: list[dict]) -> int:
        """Записать строки (upsert по составному ключу), вернуть число строк."""
        ...

    def distinct_positions(self) -> list[str]:
        """Все должности справочника (position), уникальные, сортированные.

        Пустые/NULL пропускаются. Источник добора 1С для справочника должностей
        бланков (приоритет — AD, см. ad_groups_cache)."""
        ...


class InMemoryEmployeeSyncStore:
    """Офлайн-хранилище справочника (тесты/локаль без БД), интерфейс EmployeeSyncStore."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], dict] = {}

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest/локального запуска."""
        self._rows.clear()

    def count(self) -> int:
        return len(self._rows)

    def search(
        self, enterprise: str, q: str, limit: int, offset: int = 0
    ) -> list[dict]:
        needle = (q or "").strip().lower()
        hits = []
        for row in self._rows.values():
            if row["enterprise"] != enterprise:
                continue
            if needle and not (
                needle in row["fio"].lower()
                or needle in row["tab_num"].lower()
                or needle in (row.get("position") or "").lower()
                or needle in (row.get("ad_sam") or "").lower()
            ):
                continue
            hits.append(dict(row))
        return hits[offset : offset + limit]

    def count_matching(self, enterprise: str, q: str) -> int:
        return len(self.search(enterprise, q, len(self._rows)))

    def find_by_people(
        self, enterprise: str, sam_list: list[str], tab_list: list[str]
    ) -> list[dict]:
        sams = _norm_people(sam_list)
        tabs = _norm_people(tab_list)
        if not sams and not tabs:
            return []
        hits = []
        for row in self._rows.values():
            if row["enterprise"] != enterprise:
                continue
            ad_sam = (row.get("ad_sam") or "").strip().lower()
            tab_num = (row.get("tab_num") or "").strip().lower()
            if (ad_sam and ad_sam in sams) or (tab_num and tab_num in tabs):
                hits.append(dict(row))
        return hits

    def upsert_many(self, rows: list[dict]) -> int:
        for row in rows:
            key = (row["enterprise"], row["base_code"], row["tab_num"])
            self._rows[key] = dict(row)
        return len(rows)

    def distinct_positions(self) -> list[str]:
        return sorted(
            {str(row.get("position") or "").strip() for row in self._rows.values()}
            - {""}
        )


class DbEmployeeSyncStore:
    """Хранилище справочника в Postgres (таблица employees; миграция 0004).

    Ошибки БД оборачиваются в EmployeeSyncUnavailable (503), как DbSettingsStore
    в settings_routes.py. Полный round-trip проверяется на стенде (qa-sed)."""

    # Все строки справочника (0 = синк не прошёл, нужен фолбэк на живой 1С).
    _COUNT_ALL_SQL = text("SELECT count(*) FROM employees")
    # В выборку справочника попадают только сотрудники, связанные с AD
    # (ad_sam): у остальных нет AD-карточки — нечем подтвердить службу и
    # руководителя, и маршрут согласования не собрать. Отсекает архив и
    # уволенных, учётки которых уже отключены в AD.
    _SEARCH_SQL = text(
        """
        SELECT enterprise, base_code, tab_num, fio, department, position, ad_sam, ad_status
        FROM employees
        WHERE enterprise = :enterprise
          AND ad_sam IS NOT NULL
          AND (:q = ''
               OR fio ILIKE '%' || :q || '%'
               OR tab_num ILIKE '%' || :q || '%'
               OR COALESCE(position, '') ILIKE '%' || :q || '%'
               OR COALESCE(ad_sam, '') ILIKE '%' || :q || '%')
        ORDER BY fio, tab_num
        LIMIT :limit OFFSET :offset
        """
    )
    _COUNT_MATCHING_SQL = text(
        """
        SELECT count(*) FROM employees
        WHERE enterprise = :enterprise
          AND ad_sam IS NOT NULL
          AND (:q = ''
               OR fio ILIKE '%' || :q || '%'
               OR tab_num ILIKE '%' || :q || '%'
               OR COALESCE(position, '') ILIKE '%' || :q || '%'
               OR COALESCE(ad_sam, '') ILIKE '%' || :q || '%')
        """
    )
    _UPSERT_SQL = text(
        """
        INSERT INTO employees
            (enterprise, base_code, tab_num, fio, department, position, ad_sam, ad_status)
        VALUES (:enterprise, :base_code, :tab_num, :fio, :department, :position,
                :ad_sam, :ad_status)
        ON CONFLICT (enterprise, base_code, tab_num) DO UPDATE SET
            fio = EXCLUDED.fio,
            department = EXCLUDED.department,
            position = EXCLUDED.position,
            ad_sam = EXCLUDED.ad_sam,
            ad_status = EXCLUDED.ad_status,
            updated_at = now()
        """
    )
    # Шаблон точного пакетного поиска: условие по логинам/табельным номерам
    # собирается в методе (пустые списки в IN не подставляются), параметры
    # списков — bindparam(expanding=True), иначе psycopg получает кортеж, а не
    # список значений.
    _FIND_BY_PEOPLE_SQL = """
        SELECT enterprise, base_code, tab_num, fio, department, position, ad_sam, ad_status
        FROM employees
        WHERE enterprise = :enterprise
          AND ({where})
        ORDER BY fio, tab_num
        """

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    @staticmethod
    def _to_dict(row) -> dict:
        """Строка БД -> словарь контракта search."""
        return {
            "enterprise": row.enterprise,
            "base_code": row.base_code,
            "tab_num": row.tab_num,
            "fio": row.fio,
            "department": row.department,
            "position": row.position,
            "ad_sam": row.ad_sam,
            "ad_status": row.ad_status,
        }

    def count(self) -> int:
        try:
            with self._session_factory() as session:
                value = session.execute(self._COUNT_ALL_SQL).scalar()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return int(value or 0)

    def search(
        self, enterprise: str, q: str, limit: int, offset: int = 0
    ) -> list[dict]:
        try:
            with self._session_factory() as session:
                rows = session.execute(
                    self._SEARCH_SQL,
                    {
                        "enterprise": enterprise,
                        "q": (q or "").strip(),
                        "limit": limit,
                        "offset": offset,
                    },
                ).all()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return [self._to_dict(row) for row in rows]

    def count_matching(self, enterprise: str, q: str) -> int:
        try:
            with self._session_factory() as session:
                value = session.execute(
                    self._COUNT_MATCHING_SQL,
                    {"enterprise": enterprise, "q": (q or "").strip()},
                ).scalar()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return int(value or 0)

    def find_by_people(
        self, enterprise: str, sam_list: list[str], tab_list: list[str]
    ) -> list[dict]:
        sams = _norm_people(sam_list)
        tabs = _norm_people(tab_list)
        if not sams and not tabs:
            return []
        clauses: list[str] = []
        params: dict[str, object] = {"enterprise": enterprise}
        expanding: list = []
        if sams:
            clauses.append("lower(ad_sam) IN :sam_list")
            params["sam_list"] = sams
            expanding.append(bindparam("sam_list", expanding=True))
        if tabs:
            clauses.append("lower(tab_num) IN :tab_list")
            params["tab_list"] = tabs
            expanding.append(bindparam("tab_list", expanding=True))
        statement = text(self._FIND_BY_PEOPLE_SQL.format(where=" OR ".join(clauses)))
        if expanding:
            statement = statement.bindparams(*expanding)
        try:
            with self._session_factory() as session:
                rows = session.execute(statement, params).all()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return [self._to_dict(row) for row in rows]

    def upsert_many(self, rows: list[dict]) -> int:
        params = [
            {
                "enterprise": row["enterprise"],
                "base_code": row["base_code"],
                "tab_num": row["tab_num"],
                "fio": row["fio"],
                "department": row.get("department"),
                "position": row.get("position"),
                "ad_sam": row.get("ad_sam"),
                "ad_status": row.get("ad_status"),
            }
            for row in rows
        ]
        try:
            with self._session_factory() as session:
                session.execute(self._UPSERT_SQL, params)
                session.commit()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return len(rows)

    _POSITIONS_SQL = text(
        """
        SELECT DISTINCT position FROM employees
        WHERE position IS NOT NULL AND position <> ''
        ORDER BY position
        """
    )

    def distinct_positions(self) -> list[str]:
        try:
            with self._session_factory() as session:
                rows = session.execute(self._POSITIONS_SQL).all()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return [row[0] for row in rows]


_db_employee_store: DbEmployeeSyncStore | None = None


def get_employee_sync_store(settings: Settings = Depends(get_settings)) -> EmployeeSyncStore:
    """Боевое хранилище справочника сотрудников (Postgres): один движок на процесс.

    В офлайн-тестах подменяется InMemoryEmployeeSyncStore через dependency_overrides
    (как get_settings_store в settings_routes.py)."""
    global _db_employee_store
    if _db_employee_store is None:
        _db_employee_store = DbEmployeeSyncStore(settings.DATABASE_URL)
    return _db_employee_store


def _link_row(card, links_store) -> dict:
    """ad_sam/ad_status по существующей связке 1С↔AD (без падения без хранилища).

    Связка есть — ad_sam=sam, ad_status='linked'; иначе — NULL (на чтении поиск
    трактует отсутствие как 'no_match', см. employees._local_item)."""
    try:
        rec = links_store.find(card.key())
    except Exception:
        rec = None
    if rec is None:
        return {"ad_sam": None, "ad_status": None}
    return {"ad_sam": rec.sam, "ad_status": "linked"}


def _list_all(client, base_code: str, enterprise: str, errors: List[str]):
    """Все сотрудники базы по предприятию страницами (толерантно к падению базы).

    Возврат: (cards, fetched) — fetched=True, если база ответила хотя бы одной
    страницей (пустой ответ — «живая база без сотрудников», не падение)."""
    from .onec_client import OneCBaseDown, OneCCircuitOpen, OneCError

    cards: List = []
    fetched = False
    skip = 0
    while True:
        try:
            page = client.list_employees(
                base_code, enterprise, skip=skip, top=LIST_PAGE
            )
        except (OneCBaseDown, OneCCircuitOpen) as exc:
            errors.append("%s/%s: %s" % (enterprise, base_code, exc))
            return cards, fetched
        except OneCError as exc:
            errors.append("%s/%s: %s" % (enterprise, base_code, exc))
            return cards, fetched
        except Exception as exc:  # сеть/прочее — изолируем, опрос продолжаем
            errors.append("%s/%s: %s: %s" % (enterprise, base_code, type(exc).__name__, exc))
            return cards, fetched
        fetched = True
        if not page:
            break
        cards.extend(page)
        if len(page) < LIST_PAGE:
            break
        skip += len(page)
    return cards, fetched


def sync_employees(store, links_store, emp_store: Optional[EmployeeSyncStore] = None) -> dict:
    """Опрос баз 1С: собрать сотрудников в локальную таблицу employees.

    Базы — settings.onec_bases; предприятия и маппинг «предприятие→базы» — из
    синхронизации (settings.enterprises / onec_enterprise_bases), иначе карточка
    не получит корректный enterprise (в выгрузке без фильтра он пуст). Для каждого
    предприятия→базы — постраничная выгрузка list_employees($skip/$top); для каждой
    карточки — ad_sam/ad_status по связке из links_store; UPSERT в таблицу employees.
    Падение одной базы не валит остальные; если не ответила НИ ОДНА — 
    EmployeeSyncUnavailable (503). Возврат: {"synced": N, "errors": [...]}."""
    from .employees import (  # локально против циклического импорта
        _bases_from_settings,
        _enterprise_index_from_settings,
    )
    from .onec_client import OneCClient

    bases_raw = read_setting_value(store, "onec_bases")
    if not isinstance(bases_raw, list) or not bases_raw:
        raise EmployeeSyncUnavailable("Базы 1С не настроены")
    bases = _bases_from_settings(store)
    if not bases:
        raise EmployeeSyncUnavailable("Базы 1С не настроены")
    client = OneCClient(bases, enterprise_index=_enterprise_index_from_settings(store))
    raw_enterprises = read_setting_value(store, "enterprises")
    enterprises = [
        str(item["code"])
        for item in raw_enterprises
        if isinstance(item, dict) and item.get("code")
    ] if isinstance(raw_enterprises, list) else []
    if not enterprises:
        raise EmployeeSyncUnavailable(
            "Предприятия не настроены: выполните синхронизацию предприятий из 1С"
        )
    if emp_store is None:
        emp_store = get_employee_sync_store(get_settings())

    errors: List[str] = []
    synced = 0
    ok = False
    for enterprise in enterprises:
        for base_code in client.bases_for_enterprise(enterprise):
            cards, fetched = _list_all(client, base_code, enterprise, errors)
            ok = ok or fetched
            if not cards:
                continue
            rows = [
                {
                    "enterprise": card.enterprise,
                    "base_code": card.base_code,
                    "tab_num": card.tab_num,
                    "fio": card.fio,
                    "department": card.dept or None,
                    "position": card.position or None,
                    **_link_row(card, links_store),
                }
                for card in cards
            ]
            synced += emp_store.upsert_many(rows)
    if not ok:
        raise EmployeeSyncUnavailable(
            "Ни одна база 1С не ответила: " + "; ".join(errors or ["баз нет"])
        )
    return {"synced": synced, "errors": errors}


def maybe_sync_employees_weekly(store, links_store) -> bool:
    """Регламентная синхронизация справочника сотрудников (worker): тихо, без сбоев.

    Базы 1С не настроены — False; «не пора» по расписанию schedule_employees_sync
    (нет расписания — раз в 7 дней от employees_synced_at) — False; иначе
    sync_employees (сбой не валит worker — False), метка employees_synced_at
    пишется только после успеха — True."""
    from .onec_sync import due_schedule  # лениво: избегаем циклов импорта

    bases = read_setting_value(store, "onec_bases")
    if not isinstance(bases, list) or not bases:
        return False
    schedule = read_setting_value(store, "schedule_employees_sync")
    last_raw = read_setting_value(store, "employees_synced_at")
    if not due_schedule(schedule, last_raw):
        return False
    try:
        sync_employees(store, links_store)
        store.set(
            "employees_synced_at",
            json.dumps(datetime.now(timezone.utc).isoformat()),
        )
        return True
    except Exception:
        return False

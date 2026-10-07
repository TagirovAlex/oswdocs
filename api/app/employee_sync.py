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
from datetime import date, datetime, timezone
from typing import List, Optional, Protocol

from fastapi import Depends
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings
from .settings_routes import read_setting_value

# Размер страницы выгрузки сотрудников (лимит одной OData-выдачи), как в ad_sync.
LIST_PAGE = 500
# Размер страницы регистра кадровых данных (тот же лимит OData).
HR_PAGE = 500
# Порция строк в одном UPDATE дат увольнения (длина запроса к Postgres).
DISMISSAL_BATCH = 1000


class EmployeeSyncUnavailable(Exception):
    """Локальный справочник сотрудников недоступен (нет баз/предприятий/БД) — 503."""


def _as_date(value: object) -> date | None:
    """Дата из строки 1С (ISO «2026-04-14T00:00:00») в date; пусто/битое — None.

    None = увольнения не было: прежнюю дату нужно очистить, чтобы
    восстановленного сотрудника вернуло в выдачу справочника."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _dismissed(row: dict, today: date | None = None) -> bool:
    """Уволен ли сотрудник по дате из регистра 1С.

    Критерий — только дата увольнения из 1С (решение человека): пусто или позже
    сегодняшнего дня — работает; сегодня или раньше — уволен (учётку в AD после
    увольнения отключают вручную, состояние AD не проверяем)."""
    raw = str(row.get("dismissal_date") or "").strip()
    if not raw:
        return False
    try:
        return date.fromisoformat(raw[:10]) <= (today or date.today())
    except ValueError:  # битая дата — считаем работающим, лучше показать лишнее
        return False


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

    def dismissed_keys(self) -> set:
        """Ключи уволенных карточек: 'enterprise|base_code|tab_num'.

        Критерий — дата увольнения из регистра 1С (dismissal_date <= сегодня);
        пустая дата или будущая — сотрудник работает. Нужны автосопоставлению,
        чтобы не предлагать связи тем, кто уже уволен (связь и маршрут им не
        нужны, а расхождения только мешают разбору)."""
        ...

    def mark_linked(self, enterprise: str, tab_num: str, sam: str, base_code: str = "") -> int:
        """Проставить ad_sam/ad_status строке справочника после оформления связи.

        Нужно сразу после подтверждения связи человеком: иначе следующий запрос
        создания заявки не увидит логин и снова предложит подтвердить (и собрать
        маршрут не сможет) — до следующего планового синка справочника.
        Совпадение — по табельному номеру в предприятии (base_code уточняет,
        если передан); вернулось число обновлённых строк."""
        ...

    def update_dismissals(self, base_code: str, rows: list[dict]) -> int:
        """Проставить даты увольнения по парам (ref_key, дата) базы.

        Строка справочника и запись регистра сопоставляются по Ref_Key сотрудника
        (поле «Сотрудник_Key» регистра). Дата пустая — запись в регистре есть, но
        увольнения не было: очищаем прежнюю дату (сотрудник восстановлен).
        Неизвестный ref_key игнорируем. Возврат — число обновлённых строк."""
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
        today = date.today()
        hits = []
        for row in self._rows.values():
            if row["enterprise"] != enterprise:
                continue
            if _dismissed(row, today):
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

    def dismissed_keys(self) -> set:
        """Ключи карточек с датой увольнения <= сегодня (локальная логика даты)."""
        return {
            "%s|%s|%s" % (row.get("enterprise"), row.get("base_code"), row.get("tab_num"))
            for row in self._rows.values()
            if _dismissed(row)
        }

    def mark_linked(self, enterprise: str, tab_num: str, sam: str, base_code: str = "") -> int:
        updated = 0
        for row in self._rows.values():
            if row.get("enterprise") != enterprise or row.get("tab_num") != tab_num:
                continue
            if base_code and row.get("base_code") != base_code:
                continue
            row["ad_sam"] = sam
            row["ad_status"] = "linked"
            updated += 1
        return updated

    def update_dismissals(self, base_code: str, rows: list[dict]) -> int:
        by_ref = {str(item.get("ref_key") or "").strip(): item for item in rows}
        updated = 0
        for row in self._rows.values():
            if row.get("base_code") != base_code:
                continue
            item = by_ref.get(str(row.get("ref_key") or "").strip())
            if item is None:
                continue
            row["dismissal_date"] = item.get("dismissal_date") or None
            updated += 1
        return updated

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
    # Из выборки справочника исключены уволенные: увольнение определяется датой
    # из регистра кадровых данных 1С (dismissal_date), а не состоянием AD —
    # учётку в AD после увольнения отключают вручную. Пустая дата или позже
    # сегодняшнего дня — сотрудник работает и в выдаче.
    _WORKING_SQL = (
        "AND (dismissal_date IS NULL OR dismissal_date > CURRENT_DATE)"
    )
    _SEARCH_SQL = text(
        """
        SELECT enterprise, base_code, tab_num, fio, department, position, ad_sam, ad_status
        FROM employees
        WHERE enterprise = :enterprise
          """ + _WORKING_SQL + """
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
          """ + _WORKING_SQL + """
          AND (:q = ''
               OR fio ILIKE '%' || :q || '%'
               OR tab_num ILIKE '%' || :q || '%'
               OR COALESCE(position, '') ILIKE '%' || :q || '%'
               OR COALESCE(ad_sam, '') ILIKE '%' || :q || '%')
        """
    )
    # ref_key — ключ строки для регистра кадровых данных (сопоставление по
    # увольнению). dismissal_date НЕ трогаем: его пишет только проход по
    # регистру (update_dismissals), ежедневный справочник его бы затирал.
    _UPSERT_SQL = text(
        """
        INSERT INTO employees
            (enterprise, base_code, tab_num, fio, department, position, ad_sam,
             ad_status, ref_key)
        VALUES (:enterprise, :base_code, :tab_num, :fio, :department, :position,
                :ad_sam, :ad_status, :ref_key)
        ON CONFLICT (enterprise, base_code, tab_num) DO UPDATE SET
            fio = EXCLUDED.fio,
            department = EXCLUDED.department,
            position = EXCLUDED.position,
            ad_sam = EXCLUDED.ad_sam,
            ad_status = EXCLUDED.ad_status,
            ref_key = EXCLUDED.ref_key,
            updated_at = now()
        """
    )
    # Порция обновления дат увольнения. VALUES перечисляем текстом с нумерованными
    # параметрами (bindparam(expanding=True) с multi-column VALUES не собирает
    # пары, psycopg получает dict вместо строки) — порциями по DISMISSAL_BATCH
    # строк, иначе запрос разрастается на весь регистр базы.
    _UPDATE_DISMISSALS_SQL_TMPL = (
        "UPDATE employees e "
        "SET dismissal_date = v.dismissal_date::date, hr_synced_at = now() "
        "FROM (VALUES {values}) AS v(ref_key, dismissal_date) "
        "WHERE e.base_code = :base_code "
        "AND e.ref_key = v.ref_key"
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
                "ref_key": row.get("ref_key") or None,
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

    _DISMISSED_KEYS_SQL = text(
        """
        SELECT enterprise, base_code, tab_num FROM employees
        WHERE dismissal_date IS NOT NULL AND dismissal_date <= CURRENT_DATE
        """
    )

    def dismissed_keys(self) -> set:
        """Ключи уволенных карточек из локальной таблицы (дата из регистра 1С)."""
        try:
            with self._session_factory() as session:
                rows = session.execute(self._DISMISSED_KEYS_SQL).all()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return {"%s|%s|%s" % (row[0], row[1], row[2]) for row in rows}

    _MARK_LINKED_SQL = text(
        """
        UPDATE employees
        SET ad_sam = :sam, ad_status = 'linked', updated_at = now()
        WHERE enterprise = :enterprise AND tab_num = :tab_num
          AND (:base_code = '' OR base_code = :base_code)
        """
    )

    def mark_linked(self, enterprise: str, tab_num: str, sam: str, base_code: str = "") -> int:
        try:
            with self._session_factory() as session:
                result = session.execute(
                    self._MARK_LINKED_SQL,
                    {
                        "enterprise": enterprise,
                        "tab_num": tab_num,
                        "sam": sam,
                        "base_code": (base_code or "").strip(),
                    },
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return int(result.rowcount or 0)

    def update_dismissals(self, base_code: str, rows: list[dict]) -> int:
        pairs = [
            (
                str(row.get("ref_key") or "").strip(),
                _as_date(row.get("dismissal_date")),
            )
            for row in rows
            if str(row.get("ref_key") or "").strip()
        ]
        if not pairs:
            return 0
        updated = 0
        try:
            with self._session_factory() as session:
                for start in range(0, len(pairs), DISMISSAL_BATCH):
                    chunk = pairs[start : start + DISMISSAL_BATCH]
                    values = ", ".join(["(:r%d, :d%d)" % (i, i) for i in range(len(chunk))])
                    params = {"base_code": base_code}
                    params.update(
                        {"r%d" % i: ref for i, (ref, _) in enumerate(chunk)}
                    )
                    params.update(
                        {"d%d" % i: value for i, (_, value) in enumerate(chunk)}
                    )
                    result = session.execute(
                        text(self._UPDATE_DISMISSALS_SQL_TMPL.format(values=values)), params
                    )
                    updated += int(result.rowcount or 0)
                session.commit()
        except SQLAlchemyError as exc:
            raise EmployeeSyncUnavailable(
                "Справочник сотрудников недоступен: %s" % exc
            ) from exc
        return updated

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
                    # Ключ строки для регистра кадровых данных (увольнение).
                    "ref_key": card.ref_key or None,
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


def sync_hr_dismissals(
    store, emp_store: Optional[EmployeeSyncStore] = None
) -> dict:
    """Ежедневный проход по регистру кадровых данных: записать даты увольнения.

    Источник — регистр текущих кадровых данных базы (InformationRegister_…), по
    страницам ($skip/$top) одной выгрузкой на базу: пара (Ref_Key сотрудника,
    дата увольнения) пишется в employees.dismissal_date через ref_key. Пустая
    дата = увольнения не было (сотрудник работает) — прежняя дата очищается.

    Только чтение 1С, запись — только в нашу таблицу employees. Падение одной
    базы не валит остальные; не ответил НИ ОДИН регистр — EmployeeSyncUnavailable
    (503). Возврат: {"scanned", "updated", "dismissed", "errors"}."""
    from .employees import (  # локально против циклического импорта
        _bases_from_settings,
        _enterprise_index_from_settings,
    )
    from .onec_client import (
        OneCBaseDown,
        OneCCircuitOpen,
        OneCClient,
        OneCError,
    )

    bases = _bases_from_settings(store)
    if not bases:
        raise EmployeeSyncUnavailable("Базы 1С не настроены")
    client = OneCClient(bases, enterprise_index=_enterprise_index_from_settings(store))
    if emp_store is None:
        emp_store = get_employee_sync_store(get_settings())

    errors: List[str] = []
    scanned = 0
    updated = 0
    dismissed = 0
    ok = False
    for base_code in sorted(bases):
        skip = 0
        page_rows: List[dict] = []
        while True:
            try:
                page = client.list_hr_dismissals(base_code, skip=skip, top=HR_PAGE)
            except (OneCBaseDown, OneCCircuitOpen, OneCError) as exc:
                errors.append("%s: %s" % (base_code, exc))
                break
            except Exception as exc:  # сеть/прочее — изолируем, обход продолжаем
                errors.append("%s: %s: %s" % (base_code, type(exc).__name__, exc))
                break
            ok = True
            if not page:
                break
            scanned += len(page)
            page_rows.extend(
                {"ref_key": ref_key, "dismissal_date": date_value or None}
                for ref_key, date_value in page
            )
            if len(page) < HR_PAGE:
                break
            skip += len(page)
        if page_rows:
            updated += emp_store.update_dismissals(base_code, page_rows)
            dismissed += sum(1 for row in page_rows if row["dismissal_date"])
    if not ok:
        raise EmployeeSyncUnavailable(
            "Регистр кадровых данных не ответил: " + "; ".join(errors or ["баз нет"])
        )
    return {
        "scanned": scanned,
        "updated": updated,
        "dismissed": dismissed,
        "errors": errors,
    }


def maybe_sync_hr_daily(store, emp_store: Optional[EmployeeSyncStore] = None) -> bool:
    """Регламентный проход по регистру кадровых данных (worker): тихо, без сбоев.

    Базы 1С не настроены — False; «не пора» по расписанию
    schedule_hr_dismissals_sync (нет расписания — раз в 7 дней от
    hr_dismissals_synced_at) — False; иначе sync_hr_dismissals, метка
    hr_dismissals_synced_at пишется только после успеха — True."""
    from .onec_sync import due_schedule  # лениво: избегаем циклов импорта

    bases = read_setting_value(store, "onec_bases")
    if not isinstance(bases, list) or not bases:
        return False
    schedule = read_setting_value(store, "schedule_hr_dismissals_sync")
    last_raw = read_setting_value(store, "hr_dismissals_synced_at")
    if not due_schedule(schedule, last_raw):
        return False
    try:
        sync_hr_dismissals(store, emp_store)
        store.set(
            "hr_dismissals_synced_at",
            json.dumps(datetime.now(timezone.utc).isoformat()),
        )
        return True
    except Exception:
        return False


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

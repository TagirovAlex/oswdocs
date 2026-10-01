# Хранилище связок 1С-AD (волна 4): интерфейс LinksStore + in-memory реализация
# для офлайн-тестов/локали и DbLinksStore (Postgres, таблица link_1c_ad из 0001)
# для стенда. Эндпоинты link.py получают хранилище зависимостью get_links_store()
# (как get_settings_store в settings_routes.py); падение БД — LinksUnavailable
# -> роутер отвечает 503 (не 500); офлайн-тесты переопределяют InMemoryLinksStore.

from __future__ import annotations

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

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest/локального запуска."""
        self._links.clear()
        self._bases.clear()
        self._employees.clear()
        self._users.clear()

    def find(self, key: str) -> LinkRecord | None:
        return self._links.get(key)

    def find_by_sam(self, sam: str) -> list[LinkRecord]:
        """Все связки логина: регистр и пробелы логина не различаются."""
        wanted = sam.strip().lower()
        return [rec for rec in self._links.values() if rec.sam.strip().lower() == wanted]

    def save(self, record: LinkRecord) -> None:
        self._links[record.key] = record

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
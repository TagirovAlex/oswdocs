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


class InMemoryLinksStore:
    """Офлайн-хранилище связок (словарь процесса), интерфейс LinksStore."""

    def __init__(self) -> None:
        self._links: dict[str, LinkRecord] = {}

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest/локального запуска."""
        self._links.clear()

    def find(self, key: str) -> LinkRecord | None:
        return self._links.get(key)

    def find_by_sam(self, sam: str) -> list[LinkRecord]:
        """Все связки логина: регистр и пробелы логина не различаются."""
        wanted = sam.strip().lower()
        return [rec for rec in self._links.values() if rec.sam.strip().lower() == wanted]

    def save(self, record: LinkRecord) -> None:
        self._links[record.key] = record


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
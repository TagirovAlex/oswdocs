# Хранилище справочника заместителей: таблица substitutes из миграции 0023.
# Запись «кому кого замещает» на интервал дат (valid_from/valid_to), заводит
# администратор. Данные локальные: ни в AD, ни в 1С не пишем, логины берём
# как идентификаторы людей (ФИО меняется — логин нет).
# Слой доступа — raw SQL (SQLAlchemy text()), без ORM-моделей, по образцу
# routing_store.py; контракт наружу — неизменяемая запись Substitute.
# Падение БД — SubstitutesUnavailable (роутер отвечает 503, не 500), как
# RoutingUnavailable в routing_store.py. Нарушение UNIQUE (principal_sam,
# deputy_sam) — отдельное исключение SubstitutePairTaken: это конфликт
# состояния справочника (409 на границе), а не недоступность хранилища.
# Отсутствующий id — SubstituteNotFound (404 на границе), как «Бланк не
# найден» в settings_routes.py.
# Метки времени — дефолты/now() БД (миграция 0023), в памяти —
# datetime.now(timezone.utc), как в ad_groups_cache.py/requests_store.py.

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Iterator, Protocol

from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings


class SubstitutesUnavailable(Exception):
    """Хранилище справочника заместителей (БД) недоступно — роутер отвечает 503, а не 500."""


class SubstitutePairTaken(Exception):
    """Пара «кому замещают — кто замещает» уже занята (UNIQUE в БД): 409."""


class SubstituteNotFound(Exception):
    """Записи с таким id нет в справочнике: 404."""


@dataclass(frozen=True)
class Substitute:
    """Запись справочника заместителей (миграция 0023).

    principal_sam/deputy_sam — логины AD (sAMAccountName); valid_to = None —
    замещение действует, пока его не отменят; notify — слать заместителю
    копию письма «назначена» по шагам оригинала."""

    id: int
    principal_sam: str
    deputy_sam: str
    valid_from: date
    valid_to: date | None
    notify: bool
    note: str
    created_by: str
    created_at: datetime
    updated_at: datetime | None


class SubstituteStore(Protocol):
    """Интерфейс хранилища справочника заместителей: единый для Postgres и памяти."""

    def list_all(self) -> list[Substitute]:
        """Весь справочник (админка правит целиком, включая истёкшие сроки)."""
        ...  # pragma: no cover

    def list_active(self, on: date) -> list[Substitute]:
        """Действующие на дату записи: срок начался и ещё не кончился."""
        ...  # pragma: no cover

    def by_id(self, sub_id: int) -> Substitute | None:
        """Запись по id либо None (нет в справочнике)."""
        ...  # pragma: no cover

    def create(
        self,
        principal_sam: str,
        deputy_sam: str,
        valid_from: date,
        valid_to: date | None,
        notify: bool,
        note: str,
        actor: str,
    ) -> Substitute:
        """Создать запись (created_by = actor); дубль пары — SubstitutePairTaken."""
        ...  # pragma: no cover

    def update(
        self,
        sub_id: int,
        *,
        principal_sam: str,
        deputy_sam: str,
        valid_from: date,
        valid_to: date | None,
        notify: bool,
        note: str,
    ) -> Substitute:
        """Полностью заменить поля записи и поставить updated_at; нет записи —
        SubstituteNotFound, дубль пары (кроме своей) — SubstitutePairTaken."""
        ...  # pragma: no cover

    def delete(self, sub_id: int) -> None:
        """Удалить запись; нет записи — SubstituteNotFound."""
        ...  # pragma: no cover


# Колонки записи в порядке контракта: общий список для чтения, вставки с
# RETURNING и обновления — расхождение с миграцией 0023 сразу видно.
_COLUMNS = """
    id, principal_sam, deputy_sam, valid_from, valid_to, notify, note,
    created_by, created_at, updated_at
"""


class DbSubstituteStore:
    """Справочник заместителей в Postgres (миграция 0023), слой доступа — raw SQL.

    Ошибки БД оборачиваются в SubstitutesUnavailable (503), нарушение UNIQUE
    пары — в SubstitutePairTaken (409), запись только в свою таблицу."""

    _LIST_ALL = text(
        f"""
        SELECT {_COLUMNS}
        FROM substitutes
        ORDER BY principal_sam, deputy_sam, id
        """
    )
    _LIST_ACTIVE = text(
        f"""
        SELECT {_COLUMNS}
        FROM substitutes
        WHERE valid_from <= :on AND (valid_to IS NULL OR valid_to >= :on)
        ORDER BY principal_sam, deputy_sam, id
        """
    )
    _BY_ID = text(f"SELECT {_COLUMNS} FROM substitutes WHERE id = :sub_id")
    _INSERT = text(
        f"""
        INSERT INTO substitutes (principal_sam, deputy_sam, valid_from, valid_to,
                                 notify, note, created_by)
        VALUES (:principal_sam, :deputy_sam, :valid_from, :valid_to,
                COALESCE(:notify, TRUE), :note, :created_by)
        RETURNING {_COLUMNS}
        """
    )
    _UPDATE = text(
        f"""
        UPDATE substitutes
        SET principal_sam = :principal_sam, deputy_sam = :deputy_sam,
            valid_from = :valid_from, valid_to = :valid_to, notify = :notify,
            note = :note, updated_at = now()
        WHERE id = :sub_id
        RETURNING {_COLUMNS}
        """
    )
    _DELETE = text("DELETE FROM substitutes WHERE id = :sub_id")

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    @contextmanager
    def _session(self) -> Iterator[object]:
        """Сессия с обёрткой ошибок БД в SubstitutesUnavailable (503)."""
        try:
            with self._session_factory() as session:
                yield session
        except SQLAlchemyError as exc:
            raise SubstitutesUnavailable(
                f"Хранилище справочника заместителей недоступно: {exc}"
            ) from exc

    @staticmethod
    def _row_to_record(row) -> Substitute:
        """Строка БД -> запись контракта (метки времени — timestamptz БД)."""
        return Substitute(
            id=int(row.id),
            principal_sam=str(row.principal_sam or ""),
            deputy_sam=str(row.deputy_sam or ""),
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            notify=bool(row.notify),
            note=str(row.note or ""),
            created_by=str(row.created_by or ""),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_all(self) -> list[Substitute]:
        """Весь справочник: по логинам и id (порядок стабилен для админки)."""
        with self._session() as session:
            rows = session.execute(self._LIST_ALL).all()
        return [self._row_to_record(row) for row in rows]

    def list_active(self, on: date) -> list[Substitute]:
        """Действующие на дату: valid_from <= on и (valid_to IS NULL OR valid_to >= on)."""
        with self._session() as session:
            rows = session.execute(self._LIST_ACTIVE, {"on": on}).all()
        return [self._row_to_record(row) for row in rows]

    def by_id(self, sub_id: int) -> Substitute | None:
        """Запись по id либо None (нет в справочнике)."""
        with self._session() as session:
            row = session.execute(self._BY_ID, {"sub_id": sub_id}).first()
        return self._row_to_record(row) if row is not None else None

    def create(
        self,
        principal_sam: str,
        deputy_sam: str,
        valid_from: date,
        valid_to: date | None,
        notify: bool,
        note: str,
        actor: str,
    ) -> Substitute:
        """Создать запись (метка времени — дефолт БД now()).

        Дубль пары ловится отдельным исключением: IntegrityError проверяется
        раньше общего SQLAlchemyError, иначе конфликт UNIQUE ушёл бы в 503."""
        with self._session() as session:
            try:
                row = session.execute(
                    self._INSERT,
                    {
                        "principal_sam": principal_sam,
                        "deputy_sam": deputy_sam,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "notify": notify,
                        "note": note,
                        "created_by": str(actor or ""),
                    },
                ).first()
            except IntegrityError as exc:
                raise SubstitutePairTaken(
                    "Такая пара заместителей уже есть в справочнике"
                ) from exc
            session.commit()
        return self._row_to_record(row)

    def update(
        self,
        sub_id: int,
        *,
        principal_sam: str,
        deputy_sam: str,
        valid_from: date,
        valid_to: date | None,
        notify: bool,
        note: str,
    ) -> Substitute:
        """Полностью заменить поля записи, updated_at = now(); нет записи —
        SubstituteNotFound (404), дубль пары — SubstitutePairTaken (409)."""
        with self._session() as session:
            try:
                row = session.execute(
                    self._UPDATE,
                    {
                        "sub_id": sub_id,
                        "principal_sam": principal_sam,
                        "deputy_sam": deputy_sam,
                        "valid_from": valid_from,
                        "valid_to": valid_to,
                        "notify": notify,
                        "note": note,
                    },
                ).first()
            except IntegrityError as exc:
                raise SubstitutePairTaken(
                    "Такая пара заместителей уже есть в справочнике"
                ) from exc
            session.commit()
        if row is None:
            raise SubstituteNotFound("Запись справочника заместителей не найдена")
        return self._row_to_record(row)

    def delete(self, sub_id: int) -> None:
        """Удалить запись; отсутствующая — SubstituteNotFound (404).

        Внешних ссылок на substitutes нет (миграция 0023), поэтому удаление
        безопасно: история отметок и аудит её не читают."""
        with self._session() as session:
            result = session.execute(self._DELETE, {"sub_id": sub_id})
            deleted = bool(result.rowcount)
            session.commit()
        if not deleted:
            raise SubstituteNotFound("Запись справочника заместителей не найдена")


class InMemorySubstituteStore:
    """Офлайн-хранилище справочника заместителей (словарь процесса).

    Тот же контракт, что у DbSubstituteStore, включая SubstitutePairTaken на
    дубле пары и SubstituteNotFound на неизвестном id: офлайн-прогоны видят тот
    же контракт ошибок, что и боевая БД. Метки времени — datetime.now(UTC),
    как в остальных хранилищах памяти."""

    def __init__(self) -> None:
        self._records: dict[int, Substitute] = {}
        self._next_id = 1

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest/локального запуска."""
        self._records.clear()
        self._next_id = 1

    def _sorted(self, records) -> list[Substitute]:
        return sorted(
            records, key=lambda item: (item.principal_sam, item.deputy_sam, item.id)
        )

    def _reject_pair(self, principal_sam: str, deputy_sam: str, skip_id: int) -> None:
        """Пара уникальна (UNIQUE в БД): чужая запись с той же парой — конфликт."""
        for record in self._records.values():
            if record.id == skip_id:
                continue
            if record.principal_sam == principal_sam and record.deputy_sam == deputy_sam:
                raise SubstitutePairTaken("Такая пара заместителей уже есть в справочнике")

    def list_all(self) -> list[Substitute]:
        return self._sorted(self._records.values())

    def list_active(self, on: date) -> list[Substitute]:
        return self._sorted(
            record
            for record in self._records.values()
            if record.valid_from <= on and (record.valid_to is None or record.valid_to >= on)
        )

    def by_id(self, sub_id: int) -> Substitute | None:
        return self._records.get(sub_id)

    def create(
        self,
        principal_sam: str,
        deputy_sam: str,
        valid_from: date,
        valid_to: date | None,
        notify: bool,
        note: str,
        actor: str,
    ) -> Substitute:
        self._reject_pair(principal_sam, deputy_sam, skip_id=0)
        record = Substitute(
            id=self._next_id,
            principal_sam=principal_sam,
            deputy_sam=deputy_sam,
            valid_from=valid_from,
            valid_to=valid_to,
            notify=bool(notify),
            note=note,
            created_by=str(actor or ""),
            created_at=datetime.now(timezone.utc),
            updated_at=None,
        )
        self._records[record.id] = record
        self._next_id += 1
        return record

    def update(
        self,
        sub_id: int,
        *,
        principal_sam: str,
        deputy_sam: str,
        valid_from: date,
        valid_to: date | None,
        notify: bool,
        note: str,
    ) -> Substitute:
        current = self._records.get(sub_id)
        if current is None:
            raise SubstituteNotFound("Запись справочника заместителей не найдена")
        self._reject_pair(principal_sam, deputy_sam, skip_id=sub_id)
        updated = replace(
            current,
            principal_sam=principal_sam,
            deputy_sam=deputy_sam,
            valid_from=valid_from,
            valid_to=valid_to,
            notify=bool(notify),
            note=note,
            updated_at=datetime.now(timezone.utc),
        )
        self._records[sub_id] = updated
        return updated

    def delete(self, sub_id: int) -> None:
        if self._records.pop(sub_id, None) is None:
            raise SubstituteNotFound("Запись справочника заместителей не найдена")


_db_substitute_store: DbSubstituteStore | None = None


def get_substitute_store(settings: Settings = Depends(get_settings)) -> SubstituteStore:
    """Боевое хранилище справочника заместителей (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется InMemorySubstituteStore через
    dependency_overrides (как get_routing_store/get_links_store)."""
    global _db_substitute_store
    if _db_substitute_store is None:
        _db_substitute_store = DbSubstituteStore(settings.DATABASE_URL)
    return _db_substitute_store

# Кэш состава групп AD в локальной БД: карточка заявки и конструктор читают
# состав без чтения каталога (AD дёргается только на синке — регламентном
# из worker и ручном POST /api/ad/groups/sync). Записи в AD/1С нет: только
# чтение каталога и запись состава у нас.
# Хранилище — зависимость get_groups_cache_store: in-memory в тестах,
# Postgres (DbGroupsCacheStore) на стенде; падение БД — GroupsCacheUnavailable
# -> роутер отвечает 503 (не 500).
#
# ПРИОРИТЕТ ИСТОЧНИКОВ ДОЛЖНОСТЕЙ (исключение!): в справочнике должностей
# для бланков сначала AD (перечисление всех титулов + состав групп), затем —
# только для непокрытых — 1С (distinct из локального справочника employees).
# Везде иначе истина — 1С; обратный приоритет действует ТОЛЬКО здесь.

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .ad_reader import AdNotFound, AdUnavailable
from .config import Settings, get_settings


class GroupsCacheUnavailable(Exception):
    """Хранилище состава групп (БД) недоступно — роутер отвечает 503, а не 500."""


class CachedMember(BaseModel):
    """Участник группы из кэша (строка ad_group_members)."""

    group_name: str = Field(description="Имя группы AD")
    sam: str = Field(description="Логин участника (sAMAccountName)")
    display_name: str = Field(default="", description="ФИО участника")
    department: str | None = Field(default=None, description="Подразделение")
    title: str | None = Field(default=None, description="Должность")
    mail: str | None = Field(default=None, description="Почта")


class GroupsCacheStore(Protocol):
    """Интерфейс хранилища состава групп: единый для in-memory и Postgres."""

    def load(self, group: str) -> tuple[bool, list[CachedMember]]:
        """Состав группы: (синхронизирована?, участники)."""
        ...  # pragma: no cover

    def save(self, group: str, members: list[CachedMember]) -> None:
        """Перезаписать состав группы целиком (метка синка — сейчас)."""
        ...  # pragma: no cover

    def titles(self) -> list[str]:
        """Должности справочника (для наборов бланков): уникальные, сортированные."""
        ...  # pragma: no cover

    def rebuild_directory(self) -> list[str]:
        """Пересобрать справочник должностей по составу групп (после синка):
        удаляет протухшие титулы. Возвращает итоговый список."""
        ...  # pragma: no cover

    def merge_titles(self, titles: list[str]) -> list[str]:
        """Добавить титулы в справочник (без удаления; дубликаты схлопываются).
        Возвращает итоговый список."""
        ...  # pragma: no cover


class InMemoryGroupsCacheStore:
    """Офлайн-хранилище состава (dict), интерфейс GroupsCacheStore."""

    def __init__(self) -> None:
        self._members: dict[str, list[CachedMember]] = {}
        self._synced: set[str] = set()
        self._directory: set[str] = set()

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest."""
        self._members.clear()
        self._synced.clear()
        self._directory.clear()

    def load(self, group: str) -> tuple[bool, list[CachedMember]]:
        return group in self._synced, list(self._members.get(group, []))

    def save(self, group: str, members: list[CachedMember]) -> None:
        self._members[group] = list(members)
        self._synced.add(group)
        for title in self._titles_of(members):
            self._directory.add(title)

    @staticmethod
    def _titles_of(members: list[CachedMember]) -> set[str]:
        return {m.title.strip() for m in members if (m.title or "").strip()}

    def titles(self) -> list[str]:
        return sorted(self._directory)

    def rebuild_directory(self) -> list[str]:
        self._directory = {
            title
            for members in self._members.values()
            for title in self._titles_of(members)
        }
        return sorted(self._directory)

    def merge_titles(self, titles: list[str]) -> list[str]:
        for title in titles or []:
            clean = (title or "").strip()
            if clean:
                self._directory.add(clean)
        return sorted(self._directory)


class DbGroupsCacheStore:
    """Состав групп в Postgres (таблицы ad_group_members/ad_group_sync_state
    из 0006). Ошибки БД оборачиваются в GroupsCacheUnavailable (503)."""

    _SELECT_MEMBERS = text(
        """
        SELECT group_name, sam, display_name, department, title, mail
        FROM ad_group_members
        WHERE group_name = :group_name
        ORDER BY sam
        """
    )
    _SELECT_STATE = text(
        """
        SELECT group_name FROM ad_group_sync_state WHERE group_name = :group_name
        """
    )
    _DELETE_MEMBERS = text(
        "DELETE FROM ad_group_members WHERE group_name = :group_name"
    )
    _UPSERT_MEMBERS = text(
        """
        INSERT INTO ad_group_members
            (group_name, sam, display_name, department, title, mail)
        VALUES (:group_name, :sam, :display_name, :department, :title, :mail)
        """
    )
    _UPSERT_STATE = text(
        """
        INSERT INTO ad_group_sync_state (group_name, synced_at, member_count)
        VALUES (:group_name, :synced_at, :member_count)
        ON CONFLICT (group_name) DO UPDATE SET
            synced_at = EXCLUDED.synced_at,
            member_count = EXCLUDED.member_count
        """
    )
    _SELECT_DIRECTORY = text(
        "SELECT title FROM ad_position_directory ORDER BY title"
    )
    _DELETE_DIRECTORY = text("DELETE FROM ad_position_directory")
    _REBUILD_DIRECTORY = text(
        """
        INSERT INTO ad_position_directory (title, updated_at)
        SELECT DISTINCT title, :updated_at
        FROM ad_group_members
        WHERE title IS NOT NULL AND title <> ''
        """
    )
    _UPSERT_DIRECTORY_TITLE = text(
        """
        INSERT INTO ad_position_directory (title, updated_at)
        VALUES (:title, :updated_at)
        ON CONFLICT (title) DO NOTHING
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def load(self, group: str) -> tuple[bool, list[CachedMember]]:
        try:
            with self._session_factory() as session:
                state = session.execute(
                    self._SELECT_STATE, {"group_name": group}
                ).first()
                if state is None:
                    return False, []
                rows = session.execute(
                    self._SELECT_MEMBERS, {"group_name": group}
                ).all()
        except SQLAlchemyError as exc:
            raise GroupsCacheUnavailable(
                f"Хранилище состава групп недоступно: {exc}"
            ) from exc
        return True, [
            CachedMember(
                group_name=row.group_name,
                sam=row.sam,
                display_name=row.display_name or "",
                department=row.department,
                title=row.title,
                mail=row.mail,
            )
            for row in rows
        ]

    def save(self, group: str, members: list[CachedMember]) -> None:
        try:
            with self._session_factory() as session:
                session.execute(self._DELETE_MEMBERS, {"group_name": group})
                for member in members:
                    session.execute(
                        self._UPSERT_MEMBERS,
                        {
                            "group_name": group,
                            "sam": member.sam,
                            "display_name": member.display_name or "",
                            "department": member.department,
                            "title": member.title,
                            "mail": member.mail,
                        },
                    )
                session.execute(
                    self._UPSERT_STATE,
                    {
                        "group_name": group,
                        "synced_at": datetime.now(timezone.utc),
                        "member_count": len(members),
                    },
                )
                moment = datetime.now(timezone.utc)
                for member in members:
                    title = (member.title or "").strip()
                    if title:
                        session.execute(
                            self._UPSERT_DIRECTORY_TITLE,
                            {"title": title, "updated_at": moment},
                        )
                session.commit()
        except SQLAlchemyError as exc:
            raise GroupsCacheUnavailable(
                f"Хранилище состава групп недоступно: {exc}"
            ) from exc

    def titles(self) -> list[str]:
        try:
            with self._session_factory() as session:
                rows = session.execute(self._SELECT_DIRECTORY).all()
        except SQLAlchemyError as exc:
            raise GroupsCacheUnavailable(
                f"Хранилище состава групп недоступно: {exc}"
            ) from exc
        return [row[0] for row in rows]

    def rebuild_directory(self) -> list[str]:
        try:
            with self._session_factory() as session:
                session.execute(self._DELETE_DIRECTORY)
                session.execute(
                    self._REBUILD_DIRECTORY,
                    {"updated_at": datetime.now(timezone.utc)},
                )
                session.commit()
                rows = session.execute(self._SELECT_DIRECTORY).all()
        except SQLAlchemyError as exc:
            raise GroupsCacheUnavailable(
                f"Хранилище состава групп недоступно: {exc}"
            ) from exc
        return [row[0] for row in rows]

    def merge_titles(self, titles: list[str]) -> list[str]:
        try:
            with self._session_factory() as session:
                moment = datetime.now(timezone.utc)
                for title in titles or []:
                    clean = (title or "").strip()
                    if clean:
                        session.execute(
                            self._UPSERT_DIRECTORY_TITLE,
                            {"title": clean, "updated_at": moment},
                        )
                session.commit()
                rows = session.execute(self._SELECT_DIRECTORY).all()
        except SQLAlchemyError as exc:
            raise GroupsCacheUnavailable(
                f"Хранилище состава групп недоступно: {exc}"
            ) from exc
        return [row[0] for row in rows]


_db_groups_cache_store: DbGroupsCacheStore | None = None


def get_groups_cache_store(settings: Settings = Depends(get_settings)) -> GroupsCacheStore:
    """Боевое хранилище состава групп (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется InMemoryGroupsCacheStore через
    dependency_overrides (как get_links_store/get_requests_store).
    """
    global _db_groups_cache_store
    if _db_groups_cache_store is None:
        _db_groups_cache_store = DbGroupsCacheStore(settings.DATABASE_URL)
    return _db_groups_cache_store


def sync_ad_group_members(reader, groups: list[str], cache: GroupsCacheStore) -> dict:
    """Синхронизировать состав групп из AD в кэш (ручной и регламентный синк).

    reader — AdReader (только чтение group_members + перечисление титулов);
    groups — имена групп (пустые отбрасываются). Группа не найдена в AD
    (AdNotFound) — ошибка в errors, метка синка НЕ ставится (эндпоинт, как
    раньше, отдаст 404 живым чтением). Сбой каталога (AdUnavailable) — ошибка
    в errors, метка тоже не ставится. В конце — пересборка справочника
    должностей из состава групп + сплошное перечисление титулов AD
    (1С-добор — на чтении /api/ad/titles). Возврат: {synced_groups, members,
    titles, errors}.
    """
    synced_groups = 0
    members_total = 0
    errors: list[str] = []
    for group in [g.strip() for g in groups or [] if g and g.strip()]:
        try:
            users = reader.group_members(group)
        except AdNotFound as exc:
            errors.append(str(exc))
            continue
        except AdUnavailable as exc:
            errors.append(str(exc))
            continue
        cache.save(
            group,
            [
                CachedMember(
                    group_name=group,
                    sam=u.sam,
                    display_name=u.display_name or "",
                    department=u.department or None,
                    title=u.title or None,
                    mail=u.mail or None,
                )
                for u in users
            ],
        )
        synced_groups += 1
        members_total += len(users)
    # Справочник должностей — по итогам синка (и авто, и ручного: обе точки
    # идут через эту функцию): сначала строгая пересборка из состава групп
    # (чистит протухшее), затем слияние сплошного перечисления титулов AD.
    # Перечисление best-effort (сбой не валит синк групп).
    titles = cache.rebuild_directory()
    try:
        titles = cache.merge_titles(reader.list_all_titles())
    except Exception:
        pass
    return {
        "synced_groups": synced_groups,
        "members": members_total,
        "titles": len(titles),
        "errors": errors,
    }


def maybe_sync_ad_groups_weekly(settings_store, cache_store, reader) -> bool:
    """Регламентный синк состава групп (worker): тихо, без сбоев.

    «Не пора» по расписанию schedule_ad_groups_sync (нет расписания — раз в 7
    дней от ad_groups_synced_at) — False; нет ридера AD — False; нет групп
    в справочнике — False; иначе sync_ad_group_members (сбой не валит worker —
    False). Успех — метка ad_groups_synced_at — True."""
    import json

    from .onec_sync import due_schedule
    from .settings_routes import _groups_with_names, read_setting_value

    if reader is None:
        return False
    schedule = read_setting_value(settings_store, "schedule_ad_groups_sync")
    last_raw = read_setting_value(settings_store, "ad_groups_synced_at")
    if not due_schedule(schedule, last_raw):
        return False
    raw = read_setting_value(settings_store, "allowed_ad_groups")
    groups = (
        [item["id"] for item in _groups_with_names(raw)] if isinstance(raw, list) else []
    )
    if not groups:
        return False
    try:
        sync_ad_group_members(reader, groups, cache_store)
        settings_store.set(
            "ad_groups_synced_at",
            json.dumps(datetime.now(timezone.utc).isoformat()),
        )
        return True
    except Exception:
        return False

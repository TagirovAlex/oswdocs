# Прикладные настройки (админка): GET/PUT /settings, только роль admin.
# Хранилище — таблица settings в Postgres (JSONB value), общение строкой
# в «сид-формате» (db/seeds/settings.sql): int '3', bool 'true', строка '"..."'.
# Редактируются только 5 прикладных ключей контракта; остальные настройки
# (OU, группы, предприятия, шаблоны) админка не трогает.
# Падение БД -> 503, а не 500 (как RedisSessionStore -> SessionUnavailable в auth).
# Зависимость get_settings_store() НЕ конфликтует с get_settings из config.py.

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user

router = APIRouter(tags=["настройки"])


class SettingsUnavailable(Exception):
    """Хранилище настроек (БД) недоступно — роутер отвечает 503, а не 500."""


# Прикладные ключи админки (состав — контракт GET/PUT /settings).
SETTINGS_KEYS: tuple[str, ...] = (
    "approval_ttl_days",
    "scan_retention_days",
    "scan_max_mb",
    "require_paper_signature",
    "smtp_from",
)


def _to_stored(value: object) -> str:
    """Типизированное значение в сид-формат строки: json.dumps дает
    '3'/'true'/'\"sed@example.com\"' — тот же формат, что в сидах."""
    return json.dumps(value)


def _from_stored(raw: str | None) -> object:
    """Строка сид-формата в типизированное значение; ключа нет (или битое
    значение) — None. Дефолтов в коде нет: значения живут только в БД
    (AGENTS.md п.3), отсутствующий ключ админ заполняет через PUT."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # JSONB всегда валиден, ветка чисто защитная.
        return None


class DbSettingsStore:
    """Хранилище прикладных настроек в Postgres (таблица settings: key/value).

    Значения — JSONB; наружу и внутрь — строки сид-формата. Ошибки БД
    оборачиваются в SettingsUnavailable (503), как SessionUnavailable в auth.
    """

    _GET_SQL = text("SELECT CAST(value AS text) FROM settings WHERE key = :key")
    _GET_MANY_SQL = text(
        "SELECT key, CAST(value AS text) FROM settings WHERE key IN :keys"
    ).bindparams(bindparam("keys", expanding=True))
    _UPSERT_SQL = text(
        """
        INSERT INTO settings (key, value) VALUES (:key, CAST(:value AS jsonb))
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def get(self, key: str) -> str | None:
        """Значение ключа в сид-формате либо None (ключа нет в БД)."""
        try:
            with self._session_factory() as session:
                row = session.execute(self._GET_SQL, {"key": key}).first()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        """Записать ключ (upsert); value — строка сид-формата."""
        try:
            with self._session_factory() as session:
                session.execute(self._UPSERT_SQL, {"key": key, "value": value})
                session.commit()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc

    def get_many(self, keys: Sequence[str]) -> dict[str, str]:
        """Значения по ключам: {key: строка сид-формата}; отсутствующих нет."""
        try:
            with self._session_factory() as session:
                rows = session.execute(self._GET_MANY_SQL, {"keys": list(keys)}).all()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return {row[0]: row[1] for row in rows}

    def set_many(self, values: Mapping[str, str]) -> None:
        """Записать несколько ключей одним батчем (upsert)."""
        try:
            with self._session_factory() as session:
                for key, value in values.items():
                    session.execute(self._UPSERT_SQL, {"key": key, "value": value})
                session.commit()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc


_db_store: DbSettingsStore | None = None


def get_settings_store(settings: Settings = Depends(get_settings)) -> DbSettingsStore:
    """Боевое хранилище настроек (Postgres): один движок на процесс.

    В офлайн-тестах подменяется in-memory моком через dependency_overrides
    (аналог get_auth_service/get_onec_client). Имя не конфликтует с
    get_settings из config.py (тот — pydantic-Settings из env).
    """
    global _db_store
    if _db_store is None:
        _db_store = DbSettingsStore(settings.DATABASE_URL)
    return _db_store


class SettingsPayload(BaseModel):
    """Тело GET/PUT /settings: 5 прикладных ключей (в PUT все обязательны)."""

    approval_ttl_days: int = Field(..., description="Срок отметки шага в днях")
    scan_retention_days: int = Field(..., description="Срок хранения сканов в днях")
    scan_max_mb: int = Field(..., description="Максимальный размер скана в МБ")
    require_paper_signature: bool = Field(..., description="Нужна ли бумажная подпись")
    smtp_from: str = Field(..., description="Отправитель уведомлений (SMTP FROM)")


def _require_admin(user: CurrentUser) -> None:
    """Настройки — строго роль admin (ОК/владельцам 403; is_privileged не подходит)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Настройки доступны только администраторам",
        )


def _require_hr(user: CurrentUser) -> None:
    """Справочники Волны 1 (предприятия/группы шагов) — только ОК и админы."""
    if user.role not in ("hr", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Справочники доступны только разрешенной группе",
        )


def _settings_dict(store: DbSettingsStore) -> dict:
    """Типизированный словарь настроек из хранилища (None — ключа нет в БД)."""
    raw = store.get_many(SETTINGS_KEYS)
    return {key: _from_stored(raw.get(key)) for key in SETTINGS_KEYS}


@router.get("/settings")
def read_settings(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Прикладные настройки админки: только admin, иначе 403; БД недоступна — 503."""
    _require_admin(user)
    try:
        values = _settings_dict(store)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.read",
            entity="settings",
            entity_id=",".join(SETTINGS_KEYS),
        )
    )
    return values


@router.put("/settings")
def update_settings(
    payload: SettingsPayload,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Обновить настройки: только admin, все 5 полей обязательны (иначе 422)."""
    _require_admin(user)
    stored = {key: _to_stored(getattr(payload, key)) for key in SETTINGS_KEYS}
    try:
        store.set_many(stored)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.update",
            entity="settings",
            entity_id=",".join(SETTINGS_KEYS),
        )
    )
    return payload.model_dump()


@router.get("/enterprises")
def list_enterprises(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> list[dict]:
    """Предприятия (Волна 1): ОК/админы, иначе 403; БД недоступна — 503.
    Значения — только из таблицы settings (ключ enterprises), хардкода нет."""
    _require_hr(user)
    try:
        raw = store.get("enterprises")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    values = _from_stored(raw)
    items = [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="enterprises.read",
            entity="enterprise",
            entity_id="enterprises",
        )
    )
    return items


@router.get("/step-groups")
def list_step_groups(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> list[str]:
    """Группы ручного конструктора шагов (Волна 1): ОК/админы, иначе 403;
    значения — из таблицы settings (ключ allowed_ad_groups), хардкода нет."""
    _require_hr(user)
    try:
        raw = store.get("allowed_ad_groups")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    values = _from_stored(raw)
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str)]
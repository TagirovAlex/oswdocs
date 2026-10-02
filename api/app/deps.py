# Зависимости FastAPI: текущий пользователь и ролевая проверка.
# Реальный путь — Bearer-токен через AuthService.me() (сессия в Redis);
# мок-путь на заголовках X-Mock-* работает только при AUTH_MOCK_ENABLED=true
# (офлайн-тесты; на ВМ флаг=false, README п.1 и TASKS_AUTH.md).
# Вход по мок-пути — объединение env ALLOWED_AD_GROUPS (bootstrap) и
# инфра-ключа access_groups из БД; роль — эффективные группы ролей (БД→env).
# Контракт CurrentUser/401/403 сохраняется.

from __future__ import annotations

import base64

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .config import Settings, get_settings


class CurrentUser(BaseModel):
    """Аутентифицированный пользователь (минимум для ролевой обрезки)."""

    sam: str = Field(description="Логин AD (sAMAccountName)")
    fio: str | None = Field(default=None, description="Полное ФИО (ПДн)")
    mail: str | None = Field(default=None, description="Почта из AD (ПДн)")
    department: str | None = Field(default=None, description="Подразделение из AD")
    title: str | None = Field(default=None, description="Должность из AD")
    groups: list[str] = Field(default_factory=list, description="Группы memberOf")
    role: str = Field(default="owner", description="admin | hr_admin | hr | owner")


def _split_groups(raw: str | None) -> list[str]:
    """Разбор заголовка групп: запятая как разделитель, пустые отбрасываются."""
    if not raw:
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


def _decode_mock(raw: str | None) -> str | None:
    """Декодирование мок-заголовка: base64(utf-8), иначе как есть.

    HTTP-заголовки обязаны быть ASCII, поэтому кириллица (ФИО и др.)
    едет в base64 (кодирует mock_headers в conftest). На стенде эти
    заголовки исчезнут вместе с моком (замена на LDAPS bind).
    """
    if not raw:
        return None
    try:
        return base64.b64decode(raw.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError):
        return raw


def _settings_store_or_none(request: Request, settings: Settings) -> object | None:
    """Хранилище настроек для мок-пути входа: подмена из конфигурации приложения
    (dependency_overrides — офлайн-тесты), иначе боевое DbSettingsStore.

    Импорт settings_routes ленивый (модуль импортирует deps), а подмена берётся
    из dependency_overrides, чтобы офлайн-прогоны не ходили в живой Postgres.
    Любая ошибка — None: вход и роли считаются по env (фолбэк, не 500)."""
    try:
        from .settings_routes import get_settings_store

        overrides = getattr(request.app, "dependency_overrides", {})
        factory = overrides.get(get_settings_store)
        return factory() if factory is not None else get_settings_store(settings)
    except Exception:
        return None


def _login_group_allowed(allowed: set[str], prefix: str, group: str) -> bool:
    """Группа даёт вход: в объединённом наборе (env + access_groups из БД)
    либо по префиксу групп владельцев шагов (то же правило, что в auth)."""
    name = (group or "").strip()
    if not name:
        return False
    return name in allowed or (bool(prefix) and name.startswith(prefix))


def _detect_role(
    groups: list[str], settings: Settings, store: object | None = None
) -> str:
    """Роль по группам: эффективные группы ролей из настроек БД
    (admin_groups/hr_groups/hr_admin_groups), иначе env. Админ важнее
    руководителя ОК, тот важнее ОК, а ОК — важнее владельца шага.

    store — DbSettingsStore или его подмена (аннотация object, чтобы не тянуть
    settings_routes на уровень модуля: цикл импорта); None/ошибка БД — env.
    """
    from .settings_routes import resolve_role_groups

    role_groups = resolve_role_groups(settings, store)
    group_set = set(groups)
    if group_set & role_groups["admin_groups"]:
        return "admin"
    if group_set & role_groups["hr_admin_groups"]:
        return "hr_admin"
    if group_set & role_groups["hr_groups"]:
        return "hr"
    return "owner"


def _bearer_token(authorization: str | None) -> str | None:
    """Выделить токен из 'Authorization: Bearer <token>' (иначе None)."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not token:
        return None
    return token.strip()


def is_privileged(user: CurrentUser) -> bool:
    """Полная карточка положена только ОК, руководителям ОК и админам
    (остальным — урезанная)."""
    return user.role in ("admin", "hr_admin", "hr")


async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
    x_mock_sam: str | None = Header(default=None),
    x_mock_fio: str | None = Header(default=None),
    x_mock_mail: str | None = Header(default=None),
    x_mock_department: str | None = Header(default=None),
    x_mock_title: str | None = Header(default=None),
    x_mock_groups: str | None = Header(default=None),
) -> CurrentUser:
    """Текущий пользователь: мок-путь (X-Mock-*) за флагом AUTH_MOCK_ENABLED,
    иначе — Bearer-токен через AuthService.me(). Роль считает _detect_role."""
    if not settings.AUTH_MOCK_ENABLED:
        # Реальный путь: токен сессии из Redis (LDAPS bind — на входе /auth/login).
        from .auth import SessionUnavailable, get_auth_service

        token = _bearer_token(authorization)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Нет учетных данных (Authorization: Bearer <token>)",
            )
        try:
            user = get_auth_service(settings).me(token)
        except SessionUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Сессия не найдена или истекла",
            )
        return user
    # Мок-путь (офлайн-тесты; на ВМ AUTH_MOCK_ENABLED=false).
    if not x_mock_sam:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нет учетных данных",
        )
    groups = _split_groups(x_mock_groups)
    # Вход по объединённому набору: env (bootstrap) + access_groups из БД.
    from .settings_routes import resolve_allowed_groups

    store = _settings_store_or_none(request, settings)
    allowed = resolve_allowed_groups(settings, store)
    prefix = settings.STEP_GROUP_PREFIX
    if not any(_login_group_allowed(allowed, prefix, g) for g in groups):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа: пользователь не входит в разрешенные группы",
        )
    return CurrentUser(
        sam=x_mock_sam,
        fio=_decode_mock(x_mock_fio),
        mail=_decode_mock(x_mock_mail),
        department=_decode_mock(x_mock_department),
        title=_decode_mock(x_mock_title),
        groups=groups,
        role=_detect_role(groups, settings, store),
    )

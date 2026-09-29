# Зависимости FastAPI: текущий пользователь и ролевая проверка (скелет).
# Сейчас — заглушка на заголовках X-Mock-* для offline-тестов.
# На стенде (волны A4/B1) будет заменена LDAPS bind + проверка memberOf
# по группам из settings; контракт CurrentUser/401/403 сохранится.

from __future__ import annotations

import base64

from fastapi import Depends, Header, HTTPException, status
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
    role: str = Field(default="owner", description="admin | hr | owner")


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


def _detect_role(groups: list[str], settings: Settings) -> str:
    """Роль по группам из настроек: админ важнее ОК, ОК важнее владельца шага."""
    group_set = set(groups)
    if group_set & settings.admin_groups:
        return "admin"
    if group_set & settings.hr_groups:
        return "hr"
    return "owner"


def is_privileged(user: CurrentUser) -> bool:
    """Полная карточка положена только ОК и админам (остальным — урезанная)."""
    return user.role in ("admin", "hr")


async def get_current_user(
    settings: Settings = Depends(get_settings),
    x_mock_sam: str | None = Header(default=None),
    x_mock_fio: str | None = Header(default=None),
    x_mock_mail: str | None = Header(default=None),
    x_mock_department: str | None = Header(default=None),
    x_mock_title: str | None = Header(default=None),
    x_mock_groups: str | None = Header(default=None),
) -> CurrentUser:
    """Текущий пользователь из мок-заголовков; нет группы — 403, нет логина — 401."""
    if not x_mock_sam:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нет учетных данных",
        )
    groups = _split_groups(x_mock_groups)
    if not any(settings.is_group_allowed(g) for g in groups):
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
        role=_detect_role(groups, settings),
    )

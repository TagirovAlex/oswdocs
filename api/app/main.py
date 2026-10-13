# Точка входа API СЭД (волна B: подключены employees/link/requests/auth через include_router).
# Эндпоинты: GET /health (без auth), GET /me (заглушка с ролевой обрезкой),
# POST /auth/login, GET /auth/me (LDAPS bind + сессии в Redis); логика — в своих модулях.

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import archive as archive_routes  # блок F: закладка «Архивация» (бэкапы)
from . import attachments as attachments_routes
from . import auth as auth_routes
from . import documents as documents_routes
from . import employees as employees_routes
from . import link as link_routes
from . import requests as requests_routes
from . import settings_routes as settings_routes
# Импорт включает слушатель SQLAlchemy, который задаёт часовую зону соединениям
# БД из настройки timezone (app/timezone.py). Модуль нужно импортировать до первого
# соединения с БД — регистрация роутеров ниже этого момента ещё не начата.
from . import timezone as _app_timezone  # noqa: F401
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user, is_privileged


def _load_system_timezone() -> None:
    """Зона системы на старте процесса (settings.timezone, иначе TZ).

    Читаем явно, а не по первому /auth/me: фоновые вещи (worker-проходы, почта)
    считают время раньше, чем придёт первый запрос. Ошибка не поднимает процесс —
    остаётся зона контейнера, как и до настройки."""
    try:
        from .settings_routes import get_settings_store

        # Через модуль, а не прямой импорт: офлайн-тесты глушат именно
        # timezone.refresh_timezone_from_settings (БД нет — читать зону нечем).
        _app_timezone.refresh_timezone_from_settings(get_settings_store(get_settings()))
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Старт приложения (lifespan): зона системы читается до первого запроса."""
    _load_system_timezone()
    yield


app = FastAPI(title="SED API", version="0.1.0", lifespan=_lifespan)


app.include_router(archive_routes.router)  # блок F: /api/archive*
app.include_router(auth_routes.router)
app.include_router(employees_routes.router)
app.include_router(link_routes.router)
app.include_router(requests_routes.router)
app.include_router(settings_routes.router)
app.include_router(documents_routes.router)
app.include_router(attachments_routes.router)


@app.get("/health")
def health() -> dict:
    """Проверка живости без авторизации (для probe compose/мониторинга)."""
    return {"status": "ok"}


@app.get("/me")
def me(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Текущий пользователь: ОК/админам — полная заглушка, владельцам — урезанная без ПДн."""
    settings.ensure_read_only()
    audit_log.append(
        AuditEvent(actor=user.sam, action="me.read", entity="user", entity_id=user.sam)
    )
    if is_privileged(user):
        return {
            "sam": user.sam,
            "fio": user.fio,
            "department": user.department,
            "title": user.title,
            "mail": user.mail,
            "groups": user.groups,
            "role": user.role,
        }
    # Урезанная: без ФИО/подразделения/должности/почты (ПДн не светят владельцам).
    return {"sam": user.sam, "groups": user.groups, "role": user.role}

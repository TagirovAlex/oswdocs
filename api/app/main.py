# Точка входа API СЭД (волна B: подключены employees/link/requests/auth через include_router).
# Эндпоинты: GET /health (без auth), GET /me (заглушка с ролевой обрезкой),
# POST /auth/login, GET /auth/me (LDAPS bind + сессии в Redis); логика — в своих модулях.

from __future__ import annotations

from fastapi import Depends, FastAPI

from . import attachments as attachments_routes
from . import auth as auth_routes
from . import documents as documents_routes
from . import employees as employees_routes
from . import link as link_routes
from . import requests as requests_routes
from . import settings_routes as settings_routes
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user, is_privileged

app = FastAPI(title="SED API", version="0.1.0")
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

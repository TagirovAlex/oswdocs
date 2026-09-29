# Скелетные тесты API волны A2: health, матрица auth /me, append-only аудита.
# Пользователи и заголовки — из фикстур conftest.py (все ПДн вымышленные).
# Группы доступа подменяются через dependency_overrides[get_settings]:
# дефолты кода нейтральные, тестовые имена живут только здесь, реальные —
# только через env/settings стенда.

from __future__ import annotations

import base64
import os
import sys

import pytest

# Независимость от порядка загрузки conftest: кладем api/ в sys.path локально.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import AuditEvent, AuditLogger, audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402

# Тестовые группы соответствуют фикстурам conftest (вымышленные имена
# тестовых данных, не продовые значения: продовые идут только через env/БД).
TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"


@pytest.fixture
def test_settings_override():
    """Подмена групп тестовыми: дефолты кода остаются нейтральными."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


def _b64decode(raw: str) -> str:
    """Декодирование мок-заголовка conftest (base64 utf-8)."""
    return base64.b64decode(raw.encode("ascii")).decode("utf-8")


def test_health_ok(client):
    """Проверка живости доступна без авторизации."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_no_auth_401(client, noauth_headers):
    """Без логина — 401."""
    response = client.get("/me", headers=noauth_headers)
    assert response.status_code == 401


def test_me_no_group_403(client, nogroup_headers, test_settings_override):
    """Пользователь без разрешающих групп — 403."""
    response = client.get("/me", headers=nogroup_headers)
    assert response.status_code == 403


def test_me_hr_full(client, hr_headers, test_settings_override):
    """ОК: полная заглушка с ПДн и ролью hr."""
    response = client.get("/me", headers=hr_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "hr"
    assert body["sam"] == hr_headers["X-Mock-Sam"]
    assert body["fio"] == _b64decode(hr_headers["X-Mock-Fio"])
    assert body["mail"] == _b64decode(hr_headers["X-Mock-Mail"])
    assert body["department"] == _b64decode(hr_headers["X-Mock-Department"])
    assert body["title"] == _b64decode(hr_headers["X-Mock-Title"])


def test_me_owner_trimmed_no_pdn(client, owner_headers, test_settings_override):
    """Владелец шага: урезанная без ПДн (только sam/groups/role)."""
    response = client.get("/me", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert body["sam"] == owner_headers["X-Mock-Sam"]
    assert set(body) == {"sam", "groups", "role"}
    for forbidden in ("fio", "mail", "department", "title"):
        assert forbidden not in body


def test_audit_append_only(client, hr_headers, test_settings_override):
    """Чтение /me пишется в аудит; у журнала нет изменения/удаления."""
    assert audit_log.all() == []
    response = client.get("/me", headers=hr_headers)
    assert response.status_code == 200
    events = audit_log.all()
    assert len(events) == 1
    assert events[0].action == "me.read"
    assert events[0].actor == hr_headers["X-Mock-Sam"]
    assert not hasattr(AuditLogger, "update")
    assert not hasattr(AuditLogger, "delete")
    assert isinstance(audit_log.append(AuditEvent(actor="t", action="t")), AuditEvent)

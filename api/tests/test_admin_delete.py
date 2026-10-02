# Тесты удаления заявки (тестовый период): DELETE /requests/{id} — только админ.
# Хранилища in-memory через dependency_overrides (БД/ФС не вызываются), поэтому
# тесты не зависят от Postgres и не висят. ПДн вымышленные.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    RouteSettings,
    get_memory_requests_store,
    get_route_settings,
    reset_state_for_tests,
)
from app.requests_store import get_requests_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"
FAKE_ENTERPRISE = "Вымышленное предприятие"


@pytest.fixture(autouse=True)
def settings_override():
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(autouse=True)
def route_override():
    route = RouteSettings(approval_ttl_days=7)
    app.dependency_overrides[get_route_settings] = lambda: route
    yield route
    app.dependency_overrides.pop(get_route_settings, None)


@pytest.fixture(autouse=True)
def requests_store():
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_requests_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    reset_state_for_tests()
    audit_log.clear_for_tests()
    yield
    reset_state_for_tests()
    audit_log.clear_for_tests()


def _create(client, headers, **kw) -> dict:
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": "Вымышленный Сотрудник Полный",
        "tab_num": "В-0001",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION,
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
    }
    body.update(kw)
    response = client.post("/requests", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_deletes_request(client, hr_headers, admin_headers, requests_store):
    """Админ удаляет заявку: 200, повторный GET — 404, хранилище пусто, есть аудит."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = client.delete(f"/requests/{rid}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"deleted": rid}
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 404
    assert requests_store.list_all() == []
    events = [e for e in audit_log.all() if e.action == "request.delete"]
    assert len(events) == 1
    assert events[0].entity_id == rid
    assert events[0].actor == "adm.petrov"


def test_hr_cannot_delete(client, hr_headers, admin_headers):
    """Удаление — только админ: ОК получает 403, заявка на месте."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = client.delete(f"/requests/{rid}", headers=hr_headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Удаление заявок — только админ"
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 200


def test_delete_missing_request_404(client, admin_headers):
    """Несуществующей заявки нет — 404."""
    response = client.delete("/requests/REQ-9999", headers=admin_headers)
    assert response.status_code == 404
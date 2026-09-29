# Тесты админки настроек: GET/PUT /settings (только admin), хранилище — in-memory мок.
# Живого Postgres нет: get_settings_store подменяется через dependency_overrides
# (как get_auth_service/get_onec_client в других тестах). ПДн вымышленные.
# Контракт: GET 200 для admin, PUT сохраняет и возвращает, 403 для hr/owner,
# 422 на неверные типы, 503 при падении хранилища.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.settings_routes import (  # noqa: E402
    SETTINGS_KEYS,
    SettingsUnavailable,
    get_settings_store,
)

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Сид-формат значений (как в db/seeds/settings.sql): int '3', bool 'true', строка '"..."'.
SEED_VALUES = {
    "approval_ttl_days": "3",
    "scan_retention_days": "365",
    "scan_max_mb": "10",
    "require_paper_signature": "true",
    "smtp_from": '"sed@example.com"',
}

# Контрактный ответ GET /settings (ключи на месте).
CONTRACT_VALUES = {
    "approval_ttl_days": 3,
    "scan_retention_days": 365,
    "scan_max_mb": 10,
    "require_paper_signature": True,
    "smtp_from": "sed@example.com",
}


class InMemorySettingsStore:
    """Мок хранилища настроек: dict вместо Postgres, формат значений — сид-формат."""

    def __init__(self, initial=None, broken=False):
        self._data = dict(initial or {})
        self.broken = broken

    def _check(self):
        if self.broken:
            raise SettingsUnavailable("Хранилище настроек недоступно (тест)")

    def get(self, key):
        self._check()
        return self._data.get(key)

    def set(self, key, value):
        self._check()
        self._data[key] = value

    def get_many(self, keys):
        self._check()
        return {k: v for k, v in self._data.items() if k in keys}

    def set_many(self, values):
        self._check()
        self._data.update(values)


@pytest.fixture
def settings_override():
    """Тестовые группы (дефолты кода нейтральные) + возврат после теста."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def mock_store(settings_override):
    """In-memory хранилище с сид-значениями вместо Postgres."""
    store = InMemorySettingsStore(initial=dict(SEED_VALUES))
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


# --- GET /settings ---

def test_settings_get_admin_200(client, admin_headers, mock_store):
    """Админ читает настройки: 200, значения по контракту (типы int/int/int/bool/str)."""
    response = client.get("/settings", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == CONTRACT_VALUES


def test_settings_get_missing_keys_none(client, admin_headers, settings_override):
    """Ключей нет в БД — в ответе None (дефолтов в коде нет, значения только из БД)."""
    store = InMemorySettingsStore(initial={})
    app.dependency_overrides[get_settings_store] = lambda: store
    try:
        response = client.get("/settings", headers=admin_headers)
    finally:
        app.dependency_overrides.pop(get_settings_store, None)
    assert response.status_code == 200
    assert response.json() == {key: None for key in SETTINGS_KEYS}


def test_settings_get_no_auth_401(client, noauth_headers, settings_override):
    """Без логина — 401 (проверка deps, до проверки роли)."""
    response = client.get("/settings", headers=noauth_headers)
    assert response.status_code == 401


def test_settings_get_hr_403(client, hr_headers, mock_store):
    """ОК (hr) не читает настройки — 403 (admin-only, is_privileged не подходит)."""
    response = client.get("/settings", headers=hr_headers)
    assert response.status_code == 403


def test_settings_get_owner_403(client, owner_headers, mock_store):
    """Владелец шага не читает настройки — 403."""
    response = client.get("/settings", headers=owner_headers)
    assert response.status_code == 403


def test_settings_get_store_down_503(client, admin_headers, mock_store):
    """Хранилище недоступно — 503, а не 500."""
    mock_store.broken = True
    response = client.get("/settings", headers=admin_headers)
    assert response.status_code == 503


# --- PUT /settings ---

def test_settings_put_admin_200_persists(client, admin_headers, mock_store):
    """Админ сохраняет настройки: 200, значения возвращены, в хранилище — сид-формат."""
    payload = {
        "approval_ttl_days": 7,
        "scan_retention_days": 730,
        "scan_max_mb": 25,
        "require_paper_signature": False,
        "smtp_from": "noreply@example.com",
    }
    response = client.put("/settings", json=payload, headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == payload
    # Формат записи в БД — как в сидах: int строкой, bool 'true'/'false', строка JSON-строкой.
    assert mock_store._data["approval_ttl_days"] == "7"
    assert mock_store._data["scan_retention_days"] == "730"
    assert mock_store._data["scan_max_mb"] == "25"
    assert mock_store._data["require_paper_signature"] == "false"
    assert mock_store._data["smtp_from"] == '"noreply@example.com"'
    # Последующее чтение возвращает сохраненное.
    got = client.get("/settings", headers=admin_headers)
    assert got.status_code == 200
    assert got.json() == payload


def test_settings_put_hr_403(client, hr_headers, mock_store):
    """ОК не редактирует настройки — 403."""
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=hr_headers)
    assert response.status_code == 403


def test_settings_put_owner_403(client, owner_headers, mock_store):
    """Владелец шага не редактирует настройки — 403."""
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=owner_headers)
    assert response.status_code == 403


def test_settings_put_wrong_types_422(client, admin_headers, mock_store):
    """Неверные типы (строка вместо int, строка вместо bool) — 422."""
    bad_int = dict(CONTRACT_VALUES, approval_ttl_days="abc")
    assert client.put("/settings", json=bad_int, headers=admin_headers).status_code == 422
    bad_bool = dict(CONTRACT_VALUES, require_paper_signature=123)
    assert client.put("/settings", json=bad_bool, headers=admin_headers).status_code == 422


def test_settings_put_missing_field_422(client, admin_headers, mock_store):
    """Все 5 полей обязательны: пропуск smtp_from — 422."""
    payload = {k: v for k, v in CONTRACT_VALUES.items() if k != "smtp_from"}
    response = client.put("/settings", json=payload, headers=admin_headers)
    assert response.status_code == 422


def test_settings_put_store_down_503(client, admin_headers, mock_store):
    """Хранилище недоступно при записи — 503."""
    mock_store.broken = True
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=admin_headers)
    assert response.status_code == 503


# --- Аудит ---

def test_settings_audit_read_and_update(client, admin_headers, mock_store):
    """Чтение пишет settings.read, обновление — settings.update (actor — sam)."""
    assert audit_log.all() == []
    client.get("/settings", headers=admin_headers)
    read_events = [e for e in audit_log.all() if e.action == "settings.read"]
    assert len(read_events) == 1
    assert read_events[0].actor == admin_headers["X-Mock-Sam"]
    assert read_events[0].entity == "settings"
    client.put("/settings", json=dict(CONTRACT_VALUES), headers=admin_headers)
    update_events = [e for e in audit_log.all() if e.action == "settings.update"]
    assert len(update_events) == 1
    assert update_events[0].actor == admin_headers["X-Mock-Sam"]


def test_settings_audit_not_written_on_403(client, hr_headers, mock_store):
    """Отказ по роли в аудит не пишется (события нет)."""
    client.get("/settings", headers=hr_headers)
    assert audit_log.all() == []
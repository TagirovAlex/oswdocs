# Тесты админки настроек: GET/PUT /settings (только admin) и GET/PUT
# /settings/content (admin + руководитель ОК), хранилище — in-memory мок.
# Живого Postgres нет: get_settings_store подменяется через dependency_overrides
# (как get_auth_service/get_onec_client в других тестах). ПДн вымышленные.
# Контракт B2: GET 200 для admin со всеми ключами, PUT — частичное обновление
# (пишутся только присутствующие ключи, ответ — полное состояние), 403 для hr/owner,
# 422 на неверные типы, 503 при падении хранилища. Контент-ключи — для hr_admin.

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.settings_routes import (  # noqa: E402
    CONTENT_KEYS,
    INFRA_KEYS,
    SETTINGS_KEYS,
    SMTP_PASSWORD_MASK,
    SettingsUnavailable,
    get_settings_store,
)

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_STEP_PREFIX = "SED_STEP_"

# Сид-формат значений (как в db/seeds/settings.sql): int '3', bool 'true',
# строка '"..."', массив/объект — JSON-строкой.
SEED_VALUES = {
    "session_ttl_minutes": "600",
    "approval_ttl_days": "3",
    "scan_retention_days": "365",
    "scan_max_mb": "10",
    "scan_allowed_types": '["application/pdf", "image/jpeg", "image/png"]',
    "require_paper_signature": "true",
    "smtp_host": '""',
    "smtp_port": "587",
    "smtp_from": '"sed@example.com"',
    "smtp_user": '""',
    "smtp_password": '""',
    "require_comment": "false",
    "enterprises": (
        '[{"code": "ENT_PRIMER_1", "name": "Предприятие Пример-1"},'
        ' {"code": "ENT_PRIMER_2", "name": "Предприятие Пример-2"}]'
    ),
    "allowed_ad_groups": '["SED_HR", "SED_ADMINS", "SED_STEP_EXEC"]',
    "position_to_category": '{"Старший вымышленный кассир": "линейный"}',
    "position_escalation": "{}",
    "templates": (
        '[{"service": "Служба вымышленного учета", "category": "линейный",'
        ' "steps": [{"owner_group": "SED_STEP_BUH"},'
        ' {"owner_group": "SED_STEP_HR", "require_comment": true}]}]'
    ),
    "doc_templates": (
        '[{"service": "Служба вымышленного учета", "category": "линейный",'
        ' "body": "Бегунок увольнения: {{ fio }}, {{ department }}"}]'
    ),
    "mail_templates": (
        '[{"code": "assigned", "subject": "Заявка {{ request_id }}",'
        ' "body_html": "<html>Заявка {{ request_id }} назначена {{ fio }}</html>"}]'
    ),
    "onec_bases": "[]",
}

# Контрактный ответ GET /settings (все ключи на месте, типы по B2).
# Группы ролей (admin_groups/hr_groups/hr_admin_groups) в сиде НЕ сеются — в
# ответе они приходят фолбэком на env (bootstrap); access_groups — None.
ENV_ROLE_VALUES = {
    "admin_groups": ["SED_ADMINS"],
    "hr_groups": ["SED_HR"],
    "hr_admin_groups": ["SED_HR_ADMIN"],
}

CONTRACT_VALUES = {
    "access_groups": None,
    **ENV_ROLE_VALUES,
    "session_ttl_minutes": 600,
    "approval_ttl_days": 3,
    "scan_retention_days": 365,
    "scan_max_mb": 10,
    "scan_allowed_types": ["application/pdf", "image/jpeg", "image/png"],
    "require_paper_signature": True,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_from": "sed@example.com",
    "smtp_user": "",
    "smtp_password": None,
    "require_comment": False,
    "enterprises": [
        {"code": "ENT_PRIMER_1", "name": "Предприятие Пример-1"},
        {"code": "ENT_PRIMER_2", "name": "Предприятие Пример-2"},
    ],
    "allowed_ad_groups": ["SED_HR", "SED_ADMINS", "SED_STEP_EXEC"],
    "position_to_category": {"Старший вымышленный кассир": "линейный"},
    "position_escalation": {},
    "templates": [
        {
            "service": "Служба вымышленного учета",
            "category": "линейный",
            "steps": [
                {"owner_group": "SED_STEP_BUH"},
                {"owner_group": "SED_STEP_HR", "require_comment": True},
            ],
        }
    ],
    "doc_templates": [
        {
            "service": "Служба вымышленного учета",
            "category": "линейный",
            "body": "Бегунок увольнения: {{ fio }}, {{ department }}",
        }
    ],
    "mail_templates": [
        {
            "code": "assigned",
            "subject": "Заявка {{ request_id }}",
            "body_html": "<html>Заявка {{ request_id }} назначена {{ fio }}</html>",
        }
    ],
    "onec_bases": [],
    "onec_enterprises_synced_at": None,
    "ad_links_synced_at": None,
    "schedule_enterprises_sync": None,
    "schedule_ad_links_sync": None,
}

# Контент-часть контракта: только ключи CONTENT_KEYS (для GET/PUT /settings/content).
CONTENT_CONTRACT = {key: CONTRACT_VALUES[key] for key in CONTENT_KEYS}

# Полный обновленный набор для PUT (все ключи переданы явно).
UPDATED_VALUES = {
    "access_groups": ["SED_DB_ENTRY"],
    # Группы ролей в обновлённом наборе содержат и env-группы: админ, который
    # сохраняет настройки, не должен терять роль после собственной записи.
    "admin_groups": ["SED_ADMINS", "SED_ADMINS_2"],
    "hr_groups": ["SED_HR", "SED_HR_2"],
    "hr_admin_groups": ["SED_HR_ADMIN", "SED_HR_ADMIN_2"],
    "session_ttl_minutes": 480,
    "approval_ttl_days": 7,
    "scan_retention_days": 730,
    "scan_max_mb": 25,
    "scan_allowed_types": ["application/pdf", "image/png"],
    "require_paper_signature": False,
    "smtp_host": "mail-relay.example.com",
    "smtp_port": 465,
    "smtp_from": "noreply@example.com",
    "smtp_user": "relay-user",
    "smtp_password": "relay-pass",
    "require_comment": True,
    "enterprises": [
        {"code": "ENT_PRIMER_1", "name": "Предприятие Пример-1"},
        {"code": "ENT_PRIMER_9", "name": "Предприятие Пример-9"},
    ],
    "allowed_ad_groups": ["SED_HR", "SED_ADMINS"],
    "position_to_category": {"Должность вымышленная": "руководитель"},
    "position_escalation": {"Должность вымышленная": 24},
    "templates": [
        {
            "service": "Служба вымышленного учета",
            "category": "руководитель",
            "steps": [{"owner_group": "SED_STEP_HR"}],
        }
    ],
    "doc_templates": [
        {
            "service": "Служба вымышленного учета",
            "category": "руководитель",
            "body": "Бегунок руководителя: {{ fio }}, {{ position }}",
        }
    ],
    "mail_templates": [
        {
            "code": "reminder",
            "subject": "Напоминание {{ request_id }}",
            "body_html": "<html>Напомним про {{ request_id }}</html>",
        }
    ],
    "onec_bases": [
        {
            "code": "zup_t1",
            "name": "База ЗУП тестовая",
            "url": "https://1c-mock.local/t1",
            "user": "reader",
            "password": None,
            "employee_entity": "Catalog_СотрудникиОрганизаций",
            "organization_entity": "Catalog_Организации",
        }
    ],
    "onec_enterprises_synced_at": None,
    "ad_links_synced_at": None,
    "schedule_enterprises_sync": None,
    "schedule_ad_links_sync": None,
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
        HR_ADMIN_GROUPS=TEST_HR_ADMIN,
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
    """Ключей нет в БД — в ответе None (дефолтов в коде нет, значения только из БД).

    Исключение — ключи групп ролей: они возвращают фолбэк на env (bootstrap)."""
    store = InMemorySettingsStore(initial={})
    app.dependency_overrides[get_settings_store] = lambda: store
    try:
        response = client.get("/settings", headers=admin_headers)
    finally:
        app.dependency_overrides.pop(get_settings_store, None)
    assert response.status_code == 200
    body = response.json()
    expected = {key: None for key in SETTINGS_KEYS}
    expected.update(ENV_ROLE_VALUES)
    assert body == expected


def test_settings_get_no_auth_401(client, noauth_headers, settings_override):
    """Без логина — 401 (проверка deps, до проверки роли)."""
    response = client.get("/settings", headers=noauth_headers)
    assert response.status_code == 401


def test_settings_get_hr_403(client, hr_headers, mock_store):
    """ОК (hr) не читает настройки — 403 (admin-only, is_privileged не подходит)."""
    response = client.get("/settings", headers=hr_headers)
    assert response.status_code == 403


def test_settings_get_hr_admin_403(client, hr_admin_headers, mock_store):
    """Руководитель ОК тоже не читает настройки — 403 (до Фазы 2)."""
    response = client.get("/settings", headers=hr_admin_headers)
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


# --- Группы доступа и групп ролей (инфра-ключи, правит только admin) ---

def test_settings_get_role_keys_from_db_over_env(client, admin_headers, mock_store):
    """Группы ролей из БД — единственный источник: роль по ним, env не дополняет.

    Пользователь входит по access_groups (не env) и получает роль admin по
    admin_groups из БД; env-группа админа роль больше не дает."""
    mock_store._data["access_groups"] = json.dumps(["SED_ADMINS_DB"])
    mock_store._data["admin_groups"] = json.dumps(["SED_ADMINS_DB"])
    headers = {"X-Mock-Sam": "adm.petrov", "X-Mock-Groups": "SED_ADMINS_DB"}
    body = client.get("/settings", headers=headers).json()
    assert body["admin_groups"] == ["SED_ADMINS_DB"]
    # Ключей hr нет в БД — фолбэк на env (bootstrap).
    assert body["hr_groups"] == ["SED_HR"]
    assert body["hr_admin_groups"] == ["SED_HR_ADMIN"]
    # env-группа админа при заданном ключе в БД роль admin больше не дает.
    assert client.get("/settings", headers=admin_headers).status_code == 403


def test_settings_get_role_keys_empty_list_revokes_env_role(client, admin_headers, mock_store):
    """Пустой список admin_groups в БД — env-группа админа роль больше не дает (403)."""
    mock_store._data["admin_groups"] = "[]"
    assert client.get("/settings", headers=admin_headers).status_code == 403


def test_settings_get_access_groups_none_without_key(client, admin_headers, mock_store):
    """access_groups не сеется: без ключа в БД в ответе None (env — bootstrap)."""
    assert client.get("/settings", headers=admin_headers).json()["access_groups"] is None


# --- PUT /settings ---

def test_settings_put_admin_200_persists(client, admin_headers, mock_store):
    """Админ сохраняет настройки: 200, полное состояние, в хранилище — сид-формат."""
    response = client.put("/settings", json=UPDATED_VALUES, headers=admin_headers)
    assert response.status_code == 200
    expected = dict(UPDATED_VALUES)
    expected["smtp_password"] = SMTP_PASSWORD_MASK  # в ответе пароль маскируется
    assert response.json() == expected
    # Формат записи в БД — как в сидах: int строкой, bool 'true'/'false',
    # строка JSON-строкой, массив/объект — JSON-строкой (json.dumps).
    assert mock_store._data["approval_ttl_days"] == "7"
    assert mock_store._data["scan_retention_days"] == "730"
    assert mock_store._data["scan_max_mb"] == "25"
    assert mock_store._data["require_paper_signature"] == "false"
    assert mock_store._data["require_comment"] == "true"
    assert mock_store._data["smtp_from"] == '"noreply@example.com"'
    assert mock_store._data["smtp_user"] == '"relay-user"'
    assert mock_store._data["smtp_password"] == '"relay-pass"'  # в БД — настоящее значение
    assert json.loads(mock_store._data["enterprises"]) == UPDATED_VALUES["enterprises"]
    assert json.loads(mock_store._data["allowed_ad_groups"]) == ["SED_HR", "SED_ADMINS"]
    assert json.loads(mock_store._data["position_to_category"]) == {
        "Должность вымышленная": "руководитель"
    }
    assert json.loads(mock_store._data["position_escalation"]) == {
        "Должность вымышленная": 24
    }
    assert json.loads(mock_store._data["templates"]) == UPDATED_VALUES["templates"]
    # Последующее чтение возвращает сохраненное (пароль — маской).
    got = client.get("/settings", headers=admin_headers)
    assert got.status_code == 200
    assert got.json() == expected


def test_settings_put_smtp_password_empty_keeps_existing(client, admin_headers, mock_store):
    """Пустой/маска пароль в PUT не перезаписывает заданный (сохранить текущий)."""
    client.put("/settings", json=UPDATED_VALUES, headers=admin_headers)
    assert mock_store._data["smtp_password"] == '"relay-pass"'
    # Пустое значение — не меняем.
    response = client.put("/settings", json={"smtp_password": ""}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["smtp_password"] == SMTP_PASSWORD_MASK
    assert mock_store._data["smtp_password"] == '"relay-pass"'
    # Маска — тоже не меняем.
    client.put("/settings", json={"smtp_password": SMTP_PASSWORD_MASK}, headers=admin_headers)
    assert mock_store._data["smtp_password"] == '"relay-pass"'
    # Новое значение — перезаписывает.
    response = client.put("/settings", json={"smtp_password": "new-pass"}, headers=admin_headers)
    assert response.status_code == 200
    assert mock_store._data["smtp_password"] == '"new-pass"'
    assert response.json()["smtp_password"] == SMTP_PASSWORD_MASK


def test_settings_put_smtp_user_cleared(client, admin_headers, mock_store):
    """Логин релея можно очистить (без авторизации); пароль при этом сохраняется."""
    client.put("/settings", json={"smtp_user": "relay-user", "smtp_password": "relay-pass"}, headers=admin_headers)
    response = client.put("/settings", json={"smtp_user": ""}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["smtp_user"] == ""
    assert mock_store._data["smtp_user"] == '""'
    assert mock_store._data["smtp_password"] == '"relay-pass"'


def test_settings_put_partial_200(client, admin_headers, mock_store):
    """Частичное обновление: пишутся только присутствующие ключи, остальное — из БД."""
    payload = {"approval_ttl_days": 7, "smtp_from": "noreply@example.com"}
    response = client.put("/settings", json=payload, headers=admin_headers)
    assert response.status_code == 200
    # Ответ — полное текущее состояние: измененные + прежние значения сида.
    expected = dict(CONTRACT_VALUES)
    expected.update(payload)
    assert response.json() == expected
    # В хранилище тронуты только переданные ключи, прочие в сид-формате без изменений.
    assert mock_store._data["approval_ttl_days"] == "7"
    assert mock_store._data["smtp_from"] == '"noreply@example.com"'
    assert mock_store._data["scan_retention_days"] == "365"
    assert mock_store._data["require_comment"] == "false"
    assert json.loads(mock_store._data["templates"]) == CONTRACT_VALUES["templates"]


def test_settings_put_empty_200_no_changes(client, admin_headers, mock_store):
    """Пустое тело — ничего не меняется, ответ — текущее состояние."""
    response = client.put("/settings", json={}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == CONTRACT_VALUES
    assert mock_store._data == dict(SEED_VALUES)


def test_settings_put_explicit_null_stored(client, admin_headers, mock_store):
    """Явный null в теле — ключ присутствует и пишется 'null' (дефолтов нет)."""
    response = client.put("/settings", json={"smtp_from": None}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["smtp_from"] is None
    assert mock_store._data["smtp_from"] == "null"


def test_settings_put_hr_403(client, hr_headers, mock_store):
    """ОК не редактирует настройки — 403."""
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=hr_headers)
    assert response.status_code == 403


def test_settings_put_hr_admin_403(client, hr_admin_headers, mock_store):
    """Руководитель ОК не редактирует настройки — 403 (до Фазы 2)."""
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=hr_admin_headers)
    assert response.status_code == 403


def test_settings_put_owner_403(client, owner_headers, mock_store):
    """Владелец шага не редактирует настройки — 403."""
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=owner_headers)
    assert response.status_code == 403


def test_settings_put_wrong_types_422(client, admin_headers, mock_store):
    """Неверные типы (в т.ч. структура справочников/шаблонов) — 422."""
    bad_cases = [
        dict(CONTRACT_VALUES, approval_ttl_days="abc"),
        dict(CONTRACT_VALUES, require_paper_signature=123),
        dict(CONTRACT_VALUES, require_comment="да"),
        dict(CONTRACT_VALUES, smtp_from=42),
        dict(CONTRACT_VALUES, enterprises="ENT_PRIMER_1"),
        dict(CONTRACT_VALUES, enterprises=[{"code": "X"}]),
        dict(CONTRACT_VALUES, allowed_ad_groups=["SED_HR", 123]),
        dict(CONTRACT_VALUES, position_to_category={"Должность": 123}),
        dict(CONTRACT_VALUES, position_escalation={"Должность": "много"}),
        dict(CONTRACT_VALUES, templates="not-a-list"),
        dict(CONTRACT_VALUES, templates=[{"service": "S", "category": "C"}]),
        dict(CONTRACT_VALUES, templates=[{"service": "S", "category": "C", "steps": [{"resolver": "by_group"}]}]),
        dict(CONTRACT_VALUES, doc_templates="not-a-list"),
        dict(CONTRACT_VALUES, doc_templates=[{"service": "S", "category": "C"}]),
        dict(CONTRACT_VALUES, mail_templates="not-a-list"),
        dict(CONTRACT_VALUES, mail_templates=[{"code": "assigned"}]),
    ]
    for bad in bad_cases:
        assert client.put("/settings", json=bad, headers=admin_headers).status_code == 422


def test_settings_put_store_down_503(client, admin_headers, mock_store):
    """Хранилище недоступно при записи — 503."""
    mock_store.broken = True
    response = client.put("/settings", json=dict(CONTRACT_VALUES), headers=admin_headers)
    assert response.status_code == 503


# --- PUT: access_groups и ключи ролей (инфра, только admin) ---

def test_settings_put_access_groups_and_roles_persists(client, admin_headers, mock_store):
    """Админ правит access_groups и группы ролей: в БД — сид-формат (JSON-массив)."""
    response = client.put(
        "/settings",
        json={"access_groups": ["SED_DB_ENTRY", "SED_DB_ENTRY_2"], "hr_groups": ["SED_HR_DB"]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert json.loads(mock_store._data["access_groups"]) == ["SED_DB_ENTRY", "SED_DB_ENTRY_2"]
    assert json.loads(mock_store._data["hr_groups"]) == ["SED_HR_DB"]
    assert response.json()["access_groups"] == ["SED_DB_ENTRY", "SED_DB_ENTRY_2"]
    assert response.json()["hr_groups"] == ["SED_HR_DB"]
    # Незаписанный ключ роли — фолбэк на env.
    assert response.json()["admin_groups"] == ["SED_ADMINS"]


def test_settings_put_group_keys_wrong_types_422(client, admin_headers, mock_store):
    """Неверные типы групповых ключей входа и ролей — 422."""
    bad_cases = [
        {"access_groups": ["SED_X", 1]},
        {"access_groups": "SED_HR"},
        {"admin_groups": "SED_ADMINS"},
        {"hr_groups": [None]},
        {"hr_admin_groups": {"SED_HR_ADMIN": True}},
    ]
    for bad in bad_cases:
        assert client.put("/settings", json=bad, headers=admin_headers).status_code == 422


def test_settings_put_access_groups_hr_admin_403(client, hr_admin_headers, mock_store):
    """access_groups правит ТОЛЬКО админ: руководитель ОК — 403, в БД пусто."""
    response = client.put(
        "/settings", json={"access_groups": ["SED_HACK"]}, headers=hr_admin_headers
    )
    assert response.status_code == 403
    assert "access_groups" not in mock_store._data


def test_settings_put_access_groups_hr_403(client, hr_headers, mock_store):
    """ОК не правит access_groups — 403."""
    response = client.put(
        "/settings", json={"access_groups": ["SED_HACK"]}, headers=hr_headers
    )
    assert response.status_code == 403
    assert "access_groups" not in mock_store._data


# --- onec_bases (базы 1С: пароль маскируется в GET, сливается при PUT) ---

def test_settings_put_onec_bases_passwords_stored_and_masked(client, admin_headers, mock_store):
    """PUT баз с паролями: в БД пароли сохранены, в ответе замаскированы."""
    bases = [
        {
            "code": "zup_t1",
            "name": "База ЗУП",
            "url": "https://1c-mock.local/t1",
            "user": "reader",
            "password": "secret-1",
        }
    ]
    response = client.put("/settings", json={"onec_bases": bases}, headers=admin_headers)
    assert response.status_code == 200
    # В ответе пароль маскируется (как smtp_password).
    assert response.json()["onec_bases"][0]["password"] == SMTP_PASSWORD_MASK
    # В БД — настоящее значение.
    stored = json.loads(mock_store._data["onec_bases"])
    assert stored[0]["password"] == "secret-1"
    # Повторный GET тоже отдаёт маску.
    got = client.get("/settings", headers=admin_headers)
    assert got.json()["onec_bases"][0]["password"] == SMTP_PASSWORD_MASK


def test_settings_put_onec_bases_empty_password_keeps_existing(client, admin_headers, mock_store):
    """Пустое значение/маска пароля существующей базы — пароль из БД сохраняется."""
    bases = [
        {
            "code": "zup_t1",
            "name": "База ЗУП",
            "url": "https://1c-mock.local/t1",
            "user": "reader",
            "password": "secret-1",
        }
    ]
    assert client.put("/settings", json={"onec_bases": bases}, headers=admin_headers).status_code == 200
    # Тот же список, но пароль пустой — текущий должен сохраниться.
    kept = [
        {
            "code": "zup_t1",
            "name": "База ЗУП",
            "url": "https://1c-mock.local/t1",
            "user": "reader",
            "password": "",
        }
    ]
    response = client.put("/settings", json={"onec_bases": kept}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["onec_bases"][0]["password"] == SMTP_PASSWORD_MASK
    assert json.loads(mock_store._data["onec_bases"])[0]["password"] == "secret-1"
    # Маска в PUT тоже не затирает текущий пароль.
    masked = [
        {
            "code": "zup_t1",
            "name": "База ЗУП",
            "url": "https://1c-mock.local/t1",
            "user": "reader",
            "password": SMTP_PASSWORD_MASK,
        }
    ]
    assert client.put("/settings", json={"onec_bases": masked}, headers=admin_headers).status_code == 200
    assert json.loads(mock_store._data["onec_bases"])[0]["password"] == "secret-1"


def test_settings_put_onec_bases_new_base_no_password(client, admin_headers, mock_store):
    """Новая база без пароля — пароль пустой (None в ответе, '' в БД-JSON)."""
    bases = [
        {
            "code": "zup_t9",
            "name": "Новая база",
            "url": "https://1c-mock.local/t9",
            "user": "reader",
            "password": None,
        }
    ]
    response = client.put("/settings", json={"onec_bases": bases}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["onec_bases"][0]["password"] is None
    assert json.loads(mock_store._data["onec_bases"])[0]["password"] is None


def test_settings_content_ignores_onec_bases(client, hr_admin_headers, mock_store):
    """Контент-эндпоинт не отдаёт и не пишет onec_bases (инфра-ключ)."""
    response = client.get("/settings/content", headers=hr_admin_headers)
    assert response.status_code == 200
    assert "onec_bases" not in response.json()


def test_settings_content_ignores_access_groups_and_roles(client, hr_admin_headers, mock_store):
    """access_groups и ключи ролей — инфра: контент-эндпоинт их не отдаёт и не пишет."""
    body = client.get("/settings/content", headers=hr_admin_headers).json()
    for key in ("access_groups", "admin_groups", "hr_groups", "hr_admin_groups"):
        assert key not in body
    response = client.put(
        "/settings/content",
        json={"access_groups": ["SED_HACK"], "admin_groups": ["SED_HACK"]},
        headers=hr_admin_headers,
    )
    assert response.status_code == 200
    assert "access_groups" not in mock_store._data
    assert "admin_groups" not in mock_store._data


# --- GET/PUT /settings/content (контент: руководитель ОК + админ) ---

def test_settings_content_get_admin_200(client, admin_headers, mock_store):
    """Админ читает контент-настройки: 200, только CONTENT_KEYS без инфра-ключей."""
    response = client.get("/settings/content", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == CONTENT_CONTRACT
    assert set(response.json()) == set(CONTENT_KEYS)


def test_settings_content_get_hr_admin_200(client, hr_admin_headers, mock_store):
    """Руководитель ОК читает контент-настройки: 200 (те же контент-ключи)."""
    response = client.get("/settings/content", headers=hr_admin_headers)
    assert response.status_code == 200
    assert response.json() == CONTENT_CONTRACT


def test_settings_content_get_hr_403(client, hr_headers, mock_store):
    """ОК не читает контент-настройки — 403 (контент — руководителю ОК и админу)."""
    response = client.get("/settings/content", headers=hr_headers)
    assert response.status_code == 403


def test_settings_content_get_owner_403(client, owner_headers, mock_store):
    """Владелец шага не читает контент-настройки — 403."""
    response = client.get("/settings/content", headers=owner_headers)
    assert response.status_code == 403


def test_settings_content_put_hr_admin_200_keeps_infra(client, hr_admin_headers, mock_store):
    """Руководитель ОК правит контент: 200, инфра-ключи в БД не тронуты."""
    response = client.put(
        "/settings/content",
        json={"approval_ttl_days": 7, "require_comment": True},
        headers=hr_admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["approval_ttl_days"] == 7
    assert response.json()["require_comment"] is True
    # Контент-ключ записан в сид-формате.
    assert mock_store._data["approval_ttl_days"] == "7"
    assert mock_store._data["require_comment"] == "true"
    # Инфра-ключи остались сидовыми (PUT /settings/content их не трогает).
    assert mock_store._data["session_ttl_minutes"] == "600"
    assert mock_store._data["smtp_from"] == '"sed@example.com"'
    assert mock_store._data["scan_max_mb"] == "10"


def test_settings_content_put_admin_200(client, admin_headers, mock_store):
    """Админ правит контент: 200, инфра-ключи не затронуты."""
    response = client.put(
        "/settings/content",
        json={"enterprises": [{"code": "ENT_PRIMER_9", "name": "Предприятие Пример-9"}]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["enterprises"] == [
        {"code": "ENT_PRIMER_9", "name": "Предприятие Пример-9"}
    ]
    assert mock_store._data["session_ttl_minutes"] == "600"


def test_settings_content_put_ignores_infra_keys(client, hr_admin_headers, mock_store):
    """Чужие (инфра) ключи в теле контента игнорируются: не 422, в БД не пишутся."""
    response = client.put(
        "/settings/content",
        json={"approval_ttl_days": 9, "session_ttl_minutes": 123, "smtp_host": "x"},
        headers=hr_admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["approval_ttl_days"] == 9
    assert mock_store._data["approval_ttl_days"] == "9"
    assert mock_store._data["session_ttl_minutes"] == "600"


def test_settings_content_put_hr_403(client, hr_headers, mock_store):
    """ОК не правит контент-настройки — 403."""
    response = client.put(
        "/settings/content",
        json={"approval_ttl_days": 7},
        headers=hr_headers,
    )
    assert response.status_code == 403


def test_settings_content_get_store_down_503(client, admin_headers, mock_store):
    """Хранилище недоступно при чтении контента — 503."""
    mock_store.broken = True
    response = client.get("/settings/content", headers=admin_headers)
    assert response.status_code == 503


def test_settings_content_audit_read_and_update(client, hr_admin_headers, mock_store):
    """Чтение/правка контента пишут settings.read/settings.update по CONTENT_KEYS."""
    assert audit_log.all() == []
    client.get("/settings/content", headers=hr_admin_headers)
    read_events = [e for e in audit_log.all() if e.action == "settings.read"]
    assert len(read_events) == 1
    assert read_events[0].actor == hr_admin_headers["X-Mock-Sam"]
    assert read_events[0].entity_id == ",".join(CONTENT_KEYS)
    client.put("/settings/content", json={"approval_ttl_days": 7}, headers=hr_admin_headers)
    update_events = [e for e in audit_log.all() if e.action == "settings.update"]
    assert len(update_events) == 1
    assert update_events[0].entity_id == ",".join(CONTENT_KEYS)
    assert update_events[0].detail == "approval_ttl_days"


# --- Аудит ---

def test_settings_audit_read_and_update(client, admin_headers, mock_store):
    """Чтение пишет settings.read, обновление — settings.update (actor — sam,
    detail — измененные ключи)."""
    assert audit_log.all() == []
    client.get("/settings", headers=admin_headers)
    read_events = [e for e in audit_log.all() if e.action == "settings.read"]
    assert len(read_events) == 1
    assert read_events[0].actor == admin_headers["X-Mock-Sam"]
    assert read_events[0].entity == "settings"
    client.put("/settings", json={"approval_ttl_days": 7}, headers=admin_headers)
    update_events = [e for e in audit_log.all() if e.action == "settings.update"]
    assert len(update_events) == 1
    assert update_events[0].actor == admin_headers["X-Mock-Sam"]
    assert update_events[0].entity == "settings"
    assert update_events[0].detail == "approval_ttl_days"


def test_settings_audit_not_written_on_403(client, hr_headers, mock_store):
    """Отказ по роли в аудит не пишется (события нет)."""
    client.get("/settings", headers=hr_headers)
    assert audit_log.all() == []

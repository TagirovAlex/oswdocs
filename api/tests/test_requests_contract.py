# Тесты контракта Волны 1: /enterprises, /step-groups, fio в заявке, /folders.
# Все ПДн вымышленные; хранилище настроек — in-memory мок (как в test_settings_api),
# заявки — in-memory хранилище requests.py. Группы — через оверрайд get_settings.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    get_memory_requests_store,
    get_route_settings,
)
from app.requests import RouteSettings, RouteTemplate, RouteStepTemplate  # noqa: E402
from app.requests_store import get_requests_store  # noqa: E402
from app.settings_routes import SettingsUnavailable, get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные служба/должность/ФИО (не продовые значения).
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION_LINE = "Старший вымышленный кассир"
FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_FIO = "Сотрудник Вымышленный Полный"

# Сид-формат (как в db/seeds/settings.sql): JSON-массивы строками.
SEED_ENTERPRISES = (
    '[{"code": "ENT_PRIMER_1", "name": "Предприятие Пример-1"},'
    ' {"code": "ENT_PRIMER_2", "name": "Предприятие Пример-2"}]'
)
SEED_GROUPS = '["SED_HR", "SED_ADMINS", "SED_STEP_EXEC"]'


class InMemorySettingsStore:
    """Мок хранилища настроек: dict вместо Postgres, значения в сид-формате."""

    def __init__(self, initial=None, broken=False):
        self._data = dict(initial or {})
        self.broken = broken

    def _check(self):
        if self.broken:
            raise SettingsUnavailable("Хранилище настроек недоступно (тест)")

    def get(self, key):
        self._check()
        return self._data.get(key)

    def get_many(self, keys):
        self._check()
        return {k: v for k, v in self._data.items() if k in keys}


@pytest.fixture
def settings_override():
    """Группы тестовыми (дефолты кода нейтральные)."""
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
def route_override():
    """Один шаблон (служба+линейный → 2 шага), как в test_requests."""
    route = RouteSettings(
        approval_ttl_days=7,
        position_to_category={FAKE_POSITION_LINE: "линейный"},
        position_escalation={},
        templates=[
            RouteTemplate(
                service=FAKE_SERVICE,
                category="линейный",
                steps=[
                    RouteStepTemplate(owner_group="SED_STEP_BUH"),
                    RouteStepTemplate(owner_group="SED_STEP_HR"),
                ],
            )
        ],
    )
    app.dependency_overrides[get_route_settings] = lambda: route
    yield route
    app.dependency_overrides.pop(get_route_settings, None)


@pytest.fixture
def settings_store(settings_override):
    """In-memory хранилище с сид-значениями enterprises/allowed_ad_groups."""
    store = InMemorySettingsStore(
        initial={"enterprises": SEED_ENTERPRISES, "allowed_ad_groups": SEED_GROUPS}
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture
def requests_store():
    """Хранилище заявок через зависимость (общий InMemory-экземпляр)."""
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_requests_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    """Чистое хранилище заявок и аудит на каждый тест."""
    requests_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    audit_log.clear_for_tests()


def _create(client, headers, **kw) -> object:
    """Создание заявки с обязательным полем fio (Волна 1)."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": FAKE_FIO,
        "tab_num": "В-0001",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION_LINE,
    }
    body.update(kw)
    return client.post("/requests", json=body, headers=headers)


# --- GET /enterprises ---

def test_enterprises_hr_200(client, hr_headers, settings_store):
    """ОК читает предприятия: 200, пары code/name из сида."""
    response = client.get("/enterprises", headers=hr_headers)
    assert response.status_code == 200
    assert response.json() == [
        {"code": "ENT_PRIMER_1", "name": "Предприятие Пример-1"},
        {"code": "ENT_PRIMER_2", "name": "Предприятие Пример-2"},
    ]


def test_enterprises_admin_200(client, admin_headers, settings_store):
    """Админ читает предприятия: 200."""
    response = client.get("/enterprises", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_enterprises_owner_403(client, owner_headers, settings_store):
    """Владелец шага не читает предприятия — 403."""
    assert client.get("/enterprises", headers=owner_headers).status_code == 403


def test_enterprises_no_auth_401(client, noauth_headers, settings_store):
    """Без логина — 401."""
    assert client.get("/enterprises", headers=noauth_headers).status_code == 401


def test_enterprises_missing_key_empty(client, hr_headers, settings_override):
    """Ключа нет в БД — [] (дефолтов в коде нет)."""
    store = InMemorySettingsStore(initial={})
    app.dependency_overrides[get_settings_store] = lambda: store
    try:
        response = client.get("/enterprises", headers=hr_headers)
    finally:
        app.dependency_overrides.pop(get_settings_store, None)
    assert response.status_code == 200
    assert response.json() == []


def test_enterprises_empty_array(client, hr_headers, settings_override):
    """Пустой массив в БД — []."""
    store = InMemorySettingsStore(initial={"enterprises": "[]"})
    app.dependency_overrides[get_settings_store] = lambda: store
    try:
        response = client.get("/enterprises", headers=hr_headers)
    finally:
        app.dependency_overrides.pop(get_settings_store, None)
    assert response.status_code == 200
    assert response.json() == []


def test_enterprises_store_down_503(client, hr_headers, settings_store):
    """Хранилище недоступно — 503, а не 500."""
    settings_store.broken = True
    assert client.get("/enterprises", headers=hr_headers).status_code == 503


def test_enterprises_audit_written(client, hr_headers, settings_store):
    """Успешное чтение пишет enterprises.read (актор, entity=enterprise)."""
    client.get("/enterprises", headers=hr_headers)
    events = [e for e in audit_log.all() if e.action == "enterprises.read"]
    assert len(events) == 1
    assert events[0].actor == hr_headers["X-Mock-Sam"]
    assert events[0].entity == "enterprise"


def test_enterprises_audit_not_on_403(client, owner_headers, settings_store):
    """Отказ по роли в аудит не пишется."""
    client.get("/enterprises", headers=owner_headers)
    assert audit_log.all() == []


# --- GET /step-groups ---

def test_step_groups_hr_200(client, hr_headers, settings_store):
    """ОК читает группы шагов: 200, список из сида."""
    response = client.get("/step-groups", headers=hr_headers)
    assert response.status_code == 200
    assert response.json() == ["SED_HR", "SED_ADMINS", "SED_STEP_EXEC"]


def test_step_groups_admin_200(client, admin_headers, settings_store):
    """Админ читает группы шагов: 200."""
    assert client.get("/step-groups", headers=admin_headers).status_code == 200


def test_step_groups_owner_403(client, owner_headers, settings_store):
    """Владелец шага не читает группы — 403."""
    assert client.get("/step-groups", headers=owner_headers).status_code == 403


def test_step_groups_missing_key_empty(client, hr_headers, settings_override):
    """Ключа нет в БД — []."""
    store = InMemorySettingsStore(initial={})
    app.dependency_overrides[get_settings_store] = lambda: store
    try:
        response = client.get("/step-groups", headers=hr_headers)
    finally:
        app.dependency_overrides.pop(get_settings_store, None)
    assert response.status_code == 200
    assert response.json() == []


def test_step_groups_store_down_503(client, hr_headers, settings_store):
    """Хранилище недоступно — 503."""
    settings_store.broken = True
    assert client.get("/step-groups", headers=hr_headers).status_code == 503


# --- fio в заявке (Волна 1, п.3) ---

def test_create_request_with_fio(client, hr_headers, settings_override, route_override):
    """ОК создает заявку с fio: 201, привилегированному fio отдается."""
    response = _create(client, hr_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["fio"] == FAKE_FIO
    assert body["tab_num"] == "В-0001"


def test_create_request_without_fio_422(client, hr_headers, settings_override, route_override):
    """fio обязателен: без него — 422."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "tab_num": "В-0001",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION_LINE,
    }
    assert client.post("/requests", json=body, headers=hr_headers).status_code == 422


def test_fio_hidden_from_owner(client, hr_headers, owner_headers, settings_override, route_override):
    """Владельцу шага fio не отдается (ПДн), но заявка в его списке есть."""
    rid = _create(client, hr_headers).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    listing = client.get("/requests", headers=owner_headers)
    assert listing.status_code == 200
    mine = [r for r in listing.json() if r["id"] == rid]
    assert len(mine) == 1
    assert mine[0]["fio"] is None
    assert mine[0]["tab_num"] is None


# --- enterprise_name: название предприятия из settings.enterprises (§3 handoff 3.2) ---

def test_enterprise_name_from_settings_privileged(
    client, hr_headers, settings_store, settings_override, route_override
):
    """Привилегированному название предприятия по коду из settings.enterprises."""
    body = _create(client, hr_headers).json()
    assert body["enterprise"] == FAKE_ENTERPRISE
    assert body["enterprise_name"] == "Предприятие Пример-1"


def test_enterprise_name_hidden_from_owner(
    client, hr_headers, owner_headers, settings_store, settings_override, route_override
):
    """Непривилегированному enterprise_name не отдается (ПДн уровня enterprise)."""
    rid = _create(client, hr_headers).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    listing = client.get("/requests", headers=owner_headers)
    mine = [r for r in listing.json() if r["id"] == rid]
    assert len(mine) == 1
    assert mine[0]["enterprise"] is None
    assert mine[0]["enterprise_name"] is None


def test_enterprise_name_none_when_key_missing(
    client, hr_headers, settings_override, route_override
):
    """Ключа enterprises нет в настройках — enterprise_name=None, без 500."""
    app.dependency_overrides[get_settings_store] = lambda: InMemorySettingsStore(initial={})
    try:
        response = _create(client, hr_headers)
    finally:
        app.dependency_overrides.pop(get_settings_store, None)
    assert response.status_code == 201
    assert response.json()["enterprise_name"] is None


def test_enterprise_name_none_when_store_down(
    client, hr_headers, settings_store, settings_override, route_override
):
    """Хранилище настроек недоступно — enterprise_name=None и 201, не 500."""
    settings_store.broken = True
    response = _create(client, hr_headers)
    assert response.status_code == 201
    assert response.json()["enterprise_name"] is None


# --- GET /folders (Волна 1, п.4) ---

def test_folders_empty_counts(client, hr_headers, settings_override):
    """Пустое хранилище: счетчики нулевые, id/заголовки по контракту."""
    folders = client.get("/folders", headers=hr_headers).json()
    by_id = {f["id"]: f for f in folders}
    assert set(by_id) == {"agreement", "revision", "done", "mine"}
    assert by_id["agreement"] == {"id": "agreement", "title": "На согласовании", "count": 0}
    assert by_id["revision"] == {"id": "revision", "title": "На доработке", "count": 0}
    assert by_id["done"] == {"id": "done", "title": "Завершённые", "count": 0}
    assert by_id["mine"] == {"id": "mine", "title": "Мои задачи", "count": 0}


def test_folders_counts_by_status(
    client, hr_headers, owner_headers, settings_override, route_override
):
    """Счетчики по статусам: согласование/доработка/завершенные (отказ+отзыв)."""
    # A — На согласовании.
    a = _create(client, hr_headers).json()
    assert client.post(f"/requests/{a['id']}/submit", headers=hr_headers).status_code == 200
    # B — На доработке (возврат владельцем первого шага).
    b = _create(client, hr_headers).json()
    assert client.post(f"/requests/{b['id']}/submit", headers=hr_headers).status_code == 200
    assert client.post(
        f"/requests/{b['id']}/steps/1/decision",
        json={"decision": "return", "comment": "На доработку"},
        headers=owner_headers,
    ).status_code == 200
    # C — Отклонено (завершенные).
    c = _create(client, hr_headers).json()
    assert client.post(f"/requests/{c['id']}/submit", headers=hr_headers).status_code == 200
    assert client.post(
        f"/requests/{c['id']}/steps/1/decision",
        json={"decision": "reject", "comment": "Отказ"},
        headers=owner_headers,
    ).status_code == 200
    # D — Отозвано (завершенные).
    d = _create(client, hr_headers).json()
    assert client.post(f"/requests/{d['id']}/submit", headers=hr_headers).status_code == 200
    assert client.post(f"/requests/{d['id']}/withdraw", headers=hr_headers).status_code == 200

    folders = client.get("/folders", headers=hr_headers).json()
    by_id = {f["id"]: f for f in folders}
    assert by_id["agreement"]["count"] == 1
    assert by_id["revision"]["count"] == 1
    assert by_id["done"]["count"] == 2
    # У ОК нет шагов (группы SED_HR нет среди SED_STEP_*), поэтому mine=0.
    assert by_id["mine"]["count"] == 0


def test_folders_owner_only_mine(
    client, hr_headers, owner_headers, settings_override, route_override
):
    """Владелец шага видит только папку mine (его заявки)."""
    first = _create(client, hr_headers).json()
    assert client.post(f"/requests/{first['id']}/submit", headers=hr_headers).status_code == 200
    _create(client, hr_headers)
    assert client.get("/folders", headers=owner_headers).json() == [
        {"id": "mine", "title": "Мои задачи", "count": 2}
    ]


def test_folders_no_auth_401(client, noauth_headers, settings_override):
    """Без логина — 401."""
    assert client.get("/folders", headers=noauth_headers).status_code == 401
# Тесты хранилища заявок (волна B1): unit InMemoryRequestsStore (create/get/
# list_all/update/next_id/reset) и проверка, что эндпоинты ходят через
# зависимость get_requests_store (override со счетчиком вызовов).
# Все ПДн вымышленные; группы — через оверрайд get_settings.

from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    AGREED,
    DONE,
    DRAFT,
    IN_APPROVAL,
    REJECTED,
    REVOKED,
    REWORK,
    STEP_APPROVED,
    STEP_EXPIRED,
    STEP_PENDING,
    STEP_REJECTED,
    STEP_RETURNED,
    TO_EXECUTION,
    _Request,
    _Step,
    _utcnow,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests import RouteSettings  # noqa: E402
from app.requests_store import (  # noqa: E402
    DbRequestsStore,
    InMemoryRequestsStore,
    REQUEST_STATUS_TO_CODE,
    ROUTE_ORIGIN_TO_CODE,
    STEP_STATUS_TO_CODE,
    get_requests_store,
    req_id_to_number,
    req_number_to_id,
    request_status_from_db,
    request_status_to_db,
    route_origin_from_db,
    route_origin_to_db,
    step_status_from_db,
    step_status_to_db,
)

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные служба/должность/предприятие (не продовые значения).
FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"
BUH_GROUP = "SED_STEP_BUH"


def _b64(value: str) -> str:
    """Кодирование кириллицы для мок-заголовков (как в conftest)."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _headers_for(sam: str, groups: list[str]) -> dict:
    """Заголовки мок-пользователя с вымышленными ПДн."""
    return {
        "X-Mock-Sam": sam,
        "X-Mock-Fio": _b64("Вымышленный Пользователь Тестовый"),
        "X-Mock-Mail": _b64(f"{sam}@example.com"),
        "X-Mock-Department": _b64("Вымышленный отдел"),
        "X-Mock-Title": _b64("Вымышленная должность"),
        "X-Mock-Groups": ",".join(groups),
    }


def _hr_headers() -> dict:
    """Заголовки ОК (разрешенная группа для конструктора)."""
    return _headers_for("ok.vymyshlennaya", ["SED_HR"])


def _make_request(request_id: str = "REQ-0001") -> _Request:
    """Вымышленная заявка для unit-тестов хранилища."""
    return _Request(
        id=request_id,
        status="Черновик",
        route_origin="custom",
        enterprise=FAKE_ENTERPRISE,
        fio="Вымышленный Сотрудник Полный",
        tab_num="В-0001",
        department=FAKE_SERVICE,
        position=FAKE_POSITION,
        created_by="ok.vymyshlennaya",
        steps=[_Step(order=1, owner_group=BUH_GROUP, expires_at=_utcnow())],
    )


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
    """Маршрут без шаблонов: создание только ручным конструктором."""
    route = RouteSettings(approval_ttl_days=7)
    app.dependency_overrides[get_route_settings] = lambda: route
    yield route
    app.dependency_overrides.pop(get_route_settings, None)


@pytest.fixture(autouse=True)
def clean_state():
    """Чистое хранилище и аудит на каждый тест."""
    get_memory_requests_store().reset()
    audit_log.clear_for_tests()
    yield
    get_memory_requests_store().reset()
    audit_log.clear_for_tests()


# --- InMemoryRequestsStore: unit ---

def test_inmemory_next_id_sequence():
    """next_id наращивает номер по порядку: REQ-0001, REQ-0002, ..."""
    store = InMemoryRequestsStore()
    assert store.next_id() == "REQ-0001"
    assert store.next_id() == "REQ-0002"
    assert store.next_id() == "REQ-0003"


def test_inmemory_create_get_list():
    """create/get/list_all: заявки сохраняются и читаются по id."""
    store = InMemoryRequestsStore()
    first = _make_request("REQ-0001")
    second = _make_request("REQ-0002")
    store.create(first)
    store.create(second)
    assert store.get("REQ-0001") is first
    assert store.get("REQ-9999") is None
    assert [r.id for r in store.list_all()] == ["REQ-0001", "REQ-0002"]


def test_inmemory_update():
    """update перезаписывает заявку (в т.ч. шаги) по id."""
    store = InMemoryRequestsStore()
    request = _make_request()
    store.create(request)
    request.status = "На согласовании"
    request.steps[0].status = "согласован"
    store.update(request)
    stored = store.get("REQ-0001")
    assert stored.status == "На согласовании"
    assert stored.steps[0].status == "согласован"


def test_inmemory_reset():
    """reset очищает заявки и обнуляет счетчик номеров."""
    store = InMemoryRequestsStore()
    store.create(_make_request())
    store.next_id()
    assert store.list_all()
    store.reset()
    assert store.list_all() == []
    assert store.next_id() == "REQ-0001"


# --- Эндпоинты ходят через get_requests_store ---

class CountingRequestsStore:
    """Обертка InMemory-хранилища со счетчиком вызовов протокола."""

    def __init__(self) -> None:
        self.inner = InMemoryRequestsStore()
        self.calls = {"create": 0, "get": 0, "list_all": 0, "update": 0, "next_id": 0}

    def create(self, request):
        self.calls["create"] += 1
        self.inner.create(request)

    def get(self, request_id):
        self.calls["get"] += 1
        return self.inner.get(request_id)

    def list_all(self):
        self.calls["list_all"] += 1
        return self.inner.list_all()

    def update(self, request):
        self.calls["update"] += 1
        self.inner.update(request)

    def next_id(self):
        self.calls["next_id"] += 1
        return self.inner.next_id()


def test_endpoints_go_through_store(client, settings_override, route_override):
    """Все обращения к заявкам идут через зависимость get_requests_store."""
    store = CountingRequestsStore()
    app.dependency_overrides[get_requests_store] = lambda: store
    try:
        headers = _hr_headers()
        created = client.post(
            "/requests",
            json={
                "enterprise": FAKE_ENTERPRISE,
                "fio": "Вымышленный Сотрудник Полный",
                "tab_num": "В-0001",
                "department": FAKE_SERVICE,
                "position": FAKE_POSITION,
                "steps": [{"owner_group": BUH_GROUP}],
            },
            headers=headers,
        )
        assert created.status_code == 201
        rid = created.json()["id"]
        assert store.calls["next_id"] == 1
        assert store.calls["create"] == 1

        listing = client.get("/requests", headers=headers)
        assert listing.status_code == 200
        assert store.calls["list_all"] == 1

        card = client.get(f"/requests/{rid}", headers=headers)
        assert card.status_code == 200
        assert store.calls["get"] == 1

        submitted = client.post(f"/requests/{rid}/submit", headers=headers)
        assert submitted.status_code == 200
        assert store.calls["update"] >= 1

        folders = client.get("/folders", headers=headers)
        assert folders.status_code == 200
        assert store.calls["list_all"] == 2
    finally:
        app.dependency_overrides.pop(get_requests_store, None)


# --- Маппинги модель <-> код БД (чистые функции, без обращения к БД) ---

def test_request_status_mapping_roundtrip():
    """Каждый русский статус контракта <-> код БД туда и обратно."""
    for russian, code in REQUEST_STATUS_TO_CODE.items():
        assert request_status_to_db(russian) == code
        assert request_status_from_db(code) == russian


def test_request_status_mapping_covers_contract():
    """Ключи маппинга — ровно статусы контракта requests.py (README п.1)."""
    contract = {
        DRAFT,
        IN_APPROVAL,
        REWORK,
        AGREED,
        TO_EXECUTION,
        DONE,
        REJECTED,
        REVOKED,
    }
    assert set(REQUEST_STATUS_TO_CODE) == contract


def test_step_status_mapping_roundtrip():
    """Статусы шага: русский контракт <-> коды CHECK миграции 0001."""
    contract = {
        STEP_PENDING,
        STEP_APPROVED,
        STEP_REJECTED,
        STEP_RETURNED,
        STEP_EXPIRED,
    }
    assert set(STEP_STATUS_TO_CODE) == contract
    for russian, code in STEP_STATUS_TO_CODE.items():
        assert step_status_to_db(russian) == code
        assert step_status_from_db(code) == russian


def test_route_origin_mapping():
    """route_origin: custom в модели = manual в БД (синонимы ручного маршрута)."""
    assert ROUTE_ORIGIN_TO_CODE == {"template": "template", "custom": "manual"}
    assert route_origin_to_db("template") == "template"
    assert route_origin_to_db("custom") == "manual"
    assert route_origin_from_db("template") == "template"
    assert route_origin_from_db("manual") == "custom"


def test_unknown_mapping_raises():
    """Неизвестные статусы/коды — ValueError, а не тихое искажение данных."""
    with pytest.raises(ValueError):
        request_status_to_db("Нет такого статуса")
    with pytest.raises(ValueError):
        request_status_from_db("no_such_code")
    with pytest.raises(ValueError):
        step_status_to_db("нет такого статуса")
    with pytest.raises(ValueError):
        step_status_from_db("no_such_code")
    with pytest.raises(ValueError):
        route_origin_from_db("custom")


def test_req_id_number_conversion():
    """Бизнес-номер REQ-XXXX <-> число (для next_id по MAX числовой части)."""
    assert req_number_to_id(1) == "REQ-0001"
    assert req_number_to_id(42) == "REQ-0042"
    assert req_id_to_number("REQ-0001") == 1
    assert req_id_to_number("REQ-0042") == 42
    for bad in ("REQ-", "REQ-0001a", "abc", ""):
        with pytest.raises(ValueError):
            req_id_to_number(bad)


def test_db_store_implements_protocol():
    """DbRequestsStore: конструирование движка не коннектится к Postgres,
    все методы интерфейса на месте. Полный round-trip — на стенде с живой БД
    (qa-sed): локально Postgres нет, сеть в тестах не задеваем."""
    store = DbRequestsStore("postgresql://localhost:1/sed")
    for method in ("create", "get", "list_all", "update", "next_id"):
        assert callable(getattr(store, method))
# Подбор маршрута из справочников (route_mode = auto) в requests.py: сборка
# шагов по профилю службы, снятие/добавление этапов, 422 при отсутствии
# профиля/руководителя, прежнее поведение custom и право по реестру этапа
# (owner_kind = stage_roster). Справочники подменены in-memory хранилищем через
# подмену зависимости get_routing_store (как get_requests_store в test_requests.py).
# Все службы, группы, логины и ФИО ниже — вымышленные.

from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    IN_APPROVAL,
    RouteSettings,
    _public_view,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests_store import get_requests_store  # noqa: E402
from app.routing_store import get_routing_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_STEP_PREFIX = "SED_STEP_"

# --- Вымышленные данные справочников и сотрудников ---
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_SERVICE_OTHER = "Служба вымышленного документооборота"
FAKE_AD_TITLE = "Вымышленный старший кассир"
FAKE_STEP_GROUP = "SED_STEP_BUH"
FAKE_ROSTER_STEP = "SED_STEP_OTHER"
FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_TAB = "В-0001"
EMP_SAM = "sotrudnik.vymyshlennyy"
EMP_MANAGER_DN = "CN=Вымышленный Начальник,OU=SED,DC=example,DC=local"
MANAGER_SAM = "rukovoditel.vymyshlennyy"
ROSTER_SAM = "chlen.reestra"
OUTSIDER_SAM = "postoronniy.vymyshlennyy"
FAKE_EMPLOYEE_FIO = "Вымышленный Сотрудник Полный"

# Идентификаторы справочников (числа — как id в БД).
SERVICE_ID = 1
PROFILE_ID = 10
STAGE_BUH = 21
STAGE_BOSS = 22
STAGE_HR = 23
STAGE_EXTRA = 24

SERVICE_ROW = {
    "id": SERVICE_ID,
    "dept_name": FAKE_SERVICE,
    "status": "active",
    "active_count": 3,
    "last_seen_at": None,
    "blank_kind": "office",
    "route_profile_id": PROFILE_ID,
}
PROFILE_ROW = {
    "id": PROFILE_ID,
    "code": "buh_route",
    "name": "Маршрут вымышленной службы",
    "service_id": SERVICE_ID,
    "active": True,
}
STAGE_BUH_ROW = {
    "id": STAGE_BUH,
    "code": "buh",
    "title": "Бухгалтерия",
    "stage_lines": ["проверить расчёт"],
    "owner_kind": "ad_group",
    "owner_group": FAKE_STEP_GROUP,
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "active": True,
}
STAGE_BOSS_ROW = {
    "id": STAGE_BOSS,
    "code": "boss",
    "title": "Руководитель сотрудника",
    "stage_lines": [],
    "owner_kind": "manager_ad",
    "owner_group": None,
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "active": True,
}
STAGE_HR_ROW = {
    "id": STAGE_HR,
    "code": "hr",
    "title": "Отдел кадров (реестр)",
    "stage_lines": ["оформить прекращение"],
    "owner_kind": "stage_roster",
    "owner_group": None,
    "optional": True,
    "print_assignee": False,
    "require_comment": False,
    "active": True,
}
STAGE_EXTRA_ROW = {
    "id": STAGE_EXTRA,
    "code": "extra",
    "title": "Вымышленная служба безопасности",
    "stage_lines": [],
    "owner_kind": "ad_group",
    "owner_group": FAKE_ROSTER_STEP,
    "optional": True,
    "print_assignee": False,
    "require_comment": False,
    "active": True,
}
STAGES = [STAGE_BUH_ROW, STAGE_BOSS_ROW, STAGE_HR_ROW, STAGE_EXTRA_ROW]
EMPLOYEE_CARD = {
    "sam": EMP_SAM,
    "fio_full": FAKE_EMPLOYEE_FIO,
    "dept_ad": FAKE_SERVICE,
    "title_ad": FAKE_AD_TITLE,
    "manager_dn": EMP_MANAGER_DN,
}


def _profile_step(step_order: int, stage_row: dict, require_comment_override=None) -> dict:
    """Шаг профиля вместе с этапом (формат строки list_profile_steps)."""
    return {
        "profile_step_id": 300 + step_order,
        "profile_id": PROFILE_ID,
        "step_order": step_order,
        "optional_override": None,
        "require_comment_override": require_comment_override,
        "stage_id": stage_row["id"],
        "stage_code": stage_row["code"],
        "stage_title": stage_row["title"],
        "stage_lines": list(stage_row["stage_lines"]),
        "owner_kind": stage_row["owner_kind"],
        "owner_group": stage_row["owner_group"],
        "optional": stage_row["optional"],
        "print_assignee": stage_row["print_assignee"],
        "require_comment": stage_row["require_comment"],
        "stage_active": True,
    }


def _steps(order_stages) -> list[dict]:
    """Шаги профиля по списку этапов (порядок задаёт вызывающий тест)."""
    return [_profile_step(i, row) for i, row in enumerate(order_stages, start=1)]


class FakeRoutingStore:
    """Справочники маршрута в памяти: контракт DbRoutingStore на чтение.

    Поля переопределяются тестом (пустые профили — сценарий «профиль не найден»,
    другой порядок шагов — сценарий «реестр первым»)."""

    def __init__(
        self,
        services: list[dict] | None = None,
        profiles: list[dict] | None = None,
        profile_steps: list[dict] | None = None,
        rosters: dict[int, list[dict]] | None = None,
        cards: dict[str, dict] | None = None,
        manager_sams: dict[str, str] | None = None,
        manager_sams_flag: bool = True,
    ) -> None:
        self.services = [SERVICE_ROW] if services is None else services
        self.profiles = [PROFILE_ROW] if profiles is None else profiles
        self.profile_steps = (
            _steps([STAGE_BUH_ROW, STAGE_BOSS_ROW, STAGE_HR_ROW])
            if profile_steps is None
            else profile_steps
        )
        self.rosters = rosters if rosters is not None else {}
        self.cards = {EMP_SAM: dict(EMPLOYEE_CARD)} if cards is None else cards
        self.manager_sams = (
            {EMP_MANAGER_DN: MANAGER_SAM} if manager_sams is None else manager_sams
        )
        self.manager_sams_flag = manager_sams_flag

    # --- справочники ---
    def list_services(self, active_only: bool = False) -> list[dict]:
        return [dict(item) for item in self.services]

    def list_profiles(self, active_only: bool = False) -> list[dict]:
        return [dict(item) for item in self.profiles]

    def list_stages(self, active_only: bool = False) -> list[dict]:
        return [dict(item) for item in STAGES]

    def list_profile_steps(self, profile_id: int) -> list[dict]:
        return [dict(item) for item in self.profile_steps if item["profile_id"] == profile_id]

    def list_all_profile_steps(self) -> list[dict]:
        return [dict(item) for item in self.profile_steps]

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        return [dict(item) for item in self.rosters.get(stage_id, []) if item.get("active", True)]

    # --- сотрудники (только чтение users) ---
    def user_card(self, sam: str) -> dict | None:
        return self.cards.get(sam)

    def is_manager(self, sam: str) -> bool:
        return self.manager_sams_flag

    def manager_sam_by_dn(self, manager_dn: str) -> str | None:
        return self.manager_sams.get((manager_dn or "").strip())


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _headers_for(sam: str, groups: list[str]) -> dict:
    """Заголовки мок-пользователя с вымышленными ПДн."""
    return {
        "X-Mock-Sam": sam,
        "X-Mock-Fio": _b64("Вымышленный Пользователь Тестовый"),
        "X-Mock-Mail": _b64("%s@example.local" % sam),
        "X-Mock-Department": _b64("Вымышленный отдел"),
        "X-Mock-Title": _b64("Вымышленная должность"),
        "X-Mock-Groups": ",".join(groups),
    }


@pytest.fixture
def settings_override():
    """Группы тестовыми (дефолты кода нейтральные)."""
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
def route_override():
    """Настройки маршрута без шаблонов (они — ручной путь, здесь auto)."""
    route = RouteSettings(approval_ttl_days=5)
    app.dependency_overrides[get_route_settings] = lambda: route
    yield route
    app.dependency_overrides.pop(get_route_settings, None)


@pytest.fixture
def requests_store():
    """Хранилище заявок через зависимость (общий InMemory-экземпляр)."""
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_requests_store, None)


@pytest.fixture
def routing_store():
    """Справочники маршрута через зависимость (тест может заменить содержимое)."""
    store = FakeRoutingStore()
    app.dependency_overrides[get_routing_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_routing_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    """Чистое хранилище заявок и аудит на каждый тест."""
    requests_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    audit_log.clear_for_tests()


@pytest.fixture
def hr() -> dict:
    """Заголовки ОК (создание заявки)."""
    return _headers_for("ok.vymyshlennaya", ["SED_HR"])


def _create(client, headers, **kw):
    """Создание заявки в режиме auto по умолчанию (вымышленные поля 1С)."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": FAKE_EMPLOYEE_FIO,
        "tab_num": FAKE_TAB,
        "department": FAKE_SERVICE_OTHER,
        "position": "Вымышленная должность 1С",
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
        "ad_sam": EMP_SAM,
    }
    body.update(kw)
    return client.post("/requests", json=body, headers=headers)


def test_auto_route_steps_from_profile(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """auto: шаги собраны из этапов профиля, данные сотрудника — из AD,
    снимок подбора (профиль/служба/этапы) заполнен в заявке и шагах."""
    response = _create(client, hr)
    assert response.status_code == 201, response.text
    body = response.json()
    # Маршрут собран из профиля-шаблона; профиль/служба — по данных AD.
    assert body["route_origin"] == "template"
    assert body["profile_id"] == PROFILE_ID
    assert body["profile_name"] == PROFILE_ROW["name"]
    assert body["service_id"] == SERVICE_ID
    assert body["service_name"] == FAKE_SERVICE
    # Должность из AD приоритетнее значения из тела (служба — по совпадению).
    assert body["department"] == FAKE_SERVICE
    assert body["position"] == FAKE_AD_TITLE
    assert body["is_manager"] is True
    steps = body["steps"]
    assert [s["owner_group"] for s in steps] == [
        FAKE_STEP_GROUP,
        MANAGER_SAM,
        "hr",
    ]
    assert [s["stage_code"] for s in steps] == ["buh", "boss", "hr"]
    assert [s["stage_title"] for s in steps] == [
        "Бухгалтерия",
        "Руководитель сотрудника",
        "Отдел кадров (реестр)",
    ]
    assert steps[0]["resolver"] == "by_group"
    assert steps[1]["resolver"] == "ad_direct_manager"
    # Снимок этапа в модели шага: id этапа, строки бланка, шаг профиля.
    stored = requests_store.get(body["id"])
    first = stored.steps[0]
    assert first.stage_id == STAGE_BUH
    assert first.profile_step_id == 301
    assert first.stage_lines == ["проверить расчёт"]


def test_auto_route_dismissed_and_added(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """Снятый этап выпадает из маршрута, добавленный — дописывается в конец."""
    dismissed = _create(client, hr, dismissed_stages=["buh"])
    assert dismissed.status_code == 201, dismissed.text
    codes = [s["stage_code"] for s in dismissed.json()["steps"]]
    assert codes == ["boss", "hr"]

    added = _create(client, hr, added_stages=["extra"])
    assert added.status_code == 201, added.text
    codes = [s["stage_code"] for s in added.json()["steps"]]
    assert codes == ["buh", "boss", "hr", "extra"]


def test_auto_route_profile_not_found_422(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """Профиль не подобран — 422 с причиной; молчаливого ручного маршрута нет."""
    routing_store.profiles = []
    response = _create(client, hr)
    assert response.status_code == 422
    assert "профиль не найден" in response.text
    assert "profile_not_found" in response.text
    assert not requests_store.list_all()


def test_auto_route_service_not_registered_422(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """Служба не задана и профиля по умолчанию нет — 422 «служба не заведена»."""
    routing_store.profiles = []
    response = _create(client, hr, department="", ad_sam="")
    assert response.status_code == 422
    assert "служба не заведена" in response.text
    assert "service_not_registered" in response.text


def test_auto_route_manager_stage_without_manager_422(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """Этап manager_ad без руководителя в AD — 422 с предложением замены."""
    routing_store.manager_sams = {}
    response = _create(client, hr)
    assert response.status_code == 422
    assert "Руководитель сотрудника не определён" in response.text
    # В карточке DN руководителя есть, но не резолвится — причина именно в этом.
    assert "руководитель из AD не читается" in response.text
    assert "Руководитель" in response.text
    assert "manager" in response.text
    assert not requests_store.list_all()
    # Замена руководителя от ОК (manager) — этап назначается на неё.
    replaced = _create(client, hr, manager="zamen.vymyshlennaya")
    assert replaced.status_code == 201, replaced.text
    steps = replaced.json()["steps"]
    assert steps[1]["resolver"] == "ad_direct_manager"
    assert steps[1]["owner_group"] == "zamen.vymyshlennaya"


def test_auto_route_manager_stage_without_manager_dn_422(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """В карточке сотрудника вовсе нет руководителя — причина в тексте 422 другая."""
    routing_store.cards = {
        sam: {key: value for key, value in card.items() if key != "manager_dn"}
        for sam, card in routing_store.cards.items()
    }
    routing_store.manager_sams = {}
    response = _create(client, hr)
    assert response.status_code == 422
    assert "в AD не указан руководитель" in response.text
    assert not requests_store.list_all()


def test_route_mode_custom_keeps_previous_behaviour(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """custom: маршрут из steps, как раньше (origin=custom, без снимка этапа)."""
    response = _create(
        client, hr, route_mode="custom", steps=[{"owner_group": FAKE_STEP_GROUP}]
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["route_origin"] == "custom"
    assert body["profile_id"] is None
    assert body["service_id"] is None
    assert body["service_name"] is None
    assert body["is_manager"] is False
    step = body["steps"][0]
    assert step["owner_group"] == FAKE_STEP_GROUP
    assert step["stage_code"] is None
    assert step["stage_title"] is None


def test_roster_step_rights_by_roster(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """Право по реестру этапа (stage_roster): участник решает, посторонний — 403."""
    routing_store.profile_steps = _steps([STAGE_HR_ROW, STAGE_BUH_ROW])
    routing_store.rosters = {
        STAGE_HR: [{"sam": ROSTER_SAM, "position_title": "Инспектор", "active": True}]
    }
    created = _create(client, hr)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200

    outsider = _headers_for(OUTSIDER_SAM, [FAKE_ROSTER_STEP])
    denied = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=outsider,
    )
    assert denied.status_code == 403

    member = _headers_for(ROSTER_SAM, [FAKE_ROSTER_STEP])
    approved = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=member,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["steps"][0]["status"] == "согласован"
    assert approved.json()["status"] == IN_APPROVAL


def test_roster_step_can_act_flag(
    client, hr, settings_override, route_override, requests_store, routing_store
):
    """can_act реестрного шага: True у участника состава этапа, False у постороннего.

    Проверяется на карточке (_public_view), а не через GET /requests/{id}: доступ
    к карточке даётся владельцу группы-владельца шага, а участник реестра решает
    по шагу (см. test_roster_step_rights_by_roster)."""
    routing_store.profile_steps = _steps([STAGE_HR_ROW, STAGE_BUH_ROW])
    routing_store.rosters = {
        STAGE_HR: [{"sam": ROSTER_SAM, "position_title": None, "active": True}]
    }
    created = _create(client, hr)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200

    stored = requests_store.get(rid)
    assert stored.status == IN_APPROVAL
    member = CurrentUser(sam=ROSTER_SAM, groups=[], role="owner")
    outsider = CurrentUser(sam=OUTSIDER_SAM, groups=[], role="owner")
    assert _public_view(stored, member).steps[0].can_act is True
    assert _public_view(stored, outsider).steps[0].can_act is False
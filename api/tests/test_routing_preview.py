# Предпросмотр маршрута (POST /requests/route/preview) и видимость заявки
# участнику состава этапа (owner_kind = stage_roster): подбор профиля/этапов из
# справочников без создания заявки, исполнители по этапам (ФИО руководителя из
# AD, наименование группы, ФИО состава), снятие/добавление этапов, fail-soft на
# пустых справочниках. Отдельно — восстановление owner_kind шага, перечитанного
# из хранилища (в request_steps такой колонки нет): участник реестра видит заявку
# в карточке и в списке, посторонний — 403. Справочники, настройки и AD подменены
# in-memory заглушками через подмену зависимостей (как get_routing_store в
# test_requests_route_mode.py). Все службы, группы, логины и ФИО — вымышленные.

from __future__ import annotations

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    RouteSettings,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests_store import get_requests_store  # noqa: E402
from app.routing_store import get_routing_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402

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
FAKE_STEP_GROUP_NAME = "Согласующие вымышленной службы"
FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_TAB = "В-0001"
EMP_SAM = "sotrudnik.vymyshlennyy"
EMP_MANAGER_DN = "CN=Вымышленный Начальник,OU=SED,DC=example,DC=local"
MANAGER_SAM = "rukovoditel.vymyshlennyy"
MANAGER_FIO = "Вымышленный Начальник Полный"
ROSTER_SAM = "chlen.reestra"
ROSTER_FIO = "Вымышленный Член Реестра Полный"
OUTSIDER_SAM = "postoronniy.vymyshlennyy"
FAKE_EMPLOYEE_FIO = "Вымышленный Сотрудник Полный"

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
    "owner_group": "SED_STEP_OTHER",
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


def _profile_step(step_order: int, stage_row: dict) -> dict:
    """Шаг профиля вместе с этапом (формат строки list_profile_steps)."""
    return {
        "profile_step_id": 300 + step_order,
        "profile_id": PROFILE_ID,
        "step_order": step_order,
        "optional_override": None,
        "require_comment_override": None,
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

    Счётчики calls нужны тесту админ-справочников: один list_all_profile_steps
    вместо запроса на каждый профиль."""

    def __init__(
        self,
        services: list[dict] | None = None,
        profiles: list[dict] | None = None,
        profile_steps: list[dict] | None = None,
        rosters: dict[int, list[dict]] | None = None,
        cards: dict[str, dict] | None = None,
        manager_sams: dict[str, str] | None = None,
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
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    # --- справочники ---
    def list_services(self, active_only: bool = False) -> list[dict]:
        self._count("list_services")
        return [dict(item) for item in self.services]

    def list_profiles(self, active_only: bool = False) -> list[dict]:
        self._count("list_profiles")
        return [dict(item) for item in self.profiles]

    def list_stages(self, active_only: bool = False) -> list[dict]:
        self._count("list_stages")
        return [dict(item) for item in STAGES]

    def list_profile_steps(self, profile_id: int) -> list[dict]:
        self._count("list_profile_steps")
        return [dict(item) for item in self.profile_steps if item["profile_id"] == profile_id]

    def list_all_profile_steps(self) -> list[dict]:
        self._count("list_all_profile_steps")
        return [
            {
                "profile_step_id": item["profile_step_id"],
                "profile_id": item["profile_id"],
                "stage_id": item["stage_id"],
                "step_order": item["step_order"],
                "optional_override": item["optional_override"],
                "require_comment_override": item["require_comment_override"],
            }
            for item in self.profile_steps
        ]

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        self._count("list_stage_assignees")
        return [dict(item) for item in self.rosters.get(stage_id, []) if item.get("active", True)]

    # --- сотрудники (только чтение users) ---
    def user_card(self, sam: str) -> dict | None:
        self._count("user_card")
        return self.cards.get(sam)

    def is_manager(self, sam: str) -> bool:
        return True

    def manager_sam_by_dn(self, manager_dn: str) -> str | None:
        return self.manager_sams.get((manager_dn or "").strip())


class FakeAdUser:
    """Карточка сотрудника AD для заглушки ридера (только чтение)."""

    def __init__(self, sam: str, display_name: str, title: str = "") -> None:
        self.sam = sam
        self.display_name = display_name
        self.title = title


class FakeAdReader:
    """Ридер AD в памяти: карточки по логину и по DN (только чтение).

    Заглушка нужна этапам manager_ad в предпросмотре: ФИО руководителя берётся
    из AD по DN."""

    def __init__(self) -> None:
        self.by_dn = {EMP_MANAGER_DN: FakeAdUser(MANAGER_SAM, MANAGER_FIO, "Начальник")}
        self.by_sam = {MANAGER_SAM: FakeAdUser(MANAGER_SAM, MANAGER_FIO, "Начальник")}

    def get_user(self, sam: str) -> FakeAdUser | None:
        return self.by_sam.get((sam or "").strip())

    def get_user_by_dn(self, dn: str) -> FakeAdUser | None:
        return self.by_dn.get((dn or "").strip())


class SeededSettingsStore:
    """Настройки в памяти с одним ключом (наименования групп шагов)."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_many(self, keys) -> dict[str, str | None]:
        return {key: self.values.get(key) for key in keys}

    def set_many(self, values) -> None:
        self.values.update(values)


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


@pytest.fixture
def ad_reader():
    """Ридер AD в памяти (этапы manager_ad предпросмотра)."""
    reader = FakeAdReader()
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


@pytest.fixture
def groups_store():
    """Настройки с наименованием группы-владельца этапа (owner_name в предпросмотре).

    Заодно включён запасной подбор маршрута по службе (blank_autopick="on"):
    модуль проверяет именно его, ведь бланк выбирает сотрудник ОК."""
    store = SeededSettingsStore(
        {
            "allowed_ad_groups": json.dumps(
                [{"id": FAKE_STEP_GROUP, "name": FAKE_STEP_GROUP_NAME}], ensure_ascii=False
            ),
            "blank_autopick": json.dumps("on"),
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


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
    """Заголовки ОК (создание заявки и предпросмотр)."""
    return _headers_for("ok.vymyshlennaya", ["SED_HR"])


def _preview_body(**kw) -> dict:
    """Тело предпросмотра: идентификация сотрудника из вымышленных полей 1С."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "tab_num": FAKE_TAB,
        "department": FAKE_SERVICE_OTHER,
        "position": "Вымышленная должность 1С",
        "ad_sam": EMP_SAM,
    }
    body.update(kw)
    return body


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


# --- Предпросмотр маршрута ---


def test_preview_registered_service_stages_and_owners(
    client, hr, settings_override, route_override, ad_reader, groups_store, routing_store
):
    """Зарегистрированная служба: профиль подобран, этапы с исполнителем и бланком.

    owner_name по виду этапа: группа — наименование из allowed_ad_groups,
    руководитель — ФИО из AD по DN, реестр — ФИО состава этапа из users."""
    routing_store.rosters = {
        STAGE_HR: [{"sam": ROSTER_SAM, "position_title": "Инспектор", "active": True}]
    }
    routing_store.cards[ROSTER_SAM] = {"sam": ROSTER_SAM, "fio_full": ROSTER_FIO}
    response = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reason"] == "service_profile"
    assert body["profile"] == {
        "id": PROFILE_ID,
        "code": PROFILE_ROW["code"],
        "name": PROFILE_ROW["name"],
    }
    assert body["service"] == {
        "id": SERVICE_ID,
        "dept_name": FAKE_SERVICE,
        "blank_kind": "office",
    }
    assert body["blank"] == "office"
    stages = body["stages"]
    assert [stage["code"] for stage in stages] == ["buh", "boss", "hr"]
    assert [stage["owner_kind"] for stage in stages] == ["ad_group", "manager_ad", "stage_roster"]
    assert stages[0]["owner_group"] == FAKE_STEP_GROUP
    assert stages[0]["owner_name"] == FAKE_STEP_GROUP_NAME
    assert stages[0]["stage_lines"] == ["проверить расчёт"]
    assert stages[0]["optional"] is False and stages[0]["blocked_reason"] is None
    assert stages[1]["owner_name"] == MANAGER_FIO
    assert stages[1]["blocked_reason"] is None
    assert stages[2]["owner_name"] == ROSTER_FIO
    assert stages[2]["blocked_reason"] is None
    # Предпросмотр ничего не сохраняет и не аудирует.
    assert not get_memory_requests_store().list_all()
    assert [event.action for event in audit_log.all()] == []


def test_preview_service_not_registered(
    client, hr, settings_override, route_override, ad_reader, groups_store, routing_store
):
    """Служба не задана и профиля по умолчанию нет — service_not_registered, без 422."""
    routing_store.profiles = []
    response = client.post(
        "/requests/route/preview", json=_preview_body(ad_sam="", department=""), headers=hr
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reason"] == "service_not_registered"
    assert body["profile"] is None and body["service"] is None
    assert body["stages"] == [] and body["blank"] is None


def test_preview_profile_not_found_for_unknown_service(
    client, hr, settings_override, route_override, ad_reader, groups_store, routing_store
):
    """У службы нет профиля и профиля по умолчанию — profile_not_found (не 500)."""
    routing_store.profiles = []
    response = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert response.status_code == 200, response.text
    body = response.json()
    # Служба зарегистрирована, но её профиль недоступен — это пометка к причине.
    assert body["reason"].startswith("profile_not_found")
    assert body["profile"] is None and body["stages"] == []
    # Служба опознана (по ней и подбирался профиль) — её видно в ответе.
    assert body["service"]["id"] == SERVICE_ID


def test_preview_empty_catalogs_fail_soft(
    client, hr, settings_override, route_override, ad_reader, groups_store, routing_store
):
    """Пустые справочники — ответ с причиной подбора и пустыми этапами, не 500."""
    routing_store.services = []
    routing_store.profiles = []
    routing_store.profile_steps = []
    response = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reason"] == "profile_not_found"
    assert body["stages"] == [] and body["profile"] is None


def test_preview_dismissed_and_added_stages(
    client, hr, settings_override, route_override, ad_reader, groups_store, routing_store
):
    """Снятые этапы выпадают из предпросмотра, добавленные — дописываются в конец."""
    dismissed = client.post(
        "/requests/route/preview", json=_preview_body(dismissed_stages=["buh"]), headers=hr
    )
    assert dismissed.status_code == 200, dismissed.text
    assert [stage["code"] for stage in dismissed.json()["stages"]] == ["boss", "hr"]

    added = client.post(
        "/requests/route/preview", json=_preview_body(added_stages=["extra"]), headers=hr
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert [stage["code"] for stage in body["stages"]] == ["buh", "boss", "hr", "extra"]
    assert body["reason"].startswith("service_profile+Added=extra")


def test_preview_blocked_reasons_without_executor(
    client, hr, settings_override, route_override, ad_reader, groups_store, routing_store
):
    """Этапы, которые нечем закрыть, остаются в ответе с blocked_reason."""
    routing_store.cards[EMP_SAM] = dict(EMPLOYEE_CARD, manager_dn=None)
    response = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert response.status_code == 200, response.text
    stages = {stage["code"]: stage for stage in response.json()["stages"]}
    # Руководитель не найден в AD: создание заявки дало бы 422, предпросмотр — текст.
    assert stages["boss"]["owner_name"] is None
    assert "руководитель" in stages["boss"]["blocked_reason"].lower()
    # Пустой состав этапа-реестра.
    assert stages["hr"]["owner_name"] is None
    assert "состав этапа" in stages["hr"]["blocked_reason"].lower()


def test_preview_requires_hr_role(
    client, noauth_headers, settings_override, route_override,
    ad_reader, groups_store, routing_store
):
    """Предпросмотр — только разрешенной группе, как создание заявки (401 без входа)."""
    body = _preview_body()
    assert client.post("/requests/route/preview", json=body, headers=noauth_headers).status_code == 401
    # Владелец шага (группа SED_STEP_*) входит в систему, но не ОК.
    owner = _headers_for(OUTSIDER_SAM, [FAKE_STEP_GROUP])
    forbidden = client.post("/requests/route/preview", json=body, headers=owner)
    assert forbidden.status_code == 403
    assert "разрешенной группе" in forbidden.text


def test_create_request_still_works_after_preview_route(
    client, hr, settings_override, route_override, requests_store, ad_reader,
    groups_store, routing_store
):
    """Предпросмотр не перехватывает путь создания: POST /requests отвечает 201."""
    assert client.post("/requests/route/preview", json=_preview_body(), headers=hr).status_code == 200
    created = _create(client, hr)
    assert created.status_code == 201, created.text
    assert created.json()["route_origin"] == "template"


# --- Видимость заявки участнику состава этапа ---


def _drop_owner_kind(stored) -> None:
    """Сыграть перечитывание из Postgres: owner_kind в request_steps не хранится."""
    for step in stored.steps:
        step.owner_kind = None


def test_roster_member_sees_request_after_store_reload(
    client, hr, settings_override, route_override, requests_store, routing_store, groups_store
):
    """owner_kind восстановлен из справочника: участник реестра видит заявку.

    Карточка после перечитывания (owner_kind сброшен, как в Postgres): участник
    состава этапа получает 200 и can_act, ПДн (tab_num/fio) остаются обрезанными."""
    routing_store.profile_steps = _steps([STAGE_HR_ROW, STAGE_BUH_ROW])
    routing_store.rosters = {
        STAGE_HR: [{"sam": ROSTER_SAM, "position_title": "Инспектор", "active": True}]
    }
    created = _create(client, hr)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    _drop_owner_kind(requests_store.get(rid))

    member = _headers_for(ROSTER_SAM, [FAKE_STEP_GROUP])
    card = client.get(f"/requests/{rid}", headers=member)
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["steps"][0]["can_act"] is True
    # Видимость реестрного участника не шире владельца шага: ПДн обрезаны.
    assert body["tab_num"] is None and body["fio"] is None
    assert [item["id"] for item in client.get("/requests", headers=member).json()] == [rid]

    outsider = _headers_for(OUTSIDER_SAM, ["SED_STEP_OTHER"])
    assert client.get(f"/requests/{rid}", headers=outsider).status_code == 403
    assert client.get("/requests", headers=outsider).json() == []
    # Счётчик «Мои задачи» считает ту же видимость, что и список.
    folders = {f["id"]: f for f in client.get("/folders", headers=member).json()}
    assert folders["mine"]["count"] == 1


def test_roster_member_decision_after_store_reload(
    client, hr, settings_override, route_override, requests_store, routing_store, groups_store
):
    """Право по реестру работает и после перечитывания: отметка участника принята."""
    routing_store.profile_steps = _steps([STAGE_HR_ROW, STAGE_BUH_ROW])
    routing_store.rosters = {
        STAGE_HR: [{"sam": ROSTER_SAM, "position_title": None, "active": True}]
    }
    created = _create(client, hr)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    _drop_owner_kind(requests_store.get(rid))

    member = _headers_for(ROSTER_SAM, [FAKE_STEP_GROUP])
    approved = client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=member
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["steps"][0]["status"] == "согласован"


# --- Администрирование шагов профиля ---


def test_admin_replaces_profile_steps(client, admin_headers, settings_override, routing_store):
    """PUT шагов профиля: состав заменяется, дубль порядка — 422."""
    stored: dict = {}

    class _WritableRoutingStore(FakeRoutingStore):
        """Заглушка записи шагов профиля (вызовы фиксируются для проверок)."""

        def set_profile_steps(self, profile_id, items, actor):
            stored["profile_id"] = profile_id
            stored["items"] = list(items)
            stored["actor"] = actor

    app.dependency_overrides[get_routing_store] = lambda: _WritableRoutingStore()

    ok = client.put(
        "/settings/routing/profiles/10/steps",
        json={
            "steps": [
                {"stage_id": 21, "step_order": 1},
                {"stage_id": 23, "step_order": 2, "optional_override": False},
            ]
        },
        headers=admin_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"profile_id": 10, "count": 2}
    assert stored["profile_id"] == 10 and stored["actor"] == "adm.petrov"
    assert stored["items"][1]["optional_override"] is False

    duplicate = client.put(
        "/settings/routing/profiles/10/steps",
        json={"steps": [{"stage_id": 21, "step_order": 1}, {"stage_id": 23, "step_order": 1}]},
        headers=admin_headers,
    )
    assert duplicate.status_code == 422
    assert "уникален" in duplicate.text


def test_admin_profile_steps_requires_admin(client, hr, settings_override, routing_store):
    """Правка шагов профиля — только админу (ОК 403), как у профилей и этапов."""
    response = client.put(
        "/settings/routing/profiles/10/steps",
        json={"steps": [{"stage_id": 21, "step_order": 1}]},
        headers=hr,
    )
    assert response.status_code == 403


def test_catalogs_read_profile_steps_in_one_query(
    client, admin_headers, settings_override, groups_store, routing_store
):
    """Справочники админки: шаги всех профилей ОДНИМ запросом (без N+1)."""
    response = client.get("/settings/routing/catalogs", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert routing_store.calls["list_all_profile_steps"] == 1
    assert "list_profile_steps" not in routing_store.calls
    assert [item["profile_id"] for item in body["profile_steps"]] == [PROFILE_ID] * 3
# Выбор бланка сотрудником ОК при создании заявки (миграция 0012): список
# бланков для селекта (GET /requests/route/blanks), предпросмотр по выбранному
# бланку вместо профиля службы, запасная автоподстановка по службе за настройкой
# blank_autopick и снимок бланка (blank_id/name/version/layout) в заявке.
# Справочники, настройки, AD и локальный справочник подменены in-memory
# заглушками через подмену зависимостей (как в test_routing_preview.py).
# Все службы, группы, бланки, логины и ФИО — вымышленные.

from __future__ import annotations

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.employee_sync import (  # noqa: E402
    InMemoryEmployeeSyncStore,
    get_employee_sync_store,
)
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    RouteSettings,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests_store import get_requests_store  # noqa: E402
from app.routing import pick_blank_steps, pick_profile  # noqa: E402
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
STEP_GROUP = "SED_STEP_BUH"
STEP_GROUP_NAME = "Согласующие вымышленной службы"
FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_TAB = "В-0001"
FAKE_TAB_NOLINK = "В-0002"
EMP_SAM = "sotrudnik.vymyshlennyy"
EMP_FIO = "Вымышленный Сотрудник Полный"
EMP_MANAGER_DN = "CN=Вымышленный Начальник,OU=SED,DC=example,DC=local"
MANAGER_SAM = "rukovoditel.vymyshlennyy"
MANAGER_FIO = "Вымышленный Начальник Полный"
ROSTER_SAM = "chlen.reestra"
ROSTER_FIO = "Вымышленный Член Реестра Полный"
CANDIDATE_SAM = "kandidat.vymyshlennyy"
CANDIDATE_FIO = "Вымышленный Кандидат Полный"

SERVICE_ID = 1
PROFILE_ID = 10
STAGE_BUH = 21
STAGE_BOSS = 22
STAGE_HR = 23

BLANK_ID = 30
BLANK_CODE = "uvol_mol"
BLANK_NAME = "Увольнение вымышленного МОЛ"
BLANK_VERSION = 4
BLANK_STEP_COUNT = 3
BLANK_HEADER = "<p>Увольнение {fio}</p>"
BLANK_FOOTER = ["Подпись {fio}", "Дата {date}"]
BLANK_LINE_ID = 31
BLANK_LINE_CODE = "uvol_line"
BLANK_OFF_ID = 32
BLANK_EMPTY_ID = 33

SERVICE_ROW = {
    "id": SERVICE_ID,
    "dept_name": FAKE_SERVICE,
    "status": "active",
    "active_count": 2,
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
    "owner_group": STEP_GROUP,
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
STAGES = [STAGE_BUH_ROW, STAGE_BOSS_ROW, STAGE_HR_ROW]
EMP_CARD = {
    "sam": EMP_SAM,
    "fio_full": EMP_FIO,
    "dept_ad": FAKE_SERVICE,
    "title_ad": FAKE_AD_TITLE,
    "manager_dn": EMP_MANAGER_DN,
}
BLANK_ROW = {
    "id": BLANK_ID,
    "code": BLANK_CODE,
    "name": BLANK_NAME,
    "doc_type_code": BLANK_CODE,
    "description": "Вымышленный бланк с тремя шагами",
    "layout": "office",
    "active": True,
    "version": BLANK_VERSION,
    "updated_at": None,
    "updated_by": None,
    "header_html": BLANK_HEADER,
    "footer_lines": list(BLANK_FOOTER),
}
BLANK_LINE_ROW = dict(
    BLANK_ROW,
    id=BLANK_LINE_ID,
    code=BLANK_LINE_CODE,
    name="Увольнение вымышленного линейного",
    layout="line",
    version=1,
    header_html=None,
    footer_lines=[],
)
BLANK_OFF_ROW = dict(
    BLANK_ROW,
    id=BLANK_OFF_ID,
    code="uvol_old",
    name="Бланк вымышленный старый",
    active=False,
)
# Активный бланк без шагов: выбрать его нельзя (маршрут заявке нечем задать).
BLANK_EMPTY_ROW = dict(
    BLANK_ROW,
    id=BLANK_EMPTY_ID,
    code="uvol_empty",
    name="Бланк вымышленный пустой",
    header_html=None,
    footer_lines=[],
)


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


def _blank_step(
    blank_id: int,
    step_order: int,
    title: str,
    executor_kind: str = "ad_group",
    **kw,
) -> dict:
    """Самостоятельный шаг бланка (формат строки list_blank_steps, миграция 0014).

    У шага свой текст (title/stage_lines) и свой исполнитель: people — список
    логинов согласующих (assignees), ad_group — группа AD, manager_ad —
    руководитель сотрудника. Этапа у шага нет."""
    step = {
        "blank_id": blank_id,
        "step_order": step_order,
        "title": title,
        "stage_lines": ["Вымышленная строка шага"],
        "executor_kind": executor_kind,
        "assignees": [],
        "owner_group": None,
        "optional": False,
        "require_comment": False,
        "approval_mode": "sequential",
    }
    step.update(kw)
    return step


def _steps(order_stages) -> list[dict]:
    """Шаги профиля по списку этапов (порядок задаёт вызывающий тест)."""
    return [_profile_step(i, row) for i, row in enumerate(order_stages, start=1)]


class FakeRoutingStore:
    """Справочники маршрута в памяти: профили/этапы + бланки и их состав."""

    def __init__(self) -> None:
        self.services = [dict(SERVICE_ROW)]
        self.profiles = [dict(PROFILE_ROW)]
        self.stages = [dict(row) for row in STAGES]
        self.profile_steps = _steps([STAGE_BUH_ROW, STAGE_BOSS_ROW])
        self.blanks = [
            dict(row)
            for row in (BLANK_ROW, BLANK_LINE_ROW, BLANK_OFF_ROW, BLANK_EMPTY_ROW)
        ]
        # Порядок шагов бланка намеренно отличается от профиля: маршрут задаёт
        # бланк, а не профиль службы.
        self.blank_steps = [
            _blank_step(BLANK_ID, 1, "Руководитель сотрудника", "manager_ad",
                        stage_lines=[]),
            _blank_step(BLANK_ID, 2, "Бухгалтерия", "ad_group", owner_group=STEP_GROUP,
                        stage_lines=["проверить расчёт"]),
            _blank_step(BLANK_ID, 3, "Отдел кадров", "people",
                        assignees=[ROSTER_SAM], stage_lines=["оформить прекращение"],
                        approval_mode="parallel"),
            _blank_step(BLANK_LINE_ID, 1, "Бухгалтерия", "ad_group",
                        owner_group=STEP_GROUP),
        ]
        self.cards = {
            EMP_SAM: dict(EMP_CARD),
            ROSTER_SAM: {"sam": ROSTER_SAM, "fio_full": ROSTER_FIO},
        }
        self.manager_sams = {EMP_MANAGER_DN: MANAGER_SAM}
        self.rosters = {
            STAGE_HR: [{"sam": ROSTER_SAM, "position_title": "Инспектор", "active": True}]
        }
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
        return [dict(item) for item in self.stages]

    def list_profile_steps(self, profile_id: int) -> list[dict]:
        self._count("list_profile_steps")
        return [dict(item) for item in self.profile_steps if item["profile_id"] == profile_id]

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        self._count("list_stage_assignees")
        return [
            dict(item)
            for item in self.rosters.get(stage_id, [])
            if item.get("active", True)
        ]

    # --- бланки (миграция 0012) ---
    def list_blanks(self, active_only: bool = False) -> list[dict]:
        self._count("list_blanks")
        rows = []
        for item in self.blanks:
            if active_only and not item.get("active"):
                continue
            row = dict(item)
            row["step_count"] = len(
                [s for s in self.blank_steps if s["blank_id"] == item["id"]]
            )
            rows.append(row)
        return rows

    def blank_by_id(self, blank_id: int) -> dict | None:
        self._count("blank_by_id")
        found = next((item for item in self.blanks if item["id"] == blank_id), None)
        if found is None:
            return None
        row = dict(found)
        row["step_count"] = len(
            [s for s in self.blank_steps if s["blank_id"] == blank_id]
        )
        return row

    def list_blank_steps(self, blank_id: int) -> list[dict]:
        self._count("list_blank_steps")
        return [dict(item) for item in self.blank_steps if item["blank_id"] == blank_id]

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

    def __init__(
        self,
        sam: str,
        display_name: str,
        title: str = "",
        department: str = "",
        manager_dn: str = "",
    ) -> None:
        self.sam = sam
        self.display_name = display_name
        self.title = title
        self.department = department
        self.manager_dn = manager_dn
        self.mail = "%s@example.local" % sam


class FakeAdReader:
    """Ридер AD в памяти: карточки по логину/DN и поиск по ФИО (только чтение).

    Нужен этапам manager_ad (ФИО руководителя по DN) и состоянию связи 1С↔AD
    (поиск кандидата по ФИО)."""

    def __init__(self) -> None:
        self.by_dn = {EMP_MANAGER_DN: FakeAdUser(MANAGER_SAM, MANAGER_FIO, "Начальник")}
        self.by_sam = {MANAGER_SAM: FakeAdUser(MANAGER_SAM, MANAGER_FIO, "Начальник")}
        self.found: list[FakeAdUser] = []

    def get_user(self, sam: str) -> FakeAdUser | None:
        return self.by_sam.get((sam or "").strip())

    def get_user_by_dn(self, dn: str) -> FakeAdUser | None:
        return self.by_dn.get((dn or "").strip())

    def search_users(self, term: str) -> list[FakeAdUser]:
        return list(self.found)


class SeededSettingsStore:
    """Настройки в памяти; значения — строками в сид-формате (JSONB)."""

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
    """Справочники маршрута и бланки через зависимость (тест правит содержимое)."""
    store = FakeRoutingStore()
    app.dependency_overrides[get_routing_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_routing_store, None)


@pytest.fixture
def ad_reader():
    """Ридер AD в памяти (этапы manager_ad предпросмотра, кандидат связи)."""
    reader = FakeAdReader()
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


@pytest.fixture
def settings_store():
    """Настройки с наименованием группы-владельца этапа; ключа blank_autopick нет.

    Отсутствие ключа — это «автоподстановка выключена»: тест, которому нужен
    включённый механизм, добавляет blank_autopick сам (значение — "on"/"off")."""
    store = SeededSettingsStore(
        {
            "allowed_ad_groups": json.dumps(
                [{"id": STEP_GROUP, "name": STEP_GROUP_NAME}], ensure_ascii=False
            )
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture
def employee_store():
    """Локальный справочник сотрудников: ФИО по табельному номеру (поиск кандидата)."""
    store = InMemoryEmployeeSyncStore()
    app.dependency_overrides[get_employee_sync_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_employee_sync_store, None)


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
        "fio": EMP_FIO,
        "tab_num": FAKE_TAB,
        "department": FAKE_SERVICE_OTHER,
        "position": "Вымышленная должность 1С",
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
        "ad_sam": EMP_SAM,
    }
    body.update(kw)
    return client.post("/requests", json=body, headers=headers)


# --- Сборка шагов бланка (app.routing.pick_blank_steps) ---


def test_pick_blank_steps_order_and_own_flags():
    """Шаги бланка: порядок по step_order, флаги и исполнитель — свои.

    Этапа у шага нет (нет stage_active/stage_id), поэтому в маршрут попадает
    каждый переданный шаг; профиль/служба/причина подбора остаются от базового
    RoutePick — для выбранного бланка они справочная подсказка."""
    base = pick_profile(FAKE_SERVICE, [dict(SERVICE_ROW)], [dict(PROFILE_ROW)], [])
    assert base.profile is not None
    steps = [
        # Порядок задаётся не порядком строк, а step_order.
        _blank_step(BLANK_ID, 2, "Отдел кадров", "people",
                    assignees=[ROSTER_SAM], optional=True),
        _blank_step(BLANK_ID, 1, "Бухгалтерия", "ad_group", owner_group=STEP_GROUP),
    ]
    picked = pick_blank_steps(base, steps)
    assert [stage.get("title") for stage, _ in picked.stages] == [
        "Бухгалтерия",
        "Отдел кадров",
    ]
    assert [stage.get("executor_kind") for stage, _ in picked.stages] == [
        "ad_group",
        "people",
    ]
    assert [optional for _, optional in picked.stages] == [False, True]
    assert picked.stages[1][0]["assignees"] == [ROSTER_SAM]
    assert picked.profile["id"] == PROFILE_ID
    assert picked.service["id"] == SERVICE_ID
    assert picked.reason == base.reason


def test_pick_blank_steps_keeps_stage_lines_and_mode():
    """Текст шага и режим из строки бланка доезжают до маршрута как есть."""
    base = pick_profile(FAKE_SERVICE, [dict(SERVICE_ROW)], [dict(PROFILE_ROW)], [])
    steps = [
        _blank_step(BLANK_ID, 1, "Отдел кадров", "people",
                    assignees=[ROSTER_SAM], stage_lines=["оформить", "передать"],
                    approval_mode="parallel", require_comment=True),
    ]
    picked = pick_blank_steps(base, steps)
    stage = picked.stages[0][0]
    assert stage["stage_lines"] == ["оформить", "передать"]
    assert stage["approval_mode"] == "parallel"
    assert stage["require_comment"] is True


# --- Список бланков для селекта ОК ---


def test_route_blanks_lists_active_with_steps_and_autopick_off(
    client, hr, noauth_headers, settings_override, settings_store, routing_store
):
    """Доступны только активные бланки С ШАГАМИ; autopick выключен, пока ключа нет.

    Поля строки — то, что нужно форме: код, название, описание, макет, число
    шагов. Отключённый и пустой бланки в выборку не попадают: ни отключённый
    выбрать нельзя, ни пустой (маршрут заявке нечем задать)."""
    response = client.get("/requests/route/blanks", headers=hr)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body] == [BLANK_ID, BLANK_LINE_ID]
    assert [item["code"] for item in body] == [BLANK_CODE, BLANK_LINE_CODE]
    assert body[0]["name"] == BLANK_NAME
    assert body[0]["description"] == BLANK_ROW["description"]
    assert body[0]["layout"] == "office" and body[1]["layout"] == "line"
    assert body[0]["step_count"] == BLANK_STEP_COUNT
    assert body[1]["step_count"] == 1
    assert [item["autopick"] for item in body] == [False, False]
    # Список бланков — только для создания заявки (роль как у POST /requests).
    assert client.get("/requests/route/blanks", headers=noauth_headers).status_code == 401
    owner = _headers_for("shag.vymyshlennyy", [STEP_GROUP])
    assert client.get("/requests/route/blanks", headers=owner).status_code == 403


def test_route_blanks_autopick_flag_follows_setting(
    client, hr, settings_override, settings_store, routing_store
):
    """blank_autopick="on" — механизм подбора по службе включён (autopick=true)."""
    settings_store.values["blank_autopick"] = json.dumps("on")
    body = client.get("/requests/route/blanks", headers=hr).json()
    assert body and all(item["autopick"] is True for item in body)


# --- Предпросмотр по выбранному бланку ---


def test_preview_with_blank_uses_blank_steps_and_owners(
    client, hr, settings_override, settings_store, routing_store, ad_reader, requests_store
):
    """Бланк выбран: этапы и ответственные — его состав, порядок бланка.

    Порядок отличается от профиля службы (профиль — только подсказка):
    blank_source=chosen, а в blank — снимок бланка с числом шагов."""
    response = client.post(
        "/requests/route/preview", json=_preview_body(blank_id=BLANK_ID), headers=hr
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blank_source"] == "chosen"
    assert body["blank"] == {
        "id": BLANK_ID,
        "code": BLANK_CODE,
        "name": BLANK_NAME,
        "layout": "office",
        "version": BLANK_VERSION,
        "step_count": BLANK_STEP_COUNT,
    }
    stages = body["stages"]
    # Шаг бланка самостоятельный: своего этапа у него нет (code/stage_id пустые),
    # зато есть название, текст и вид исполнителя.
    assert [stage["title"] for stage in stages] == [
        "Руководитель сотрудника",
        "Бухгалтерия",
        "Отдел кадров",
    ]
    assert [stage["code"] for stage in stages] == [None, None, None]
    assert [stage["stage_id"] for stage in stages] == [None, None, None]
    assert [stage["owner_kind"] for stage in stages] == [
        "manager_ad",
        "ad_group",
        "people",
    ]
    # Ответственные по тому же резолву, что и у подбора по профилю.
    assert stages[0]["owner_name"] == MANAGER_FIO and stages[0]["blocked_reason"] is None
    assert stages[1]["owner_group"] == STEP_GROUP
    assert stages[1]["owner_name"] == STEP_GROUP_NAME
    assert stages[1]["stage_lines"] == ["проверить расчёт"]
    assert stages[2]["owner_name"] == ROSTER_FIO and stages[2]["blocked_reason"] is None
    # Профиль службы остаётся справочной подсказкой.
    assert body["profile"]["id"] == PROFILE_ID
    # Предпросмотр ничего не сохраняет и не аудирует.
    assert not requests_store.list_all()
    assert [event.action for event in audit_log.all()] == []


def test_preview_with_blank_uses_manager_override(
    client, hr, settings_override, settings_store, routing_store, ad_reader
):
    """Ручная замена руководителя применяется к шагу manager_ad выбранного бланка."""
    response = client.post(
        "/requests/route/preview",
        json=_preview_body(blank_id=BLANK_ID, manager=MANAGER_SAM),
        headers=hr,
    )
    assert response.status_code == 200, response.text
    boss = response.json()["stages"][0]
    assert boss["title"] == "Руководитель сотрудника" and boss["blocked_reason"] is None


def test_preview_without_blank_and_autopick_off_422_with_list(
    client, hr, settings_override, settings_store, routing_store, ad_reader
):
    """Бланк не выбран, автоподстановка выключена — 422 с вариантами и списком.

    Предпросмотр отказывает наравне с созданием (см.
    test_create_and_preview_without_blank_agree): молчаливый маршрут в
    предпросмотре обещал бы то, что создание не примет. Текст перечисляет оба
    варианта — выбрать бланк или задать маршрут вручную; отключённый бланк в
    списке не предлагается."""
    response = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "Выберите бланк" in detail
    assert BLANK_NAME in detail and BLANK_LINE_ROW["name"] in detail
    assert BLANK_OFF_ROW["name"] not in detail
    # Явное "off" ведёт себя так же, как отсутствие ключа.
    settings_store.values["blank_autopick"] = json.dumps("off")
    again = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert again.status_code == 422
    assert "Выберите бланк" in again.json()["detail"]


def test_create_and_preview_without_blank_agree(
    client, hr, settings_override, settings_store, requests_store, routing_store, ad_reader
):
    """Предпросмотр и создание без бланка отвечают одинаково — кодом и текстом.

    Расхождение (200 с подсказкой в предпросмотре против 422 при отправке) путало
    ОК: он видел готовый маршрут и упирался в отказ при создании."""
    preview = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    created = _create(client, hr, blank_id=None, route_mode="auto")
    assert preview.status_code == created.status_code == 422, (
        preview.text,
        created.text,
    )
    assert preview.json()["detail"] == created.json()["detail"]
    # Ни то, ни другое ничего не записало.
    assert not requests_store.list_all()


def test_preview_without_blank_autopick_on_picks_profile_by_service(
    client, hr, settings_override, settings_store, routing_store, ad_reader
):
    """blank_autopick="on": прежнее поведение — подбор профиля по службе.

    blank_source честно autopick, а blank остаётся прежним значением (вид бланка
    печати из справочника служб)."""
    settings_store.values["blank_autopick"] = json.dumps("on")
    response = client.post("/requests/route/preview", json=_preview_body(), headers=hr)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["blank_source"] == "autopick"
    assert body["blank"] == "office"
    assert body["reason"] == "service_profile"
    assert body["profile"]["id"] == PROFILE_ID
    assert [stage["code"] for stage in body["stages"]] == ["buh", "boss"]


def test_preview_unknown_blank_422(
    client, hr, settings_override, settings_store, routing_store, ad_reader
):
    """Несуществующий бланк — 422 с понятным текстом, а не 500 и не пустой маршрут."""
    response = client.post(
        "/requests/route/preview", json=_preview_body(blank_id=999999), headers=hr
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "не найден" in detail and BLANK_NAME in detail


def test_preview_inactive_blank_422(
    client, hr, settings_override, settings_store, routing_store, ad_reader
):
    """Отключённый бланк выбрать нельзя — 422 с перечнем доступных."""
    response = client.post(
        "/requests/route/preview", json=_preview_body(blank_id=BLANK_OFF_ID), headers=hr
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "отключён" in detail and BLANK_NAME in detail


def test_preview_blank_keeps_link_state_and_notice(
    client, hr, settings_override, settings_store, routing_store, ad_reader, employee_store
):
    """link_state/notice работают вместе с бланком: связь не оформлена, шаги показаны.

    Сотрудника нет в зеркале users, но в AD есть ровно один кандидат: предпросмотр
    предлагает подтвердить связь и при этом показывает маршрут выбранного бланка."""
    employee_store.upsert_many(
        [
            {
                "enterprise": FAKE_ENTERPRISE,
                "base_code": "zup_t1",
                "tab_num": FAKE_TAB_NOLINK,
                "fio": CANDIDATE_FIO,
                "department": None,
                "position": None,
                "ad_sam": None,
                "ad_status": None,
            }
        ]
    )
    ad_reader.found = [
        FakeAdUser(
            CANDIDATE_SAM,
            CANDIDATE_FIO,
            "Вымышленная должность",
            FAKE_SERVICE,
            EMP_MANAGER_DN,
        )
    ]
    response = client.post(
        "/requests/route/preview",
        json=_preview_body(blank_id=BLANK_ID, tab_num=FAKE_TAB_NOLINK, ad_sam=""),
        headers=hr,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["link_state"] == "need_link"
    assert body["link_candidate"]["sam"] == CANDIDATE_SAM
    assert body["notice"] and "Связь 1С↔AD не оформлена" in body["notice"]
    assert body["blank_source"] == "chosen"
    assert [stage["title"] for stage in body["stages"]] == [
        "Руководитель сотрудника",
        "Бухгалтерия",
        "Отдел кадров",
    ]


# --- Создание заявки по выбранному бланку ---


def test_create_auto_with_blank_writes_snapshot(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """auto + blank_id: свои шаги бланка в заявке и снимок blank_* (name/version/
    layout/шапка/подвал).

    Права/резолвы прежние: группа-владелец, руководитель из AD, персональные
    согласующие шага."""
    created = _create(client, hr, blank_id=BLANK_ID)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["route_origin"] == "template"
    assert body["blank_id"] == BLANK_ID
    assert body["blank_name"] == BLANK_NAME
    assert body["blank_version"] == BLANK_VERSION
    assert body["blank_layout"] == "office"

    stored = requests_store.get(body["id"])
    assert stored.blank_id == BLANK_ID
    assert stored.blank_name == BLANK_NAME
    assert stored.blank_version == BLANK_VERSION
    assert stored.blank_layout == "office"
    # Шаги — свои шаги бланка (порядок бланка, не профиля), этапа у них нет.
    assert [step.stage_title for step in stored.steps] == [
        "Руководитель сотрудника",
        "Бухгалтерия",
        "Отдел кадров",
    ]
    assert [step.stage_id for step in stored.steps] == [None, None, None]
    assert stored.steps[0].owner_kind == "manager_ad"
    assert stored.steps[0].resolver == "ad_direct_manager"
    assert stored.steps[0].assignee == MANAGER_SAM
    assert stored.steps[1].owner_group == STEP_GROUP
    assert stored.steps[1].resolver == "by_group"
    assert stored.steps[1].stage_lines == ["проверить расчёт"]
    # Персональный шаг: согласующие и режим — из шага бланка (people/parallel).
    assert stored.steps[2].owner_kind == "people"
    assert stored.steps[2].resolver == "by_user"
    assert stored.steps[2].assignees == [ROSTER_SAM]
    assert stored.steps[2].assignee == ROSTER_SAM
    assert stored.steps[2].approval_mode == "parallel"
    # Снимок шага в ответе — тот же, что в заявке.
    assert [step["stage_title"] for step in body["steps"]] == [
        "Руководитель сотрудника",
        "Бухгалтерия",
        "Отдел кадров",
    ]


def test_create_auto_with_blank_snapshots_header_and_footer(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Шапка и подвал бланка — снимок на момент выдачи (печать идёт по нему).

    Правка справочника после выдачи заявку не меняет: как blank_name/blank_version."""
    created = _create(client, hr, blank_id=BLANK_ID)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["blank_header_html"] == BLANK_HEADER
    assert body["blank_footer_lines"] == BLANK_FOOTER
    stored = requests_store.get(body["id"])
    assert stored.blank_header_html == BLANK_HEADER
    assert stored.blank_footer_lines == BLANK_FOOTER
    # Правка справочника не меняет выданную заявку.
    blank = next(item for item in routing_store.blanks if item["id"] == BLANK_ID)
    blank["header_html"] = "<p>Другая шапка</p>"
    blank["footer_lines"] = ["Другой подвал"]
    again = requests_store.get(body["id"])
    assert again.blank_header_html == BLANK_HEADER
    assert again.blank_footer_lines == BLANK_FOOTER


def test_create_auto_with_blank_without_header_keeps_empty_snapshot(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Бланк без шапки/подвала — снимок пустой (печать отдаст прежнее поведение)."""
    created = _create(client, hr, blank_id=BLANK_LINE_ID)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["blank_header_html"] is None
    assert body["blank_footer_lines"] == []


def test_create_auto_with_blank_and_manager_override(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Замена руководителя ОК работает и для этапа manager_ad выбранного бланка."""
    routing_store.manager_sams = {}
    routing_store.cards[EMP_SAM] = dict(EMP_CARD, manager_dn=None)
    created = _create(client, hr, blank_id=BLANK_ID, manager=MANAGER_SAM)
    assert created.status_code == 201, created.text
    stored = requests_store.get(created.json()["id"])
    assert stored.steps[0].assignee == MANAGER_SAM
    assert stored.steps[0].resolver == "ad_direct_manager"


def test_create_auto_with_blank_requires_manager_for_manager_stage(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Руководителя этапа manager_ad нет ни в AD, ни в замене — 422 (текст прежний)."""
    routing_store.manager_sams = {}
    routing_store.cards[EMP_SAM] = dict(EMP_CARD, manager_dn=None)
    response = _create(client, hr, blank_id=BLANK_ID)
    assert response.status_code == 422
    assert "Руководитель" in response.json()["detail"]


def test_create_auto_with_unknown_or_inactive_blank_422(
    client, hr, settings_override, routing_store, ad_reader
):
    """Несуществующий/отключённый бланк в создании — тот же 422, что в предпросмотре."""
    unknown = _create(client, hr, blank_id=999999)
    assert unknown.status_code == 422 and "не найден" in unknown.json()["detail"]
    inactive = _create(client, hr, blank_id=BLANK_OFF_ID)
    assert inactive.status_code == 422 and "отключён" in inactive.json()["detail"]


def test_create_auto_with_blank_without_steps_422(
    client, hr, settings_override, routing_store, ad_reader
):
    """Бланк без шагов выбрать нельзя: маршрут заявке нечем задать — 422, не 500."""
    response = _create(client, hr, blank_id=BLANK_EMPTY_ID)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "без шагов" in detail
    assert BLANK_EMPTY_ROW["name"] in detail and BLANK_NAME in detail


def test_create_auto_without_blank_and_autopick_off_422_with_variants(
    client, hr, settings_override, settings_store, requests_store, routing_store, ad_reader
):
    """Бланк не выбран, автоподстановка выключена — 422 с обоими вариантами.

    Раньше был 500/тихий отказ: теперь ОК видит, что выбрать — бланк или ручной
    маршрут (route_mode=custom с blocks/steps)."""
    response = _create(client, hr)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "Выберите бланк" in detail
    assert "маршрут вручную" in detail
    assert BLANK_NAME in detail
    assert not requests_store.list_all()
    # Ручной маршрут без бланка — рабочий путь (контракт п.1).
    manual = _create(
        client,
        hr,
        route_mode="custom",
        steps=[{"owner_group": STEP_GROUP, "resolver": "by_group"}],
    )
    assert manual.status_code == 201, manual.text


def test_create_custom_without_blank_still_works(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Ручной маршрут (custom) бланком не обязан быть — шаги из конструктора."""
    created = _create(
        client,
        hr,
        route_mode="custom",
        blocks=[
            {
                "mode": "sequential",
                "steps": [{"owner_group": STEP_GROUP, "resolver": "by_group"}],
            }
        ],
    )
    assert created.status_code == 201, created.text
    stored = requests_store.get(created.json()["id"])
    assert stored.route_origin == "custom"
    assert stored.blank_id is None and stored.blank_name is None
    assert stored.steps[0].owner_group == STEP_GROUP


# --- Снятие шага бланка по номеру шага (dismissed_step_orders) ---
# У шага бланка нет кода этапа, поэтому снять его кодом (dismissed_stages)
# нельзя — сотрудник ОК снимает шаг по его номеру в составе бланка
# (решение человека 2026-10-08).


def _blank_optional_steps(routing_store, blank_id: int, orders: list[int]) -> None:
    """Пометить шаги бланка необязательными (в тестовом справочнике optional=False)."""
    for step in routing_store.blank_steps:
        if step["blank_id"] == blank_id and step["step_order"] in orders:
            step["optional"] = True


def test_preview_blank_step_optional_flag_and_order(
    client, hr, settings_override, settings_store, routing_store, ad_reader
):
    """Предпросмотр отдаёт optional и номер шага бланка (для снятия по номеру).

    optional=false у шага по умолчанию — его снять нельзя; optional=true —
    можно, и предпросмотр показывает, какой это номер."""
    _blank_optional_steps(routing_store, BLANK_ID, [2])
    response = client.post(
        "/requests/route/preview", json=_preview_body(blank_id=BLANK_ID), headers=hr
    )
    assert response.status_code == 200, response.text
    stages = response.json()["stages"]
    assert [stage["step_order"] for stage in stages] == [1, 2, 3]
    assert [stage["optional"] for stage in stages] == [False, True, False]


def test_preview_dismiss_optional_blank_step_by_order(
    client, hr, settings_override, settings_store, routing_store, ad_reader, requests_store
):
    """Необязательный шаг бланка снимается по номеру — предпросмотр и создание.

    Предпросмотр ничего не сохраняет (как и прежде), создание отдаёт маршрут
    без снятого шага."""
    _blank_optional_steps(routing_store, BLANK_ID, [2])
    preview = client.post(
        "/requests/route/preview",
        json=_preview_body(blank_id=BLANK_ID, dismissed_step_orders=[2]),
        headers=hr,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert [stage["title"] for stage in body["stages"]] == [
        "Руководитель сотрудника",
        "Отдел кадров",
    ]
    assert "DismissedStep=2" in body["reason"]
    assert not requests_store.list_all()

    created = _create(client, hr, blank_id=BLANK_ID, dismissed_step_orders=[2])
    assert created.status_code == 201, created.text
    assert [step["stage_title"] for step in created.json()["steps"]] == [
        "Руководитель сотрудника",
        "Отдел кадров",
    ]


def test_dismiss_blank_step_by_order_in_create_and_snapshot(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Снятие по номеру в создании заявки; optional остаётся в снимке шага.

    Снимок нужен фронту и печати: правка справочника уже выданную заявку
    не меняет (миграция 0015)."""
    _blank_optional_steps(routing_store, BLANK_ID, [2])
    created = _create(client, hr, blank_id=BLANK_ID, dismissed_step_orders=[2])
    assert created.status_code == 201, created.text
    body = created.json()
    assert [step["stage_title"] for step in body["steps"]] == [
        "Руководитель сотрудника",
        "Отдел кадров",
    ]
    stored = requests_store.get(body["id"])
    assert [step.stage_title for step in stored.steps] == [
        "Руководитель сотрудника",
        "Отдел кадров",
    ]
    # Снимок optional по шагам: снятого нет, остальные сохранили признак.
    assert [step.optional for step in stored.steps] == [False, False]
    assert [step["optional"] for step in body["steps"]] == [False, False]


def test_blank_step_snapshot_keeps_optional_true(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Необязательный шаг, который остался в маршруте, хранит optional=True."""
    _blank_optional_steps(routing_store, BLANK_ID, [3])
    created = _create(client, hr, blank_id=BLANK_ID)
    assert created.status_code == 201, created.text
    stored = requests_store.get(created.json()["id"])
    assert [step.optional for step in stored.steps] == [False, False, True]


def test_dismiss_required_blank_step_422(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Обязательный шаг бланка снять нельзя — 422 с понятным текстом."""
    response = client.post(
        "/requests/route/preview",
        json=_preview_body(blank_id=BLANK_ID, dismissed_step_orders=[1]),
        headers=hr,
    )
    assert response.status_code == 422, response.text
    assert "обязательный" in response.json()["detail"]

    created = _create(client, hr, blank_id=BLANK_ID, dismissed_step_orders=[1])
    assert created.status_code == 422, created.text
    assert not requests_store.list_all()


def test_dismiss_unknown_step_order_422(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Номера вне состава шагов бланка — 422 с перечнем доступных номеров."""
    response = client.post(
        "/requests/route/preview",
        json=_preview_body(blank_id=BLANK_ID, dismissed_step_orders=[9]),
        headers=hr,
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "9" in detail and "1, 2, 3" in detail

    created = _create(client, hr, blank_id=BLANK_ID, dismissed_step_orders=[9])
    assert created.status_code == 422, created.text
    assert not requests_store.list_all()


def test_dismiss_all_blank_steps_422(
    client, hr, settings_override, requests_store, routing_store, ad_reader
):
    """Снять все шаги нельзя: заявке нечем задавать маршрут — 422."""
    _blank_optional_steps(routing_store, BLANK_ID, [1, 2, 3])
    created = _create(client, hr, blank_id=BLANK_ID, dismissed_step_orders=[1, 2, 3])
    assert created.status_code == 422, created.text
    assert "все шаги" in created.json()["detail"]
    assert not requests_store.list_all()


def test_dismissed_stages_still_work_for_profile_steps(
    client, hr, settings_override, settings_store, requests_store, routing_store, ad_reader
):
    """Снятие этапов профиля по кодам не сломано новым полем.

    Бланк не выбран (blank_autopick=on — маршрут по службе): маршрут из этапов
    справочника, у них есть коды, номера шагов бланка нет."""
    settings_store.values["blank_autopick"] = json.dumps("on")
    preview = client.post(
        "/requests/route/preview",
        json=_preview_body(dismissed_stages=["buh"]),
        headers=hr,
    )
    assert preview.status_code == 200, preview.text
    stages = preview.json()["stages"]
    assert [stage["code"] for stage in stages] == ["boss"]
    assert [stage["step_order"] for stage in stages] == [None]

    created = _create(client, hr, dismissed_stages=["buh"])
    assert created.status_code == 201, created.text
    assert [step["stage_code"] for step in created.json()["steps"]] == ["boss"]
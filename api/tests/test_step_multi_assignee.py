# Несколько ответственных у шага (миграция 0013, ТЗ BLANK_CONTRACT_STEPS):
# снимок ответственных шага (assignees — все активные участники реестра этапа,
# assignee — первый), режим шага (approval_mode: parallel — «кто-то один» из
# blank_steps, sequential — «все»), отметки (approvals), права по снимку,
# частичное согласие без движения заявки и без уведомлений, повтор отметки (409),
# отказ от одного ответственного сразу, печать и round-trip снимка через БД.
# Справочники, настройки, AD и локальный справочник подменены in-memory
# заглушками (как в test_requests_blank_flow.py). Все логины, ФИО и службы
# вымышленные.

from __future__ import annotations

import base64
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.documents import _fill_step_fio  # noqa: E402
from app.docs import build_blank_document, build_bypass_context  # noqa: E402
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.mailer import (  # noqa: E402
    EVENT_ASSIGNED,
    EVENT_RETURNED,
    FileMailQueue,
    get_mail_queue,
)
from app.requests import (  # noqa: E402
    AGREED,
    IN_APPROVAL,
    REWORK,
    STEP_APPROVED,
    STEP_PENDING,
    STEP_REJECTED,
    RouteSettings,
    _public_view,
    _Request,
    _Step,
    _utcnow,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests_store import DbRequestsStore, get_requests_store  # noqa: E402
from app.routing_store import get_routing_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"
STEP_GROUP = "SED_STEP_BUH"
STEP_GROUP_NAME = "Согласующие вымышленной службы"
# Группы-шаги реестра и постороннего: вход даёт префикс групп владельцев шагов,
# а права по шагу даёт только снимок ответственных (assignees).
ROSTER_GROUP = "SED_STEP_ROESTER"
OUTSIDER_GROUP = "SED_STEP_PROCHOY"

# --- Вымышленные данные справочников и сотрудников ---
FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Вымышленная должность 1С"
FAKE_TAB = "В-0001"
FAKE_FIO = "Вымышленный Сотрудник Полный"
EMP_SAM = "sotrudnik.vymyshlennyy"
EMP_CARD = {
    "sam": EMP_SAM,
    "fio_full": FAKE_FIO,
    "dept_ad": FAKE_SERVICE,
    "title_ad": "Вымышленный старший кассир",
    "manager_dn": None,
}
SERVICE_ID = 1
PROFILE_ID = 10
SERVICE_ROW = {
    "id": SERVICE_ID,
    "dept_name": FAKE_SERVICE,
    "status": "active",
    "active_count": 1,
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
# Два этапа-реестра: один «кто-то один» (parallel), другой «все» (sequential).
STAGE_PAR = 41
STAGE_SEQ = 42
STAGE_PAR_ROW = {
    "id": STAGE_PAR,
    "code": "roster_par",
    "title": "Реестр вымышленный (параллельный)",
    "stage_lines": ["оформить параллельный этап"],
    "owner_kind": "stage_roster",
    "owner_group": None,
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "active": True,
}
STAGE_SEQ_ROW = {
    "id": STAGE_SEQ,
    "code": "roster_seq",
    "title": "Реестр вымышленный (последовательный)",
    "stage_lines": ["оформить последовательный этап"],
    "owner_kind": "stage_roster",
    "owner_group": None,
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "active": True,
}
STAGES = [STAGE_PAR_ROW, STAGE_SEQ_ROW]
# Три активных участника реестра каждого этапа + один отключённый (в снимок не
# попадает: состав читается только активным).
ROSTER = [
    ("reestr.pervyy", "Вымышленный Член Реестра Первый"),
    ("reestr.vtoroy", "Вымышленный Член Реестра Второй"),
    ("reestr.tretiy", "Вымышленный Член Реестра Третий"),
]
ROSTER_OFF_SAM = "reestr.povyshennyi"
ROSTER_OFF = {"sam": ROSTER_OFF_SAM, "position_title": "Инспектор", "active": False}
ROSTER_FIOS = {sam: fio for sam, fio in ROSTER}

BLANK_PAR_ID = 51
BLANK_SEQ_ID = 52
BLANK_ROW = {
    "id": BLANK_PAR_ID,
    "code": "uvol_par",
    "name": "Бланк вымышленный параллельный",
    "doc_type_code": "uvol_par",
    "description": "Один шаг-реестр в режиме «кто-то один»",
    "layout": "office",
    "active": True,
    "version": 2,
}
BLANK_SEQ_ROW = dict(
    BLANK_ROW,
    id=BLANK_SEQ_ID,
    code="uvol_seq",
    name="Бланк вымышленный последовательный",
    description="Один шаг-реестр в режиме «все ответственные»",
)

MAIL_TEMPLATES_SEED = json.dumps(
    [
        {
            "code": EVENT_ASSIGNED,
            "subject": "Назначено {{ request_id }}",
            "body_html": "<html>{{ fio }} {{ url }}</html>",
        },
        {
            "code": EVENT_RETURNED,
            "subject": "Возврат {{ request_id }}",
            "body_html": "<html>{{ url }}</html>",
        },
    ],
    ensure_ascii=False,
)


def _blank_step(blank_id: int, step_order: int, stage_row: dict, mode: str) -> dict:
    """Шаг бланка вместе с этапом (формат строки list_blank_steps, миграция 0013)."""
    return {
        "blank_id": blank_id,
        "stage_id": stage_row["id"],
        "step_order": step_order,
        "optional_override": None,
        "require_comment_override": None,
        "approval_mode": mode,
        "stage_code": stage_row["code"],
        "title": stage_row["title"],
        "stage_lines": list(stage_row["stage_lines"]),
        "owner_kind": stage_row["owner_kind"],
        "owner_group": stage_row["owner_group"],
        "optional": stage_row["optional"],
        "print_assignee": stage_row["print_assignee"],
        "require_comment": stage_row["require_comment"],
        "stage_active": True,
    }


class FakeRoutingStore:
    """Справочники маршрута в памяти: служба, профиль, этапы, бланки, реестры."""

    def __init__(self) -> None:
        self.services = [dict(SERVICE_ROW)]
        self.profiles = [dict(PROFILE_ROW)]
        self.stages = [dict(row) for row in STAGES]
        self.blanks = [dict(BLANK_ROW), dict(BLANK_SEQ_ROW)]
        self.blank_steps = [
            _blank_step(BLANK_PAR_ID, 1, STAGE_PAR_ROW, "parallel"),
            _blank_step(BLANK_SEQ_ID, 1, STAGE_SEQ_ROW, "sequential"),
        ]
        self.cards = {EMP_SAM: dict(EMP_CARD)}
        # Участники реестров есть в зеркале users: по их логинам documents.py
        # подставляет ФИО в колонку «Ответственный» печатной формы.
        for sam, fio in ROSTER:
            self.cards[sam] = dict(
                EMP_CARD, sam=sam, fio_full=fio, title_ad="Вымышленный участник реестра"
            )
        self.rosters = {
            STAGE_PAR: [
                {"sam": sam, "position_title": None, "active": True} for sam, _ in ROSTER
            ]
            + [dict(ROSTER_OFF)],
            STAGE_SEQ: [
                {"sam": sam, "position_title": None, "active": True} for sam, _ in ROSTER
            ],
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
        return []

    def list_all_profile_steps(self) -> list[dict]:
        return []

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        self._count("list_stage_assignees")
        rows = [dict(item) for item in self.rosters.get(stage_id, [])]
        if active_only:
            rows = [item for item in rows if item.get("active", True)]
        return rows

    # --- бланки ---
    def list_blanks(self, active_only: bool = False) -> list[dict]:
        self._count("list_blanks")
        return [dict(item) for item in self.blanks if item.get("active") or not active_only]

    def blank_by_id(self, blank_id: int) -> dict | None:
        self._count("blank_by_id")
        found = next((item for item in self.blanks if item["id"] == blank_id), None)
        return dict(found) if found is not None else None

    def list_blank_steps(self, blank_id: int) -> list[dict]:
        self._count("list_blank_steps")
        return [dict(item) for item in self.blank_steps if item["blank_id"] == blank_id]

    # --- сотрудники (только чтение users) ---
    def user_card(self, sam: str) -> dict | None:
        self._count("user_card")
        return self.cards.get(sam)

    def is_manager(self, sam: str) -> bool:
        return False

    def manager_sam_by_dn(self, manager_dn: str) -> str | None:
        return None


class FakeAdReader:
    """Ридер AD в памяти: карточки по логину, почта — по правилу sam@domain."""

    def get_user(self, sam: str):
        login = (sam or "").strip()
        if not login:
            return None
        return SimpleNamespace(
            sam=login,
            display_name=ROSTER_FIOS.get(login, "Вымышленный Пользователь Тестовый"),
            title="Вымышленная должность",
            mail="%s@example.local" % login,
        )

    def group_members(self, group: str):
        return []


class MemSettingsStore:
    """Настройки в памяти; значения — строками в сид-формате (JSONB)."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_many(self, keys) -> dict:
        return {key: self.values.get(key) for key in keys}

    def set_many(self, values) -> None:
        self.values.update(values)


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _headers_for(sam: str, groups: list[str] | None = None) -> dict:
    """Заголовки мок-пользователя с вымышленными ПДн."""
    return {
        "X-Mock-Sam": sam,
        "X-Mock-Fio": _b64(ROSTER_FIOS.get(sam, "Вымышленный Пользователь Тестовый")),
        "X-Mock-Mail": _b64("%s@example.local" % sam),
        "X-Mock-Department": _b64("Вымышленный отдел"),
        "X-Mock-Title": _b64("Вымышленная должность"),
        "X-Mock-Groups": ",".join(groups or []),
    }


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
    """Настройки маршрута: срок шага 5 дней, шаблонов нет (маршрут из бланка)."""
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
    """Справочники маршрута, бланки и реестры через зависимость."""
    store = FakeRoutingStore()
    app.dependency_overrides[get_routing_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_routing_store, None)


@pytest.fixture
def ad_reader():
    """Ридер AD в памяти (ФИО/почта ответственных для уведомлений и печати)."""
    reader = FakeAdReader()
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


@pytest.fixture
def settings_store():
    """Настройки писем и наименования группы-владельца; ключа blank_autopick нет."""
    store = MemSettingsStore(
        {
            "allowed_ad_groups": json.dumps(
                [{"id": STEP_GROUP, "name": STEP_GROUP_NAME}], ensure_ascii=False
            ),
            "mail_templates": MAIL_TEMPLATES_SEED,
            "smtp_from": json.dumps("sed@example.local"),
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture
def mail_queue(tmp_path):
    """Файловая очередь писем — что ушло (или не ушло), видно в тесте."""
    queue = FileMailQueue(tmp_path / "mail_queue.json")
    app.dependency_overrides[get_mail_queue] = lambda: queue
    yield queue
    app.dependency_overrides.pop(get_mail_queue, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    """Чистое хранилище заявок и аудит на каждый тест."""
    requests_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    audit_log.clear_for_tests()


def _create(client, headers, **kw):
    """Создание заявки от ОК (вымышленные поля 1С) с выбранным бланком."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": FAKE_FIO,
        "tab_num": FAKE_TAB,
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION,
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
        "ad_sam": EMP_SAM,
    }
    body.update(kw)
    return client.post("/requests", json=body, headers=headers)


def _hr() -> dict:
    """Заголовки сотрудника ОК (создание заявки)."""
    return _headers_for("ok.vymyshlennaya", [TEST_HR])


def _member(sam: str) -> dict:
    """Заголовки ответственного шага-реестра (вход по группе-шагу)."""
    return _headers_for(sam, [ROSTER_GROUP])


def _outsider(sam: str) -> dict:
    """Заголовки постороннего: вход есть (группа-шаг), прав по шагу нет."""
    return _headers_for(sam, [OUTSIDER_GROUP])


def _ready_request(client, blank_id: int, headers: dict) -> str:
    """Заявка по бланку, поданная на согласование: возвращает её номер."""
    created = _create(client, headers, blank_id=blank_id)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=headers).status_code == 200
    return rid


def _decide(client, rid: str, order: int, headers: dict, **body):
    """Отметка по шагу (по умолчанию — согласие)."""
    payload = {"decision": "approve"}
    payload.update(body)
    return client.post(
        f"/requests/{rid}/steps/{order}/decision", json=payload, headers=headers
    )


def _sent(queue) -> list[tuple[str, str]]:
    """Письма очереди как пары (получатель, событие)."""
    return [(message.to, message.event) for message in queue.pending()]


def _actions() -> list[str]:
    """Действия журнала аудита по порядку."""
    return [event.action for event in audit_log.all()]


# ---------------------------------------------------------------------------
# Сборка шага: снимок ответственных и режим
# ---------------------------------------------------------------------------


def test_step_snapshot_has_all_active_roster_assignees(
    client, requests_store, routing_store, settings_override, route_override
):
    """Шаг-реестр: в снимок попадают все АКТИВНЫЕ участники, assignee — первый.

    Отключённый участник реестра в снимок не берётся: состав читается только
    активным (list_stage_assignees(active_only=True))."""
    created = _create(client, _hr(), blank_id=BLANK_SEQ_ID)
    assert created.status_code == 201, created.text
    step = created.json()["steps"][0]
    assert step["assignees"] == [sam for sam, _ in ROSTER]
    assert step["assignee"] == ROSTER[0][0]
    assert step["approval_mode"] == "sequential"
    assert step["assignee_count"] == 3 and step["approved_count"] == 0
    assert step["approvals"] == []
    assert step["can_act"] is False
    stored = requests_store.get(created.json()["id"]).steps[0]
    assert stored.assignees == [sam for sam, _ in ROSTER]
    assert stored.assignee == ROSTER[0][0]
    assert stored.approval_mode == "sequential"
    assert ROSTER_OFF_SAM not in stored.assignees


def test_manual_route_mode_comes_from_block(
    client, requests_store, settings_override, route_override
):
    """Ручной маршрут: режим шага снимается с блока — параллельный/последовательный.

    Персональный шаг блока хранит список из одного ответственного."""
    created = _create(
        client,
        _hr(),
        route_mode="custom",
        blocks=[
            {"mode": "parallel", "steps": [{"sam": "rukovoditel.vymyshlennyy"}]},
            {"mode": "sequential", "steps": [{"sam": "nachalnik.vymyshlennyy"}]},
        ],
    )
    assert created.status_code == 201, created.text
    steps = created.json()["steps"]
    assert steps[0]["approval_mode"] == "parallel"
    assert steps[0]["assignees"] == ["rukovoditel.vymyshlennyy"]
    assert steps[1]["approval_mode"] == "sequential"
    assert steps[1]["assignees"] == ["nachalnik.vymyshlennyy"]


def test_can_act_only_for_responsible_without_own_mark(
    client, requests_store, routing_store, settings_override, route_override
):
    """can_act: ответственный без своей отметки — True, после отметки — False."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    request = requests_store.get(rid)
    first, second = ROSTER[0][0], ROSTER[1][0]
    outsider = "postoronniy.vymyshlennyy"
    assert _public_view(request, CurrentUser(sam=first, groups=[], role="owner")).steps[0].can_act is True
    assert _public_view(request, CurrentUser(sam=outsider, groups=[], role="owner")).steps[0].can_act is False
    assert _decide(client, rid, 1, _member(first)).status_code == 200
    request = requests_store.get(rid)
    assert _public_view(request, CurrentUser(sam=first, groups=[], role="owner")).steps[0].can_act is False
    assert _public_view(request, CurrentUser(sam=second, groups=[], role="owner")).steps[0].can_act is True
    # Карточку видят все ответственные снимка (не только первый), посторонний — нет.
    assert client.get(f"/requests/{rid}", headers=_member(second)).status_code == 200
    assert client.get(f"/requests/{rid}", headers=_outsider(outsider)).status_code == 403


# ---------------------------------------------------------------------------
# Параллельный шаг: закрывает любой ответственный
# ---------------------------------------------------------------------------


def test_parallel_step_closes_by_any_responsible(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Режим parallel («кто-то один»): отметка второго закрывает шаг, заявка согласована.

    Отметки остаются в снимке (approved_count = 1 из 3); вторая отметка по
    закрытому шагу отклоняется с прежним 409."""
    rid = _ready_request(client, BLANK_PAR_ID, _hr())
    second = ROSTER[1][0]
    response = _decide(client, rid, 1, _member(second))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == AGREED
    assert body["steps"][0]["status"] == STEP_APPROVED
    assert body["steps"][0]["assignee_count"] == 3
    assert body["steps"][0]["approved_count"] == 1
    assert [item["sam"] for item in body["steps"][0]["approvals"]] == [second]
    closed = _decide(client, rid, 1, _member(ROSTER[2][0]))
    assert closed.status_code == 409
    # Заявка уже согласована — отметки по шагу больше нет (тексты прежние).
    assert closed.json()["detail"] == "Отметки — только в статусе На согласовании"


# ---------------------------------------------------------------------------
# Последовательный шаг: закрывают все ответственные
# ---------------------------------------------------------------------------


def test_sequential_step_waits_for_all_responsible(
    client, requests_store, routing_store, settings_override, route_override,
    mail_queue, settings_store, ad_reader
):
    """Режим sequential: две отметки из трёх не двигают заявку и не шлют писем.

    Шаг остаётся «ожидает», заявка — «На согласовании», прогресс виден
    (2 из 3), аудит частичных отметок — step.approve_partial. Третья отметка
    закрывает шаг, и тогда заявка согласована."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    # Письмо «назначена» при подаче заявки было каждому ответственному снимка
    # (адресаты по логинам из AD), а не только первому из них.
    sent_before = _sent(mail_queue)
    assert sent_before == [
        ("%s@example.local" % sam, EVENT_ASSIGNED) for sam, _ in ROSTER
    ]
    first, second, third = (sam for sam, _ in ROSTER)
    assert _decide(client, rid, 1, _member(first)).status_code == 200
    response = _decide(client, rid, 1, _member(second))
    assert response.status_code == 200, response.text
    body = response.json()
    step = body["steps"][0]
    assert body["status"] == IN_APPROVAL
    assert step["status"] == STEP_PENDING
    assert step["approval_mode"] == "sequential"
    assert step["approved_count"] == 2 and step["assignee_count"] == 3
    assert len(step["approvals"]) == 2
    # Чужие логины согласующих непривилегированному не видны (только своя отметка),
    # прогресс «2 из 3» при этом виден.
    assert [item["sam"] for item in step["approvals"]] == [None, second]
    assert all(item["decision"] == "approve" and item["at"] for item in step["approvals"])
    # ОК (привилегированный) видит все логины ответственных и весь снимок.
    privileged = client.get(f"/requests/{rid}", headers=_hr()).json()["steps"][0]
    assert privileged["assignees"] == [sam for sam, _ in ROSTER]
    assert [item["sam"] for item in privileged["approvals"]] == [first, second]
    # Частичные отметки не двигают заявку: новых писем «назначена» нет.
    assert _sent(mail_queue) == sent_before
    actions = _actions()
    assert actions.count("step.approve_partial") == 2
    assert "step.approve" not in actions
    assert not [action for action in actions if action.startswith("notify.")]
    # done_by/done_at шага не заполнены — шаг ещё не закрыт.
    stored = requests_store.get(rid).steps[0]
    assert stored.done_by is None and stored.done_at is None
    # Третий ответственный закрывает шаг — заявка согласована.
    final = _decide(client, rid, 1, _member(third))
    assert final.status_code == 200, final.text
    assert final.json()["status"] == AGREED
    assert final.json()["steps"][0]["status"] == STEP_APPROVED
    assert final.json()["steps"][0]["approved_count"] == 3


def test_repeat_approval_by_same_responsible_409(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Повторная отметка тем же sam — 409 «Вы уже согласовали этот шаг»."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    first, second = ROSTER[0][0], ROSTER[1][0]
    assert _decide(client, rid, 1, _member(first)).status_code == 200
    again = _decide(client, rid, 1, _member(first))
    assert again.status_code == 409
    assert again.json()["detail"] == "Вы уже согласовали этот шаг"
    # Чужой отметкой это не мешает: остальные ответственные решают как обычно.
    assert _decide(client, rid, 1, _member(second)).status_code == 200


def test_outsider_cannot_decide_multi_assignee_step(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Не ответственный (ни в снимке, ни в группе, ни в реестре) — 403."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    for outsider in ("postoronniy.vymyshlennyy", ROSTER_OFF_SAM):
        denied = _decide(client, rid, 1, _outsider(outsider))
        assert denied.status_code == 403, (outsider, denied.text)
        assert "другому исполнителю" in denied.json()["detail"]
    assert requests_store.get(rid).steps[0].approvals == []


def test_reject_by_one_responsible_applies_immediately(
    client, requests_store, routing_store, settings_override, route_override,
    mail_queue, settings_store, ad_reader
):
    """Отказ действует сразу от любого ответственного: согласия остальных не нужно."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    second = ROSTER[1][0]
    sent_before = _sent(mail_queue)
    response = _decide(
        client, rid, 1, _member(second),
        decision="reject", comment="Вымышленная причина отказа",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["steps"][0]["status"] == STEP_REJECTED
    assert body["steps"][0]["done_by"] is None  # логины согласующих — только привилегированным
    # Возвращать некуда (первый блок) — заявка автору на доработку.
    assert body["status"] == REWORK
    stored = requests_store.get(rid).steps[0]
    assert len(stored.approvals) == 1
    assert stored.approvals[0]["sam"] == second
    assert stored.approvals[0]["decision"] == "reject"
    assert stored.approvals[0]["comment"] == "Вымышленная причина отказа"
    assert stored.done_by == second
    # Отказ не согласие всех: уведомляется автор заявки, а не остальные реестровые.
    assert _sent(mail_queue) == sent_before + [
        ("ok.vymyshlennaya@example.local", EVENT_RETURNED)
    ]


def test_return_by_one_responsible_reopens_previous_block(
    client, requests_store, routing_store, settings_override, route_override,
    mail_queue, settings_store, ad_reader
):
    """Возврат от одного ответственного переоткрывает предыдущий блок.

    Прежняя логика переоткрытия сохранена: шаги предыдущего блока снова в работе
    с новым сроком, а их прежние отметки сброшены — новый круг начинается чисто."""
    created = _create(
        client,
        _hr(),
        blocks=[
            {"mode": "sequential", "steps": [{"sam": "rukovoditel.vymyshlennyy"}]},
            {"mode": "sequential", "steps": [{"sam": "nachalnik.vymyshlennyy"}]},
        ],
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=_hr()).status_code == 200
    assert _decide(client, rid, 1, _member("rukovoditel.vymyshlennyy")).status_code == 200
    response = _decide(
        client, rid, 1001, _member("nachalnik.vymyshlennyy"),
        decision="return", comment="Вымышленная причина возврата",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == IN_APPROVAL
    assert body["steps"][0]["status"] == STEP_PENDING
    assert body["steps"][0]["approvals"] == []
    assert body["steps"][1]["status"] == "возвращен"
    assert [item["decision"] for item in body["steps"][1]["approvals"]] == ["return"]


def test_reissue_clears_previous_marks(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Повтор просроченного шага (reissue) начинает новый круг: отметки сброшены."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    first, second = ROSTER[0][0], ROSTER[1][0]
    assert _decide(client, rid, 1, _member(first)).status_code == 200
    # Срок шага истёк — следующая отметка вернёт 410 и переведёт заявку в доработку.
    expired_request = requests_store.get(rid)
    expired_request.steps[0].expires_at = _utcnow() - timedelta(seconds=1)
    requests_store.update(expired_request)
    late = _decide(client, rid, 1, _member(second))
    assert late.status_code == 410
    expired = client.post(f"/requests/{rid}/steps/1/reissue", headers=_hr())
    assert expired.status_code == 200, expired.text
    body = expired.json()
    assert body["status"] == IN_APPROVAL
    assert body["steps"][0]["status"] == STEP_PENDING
    assert body["steps"][0]["approvals"] == []
    assert body["steps"][0]["approved_count"] == 0


# ---------------------------------------------------------------------------
# Печать: в колонке «Ответственный» ВСЕ ответственные шага (контекст и бланк)
# ---------------------------------------------------------------------------


def _blank_text(blob: bytes) -> str:
    """Текст собранного бланка (XML как есть — тест ищет вхождения)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _responsible_cell(blob: bytes) -> str:
    """Колонка «Ответственный» таблицы шагов готового бланка (docx нужен на стенде)."""
    pytest.importorskip("docx")
    from docx import Document

    table = Document(io.BytesIO(blob)).tables[-1]
    return "\n".join(row.cells[2].text for row in table.rows[1:])


def _print_context(requests_store, routing_store, rid: str) -> dict:
    """Контекст печати той же заявки, который собирает documents.print_bypass."""
    context = build_bypass_context(requests_store.get(rid))
    _fill_step_fio(context, routing_store)
    return context


def test_bypass_context_has_all_step_assignees(
    client, requests_store, routing_store, settings_override, route_override
):
    """Контекст бланка несёт снимок ответственных и ФИО по всем логинам.

    assignee_names — по строке на ответственного (в порядке снимка), fio —
    ФИО первого (прежнее поле контекста)."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    step = _print_context(requests_store, routing_store, rid)["steps"][0]
    assert step["assignees"] == [sam for sam, _ in ROSTER]
    assert step["assignee"] == ROSTER[0][0]
    assert step["assignee_names"] == [fio for _sam, fio in ROSTER]
    assert step["fio"] == ROSTER[0][1]
    # Отключённый участник реестра в печать не попадает (снимок без него).
    assert ROSTER_OFF_SAM not in step["assignees"]


def test_bypass_context_keeps_legacy_single_assignee(
    client, requests_store, routing_store, settings_override, route_override
):
    """Заявка до миграции (снимка assignees нет): печатается прежний assignee."""
    request = _snapshot_request()
    request.steps[0].assignees = []
    requests_store.create(request)
    step = _print_context(requests_store, routing_store, request.id)["steps"][0]
    assert step["assignees"] == [request.steps[0].assignee]
    assert step["assignee_names"] == [ROSTER_FIOS[request.steps[0].assignee]]
    assert step["fio"] == ROSTER_FIOS[request.steps[0].assignee]


def test_bypass_context_group_step_has_no_assignee_names(
    client, requests_store, routing_store, settings_override, route_override
):
    """Групповой шаг: ФИО не подставляем — группу AD на бланке пишут от руки,
    зеркало users ради него не читается."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    context = build_bypass_context(requests_store.get(rid))
    context["steps"] = [
        dict(context["steps"][0], assignees=[], assignee="", owner_group=STEP_GROUP)
    ]
    routing_store.calls.clear()
    _fill_step_fio(context, routing_store)
    assert context["steps"][0]["assignee_names"] == []
    assert context["steps"][0]["fio"] == ""
    assert "user_card" not in routing_store.calls


def test_print_lists_all_step_assignees(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Бланк печатает всех ответственных шага-реестра (ФИО из AD по логинам).

    В колонке «Ответственный» — по строке на ответственного, в порядке снимка."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    context = _print_context(requests_store, routing_store, rid)
    blob = build_blank_document(dict(context, qr_url=""))
    for _sam, fio in ROSTER:
        assert fio in _blank_text(blob), fio
    assert _responsible_cell(blob).splitlines() == [fio for _sam, fio in ROSTER]


def test_print_single_assignee_cell_has_one_name(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Один ответственный — одна строка в колонке (без дублей и пустых строк)."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    context = _print_context(requests_store, routing_store, rid)
    context["steps"][0].update(
        assignees=[ROSTER[0][0]],
        assignee=ROSTER[0][0],
        assignee_names=[ROSTER[0][1]],
        fio=ROSTER[0][1],
    )
    blob = build_blank_document(dict(context, qr_url=""))
    assert _responsible_cell(blob).splitlines() == [ROSTER[0][1]]


def test_print_group_step_shows_group_name(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Шаг без персональных исполнителей печатает владельца-группу как есть."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    context = _print_context(requests_store, routing_store, rid)
    context["steps"][0].update(
        assignees=[], assignee="", assignee_names=[], fio="", owner=STEP_GROUP
    )
    blob = build_blank_document(dict(context, qr_url=""))
    assert _responsible_cell(blob).splitlines() == [STEP_GROUP]


def test_print_context_reads_user_card_once_per_login(
    client, requests_store, routing_store, settings_override, route_override, ad_reader
):
    """Один логин на нескольких шагах — карточка users читается один раз."""
    rid = _ready_request(client, BLANK_SEQ_ID, _hr())
    context = _print_context(requests_store, routing_store, rid)
    first = dict(context["steps"][0])
    context["steps"] = [dict(first), dict(first, order=2), dict(first, order=3)]
    routing_store.calls.clear()
    _fill_step_fio(context, routing_store)
    assert routing_store.calls["user_card"] == len(ROSTER)


# ---------------------------------------------------------------------------
# Персистентность снимка: DbRequestsStore (миграция 0013)
# ---------------------------------------------------------------------------


class FakeRow:
    """Строка ответа сессии: доступ по имени, по номеру и через _mapping."""

    def __init__(self, values: dict) -> None:
        self._values = dict(values)
        self._mapping = dict(self._values)
        for key, value in self._values.items():
            setattr(self, key, value)

    def __getitem__(self, index):
        if isinstance(index, int):
            return list(self._values.values())[index]
        return self._values[index]


class FakeResult:
    """Ответ сессии-мока: заранее заданные строки (dict -> FakeRow)."""

    def __init__(self, rows=None) -> None:
        self._rows = rows

    def _as_rows(self) -> list:
        if self._rows is None:
            return []
        if isinstance(self._rows, list):
            return self._rows
        return [self._rows]

    def all(self) -> list:
        return [FakeRow(row) if isinstance(row, dict) else row for row in self._as_rows()]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None

    def scalar(self):
        row = self.first()
        return None if row is None else row[0]


class FakeSession:
    """Сессия-мок: ответы по порядку, SQL/параметры/commit запоминаются."""

    def __init__(self, results=None) -> None:
        self._results = list(results or [])
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        if not self._results:
            return FakeResult(None)
        return FakeResult(self._results.pop(0))

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


def _db_store(session: FakeSession) -> DbRequestsStore:
    """DbRequestsStore с подменённой сессией (движок не создаётся)."""
    store = object.__new__(DbRequestsStore)
    store._engine = None
    store._session_factory = lambda: session
    return store


def _snapshot_step() -> _Step:
    """Шаг со снимком нескольких ответственных и собранной отметкой."""
    return _Step(
        order=1,
        owner_group="roster_seq",
        assignee=ROSTER[0][0],
        status=STEP_PENDING,
        expires_at=_utcnow() + timedelta(days=5),
        owner_kind="stage_roster",
        stage_id=STAGE_SEQ,
        stage_code="roster_seq",
        stage_title="Реестр вымышленный (последовательный)",
        stage_lines=["оформить последовательный этап"],
        assignees=[sam for sam, _ in ROSTER],
        approval_mode="sequential",
        approvals=[
            {
                "sam": ROSTER[0][0],
                "at": "2026-10-06T09:00:00+00:00",
                "decision": "approve",
                "comment": None,
            }
        ],
    )


def _snapshot_request() -> _Request:
    """Заявка с одним шагом-реестром (вымышленные ПДн)."""
    return _Request(
        id="REQ-0001",
        status=IN_APPROVAL,
        route_origin="template",
        enterprise=FAKE_ENTERPRISE,
        fio=FAKE_FIO,
        tab_num=FAKE_TAB,
        department=FAKE_SERVICE,
        position=FAKE_POSITION,
        subject="Вымышленная тема",
        content="Вымышленное содержание",
        created_by="ok.vymyshlennaya",
        blank_id=BLANK_SEQ_ID,
        blank_name=BLANK_SEQ_ROW["name"],
        blank_version=BLANK_SEQ_ROW["version"],
        blank_layout="office",
        steps=[_snapshot_step()],
    )


REQUEST_ROW = {
    "id": 7,
    "code": "REQ-0001",
    "enterprise": FAKE_ENTERPRISE,
    "tab_num": FAKE_TAB,
    "initiated_by_hr": "ok.vymyshlennaya",
    "route_origin": "manual",
    "status": "in_approval",
    "fio": FAKE_FIO,
    "department": FAKE_SERVICE,
    "position": FAKE_POSITION,
    "category": None,
    "escalation_hours": None,
    "subject": "Вымышленная тема",
    "content": "Вымышленное содержание",
    "doc_type_code": None,
    "profile_id": None,
    "service_id": None,
    "service_name": None,
}
STEP_ROW = {
    "request_id": 7,
    "step_order": 1,
    "owner_group": "roster_seq",
    "done_by": None,
    "status": "pending",
    "resolver": "by_group",
    "assignee": ROSTER[0][0],
    "require_comment": False,
    "done_at": None,
    "expires_at": datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc),
    "comment": None,
    "stage_id": STAGE_SEQ,
    "stage_code": "roster_seq",
    "stage_title": "Реестр вымышленный (последовательный)",
    "stage_lines": ["оформить последовательный этап"],
    "profile_step_id": None,
    "assignees": [sam for sam, _ in ROSTER],
    "approval_mode": "sequential",
    "approvals": [
        {
            "sam": ROSTER[0][0],
            "at": "2026-10-06T09:00:00+00:00",
            "decision": "approve",
            "comment": None,
        }
    ],
}


def test_db_store_writes_step_snapshot_on_create():
    """INSERT шага пишет assignees/approval_mode/approvals (jsonb — строкой)."""
    session = FakeSession([FakeRow({"id": 7})])
    _db_store(session).create(_snapshot_request())
    inserts = [params for sql, params in session.calls if "INSERT INTO request_steps" in sql]
    assert len(inserts) == 1
    params = inserts[0]
    assert json.loads(params["assignees"]) == [sam for sam, _ in ROSTER]
    assert params["approval_mode"] == "sequential"
    assert json.loads(params["approvals"]) == [
        {
            "sam": ROSTER[0][0],
            "at": "2026-10-06T09:00:00+00:00",
            "decision": "approve",
            "comment": None,
        }
    ]
    assert session.commits == 1


def test_db_store_reads_step_snapshot_back():
    """Чтение заявки восстанавливает снимок ответственных, режим и отметки."""
    session = FakeSession([dict(REQUEST_ROW), [dict(STEP_ROW)]])
    request = _db_store(session).get("REQ-0001")
    assert request is not None
    step = request.steps[0]
    assert step.assignees == [sam for sam, _ in ROSTER]
    assert step.assignee == ROSTER[0][0]
    assert step.approval_mode == "sequential"
    assert step.approvals == [
        {
            "sam": ROSTER[0][0],
            "at": "2026-10-06T09:00:00+00:00",
            "decision": "approve",
            "comment": None,
        }
    ]
    assert session.commits == 0


def test_db_store_survives_jsonb_garbage_in_snapshot():
    """Мусор в jsonb не роняет выдачу: снимок пустой, права прежние (assignee)."""
    broken = dict(STEP_ROW, assignees="{битый", approvals="не список")
    session = FakeSession([dict(REQUEST_ROW), [broken]])
    step = _db_store(session).get("REQ-0001").steps[0]
    assert step.assignees == [] and step.approvals == []
    assert step.assignee == ROSTER[0][0] and step.approval_mode == "sequential"


def test_offline_view_of_request_without_snapshot_keeps_previous_rules(
    client, requests_store
):
    """Заявка, выданная до миграции (пустой снимок): прежние права по assignee.

    Пустой assignees = прежние правила (assignee/группа/реестр), пустой approvals —
    шаг ждёт отметок как новый, can_act у персонального исполнителя True."""
    request = _Request(
        id="REQ-0001",
        status=IN_APPROVAL,
        route_origin="custom",
        enterprise=FAKE_ENTERPRISE,
        fio=FAKE_FIO,
        tab_num=FAKE_TAB,
        department=FAKE_SERVICE,
        position=FAKE_POSITION,
        subject="Вымышленная тема",
        content="Вымышленное содержание",
        created_by="ok.vymyshlennaya",
        steps=[
            _Step(
                order=1,
                owner_group=STEP_GROUP,
                assignee=ROSTER[0][0],
                status=STEP_PENDING,
                expires_at=_utcnow() + timedelta(days=5),
            )
        ],
    )
    requests_store.create(request)
    view = _public_view(requests_store.get("REQ-0001"), CurrentUser(sam=ROSTER[0][0], groups=[], role="owner"))
    assert view.steps[0].can_act is True
    assert view.steps[0].assignees == []
    assert view.steps[0].approval_mode is None
    assert view.steps[0].approved_count == 0 and view.steps[0].assignee_count == 0

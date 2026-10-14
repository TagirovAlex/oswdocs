# Замещение в отметках по шагам (решение человека 2026-10-14): шаг остаётся
# назначенным оригиналу, но действующий заместитель может поставить согласование
# или отказ ЗА него — если у него есть блок прав «Отметки по шагам» (step_decide).
# Отметка пишется от логина заместителя (в approvals и аудите — его ФИО/логин).
#
# Проверяем контракт:
#   • заместитель видит карточку (can_act=True) и его отметка принимается;
#   • само замещение права на отметку НЕ даёт: без блока step_decide — 403;
#   • срок записи значения не имеет, пока он действует: истёкшая и ещё не
#     начавшаяся записи не действуют, valid_to = NULL действует всегда
#     («начальная дата без конечной — действует, пока не отменят»);
#   • замещение персональное: групповой шаг и состав этапа (stage_roster) им не
#     закрываются;
#   • посторонний (не заместитель) по-прежнему получает 403;
#   • копия письма «назначена» уходит заместителю при notify=true и не уходит
#     при notify=false.
#
# Офлайн-прогон без Postgres: справочник заместителей — InMemorySubstituteStore
# (conftest, OFFLINE_BOUNDARIES), заявки и справочники маршрута — in-memory
# заглушки (как в test_step_multi_assignee.py), письма — файловая очередь.
# Все логины, группы и ФИО вымышленные.

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.mailer import EVENT_ASSIGNED, FileMailQueue, get_mail_queue  # noqa: E402
from app.permissions import (  # noqa: E402
    InMemoryPermissionsStore,
    PermissionBlock,
    PermissionCatalog,
    PermissionTemplate,
    get_permissions_store,
)
from app.requests import (  # noqa: E402
    AGREED,
    RouteSettings,
    _public_view,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests_store import get_requests_store  # noqa: E402
from app.routing_store import get_routing_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402
from app.substitute_store import InMemorySubstituteStore, get_substitute_store  # noqa: E402
from app.timezone import current_timezone  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные участники: оригинал (персональный шаг), его заместитель и посторонний.
HR_SAM = "ok.vymyshlennaya"
PRINCIPAL = "sotrudnik.osnovnoy"
DEPUTY = "zameshchitel.vymyshlennyy"
OUTSIDER = "postoronniy.vymyshlennyy"
# Второй ответственный шага режима sequential (сценарии частичного согласия).
SECOND = "vtoroy.otvetstvennyy"
# Логин увольняемого из тела заявки: по нему подбор маршрута auto ищет карточку
# сотрудника в справочнике (без неё маршрут не соберётся).
EMPLOYEE = "uvolnyaemyy.vymyshlennyy"
# Группы шагов: у каждого своя (вход по префиксу SED_STEP_), поэтому владение
# шагом у заместителя и постороннего своего нет.
PRINCIPAL_GROUP = "SED_STEP_OSNOVNOY"
DEPUTY_GROUP = "SED_STEP_ZAMESTVITEL"
OUTSIDER_GROUP = "SED_STEP_PROCHOY"
SECOND_GROUP = "SED_STEP_VTOROY"

FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Вымышленная должность"
FAKE_TAB = "В-0001"
FAKE_FIO = "Вымышленный Увольняемый Полный"

MAIL_TEMPLATES_SEED = json.dumps(
    [
        {
            "code": EVENT_ASSIGNED,
            "subject": "Назначено {{ request_id }}",
            "body_html": "<html>{{ fio }} {{ url }}</html>",
        }
    ],
    ensure_ascii=False,
)

# --- Вымышленный справочник маршрута: служба, профиль, этап-реестр ---
SERVICE_ID = 1
PROFILE_ID = 10
STAGE_ROSTER = 77
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
STAGE_ROSTER_ROW = {
    "id": STAGE_ROSTER,
    "code": "roster_vymyshlennyy",
    "title": "Реестр вымышленный",
    "stage_lines": ["оформить прекращение"],
    "owner_kind": "stage_roster",
    "owner_group": None,
    "optional": False,
    "print_assignee": False,
    "require_comment": False,
    "active": True,
}


def _profile_step(stage_row: dict) -> dict:
    """Шаг профиля вместе с этапом-реестром (формат строки list_profile_steps)."""
    return {
        "profile_step_id": 300 + stage_row["id"],
        "profile_id": PROFILE_ID,
        "step_order": 1,
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


def _today() -> date:
    """Сегодня в часовой зоне системы — так же считает резолвер заместителей."""
    return datetime.now(current_timezone()).date()


class FakeRoutingStore:
    """Справочники маршрута в памяти: служба, профиль, состав этапа-реестра."""

    def __init__(self) -> None:
        self.services = [dict(SERVICE_ROW)]
        self.profiles = [dict(PROFILE_ROW)]
        self.profile_steps: list[dict] = []
        self.rosters: dict[int, list[dict]] = {}
        # Карточка увольняемого в зеркале users: без неё подбор маршрута auto
        # отвечает 422 (связь 1С↔AD не оформлена).
        self.cards: dict[str, dict] = {
            EMPLOYEE: {
                "sam": EMPLOYEE,
                "fio_full": FAKE_FIO,
                "dept_ad": FAKE_SERVICE,
                "title_ad": FAKE_POSITION,
                "manager_dn": None,
            }
        }
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def list_services(self, active_only: bool = False) -> list[dict]:
        self._count("list_services")
        return [dict(item) for item in self.services]

    def list_profiles(self, active_only: bool = False) -> list[dict]:
        self._count("list_profiles")
        return [dict(item) for item in self.profiles]

    def list_stages(self, active_only: bool = False) -> list[dict]:
        self._count("list_stages")
        return [dict(STAGE_ROSTER_ROW)]

    def list_profile_steps(self, profile_id: int) -> list[dict]:
        self._count("list_profile_steps")
        return [dict(item) for item in self.profile_steps if item["profile_id"] == profile_id]

    def list_all_profile_steps(self) -> list[dict]:
        self._count("list_all_profile_steps")
        return []

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        self._count("list_stage_assignees")
        return [dict(item) for item in self.rosters.get(stage_id, []) if item.get("active", True)]

    def user_card(self, sam: str) -> dict | None:
        self._count("user_card")
        return self.cards.get(sam)

    def is_manager(self, sam: str) -> bool:
        return False

    def manager_sam_by_dn(self, manager_dn: str) -> str | None:
        return None


class FakeAdReader:
    """Ридер AD в памяти: карточка по логину (только чтение), почта — по правилу."""

    def get_user(self, sam: str):
        login = (sam or "").strip()
        if not login:
            return None
        return SimpleNamespace(
            sam=login,
            display_name="Вымышленный Пользователь Тестовый",
            title="Вымышленная должность",
            mail="%s@example.local" % login,
        )

    def group_members(self, group: str):
        return []


class BrokenSubstituteStore:
    """Справочник заместителей, который падает на чтении (ошибка БД)."""

    def list_active(self, on):
        raise RuntimeError("вымышленная недоступность справочника заместителей")

    def list_all(self):
        raise RuntimeError("вымышленная недоступность справочника заместителей")


class MemSettingsStore:
    """Настройки в памяти; значения — строками в сид-формате (JSONB)."""

    def __init__(self, values: dict | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_many(self, keys) -> dict:
        return {key: self.values.get(key) for key in keys}

    def set_many(self, values) -> None:
        self.values.update(values)


def _b64(value: str) -> str:
    """Кодирование кириллицы для мок-заголовков (как в conftest)."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _headers_for(sam: str, groups: list[str]) -> dict:
    """Заголовки мок-пользователя с вымышленными ПДн."""
    return {
        "X-Mock-Sam": sam,
        "X-Mock-Fio": _b64("Вымышленный Пользователь Тестовый"),
        "X-Mock-Mail": _b64("%s@example.local" % sam),
        "X-Mock-Department": _b64(FAKE_SERVICE),
        "X-Mock-Title": _b64(FAKE_POSITION),
        "X-Mock-Groups": ",".join(groups),
    }


@pytest.fixture(autouse=True)
def settings_override():
    """Группы ролей тестовыми (дефолты кода нейтральные).

    Автоприёмка: без них вход по мок-заголовкам откажет (группы ролей пустые),
    а в шапке роутов набор блоков прав был бы другим."""
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
    """Настройки маршрута: срок шага 5 дней, шаблонов службы нет (ручной путь)."""
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
    """Справочники маршрута через зависимость (состав этапа-реестра задаёт тест)."""
    store = FakeRoutingStore()
    app.dependency_overrides[get_routing_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_routing_store, None)


@pytest.fixture
def ad_reader():
    """Ридер AD в памяти (ФИО/почта ответственных для карточки и писем)."""
    reader = FakeAdReader()
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


@pytest.fixture
def settings_store():
    """Настройки писем и наименования группы-владельца шага."""
    store = MemSettingsStore(
        {
            "allowed_ad_groups": json.dumps(
                [{"id": PRINCIPAL_GROUP, "name": "Согласующие вымышленной службы"}],
                ensure_ascii=False,
            ),
            "mail_templates": MAIL_TEMPLATES_SEED,
            "smtp_from": json.dumps("sed@example.local"),
            # Запасной подбор маршрута по службе нужен сценарию этапа-реестра
            # (owner_kind = stage_roster приходит из справочника этапов профиля),
            # в тестах маршрут строится вручную и настройку не задевает.
            "blank_autopick": json.dumps("on"),
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


@pytest.fixture
def substitutes():
    """Справочник заместителей через зависимость (чистое состояние на тест)."""
    store = InMemorySubstituteStore()
    app.dependency_overrides[get_substitute_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_substitute_store, None)


@pytest.fixture
def perms_without_decide():
    """Справочник прав, где у владельца шага НЕТ блока step_decide.

    Замещение даёт право отметить шаг за оригинала, но не блок функций: без
    блока «Отметки по шагам» отметка отклоняется гейтом (решение человека).
    Состав блоков у ОК прежний — заявку в тесте по-прежнему создать можно."""
    blocks = ("requests_list", "request_card", "request_create", "employee_directory")
    catalog = PermissionCatalog(
        # Кортеж, а не генератор: справочник читается из резолвера прав на каждый
        # запрос, а генератор после первого прохода был бы исчерпан — и гейт
        # отдавал бы 403 даже там, где блок выдан.
        blocks=tuple(
            PermissionBlock(code=code, title=code, sort_order=10 * (index + 1))
            for index, code in enumerate((*blocks, "step_decide"))
        ),
        templates=(
            PermissionTemplate(
                code="hr",
                title="Сотрудник ОК",
                sort_order=40,
                blocks=blocks,
                groups=(TEST_HR,),
            ),
            PermissionTemplate(
                code="owner",
                title="Владелец шага",
                sort_order=50,
                blocks=("requests_list", "request_card"),
            ),
        ),
    )
    store = InMemoryPermissionsStore(catalog)
    app.dependency_overrides[get_permissions_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_permissions_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    """Чистое хранилище заявок и аудит на каждый тест."""
    requests_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    audit_log.clear_for_tests()


def _hr() -> dict:
    """Заголовки сотрудника ОК (создание заявки)."""
    return _headers_for(HR_SAM, [TEST_HR])


def _principal() -> dict:
    """Заголовки оригинала: шаг назначен ему лично."""
    return _headers_for(PRINCIPAL, [PRINCIPAL_GROUP])


def _deputy() -> dict:
    """Заголовки заместителя: в группу-владельца шага он не входит."""
    return _headers_for(DEPUTY, [DEPUTY_GROUP])


def _outsider() -> dict:
    """Заголовки постороннего сотрудника (в своей группе, не заместитель)."""
    return _headers_for(OUTSIDER, [OUTSIDER_GROUP])


def _add_substitute(
    substitutes,
    *,
    principal: str = PRINCIPAL,
    deputy: str = DEPUTY,
    valid_from: date | None = None,
    valid_to: date | None = None,
    notify: bool = True,
) -> None:
    """Завести запись замещения прямо в справочнике (как это сделал бы админ).

    Тесты резолвера не должны зависеть от гейта настроек: проверяется разбор
    записи (срок, флаг писем), а не CRUD — он в test_substitutes.py."""
    substitutes.create(
        principal_sam=principal,
        deputy_sam=deputy,
        valid_from=valid_from if valid_from is not None else _today(),
        valid_to=valid_to,
        notify=notify,
        note="Вымышленная причина замещения",
        actor="adm.petrov",
    )


def _body(**kw) -> dict:
    """Тело создания заявки с вымышленными полями 1С."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": FAKE_FIO,
        "tab_num": FAKE_TAB,
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION,
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
    }
    body.update(kw)
    return body


def _personal_request(client) -> str:
    """Заявка с персональным шагом, поданная на согласование: id.

    Шаг назначен одному сотруднику (assignee/assignees = логин оригинала) —
    единственный случай, который замещение закрывает."""
    created = client.post(
        "/requests",
        json=_body(blocks=[{"mode": "sequential", "steps": [{"sam": PRINCIPAL}]}]),
        headers=_hr(),
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=_hr()).status_code == 200
    return rid


def _roster_request(client) -> str:
    """Заявка по профилю службы с единственным этапом-составом (stage_roster)."""
    created = client.post(
        "/requests",
        json=_body(ad_sam=EMPLOYEE),
        headers=_hr(),
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=_hr()).status_code == 200
    return rid


def _two_responsible_request(client, requests_store) -> str:
    """Заявка с шагом режима sequential на двоих ответственных (оригинал + второй).

    Несколько ответственных — из снимка шага (миграция 0013): ручной маршрут
    (`blocks/steps`) назначает шагу одного персонального исполнителя, поэтому
    список ответственных дописывается в снимок напрямую — так же он выглядит
    после выдачи заявки по бланку с двумя согласующими."""
    rid = _personal_request(client)
    request = requests_store.get(rid)
    request.steps[0].assignees = [PRINCIPAL, SECOND]
    request.steps[0].approval_mode = "sequential"
    requests_store.update(request)
    return rid


def _group_step_request(client) -> str:
    """Заявка с групповым шагом (владелец-группа, персональных ответственных нет)."""
    created = client.post(
        "/requests",
        json=_body(steps=[{"owner_group": PRINCIPAL_GROUP}]),
        headers=_hr(),
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=_hr()).status_code == 200
    return rid


def _decide(client, rid: str, headers: dict, **body):
    """Отметка по первому шагу заявки (по умолчанию — согласие)."""
    payload = {"decision": "approve"}
    payload.update(body)
    return client.post(f"/requests/{rid}/steps/1/decision", json=payload, headers=headers)


def _can_act(requests_store, rid: str, sam: str, groups: list[str]) -> bool:
    """can_act по карточке, собранной для пользователя (без обращения к API)."""
    user = CurrentUser(sam=sam, groups=list(groups), role="owner")
    view = _public_view(requests_store.get(rid), user)
    return bool(view.steps[0].can_act)


def _sent(queue) -> list[tuple[str, str]]:
    """Письма очереди как пары (получатель, событие)."""
    return [(message.to, message.event) for message in queue.pending()]


# ---------------------------------------------------------------------------
# Заместитель с блоком step_decide отмечает шаг за оригинала
# ---------------------------------------------------------------------------


def test_deputy_with_decide_block_approves_step_of_principal(
    client, requests_store, substitutes
):
    """Действующий заместитель видит карточку (can_act=True) и его отметка идёт.

    Шаг остаётся назначенным оригиналу, а в approvals пишется логин ЗАМЕСТИТЕЛЯ."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    assert _can_act(requests_store, rid, DEPUTY, [DEPUTY_GROUP]) is True
    card = client.get(f"/requests/{rid}", headers=_deputy())
    assert card.status_code == 200, card.text
    assert card.json()["steps"][0]["can_act"] is True
    response = _decide(client, rid, _deputy())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == AGREED
    assert body["steps"][0]["status"] == "согласован"
    # Отметка в снимке — от логина заместителя (решение человека: его ФИО).
    assert [item["sam"] for item in body["steps"][0]["approvals"]] == [DEPUTY]
    stored = requests_store.get(rid)
    assert stored.steps[0].done_by == DEPUTY
    assert stored.steps[0].approvals[0]["sam"] == DEPUTY


def test_deputy_may_reject_step_of_principal(client, requests_store, substitutes):
    """Заместитель может и отказать за оригинала (решение человека — оба решения)."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    response = _decide(
        client, rid, _deputy(), decision="reject", comment="Вымышленная причина отказа"
    )
    assert response.status_code == 200, response.text
    stored = requests_store.get(rid)
    assert stored.steps[0].approvals[0]["sam"] == DEPUTY
    assert stored.steps[0].approvals[0]["decision"] == "reject"


def test_deputy_decide_audited_by_own_login(client, substitutes):
    """В аудите отметки — логин заместителя: решение принял он, а не оригинал."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    assert _decide(client, rid, _deputy()).status_code == 200
    approvals = [event for event in audit_log.all() if event.action == "step.approve"]
    assert approvals, [event.action for event in audit_log.all()]
    assert approvals[-1].actor == DEPUTY


def test_deputy_sees_request_in_list(client, substitutes):
    """Заместитель — участник заявки, поэтому заявка видна и в списке."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    listing = client.get("/requests", headers=_deputy())
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()] == [rid]


def test_deputy_without_decide_block_403(
    client, requests_store, substitutes, perms_without_decide
):
    """Само замещение права на отметку не даёт: без блока step_decide — 403.

    Гейт по блоку проверяется раньше владения шагом, поэтому запись замещения
    его не обходит."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    denied = _decide(client, rid, _deputy())
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"]
    assert requests_store.get(rid).steps[0].approvals == []


# ---------------------------------------------------------------------------
# Срок записи: действует только в интервале, valid_to = NULL — всегда
# ---------------------------------------------------------------------------


def test_expired_substitute_does_not_cover_step(client, requests_store, substitutes):
    """Запись с прошедшим valid_to не действует: отметка запрещена (403)."""
    _add_substitute(
        substitutes,
        valid_from=_today() - timedelta(days=30),
        valid_to=_today() - timedelta(days=1),
    )
    rid = _personal_request(client)
    assert _decide(client, rid, _deputy()).status_code == 403
    assert _can_act(requests_store, rid, DEPUTY, [DEPUTY_GROUP]) is False


def test_not_yet_started_substitute_does_not_cover_step(
    client, requests_store, substitutes
):
    """Запись с будущим valid_from ещё не действует (403)."""
    _add_substitute(
        substitutes,
        valid_from=_today() + timedelta(days=1),
        valid_to=_today() + timedelta(days=30),
    )
    rid = _personal_request(client)
    assert _decide(client, rid, _deputy()).status_code == 403
    assert _can_act(requests_store, rid, DEPUTY, [DEPUTY_GROUP]) is False


def test_open_ended_substitute_covers_step(client, requests_store, substitutes):
    """valid_to = NULL — «действует, пока не отменят» (ключевое требование).

    Дата начала в далёком прошлом, конечной нет: замещение работает."""
    _add_substitute(substitutes, valid_from=_today() - timedelta(days=365), valid_to=None)
    rid = _personal_request(client)
    assert _can_act(requests_store, rid, DEPUTY, [DEPUTY_GROUP]) is True
    assert _decide(client, rid, _deputy()).status_code == 200


def test_substitute_valid_today_covers_step(client, requests_store, substitutes):
    """Границы интервала включающие: сегодняшние valid_from и valid_to действуют."""
    today = _today()
    _add_substitute(substitutes, valid_from=today, valid_to=today)
    rid = _personal_request(client)
    assert _decide(client, rid, _deputy()).status_code == 200


def test_substitute_store_down_is_fail_soft(client, requests_store, substitutes):
    """Справочник заместителей недоступен — карточка и решение не падают (503'а нет).

    Отметка при этом отклоняется как чужая (fail-soft резолвера, как у состава
    этапа): карточка закрыта, отметка — 403, а не 500."""
    app.dependency_overrides[get_substitute_store] = lambda: BrokenSubstituteStore()
    try:
        rid = _personal_request(client)
        assert client.get(f"/requests/{rid}", headers=_deputy()).status_code == 403
        assert _decide(client, rid, _deputy()).status_code == 403
    finally:
        app.dependency_overrides.pop(get_substitute_store, None)


# ---------------------------------------------------------------------------
# Замещение персональное: группа и состав этапа им не закрываются
# ---------------------------------------------------------------------------


def test_group_step_is_not_covered_by_substitute(client, requests_store, substitutes):
    """Групповой шаг (owner_group) персональным не является: замещение не даёт
    права отметить его, даже если заместитель замещает участника этой группы."""
    _add_substitute(substitutes)
    rid = _group_step_request(client)
    assert _decide(client, rid, _deputy()).status_code == 403
    assert requests_store.get(rid).steps[0].approvals == []
    # Участник группы-владельца (оригинал записи замещения) отмечает как обычно.
    assert _decide(client, rid, _principal()).status_code == 200


def test_stage_roster_step_is_covered_by_snapshot_substitute(
    client, requests_store, routing_store, substitutes, settings_store
):
    """Шаг состава этапа (owner_kind = stage_roster) замещением закрывается.

    Состав реестра кладётся в снимок ответственных шага (assignees), поэтому
    участник реестра — такой же персональный ответственный, как и в шаге
    «люди»: его заместитель отмечает шаг за него. Не закрывает замещение только
    ГРУППОВОЙ шаг — там ответственных нет вовсе (см. тест выше)."""
    routing_store.rosters = {
        STAGE_ROSTER: [{"sam": PRINCIPAL, "position_title": None, "active": True}]
    }
    routing_store.profile_steps = [_profile_step(STAGE_ROSTER_ROW)]
    _add_substitute(substitutes)
    rid = _roster_request(client)
    assert requests_store.get(rid).steps[0].owner_kind == "stage_roster"
    # Отметка заместителя идёт от его логина и закрывает шаг участнику реестра.
    assert _decide(client, rid, _deputy()).status_code == 200
    approvals = requests_store.get(rid).steps[0].approvals
    assert [item["sam"] for item in approvals] == [DEPUTY]
    # Повторная отметка заместителем — 409 (уже согласовал).
    assert _decide(client, rid, _deputy(), decision="approve").status_code == 409


# ---------------------------------------------------------------------------
# Посторонний: замещение чужих логинов прав не даёт
# ---------------------------------------------------------------------------


def test_outsider_still_gets_403(client, requests_store, substitutes):
    """Сотрудник без записи замещения отметку не поставит — 403, как и раньше."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    denied = _decide(client, rid, _outsider())
    assert denied.status_code == 403, denied.text
    assert requests_store.get(rid).steps[0].approvals == []
    assert _can_act(requests_store, rid, OUTSIDER, [OUTSIDER_GROUP]) is False


def test_substitute_of_other_person_gives_nothing(client, requests_store, substitutes):
    """Запись за другого сотрудника прав на этот шаг не даёт."""
    _add_substitute(substitutes, principal=OUTSIDER, deputy=DEPUTY)
    rid = _personal_request(client)
    assert _decide(client, rid, _deputy()).status_code == 403


def test_substitution_is_not_transitive(client, requests_store, substitutes):
    """Замещение не выстраивается в цепочку: при записях A→B и B→C заместитель C
    действует за B, но НЕ за A.

    Цепочка записей выдала бы право отмечать чужие шаги через произвольную длину —
    этого в справочнике быть не должно."""
    _add_substitute(substitutes, principal=PRINCIPAL, deputy=DEPUTY)
    _add_substitute(substitutes, principal=DEPUTY, deputy=OUTSIDER)
    rid = _personal_request(client)
    # Шаг назначен PRINCIPAL: его заместитель DEPUTY действует, а «второй уровень»
    # OUTSIDER — нет.
    assert _decide(client, rid, _outsider()).status_code == 403
    assert requests_store.get(rid).steps[0].approvals == []
    assert _decide(client, rid, _deputy()).status_code == 200
    assert _can_act(requests_store, rid, DEPUTY, [DEPUTY_GROUP]) is False


def test_deputy_can_act_only_while_step_is_open(client, requests_store, substitutes):
    """Замещение не обходит обычные правила шага: повторную отметку не поставить."""
    _add_substitute(substitutes)
    rid = _personal_request(client)
    assert _decide(client, rid, _deputy()).status_code == 200
    assert _can_act(requests_store, rid, DEPUTY, [DEPUTY_GROUP]) is False


def test_deputy_partial_approval_keeps_request_in_approval(
    client, requests_store, substitutes
):
    """Режим sequential на двоих: голос заместителя засчитан оригиналу, но шаг ждёт второго.

    Заявка остаётся «На согласовании», шаг — «ожидает»."""
    _add_substitute(substitutes)
    rid = _two_responsible_request(client, requests_store)
    assert _decide(client, rid, _deputy()).status_code == 200
    stored = requests_store.get(rid)
    assert stored.status == "На согласовании"
    assert stored.steps[0].status == "ожидает"
    assert [item["sam"] for item in stored.steps[0].approvals] == [DEPUTY]


def test_deputy_cannot_mark_same_step_twice(client, requests_store, substitutes):
    """Повторная отметка заместителем — 409 по его же логину (как у ответственного).

    Второй ответственный снимка отмечает и шаг закрывается: голос заместителя уже
    засчитан за оригинала."""
    _add_substitute(substitutes)
    rid = _two_responsible_request(client, requests_store)
    assert _decide(client, rid, _deputy()).status_code == 200
    repeated = _decide(client, rid, _deputy())
    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "Вы уже согласовали этот шаг"
    assert _decide(client, rid, _headers_for(SECOND, [SECOND_GROUP])).status_code == 200
    assert requests_store.get(rid).steps[0].status == "согласован"


# ---------------------------------------------------------------------------
# Письмо «назначена»: копия заместителю по флагу notify записи
# ---------------------------------------------------------------------------


def test_deputy_receives_assigned_mail_when_notify_true(
    client, routing_store, substitutes, mail_queue, settings_store, ad_reader
):
    """notify=true: заместитель оригинала получает копию письма «назначена»."""
    _add_substitute(substitutes, notify=True)
    _personal_request(client)
    assert sorted(_sent(mail_queue)) == sorted(
        [
            ("%s@example.local" % PRINCIPAL, EVENT_ASSIGNED),
            ("%s@example.local" % DEPUTY, EVENT_ASSIGNED),
        ]
    )


def test_deputy_gets_no_mail_when_notify_false(
    client, routing_store, substitutes, mail_queue, settings_store, ad_reader
):
    """notify=false: письмо уходит только оригиналу, заместитель — нет."""
    _add_substitute(substitutes, notify=False)
    _personal_request(client)
    assert _sent(mail_queue) == [("%s@example.local" % PRINCIPAL, EVENT_ASSIGNED)]


def test_no_duplicate_mail_when_deputy_is_also_responsible(
    client, substitutes, mail_queue, settings_store, ad_reader
):
    """Заместитель, который сам ответственный шага, письмо получает один раз.

    Маршрут из двух шагов: первый — оригиналу (ему же уходит копия как
    заместителю), второй — заместителю. Без дедупликации по адресу он получил бы
    два письма на одну отметку."""
    _add_substitute(substitutes, principal=PRINCIPAL, deputy=DEPUTY, notify=True)
    created = client.post(
        "/requests",
        json=_body(steps=[{"sam": PRINCIPAL}, {"sam": DEPUTY}]),
        headers=_hr(),
    )
    assert created.status_code == 201, created.text
    assert client.post(f"/requests/{created.json()['id']}/submit", headers=_hr()).status_code == 200
    assert sorted(_sent(mail_queue)) == sorted(
        [
            ("%s@example.local" % PRINCIPAL, EVENT_ASSIGNED),
            ("%s@example.local" % DEPUTY, EVENT_ASSIGNED),
        ]
    )


def test_owner_mail_sent_when_store_unavailable(
    client, substitutes, mail_queue, settings_store, ad_reader
):
    """Справочник заместителей недоступен — письмо владельцу шага уходит как обычно.

    Резолвер замещения fail-soft: недоступность справочника не должна отбирать
    уведомление у тех, кому шаг назначен на самом деле."""
    app.dependency_overrides[get_substitute_store] = lambda: BrokenSubstituteStore()
    _add_substitute(substitutes)
    _personal_request(client)
    assert _sent(mail_queue) == [("%s@example.local" % PRINCIPAL, EVENT_ASSIGNED)]


def test_no_deputy_mail_for_group_step(
    client, substitutes, mail_queue, settings_store, ad_reader
):
    """Групповой шаг персональных ответственных не имеет — замещение писем не даёт."""
    _add_substitute(substitutes, principal=PRINCIPAL, deputy=DEPUTY, notify=True)
    _group_step_request(client)
    assert _sent(mail_queue) == []
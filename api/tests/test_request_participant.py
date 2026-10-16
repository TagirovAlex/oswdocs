# Участник согласования видит карточку заявки (решение человека 2026-10-15):
# инициатор (даже без блока request_create) и сотрудник, оставивший отметку по
# шагу, которому потом переназначили ответственного. Проверяются три места
# применения правила (список заявок, счётчик «Мои задачи», гейт карточки) и
# бланк заявки (documents._can_view), а также то, что расширение НЕ открыло
# постороннему ничего и НЕ расширило ПДн участника.
# Справочники, настройки, AD, права и хранилища — офлайн-подмены conftest.
# Все логины, ФИО, службы и предприятие — вымышленные.

from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.documents import (  # noqa: E402
    DocumentRecord,
    InMemoryDocumentsStore,
    _can_view,
    get_documents_store,
)
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    STEP_APPROVED,
    RouteSettings,
    _public_view,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests_store import get_requests_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"
# Группа-владелец шага заявки.
STEP_GROUP = "SED_STEP_BUH"
# Прочая группа шага: даёт вход и роль owner (базовый шаблон — просмотр списка и
# карточки, отметка по своему шагу), но владения шагом заявки не даёт.
OTHER_STEP_GROUP = "SED_STEP_KADR"

FAKE_ENTERPRISE = "ENT_PRIMER_1"
FAKE_TAB = "В-0001"
FAKE_FIO = "Вымышленный Сотрудник Полный"
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"

HR_SAM = "ok.vymyshlennaya"
INITIATOR_SAM = "iniciator.vymyshlennyy"
APPROVER_SAM = "soglasovshchik.vymyshlennyy"
NEW_RESPONSIBLE_SAM = "otvetstvennyy.vymyshlennyy"
OUTSIDER_SAM = "postoronniy.vymyshlennyy"


def _b64(value: str) -> str:
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
    """Настройки маршрута без шаблонов (маршрут задаёт ручной конструктор)."""
    route = RouteSettings(approval_ttl_days=7, position_escalation={})
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
def documents_store():
    """Хранилище документов (бланков) через зависимость."""
    store = InMemoryDocumentsStore()
    app.dependency_overrides[get_documents_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_documents_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store, documents_store):
    """Чистое хранилище заявок/документов и аудит на каждый тест."""
    requests_store.reset()
    documents_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    documents_store.reset()
    audit_log.clear_for_tests()


def _create(client, headers, **kw):
    """Создание заявки (вымышленные поля 1С) с шагом группы SED_STEP_BUH."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": FAKE_FIO,
        "tab_num": FAKE_TAB,
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION,
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
        "steps": [{"owner_group": STEP_GROUP}],
    }
    body.update(kw)
    return client.post("/requests", json=body, headers=headers)


def _submitted(client, headers, **kw) -> str:
    """Заявка, поданная на согласование: возвращает её номер."""
    created = _create(client, headers, **kw)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=headers).status_code == 200
    return rid


def _plain(sam: str) -> dict:
    """Заголовки сотрудника без привилегированных групп (роль owner)."""
    return _headers_for(sam, [OTHER_STEP_GROUP])


def _mine(client, headers) -> list[dict]:
    """«Мои задачи» из счётчика папок (у owner — единственная папка)."""
    folders = client.get("/folders", headers=headers)
    assert folders.status_code == 200, folders.text
    return [folder for folder in folders.json() if folder["id"] == "mine"]


# ---------------------------------------------------------------------------
# Инициатор: роль по умолчанию (owner), блока request_create нет
# ---------------------------------------------------------------------------


def test_initiator_without_request_create_sees_card_list_and_counter(
    client, settings_override, route_override
):
    """Инициатор без блока request_create видит свою карточку и находит её
    в списке и в счётчике «Мои задачи».

    Заявку он создал, когда был в группе ОК (request_create), а открывает её уже
    как обычный сотрудник: роль owner, группы SED_STEP_BUH (владельца шага) у
    него нет — карточку открывает правило инициатора (решение человека
    2026-10-15)."""
    rid = _submitted(client, _headers_for(INITIATOR_SAM, [TEST_HR]))
    initiator = _plain(INITIATOR_SAM)

    card = client.get(f"/requests/{rid}", headers=initiator)
    assert card.status_code == 200, card.text

    listing = client.get("/requests", headers=initiator)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()] == [rid]

    assert _mine(client, initiator) == [
        {"id": "mine", "title": "Мои задачи", "count": 1}
    ]


def test_initiator_card_keeps_personal_data_masked(
    client, settings_override, route_override
):
    """Карточка инициатора-участника: ФИО и служба есть, ПДн закрыты.

    Расширение видимости не расширяет ПДн: табельный номер, предприятие и ключ
    сотрудника остаются только привилегированным, логин автора скрыт."""
    rid = _submitted(client, _headers_for(INITIATOR_SAM, [TEST_HR]))

    body = client.get(f"/requests/{rid}", headers=_plain(INITIATOR_SAM)).json()
    assert body["fio"] == FAKE_FIO
    assert body["department"] == FAKE_SERVICE
    assert body["tab_num"] is None
    assert body["enterprise"] is None
    assert body["enterprise_name"] is None
    assert body["employee_key"] is None
    assert body["created_by"] is None


def test_initiator_view_has_no_can_act(
    client, requests_store, settings_override, route_override
):
    """_public_view инициатора: can_act ни на одном шаге не True.

    Видимость карточки не даёт права отметки: шаг закрыт отметкой группы."""
    rid = _submitted(client, _headers_for(INITIATOR_SAM, [TEST_HR]))
    decided = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=_headers_for("soglasuyushchiy.vymyshlennyy", [STEP_GROUP]),
    )
    assert decided.status_code == 200, decided.text

    view = _public_view(
        requests_store.get(rid), CurrentUser(sam=INITIATOR_SAM, groups=[], role="owner")
    )
    assert [step.status for step in view.steps] == [STEP_APPROVED]
    assert [step.can_act for step in view.steps] == [False]


# ---------------------------------------------------------------------------
# Граница ролей: правило участника расширило видимость согласующим, а не ролям
# ---------------------------------------------------------------------------


def test_blank_not_visible_for_sed_admin(client, requests_store, settings_override, route_override):
    """Администратор СЭД — привилегированная роль (карточку он видит как раньше),
    но бланк ему НЕ полагается.

    Решение человека 2026-10-15 касалось УЧАСТНИКОВ согласования (инициатор,
    владелец шага, отработавший согласование), а не ролей: прежний список ролей
    у бланка остаётся — ОК/руководители ОК/админ. Без явного разделения
    `_can_view` унаследовал бы привилегированность `sed_admin` из правила
    участника и открыл бы ему содержимое всех бланков.

    Проверяется правило `_can_view` напрямую: роль приходит из разбора блоков
    прав, и в тестовой подстановке SED_ADMINS даёт `admin`, а не `sed_admin`."""
    rid = _approved_by_approver(client, settings_override, route_override)
    request = requests_store.get(rid)

    assert _can_view(request, CurrentUser(sam="admin.sed", groups=[], role="sed_admin")) is False
    assert _can_view(request, CurrentUser(sam="admin.priv", groups=[], role="admin")) is True
    # Участник согласования бланк видит.
    assert _can_view(request, CurrentUser(sam=APPROVER_SAM, groups=[], role="owner")) is True


# ---------------------------------------------------------------------------
# Согласовавший, чей шаг переназначили
# ---------------------------------------------------------------------------


def _approved_by_approver(client, settings_override, route_override) -> str:
    """Заявка с персональным шагом согласовавшего: он оставил отметку."""
    rid = _submitted(client, _hr(), steps=[{"sam": APPROVER_SAM}], route_mode="custom")
    decided = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=_plain(APPROVER_SAM),
    )
    assert decided.status_code == 200, decided.text
    return rid


def _hr() -> dict:
    """Заголовки сотрудника ОК (создание заявки)."""
    return _headers_for(HR_SAM, [TEST_HR])


def _blank_version(request_id: str, documents_store) -> None:
    """Заглушка версии бланка заявки (мета нужна только для проверки доступа)."""
    documents_store.create(
        DocumentRecord(
            request_id=request_id,
            version="v1",
            pdf_path="/inmemory/blank.pdf",
            qr_payload="https://sed-mock.local/req",
        )
    )


def test_approver_keeps_card_blank_and_history_after_reassignment(
    client, requests_store, documents_store, settings_override, route_override
):
    """Согласовавший видит карточку, бланк и историю, даже когда ответственного
    шага переназначил сотрудник ОК.

    Отметка в снимке шага сохраняется, а владельцем шага становится другой
    сотрудник: в согласовании участвовал согласовавший, а не текущий
    ответственный (решение человека 2026-10-15)."""
    rid = _approved_by_approver(client, settings_override, route_override)
    approver = _plain(APPROVER_SAM)
    _blank_version(rid, documents_store)

    # Сотрудник ОК отдаёт шаг другому ответственному: отметка при этом остаётся
    # в снимке (как сохраняет закрытый шаг PATCH /requests/{id}/steps).
    request = requests_store.get(rid)
    step = request.steps[0]
    assert [item["sam"] for item in step.approvals] == [APPROVER_SAM]
    step.assignee = NEW_RESPONSIBLE_SAM
    step.assignees = [NEW_RESPONSIBLE_SAM]
    requests_store.update(request)

    card = client.get(f"/requests/{rid}", headers=approver)
    assert card.status_code == 200, card.text
    body = card.json()
    assert [item["sam"] for item in body["steps"][0]["approvals"]] == [APPROVER_SAM]
    assert body["steps"][0]["approved_count"] == 1
    # ПДн участнику по-прежнему не светят.
    assert body["fio"] == FAKE_FIO and body["department"] == FAKE_SERVICE
    assert body["tab_num"] is None
    assert body["enterprise"] is None
    assert body["employee_key"] is None

    listing = client.get("/requests", headers=approver)
    assert [item["id"] for item in listing.json()] == [rid]
    assert _mine(client, approver) == [
        {"id": "mine", "title": "Мои задачи", "count": 1}
    ]
    assert client.get(f"/documents/{rid}", headers=approver).status_code == 200
    assert client.get(f"/requests/{rid}/history", headers=approver).status_code == 200


def test_new_responsible_sees_card_and_outsider_still_denied(
    client, requests_store, settings_override, route_override
):
    """Новый ответственный шага видит карточку, посторонний — по-прежнему 403."""
    rid = _approved_by_approver(client, settings_override, route_override)
    request = requests_store.get(rid)
    request.steps[0].assignee = NEW_RESPONSIBLE_SAM
    request.steps[0].assignees = [NEW_RESPONSIBLE_SAM]
    requests_store.update(request)

    assert client.get(
        f"/requests/{rid}", headers=_plain(NEW_RESPONSIBLE_SAM)
    ).status_code == 200
    outsider = client.get(f"/requests/{rid}", headers=_plain(OUTSIDER_SAM))
    assert outsider.status_code == 403
    assert outsider.json()["detail"] == "Нет доступа к заявке"


# ---------------------------------------------------------------------------
# Посторонний: расширение видимости ничего не открыло
# ---------------------------------------------------------------------------


def test_outsider_still_sees_nothing(
    client, documents_store, settings_override, route_override
):
    """Постороннему по-прежнему ничего: 403 на карточку, пустой список, нулевой
    счётчик «Мои задачи», 403 на бланк и историю."""
    rid = _approved_by_approver(client, settings_override, route_override)
    _blank_version(rid, documents_store)
    outsider = _plain(OUTSIDER_SAM)

    denied = client.get(f"/requests/{rid}", headers=outsider)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Нет доступа к заявке"
    assert client.get("/requests", headers=outsider).json() == []
    assert _mine(client, outsider) == [
        {"id": "mine", "title": "Мои задачи", "count": 0}
    ]
    assert client.get(f"/documents/{rid}", headers=outsider).status_code == 403
    assert client.get(f"/requests/{rid}/history", headers=outsider).status_code == 403
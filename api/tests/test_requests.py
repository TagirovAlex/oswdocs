# Тесты заявок и маршрутов (волна B2): шаблон/ручной, TTL, комментарии, чужие шаги.
# Все ПДн вымышленные; группы подменяются оверрайдом get_settings.

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdNotFound, AdUnavailable  # noqa: E402
from app import settings_routes  # noqa: E402
from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.mailer import EVENT_ASSIGNED, FileMailQueue  # noqa: E402
from app.requests import (  # noqa: E402
    IN_APPROVAL,
    STEP_APPROVED,
    _enterprise_names_map,
    _notify_assigned,
    _public_view,
    _utcnow,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests import _Request, _Step  # noqa: E402
from app.requests import RouteSettings, RouteStepTemplate, RouteTemplate  # noqa: E402
from app.requests_store import get_requests_store  # noqa: E402
from app.settings_routes import SettingsUnavailable, get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные служба/должности (не продовые значения).
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION_LINE = "Старший вымышленный кассир"
FAKE_POSITION_OTHER = "Вымышленный архивариус"
FAKE_ENTERPRISE = "Вымышленное предприятие"

# Вымышленные предприятие/персоналии для резолва имён (§3 handoff 3.2).
ENT_CODE = "ENT_TEST_1"
ENT_NAME = "Вымышленное предприятие (наименование)"
SEED_ENTERPRISES = '[{"code": "%s", "name": "%s"}]' % (ENT_CODE, ENT_NAME)
BUH_SAM = "step.buhgalter"
COLLEAGUE_SAM = "step.kollega"
FAKE_OWNER_FIO = "Вымышленный Согласующий Полный"


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


@pytest.fixture
def route_override():
    """Настройки маршрута с одним шаблоном (служба+линейный → 2 шага)."""
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
def test_settings_override():
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
def requests_store():
    """Хранилище заявок через зависимость (общий InMemory-экземпляр)."""
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_requests_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    """Чистое хранилище и аудит на каждый тест."""
    requests_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    audit_log.clear_for_tests()


@pytest.fixture
def hr() -> dict:
    """Заголовки ОК (разрешенная группа для конструктора)."""
    return _headers_for("ok.vymyshlennaya", ["SED_HR"])


@pytest.fixture
def hr_admin() -> dict:
    """Заголовки руководителя ОК (права конструктора, как ОК)."""
    return _headers_for("ok.head.vymyshlenny", ["SED_HR_ADMIN"])


@pytest.fixture
def buh_owner() -> dict:
    """Заголовки владельца первого шага шаблона."""
    return _headers_for("step.buhgalter", ["SED_STEP_BUH"])


@pytest.fixture
def other_owner() -> dict:
    """Заголовки чужой группы (не владелец первого шага)."""
    return _headers_for("step.chuzhoi", ["SED_STEP_OTHER"])


@pytest.fixture
def hr_step_owner() -> dict:
    """Заголовки владельца второго шага шаблона (группы SED_STEP_HR)."""
    return _headers_for("step.kadrovik", ["SED_STEP_HR"])


class FakeAdReader:
    """Мок AdReader (только чтение): ФИО по sAMAccountName из вымышленных записей.

    raise_exc=True — AD недоступен (любой вызов get_user падает)."""

    def __init__(self, entries: dict, raise_exc: bool = False):
        self._entries = entries
        self._raise = raise_exc

    def get_user(self, sam: str):
        if self._raise:
            raise AdUnavailable("AD недоступен (тест)")
        if sam not in self._entries:
            raise AdNotFound("Пользователь не найден (тест)")
        return SimpleNamespace(
            sam=sam,
            display_name=self._entries[sam],
            mail="%s@example.local" % sam,
        )


class InMemorySettingsStore:
    """Мок DbSettingsStore: значения ключей в сид-формате (как в test_requests_contract)."""

    def __init__(self, values: dict | None = None, broken: bool = False):
        self._values = dict(values or {})
        self.broken = broken

    def get(self, key: str):
        if self.broken:
            raise SettingsUnavailable("Хранилище настроек недоступно (тест)")
        return self._values.get(key)

    def get_many(self, keys):
        if self.broken:
            raise SettingsUnavailable("Хранилище настроек недоступно (тест)")
        return {k: v for k, v in self._values.items() if k in keys}


@pytest.fixture
def settings_store():
    """Хранилище настроек с одним предприятием (сид-формат settings.enterprises)."""
    store = InMemorySettingsStore({"enterprises": SEED_ENTERPRISES})
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture
def ad_reader():
    """Фейк-ридер AD: ФИО согласующего резолвится по sAMAccountName."""
    reader = FakeAdReader({BUH_SAM: FAKE_OWNER_FIO, COLLEAGUE_SAM: "Вымышленный Коллега Полный"})
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


def _create(client, headers, **kw) -> dict:
    """Создание заявки с вымышленными полями по умолчанию."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": "Вымышленный Сотрудник Полный",
        "tab_num": "В-0001",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION_LINE,
    }
    body.update(kw)
    response = client.post("/requests", json=body, headers=headers)
    return response


def test_no_template_falls_back_to_manual(client, hr, test_settings_override, route_override):
    """Нет шаблона → 422 без steps, с ручным маршрутом — 201 custom."""
    bad = _create(client, hr, position=FAKE_POSITION_OTHER)
    assert bad.status_code == 422
    ok = _create(
        client,
        hr,
        position=FAKE_POSITION_OTHER,
        steps=[{"owner_group": "SED_STEP_BUH"}],
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["route_origin"] == "custom"
    assert body["status"] == "Черновик"
    assert len(body["steps"]) == 1


def test_step_without_executor_422(client, hr, test_settings_override, route_override):
    """Шаг без sam и owner_group — 422 от схемы (раньше доходил до _Step и давал 500).

    Пустая/пробельная группа тоже считается отсутствующим исполнителем."""
    without_group = _create(
        client, hr, position=FAKE_POSITION_OTHER, steps=[{"resolver": "by_group"}]
    )
    assert without_group.status_code == 422
    assert "owner_group" in without_group.text
    blank_group = _create(
        client, hr, position=FAKE_POSITION_OTHER, steps=[{"owner_group": "  "}]
    )
    assert blank_group.status_code == 422


def test_block_step_without_executor_422(client, hr, test_settings_override, route_override):
    """Блочный маршрут: шаг без sam и owner_group — 422; контракт блоков цел
    (групповой шаг owner_group+by_group и персональный sam создаются)."""
    bad = _create(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"resolver": "by_group"}]}]
    )
    assert bad.status_code == 422
    group = _create(
        client,
        hr,
        blocks=[
            {
                "mode": "sequential",
                "steps": [{"owner_group": "SED_STEP_BUH", "resolver": "by_group"}],
            }
        ],
    )
    assert group.status_code == 201
    personal = _create(client, hr, blocks=[{"mode": "parallel", "steps": [{"sam": BUH_SAM}]}])
    assert personal.status_code == 201
    step = personal.json()["steps"][0]
    assert step["owner_group"] == BUH_SAM
    assert step["resolver"] == "by_user"
    assert step["assignee"] == BUH_SAM


def test_step_assignee_is_valid_executor(client, hr, test_settings_override, route_override):
    """assignee (замена руководителя) — валидный исполнитель наравне с sam.

    Раньше такой шаг получал 422 с текстом про sam при уже указанном исполнителе.
    owner_group шага = assignee (иначе обязательный _Step.owner_group пустеет),
    резолвер ad_direct_manager не ломается."""
    personal = _create(
        client,
        hr,
        blocks=[
            {
                "mode": "sequential",
                "steps": [{"assignee": BUH_SAM, "resolver": "ad_direct_manager"}],
            }
        ],
    )
    assert personal.status_code == 201
    step = personal.json()["steps"][0]
    assert step["owner_group"] == BUH_SAM
    assert step["assignee"] == BUH_SAM
    assert step["resolver"] == "ad_direct_manager"
    # Плоский steps — тот же контракт.
    flat = _create(
        client,
        hr,
        position=FAKE_POSITION_OTHER,
        steps=[{"assignee": BUH_SAM, "resolver": "ad_direct_manager"}],
    )
    assert flat.status_code == 201
    assert flat.json()["steps"][0]["assignee"] == BUH_SAM
    # Пустой assignee без группы — по-прежнему 422.
    blank = _create(
        client,
        hr,
        position=FAKE_POSITION_OTHER,
        steps=[{"assignee": "  ", "resolver": "ad_direct_manager"}],
    )
    assert blank.status_code == 422


def test_flat_step_sam_is_personal_executor(client, hr, test_settings_override, route_override):
    """sam в плоском steps работает как в блоках: by_user, owner_group = sam.

    Раньше owner_group оставался пустым и POST падал в 500 на _Step."""
    response = _create(client, hr, position=FAKE_POSITION_OTHER, steps=[{"sam": BUH_SAM}])
    assert response.status_code == 201
    step = response.json()["steps"][0]
    assert step["owner_group"] == BUH_SAM
    assert step["resolver"] == "by_user"
    assert step["assignee"] == BUH_SAM


def test_template_picked_by_service_category(client, hr, test_settings_override, route_override):
    """Шаблон по службе/категории (категория из position_to_category)."""
    response = _create(client, hr)
    assert response.status_code == 201
    body = response.json()
    assert body["route_origin"] == "template"
    assert [s["owner_group"] for s in body["steps"]] == ["SED_STEP_BUH", "SED_STEP_HR"]
    # Явная категория от ОК важнее подсказки: чужой категории нет в шаблонах → нужен ручной.
    miss = _create(client, hr, category="руководитель")
    assert miss.status_code == 422


def test_hr_admin_can_create(client, hr_admin, test_settings_override, route_override):
    """Руководитель ОК создает заявку, как ОК (201, ручной конструктор разрешен)."""
    response = _create(client, hr_admin)
    assert response.status_code == 201
    body = response.json()
    assert body["route_origin"] == "template"
    assert body["status"] == "Черновик"


def test_hr_admin_can_patch_steps(client, hr_admin, test_settings_override, route_override):
    """Руководитель ОК правит шаги (только разрешенной группе, 200)."""
    rid = _create(client, hr_admin).json()["id"]
    response = client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": "SED_STEP_BUH"}], "reason": "Вымышленная правка"},
        headers=hr_admin,
    )
    assert response.status_code == 200


def test_patch_block_route_steps(client, hr, test_settings_override, route_override):
    """Правка блочного маршрута: с blocks — 200, ожидающие заменяются, order
    продолжается после существующих блоков (кодировка блоков цела)."""
    rid = _create(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": "SED_STEP_BUH"}]},
            {"mode": "sequential", "steps": [{"owner_group": "SED_STEP_BUH"}]},
        ],
    ).json()["id"]
    response = client.patch(
        f"/requests/{rid}/steps",
        json={
            "blocks": [{"mode": "parallel", "steps": [{"owner_group": "SED_STEP_BUH"}]}],
            "reason": "Вымышленная правка блоков",
        },
        headers=hr,
    )
    assert response.status_code == 200
    steps = response.json()["steps"]
    assert len(steps) == 1
    # Все прежние шаги были ожидающими (отброшены) — нумерация с блока 0,
    # параллельный шаг блока 0 = 100 + 1.
    assert steps[0]["order"] == 101
    assert any(e.action == "steps.patch" for e in audit_log.all())


def test_patch_block_route_keeps_closed_step_order(
    client, hr, requests_store, test_settings_override, route_override
):
    """Правка блоками при закрытом шаге: order не дублируется (регресс B1).

    Одиночный последовательный блок 0 кодируется как order 1..N — так же, как
    плоский список. Если закрытый шаг (order=1) сохранён, новый блок обязан
    получить order >= 1001, иначе decide_step будет попадать в закрытый шаг."""
    rid = _create(
        client,
        hr,
        blocks=[{"mode": "sequential", "steps": [{"owner_group": "SED_STEP_BUH"}]}],
    ).json()["id"]
    requests_store.get(rid).steps[0].status = STEP_APPROVED
    response = client.patch(
        f"/requests/{rid}/steps",
        json={
            "blocks": [
                {"mode": "sequential", "steps": [{"owner_group": "SED_STEP_BUH"}]}
            ]
        },
        headers=hr,
    )
    assert response.status_code == 200
    orders = [s["order"] for s in response.json()["steps"]]
    assert len(orders) == len(set(orders))  # нет дублей order
    assert min(orders) == 1  # закрытый шаг сохранён в блоке 0
    assert max(orders) >= 1001  # новый блок — после закрытого


def test_patch_block_route_flat_steps_409(client, hr, test_settings_override, route_override):
    """Блочную заявку нельзя переписать плоским steps — 409 (нужен blocks)."""
    rid = _create(
        client,
        hr,
        blocks=[{"mode": "parallel", "steps": [{"owner_group": "SED_STEP_BUH"}]}],
    ).json()["id"]
    response = client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": "SED_STEP_BUH"}]},
        headers=hr,
    )
    assert response.status_code == 409


def test_patch_flat_route_with_blocks_upgrades(client, hr, test_settings_override, route_override):
    """Плоскую заявку можно перевести на блочный маршрут (blocks) — 200."""
    rid = _create(client, hr).json()["id"]
    response = client.patch(
        f"/requests/{rid}/steps",
        json={"blocks": [{"mode": "sequential", "steps": [{"owner_group": "SED_STEP_BUH"}]}]},
        headers=hr,
    )
    assert response.status_code == 200
    assert len(response.json()["steps"]) == 1


def test_patch_empty_blocks_422(client, hr, test_settings_override, route_override):
    """Пустой блочный payload — 422, как и при создании."""
    rid = _create(client, hr).json()["id"]
    response = client.patch(
        f"/requests/{rid}/steps",
        json={"blocks": [{"mode": "sequential", "steps": []}]},
        headers=hr,
    )
    assert response.status_code == 422


def test_reject_without_comment_422(client, hr, buh_owner, test_settings_override, route_override):
    """Отказ без комментария → 422, с комментарием → Отклонено."""
    created = _create(client, hr).json()
    rid = created["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    no_comment = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "reject"},
        headers=buh_owner,
    )
    assert no_comment.status_code == 422
    with_comment = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "reject", "comment": "Вымышленная причина отказа"},
        headers=buh_owner,
    )
    assert with_comment.status_code == 200
    assert with_comment.json()["status"] == "Отклонено"


def test_foreign_step_403(client, hr, other_owner, test_settings_override, route_override):
    """Чужой шаг (другая группа) → 403."""
    rid = _create(client, hr).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    response = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=other_owner,
    )
    assert response.status_code == 403


def test_expired_reissue(
    client, hr, buh_owner, requests_store, test_settings_override, route_override
):
    """Просрочка TTL → 410 и На доработке, повтор (reissue) возвращает в работу."""
    rid = _create(client, hr).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    # Искусственно состариваем шаг мимо TTL (хранилище in-memory — стенд хранит в БД).
    requests_store.get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    late = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    )
    assert late.status_code == 410
    assert requests_store.get(rid).status == "На доработке"
    again = client.post(f"/requests/{rid}/steps/1/reissue", headers=hr)
    assert again.status_code == 200
    assert again.json()["status"] == "На согласовании"
    assert again.json()["steps"][0]["status"] == "ожидает"
    # После повтора согласие без комментария (флаг не требует) — успешно.
    done = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    )
    assert done.status_code == 200


def test_manual_and_patch_only_allowed_group(
    client, buh_owner, hr, test_settings_override, route_override
):
    """Ручной конструктор и правка шагов — только разрешенной группе + audit_log."""
    manual = _create(
        client,
        buh_owner,
        position=FAKE_POSITION_OTHER,
        steps=[{"owner_group": "SED_STEP_BUH"}],
    )
    assert manual.status_code == 403
    rid = _create(
        client, hr, position=FAKE_POSITION_OTHER, steps=[{"owner_group": "SED_STEP_BUH"}]
    ).json()["id"]
    forbidden = client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": "SED_STEP_OTHER"}]},
        headers=buh_owner,
    )
    assert forbidden.status_code == 403
    actions_before = {e.action for e in audit_log.all()}
    assert "steps.patch" not in actions_before
    ok = client.patch(
        f"/requests/{rid}/steps",
        json={
            "steps": [{"owner_group": "SED_STEP_BUH", "resolver": "ad_direct_manager"}],
            "manager": "step.buhgalter",
            "reason": "Замена руководителя",
        },
        headers=hr,
    )
    assert ok.status_code == 200
    step = ok.json()["steps"][0]
    assert step["assignee"] == "step.buhgalter"
    assert any(e.action == "steps.patch" for e in audit_log.all())


# --- can_act: кнопка есть ровно там, где API примет отметку (§3 handoff 3.1) ---

def _step_of(body: dict, order: int) -> dict:
    """Шаг заявки из ответа по номеру (order)."""
    return next(s for s in body["steps"] if s["order"] == order)


def _create_and_submit(client, headers, **kw) -> str:
    """Заявка по шаблону, поданная (статус «На согласовании»): возвращает id."""
    rid = _create(client, headers, **kw).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=headers).status_code == 200
    return rid


def test_can_act_true_for_owner_of_current_step(
    client, hr, buh_owner, test_settings_override, route_override, settings_store
):
    """can_act=True у владельца текущего ожидающего шага; следующий шаг — False."""
    rid = _create_and_submit(client, hr)
    body = client.get(f"/requests/{rid}", headers=buh_owner).json()
    assert _step_of(body, 1)["can_act"] is True
    assert _step_of(body, 2)["can_act"] is False


def test_can_act_false_for_owner_of_another_step(
    client, hr, hr_step_owner, test_settings_override, route_override, settings_store
):
    """Сотрудник-не-владелец текущего шага (владелец другого): can_act=False везде."""
    rid = _create_and_submit(client, hr)
    body = client.get(f"/requests/{rid}", headers=hr_step_owner).json()
    assert all(s["can_act"] is False for s in body["steps"])


def test_can_act_false_for_stranger(
    client, hr, requests_store, test_settings_override, route_override, settings_store
):
    """Посторонний (не в группе шага и не assignee): карточка ему не отдается (403),
    а в сборке представления can_act=False."""
    rid = _create_and_submit(client, hr)
    stranger = _headers_for("user.postoronny", ["SED_STEP_OTHER"])
    assert client.get(f"/requests/{rid}", headers=stranger).status_code == 403
    view = _public_view(
        requests_store.get(rid), CurrentUser(sam="user.postoronny", groups=[], role="owner")
    )
    assert all(s.can_act is False for s in view.steps)


def test_can_act_false_on_approved_step(
    client, hr, buh_owner, test_settings_override, route_override, settings_store
):
    """Завершённый шаг: can_act=False (отметка уже стоит, второй шаг — чужая группа)."""
    rid = _create_and_submit(client, hr)
    approved = client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=buh_owner
    )
    assert approved.status_code == 200
    body = client.get(f"/requests/{rid}", headers=buh_owner).json()
    assert _step_of(body, 1)["status"] == "согласован"
    assert all(s["can_act"] is False for s in body["steps"])


def test_can_act_false_outside_approval_status(
    client, hr, test_settings_override, route_override, settings_store
):
    """Черновик (не «На согласовании»): can_act=False даже у привилегированного."""
    body = _create(client, hr).json()
    assert body["status"] == "Черновик"
    assert all(s["can_act"] is False for s in body["steps"])


def test_can_act_false_on_expired_step(
    client, hr, buh_owner, requests_store, test_settings_override, route_override, settings_store
):
    """Просроченный шаг (expires_at в прошлом): can_act=False (иначе кнопка, а API даст 410)."""
    rid = _create_and_submit(client, hr)
    requests_store.get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    body = client.get(f"/requests/{rid}", headers=buh_owner).json()
    assert _step_of(body, 1)["can_act"] is False


def test_can_act_false_for_admin_not_owner(
    client, hr, test_settings_override, route_override, settings_store
):
    """Роль не даёт обход: админ-не-владелец шага видит can_act=False."""
    rid = _create_and_submit(client, hr)
    admin = _headers_for("adm.vymyshlenny", [TEST_ADMINS])
    body = client.get(f"/requests/{rid}", headers=admin).json()
    assert all(s["can_act"] is False for s in body["steps"])


# --- owner_name: ФИО согласующего из AD (fail-soft) ---

def _with_assignee(client, hr, sam: str) -> str:
    """Заявка с одним шагом, персональный исполнитель — sam (замена руководителя)."""
    rid = _create(client, hr).json()["id"]
    patched = client.patch(
        f"/requests/{rid}/steps",
        json={
            "steps": [{"owner_group": "SED_STEP_BUH", "resolver": "ad_direct_manager"}],
            "manager": sam,
            "reason": "Замена руководителя (вымышленная)",
        },
        headers=hr,
    )
    assert patched.status_code == 200
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    return rid


def test_owner_name_resolved_from_ad_for_assignee(
    client, hr, buh_owner, ad_reader, test_settings_override, route_override, settings_store
):
    """Шаг с персональным исполнителем: owner_name — ФИО из AD, can_act=True."""
    rid = _with_assignee(client, hr, BUH_SAM)
    step = client.get(f"/requests/{rid}", headers=buh_owner).json()["steps"][0]
    assert step["assignee"] == BUH_SAM
    assert step["owner_name"] == FAKE_OWNER_FIO
    assert step["can_act"] is True


def test_owner_name_group_step_none_can_act_by_group(
    client, hr, buh_owner, requests_store, test_settings_override, route_override, settings_store
):
    """Групповой шаг (без assignee): owner_name=None, у члена группы can_act=True,
    у постороннего — False."""
    rid = _create_and_submit(client, hr)
    step = client.get(f"/requests/{rid}", headers=buh_owner).json()["steps"][0]
    assert step["assignee"] is None
    assert step["owner_name"] is None
    assert step["can_act"] is True
    stranger = CurrentUser(sam="user.postoronny", groups=["SED_STEP_OTHER"], role="owner")
    assert all(not s.can_act for s in _public_view(requests_store.get(rid), stranger).steps)


def test_owner_name_none_when_no_ad_reader(
    client, hr, buh_owner, test_settings_override, route_override, settings_store
):
    """Ридер AD недоступен (lambda: None): owner_name=None, ответ 200 (fail-soft)."""
    rid = _with_assignee(client, hr, BUH_SAM)
    app.dependency_overrides[get_ad_reader] = lambda: None
    try:
        response = client.get(f"/requests/{rid}", headers=buh_owner)
    finally:
        app.dependency_overrides.pop(get_ad_reader, None)
    assert response.status_code == 200
    assert response.json()["steps"][0]["owner_name"] is None


def test_owner_name_none_when_ad_raises(
    client, hr, buh_owner, test_settings_override, route_override, settings_store
):
    """Ридер AD падает (AdUnavailable): owner_name=None, ответ 200 — выдача не роняется."""
    rid = _with_assignee(client, hr, BUH_SAM)
    app.dependency_overrides[get_ad_reader] = lambda: FakeAdReader({}, raise_exc=True)
    try:
        response = client.get(f"/requests/{rid}", headers=buh_owner)
    finally:
        app.dependency_overrides.pop(get_ad_reader, None)
    assert response.status_code == 200
    assert response.json()["steps"][0]["owner_name"] is None


# --- assignee (это sAMAccountName): непривилегированному не-владельцу скрывается ---

def test_assignee_hidden_from_non_owner(
    client, hr, test_settings_override, route_override, settings_store
):
    """Регресс на утечку логина: assignee (это sAMAccountName) не отдается
    непривилегированному не-владельцу шага; владельцу (это его логин) и
    привилегированному — отдается."""
    rid = _with_assignee(client, hr, COLLEAGUE_SAM)
    assignee_view = client.get(
        f"/requests/{rid}", headers=_headers_for(COLLEAGUE_SAM, ["SED_STEP_BUH"])
    )
    assert assignee_view.json()["steps"][0]["assignee"] == COLLEAGUE_SAM
    assert assignee_view.json()["steps"][0]["can_act"] is True
    # Коллега из той же группы, но не исполнитель: логин ему не отдается, кнопки нет.
    colleague_view = client.get(
        f"/requests/{rid}", headers=_headers_for("step.ne.avtor", ["SED_STEP_BUH"])
    )
    assert colleague_view.status_code == 200
    assert colleague_view.json()["steps"][0]["assignee"] is None
    assert colleague_view.json()["steps"][0]["can_act"] is False
    # Привилегированному (ОК) логин виден.
    assert client.get(f"/requests/{rid}", headers=hr).json()["steps"][0]["assignee"] == COLLEAGUE_SAM


# --- created_by/done_by (это sAMAccountName): только привилегированным ---

def test_created_by_and_done_by_hidden_from_owner(
    client, hr, buh_owner, test_settings_override, route_override
):
    """Автор заявки и отметивший — логины: владельцу шага не отдаются (как
    assignee), привилегированному отдаются. Даты отметки (done_at) остаются."""
    rid = _create(client, hr).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    ).status_code == 200
    owner_view = client.get(f"/requests/{rid}", headers=buh_owner).json()
    assert owner_view["created_by"] is None
    assert owner_view["steps"][0]["done_by"] is None
    assert owner_view["steps"][0]["done_at"] is not None
    hr_view = client.get(f"/requests/{rid}", headers=hr).json()
    assert hr_view["created_by"] == hr["X-Mock-Sam"]
    assert hr_view["steps"][0]["done_by"] == buh_owner["X-Mock-Sam"]


# --- enterprise_name: продакшн-ветка резолва настроек (без dependency_overrides) ---

class _EnterpriseStore:
    """Хранилище настроек с одним ключом enterprises (как DbSettingsStore)."""

    def __init__(self, raw):
        self._raw = raw

    def get(self, key):
        return self._raw


def test_enterprise_names_map_reads_real_settings_store(monkeypatch, real_boundaries):
    """Резолв карты предприятий идёт через боевое хранилище настроек.

    Проверяется именно продакшн-ветка (подменён сам _db_store, а не
    dependency_overrides): раньше сюда попадал объект Depends вместо Settings
    и карта молча превращалась в {} — enterprise_name был всегда null.
    real_boundaries снимает офлайн-подмену границ из conftest.
    """
    monkeypatch.setattr(
        settings_routes, "_db_store",
        _EnterpriseStore('[{"code":"E1","name":"Первое"},{"code":"E2","name":"Второе"}]'),
    )
    assert _enterprise_names_map() == {"E1": "Первое", "E2": "Второе"}


def test_enterprise_names_map_empty_when_settings_fails(monkeypatch, real_boundaries):
    """Ключа нет или БД недоступна — пустая карта, без исключения (fail-soft)."""

    class BrokenStore:
        def get(self, key):
            raise SettingsUnavailable("настройки недоступны")

    monkeypatch.setattr(settings_routes, "_db_store", BrokenStore())
    assert _enterprise_names_map() == {}
    monkeypatch.setattr(settings_routes, "_db_store", _EnterpriseStore(None))
    assert _enterprise_names_map() == {}


# --- Уведомление «назначена»: настройки письма читаются лениво (только при адресатах) ---

class _CountingMailSettingsStore:
    """Хранилище настроек, считающее прочитанные ключи (для проверки ленивости)."""

    def __init__(self):
        self.reads: list[str] = []

    def get(self, key):
        self.reads.append(key)
        if key == "mail_templates":
            return json.dumps(
                [
                    {
                        "code": EVENT_ASSIGNED,
                        "subject": "Заявка {{ request_id }} назначена",
                        "body_html": "<html>{{ fio }} {{ url }}</html>",
                    }
                ]
            )
        if key == "smtp_from":
            return json.dumps("sed@example.local")
        return None


def _request_for_notify(assignee: str | None = BUH_SAM) -> _Request:
    """Заявка на согласовании с одним персональным шагом (адресат — из AD)."""
    return _Request(
        id="REQ-9001",
        status=IN_APPROVAL,
        enterprise=FAKE_ENTERPRISE,
        fio="Вымышленный Сотрудник Полный",
        tab_num="В-0001",
        department=FAKE_SERVICE,
        position=FAKE_POSITION_LINE,
        created_by="ok.vymyshlennaya",
        steps=[
            _Step(
                order=1,
                owner_group=assignee or "SED_STEP_BUH",
                assignee=assignee,
                expires_at=_utcnow() + timedelta(days=1),
            )
        ],
    )


def test_notify_assigned_lazy_settings_read(tmp_path, ad_reader):
    """Без адресатов mail_templates/smtp_from не читаются вовсе, с адресатами —
    по одному чтению каждого ключа на заявку (регресс на лишний SELECT к настройкам)."""
    queue = FileMailQueue(tmp_path / "mail_queue.json")
    store = _CountingMailSettingsStore()
    settings = Settings(APP_BASE_URL="https://sed.example.local")
    # Ридера AD нет — адресатов нет, письмо не ставится и настройки не читаются.
    _notify_assigned(_request_for_notify(), queue, store, None, settings)
    assert store.reads == []
    assert queue.pending_count() == 0
    # С ридером AD — по одному чтению на заявку и письмо в очередь.
    _notify_assigned(_request_for_notify(), queue, store, ad_reader, settings)
    assert store.reads == ["mail_templates", "smtp_from"]
    assert queue.pending_count() == 1

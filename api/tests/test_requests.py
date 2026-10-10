# Тесты заявок и маршрутов (волна B2): бланк/ручной, TTL, комментарии, чужие шаги.
# Все ПДн вымышленные; группы подменяются оверрайдом get_settings.

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdNotFound, AdUnavailable  # noqa: E402
from app import settings_routes  # noqa: E402
from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.employee_sync import (  # noqa: E402
    EmployeeSyncUnavailable,
    InMemoryEmployeeSyncStore,
    get_employee_sync_store,
)
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.mailer import (  # noqa: E402
    EVENT_ASSIGNED,
    EVENT_CLOSED,
    EVENT_RETURNED,
    FileMailQueue,
    get_mail_queue,
)
from app.requests import (  # noqa: E402
    IN_APPROVAL,
    STEP_APPROVED,
    _employee_keys,
    _enterprise_names_map,
    _notify_assigned,
    _public_view,
    _utcnow,
    get_memory_requests_store,
    get_route_settings,
)
from app.requests import _Request, _Step  # noqa: E402
from app.requests import RouteSettings  # noqa: E402
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
    """Настройки маршрута без шаблонов службы (ключ templates снят).

    Маршрут в этих тестах задаёт либо ручной конструктор (steps/blocks), либо
    выбранный бланк — см. test_requests_blank_flow.py и test_requests_route_mode.py."""
    route = RouteSettings(
        approval_ttl_days=7,
        position_escalation={},
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
    """Заголовки владельца первого шага маршрута."""
    return _headers_for("step.buhgalter", ["SED_STEP_BUH"])


@pytest.fixture
def other_owner() -> dict:
    """Заголовки чужой группы (не владелец первого шага)."""
    return _headers_for("step.chuzhoi", ["SED_STEP_OTHER"])


@pytest.fixture
def hr_step_owner() -> dict:
    """Заголовки владельца второго шага маршрута (группы SED_STEP_HR)."""
    return _headers_for("step.kadrovik", ["SED_STEP_HR"])


class FakeAdReader:
    """Мок AdReader (только чтение): ФИО по sAMAccountName из вымышленных записей.

    raise_exc=True — AD недоступен (любой вызов get_user падает); titles —
    должности (title) по логинам, без записи — пусто (как старые записи)."""

    def __init__(self, entries: dict, raise_exc: bool = False, titles: dict | None = None):
        self._entries = entries
        self._raise = raise_exc
        self._titles = titles or {}

    def get_user(self, sam: str):
        if self._raise:
            raise AdUnavailable("AD недоступен (тест)")
        if sam not in self._entries:
            raise AdNotFound("Пользователь не найден (тест)")
        return SimpleNamespace(
            sam=sam,
            display_name=self._entries[sam],
            title=self._titles.get(sam, ""),
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
    """Создание заявки с вымышленными полями по умолчанию.

    Маршрут по умолчанию — ручной (два групповых шага): шаблоны службы сняты,
    маршрут либо выбирает бланк, либо задаёт конструктор. Тест, которому нужен
    другой маршрут, передаёт steps/blocks/blank_id."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": "Вымышленный Сотрудник Полный",
        "tab_num": "В-0001",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION_LINE,
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
        "steps": [
            {"owner_group": "SED_STEP_BUH"},
            {"owner_group": "SED_STEP_HR"},
        ],
    }
    body.update(kw)
    response = client.post("/requests", json=body, headers=headers)
    return response


def test_without_blank_and_without_steps_422(
    client, hr, requests_store, test_settings_override, route_override
):
    """Ни бланка, ни ручного маршрута — 422 и заявки нет; с steps — 201 custom.

    Снятые шаблоны службы больше не подставляют маршрут молча: маршрут задаёт
    либо бланк (его шаги), либо ручной конструктор blocks/steps. Без бланка в
    режиме auto подбор по службе идёт только за настройкой blank_autopick
    (выключена по умолчанию), а в этом файле справочники маршрута офлайн
    (карточки сотрудника нет), поэтому API отвечает 422 с подсказкой переключиться
    на «Вручную» и ничего не создаёт. С явными steps создаётся ручная заявка."""
    bad = _create(client, hr, steps=None)
    assert bad.status_code == 422
    assert "Вручную" in bad.json()["detail"]
    assert requests_store.list_all() == []
    ok = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])
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


def test_manual_route_replaces_removed_service_template(
    client, hr, test_settings_override, route_override
):
    """Маршрут задаёт ручной конструктор: шаги из steps по порядку, origin=custom.

    Ключ настроек templates (служба+категория → шаги) снят, маршрут по шаблону
    службы больше не подставляется; явная категория ОК остаётся в заявке."""
    response = _create(client, hr, category="линейный")
    assert response.status_code == 201
    body = response.json()
    assert body["route_origin"] == "custom"
    assert body["category"] == "линейный"
    assert [s["owner_group"] for s in body["steps"]] == ["SED_STEP_BUH", "SED_STEP_HR"]


def test_hr_admin_can_create(client, hr_admin, test_settings_override, route_override):
    """Руководитель ОК создает заявку, как ОК (201, ручной конструктор разрешен)."""
    response = _create(client, hr_admin)
    assert response.status_code == 201
    body = response.json()
    assert body["route_origin"] == "custom"
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
    """Отказ без комментария → 422, с комментарием → заявка инициатору на доработку."""
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
    # Отказ всегда уводит заявку инициатору на доработку: маршрут сам не
    # переоткрывается, решает инициатор (комментарии + правка шагов).
    assert with_comment.json()["status"] == "На доработке"


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
    """Заявка с заданным маршрутом, поданная (статус «На согласовании»): id."""
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


# --- owner_duty: должность персонального исполнителя из AD (fail-soft) ---


def test_owner_duty_resolved_from_ad_for_assignee(
    client, hr, buh_owner, test_settings_override, route_override, settings_store
):
    """Шаг с персональным исполнителем: owner_duty — должность (title) из AD."""
    rid = _with_assignee(client, hr, BUH_SAM)
    app.dependency_overrides[get_ad_reader] = lambda: FakeAdReader(
        {BUH_SAM: FAKE_OWNER_FIO}, titles={BUH_SAM: "Вымышленный Бухгалтер"}
    )
    try:
        step = client.get(f"/requests/{rid}", headers=buh_owner).json()["steps"][0]
    finally:
        app.dependency_overrides.pop(get_ad_reader, None)
    assert step["owner_name"] == FAKE_OWNER_FIO
    assert step["owner_duty"] == "Вымышленный Бухгалтер"


def test_owner_duty_none_for_group_step_and_no_reader(
    client, hr, buh_owner, requests_store, test_settings_override, route_override, settings_store
):
    """Групповой шаг — owner_duty None; без ридера у персонального — тоже None."""
    rid = _create_and_submit(client, hr)
    step = client.get(f"/requests/{rid}", headers=buh_owner).json()["steps"][0]
    assert step["assignee"] is None
    assert step["owner_duty"] is None
    rid = _with_assignee(client, hr, BUH_SAM)
    app.dependency_overrides[get_ad_reader] = lambda: None
    try:
        step = client.get(f"/requests/{rid}", headers=buh_owner).json()["steps"][0]
    finally:
        app.dependency_overrides.pop(get_ad_reader, None)
    assert step["owner_duty"] is None


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


# --- employee_key: пакетный резолв ключей карточек сотрудников по справочнику ---

# Вымышленные предприятие/база/люди для локального справочника employees.
EMP_ENT = "ENT_VYMYSHLENNAYA"
EMP_BASE = "ZUP_VYM"
EMP_TAB = "Т-000777"
EMP_SAM = "vymyshlenny.soglasuyushchiy"


class BrokenEmployeeStore:
    """Справочник сотрудников недоступен (503-источник) — выдача не должна роняться."""

    def find_by_people(self, enterprise, sam_list, tab_list):
        raise EmployeeSyncUnavailable("Справочник сотрудников недоступен (тест)")


class ExplodingEmployeeStore:
    """Справочник падает не типизированным исключением (драйвер БД, сеть)."""

    def find_by_people(self, enterprise, sam_list, tab_list):
        raise RuntimeError("соединение разорвано (тест)")


class CountingEmployeeStore(InMemoryEmployeeSyncStore):
    """Справочник, считающий вызовы find_by_people (проверка пакетности)."""

    def __init__(self, rows: list[dict]):
        super().__init__()
        self.calls: list[tuple] = []
        self.upsert_many(rows)

    def find_by_people(self, enterprise, sam_list, tab_list):
        self.calls.append((enterprise, sorted(sam_list), sorted(tab_list)))
        return super().find_by_people(enterprise, sam_list, tab_list)


def _employee_store(rows: list[dict]):
    """Хранилище справочника с вымышленными строками employees.

    Подмена границы ставится на тест; снимает её автофикстура conftest
    (offline_boundaries) — как и остальные офлайн-границы."""
    store = InMemoryEmployeeSyncStore()
    store.upsert_many(rows)
    app.dependency_overrides[get_employee_sync_store] = lambda: store
    return store


def _employee_row(tab_num: str, sam: str | None, base_code: str = EMP_BASE) -> dict:
    """Строка employees контракта справочника (как отдаёт sync_employees)."""
    return {
        "enterprise": EMP_ENT,
        "base_code": base_code,
        "tab_num": tab_num,
        "fio": "Вымышленный Сотрудник Полный",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION_LINE,
        "ad_sam": sam,
        "ad_status": "linked" if sam else None,
    }


def test_employee_key_resolved_in_list_for_hr(
    client, hr, test_settings_override, route_override, settings_store
):
    """Привилегированному employee_key заявки и шага — по локальному справочнику.

    Ключ собирается из найденной строки employees (enterprise|base_code|tab_num),
    поэтому в заявке base_code знать не нужно: достаточно предприятия и табельного
    номера. Шаг резолвится по логину исполнителя (assignee)."""
    _employee_store(
        [_employee_row(EMP_TAB, None), _employee_row("Т-000888", EMP_SAM)]
    )
    # Заявка с персональным шагом (исполнитель — вымышленный сотрудник справочника).
    created = _create(
        client,
        hr,
        enterprise=EMP_ENT,
        tab_num=EMP_TAB,
        position=FAKE_POSITION_OTHER,
        steps=[{"owner_group": "SED_STEP_BUH", "assignee": EMP_SAM}],
    )
    assert created.status_code == 201
    body = client.get("/requests", headers=hr).json()[0]
    assert body["employee_key"] == f"{EMP_ENT}|{EMP_BASE}|{EMP_TAB}"
    step = body["steps"][0]
    assert step["employee_key"] == f"{EMP_ENT}|{EMP_BASE}|Т-000888"
    assert step["emp_enterprise"] == EMP_ENT
    assert step["emp_base_code"] == EMP_BASE
    assert step["emp_tab_num"] == "Т-000888"


def test_employee_key_hidden_from_owner(
    client, hr, test_settings_override, route_override, settings_store
):
    """Ключи сотрудников (в них табельный номер — ПДн) — только привилегированным.

    Владелец шага получает employee_key=None и emp_*=None, как enterprise/tab_num."""
    _employee_store(
        [_employee_row(EMP_TAB, None), _employee_row("Т-000888", EMP_SAM)]
    )
    created = _create(
        client,
        hr,
        enterprise=EMP_ENT,
        tab_num=EMP_TAB,
        position=FAKE_POSITION_OTHER,
        steps=[{"owner_group": "SED_STEP_BUH", "assignee": EMP_SAM}],
    )
    rid = created.json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    owner_headers = _headers_for(EMP_SAM, ["SED_STEP_BUH"])
    body = client.get("/requests", headers=owner_headers).json()[0]
    assert body["employee_key"] is None
    assert body["steps"][0]["employee_key"] is None
    assert body["steps"][0]["emp_tab_num"] is None
    # Привилегированному по-прежнему виден.
    assert client.get("/requests", headers=hr).json()[0]["employee_key"] is not None


def test_employee_key_none_on_ambiguous_match(
    client, hr, test_settings_override, route_override, settings_store
):
    """Неоднозначное совпадение ключа не даёт (иначе ссылка увела бы на чужую карточку).

    Тот же табельный номер в двух базах предприятия и тот же логин в двух строках —
    employee_key=None у заявки и у шага."""
    _employee_store(
        [
            _employee_row(EMP_TAB, EMP_SAM),
            _employee_row(EMP_TAB, None, base_code="ZUP_VYM_2"),
            _employee_row("Т-000999", EMP_SAM, base_code="ZUP_VYM_2"),
        ]
    )
    _create(
        client,
        hr,
        enterprise=EMP_ENT,
        tab_num=EMP_TAB,
        position=FAKE_POSITION_OTHER,
        steps=[{"owner_group": "SED_STEP_BUH", "assignee": EMP_SAM}],
    )
    body = client.get("/requests", headers=hr).json()[0]
    assert body["employee_key"] is None
    assert body["steps"][0]["employee_key"] is None
    # emp_* шага тоже пустые — данных о сотруднике не резолвили.
    assert body["steps"][0]["emp_base_code"] is None


def test_employee_key_none_when_not_in_directory(
    client, hr, test_settings_override, route_override, settings_store
):
    """Сотрудника нет в справочнике (или другое предприятие) — ключа нет, ответ 200."""
    _employee_store([_employee_row(EMP_TAB, None)])
    _create(
        client,
        hr,
        enterprise=EMP_ENT,
        tab_num="Т-000555",
        position=FAKE_POSITION_OTHER,
        steps=[{"owner_group": "SED_STEP_BUH", "assignee": EMP_SAM}],
    )
    body = client.get("/requests", headers=hr).json()[0]
    assert body["employee_key"] is None
    assert body["steps"][0]["employee_key"] is None


def test_list_survives_unavailable_directory(
    client, hr, test_settings_override, route_override, settings_store
):
    """Справочник недоступен (EmployeeSyncUnavailable) — список отдаётся, ключей нет."""
    app.dependency_overrides[get_employee_sync_store] = lambda: BrokenEmployeeStore()
    try:
        _create(client, hr, enterprise=EMP_ENT, tab_num=EMP_TAB)
        response = client.get("/requests", headers=hr)
    finally:
        app.dependency_overrides.pop(get_employee_sync_store, None)
    assert response.status_code == 200
    assert response.json()[0]["employee_key"] is None


# Справочник падает не «своим» исключением (например, драйвер БД) — выдача
# списка тоже не должна роняться: справочник здесь мягкая зависимость.
def test_list_survives_broken_directory(
    client, hr, test_settings_override, route_override, settings_store
):
    app.dependency_overrides[get_employee_sync_store] = lambda: ExplodingEmployeeStore()
    try:
        _create(client, hr, enterprise=EMP_ENT, tab_num=EMP_TAB)
        response = client.get("/requests", headers=hr)
    finally:
        app.dependency_overrides.pop(get_employee_sync_store, None)
    assert response.status_code == 200
    assert response.json()[0]["employee_key"] is None


def test_employee_keys_one_query_per_enterprise(
    client, hr, requests_store, test_settings_override, route_override, settings_store
):
    """Резолв пакетный: один вызов find_by_people на предприятие, а не на заявку/шаг."""
    store = CountingEmployeeStore([_employee_row(EMP_TAB, EMP_SAM)])
    app.dependency_overrides[get_employee_sync_store] = lambda: store
    try:
        for tab in (EMP_TAB, "Т-000778"):
            _create(
                client,
                hr,
                enterprise=EMP_ENT,
                tab_num=tab,
                position=FAKE_POSITION_OTHER,
                steps=[{"owner_group": "SED_STEP_BUH", "assignee": EMP_SAM}],
            )
        assert len(requests_store.list_all()) == 2
        body = client.get("/requests", headers=hr).json()
    finally:
        app.dependency_overrides.pop(get_employee_sync_store, None)
    # Один запрос на предприятие с обоими табельными номерами и логином шага.
    assert len(store.calls) == 1
    assert store.calls[0] == (EMP_ENT, [EMP_SAM], [EMP_TAB, "Т-000778"])
    # Ключ есть только у той заявки, чей табельный номер найден.
    assert [item["employee_key"] for item in body] == [f"{EMP_ENT}|{EMP_BASE}|{EMP_TAB}", None]


def test_find_by_people_exact_match_only():
    """Справочник: только точные совпадения (без подстроки), регистр не важен."""
    store = InMemoryEmployeeSyncStore()
    store.upsert_many(
        [
            _employee_row(EMP_TAB, EMP_SAM),
            {"enterprise": "ENT_DRUGOY", "base_code": EMP_BASE, "tab_num": "Т-000777",
             "fio": "Вымышленный Чужой", "department": FAKE_SERVICE, "position": "",
             "ad_sam": EMP_SAM, "ad_status": "linked"},
        ]
    )
    # Точное совпадение по табельному номеру (в другом регистре) — нашёлся.
    assert [row["fio"] for row in store.find_by_people(EMP_ENT, [], [EMP_TAB.lower()])] == [
        "Вымышленный Сотрудник Полный"
    ]
    # Подстрока не совпадение.
    assert store.find_by_people(EMP_ENT, [], ["0007"]) == []
    # Другое предприятие не отдаётся, даже при совпадении номера/логина.
    assert store.find_by_people("ENT_DRUGOY", [], [EMP_TAB])[0]["fio"] == "Вымышленный Чужой"
    # Пустые списки — хранилище не запрашивается вовсе.
    assert store.find_by_people(EMP_ENT, [], []) == []


def test_employee_keys_empty_without_requests():
    """Нет заявок — пустые словари, обращения к справочнику не происходит."""
    assert _employee_keys([]) == ({}, {})


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


# --- Уведомления согласующим на этапах: переход блока, возврат, повтор (W3a) ---

# Вымышленные группы-владельцы шагов и их участники (только для мока AD).
GROUP_BUH = "SED_STEP_BUH"
GROUP_HR = "SED_STEP_HR"
GROUP_DIRECTOR = "SED_STEP_DIRECTOR"
GROUP_ARCHIVE = "SED_STEP_ARCHIVE"
GROUP_OTHER = "SED_STEP_OTHER"
HR_STEP_SAM = "step.kadrovik"
DIRECTOR_SAM = "step.direktor"

MAIL_TEMPLATES_SEED = json.dumps(
    [
        {
            "code": EVENT_ASSIGNED,
            "subject": "Назначена {{ request_id }}",
            "body_html": "<html>{{ fio }} {{ url }}</html>",
        },
        {
            "code": EVENT_RETURNED,
            "subject": "Возврат {{ request_id }}",
            "body_html": "<html>{{ url }}</html>",
        },
        {
            "code": EVENT_CLOSED,
            "subject": "Закрыта {{ request_id }}",
            "body_html": "<html>{{ url }}</html>",
        },
    ],
    ensure_ascii=False,
)


class FakeMailAdReader:
    """Мок AdReader для писем (только чтение): почта по sAMAccountName и состав групп."""

    def __init__(self, groups: dict):
        self._groups = dict(groups)

    def get_user(self, sam: str):
        return SimpleNamespace(
            sam=sam,
            display_name="Вымышленный Участник Группы",
            mail="%s@example.local" % sam,
        )

    def group_members(self, group: str):
        return [
            SimpleNamespace(sam=sam, mail="%s@example.local" % sam, enabled=True)
            for sam in self._groups.get(group, [])
        ]


def _mail_of(sam: str) -> str:
    """Почта участника по правилам FakeMailAdReader (адрес только из AD)."""
    return "%s@example.local" % sam


@pytest.fixture
def mail_queue(tmp_path):
    """Файловая очередь писем (вместо боевой очереди в БД) — что ушло, видно в тесте."""
    queue = FileMailQueue(tmp_path / "mail_queue.json")
    app.dependency_overrides[get_mail_queue] = lambda: queue
    yield queue
    app.dependency_overrides.pop(get_mail_queue, None)


@pytest.fixture
def mail_settings_store():
    """Хранилище настроек писем: mail_templates/smtp_from + предприятия (сид-формат)."""
    store = InMemorySettingsStore(
        {
            "enterprises": SEED_ENTERPRISES,
            "mail_templates": MAIL_TEMPLATES_SEED,
            "smtp_from": json.dumps("sed@example.local"),
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture
def mail_ad_reader():
    """Ридер AD с составом вымышленных групп-владельцев шагов."""
    reader = FakeMailAdReader(
        {GROUP_BUH: [BUH_SAM], GROUP_HR: [HR_STEP_SAM], GROUP_DIRECTOR: [DIRECTOR_SAM]}
    )
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


def _sent(queue) -> list[tuple[str, str]]:
    """Письма очереди как пары (получатель, событие)."""
    return [(message.to, message.event) for message in queue.pending()]


def _notify_skips() -> list[str]:
    """Причины пропущенных уведомлений из журнала аудита (без ПДн)."""
    return [e.detail for e in audit_log.all() if e.action == "notify.skip"]


def _approve(client, rid: str, order: int, headers: dict):
    """Отметка «согласовано» владельцем шага."""
    response = client.post(
        f"/requests/{rid}/steps/{order}/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response


def test_approve_notifies_next_block(
    client, hr, buh_owner, hr_step_owner, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Переход этапа: после отметки последнего шага блока письмо уходит следующему.

    Раньше письмо ставилось только при подаче, поэтому второй блок узнавал о
    назначении лишь из карточки."""
    rid = _create(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]},
        ],
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert _sent(mail_queue) == [(_mail_of(BUH_SAM), EVENT_ASSIGNED)]
    response = _approve(client, rid, 1, buh_owner)
    assert response.json()["status"] == IN_APPROVAL
    assert _sent(mail_queue) == [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(HR_STEP_SAM), EVENT_ASSIGNED),
    ]


def test_parallel_block_notifies_each_step_once(
    client, hr, buh_owner, hr_step_owner, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Параллельный блок: письма по одному на шаг блока и НЕ по одному на отметку.

    Отметка второго шага параллельного блока новых писем не добавляет — иначе
    второй согласующий получал бы дубли."""
    director = _headers_for(DIRECTOR_SAM, [GROUP_DIRECTOR])
    rid = _create(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {
                "mode": "parallel",
                "steps": [{"owner_group": GROUP_HR}, {"owner_group": GROUP_DIRECTOR}],
            },
        ],
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert len(_sent(mail_queue)) == 1
    _approve(client, rid, 1, buh_owner)
    expected = [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(HR_STEP_SAM), EVENT_ASSIGNED),
        (_mail_of(DIRECTOR_SAM), EVENT_ASSIGNED),
    ]
    assert _sent(mail_queue) == expected
    # Отметка первого шага параллельного блока: новых писем нет (второй уже получил).
    _approve(client, rid, 1101, hr_step_owner)
    assert _sent(mail_queue) == expected
    # Последний шаг блока закрывает заявку — уведомлений по-прежнему нет.
    final = _approve(client, rid, 1102, director)
    assert final.json()["status"] == "Согласовано"
    assert _sent(mail_queue) == expected


def test_return_reopens_previous_block_and_notifies(
    client, hr, buh_owner, hr_step_owner, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Возврат: предыдущий блок снова в работе (новый TTL), уведомлён его владелец.

    Заявка остаётся «На согласовании» — возвращающий не отправляет её автору."""
    rid = _create(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]},
        ],
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    _approve(client, rid, 1, buh_owner)
    returned = client.post(
        f"/requests/{rid}/steps/1001/decision",
        json={"decision": "return", "comment": "Вымышленная причина возврата"},
        headers=hr_step_owner,
    )
    assert returned.status_code == 200, returned.text
    body = returned.json()
    assert body["status"] == IN_APPROVAL
    reopened = _step_of(body, 1)
    assert reopened["status"] == "ожидает"
    assert reopened["done_by"] is None
    assert reopened["done_at"] is None
    assert reopened["comment"] is None
    # TTL переоткрытого шага — из route.approval_ttl_days, заново от момента возврата.
    expires_at = datetime.fromisoformat(reopened["expires_at"])
    assert expires_at > _utcnow() + timedelta(days=route_override.approval_ttl_days - 1)
    assert _step_of(body, 1001)["status"] == "возвращен"
    assert _sent(mail_queue) == [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(HR_STEP_SAM), EVENT_ASSIGNED),
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
    ]


def test_return_then_reapprove_reopens_returned_step(
    client, hr, buh_owner, hr_step_owner, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Возврат отыгран: повторное согласование предыдущего блока снова открывает
    возвращённый шаг (новый TTL) и уведомляет его владельца — заявка не зависает."""
    rid = _create(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]},
        ],
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    _approve(client, rid, 1, buh_owner)
    assert client.post(
        f"/requests/{rid}/steps/1001/decision",
        json={"decision": "return", "comment": "Вымышленная причина возврата"},
        headers=hr_step_owner,
    ).status_code == 200
    again = _approve(client, rid, 1, buh_owner)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["status"] == IN_APPROVAL
    step = _step_of(body, 1001)
    assert step["status"] == "ожидает"
    assert step["done_by"] is None
    assert step["comment"] is None
    expires_at = datetime.fromisoformat(step["expires_at"])
    assert expires_at > _utcnow() + timedelta(days=route_override.approval_ttl_days - 1)
    assert _sent(mail_queue) == [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(HR_STEP_SAM), EVENT_ASSIGNED),
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(HR_STEP_SAM), EVENT_ASSIGNED),
    ]


def test_return_without_previous_block_goes_to_rework(
    client, hr, buh_owner, mail_queue, mail_settings_store, mail_ad_reader,
    test_settings_override, route_override
):
    """Возвращать некуда (первый блок) — заявка на доработку, письмо «возврат» автору."""
    rid = _create_and_submit(client, hr)
    returned = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "return", "comment": "Вымышленная причина возврата"},
        headers=buh_owner,
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["status"] == "На доработке"
    assert _sent(mail_queue) == [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(hr["X-Mock-Sam"]), EVENT_RETURNED),
    ]


def test_reissue_notifies_step_owner(
    client, hr, buh_owner, requests_store, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Повтор просроченного шага (reissue) — письмо «назначена» его владельцу."""
    rid = _create(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]}]
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    requests_store.get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    assert client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=buh_owner
    ).status_code == 410
    assert client.post(f"/requests/{rid}/steps/1/reissue", headers=hr).status_code == 200
    assert _sent(mail_queue) == [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
    ]


def test_notify_skip_audited_without_ad_reader(
    client, hr, mail_queue, mail_settings_store, test_settings_override, route_override
):
    """Ридер AD недоступен (ad_reader=None) — письмо пропущено, notify.skip в аудите.

    Отметка согласования при этом работает (уведомление не роняет решение)."""
    rid = _create(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]}]
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert _sent(mail_queue) == []
    assert _notify_skips() == ["assigned no_ad_reader"]
    approve = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=_headers_for(BUH_SAM, [GROUP_BUH]),
    )
    assert approve.status_code == 200, approve.text


def test_notify_skip_audited_without_mail_template(
    client, hr, mail_queue, mail_settings_store, mail_ad_reader,
    test_settings_override, route_override
):
    """Адресат есть, шаблона письма нет — notify.skip no_template, очередь пуста."""
    mail_settings_store._values.pop("mail_templates")
    rid = _create(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]}]
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert _sent(mail_queue) == []
    assert _notify_skips() == ["assigned no_template"]


def test_notify_skip_audited_without_recipients(
    client, hr, mail_queue, mail_settings_store, mail_ad_reader,
    test_settings_override, route_override
):
    """Группы шага нет в AD (участники не резолвятся) — notify.skip no_recipients."""
    rid = _create(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"owner_group": GROUP_ARCHIVE}]}]
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert _sent(mail_queue) == []
    assert _notify_skips() == ["assigned no_recipients"]


# --- Письмо «закрыта» (EVENT_CLOSED) автору при завершении заявки ---

def test_finish_notifies_author_closed(
    client, hr, buh_owner, mail_queue, mail_settings_store, mail_ad_reader,
    test_settings_override, route_override
):
    """К исполнению → Завершено: автору уходит письмо «закрыта» (EVENT_CLOSED).

    До finish писем «закрыта» нет — откат/возврат закрытием не считаются
    (точные списки _sent в тестах возврата это тоже фиксируют)."""
    rid = _create(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]}]
    ).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    assert _approve(client, rid, 1, buh_owner).json()["status"] == "Согласовано"
    assert client.post(f"/requests/{rid}/to-execution", headers=hr).status_code == 200
    assert not [event for _, event in _sent(mail_queue) if event == EVENT_CLOSED]

    finished = client.post(f"/requests/{rid}/finish", headers=hr)
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "Завершено"
    assert _sent(mail_queue) == [
        (_mail_of(BUH_SAM), EVENT_ASSIGNED),
        (_mail_of(hr["X-Mock-Sam"]), EVENT_CLOSED),
    ]


# --- Отзыв заявки: /withdraw и его синоним /cancel (issue_report п.1) ---


def test_cancel_endpoint_sets_revoked_not_done(
    client, hr, buh_owner, test_settings_override, route_override
):
    """Отзыв заявки → «Отозвано» (не «Завершено»): /withdraw и /cancel.

    Пункт 1 отчёта об ошибках: отозванная заявка не должна попадать в статус
    завершения. Статус «Завершено» — строка контракта, он же «done» в коде БД,
    поэтому проверяем именно его (счётчик папки «Завершённые» его и считает)."""
    # Методы: POST (как остальные действия) и PATCH (так зовёт QA-проверка
    # `curl -X PATCH /requests/{id}/cancel` из отчёта об ошибках).
    for method in ("post", "patch"):
        rid = _create_and_submit(client, hr)
        response = getattr(client, method)(f"/requests/{rid}/cancel", headers=hr)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "Отозвано"
        # Повторный отзыв закрытой заявки — 409, статус не меняется.
        again = getattr(client, method)(f"/requests/{rid}/cancel", headers=hr)
        assert again.status_code == 409
        assert client.get(f"/requests/{rid}", headers=hr).json()["status"] == "Отозвано"
        # Отозванная заявка закрыта: маршрут не правится.
        assert client.patch(
            f"/requests/{rid}/steps", json={"steps": [{"owner_group": GROUP_BUH}]}, headers=hr
        ).status_code == 409


def test_cancel_forbidden_for_non_hr(client, hr, buh_owner, test_settings_override, route_override):
    """Отзыв — только разрешённой группе (403), статус заявки не меняется."""
    rid = _create_and_submit(client, hr)
    denied = client.post(f"/requests/{rid}/cancel", headers=buh_owner)
    assert denied.status_code == 403
    assert client.get(f"/requests/{rid}", headers=hr).json()["status"] == "На согласовании"


def test_withdrawn_request_counted_in_done_folder(
    client, hr, test_settings_override, route_override
):
    """Отозванная заявка попадает в папку «Завершённые», а не теряется из списка."""
    rid = _create_and_submit(client, hr)
    assert client.post(f"/requests/{rid}/cancel", headers=hr).status_code == 200
    folders = {f["id"]: f["count"] for f in client.get("/folders", headers=hr).json()}
    assert folders["done"] == 1
    assert folders["agreement"] == 0
    assert [r["id"] for r in client.get("/requests", headers=hr).json()] == [rid]


# --- Отказ по шагу: заявка инициатору на доработку (issue_report п.6/п.7) ---


def test_reject_goes_to_rework_and_keeps_request(
    client, hr, buh_owner, hr_step_owner, requests_store,
    test_settings_override, route_override
):
    """Отказ по шагу второго блока: заявка на доработку, запись сохранена.

    Маршрут сам не переоткрывается — решает инициатор. Согласие первого блока
    при этом сохраняется (шаг остаётся «согласован»), а в папке «На доработке»
    заявка засчитана, то есть из списка не исчезает."""
    rid = _create_and_submit(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]},
        ],
    )
    _approve(client, rid, 1, buh_owner)
    rejected = client.post(
        f"/requests/{rid}/steps/1001/decision",
        json={"decision": "reject", "comment": "Вымышленная причина отказа"},
        headers=hr_step_owner,
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "На доработке"
    # Отклонённый шаг помечен «отклонен» и хранит причину; первый блок закрыт.
    assert _step_of(body, 1001)["status"] == "отклонен"
    assert _step_of(body, 1001)["comment"] == "Вымышленная причина отказа"
    assert _step_of(body, 1)["status"] == "согласован"
    # Запись на месте и учтена в папке «На доработке».
    assert requests_store.get(rid).status == "На доработке"
    folders = {f["id"]: f["count"] for f in client.get("/folders", headers=hr).json()}
    assert folders["revision"] == 1
    assert folders["agreement"] == 0
    assert [r["id"] for r in client.get("/requests", headers=hr).json()] == [rid]


def test_submit_after_rework_returns_to_who_rejected(
    client, hr, buh_owner, hr_step_owner, requests_store, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Подача из «На доработке» возвращает заявку тому, кто её вернул.

    Отклонённый шаг снова «ожидает» с новым сроком, его владелец уведомлён,
    согласованные шаги не трогаются."""
    rid = _create_and_submit(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]},
        ],
    )
    _approve(client, rid, 1, buh_owner)
    assert client.post(
        f"/requests/{rid}/steps/1001/decision",
        json={"decision": "reject", "comment": "Вымышленная причина отказа"},
        headers=hr_step_owner,
    ).status_code == 200
    sent_before = len(_sent(mail_queue))

    submitted = client.post(f"/requests/{rid}/submit", headers=hr)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == IN_APPROVAL
    reopened = _step_of(body, 1001)
    assert reopened["status"] == "ожидает"
    assert reopened["done_by"] is None
    # Причина отказа остаётся в карточке — это суть доработки.
    assert reopened["comment"] == "Вымышленная причина отказа"
    assert _step_of(body, 1)["status"] == "согласован"
    # Отметка ответственного (reject) тоже сохранена: иначе согласовавшийся заново
    # не смог бы оставить пометку, а прежняя отметка исчезла бы из истории.
    stored_step = requests_store.get(rid).steps[1]
    assert [item["decision"] for item in stored_step.approvals] == ["reject"]
    assert datetime.fromisoformat(reopened["expires_at"]) > _utcnow() + timedelta(
        days=route_override.approval_ttl_days - 1
    )
    # Возврат ушёл тому, кто вернул, — не автору и не первому блоку.
    assert _sent(mail_queue)[sent_before:] == [(_mail_of(HR_STEP_SAM), EVENT_ASSIGNED)]


def test_submit_after_rework_reopens_expired_step(
    client, hr, buh_owner, requests_store, mail_queue, mail_settings_store,
    mail_ad_reader, test_settings_override, route_override
):
    """Просроченный шаг после доработки снова ожидает, а не остаётся «просрочен».

    Иначе заявка ушла бы в «На согласовании» вообще без доступного шага
    (can_act у просроченного шага всегда false) и висела до ручного reissue."""
    rid = _create_and_submit(client, hr)
    requests_store.get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    expired = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    )
    assert expired.status_code == 410
    # Истечение срока кладёт заявку на доработку (тело 410 — это detail, не карточка).
    assert requests_store.get(rid).status == "На доработке"

    submitted = client.post(f"/requests/{rid}/submit", headers=hr)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == IN_APPROVAL
    step = _step_of(body, 1)
    assert step["status"] == "ожидает"
    # can_act считается для текущего пользователя: смотрим карточку глазами
    # владельца шага, у ОК он всегда False.
    owner_view = client.get(f"/requests/{rid}", headers=buh_owner).json()
    assert _step_of(owner_view, 1)["can_act"] is True
    assert datetime.fromisoformat(step["expires_at"]) > _utcnow()


def test_patch_steps_keeps_stage_snapshot_for_same_executor(
    client, hr, requests_store, test_settings_override, route_override
):
    """Правка маршрута не обнуляет текст шага для печати.

    Печать идёт по снимку выданной заявки, а пересборка маршрута создаёт шаги
    заново; снимок этапа переносится на шаг с тем же исполнитетелем и НЕ
    переносится, если исполнителя сменили (чужой текст хуже никакого)."""
    rid = _create_and_submit(
        client, hr, steps=[{"owner_group": GROUP_BUH}, {"owner_group": GROUP_HR}]
    )
    before = requests_store.get(rid).steps[0]
    before.stage_title = "Вымышленный этап"
    before.stage_lines = ["Вымышленный текст этапа"]

    same = client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": GROUP_BUH}, {"owner_group": GROUP_HR}]},
        headers=hr,
    )
    assert same.status_code == 200, same.text
    after_same = requests_store.get(rid).steps[0]
    assert after_same.stage_title == "Вымышленный этап"
    assert after_same.stage_lines == ["Вымышленный текст этапа"]

    changed = client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": GROUP_BUH}, {"owner_group": GROUP_OTHER}]},
        headers=hr,
    )
    assert changed.status_code == 200, changed.text
    steps = sorted(requests_store.get(rid).steps, key=lambda s: s.order)
    # Исполнителя первого шага сменили — его снимок не переносится.
    assert steps[1].owner_group == GROUP_OTHER
    assert steps[1].stage_lines == []


def test_submit_after_rework_resets_expired_step_approvals(
    client, hr, buh_owner, requests_store, test_settings_override, route_override
):
    """Отметки истёкшего круга не переносятся на новый срок шага.

    Частично согласованный шаг просрочился: после подачи он снова ожидает, но
    согласия прежнего круга не осталось — иначе прежний согласовавший получил бы
    409 «Вы уже согласовали этот шаг», а шаг закрылся бы по старой отметке."""
    rid = _create_and_submit(
        client, hr, blocks=[{"mode": "sequential", "steps": [{"sam": BUH_SAM}]}]
    )
    requests_store.get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    assert client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    ).status_code == 410
    assert requests_store.get(rid).status == "На доработке"
    # Отметка прежнего круга кладётся прямо в хранилище: собирать такой шаг через
    # API нельзя (параллельный блок закрывает шаг первым же согласованием, а
    # последовательный имеет одного ответственного), а проверяем тут именно
    # переоткрытие шага по `submit`.
    expired_step = requests_store.get(rid).steps[0]
    expired_step.approvals = [
        {
            "sam": BUH_SAM,
            "at": _utcnow().isoformat(),
            "decision": "approve",
            "comment": "Вымышленное согласие прежнего круга",
        }
    ]
    expired_step.comment = "Вымышленное согласие прежнего круга"

    submitted = client.post(f"/requests/{rid}/submit", headers=hr)
    assert submitted.status_code == 200, submitted.text
    stored_step = requests_store.get(rid).steps[0]
    assert stored_step.status == "ожидает"
    assert stored_step.approvals == []
    assert stored_step.comment is None
    # Прежний согласовавший может согласовать заново (без 409).
    again = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "Согласовано"


def test_submit_after_rework_uses_new_route_after_patch(
    client, hr, buh_owner, hr_step_owner, test_settings_override, route_override
):
    """Маршрут, изменённый на доработке, ведёт по новым шагам, а не к отклонившему.

    Правка маршрута (PATCH /requests/{id}/steps) переводит заявку в Черновик, где
    ветка переоткрытия отклонённых шагов не выполняется: заявка уходит первому
    согласующему нового маршрута."""
    rid = _create_and_submit(
        client,
        hr,
        blocks=[
            {"mode": "sequential", "steps": [{"owner_group": GROUP_BUH}]},
            {"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]},
        ],
    )
    _approve(client, rid, 1, buh_owner)
    assert client.post(
        f"/requests/{rid}/steps/1001/decision",
        json={"decision": "reject", "comment": "Вымышленная причина отказа"},
        headers=hr_step_owner,
    ).status_code == 200
    patched = client.patch(
        f"/requests/{rid}/steps",
        json={"blocks": [{"mode": "sequential", "steps": [{"owner_group": GROUP_HR}]}]},
        headers=hr,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "Черновик"

    submitted = client.post(f"/requests/{rid}/submit", headers=hr)
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == IN_APPROVAL
    # Отклонённый шаг остался отклонённым — по нему заявка не идёт.
    assert _step_of(body, 1001)["status"] == "отклонен"
    # Текущим стал первый шаг нового маршрута: его владелец снова может отметить.
    assert _step_of(body, 2001)["status"] == "ожидает"
    owner_view = client.get(f"/requests/{rid}", headers=hr_step_owner).json()
    assert _step_of(owner_view, 2001)["can_act"] is True

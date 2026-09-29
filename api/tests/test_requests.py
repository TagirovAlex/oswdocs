# Тесты заявок и маршрутов (волна B2): шаблон/ручной, TTL, комментарии, чужие шаги.
# Все ПДн вымышленные; группы подменяются оверрайдом get_settings.

from __future__ import annotations

import base64
import os
import sys
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    _REQUESTS,
    _utcnow,
    get_route_settings,
    reset_state_for_tests,
)
from app.requests import RouteSettings, RouteStepTemplate, RouteTemplate  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные служба/должности (не продовые значения).
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION_LINE = "Старший вымышленный кассир"
FAKE_POSITION_OTHER = "Вымышленный архивариус"
FAKE_ENTERPRISE = "Вымышленное предприятие"


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
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(autouse=True)
def clean_state():
    """Чистое хранилище и аудит на каждый тест."""
    reset_state_for_tests()
    audit_log.clear_for_tests()
    yield
    reset_state_for_tests()
    audit_log.clear_for_tests()


@pytest.fixture
def hr() -> dict:
    """Заголовки ОК (разрешенная группа для конструктора)."""
    return _headers_for("ok.vymyshlennaya", ["SED_HR"])


@pytest.fixture
def buh_owner() -> dict:
    """Заголовки владельца первого шага шаблона."""
    return _headers_for("step.buhgalter", ["SED_STEP_BUH"])


@pytest.fixture
def other_owner() -> dict:
    """Заголовки чужой группы (не владелец первого шага)."""
    return _headers_for("step.chuzhoi", ["SED_STEP_OTHER"])


def _create(client, headers, **kw) -> dict:
    """Создание заявки с вымышленными полями по умолчанию."""
    body = {
        "enterprise": FAKE_ENTERPRISE,
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


def test_expired_reissue(client, hr, buh_owner, test_settings_override, route_override):
    """Просрочка TTL → 410 и На доработке, повтор (reissue) возвращает в работу."""
    rid = _create(client, hr).json()["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr).status_code == 200
    # Искусственно состариваем шаг мимо TTL (хранилище in-memory — стенд хранит в БД).
    _REQUESTS[rid].steps[0].expires_at = _utcnow() - timedelta(days=1)
    late = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "approve"},
        headers=buh_owner,
    )
    assert late.status_code == 410
    assert _REQUESTS[rid].status == "На доработке"
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

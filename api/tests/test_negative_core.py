# Негативные тесты C1-ядро (волна C, скилл qa-sed): 403, чужие ресурсы,
# дубли ФИО без автосклейки, просрочка TTL (410 + повтор), полнота audit_log,
# запрет записи в 1С/AD. Только моки, живых LDAP/HTTP нет; все ПДн вымышлены.
# Прогон локально: pytest api/tests/test_negative_core.py -q (venv ./temp/.venv).
# Прогон на стенде (ВМ): пометка «на ВМ» — живой bind/OData, сессии 15–20 мин,
# TLS/HSTS, compose up (см. ./temp/OFFLINE_GAPS.md, deploy/ЧЕК-ЛИСТ_ИТ.md).
# conftest.py не правим: фикстуры client/hr/admin/owner/nogroup/noauth — оттуда,
# подмены настроек/клиента/ридера/маршрута — локально здесь с возвратом.

from __future__ import annotations

import base64
import inspect
import json
import os
import re
import sys
import urllib.parse
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdReader, AdReaderSettings, InMemoryCache  # noqa: E402
from app.audit import AuditLogger, audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.employees import get_ad_reader, get_onec_client  # noqa: E402
from app.link import clear_for_tests as clear_links  # noqa: E402
from app.link import get_memory_links_store  # noqa: E402
from app.link_store import get_links_store  # noqa: E402
from app.main import app  # noqa: E402
from app.onec_client import HttpResult, OneCBaseConfig, OneCClient  # noqa: E402
from app.requests import (  # noqa: E402
    _utcnow,
    get_memory_requests_store,
    get_route_settings,
    reset_state_for_tests,
)
from app.requests import RouteSettings  # noqa: E402
from app.requests_store import get_requests_store  # noqa: E402

# --- Тестовые группы (имена тестовые; продовые — только через env/БД) ---
TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"
BUH_GROUP = "SED_STEP_BUH"
OTHER_GROUP = "SED_STEP_OTHER"

# --- Вымышленные предприятие и персоналии (не реальные данные) ---
ENT = "Предприятие-Тест-Север"
FIO_IVAN = "Сказочников Иван Тестович"
FIO_DUBL = "Одинаков Дублий Повторович"
FAKE_POSITION = "Старший вымышленный кассир"


def _b64(value: str) -> str:
    """Кодирование кириллицы для мок-заголовков (как в conftest)."""
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


def _bases() -> dict:
    """Две мок-базы одного предприятия (таб. номера не пересекаются)."""
    return {
        "zup_t1": OneCBaseConfig(
            code="zup_t1", enterprise=ENT,
            url="https://1c-mock.local/t1", user="reader", secret="s1",
        ),
        "zup_t2": OneCBaseConfig(
            code="zup_t2", enterprise=ENT,
            url="https://1c-mock.local/t2", user="reader", secret="s2",
        ),
    }


def _card_row(tab: str, fio: str) -> dict:
    """Строка справочника Catalog_Сотрудники (вымышленная): таб.№ = Code,
    ФИО = Description; подразделение/должность/приём — в регистре (см. transport)."""
    return {
        "Ref_Key": "ref-" + tab, "Code": tab, "Description": fio,
        "ГоловнаяОрганизация_Key": ENT,
    }


def _rows_t1() -> dict[str, dict]:
    """Записи базы t1: Иван + два дубля одного ФИО."""
    return {
        "001": _card_row("001", FIO_IVAN),
        "003": _card_row("003", FIO_DUBL),
        "004": _card_row("004", FIO_DUBL),
    }


class FakeTransport:
    """Мок-HTTP 1С: база t1 (Иван + два дубля), база t2 (пусто).

    URL от клиента: $filter=Code eq 'NNN' / substringof('...', Description)
    (справочник) и регистр кадровых данных по Сотрудник_Key (второй запрос
    карточки) — ответы по фактическому $filter."""

    @staticmethod
    def _respond(rows: dict[str, dict], filter_str: str) -> HttpResult:
        if "substringof" in filter_str:
            m = re.search(r"substringof\('([^']*)'", filter_str)
            needle = (m.group(1) if m else "").lower()
            hit = [row for row in rows.values() if needle in row["Description"].lower()]
            return HttpResult(status=200, body=json.dumps({"value": hit}, ensure_ascii=False))
        m = re.search(r"eq '([^']*)'", filter_str)
        tab = m.group(1) if m else ""
        if tab in rows:
            return HttpResult(status=200, body=json.dumps({"value": [rows[tab]]}, ensure_ascii=False))
        return HttpResult(status=200, body=json.dumps({"value": []}, ensure_ascii=False))

    def get(self, url, headers, timeout):
        parsed = urllib.parse.urlparse(url)
        filter_str = urllib.parse.unquote(
            urllib.parse.parse_qs(parsed.query).get("$filter", [""])[0]
        )
        if "InformationRegister" in url:
            m = re.search(r"guid'([^']*)'", filter_str)
            ref = m.group(1) if m else ""
            tab = ref[len("ref-"):] if ref.startswith("ref-") else ""
            if tab in _rows_t1():
                return HttpResult(
                    status=200,
                    body=json.dumps(
                        {
                            "value": [
                                {
                                    "Сотрудник_Key": ref,
                                    "ТекущееПодразделение": {"Description": "Цех Тестовый"},
                                    "ТекущаяДолжность": {"Description": FAKE_POSITION},
                                    "ДатаПриема": "2023-01-15",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            return HttpResult(status=200, body=json.dumps({"value": []}))
        if "/t1/" in url:
            return self._respond(_rows_t1(), filter_str)
        if "/t2/" in url:
            return self._respond({}, filter_str)
        return HttpResult(status=404, body="{}")


def _ad_entry(sam: str, fio: str) -> dict:
    """Вымышленная запись AD."""
    return {
        "dn": "CN=%s,OU=SED,DC=example,DC=local" % fio,
        "sAMAccountName": sam, "displayName": fio, "manager": "",
        "memberOf": [], "department": "Цех Тестовый", "title": FAKE_POSITION,
        "mail": "%s@example.local" % sam, "userAccountControl": 512,
    }


class FakeGateway:
    """Фейк шлюза LDAP (только чтение, вымышленные записи)."""

    def __init__(self, entries):
        self._by_sam = {e["sAMAccountName"].lower(): dict(e) for e in entries}
        self._by_dn = {e["dn"].lower(): dict(e) for e in entries}

    def bind(self):
        return None

    def search_user_by_sam(self, sam):
        found = self._by_sam.get(sam.strip().lower())
        return dict(found) if found else None

    def search_user_by_dn(self, dn):
        found = self._by_dn.get(dn.strip().lower())
        return dict(found) if found else None


def _reader(entries) -> AdReader:
    """Фейк-ридер AD с тестовыми настройками (LDAPS, BASE_DN из настроек)."""
    settings = AdReaderSettings(
        ad_url="ldaps://mock.local:636",
        base_dn="OU=SED,DC=example,DC=local",
        reader_dn="CN=sed-reader,OU=SED,DC=example,DC=local",
        cache_ttl_seconds=300, timeout_seconds=5.0,
    )
    return AdReader(settings=settings, gateway=FakeGateway(entries), cache=InMemoryCache())


@pytest.fixture
def neg_settings():
    """Тестовые группы + возврат подмены после теста."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED, ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR, STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def route_single():
    """Маршрут без шаблонов: заявки создаются ручным конструктором (1 шаг)."""
    route = RouteSettings(approval_ttl_days=7)
    app.dependency_overrides[get_route_settings] = lambda: route
    yield route
    app.dependency_overrides.pop(get_route_settings, None)


@pytest.fixture
def neg_mocks(neg_settings):
    """Мок-клиент 1С + фейк-ридер AD; состояние заявок/связок чистое."""
    client = OneCClient(_bases(), transport=FakeTransport(), failure_threshold=100)
    reader = _reader([_ad_entry("t.ivan", FIO_IVAN), _ad_entry("t.dubl", FIO_DUBL)])
    app.dependency_overrides[get_onec_client] = lambda: client
    app.dependency_overrides[get_ad_reader] = lambda: reader
    app.dependency_overrides[get_requests_store] = lambda: get_memory_requests_store()
    app.dependency_overrides[get_links_store] = lambda: get_memory_links_store()
    reset_state_for_tests()
    clear_links()
    yield {"client": client, "reader": reader}
    app.dependency_overrides.pop(get_onec_client, None)
    app.dependency_overrides.pop(get_ad_reader, None)
    app.dependency_overrides.pop(get_requests_store, None)
    app.dependency_overrides.pop(get_links_store, None)
    reset_state_for_tests()
    clear_links()


def _create_request(client, hr_headers, steps=None) -> dict:
    """Создание заявки ручным конструктором (вымышленные поля)."""
    body = {
        "enterprise": ENT, "fio": "Вымышленный Сотрудник Полный",
        "tab_num": "В-0001",
        "department": "Служба вымышленного учета", "position": FAKE_POSITION,
        "steps": steps or [{"owner_group": BUH_GROUP}],
    }
    response = client.post("/requests", json=body, headers=hr_headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- 403: нет группы / нет логина ---

def test_nogroup_403_everywhere(client, nogroup_headers, neg_mocks, route_single):
    """Без разрешающих групп — 403 на всех значимых эндпоинтах (ПДн не светят)."""
    cases = [
        ("GET", "/me", {}, None),
        ("GET", "/auth/me", {}, None),
        ("GET", "/employees", {"enterprise": ENT, "q": "Иван"}, None),
        ("GET", "/employees/card", {"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"}, None),
        ("GET", "/requests", {}, None),
        ("GET", "/link_1c_ad", {"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"}, None),
    ]
    for method, path, params, _ in cases:
        response = client.request(method, path, params=params, headers=nogroup_headers)
        assert response.status_code == 403, (method, path, response.text)
    posts = [
        ("/requests", {"enterprise": ENT, "tab_num": "В-1",
                       "department": "С", "position": FAKE_POSITION,
                       "steps": [{"owner_group": BUH_GROUP}]}),
        ("/link_1c_ad", {"enterprise": ENT, "base_code": "zup_t1",
                         "tab_num": "001", "sam": "t.ivan"}),
    ]
    for path, payload in posts:
        response = client.post(path, json=payload, headers=nogroup_headers)
        assert response.status_code == 403, (path, response.text)


def test_noauth_401_everywhere(client, noauth_headers, neg_mocks):
    """Без логина — 401 (не 403 и не 200 с данными)."""
    for path in ("/me", "/auth/me", "/requests"):
        response = client.get(path, headers=noauth_headers)
        assert response.status_code == 401, (path, response.text)


# --- 403: чужой ресурс (ролевая изоляция) ---

def test_owner_cannot_use_hr_constructor(client, hr_headers, owner_headers, neg_mocks, route_single):
    """Владелец не создает заявки, не правит маршрут, не вяжет 1С-AD, не перевыпускает."""
    manual = client.post(
        "/requests",
        json={"enterprise": ENT, "fio": "Вымышленный Сотрудник Полный",
              "tab_num": "В-9", "department": "С",
              "position": FAKE_POSITION, "steps": [{"owner_group": BUH_GROUP}]},
        headers=owner_headers,
    )
    assert manual.status_code == 403
    rid = _create_request(client, hr_headers)["id"]
    assert client.patch(
        f"/requests/{rid}/steps", json={"steps": [{"owner_group": OTHER_GROUP}]},
        headers=owner_headers,
    ).status_code == 403
    assert client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=owner_headers,
    ).status_code == 403
    assert client.post(f"/requests/{rid}/steps/1/reissue", headers=owner_headers).status_code == 403


def test_foreign_request_and_step_403(client, hr_headers, neg_mocks, route_single):
    """Чужая заявка: в списке владельца ее нет, чтение — 403, отметка — 403."""
    rid = _create_request(client, hr_headers)["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    stranger = _headers_for("step.chuzhoi", [OTHER_GROUP])
    mine = client.get("/requests", headers=stranger).json()
    assert all(r["id"] != rid for r in mine)
    assert client.get(f"/requests/{rid}", headers=stranger).status_code == 403
    assert client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=stranger,
    ).status_code == 403


def test_personal_assignee_only_himself(client, hr_headers, neg_mocks, route_single):
    """Персональный исполнитель (замена руководителя): коллега из той же группы — 403."""
    rid = _create_request(
        client, hr_headers,
        steps=[{"owner_group": BUH_GROUP, "resolver": "ad_direct_manager"}],
    )["id"]
    # Без manager assignee пуст — задаем через PATCH с заменой руководителя.
    patched = client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": BUH_GROUP, "resolver": "ad_direct_manager"}],
              "manager": "step.sidorov", "reason": "Замена руководителя"},
        headers=hr_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["steps"][0]["assignee"] == "step.sidorov"
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    colleague = _headers_for("step.kollega", [BUH_GROUP])
    denied = client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=colleague,
    )
    assert denied.status_code == 403


# --- Дубли ФИО: без автосклейки, флаг ручной сверки ---

def test_duplicates_no_auto_merge(client, hr_headers, neg_mocks):
    """Дубли одного ФИО: обе записи отдельно, ключи разные, склейки нет."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Одинаков"}, headers=hr_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert {i["tab_num"] for i in body["items"]} == {"003", "004"}
    assert len({i["key"] for i in body["items"]}) == 2
    assert body["needs_manual_review"] is True
    assert all(i["needs_manual_review"] is True for i in body["items"])


def test_duplicate_link_still_manual_review(client, hr_headers, neg_mocks):
    """Связка дубля создается (201), но требует ручной сверки; истина — 1С."""
    response = client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "003", "sam": "t.dubl"},
        headers=hr_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["link"]["needs_manual_review"] is True
    assert body["truth_source"] == "1c"
    assert body["snapshot_1c"]["fio"] == FIO_DUBL


# --- Просрочка TTL: 410 + повтор ---

def test_ttl_expired_410_and_reissue(client, hr_headers, owner_headers, neg_mocks, route_single):
    """Решение после TTL — 410 и «На доработке»; повтор возвращает в работу."""
    rid = _create_request(client, hr_headers)["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    get_memory_requests_store().get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    late = client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=owner_headers,
    )
    assert late.status_code == 410
    assert get_memory_requests_store().get(rid).status == "На доработке"
    assert any(e.action == "step.expired" for e in audit_log.all())
    # Повтор чужой группе запрещен, владельцу запрещен — только ОК.
    stranger = _headers_for("step.chuzhoi", [OTHER_GROUP])
    assert client.post(f"/requests/{rid}/steps/1/reissue", headers=stranger).status_code in (401, 403)
    again = client.post(f"/requests/{rid}/steps/1/reissue", headers=hr_headers)
    assert again.status_code == 200
    assert again.json()["status"] == "На согласовании"
    assert again.json()["steps"][0]["status"] == "ожидает"
    # Повтор не просроченного шага — 409.
    assert client.post(f"/requests/{rid}/steps/1/reissue", headers=hr_headers).status_code == 409
    # После повтора отметка проходит.
    done = client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=owner_headers,
    )
    assert done.status_code == 200


def test_decision_comment_rules_422(client, hr_headers, owner_headers, neg_mocks, route_single):
    """Отказ/возврат без комментария — 422; согласие с флагом шага — 422."""
    rid = _create_request(
        client, hr_headers, steps=[{"owner_group": BUH_GROUP, "require_comment": True}],
    )["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    assert client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=owner_headers,
    ).status_code == 422
    assert client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "reject"}, headers=owner_headers,
    ).status_code == 422
    ok = client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "reject", "comment": "Вымышленная причина отказа"},
        headers=owner_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "Отклонено"


# --- Полнота audit_log: каждое значимое действие пишет событие ---

def test_audit_completeness_lifecycle(client, hr_headers, owner_headers, neg_mocks, route_single):
    """Счастливый путь пишет все значимые события; update/delete у журнала нет."""
    assert not hasattr(AuditLogger, "update")
    assert not hasattr(AuditLogger, "delete")
    client.get("/me", headers=hr_headers)
    client.get("/auth/me", headers=hr_headers)
    client.get("/employees", params={"enterprise": ENT, "q": "Сказочников"}, headers=hr_headers)
    client.get(
        "/employees/card",
        params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
        headers=hr_headers,
    )
    client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=hr_headers,
    )
    client.get(
        "/link_1c_ad",
        params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
        headers=hr_headers,
    )
    rid = _create_request(client, hr_headers)["id"]
    client.post(f"/requests/{rid}/submit", headers=hr_headers)
    client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=owner_headers,
    )
    client.post(f"/requests/{rid}/to-execution", headers=hr_headers)
    client.post(f"/requests/{rid}/finish", headers=hr_headers)
    actions = [e.action for e in audit_log.all()]
    for expected in (
        "me.read", "employees.search", "employees.card.read", "link.create", "link.read",
        "request.create", "request.submit", "step.approve",
        "request.to_execution", "request.finish",
    ):
        assert expected in actions, (expected, actions)


def test_audit_branch_actions(client, hr_headers, owner_headers, neg_mocks, route_single):
    """Ветви тоже в аудите: возврат, отказ, отзыв, правка шагов, просрочка+повтор."""
    rid = _create_request(client, hr_headers)["id"]
    client.patch(
        f"/requests/{rid}/steps",
        json={"steps": [{"owner_group": BUH_GROUP}], "reason": "Вымышленная причина правки"},
        headers=hr_headers,
    )
    client.post(f"/requests/{rid}/submit", headers=hr_headers)
    get_memory_requests_store().get(rid).steps[0].expires_at = _utcnow() - timedelta(days=1)
    assert client.post(
        f"/requests/{rid}/steps/1/decision", json={"decision": "approve"}, headers=owner_headers,
    ).status_code == 410
    client.post(f"/requests/{rid}/steps/1/reissue", headers=hr_headers)
    client.post(
        f"/requests/{rid}/steps/1/decision",
        json={"decision": "return", "comment": "Вымышленная причина возврата"},
        headers=owner_headers,
    )
    client.post(f"/requests/{rid}/withdraw", headers=hr_headers)
    actions = {e.action for e in audit_log.all()}
    for expected in ("steps.patch", "request.submit", "step.expired",
                     "step.reissue", "step.return", "request.withdraw"):
        assert expected in actions, (expected, sorted(actions))


# --- «Нет записи в 1С/AD»: только чтение, флаг выключен ---

def test_no_write_to_1c_ad_readonly(neg_mocks, monkeypatch):
    """Клиент 1С — только GET; в AD-ридере нет add/modify/delete/disable; флаги false."""
    # Флаг из env обязан оставаться выключенным (дефолт настроек тоже False).
    assert Settings().AD_WRITE_ENABLED is False
    monkeypatch.setenv("AD_WRITE_ENABLED", "false")
    from app.ad_reader import ensure_read_only as ad_guard

    ad_guard()
    from app.config import get_settings as _gs

    _gs.cache_clear() if hasattr(_gs, "cache_clear") else None
    settings = Settings()
    settings.ensure_read_only()
    monkeypatch.setenv("AD_WRITE_ENABLED", "true")
    from app.ad_reader import AdWriteBlocked

    with pytest.raises(AdWriteBlocked):
        ad_guard()
    bad = Settings.model_construct(AD_WRITE_ENABLED=True)
    with pytest.raises(RuntimeError):
        bad.ensure_read_only()
    monkeypatch.setenv("AD_WRITE_ENABLED", "false")

    # Исходники границ: транспортный протокол — только get; вызовы записи отсутствуют.
    api_dir = Path(__file__).resolve().parents[1] / "app"
    onec_src = (api_dir / "onec_client.py").read_text(encoding="utf-8")
    ad_src = (api_dir / "ad_reader.py").read_text(encoding="utf-8")
    assert 'method="GET"' in onec_src
    for forbidden_call in ('method="POST"', "method=\"PUT\"", "method=\"PATCH\"",
                           "method=\"DELETE\"", ".post(", ".put(", ".delete("):
        assert forbidden_call not in onec_src, forbidden_call
    for name, src in (("onec_client", onec_src), ("ad_reader", ad_src)):
        for forbidden in ("add", "modify", "delete", "disable", "enable", "write", "remove"):
            assert not __import__("re").search(
                r"^\s*def\s+" + forbidden + r"\b", src, __import__("re").MULTILINE,
            ), (name, forbidden)
    assert "def get(" in onec_src  # граница транспорта — только чтение
    assert not hasattr(AdReader, "add")
    assert not hasattr(AdReader, "modify")
    assert not hasattr(AdReader, "delete")
    assert not hasattr(AdReader, "disable")
    # Кэш/аудит тоже без удаления данных: у аудита нет update/delete (см. выше).
    assert not hasattr(OneCClient, "delete")


def test_unknown_request_and_step_404(client, hr_headers, neg_mocks, route_single):
    """Чужой/несуществующий id — 404 без утечки ПДн в тексте ошибки."""
    missing = client.get("/requests/REQ-9999", headers=hr_headers)
    assert missing.status_code == 404
    assert "В-0001" not in missing.text and FIO_IVAN not in missing.text
    rid = _create_request(client, hr_headers)["id"]
    assert client.post(f"/requests/{rid}/submit", headers=hr_headers).status_code == 200
    assert client.post(
        f"/requests/{rid}/steps/99/decision",
        json={"decision": "approve"}, headers=hr_headers,
    ).status_code in (403, 404)

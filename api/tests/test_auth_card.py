# Тесты B1 auth+карточка: матрица ролей, дубли ФИО, обрезка ПДн, связка.
# Все персоналии вымышлены. Живых LDAP/HTTP нет: транспорт 1С — мок,
# шлюз AD — фейк волны A4. Общий conftest.py не правим (только читаем):
# пользовательские фикстуры заголовков и client берутся оттуда, подмены
# настроек/клиента/ридера — локально здесь с возвратом после теста.

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdReader, AdReaderSettings, InMemoryCache  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import _detect_role  # noqa: E402
from app.employees import get_ad_reader, get_onec_client  # noqa: E402
from app.link import clear_for_tests, get_memory_links_store  # noqa: E402
from app.link_store import get_links_store  # noqa: E402
from app.main import app  # noqa: E402
from app.onec_client import HttpResult, OneCBaseConfig, OneCClient  # noqa: E402

# Тестовые группы повторяют conftest (имена тестовые, продовые — через env/БД).
TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленное предприятие и персоналии (не реальные данные).
ENT = "Предприятие-Тест-Север"
FIO_IVAN = "Сказочников Иван Тестович"
FIO_PETR = "Выдуманов Петр Примерович"
FIO_DUBL = "Одинаков Дублий Повторович"


def _bases():
    return {
        "zup_t1": OneCBaseConfig(
            code="zup_t1",
            enterprise=ENT,
            url="https://1c-mock.local/t1",
            user="reader",
            secret="s1",
        ),
        "zup_t2": OneCBaseConfig(
            code="zup_t2",
            enterprise=ENT,
            url="https://1c-mock.local/t2",
            user="reader",
            secret="s2",
        ),
    }


def _card_row(tab, fio, dept="Цех Тестовый", position="Тестировщик"):
    # OData-запись справочника Catalog_Сотрудники (дефолты): таб.№ = Code,
    # ФИО = Description, предприятие = ГоловнаяОрганизация_Key.
    # Подразделение/должность/приём — в регистре кадровых данных (см. transport).
    return {
        "Ref_Key": "ref-" + tab,
        "Code": tab,
        "Description": fio,
        "ГоловнаяОрганизация_Key": ENT,
    }


def _hr_row(tab, dept="Цех Тестовый", position="Тестировщик"):
    # OData-запись регистра текущих кадровых данных с $expand полей.
    return {
        "Сотрудник_Key": "ref-" + tab,
        "ТекущееПодразделение": {"Description": dept},
        "ТекущаяДолжность": {"Description": position},
        "ДатаПриема": "2023-01-15",
    }


class FakeTransport:
    """Мок-HTTP 1С: отвечает OData-обёрткой {"value": [...]} по базе.

    URL от клиента: $filter=Code eq 'NNN' (карточка справочника) либо
    substringof('...', Description) eq true (поиск); регистр кадровых данных
    отвечает по Сотрудник_Key (guid). down_t1 имитирует падение базы t1 (5xx)."""

    def __init__(self, down_t1=False):
        self.down_t1 = down_t1
        self.calls: list[str] = []

    def _rows_t1(self):
        return {
            "001": _card_row("001", FIO_IVAN),
            "003": _card_row("003", FIO_DUBL),
            "004": _card_row("004", FIO_DUBL),
        }

    def _rows_t2(self):
        return {"002": _card_row("002", FIO_PETR)}

    @staticmethod
    def _respond(rows: dict[str, dict], filter_str: str) -> HttpResult:
        if "substringof" in filter_str:
            m = re.search(r"substringof\('([^']*)'", filter_str)
            needle = (m.group(1) if m else "").lower()
            hit = [row for row in rows.values() if needle in row["Description"].lower()]
            return HttpResult(200, json.dumps({"value": hit}, ensure_ascii=False))
        m = re.search(r"eq '([^']*)'", filter_str)
        tab = m.group(1) if m else ""
        if tab in rows:
            return HttpResult(200, json.dumps({"value": [rows[tab]]}, ensure_ascii=False))
        return HttpResult(200, json.dumps({"value": []}, ensure_ascii=False))

    def get(self, url, headers, timeout):
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        filter_str = urllib.parse.unquote(
            urllib.parse.parse_qs(parsed.query).get("$filter", [""])[0]
        )
        if "InformationRegister" in url:
            # Второй запрос карточки: кадровые данные по Ref_Key сотрудника.
            m = re.search(r"guid'([^']*)'", filter_str)
            ref = m.group(1) if m else ""
            for rows in (self._rows_t1(), self._rows_t2()):
                for tab, row in rows.items():
                    if row["Ref_Key"] == ref:
                        return HttpResult(
                            200,
                            json.dumps(
                                {"value": [_hr_row(tab)]}, ensure_ascii=False
                            ),
                        )
            return HttpResult(200, json.dumps({"value": []}, ensure_ascii=False))
        if "/t1/" in url:
            if self.down_t1:
                return HttpResult(status=500, body="down")
            return self._respond(self._rows_t1(), filter_str)
        if "/t2/" in url:
            return self._respond(self._rows_t2(), filter_str)
        return HttpResult(status=404, body="{}")


def _ad_entry(sam, fio, dept="Цех Тестовый", title="Тестировщик"):
    return {
        "dn": "CN=%s,OU=SED,DC=example,DC=local" % fio,
        "sAMAccountName": sam,
        "displayName": fio,
        "manager": "",
        "memberOf": [],
        "department": dept,
        "title": title,
        "mail": "%s@example.local" % sam,
        "userAccountControl": 512,
    }


class FakeGateway:
    """Фейк шлюза LDAP волны A4 (вымышленные записи)."""

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

    def search_users(self, query):
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            dict(e)
            for e in self._by_sam.values()
            if needle in e["displayName"].lower()
        ]


def _reader(entries):
    settings = AdReaderSettings(
        ad_url="ldaps://mock.local:636",
        base_dn="OU=SED,DC=example,DC=local",
        reader_dn="CN=sed-reader,OU=SED,DC=example,DC=local",
        cache_ttl_seconds=300,
        timeout_seconds=5.0,
    )
    return AdReader(settings=settings, gateway=FakeGateway(entries), cache=InMemoryCache())


@pytest.fixture
def b1_settings():
    """Тестовые группы + возврат подмен после теста."""
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
def b1_mocks(b1_settings):
    """Мок-клиент 1С и фейк-ридер AD по умолчанию (обе базы живы)."""
    client = OneCClient(_bases(), transport=FakeTransport(), failure_threshold=100)
    reader = _reader(
        [
            _ad_entry("t.ivan", FIO_IVAN),
            _ad_entry("t.petr", FIO_PETR),
            _ad_entry("t.dubl", FIO_DUBL),
            _ad_entry("t.chuzhoi", "Чужой Человек Выдуманный"),
        ]
    )
    app.dependency_overrides[get_onec_client] = lambda: client
    app.dependency_overrides[get_ad_reader] = lambda: reader
    app.dependency_overrides[get_links_store] = lambda: get_memory_links_store()
    clear_for_tests()
    yield {"client": client, "reader": reader}
    app.dependency_overrides.pop(get_onec_client, None)
    app.dependency_overrides.pop(get_ad_reader, None)
    app.dependency_overrides.pop(get_links_store, None)
    clear_for_tests()


# --- Матрица доступа: 401/403 ---

def test_detect_role_hierarchy(b1_settings):
    """Иерархия ролей: админ > руководитель ОК > ОК > владелец (пересечение групп)."""
    settings = b1_settings
    assert _detect_role(["SED_STEP_BUH"], settings) == "owner"
    assert _detect_role(["SED_HR"], settings) == "hr"
    assert _detect_role(["SED_HR_ADMIN"], settings) == "hr_admin"
    assert _detect_role(["SED_ADMINS"], settings) == "admin"
    # Руководитель ОК с группой ОК — всё равно hr_admin; админ в любой группе — admin.
    assert _detect_role(["SED_HR_ADMIN", "SED_HR"], settings) == "hr_admin"
    assert _detect_role(["SED_ADMINS", "SED_HR"], settings) == "admin"


def test_employees_no_auth_401(client, noauth_headers, b1_mocks):
    """Без логина — 401 (проверка deps, не дублируем логику)."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Иван"}, headers=noauth_headers
    )
    assert response.status_code == 401


def test_employees_no_group_403(client, nogroup_headers, b1_mocks):
    """Без разрешающих групп — 403, ПДн не светят."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Иван"}, headers=nogroup_headers
    )
    assert response.status_code == 403


def test_auth_me_alias_matrix(client, hr_headers, owner_headers, nogroup_headers, b1_mocks):
    """Алиас /auth/me: ОК — полностью, владелец — без ПДн, чужой — 403."""
    full = client.get("/auth/me", headers=hr_headers)
    assert full.status_code == 200
    assert full.json()["role"] == "hr"
    assert "fio" in full.json()
    trimmed = client.get("/auth/me", headers=owner_headers)
    assert trimmed.status_code == 200
    assert set(trimmed.json()) == {"sam", "groups", "role"}
    denied = client.get("/auth/me", headers=nogroup_headers)
    assert denied.status_code == 403


def test_auth_me_hr_admin_full(client, hr_admin_headers, b1_mocks):
    """Руководитель ОК — полная заглушка (как ОК) и роль hr_admin."""
    full = client.get("/auth/me", headers=hr_admin_headers)
    assert full.status_code == 200
    assert full.json()["role"] == "hr_admin"
    assert "fio" in full.json()


# --- Поиск и обрезка ПДн ---

def test_employees_hr_full(client, hr_headers, b1_mocks):
    """ОК видит полную карточку: ФИО и остаток отпуска на месте."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Сказочников"}, headers=hr_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["fio"] == FIO_IVAN
    assert item["tab_num"] == "001"
    assert body["needs_manual_review"] is False


def test_employees_admin_full(client, admin_headers, b1_mocks):
    """Админ видит полную карточку так же, как ОК."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Выдуманов"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["fio"] == FIO_PETR


def test_employees_hr_admin_full(client, hr_admin_headers, b1_mocks):
    """Руководитель ОК видит полную карточку, как ОК (is_privileged)."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Сказочников"}, headers=hr_admin_headers
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["fio"] == FIO_IVAN
    assert item["tab_num"] == "001"


def test_employees_owner_trimmed_no_pdn(client, owner_headers, b1_mocks):
    """Владелец: урезанная без ПДн (нет ФИО/почты/дат приёма-увольнения)."""
    response = client.get(
        "/employees",
        params={"enterprise": ENT, "q": "Сказочников"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    for forbidden in ("fio", "mail", "dismissal_date", "hire_date", "employment_type", "tab_num"):
        assert forbidden not in item
    # В списке справочника нет подразделения/должности (они в карточке —
    # второй запрос к регистру кадровых данных); у владельца и они пустые.
    assert item["dept"] == ""
    assert item["position"] == ""


def test_employees_empty_returns_empty(client, hr_headers, b1_mocks):
    """Пустой поиск — пустой список (остальные — пусто, не 403)."""
    response = client.get(
        "/employees",
        params={"enterprise": ENT, "q": "Несуществующий"},
        headers=hr_headers,
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_employees_unknown_enterprise_404(client, hr_headers, b1_mocks):
    """Неизвестное предприятие — 404 (нет привязки к базам)."""
    response = client.get(
        "/employees", params={"enterprise": "Чужое", "q": "Иван"}, headers=hr_headers
    )
    assert response.status_code == 404


def test_employees_duplicates_flag_no_merge(client, hr_headers, b1_mocks):
    """Дубли одного ФИО: обе записи, флаг ручной сверки, склейки нет."""
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "Одинаков"}, headers=hr_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert {i["tab_num"] for i in body["items"]} == {"003", "004"}
    assert body["needs_manual_review"] is True
    assert all(i["needs_manual_review"] is True for i in body["items"])


def test_employees_login_search_by_sam(client, hr_headers, admin_headers, b1_mocks):
    """Поиск по логину находит связанную карточку (мост — link_1c_ad)."""
    created = client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    response = client.get(
        "/employees", params={"enterprise": ENT, "q": "t.ivan"}, headers=hr_headers
    )
    assert response.status_code == 200
    assert any(i["tab_num"] == "001" for i in response.json()["items"])


def test_employees_base_down_isolated(client, hr_headers, b1_settings):
    """Одна база down — остальные отвечают, падение видно в errors."""
    client_mock = OneCClient(
        _bases(), transport=FakeTransport(down_t1=True), failure_threshold=100
    )
    app.dependency_overrides[get_onec_client] = lambda: client_mock
    app.dependency_overrides[get_ad_reader] = lambda: None
    app.dependency_overrides[get_links_store] = lambda: get_memory_links_store()
    clear_for_tests()
    try:
        response = client.get(
            "/employees", params={"enterprise": ENT, "q": "Выдуманов"}, headers=hr_headers
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["tab_num"] == "002"
        assert any("zup_t1" in e for e in body["errors"])
    finally:
        app.dependency_overrides.pop(get_onec_client, None)
        app.dependency_overrides.pop(get_ad_reader, None)
        app.dependency_overrides.pop(get_links_store, None)
        clear_for_tests()


# --- Карточка со снапшотами ---

def test_card_hr_full_with_snapshots(client, hr_headers, admin_headers, b1_mocks):
    """ОК: полные снапшоты 1С/AD, истина — 1С, расхождений нет."""
    client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=admin_headers,
    )
    response = client.get(
        "/employees/card",
        params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
        headers=hr_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["truth_source"] == "1c"
    assert body["fio"] == FIO_IVAN
    assert body["snapshot_1c"]["fio"] == FIO_IVAN
    assert body["snapshot_ad"]["display_name"] == FIO_IVAN
    assert body["link"]["verified"] is True
    assert body["divergences"] == []


def test_card_owner_trimmed_no_pdn(client, hr_headers, admin_headers, owner_headers, b1_mocks):
    """Владелец в карточке: без ФИО/почты/дат приёма-увольнения и без значений снапшотов."""
    client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=admin_headers,
    )
    response = client.get(
        "/employees/card",
        params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
        headers=owner_headers,
    )
    assert response.status_code == 200
    body = response.json()
    for forbidden in ("fio", "hire_date", "dismissal_date", "employment_type"):
        assert forbidden not in body
    assert body["snapshot_ad"] is None
    assert "fio" not in body["snapshot_1c"]
    assert body["truth_source"] == "1c"


def test_card_404_unknown_tab(client, hr_headers, b1_mocks):
    """Неизвестный таб. номер — 404."""
    response = client.get(
        "/employees/card",
        params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "999"},
        headers=hr_headers,
    )
    assert response.status_code == 404


# --- Связка link_1c_ad ---

def test_link_create_hr_verified(client, hr_headers, admin_headers, b1_mocks):
    """Админ создает связку: verified, истина — 1С, снапшоты приложены."""
    response = client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["link"]["verified"] is True
    assert body["link"]["by"] == "adm.petrov"
    assert body["truth_source"] == "1c"
    assert body["snapshot_1c"]["fio"] == FIO_IVAN
    assert body["link"]["needs_manual_review"] is False
    # Ручная привязка тоже заполняет зеркало users (FK link_1c_ad.sam).
    assert get_memory_links_store()._users["t.ivan"]["fio_full"] == FIO_IVAN


def test_link_create_diverged_truth_is_1c(client, hr_headers, admin_headers, b1_mocks):
    """Расхождение ФИО: связка создана, флаг diverged, истина — значения 1С."""
    response = client.post(
        "/link_1c_ad",
        json={
            "enterprise": ENT,
            "base_code": "zup_t1",
            "tab_num": "001",
            "sam": "t.chuzhoi",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["link"]["diverged"] is True
    assert body["link"]["needs_manual_review"] is True
    assert "fio" in body["divergences"]
    assert body["snapshot_1c"]["fio"] == FIO_IVAN
    assert body["snapshot_ad"]["display_name"] == "Чужой Человек Выдуманный"


def test_link_create_duplicates_need_manual(client, hr_headers, admin_headers, b1_mocks):
    """Связка дубля ФИО: создана, но требует ручной сверки."""
    response = client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "003", "sam": "t.dubl"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["link"]["needs_manual_review"] is True


def test_link_create_owner_403(client, owner_headers, b1_mocks):
    """Владелец не подтверждает связку — 403 (действие только ОК)."""
    response = client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=owner_headers,
    )
    assert response.status_code == 403


def test_link_create_unknown_sam_404(client, hr_headers, admin_headers, b1_mocks):
    """Неизвестный логин AD — 404 (автосклейки нет)."""
    response = client.post(
        "/link_1c_ad",
        json={
            "enterprise": ENT,
            "base_code": "zup_t1",
            "tab_num": "001",
            "sam": "no.such",
        },
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_link_create_hr_403(client, hr_headers, b1_mocks):
    """ОК (рядовой) не привязывает AD — 403 (привязка только админу)."""
    response = client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=hr_headers,
    )
    assert response.status_code == 403


def test_link_read_returns_fact(client, hr_headers, admin_headers, owner_headers, b1_mocks):
    """Чтение связки: факт без ПДн доступен и ОК, и владельцу."""
    client.post(
        "/link_1c_ad",
        json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
        headers=admin_headers,
    )
    for headers in (hr_headers, owner_headers):
        response = client.get(
            "/link_1c_ad",
            params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["link"]["sam"] == "t.ivan"
        assert response.json()["truth_source"] == "1c"


def test_owner_responses_no_tab_num_pdn(client, owner_headers, b1_mocks):
    """Владелец: в поиске и карточке нет tab_num/ФИО/почты/дат приёма-увольнения."""
    # Поиск: урезанные записи без идентификаторов человека.
    found = client.get(
        "/employees",
        params={"enterprise": ENT, "q": "Сказочников"},
        headers=owner_headers,
    )
    assert found.status_code == 200
    assert len(found.json()["items"]) == 1
    item = found.json()["items"][0]
    for forbidden in ("tab_num", "fio", "mail", "dismissal_date", "hire_date"):
        assert forbidden not in item
    # Служебный минимум для отметок остается.
    assert "key" in item and "dept" in item and "position" in item
    # Карточка: обезличенная ветка без таб.№ и значений снапшотов.
    card = client.get(
        "/employees/card",
        params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
        headers=owner_headers,
    )
    assert card.status_code == 200
    body = card.json()
    for forbidden in ("tab_num", "fio", "mail", "dismissal_date", "hire_date"):
        assert forbidden not in body
    assert "tab_num" not in body["snapshot_1c"] and "fio" not in body["snapshot_1c"]
    assert body["snapshot_ad"] is None

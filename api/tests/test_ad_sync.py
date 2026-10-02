# Тесты автосвязки 1С↔AD (Задача 2.3–2.5): движок run_ad_sync (точное ФИО/
# дубли/нет совпадения/уже связан), эндпоинт POST /link_1c_ad/sync (роли/503/
# 409), флаг ad_status. Все ПДн вымышлены. Только моки (сети нет).
# conftest.py не правим: фикстуры client/admin/hr/owner — оттуда.

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdReader, AdReaderSettings, InMemoryCache  # noqa: E402
from app.ad_sync import compute_ad_status, maybe_sync_links_weekly, run_ad_sync  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.employees import get_ad_reader, get_onec_client  # noqa: E402
from app.link import clear_for_tests, get_memory_links_store  # noqa: E402
from app.link_store import get_links_store  # noqa: E402
from app.main import app  # noqa: E402
from app.onec_client import HttpResult, OneCBaseConfig, OneCClient  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленное предприятие и персоналии.
ENT = "Предприятие-Тест-Север"
FIO_IVAN = "Сказочников Иван Тестович"
FIO_AD_DUBL = "Дважды Дублируемый Адресович"
FIO_1C_DUBL = "Одинаков Дублий Повторович"
FIO_NO_MATCH = "Без Совпадения Адович"


def _bases() -> dict:
    return {
        "zup_t1": OneCBaseConfig(
            code="zup_t1",
            enterprise=ENT,
            url="https://1c-mock.local/t1",
            user="reader",
            secret="s1",
        )
    }


def _rows() -> dict[str, dict]:
    """Сотрудники базы t1: 1 уникальный, 1 с дублями AD, 1 пара дублей 1С, 1 без AD."""
    rows = {
        "001": {"Ref_Key": "ref-001", "Code": "001", "Description": FIO_IVAN, "ГоловнаяОрганизация_Key": ENT},
        "002": {"Ref_Key": "ref-002", "Code": "002", "Description": FIO_AD_DUBL, "ГоловнаяОрганизация_Key": ENT},
        "003": {"Ref_Key": "ref-003", "Code": "003", "Description": FIO_1C_DUBL, "ГоловнаяОрганизация_Key": ENT},
        "004": {"Ref_Key": "ref-004", "Code": "004", "Description": FIO_1C_DUBL, "ГоловнаяОрганизация_Key": ENT},
        "005": {"Ref_Key": "ref-005", "Code": "005", "Description": FIO_NO_MATCH, "ГоловнаяОрганизация_Key": ENT},
    }
    return rows


class FakeTransport:
    """Мок-HTTP 1С: выгрузка всех сотрудников базы t1 (list_employees)."""

    def __init__(self):
        self.calls = 0

    def get(self, url, headers, timeout):
        self.calls += 1
        if "/t1/" not in url:
            return HttpResult(status=500, body="down")
        return HttpResult(
            status=200,
            body=json.dumps({"value": list(_rows().values())}, ensure_ascii=False),
        )


def _ad_entry(sam, fio):
    return {
        "dn": "CN=%s,OU=SED,DC=example,DC=local" % fio,
        "sAMAccountName": sam,
        "displayName": fio,
        "manager": "",
        "memberOf": [],
        "department": "Цех Тестовый",
        "title": "Тестировщик",
        "mail": "%s@example.local" % sam,
        "userAccountControl": 512,
    }


def _ad_entries():
    return [
        _ad_entry("t.ivan", FIO_IVAN),
        _ad_entry("t.ad1", FIO_AD_DUBL),
        _ad_entry("t.ad2", FIO_AD_DUBL),  # два AD с одним ФИО
    ]


class FakeGateway:
    """Фейк шлюза LDAP: поиск по подстроке displayName."""

    def __init__(self, entries):
        self._entries = [dict(e) for e in entries]

    def bind(self):
        return None

    def search_user_by_sam(self, sam):
        for e in self._entries:
            if e["sAMAccountName"].lower() == sam.strip().lower():
                return dict(e)
        return None

    def search_user_by_dn(self, dn):
        return None

    def search_users(self, query):
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            dict(e)
            for e in self._entries
            if needle in e["displayName"].lower()
        ]


def _reader(entries=None) -> AdReader:
    settings = AdReaderSettings(
        ad_url="ldaps://mock.local:636",
        base_dn="OU=SED,DC=example,DC=local",
        reader_dn="CN=sed-reader,OU=SED,DC=example,DC=local",
        cache_ttl_seconds=300,
        timeout_seconds=5.0,
    )
    return AdReader(
        settings=settings,
        gateway=FakeGateway(entries if entries is not None else _ad_entries()),
        cache=InMemoryCache(),
    )


def _client() -> OneCClient:
    return OneCClient(_bases(), transport=FakeTransport())


# ---------------------------------------------------------------------------
# Движок run_ad_sync
# ---------------------------------------------------------------------------

def test_sync_creates_unique_verified_link():
    store = get_memory_links_store()
    clear_for_tests()
    result = run_ad_sync(_client(), _reader(), store, [ENT])
    assert result.scanned == 5
    assert result.created == 1  # только FIO_IVAN (уникален и в 1С, и в AD)
    rec = store.find("|".join([ENT, "zup_t1", "001"]))
    assert rec is not None
    assert rec.verified is True
    assert rec.sam == "t.ivan"
    assert rec.by == "ad_sync"
    # Зеркала ссылок в НАШЕЙ БД (users/employee_base_map/one_c_bases) заполнены
    # из AD/1С-клиента; AD/1С не пишем.
    assert store._users["t.ivan"]["fio_full"] == FIO_IVAN
    assert store._users["t.ivan"]["mail"] == "t.ivan@example.local"
    assert store._employees["|".join([ENT, "zup_t1", "001"])]["fio"] == FIO_IVAN
    assert store._bases["zup_t1"]["odata_url"] == "https://1c-mock.local/t1"
    # Дубли AD (002) и дубли 1С (003/004) не связаны.
    assert store.find("|".join([ENT, "zup_t1", "002"])) is None
    assert store.find("|".join([ENT, "zup_t1", "003"])) is None


def test_sync_skips_ad_duplicates():
    store = get_memory_links_store()
    clear_for_tests()
    result = run_ad_sync(_client(), _reader(), store, [ENT])
    assert result.skipped_ad_duplicates == 1  # 002: два AD с этим ФИО
    assert result.skipped_1c_duplicates == 2  # 003/004: дубль ФИО в 1С
    assert result.skipped_ad_no_match == 1  # 005: в AD нет


def test_sync_keeps_existing_link():
    store = get_memory_links_store()
    clear_for_tests()
    # Первый проход создаёт связку.
    run_ad_sync(_client(), _reader(), store, [ENT])
    created = store.find("|".join([ENT, "zup_t1", "001"]))
    assert created is not None
    # Второй проход не создаёт повторно и не трогает существующую.
    result = run_ad_sync(_client(), _reader(), store, [ENT])
    assert result.created == 0
    assert result.skipped_linked == 1
    assert store.find("|".join([ENT, "zup_t1", "001"])).sam == created.sam


def test_sync_one_base_down_continues_others():
    """Падение одной базы не валит проход (ошибка копится, связки живых — есть)."""
    store = get_memory_links_store()
    clear_for_tests()
    bases = dict(_bases())
    bases["zup_down"] = OneCBaseConfig(
        code="zup_down", enterprise=ENT,
        url="https://1c-mock.local/down", user="reader", secret="s9",
    )
    client = OneCClient(bases, transport=FakeTransport())
    result = run_ad_sync(client, _reader(), store, [ENT])
    assert result.created >= 1
    assert any("zup_down" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Флаг ad_status (без записи в БД)
# ---------------------------------------------------------------------------

def _card(tab, fio):
    from app.onec_client import EmployeeCard

    return EmployeeCard(enterprise=ENT, base_code="zup_t1", tab_num=tab, fio=fio)


def test_ad_status_match_no_link():
    card = _card("001", FIO_IVAN)
    assert compute_ad_status(card, None, _reader()) == "match"


def test_ad_status_no_match():
    card = _card("005", FIO_NO_MATCH)
    assert compute_ad_status(card, None, _reader()) == "no_match"


def test_ad_status_no_reader_is_no_match():
    card = _card("001", FIO_IVAN)
    assert compute_ad_status(card, None, None) == "no_match"


def test_ad_status_linked():
    card = _card("001", FIO_IVAN)
    store = get_memory_links_store()
    clear_for_tests()
    run_ad_sync(_client(), _reader(), store, [ENT])
    link = store.find("|".join([ENT, "zup_t1", "001"]))
    assert link is not None
    assert compute_ad_status(card, link, _reader()) == "linked"


# ---------------------------------------------------------------------------
# Эндпоинт POST /link_1c_ad/sync (роли/409/503) и GET /ad/search
# ---------------------------------------------------------------------------

class InMemorySettingsStore:
    """Мок хранилища настроек (сид-формат), как в test_w3a_documents + set."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})
        self.sets = []

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value
        self.sets.append(key)

    def get_many(self, keys):
        return {k: self._data.get(k) for k in keys}


@pytest.fixture
def sync_mocks():
    """Подмены: клиент 1С, ридер AD, хранилища связок/настроек."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    settings_store = InMemorySettingsStore(
        {"enterprises": json.dumps([{"code": ENT, "name": "Тест"}], ensure_ascii=False)}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_onec_client] = lambda: _client()
    app.dependency_overrides[get_ad_reader] = lambda: _reader()
    app.dependency_overrides[get_links_store] = lambda: get_memory_links_store()
    app.dependency_overrides[get_settings_store] = lambda: settings_store
    clear_for_tests()
    yield {"settings_store": settings_store}
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_onec_client, None)
    app.dependency_overrides.pop(get_ad_reader, None)
    app.dependency_overrides.pop(get_links_store, None)
    app.dependency_overrides.pop(get_settings_store, None)
    clear_for_tests()


def test_sync_endpoint_admin_creates_links(client, admin_headers, sync_mocks):
    response = client.post("/link_1c_ad/sync", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["synced"] is True
    assert body["created"] == 1
    assert body["scanned"] == 5
    assert "ad_links_synced_at" in sync_mocks["settings_store"].sets


def test_sync_endpoint_hr_403(client, hr_headers, sync_mocks):
    response = client.post("/link_1c_ad/sync", headers=hr_headers)
    assert response.status_code == 403


def test_sync_endpoint_no_reader_503(client, admin_headers, sync_mocks):
    app.dependency_overrides[get_ad_reader] = lambda: None
    response = client.post("/link_1c_ad/sync", headers=admin_headers)
    assert response.status_code == 503
    app.dependency_overrides[get_ad_reader] = lambda: _reader()


def test_sync_endpoint_no_enterprises_409(client, admin_headers, sync_mocks):
    sync_mocks["settings_store"]._data["enterprises"] = None
    response = client.post("/link_1c_ad/sync", headers=admin_headers)
    assert response.status_code == 409


def test_ad_search_admin_returns_candidates(client, admin_headers, sync_mocks):
    response = client.get("/ad/search", params={"q": "Сказочников"}, headers=admin_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["sam"] for i in items] == ["t.ivan"]
    assert items[0]["display_name"] == FIO_IVAN
    assert "mail" in items[0] and "department" in items[0]


def test_ad_search_hr_allowed(client, hr_headers, sync_mocks):
    """AD-поиск доступен ОК (выбор исполнителей маршрута), не только админу."""
    response = client.get("/ad/search", params={"q": "Сказочников"}, headers=hr_headers)
    assert response.status_code == 200
    assert [i["sam"] for i in response.json()["items"]] == ["t.ivan"]


def test_ad_search_owner_403(client, owner_headers, sync_mocks):
    """Владельцу AD-поиск закрыт (ПДн)."""
    response = client.get("/ad/search", params={"q": "Сказочников"}, headers=owner_headers)
    assert response.status_code == 403


def test_ad_search_no_reader_503(client, admin_headers, sync_mocks):
    app.dependency_overrides[get_ad_reader] = lambda: None
    response = client.get("/ad/search", params={"q": "x"}, headers=admin_headers)
    assert response.status_code == 503
    app.dependency_overrides[get_ad_reader] = lambda: _reader()


# ---------------------------------------------------------------------------
# Эндпоинт GET /ad/groups/{group}/members (состав группы, конструктор маршрута)
# ---------------------------------------------------------------------------

class FakeGroupGateway(FakeGateway):
    """Фейк шлюза с группами: поиск группы по CN и резолв участников по DN."""

    def __init__(self, entries, groups, fail_with=None):
        super().__init__(entries)
        self._groups = {
            cn.lower(): {
                "dn": "CN=%s,OU=SED,DC=example,DC=local" % cn,
                "cn": cn,
                "members": list(members),
            }
            for cn, members in groups.items()
        }
        self._fail_with = fail_with

    def search_group_by_cn(self, cn):
        if self._fail_with is not None:
            raise self._fail_with
        found = self._groups.get(cn.strip().lower())
        return dict(found) if found else None

    def search_user_by_dn(self, dn):
        if self._fail_with is not None:
            raise self._fail_with
        for e in self._entries:
            if e["dn"].lower() == dn.strip().lower():
                return dict(e)
        return None


def _dn_of(sam, entries):
    for e in entries:
        if e["sAMAccountName"].lower() == sam.lower():
            return e["dn"]
    raise AssertionError("нет записи AD: %s" % sam)


def _group_reader(groups, entries=None, fail_with=None) -> AdReader:
    settings = AdReaderSettings(
        ad_url="ldaps://mock.local:636",
        base_dn="OU=SED,DC=example,DC=local",
        reader_dn="CN=sed-reader,OU=SED,DC=example,DC=local",
        cache_ttl_seconds=300,
        timeout_seconds=5.0,
    )
    return AdReader(
        settings=settings,
        gateway=FakeGroupGateway(entries if entries is not None else _ad_entries(),
                                 groups, fail_with),
        cache=InMemoryCache(),
    )


def test_ad_group_members_admin_returns_items(client, admin_headers, sync_mocks):
    entries = _ad_entries()
    groups = {"SED_STEP_BUH": [_dn_of("t.ivan", entries), _dn_of("t.ad1", entries)]}
    app.dependency_overrides[get_ad_reader] = lambda: _group_reader(groups, entries)
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=admin_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["sam"] for i in items] == ["t.ad1", "t.ivan"]  # порядок по sam
    assert set(items[0]) == {"sam", "display_name", "department", "title", "mail"}


def test_ad_group_members_hr_allowed(client, hr_headers, sync_mocks):
    """Состав группы доступен ОК (конструктор маршрута), не только админу."""
    entries = _ad_entries()
    app.dependency_overrides[get_ad_reader] = lambda: _group_reader(
        {"SED_STEP_BUH": [_dn_of("t.ivan", entries)]}, entries
    )
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=hr_headers)
    assert response.status_code == 200
    assert [i["sam"] for i in response.json()["items"]] == ["t.ivan"]


def test_ad_group_members_owner_403(client, owner_headers, sync_mocks):
    """Владельцу состав группы AD закрыт (ПДн)."""
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=owner_headers)
    assert response.status_code == 403


def test_ad_group_members_noauth_401(client, noauth_headers, sync_mocks):
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=noauth_headers)
    assert response.status_code == 401


def test_ad_group_members_group_not_allowed_403(client, admin_headers, sync_mocks):
    """Группа вне allowed_ad_groups и без префикса владельцев шагов — 403."""
    response = client.get("/ad/groups/SED_UNKNOWN/members", headers=admin_headers)
    assert response.status_code == 403


def test_ad_group_members_group_from_settings_db_200(client, admin_headers, sync_mocks):
    """Группа только из настроек БД (нет в env, без префикса) — доступна (#9)."""
    entries = _ad_entries()
    groups = {"SED_DB_ONLY": [_dn_of("t.ivan", entries)]}
    sync_mocks["settings_store"]._data["allowed_ad_groups"] = json.dumps(["SED_DB_ONLY"])
    app.dependency_overrides[get_ad_reader] = lambda: _group_reader(groups, entries)
    response = client.get("/ad/groups/SED_DB_ONLY/members", headers=admin_headers)
    assert response.status_code == 200
    assert [i["sam"] for i in response.json()["items"]] == ["t.ivan"]


def test_ad_group_members_empty_group_200_empty_items(client, admin_headers, sync_mocks):
    entries = _ad_entries()
    app.dependency_overrides[get_ad_reader] = lambda: _group_reader(
        {"SED_STEP_BUH": []}, entries
    )
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_ad_group_members_no_reader_503(client, admin_headers, sync_mocks):
    app.dependency_overrides[get_ad_reader] = lambda: None
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=admin_headers)
    assert response.status_code == 503


def test_ad_group_members_ad_unavailable_503(client, admin_headers, sync_mocks):
    app.dependency_overrides[get_ad_reader] = lambda: _group_reader(
        {"SED_STEP_BUH": []}, fail_with=TimeoutError("ldap timeout")
    )
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=admin_headers)
    assert response.status_code == 503


def test_ad_group_members_group_not_found_404(client, admin_headers, sync_mocks):
    app.dependency_overrides[get_ad_reader] = lambda: _group_reader({"SED_STEP_BUH": []})
    response = client.get("/ad/groups/SED_STEP_NET_TAKOY/members", headers=admin_headers)
    assert response.status_code == 404
    assert "не найдена" in response.json()["detail"]


# ---------------------------------------------------------------------------
# maybe_sync_links_weekly (регламентная автосвязка, расписание из settings)
# ---------------------------------------------------------------------------

def test_maybe_sync_links_weekly_interval_due_runs():
    """Интервальное расписание: «пора» — проход выполнен, метка записана."""
    store = get_memory_links_store()
    clear_for_tests()
    settings_store = InMemorySettingsStore(
        {
            "schedule_ad_links_sync": json.dumps({"mode": "interval", "interval_hours": 3}),
            "ad_links_synced_at": json.dumps(
                (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
            ),
            "enterprises": json.dumps([{"code": ENT, "name": "Тест"}], ensure_ascii=False),
        }
    )
    result = maybe_sync_links_weekly(settings_store, _client(), _reader(), store)
    assert result is not False  # вернулся AdSyncResult (реальный проход)
    assert result.scanned == 5
    assert result.created == 1
    assert "ad_links_synced_at" in settings_store.sets  # метка записана


def test_maybe_sync_links_weekly_interval_not_due_skips():
    """Интервальное расписание: «не пора» — False, метка не перезаписана."""
    store = get_memory_links_store()
    clear_for_tests()
    settings_store = InMemorySettingsStore(
        {
            "schedule_ad_links_sync": json.dumps({"mode": "interval", "interval_hours": 3}),
            "ad_links_synced_at": json.dumps(
                (datetime.now(timezone.utc) + timedelta(hours=100)).isoformat()
            ),
            "enterprises": json.dumps([{"code": ENT, "name": "Тест"}], ensure_ascii=False),
        }
    )
    assert maybe_sync_links_weekly(settings_store, _client(), _reader(), store) is False
    assert "ad_links_synced_at" not in settings_store.sets


def test_maybe_sync_links_weekly_no_schedule_weekly_not_due():
    """Без расписания — прежнее поведение: раз в 7 дней; сутки назад — не пора."""
    store = get_memory_links_store()
    clear_for_tests()
    settings_store = InMemorySettingsStore(
        {
            "ad_links_synced_at": json.dumps(
                (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            ),
            "enterprises": json.dumps([{"code": ENT, "name": "Тест"}], ensure_ascii=False),
        }
    )
    assert maybe_sync_links_weekly(settings_store, _client(), _reader(), store) is False

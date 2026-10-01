# Тесты автосвязки 1С↔AD (Задача 2.3–2.5): движок run_ad_sync (точное ФИО/
# дубли/нет совпадения/уже связан), эндпоинт POST /link_1c_ad/sync (роли/503/
# 409), флаг ad_status. Все ПДн вымышлены. Только моки (сети нет).
# conftest.py не правим: фикстуры client/admin/hr/owner — оттуда.

from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdReader, AdReaderSettings, InMemoryCache  # noqa: E402
from app.ad_sync import compute_ad_status, run_ad_sync  # noqa: E402
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
    # Зеркало users заполнено из AD (только наша БД; AD/1С не пишем).
    assert store._users["t.ivan"]["fio_full"] == FIO_IVAN
    assert store._users["t.ivan"]["mail"] == "t.ivan@example.local"
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


def test_ad_search_hr_403(client, hr_headers, sync_mocks):
    response = client.get("/ad/search", params={"q": "Сказочников"}, headers=hr_headers)
    assert response.status_code == 403


def test_ad_search_no_reader_503(client, admin_headers, sync_mocks):
    app.dependency_overrides[get_ad_reader] = lambda: None
    response = client.get("/ad/search", params={"q": "x"}, headers=admin_headers)
    assert response.status_code == 503
    app.dependency_overrides[get_ad_reader] = lambda: _reader()
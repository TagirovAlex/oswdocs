# Кэш состава групп AD: store (sqlite-roundtrip), sync с фейковым ридером,
# эндпоинт GET /ad/groups/{group}/members из кэша, ручной POST /ad/groups/sync.
# Все ПДн вымышленные; БД/AD не вызываются (in-memory моки + sqlite-файл).
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_groups_cache import (  # noqa: E402
    DbGroupsCacheStore,
    InMemoryGroupsCacheStore,
    get_groups_cache_store,
    maybe_sync_ad_groups_weekly,
    sync_ad_group_members,
)
from app.ad_reader import AdNotFound, AdUnavailable  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

FAKE_MEMBERS = [
    {"sam": "step.buhgalter", "display_name": "Вымышленный Бухгалтер Полный",
     "department": "Бухгалтерия", "title": "Бухгалтер", "mail": "step.buhgalter@example.local"},
    {"sam": "step.kassir", "display_name": "Вымышленный Кассир Полный",
     "department": "Бухгалтерия", "title": "Кассир", "mail": "step.kassir@example.local"},
]


class FakeAdReader:
    """Мок ридера AD: состав групп из словаря, неизвестная — AdNotFound."""

    def __init__(self, groups=None, unavailable=False):
        self._groups = dict(groups or {})
        self._unavailable = unavailable
        self.calls: list[str] = []

    def group_members(self, group: str):
        self.calls.append(group)
        if self._unavailable:
            raise AdUnavailable("AD недоступен (тест)")
        if group not in self._groups:
            raise AdNotFound(f"Группа {group!r} не найдена (тест)")
        return [SimpleNamespace(**m) for m in self._groups[group]]


class DictSettingsStore:
    """Мок DbSettingsStore: get/set поверх dict (значения — сид-формат)."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value


@pytest.fixture(autouse=True)
def settings_override():
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
def cache_store():
    store = InMemoryGroupsCacheStore()
    app.dependency_overrides[get_groups_cache_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_groups_cache_store, None)


@pytest.fixture
def fake_reader():
    reader = FakeAdReader({"SED_STEP_BUH": FAKE_MEMBERS})
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield reader
    app.dependency_overrides.pop(get_ad_reader, None)


@pytest.fixture
def settings_store():
    from app.settings_routes import get_settings_store

    store = DictSettingsStore({
        "allowed_ad_groups": json.dumps(["SED_STEP_BUH", {"id": "SED_STEP_OK", "name": "ОК"}]),
    })
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


# --- store: sqlite-roundtrip ---


def test_db_store_roundtrip_empty_and_resync(tmp_path):
    """Db-стор: пустая группа после синка отличается от несинхронизированной;
    повторный синк перезаписывает состав."""
    db = tmp_path / "groups.db"
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE ad_group_members (group_name TEXT NOT NULL, sam TEXT NOT NULL, "
            "display_name TEXT NOT NULL DEFAULT '', department TEXT, title TEXT, mail TEXT, "
            "PRIMARY KEY (group_name, sam))"
        ))
        conn.execute(text(
            "CREATE TABLE ad_group_sync_state (group_name TEXT PRIMARY KEY, "
            "synced_at TIMESTAMPTZ, member_count INTEGER NOT NULL DEFAULT 0)"
        ))
    engine.dispose()
    store = DbGroupsCacheStore(f"sqlite:///{db}")
    assert store.load("SED_STEP_BUH") == (False, [])
    from app.ad_groups_cache import CachedMember

    store.save("SED_STEP_BUH", [])
    synced, members = store.load("SED_STEP_BUH")
    assert synced is True and members == []
    store.save("SED_STEP_BUH", [CachedMember(group_name="SED_STEP_BUH", sam="a.b", display_name="А Б")])
    synced, members = store.load("SED_STEP_BUH")
    assert synced is True and [m.sam for m in members] == ["a.b"]
    store.save("SED_STEP_BUH", [])
    assert store.load("SED_STEP_BUH") == (True, [])


# --- sync_ad_group_members ---


def test_sync_writes_cache_and_counts(cache_store):
    """Синк пишет состав в кэш: счётчики групп/участников, без ошибок."""
    reader = FakeAdReader({"SED_STEP_BUH": FAKE_MEMBERS, "SED_STEP_OK": []})
    result = sync_ad_group_members(reader, ["SED_STEP_BUH", "SED_STEP_OK"], cache_store)
    assert result == {"synced_groups": 2, "members": 2, "errors": []}
    synced, members = cache_store.load("SED_STEP_BUH")
    assert synced is True and [m.sam for m in members] == ["step.buhgalter", "step.kassir"]
    assert cache_store.load("SED_STEP_OK") == (True, [])


def test_sync_unknown_group_not_marked(cache_store):
    """Неизвестная группа в AD — ошибка, метка синка не ставится (404 сохранится)."""
    reader = FakeAdReader({})
    result = sync_ad_group_members(reader, ["SED_STEP_NOPE"], cache_store)
    assert result["synced_groups"] == 0 and len(result["errors"]) == 1
    assert cache_store.load("SED_STEP_NOPE") == (False, [])


def test_sync_unavailable_not_marked(cache_store):
    """Сбой каталога — ошибка, метка синка не ставится."""
    reader = FakeAdReader({"SED_STEP_BUH": FAKE_MEMBERS}, unavailable=True)
    result = sync_ad_group_members(reader, ["SED_STEP_BUH"], cache_store)
    assert result["synced_groups"] == 0 and len(result["errors"]) == 1
    assert cache_store.load("SED_STEP_BUH") == (False, [])


# --- maybe_sync_ad_groups_weekly ---


def test_maybe_sync_weekly_runs_and_stamps():
    """Регламент: первый проход — синк и метка; без ридера/групп — False."""
    from app.ad_groups_cache import CachedMember  # noqa: F401

    store = DictSettingsStore({
        "allowed_ad_groups": json.dumps(["SED_STEP_BUH"]),
    })
    cache = InMemoryGroupsCacheStore()
    reader = FakeAdReader({"SED_STEP_BUH": FAKE_MEMBERS})
    assert maybe_sync_ad_groups_weekly(store, cache, reader) is True
    assert cache.load("SED_STEP_BUH")[0] is True
    assert store.get("ad_groups_synced_at") is not None
    assert maybe_sync_ad_groups_weekly(store, cache, None) is False
    empty = DictSettingsStore({"allowed_ad_groups": json.dumps([])})
    assert maybe_sync_ad_groups_weekly(empty, InMemoryGroupsCacheStore(), reader) is False


# --- GET /ad/groups/{group}/members из кэша ---


def test_members_from_cache_without_reader(client, hr_headers, cache_store, settings_store):
    """Состав из кэша: ридер не вызывается (подмена None из conftest)."""
    from app.ad_groups_cache import CachedMember

    cache_store.save("SED_STEP_BUH", [
        CachedMember(group_name="SED_STEP_BUH", sam="step.buhgalter",
                     display_name="Вымышленный Бухгалтер Полный"),
    ])
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=hr_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [{
        "sam": "step.buhgalter", "display_name": "Вымышленный Бухгалтер Полный",
        "department": None, "title": None, "mail": None,
    }]}


def test_members_miss_reads_live_and_stores(client, hr_headers, cache_store, settings_store, fake_reader):
    """Промах кэша — живое чтение AD с записью в кэш (повтор уже из кэша)."""
    assert cache_store.load("SED_STEP_BUH") == (False, [])
    first = client.get("/ad/groups/SED_STEP_BUH/members", headers=hr_headers)
    assert first.status_code == 200
    assert [m["sam"] for m in first.json()["items"]] == ["step.buhgalter", "step.kassir"]
    assert fake_reader.calls == ["SED_STEP_BUH"]
    assert cache_store.load("SED_STEP_BUH")[0] is True


def test_members_miss_no_reader_503(client, hr_headers, cache_store, settings_store):
    """Промах кэша без ридера AD — 503 (как раньше без кэша)."""
    assert client.get("/ad/groups/SED_STEP_BUH/members", headers=hr_headers).status_code == 503


def test_members_unknown_group_404(client, hr_headers, cache_store, settings_store, fake_reader):
    """Группы нет в AD — 404 и без метки синка (как раньше)."""
    assert client.get("/ad/groups/SED_STEP_NOPE/members", headers=hr_headers).status_code == 404
    assert cache_store.load("SED_STEP_NOPE") == (False, [])


def test_members_owner_allowed(client, owner_headers, cache_store, settings_store, fake_reader):
    """Владелец шага видит состав из кэша (доступ по должности); без логина — 401."""
    from app.ad_groups_cache import CachedMember

    cache_store.save("SED_STEP_BUH", [
        CachedMember(group_name="SED_STEP_BUH", sam="step.buhgalter",
                     display_name="Вымышленный Бухгалтер Полный"),
    ])
    response = client.get("/ad/groups/SED_STEP_BUH/members", headers=owner_headers)
    assert response.status_code == 200, response.text
    assert [m["sam"] for m in response.json()["items"]] == ["step.buhgalter"]
    assert fake_reader.calls == []
    assert client.get("/ad/groups/SED_STEP_BUH/members", headers={}).status_code == 401


def test_members_group_not_allowed_403(client, hr_headers, cache_store, settings_store, fake_reader):
    """Группа вне справочника — 403 (разрешение проверяется до кэша)."""
    assert client.get("/ad/groups/SED_OTHER/members", headers=hr_headers).status_code == 403
    assert fake_reader.calls == []


# --- POST /ad/groups/sync ---


def test_manual_sync_admin_200(client, admin_headers, cache_store, settings_store):
    """Ручной синк админа: 200, состав в кэше, счётчики в ответе."""
    from app.main import app

    both = FakeAdReader({"SED_STEP_BUH": FAKE_MEMBERS, "SED_STEP_OK": []})
    app.dependency_overrides[get_ad_reader] = lambda: both
    try:
        response = client.post("/ad/groups/sync", headers=admin_headers)
    finally:
        app.dependency_overrides.pop(get_ad_reader, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["synced_groups"] == 2 and body["members"] == 2 and body["errors"] == []
    assert "at" in body
    assert cache_store.load("SED_STEP_BUH")[0] is True
    assert cache_store.load("SED_STEP_OK") == (True, [])


def test_manual_sync_roles_403(client, hr_headers, owner_headers, cache_store, settings_store, fake_reader):
    """Ручной синк — только админ: ОК и владелец — 403, без логина — 401."""
    assert client.post("/ad/groups/sync", headers=hr_headers).status_code == 403
    assert client.post("/ad/groups/sync", headers=owner_headers).status_code == 403
    assert client.post("/ad/groups/sync", headers={}).status_code == 401


def test_manual_sync_no_reader_503(client, admin_headers, cache_store, settings_store):
    """Ручной синк без ридера AD — 503."""
    assert client.post("/ad/groups/sync", headers=admin_headers).status_code == 503

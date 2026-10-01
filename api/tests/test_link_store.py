# Тесты хранилища связок 1С-AD (волна 4): unit InMemoryLinksStore (find/save/
# find_by_sam/reset) и проверка, что эндпоинты ходят через зависимость
# get_links_store (override со счетчиком вызовов).
# Все ПДн вымышленные; группы — через оверрайд get_settings; клиент 1С и
# ридер AD — моки (границы LDAP/HTTP не задеваются).

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
from app.employees import get_ad_reader, get_onec_client  # noqa: E402
from app.link import LinkRecord, get_memory_links_store, link_key  # noqa: E402
from app.link_store import (  # noqa: E402
    DbLinksStore,
    InMemoryLinksStore,
    get_links_store,
)
from app.main import app  # noqa: E402
from app.onec_client import HttpResult, OneCBaseConfig, OneCClient  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные предприятие и персоналии (не реальные данные).
ENT = "Предприятие-Тест-Север"
FIO_IVAN = "Сказочников Иван Тестович"


def _record(key: str, sam: str = "t.ivan", **overrides) -> LinkRecord:
    """Связка-факт по составному ключу (вымышленная, для unit-тестов)."""
    enterprise, base_code, tab_num = key.split("|", 2)
    values = dict(
        enterprise=enterprise,
        base_code=base_code,
        tab_num=tab_num,
        key=key,
        sam=sam,
        by="ok.ivnova",
        at="2026-01-15T10:00:00+00:00",
        verified=True,
    )
    values.update(overrides)
    return LinkRecord(**values)


# --- InMemoryLinksStore: unit ---

def test_inmemory_save_find():
    """save затем find по составному ключу; отсутствующего ключа нет."""
    store = InMemoryLinksStore()
    key = link_key(ENT, "zup_t1", "001")
    store.save(_record(key))
    found = store.find(key)
    assert found is not None
    assert found.sam == "t.ivan"
    assert found.by == "ok.ivnova"
    assert store.find(link_key(ENT, "zup_t1", "999")) is None


def test_inmemory_save_upsert():
    """Повторный save того же ключа перезаписывает факт (upsert)."""
    store = InMemoryLinksStore()
    key = link_key(ENT, "zup_t1", "001")
    store.save(_record(key, sam="t.ivan"))
    store.save(_record(key, sam="t.ivan", verified=False))
    found = store.find(key)
    assert found is not None
    assert found.verified is False
    assert len(store.find_by_sam("t.ivan")) == 1


def test_inmemory_find_by_sam():
    """find_by_sam находит все связки логина (регистр/пробелы не важны)."""
    store = InMemoryLinksStore()
    store.save(_record(link_key(ENT, "zup_t1", "001"), sam="t.ivan"))
    store.save(_record(link_key(ENT, "zup_t2", "002"), sam="t.petr"))
    store.save(_record(link_key(ENT, "zup_t1", "003"), sam="T.IVAN"))
    found = store.find_by_sam("  t.ivan ")
    assert [r.tab_num for r in found] == ["001", "003"]
    assert store.find_by_sam("no.such") == []


def test_inmemory_reset():
    """reset очищает хранилище (изоляция pytest)."""
    store = InMemoryLinksStore()
    store.save(_record(link_key(ENT, "zup_t1", "001")))
    assert store.find(link_key(ENT, "zup_t1", "001")) is not None
    store.reset()
    assert store.find(link_key(ENT, "zup_t1", "001")) is None


# --- DbLinksStore: интерфейс без обращения к БД ---

def test_db_store_implements_protocol():
    """DbLinksStore: конструирование движка не коннектится к Postgres,
    все методы интерфейса на месте. Полный round-trip — на стенде с живой БД
    (qa-sed): локально Postgres нет, сеть в тестах не задеваем."""
    store = DbLinksStore("postgresql://localhost:1/sed")
    for method in ("find", "find_by_sam", "save"):
        assert callable(getattr(store, method))


# --- Эндпоинты ходят через get_links_store ---

class CountingLinksStore:
    """Обертка InMemory-хранилища со счетчиком вызовов протокола."""

    def __init__(self) -> None:
        self.inner = InMemoryLinksStore()
        self.calls = {"find": 0, "find_by_sam": 0, "save": 0}

    def find(self, key):
        self.calls["find"] += 1
        return self.inner.find(key)

    def find_by_sam(self, sam):
        self.calls["find_by_sam"] += 1
        return self.inner.find_by_sam(sam)

    def save(self, record):
        self.calls["save"] += 1
        self.inner.save(record)

    def ensure_user(self, row):
        self.calls["ensure_user"] = self.calls.get("ensure_user", 0) + 1
        self.inner.ensure_user(row)


def _bases():
    return {
        "zup_t1": OneCBaseConfig(
            code="zup_t1",
            enterprise=ENT,
            url="https://1c-mock.local/t1",
            user="reader",
            secret="s1",
        ),
    }


class FakeTransport:
    """Мок-HTTP 1С: карточка Ивана по таб. номеру 001 (справочник Catalog_Сотрудники)
    + регистр кадровых данных (второй запрос карточки)."""

    def get(self, url, headers, timeout):
        parsed = urllib.parse.urlparse(url)
        filter_str = urllib.parse.unquote(
            urllib.parse.parse_qs(parsed.query).get("$filter", [""])[0]
        )
        if "/t1/" not in url:
            return HttpResult(status=404, body="{}")
        row = {
            "Ref_Key": "ref-001",
            "Code": "001",
            "Description": FIO_IVAN,
            "ГоловнаяОрганизация_Key": ENT,
        }
        if "InformationRegister" in url:
            if "guid'ref-001'" in filter_str:
                return HttpResult(
                    status=200,
                    body=json.dumps(
                        {
                            "value": [
                                {
                                    "Сотрудник_Key": "ref-001",
                                    "ТекущееПодразделение": {"Description": "Цех Тестовый"},
                                    "ТекущаяДолжность": {"Description": "Тестировщик"},
                                    "ДатаПриема": "2023-01-15",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            return HttpResult(status=200, body=json.dumps({"value": []}))
        if "substringof" in filter_str:
            m = re.search(r"substringof\('([^']*)'", filter_str)
            needle = (m.group(1) if m else "").lower()
            hit = [row] if needle in FIO_IVAN.lower() else []
            return HttpResult(status=200, body=json.dumps({"value": hit}, ensure_ascii=False))
        m = re.search(r"eq '([^']*)'", filter_str)
        tab = m.group(1) if m else ""
        if tab == "001":
            return HttpResult(status=200, body=json.dumps({"value": [row]}, ensure_ascii=False))
        return HttpResult(status=200, body=json.dumps({"value": []}, ensure_ascii=False))


class FakeGateway:
    """Фейк шлюза LDAP (вымышленные записи)."""

    def __init__(self, entries):
        self._by_sam = {e["sAMAccountName"].lower(): dict(e) for e in entries}

    def bind(self):
        return None

    def search_user_by_sam(self, sam):
        found = self._by_sam.get(sam.strip().lower())
        return dict(found) if found else None

    def search_user_by_dn(self, dn):
        return None

    def search_users(self, query):
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            dict(e)
            for e in self._by_sam.values()
            if needle in e["displayName"].lower()
        ]


def _reader():
    settings = AdReaderSettings(
        ad_url="ldaps://mock.local:636",
        base_dn="OU=SED,DC=example,DC=local",
        reader_dn="CN=sed-reader,OU=SED,DC=example,DC=local",
        cache_ttl_seconds=300,
        timeout_seconds=5.0,
    )
    entry = {
        "dn": "CN=%s,OU=SED,DC=example,DC=local" % FIO_IVAN,
        "sAMAccountName": "t.ivan",
        "displayName": FIO_IVAN,
        "manager": "",
        "memberOf": [],
        "department": "Цех Тестовый",
        "title": "Тестировщик",
        "mail": "t.ivan@example.local",
        "userAccountControl": 512,
    }
    return AdReader(settings=settings, gateway=FakeGateway([entry]), cache=InMemoryCache())


def _hr_headers():
    """Заголовки ОК (X-Mock-*; замена LDAPS bind в офлайн-тестах)."""
    import base64

    b64 = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")
    return {
        "X-Mock-Sam": "ok.ivnova",
        "X-Mock-Fio": b64("Иванова Ольга Петровна"),
        "X-Mock-Mail": b64("ok.ivnova@example.com"),
        "X-Mock-Department": b64("Отдел кадров"),
        "X-Mock-Title": b64("Специалист по кадрам"),
        "X-Mock-Groups": "SED_HR",
    }


def _admin_headers():
    """Заголовки админа (привязка 1С-AD — только админ)."""
    import base64

    b64 = lambda s: base64.b64encode(s.encode("utf-8")).decode("ascii")
    return {
        "X-Mock-Sam": "root.adm",
        "X-Mock-Fio": b64("Админов Корень Системович"),
        "X-Mock-Groups": "SED_ADMINS",
    }


@pytest.fixture
def links_override():
    """Тестовые группы + мок-клиент 1С/ридер AD + возврат подмен после теста."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    client = OneCClient(_bases(), transport=FakeTransport(), failure_threshold=100)
    reader = _reader()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_onec_client] = lambda: client
    app.dependency_overrides[get_ad_reader] = lambda: reader
    yield {"settings": settings, "client": client, "reader": reader}
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_onec_client, None)
    app.dependency_overrides.pop(get_ad_reader, None)


@pytest.fixture(autouse=True)
def clean_links():
    """Чистое офлайн-хранилище связок на каждый тест."""
    get_memory_links_store().reset()
    yield
    get_memory_links_store().reset()


def test_endpoints_go_through_store(client, links_override):
    """Создание и чтение связки идут через зависимость get_links_store."""
    store = CountingLinksStore()
    app.dependency_overrides[get_links_store] = lambda: store
    try:
        headers = _admin_headers()
        created = client.post(
            "/link_1c_ad",
            json={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001", "sam": "t.ivan"},
            headers=headers,
        )
        assert created.status_code == 201
        assert store.calls["save"] == 1
        assert created.json()["link"]["sam"] == "t.ivan"

        read = client.get(
            "/link_1c_ad",
            params={"enterprise": ENT, "base_code": "zup_t1", "tab_num": "001"},
            headers=headers,
        )
        assert read.status_code == 200
        assert store.calls["find"] == 1
        assert read.json()["link"]["verified"] is True
    finally:
        app.dependency_overrides.pop(get_links_store, None)
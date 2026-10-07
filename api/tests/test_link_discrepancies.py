# -*- coding: utf-8 -*-
"""Расхождения автосопоставления 1С↔AD и массовое подтверждение.

Персоны вымышленные, AD/1С — только моки (сеть не трогаем).
Проверяем: проход складывает расхождения, выдача фильтрует и пагинирует,
подтверждение пачкой создаёт связки только там, где кандидат AD один."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_sync import (  # noqa: E402
    REASON_AD_DUPLICATE,
    REASON_NOT_IN_AD,
    REASON_ONE_C_DUPLICATE,
    run_ad_sync,
)
from app.link import clear_for_tests, get_memory_links_store  # noqa: E402
from app.main import app  # noqa: E402
from tests.test_ad_sync import (  # noqa: E402
    ENT,
    FIO_1C_DUBL,
    FIO_AD_DUBL,
    FIO_IVAN,
    _ad_entry,
    _ad_entry_as,
    _hr_client,
    _reader,
)

from app.config import Settings, get_settings  # noqa: E402
from app.link_store import get_links_store  # noqa: E402


@pytest.fixture
def admin_roles():
    """Карта групп AD -> роли: SED_ADMINS=admin (как на стенде)."""
    settings = Settings(
        ALLOWED_AD_GROUPS="SED_HR,SED_ADMINS",
        ADMIN_GROUPS="SED_ADMINS",
        HR_GROUPS="SED_HR",
        HR_ADMIN_GROUPS="SED_HR_ADMIN",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def discrepancies(admin_roles):
    """Проход автосвязки на моках + in-memory хранилище с расхождениями.

    Состав AD повторяет боевой случай: у дублей ФИО в 1С есть ЕДИНСТВЕННЫЙ
    кандидат AD (иначе это был бы not_in_ad, а не спорный случай для админа)."""
    store = get_memory_links_store()
    clear_for_tests()
    reader = _reader(
        [
            _ad_entry_as("t.dubl", FIO_1C_DUBL, "Инженер-тест", "Цех Тестовый"),
            _ad_entry("t.ivan", FIO_IVAN),
            _ad_entry("t.ad1", FIO_AD_DUBL),
            _ad_entry("t.ad2", FIO_AD_DUBL),
        ]
    )
    result = run_ad_sync(_hr_client(), reader, store, [ENT])
    app.dependency_overrides[get_links_store] = lambda: store
    yield store, result
    app.dependency_overrides.pop(get_links_store, None)


# --- Сбор расхождений проходом -----------------------------------------------

def test_pass_records_discrepancy_reasons(discrepancies):
    """Причины расхождений соответствуют тому, что проход не смог связать.

    002 — две записи AD с одним ФИО (ad_duplicate); 003/004 — две карточки 1С
    с одним ФИО (one_c_duplicate); 005 — в AD нет такого ФИО (not_in_ad);
    001 — уникальный ФИО, связался, в расхождениях его нет."""
    store, result = discrepancies
    counts = result.counts_by_reason()
    assert counts.get(REASON_AD_DUPLICATE, 0) >= 1
    assert counts.get(REASON_ONE_C_DUPLICATE, 0) >= 1
    assert counts.get(REASON_NOT_IN_AD, 0) >= 1
    # 001 и 003 связались автоматически (уникальное ФИО / совпадение должности) —
    # их в расхождениях нет; в расхождения попадает не связанная карточка 004.
    tabs = {row["tab_num"] for row in result.discrepancies}
    assert "001" not in tabs and "003" not in tabs
    assert "002" in tabs and "004" in tabs and "005" in tabs
    assert store.find("%s|zup_t1|003" % ENT) is not None


def test_pass_saves_discrepancies_to_store(discrepancies):
    """Расхождения складываются в наше хранилище (выдачу админа)."""
    store, result = discrepancies
    assert result.discrepancies_saved == len(result.discrepancies)
    assert store.count_discrepancies() == len(result.discrepancies)


def test_discrepancy_row_carries_decision_data(discrepancies):
    """В строке есть всё для решения: кандидат AD и должность/служба 1С."""
    store, _result = discrepancies
    rows = store.list_discrepancies(reason=REASON_ONE_C_DUPLICATE, limit=50)
    assert rows
    row = rows[0]
    assert row["fio"] == FIO_1C_DUBL
    assert row["ad_sam"] == "t.dubl"  # кандидат AD известен
    assert row["ad_dept"] and row["ad_title"]
    # у карточек-дублей есть должность из регистра (иначе выбирать нечем)
    assert any(item.get("one_c_position") for item in rows)
    assert all(item["key"] for item in rows)


def test_ad_duplicate_row_lists_candidates(discrepancies):
    """При двух записях AD видно их логины: выбор делает человек."""
    store, _result = discrepancies
    rows = store.list_discrepancies(reason=REASON_AD_DUPLICATE, limit=10)
    assert rows and rows[0]["fio"] == FIO_AD_DUBL
    assert sorted(rows[0]["detail"]["candidates"]) == ["t.ad1", "t.ad2"]


# --- Выдача расхождений (GET) -------------------------------------------------

def test_list_discrepancies_roles(client, admin_headers, hr_headers, discrepancies):
    assert client.get("/link_1c_ad/discrepancies", headers=hr_headers).status_code == 403
    assert client.get("/link_1c_ad/discrepancies", headers={}).status_code == 401
    response = client.get("/link_1c_ad/discrepancies", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"]
    assert body["total"] == len(body["items"])
    assert body["counts"]


def test_list_discrepancies_filter_and_can_confirm(client, admin_headers, discrepancies):
    response = client.get(
        "/link_1c_ad/discrepancies?reason=%s&page_size=1" % REASON_ONE_C_DUPLICATE,
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["reason"] == REASON_ONE_C_DUPLICATE
    assert body["items"][0]["can_confirm"] is True  # кандидат AD один
    assert body["total"] >= 1
    # Постранично: total больше выдачи
    first = client.get(
        "/link_1c_ad/discrepancies?reason=%s&page=1&page_size=1" % REASON_ONE_C_DUPLICATE,
        headers=admin_headers,
    ).json()
    assert first["page_size"] == 1


def test_list_discrepancies_query_filter(client, admin_headers, discrepancies):
    response = client.get(
        "/link_1c_ad/discrepancies?q=%s" % FIO_IVAN, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0  # связался, в расхождениях его нет


def test_ad_duplicate_cannot_confirm(client, admin_headers, discrepancies):
    """Несколько записей AD — подтверждать нельзя, выбор за человеком.

    Строка всё равно показывает первого кандидата (его служба/должность нужны
    для сравнения) и список логинов, но пакетное подтверждение её пропустит."""
    body = client.get(
        "/link_1c_ad/discrepancies?reason=%s" % REASON_AD_DUPLICATE, headers=admin_headers
    ).json()
    assert body["items"]
    assert all(item["can_confirm"] is False for item in body["items"])
    assert all(sorted(item["candidates"]) == ["t.ad1", "t.ad2"] for item in body["items"])


# --- Массовое подтверждение ---------------------------------------------------

def test_confirm_creates_links_and_resolves(client, admin_headers, discrepancies):
    store, _result = discrepancies
    rows = store.list_discrepancies(reason=REASON_ONE_C_DUPLICATE, limit=50)
    keys = [row["key"] for row in rows]
    response = client.post(
        "/link_1c_ad/discrepancies/confirm", json={"keys": keys}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["linked"] == len(keys)
    assert body["skipped"] == []
    assert body["linked_sams"]
    # Связки появились, подтверждённые строки закрыты; остальные (неоднозначные)
    # остаются открытыми — их решает человек.
    for row in rows:
        record = store.find(row["key"])
        assert record is not None and record.verified is True
    open_keys = {item["key"] for item in store.list_discrepancies(only_open=True, limit=200)}
    assert not (open_keys & set(keys))
    assert store.count_discrepancies(only_open=False) == len(_result.discrepancies)


def test_confirm_skips_ambiguous_and_reports(client, admin_headers, discrepancies):
    """В пакете смешанные строки: неоднозначные пропускаются, ошибки в ответе."""
    store, _result = discrepancies
    duplicate = store.list_discrepancies(reason=REASON_AD_DUPLICATE, limit=5)
    single = store.list_discrepancies(reason=REASON_ONE_C_DUPLICATE, limit=1)
    keys = [duplicate[0]["key"], single[0]["key"]]
    response = client.post(
        "/link_1c_ad/discrepancies/confirm", json={"keys": keys}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["linked"] == 1
    assert body["skipped"] == [duplicate[0]["key"]]


def test_confirm_roles_and_validation(client, admin_headers, hr_headers, discrepancies):
    assert (
        client.post(
            "/link_1c_ad/discrepancies/confirm", json={"keys": ["a|b|1"]}, headers=hr_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/link_1c_ad/discrepancies/confirm", json={"keys": []}, headers=admin_headers
        ).status_code
        == 422
    )


def test_confirm_unknown_key_is_noop(client, admin_headers, discrepancies):
    store, _result = discrepancies
    before = store.count_discrepancies()
    response = client.post(
        "/link_1c_ad/discrepancies/confirm",
        json={"keys": ["НЕТ|база|999"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["linked"] == 0
    assert store.count_discrepancies() == before


def test_confirm_writes_audit(client, admin_headers, discrepancies):
    """Массовое подтверждение оставляет след в аудите (кто и сколько подтвердил)."""
    from app.audit import audit_log

    audit_log.clear_for_tests()
    store = get_memory_links_store()
    rows = store.list_discrepancies(reason=REASON_ONE_C_DUPLICATE, limit=2)
    assert rows, "нужно хотя бы одно подтверждаемое расхождение"
    response = client.post(
        "/link_1c_ad/discrepancies/confirm",
        json={"keys": [rows[0]["key"]]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    actions = [event.action for event in audit_log.all()]
    assert "link.discrepancies_confirm" in actions

def test_pass_reports_saved_discrepancy_count(discrepancies):
    """В ответе прохода есть счётчик сохранённых строк расхождений.

    Без него ответ /link_1c_ad/sync падал бы AttributeError — поле AdSyncResult
    должно объявляться в самом классе, а не только заполняться в run_ad_sync.
    """
    from app.ad_sync import AdSyncResult

    assert AdSyncResult().discrepancies_saved == 0
    _store, result = discrepancies
    assert result.discrepancies_saved == len(result.discrepancies)


# --- Уволенных сопоставление не трогает ---------------------------------------

def test_pass_skips_dismissed_employees(discrepancies):
    """В проход попадают только те, кто сейчас работает.

    Решение человека: уволенным связь и маршрут не нужны, а в расхождениях они
    мешают разбору. Дата увольнения — из регистра 1С (локальный справочник)."""
    store, _result = discrepancies
    dismissed = {"%s|zup_t1|004" % ENT}
    before = store.count_discrepancies()
    result = run_ad_sync(_hr_client(), _reader(), store, [ENT], dismissed=dismissed)
    # Карточка 004 пропущена: её ключа нет среди расхождений прохода.
    skipped_key = "%s|zup_t1|004" % ENT
    assert result.skipped_dismissed == 1
    collected = {
        "%s|%s|%s" % (row["enterprise"], row["base_code"], row["tab_num"])
        for row in result.discrepancies
    }
    assert skipped_key not in collected
    # В БД старая строка осталась: проход перезаписывает выдачу, но если ключа
    # больше нет среди расхождений — он исчезает из таблицы.
    assert store.count_discrepancies() != before or before == 0


def test_dismissed_keys_from_directory():
    """Ключи уволенных строятся по дате из справочника (сегодня и раньше — уволен)."""
    from datetime import date, timedelta

    from app.employee_sync import InMemoryEmployeeSyncStore

    today = date.today()
    store = InMemoryEmployeeSyncStore()
    store.upsert_many(
        [
            {
                "enterprise": "ENT",
                "base_code": "zup_t1",
                "tab_num": "001",
                "fio": "Работает",
                "dismissal_date": None,
            },
            {
                "enterprise": "ENT",
                "base_code": "zup_t1",
                "tab_num": "002",
                "fio": "Уволен вчера",
                "dismissal_date": (today - timedelta(days=1)).isoformat(),
            },
            {
                "enterprise": "ENT",
                "base_code": "zup_t1",
                "tab_num": "003",
                "fio": "Увольнение завтра",
                "dismissal_date": (today + timedelta(days=1)).isoformat(),
            },
        ]
    )
    assert store.dismissed_keys() == {"ENT|zup_t1|002"}

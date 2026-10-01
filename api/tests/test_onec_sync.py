# Тесты синхронизации справочника предприятий из 1С (Фаза C1): onec_sync.
# Список предприятий берётся ИЗ КАЖДОЙ БАЗЫ (сущность организаций); в базе может
# быть несколько предприятий. Транспорт http_get патчится (никакой сети в тестах).
# ПДн/предприятия вымышленные.

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.onec_sync as onec_sync  # noqa: E402
from app.onec_sync import (  # noqa: E402
    OnecSyncUnavailable,
    due_schedule,
    maybe_sync_weekly,
    sync_enterprises,
)


class InMemorySettingsStore:
    """Мок хранилища settings (сид-формат значений), как в test_settings_api."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value


def _base(code="zup_a", url="https://1c-mock.local/a", org="Catalog_Организации"):
    """Одна база в сид-формате (значение списка onec_bases)."""
    return {"code": code, "name": "База " + code, "url": url, "organization_entity": org}


def _store(bases=None, synced_at=None):
    data = {}
    if bases is not None:
        data["onec_bases"] = json.dumps(bases, ensure_ascii=False)
    if synced_at is not None:
        data["onec_enterprises_synced_at"] = json.dumps(synced_at)
    return InMemorySettingsStore(data)


def _orgs_json(*pairs):
    """OData-ответ сущности организаций: список (code, name).

    У ЗУП «Организаций» нет Code — по умолчанию код = Ref_Key, имя = Description."""
    items = [{"Ref_Key": code, "Description": name} for code, name in pairs]
    return json.dumps({"value": items}, ensure_ascii=False)


# --- sync_enterprises ---

def test_sync_unconfigured_raises():
    """Базы не настроены — OnecSyncUnavailable с понятным текстом."""
    with pytest.raises(OnecSyncUnavailable) as exc_info:
        sync_enterprises(_store(bases=[]))
    assert "не настроены" in str(exc_info.value)


def test_sync_one_base_single_enterprise(monkeypatch):
    """Одна база, одно предприятие: справочник + маппинг {code:[base]}."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: _orgs_json(("A", "Альфа")))
    store = _store(bases=[_base()])
    items = sync_enterprises(store)
    assert items == [{"code": "A", "name": "Альфа"}]
    assert json.loads(store._data["enterprises"]) == [{"code": "A", "name": "Альфа"}]
    assert json.loads(store._data["onec_enterprise_bases"]) == {"A": ["zup_a"]}
    assert isinstance(json.loads(store._data["onec_enterprises_synced_at"]), str)


def test_sync_base_with_multiple_enterprises(monkeypatch):
    """В ОДНОЙ базе НЕСКОЛЬКО предприятий — маппинг по одному base_code."""
    monkeypatch.setattr(
        onec_sync, "http_get", lambda *a, **kw: _orgs_json(("A", "Альфа"), ("B", "Бета"))
    )
    store = _store(bases=[_base()])
    sync_enterprises(store)
    assert json.loads(store._data["onec_enterprise_bases"]) == {
        "A": ["zup_a"],
        "B": ["zup_a"],
    }


def test_sync_two_bases_union_and_mapping(monkeypatch):
    """Две базы: union предприятий + маппинг по каждому base_code."""
    def route(url, user, password):
        if "/a/" in url:
            return _orgs_json(("A", "Альфа"))
        if "/b/" in url:
            return _orgs_json(("A", "Альфа"), ("B", "Бета"))
        raise AssertionError("неожиданный url: " + url)

    monkeypatch.setattr(onec_sync, "http_get", route)
    store = _store(bases=[_base("zup_a", "https://1c-mock.local/a"), _base("zup_b", "https://1c-mock.local/b")])
    items = sync_enterprises(store)
    assert {i["code"] for i in items} == {"A", "B"}
    mapping = json.loads(store._data["onec_enterprise_bases"])
    assert mapping == {"A": ["zup_a", "zup_b"], "B": ["zup_b"]}


def test_sync_one_base_down_others_alive(monkeypatch):
    """Падение одной базы не валит остальные."""
    def route(url, user, password):
        if "/a/" in url:
            raise RuntimeError("база A недоступна")
        return _orgs_json(("B", "Бета"))

    monkeypatch.setattr(onec_sync, "http_get", route)
    store = _store(bases=[_base("zup_a", "https://1c-mock.local/a"), _base("zup_b", "https://1c-mock.local/b")])
    items = sync_enterprises(store)
    assert items == [{"code": "B", "name": "Бета"}]
    assert json.loads(store._data["onec_enterprise_bases"]) == {"B": ["zup_b"]}


def test_sync_all_bases_down_raises(monkeypatch):
    """Все базы упали — OnecSyncUnavailable с текстом ошибок."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("нет сети")))
    with pytest.raises(OnecSyncUnavailable) as exc_info:
        sync_enterprises(_store(bases=[_base(), _base("zup_b", "https://1c-mock.local/b")]))
    assert "Ни одна база" in str(exc_info.value)


def test_sync_skips_entries_without_code(monkeypatch):
    """Записи без Ref_Key пропускаются; пустое имя — как есть."""
    body = '{"value": [{"Ref_Key": "A", "Description": "Альфа"}, {"Description": "Без кода"}]}'
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: body)
    items = sync_enterprises(_store(bases=[_base()]))
    assert items == [{"code": "A", "name": "Альфа"}]


def test_sync_bad_json_raises(monkeypatch):
    """Битый JSON от базы — база считается упавшей, без баз — OnecSyncUnavailable."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: "{broken json")
    with pytest.raises(OnecSyncUnavailable):
        sync_enterprises(_store(bases=[_base()]))


# --- maybe_sync_weekly ---

def test_weekly_unconfigured_false(monkeypatch):
    """Базы не настроены — False (тихо), http_get не вызывается."""
    called = []
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: called.append(1) or "[]")
    assert maybe_sync_weekly(_store(bases=[])) is False
    assert called == []


def test_weekly_no_last_syncs(monkeypatch):
    """Нет отметки синхронизации — выполняется, True."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: _orgs_json(("A", "Альфа")))
    store = _store(bases=[_base()])
    assert maybe_sync_weekly(store) is True
    assert json.loads(store._data["enterprises"]) == [{"code": "A", "name": "Альфа"}]


def test_weekly_fresh_skips(monkeypatch):
    """Синхронизация моложе 7 дней — False, http_get не вызывается."""
    called = []
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: called.append(1) or "[]")
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert maybe_sync_weekly(_store(bases=[_base()], synced_at=fresh)) is False
    assert called == []


def test_weekly_old_syncs(monkeypatch):
    """Синхронизация старше 7 дней — выполняется, True."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: _orgs_json())
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert maybe_sync_weekly(_store(bases=[_base()], synced_at=old)) is True


def test_weekly_sync_error_false(monkeypatch):
    """Сбой синхронизации — False (worker не падает)."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("сеть недоступна")))
    assert maybe_sync_weekly(_store(bases=[_base()])) is False


# --- due_schedule (расписание регламентов: interval/daily/без расписания) ---

def _schedule(mode="interval", interval_hours=3, daily_time="03:00"):
    """Расписание в сид-формате (значение ключа schedule_*)."""
    return {"mode": mode, "interval_hours": interval_hours, "daily_time": daily_time, "notify": False}


def test_due_interval_past_due():
    """mode=interval: now >= last + interval_hours — пора."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    last = (now - timedelta(hours=4)).isoformat()
    assert due_schedule(_schedule(interval_hours=3), last, now) is True


def test_due_interval_fresh_skips():
    """mode=interval: last моложе интервала — не пора."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    last = (now - timedelta(hours=1)).isoformat()
    assert due_schedule(_schedule(interval_hours=3), last, now) is False


def test_due_interval_no_last_due():
    """mode=interval, отметки нет — пора (True)."""
    assert due_schedule(_schedule(interval_hours=3), None) is True


def test_due_daily_passed_and_last_yesterday_due():
    """mode=daily: время сегодня уже наступило, last вчера — пора."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    last = datetime(2026, 9, 30, 9, 0, 0, tzinfo=timezone.utc).isoformat()
    assert due_schedule(_schedule(mode="daily", daily_time="03:00"), last, now) is True


def test_due_daily_time_not_reached():
    """mode=daily: время ещё не наступило — не пора (даже без отметки)."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert due_schedule(_schedule(mode="daily", daily_time="15:00"), None, now) is False


def test_due_daily_already_ran_today():
    """mode=daily: последний запуск уже был сегодня после daily_time — не пора."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    last = datetime(2026, 10, 1, 3, 5, 0, tzinfo=timezone.utc).isoformat()
    assert due_schedule(_schedule(mode="daily", daily_time="03:00"), last, now) is False


def test_due_no_schedule_falls_back_to_7_days():
    """Расписания нет — раз в 7 дней от last_raw (совместимость)."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=8)).isoformat()
    assert due_schedule(None, fresh, now) is False
    assert due_schedule({}, old, now) is True
    assert due_schedule(None, None, now) is True


def test_due_broken_last_is_due():
    """Битое last — «пора» (True)."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert due_schedule(_schedule(interval_hours=3), "не-дата", now) is True


def test_due_invalid_schedule_falls_back_to_7_days():
    """Невалидное расписание (интервал 0/битый, битое время, чужой режим) —
    фолбэк «раз в 7 дней», а не «пора всегда» (worker не гоняет каждый проход)."""
    now = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(days=8)).isoformat()
    assert due_schedule(_schedule(mode="interval", interval_hours=0), fresh, now) is False
    assert due_schedule(_schedule(mode="interval", interval_hours="много"), old, now) is True
    assert due_schedule(_schedule(mode="daily", daily_time="25:99"), fresh, now) is False
    assert due_schedule({"mode": "неизвестный"}, fresh, now) is False


# --- maybe_sync_weekly с расписанием (мок store с сид-значениями) ---

def test_weekly_interval_due_syncs(monkeypatch):
    """Интервальное расписание: прошло 4 часа при интервале 3 — выполняется."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: _orgs_json(("A", "Альфа")))
    store = _store(bases=[_base()], synced_at=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat())
    store._data["schedule_enterprises_sync"] = json.dumps(_schedule(mode="interval", interval_hours=3), ensure_ascii=False)
    assert maybe_sync_weekly(store) is True
    assert json.loads(store._data["enterprises"]) == [{"code": "A", "name": "Альфа"}]


def test_weekly_interval_fresh_skips(monkeypatch):
    """Интервальное расписание: прошёл 1 час при интервале 3 — пропуск."""
    called = []
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: called.append(1) or "[]")
    store = _store(bases=[_base()], synced_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    store._data["schedule_enterprises_sync"] = json.dumps(_schedule(mode="interval", interval_hours=3), ensure_ascii=False)
    assert maybe_sync_weekly(store) is False
    assert called == []

# Тесты синхронизации справочника предприятий из 1С (Фаза 4): onec_sync.
# Источник — settings.onec_enterprises_source; транспорт http_get патчится
# (никакой сети в тестах). ПДн/предприятия вымышленные.

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


def _source(url="https://1c-mock.local/enterprises", user="", password=""):
    """Источник предприятий в сид-формате."""
    return json.dumps({"url": url, "user": user, "password": password})


def _store(source=None, synced_at=None):
    data = {}
    if source is not None:
        data["onec_enterprises_source"] = source
    if synced_at is not None:
        data["onec_enterprises_synced_at"] = json.dumps(synced_at)
    return InMemorySettingsStore(data)


# --- sync_enterprises ---

def test_sync_unconfigured_raises():
    """Источник не настроен — OnecSyncUnavailable с понятным текстом."""
    with pytest.raises(OnecSyncUnavailable) as exc_info:
        sync_enterprises(_store(source=_source(url="")))
    assert "не настроен" in str(exc_info.value)


def test_sync_list_format(monkeypatch):
    """Ответ списком: нормализация, запись в settings.enterprises и synced_at."""
    monkeypatch.setattr(
        onec_sync, "http_get", lambda *a, **kw: '[{"code": "A", "name": "Альфа"}]'
    )
    store = _store(source=_source())
    items = sync_enterprises(store)
    assert items == [{"code": "A", "name": "Альфа"}]
    assert json.loads(store._data["enterprises"]) == [{"code": "A", "name": "Альфа"}]
    assert isinstance(json.loads(store._data["onec_enterprises_synced_at"]), str)


def test_sync_wrapped_format(monkeypatch):
    """Ответ в конверте {"enterprises": [...]}: тот же результат."""
    monkeypatch.setattr(
        onec_sync,
        "http_get",
        lambda *a, **kw: '{"enterprises": [{"code": "B", "name": "Бета"}]}',
    )
    store = _store(source=_source())
    items = sync_enterprises(store)
    assert items == [{"code": "B", "name": "Бета"}]


def test_sync_odata_value_wrapper(monkeypatch):
    """Стандартная OData-обёртка 1С {"value": [...]} — принимается."""
    monkeypatch.setattr(
        onec_sync,
        "http_get",
        lambda *a, **kw: '{"value": [{"code": "C", "name": "Гамма"}]}',
    )
    items = sync_enterprises(_store(source=_source()))
    assert items == [{"code": "C", "name": "Гамма"}]


def test_sync_skips_entries_without_code(monkeypatch):
    """Записи без code пропускаются; пустая строка name — как есть."""
    monkeypatch.setattr(
        onec_sync,
        "http_get",
        lambda *a, **kw: '[{"code": "A", "name": "Альфа"}, {"name": "Без кода"}, "x"]',
    )
    items = sync_enterprises(_store(source=_source()))
    assert items == [{"code": "A", "name": "Альфа"}]


def test_sync_bad_json_raises(monkeypatch):
    """Битый JSON — OnecSyncUnavailable, а не 500."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: "{broken json")
    with pytest.raises(OnecSyncUnavailable):
        sync_enterprises(_store(source=_source()))


def test_sync_http_error_raises(monkeypatch):
    """Сбой HTTP — OnecSyncUnavailable с текстом ошибки."""
    def boom(*a, **kw):
        raise RuntimeError("1С недоступен")

    monkeypatch.setattr(onec_sync, "http_get", boom)
    with pytest.raises(OnecSyncUnavailable) as exc_info:
        sync_enterprises(_store(source=_source()))
    assert "1С недоступен" in str(exc_info.value)


def test_sync_unexpected_format_raises(monkeypatch):
    """Ни список, ни конверт — OnecSyncUnavailable."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: '"строка"')
    with pytest.raises(OnecSyncUnavailable):
        sync_enterprises(_store(source=_source()))


# --- maybe_sync_weekly ---

def test_weekly_unconfigured_false(monkeypatch):
    """Источник не настроен — False (тихо), http_get не вызывается."""
    called = []
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: called.append(1) or "[]")
    assert maybe_sync_weekly(_store(source=_source(url=""))) is False
    assert called == []


def test_weekly_no_last_syncs(monkeypatch):
    """Нет отметки синхронизации — выполняется, True."""
    monkeypatch.setattr(
        onec_sync, "http_get", lambda *a, **kw: '[{"code": "A", "name": "Альфа"}]'
    )
    store = _store(source=_source())
    assert maybe_sync_weekly(store) is True
    assert json.loads(store._data["enterprises"]) == [{"code": "A", "name": "Альфа"}]


def test_weekly_fresh_skips(monkeypatch):
    """Синхронизация моложе 7 дней — False, http_get не вызывается."""
    called = []
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: called.append(1) or "[]")
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert maybe_sync_weekly(_store(source=_source(), synced_at=fresh)) is False
    assert called == []


def test_weekly_old_syncs(monkeypatch):
    """Синхронизация старше 7 дней — выполняется, True."""
    monkeypatch.setattr(onec_sync, "http_get", lambda *a, **kw: "[]")
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert maybe_sync_weekly(_store(source=_source(), synced_at=old)) is True


def test_weekly_sync_error_false(monkeypatch):
    """Сбой синхронизации — False (worker не падает)."""
    def boom(*a, **kw):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(onec_sync, "http_get", boom)
    assert maybe_sync_weekly(_store(source=_source())) is False
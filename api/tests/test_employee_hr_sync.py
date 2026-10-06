# -*- coding: utf-8 -*-
"""Даты увольнения из регистра кадровых данных 1С: запись в справочник и отсечение
уволенных в поиске сотрудников. Персоны вымышленные, сети нет (только моки).

Правило (решение человека): увольнение определяется датой из регистра 1С, а не
состоянием учётной записи AD (её после увольнения отключают вручную)."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.employee_sync import (  # noqa: E402
    InMemoryEmployeeSyncStore,
    maybe_sync_hr_daily,
    sync_hr_dismissals,
)

ENT = "Предприятие-Тест"
BASE = "zup"


def _row(tab, fio, ref=None, dismissal=None, ad_sam=None):
    return {
        "enterprise": ENT,
        "base_code": BASE,
        "tab_num": tab,
        "fio": fio,
        "department": None,
        "position": None,
        "ref_key": ref,
        "ad_sam": ad_sam,
        "ad_status": "linked" if ad_sam else None,
        "dismissal_date": dismissal,
    }


def _store():
    store = InMemoryEmployeeSyncStore()
    today = date.today()
    store.upsert_many(
        [
            _row("001", "Работает Адиль", ref="ref-1", ad_sam="a.adil"),
            _row("002", "Работает Борис", ref="ref-2"),
            _row("003", "Работает Вера", ref="ref-3"),
            _row(
                "004",
                "Уволен Гусев",
                ref="ref-4",
                dismissal=(today - timedelta(days=1)).isoformat(),
            ),
            _row(
                "005",
                "Уволен сегодня Днев",
                ref="ref-5",
                dismissal=today.isoformat(),
            ),
            _row(
                "006",
                "Увольнение завтра Енина",
                ref="ref-6",
                dismissal=(today + timedelta(days=1)).isoformat(),
            ),
        ]
    )
    return store


# --- Отсечение уволенных в выдаче справочника -------------------------------

def test_search_hides_dismissed_keeps_working():
    """В выдаче только те, кто не уволен на текущий день.

    Уволенные (вчера и сегодня) скрыты; увольнение завтра — ещё работает;
    сотрудник без AD-учётки в выдаче остаётся (AD-состояние не критерий)."""
    store = _store()
    tabs = {row["tab_num"] for row in store.search(ENT, "", 50)}
    assert tabs == {"001", "002", "003", "006"}


def test_count_matching_matches_search_filter():
    store = _store()
    assert store.count_matching(ENT, "") == 4
    assert store.count_matching(ENT, "Работает") == 3


def test_search_filters_dismissed_by_query():
    store = _store()
    assert [row["tab_num"] for row in store.search(ENT, "Гусев", 50)] == []
    assert [row["tab_num"] for row in store.search(ENT, "Борис", 50)] == ["002"]


# --- Запись дат из регистра ---------------------------------------------------

def test_update_dismissals_sets_and_clears_dates():
    """Дата из регистра проставляется; пустая дата (увольнения не было) очищает."""
    store = _store()
    updated = store.update_dismissals(
        BASE,
        [
            {"ref_key": "ref-2", "dismissal_date": "2026-05-01"},
            {"ref_key": "ref-3", "dismissal_date": None},
            {"ref_key": "ref-unknown", "dismissal_date": "2026-05-02"},
        ],
    )
    assert updated == 2  # неизвестный ref_key игнорируется
    by_tab = {row["tab_num"]: row for row in store._rows.values()}
    assert by_tab["002"]["dismissal_date"] == "2026-05-01"
    assert by_tab["003"]["dismissal_date"] is None
    # 002 уволен 1 мая — исчезает из выдачи.
    assert "002" not in {row["tab_num"] for row in store.search(ENT, "", 50)}


def test_update_dismissals_ignores_other_base():
    store = _store()
    assert store.update_dismissals("other_base", [{"ref_key": "ref-2", "dismissal_date": "2026-05-01"}]) == 0


def test_update_dismissals_empty_rows():
    store = _store()
    assert store.update_dismissals(BASE, []) == 0


# --- Регламентный проход: расписание -----------------------------------------

class _SettingsStore:
    """Настройки в памяти в сид-формате (JSON-строки), как в БД.

    read_setting_value делает json.loads, поэтому значения храним строками —
    иначе расписание/метка читались бы как None и проход никогда не был «пора»."""

    def __init__(self, values):
        import json as _json

        self._values = {
            key: (_json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
            for key, value in values.items()
        }
        self.written = {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value):
        import json as _json

        self._values[key] = _json.dumps(value, ensure_ascii=False)
        self.written[key] = value


def test_hr_daily_skips_without_bases():
    store = _SettingsStore({})
    assert maybe_sync_hr_daily(store, InMemoryEmployeeSyncStore()) is False


def test_hr_daily_skips_when_not_due():
    """Расписание «раз в час», метка только что — не пора."""
    import json
    from datetime import datetime, timezone

    store = _SettingsStore(
        {
            "onec_bases": [{"code": "zup"}],
            "schedule_hr_dismissals_sync": {"mode": "interval", "interval_hours": 24},
            "hr_dismissals_synced_at": json.dumps(datetime.now(timezone.utc).isoformat()),
        }
    )
    assert maybe_sync_hr_daily(store, InMemoryEmployeeSyncStore()) is False


def test_hr_daily_writes_marker_on_success(monkeypatch):
    """Пора → проход отработал, метка записана."""
    store = _SettingsStore(
        {
            "onec_bases": [{"code": "zup"}],
            "schedule_hr_dismissals_sync": {"mode": "interval", "interval_hours": 24},
            "hr_dismissals_synced_at": None,
        }
    )
    called = {}

    def fake_sync(settings_store, emp_store=None):
        called["yes"] = True
        return {"scanned": 10, "updated": 4, "dismissed": 2, "errors": []}

    monkeypatch.setattr("app.employee_sync.sync_hr_dismissals", fake_sync)
    assert maybe_sync_hr_daily(store, InMemoryEmployeeSyncStore()) is True
    assert called["yes"] is True
    assert "hr_dismissals_synced_at" in store.written


def test_hr_daily_silent_on_failure(monkeypatch):
    """Сбой прохода не валит worker и метку не пишет."""
    store = _SettingsStore(
        {
            "onec_bases": [{"code": "zup"}],
            "schedule_hr_dismissals_sync": {"mode": "interval", "interval_hours": 24},
        }
    )

    def boom(settings_store, emp_store=None):
        raise RuntimeError("регистр недоступен")

    monkeypatch.setattr("app.employee_sync.sync_hr_dismissals", boom)
    assert maybe_sync_hr_daily(store, InMemoryEmployeeSyncStore()) is False
    assert "hr_dismissals_synced_at" not in store.written


# --- Роли для привилегированных эндпоинтов -----------------------------------

@pytest.fixture
def admin_roles():
    """Карта групп AD -> роли: SED_ADMINS=admin, SED_HR=hr (как на стенде)."""
    from app.config import Settings, get_settings
    from app.main import app

    settings = Settings(
        ALLOWED_AD_GROUPS="SED_HR,SED_ADMINS",
        ADMIN_GROUPS="SED_ADMINS",
        HR_GROUPS="SED_HR",
        HR_ADMIN_GROUPS="SED_HR_ADMIN",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


# --- Эндпоинт ----------------------------------------------------------------

def test_hr_sync_endpoint_roles(
    client, admin_headers, hr_headers, owner_headers, admin_roles
):
    """POST /employees/hr-sync — только админ (роли задаёт фикстура admin_roles)."""
    assert client.post("/employees/hr-sync", headers=hr_headers).status_code == 403
    assert client.post("/employees/hr-sync", headers=owner_headers).status_code == 403
    assert client.post("/employees/hr-sync", headers={}).status_code == 401
    # Админу без настроенных баз 1С — 503 (не 500): источник недоступен.
    response = client.post("/employees/hr-sync", headers=admin_headers)
    assert response.status_code == 503, response.text


def test_hr_sync_endpoint_success_shape(client, admin_headers, monkeypatch, admin_roles):
    """Успех: счётчики прохода и ISO-метка времени."""
    monkeypatch.setattr(
        "app.employees.sync_hr_dismissals",
        lambda store, emp_store=None: {
            "scanned": 100,
            "updated": 40,
            "dismissed": 7,
            "errors": [],
        },
    )
    response = client.post("/employees/hr-sync", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scanned"] == 100
    assert body["updated"] == 40
    assert body["dismissed"] == 7
    assert body["errors"] == []
    assert body["at"]


def test_sync_hr_dismissals_requires_bases():
    """Баз 1С нет — EmployeeSyncUnavailable (503 на эндпоинте)."""
    from app.employee_sync import EmployeeSyncUnavailable

    store = _SettingsStore({"onec_bases": []})
    with pytest.raises(EmployeeSyncUnavailable):
        sync_hr_dismissals(store, InMemoryEmployeeSyncStore())
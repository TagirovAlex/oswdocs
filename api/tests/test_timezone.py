# Часовой пояс системы (settings.timezone): он обслуживает всё сразу — показ,
# соединения БД, письма, печать, расписания и имена бэкапов. Сброс между тестами
# делает autouse-фикстура conftest; здесь зона задаётся явно, чтобы проверка была
# читаемой.
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import timezone as tz  # noqa: E402

# 2026-10-01T21:30Z — в Москве 2 октября 00:30, в UTC — 1 октября 21:30.
LATE = datetime(2026, 10, 1, 21, 30, tzinfo=timezone.utc)


def test_setting_wins_over_env(monkeypatch):
    """Настройка админа важнее TZ контейнера — иначе снова два правила времени."""
    monkeypatch.setenv("TZ", "UTC")
    tz.remember_timezone("Europe/Moscow")
    assert tz.current_timezone_name() == "Europe/Moscow"
    assert tz.current_timezone().utcoffset(LATE).total_seconds() == 3 * 3600


def test_env_used_before_setting_is_read(monkeypatch):
    """До чтения настройки (движок БД нужен, чтобы её прочитать) — TZ контейнера."""
    monkeypatch.setenv("TZ", "Europe/Samara")
    tz.reset_timezone_for_tests()
    assert tz.current_timezone_name() == "Europe/Samara"


def test_cleared_setting_falls_back_to_env(monkeypatch):
    """Ключ сняли — снова TZ контейнера, а не забытая зона."""
    monkeypatch.setenv("TZ", "Europe/Samara")
    tz.reset_timezone_for_tests()
    tz.remember_timezone("Europe/Moscow")
    assert tz.current_timezone_name() == "Europe/Moscow"
    tz.remember_timezone(None)
    assert tz.current_timezone_name() == "Europe/Samara"


def test_garbage_setting_is_ignored(monkeypatch):
    """Мусор в настройке не переключает зону (PUT его и не примет, 422)."""
    monkeypatch.setenv("TZ", "Europe/Moscow")
    tz.reset_timezone_for_tests()
    tz.remember_timezone("Марс/Средний")
    assert tz.current_timezone_name() == "Europe/Moscow"


def test_broken_env_timezone_is_ignored(monkeypatch):
    """Мусорный TZ в окружении не уходит ни в SQL, ни в интерфейс: зона UTC."""
    monkeypatch.setenv("TZ", "Марс/Средний")
    tz.reset_timezone_for_tests()
    assert tz.current_timezone_name() == "", "мусорный TZ не должен становиться зоной"
    assert tz.current_timezone() is timezone.utc


def test_session_timezone_skips_sqlite():
    """SQLite (round-trip тесты) set_config не знает — слушатель молчит."""

    class FakeSqlite:
        __module__ = "sqlite3.dbapi2"

        def execute(self, *_a, **_k):  # pragma: no cover - сюда не дойдём
            raise AssertionError("set_config до sqlite дойти не должен")

    tz._set_session_timezone(FakeSqlite(), None, None)


def test_session_timezone_set_on_checkout(monkeypatch):
    """Каждая выдача соединения Postgres получает set_config зоны системы.

    Именно checkout, а не connect: SET откатывается вместе с транзакцией, а пул
    катапует соединение — на connect зона жила бы только до первого отката.
    Значение уходит параметром, а не подстановкой в SQL."""
    monkeypatch.setenv("TZ", "UTC")
    tz.reset_timezone_for_tests()
    tz.remember_timezone("Europe/Kaliningrad")

    seen: list[tuple[str, tuple]] = []

    class FakeConnection:
        __module__ = "psycopg2.extensions"

        def execute(self, sql, params=None):
            seen.append((sql, params))

    tz._set_session_timezone(FakeConnection(), None, None)
    assert seen == [
        ("SELECT set_config('TimeZone', %s, false)", ("Europe/Kaliningrad",))
    ]


def test_session_timezone_not_set_when_unknown(monkeypatch):
    """Зона не задана нигде — set_config не отправляем (зона сервера)."""
    monkeypatch.delenv("TZ", raising=False)
    tz.reset_timezone_for_tests()

    seen: list[str] = []

    class FakeConnection:
        __module__ = "psycopg2.extensions"

        def execute(self, sql, params=None):  # pragma: no cover - не дойдём
            seen.append(sql)

    tz._set_session_timezone(FakeConnection(), None, None)
    assert seen == []


def test_session_timezone_error_does_not_break_request():
    """Ошибка set_config не должна ронять запрос (показ идёт от настройки)."""

    class Broken:
        __module__ = "psycopg2.extensions"

        def execute(self, *_a, **_k):
            raise RuntimeError("зона не принята сервером")

    tz._set_session_timezone(Broken(), None, None)  # не бросает


def test_refresh_from_settings_survives_broken_store():
    """Стартовое чтение зоны не роняет процесс, если настроек нет."""

    class Broken:
        def get(self, _key):
            raise RuntimeError("вымышленная недоступность настроек")

    tz.reset_timezone_for_tests()
    tz.refresh_timezone_from_settings(Broken())  # не бросает
    assert tz.current_timezone_name() == ""
    tz.refresh_timezone_from_settings(None)  # None — тоже не бросает


@pytest.mark.parametrize("name,expected", [
    ("Europe/Moscow", "02.10.2026 00:30"),
    ("UTC", "01.10.2026 21:30"),
])
def test_datetime_formatting_uses_system_zone(name, expected):
    """Один и тот же момент времени в бланке/письме — по зоне системы."""
    from app.docs import _step_done_text

    tz.reset_timezone_for_tests()
    tz.remember_timezone(name)
    assert _step_done_text(LATE) == expected
    tz.reset_timezone_for_tests()


def test_naive_datetime_left_as_is():
    """Метка без зоны (строка из старых данных) не сдвигается молча."""
    from app.docs import _step_done_text

    tz.reset_timezone_for_tests()
    tz.remember_timezone("Europe/Moscow")
    assert _step_done_text("2026-10-01T21:30:00") == "01.10.2026 21:30"
    tz.reset_timezone_for_tests()


def test_print_date_follows_system_zone(monkeypatch):
    """Дата печати бланка — «сегодня» в зоне системы, а не по зоне сервера."""
    from datetime import date

    from app.documents import _fill_hr_placeholders

    # Момент перед полуночью по Москве: в UTC это ещё предыдущие сутки.
    before_midnight_utc = datetime(2026, 10, 1, 21, 30, tzinfo=timezone.utc)
    # datetime.now(tz) возвращает момент В ЭТОЙ зоне — как настоящий, значит и
    # заглушка должна приводить к переданной зоне, иначе проверка ничего не скажет.
    monkeypatch.setattr(
        "app.documents.datetime",
        type("D", (), {"now": staticmethod(lambda tz_: before_midnight_utc.astimezone(tz_))}),
    )
    # Зона берётся настоящая, системная (ниже её и задаём) — это и проверяем.
    tz.remember_timezone("Europe/Moscow")

    context: dict = {}
    _fill_hr_placeholders(context, request=object(), routing_store=None, employee_store=None)
    # Зона системы MSK: 21:30Z + 3ч = уже 2 октября; при UTC было бы 1-е.
    assert context["date"] == date(2026, 10, 2)
    tz.reset_timezone_for_tests()
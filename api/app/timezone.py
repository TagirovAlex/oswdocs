# Часовой пояс системы — одна настройка на всё (решение человека 2026-10-13).
#
# Источник — ключ settings.timezone (его правит админ). До чтения настройки и если
# она снята — TZ контейнера из env; это единственное место в приложении, где
# смотрим на переменную окружения. Всё остальное идёт отсюда:
#
#   * часовая зона сессий Postgres — иначе `now()`/`CURRENT_DATE` в SQL считались
#     бы в Etc/UTC, пока интерфейс показывает Moscow: внутри системы было бы два
#     разных времени;
#   * тексты писем и бланков (docs._step_done_text), дата печати бланка
#     (documents), расписания синхронизаций (onec_sync), имена бэкапов
#     (archive) — всё это сотрудник сравнивает с тем, что видит в карточке.
#
# Показ в интерфейсе сюда не относится: браузер получает зону из /auth/me и
# форматирует через frontend/src/date-format.ts. Хранимое везде остаётся UTC —
# зона меняет только отображение и вычисления «сейчас».
#
# Цикл «движок БД нужен, чтобы прочитать настройку» разорван так: до чтения
# настройки зона берётся из TZ, после — из settings. На старте процесса зона
# читается явно (main/worker), а также при чтении ключа и после его записи
# (settings_routes) — поэтому смена зоны админом подхватывается без перезапуска.
from __future__ import annotations

import logging
import os
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.engine import Engine

_LOGGER = logging.getLogger(__name__)

# Зона из настройки админа; пустая — берём TZ контейнера.
_setting_timezone: str = ""
# Валидная зона из TZ, вычисляется один раз.
_env_timezone: str = ""
_env_timezone_read: bool = False


def _is_zone(name: str) -> bool:
    """Существует ли такая IANA-зона (единственный допуск к SQL и к /auth/me)."""
    if not name:
        return False
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def current_timezone_name() -> str:
    """Имя зоны системы; пустая строка — зона не задана (время сервера)."""
    global _env_timezone, _env_timezone_read
    if _setting_timezone:
        return _setting_timezone
    if not _env_timezone_read:
        _env_timezone_read = True
        name = (os.environ.get("TZ") or "").strip()
        _env_timezone = name if _is_zone(name) else ""
    return _env_timezone


def current_timezone() -> tzinfo:
    """Зона системы для вычислений: настройка, иначе TZ контейнера, иначе UTC."""
    name = current_timezone_name()
    return ZoneInfo(name) if name else timezone.utc


def remember_timezone(name: object) -> None:
    """Запомнить зону из settings.timezone (вызывает чтение этого ключа).

    Неизвестное имя игнорируем: PUT /settings его не примет (ZoneInfo в
    settings_routes), а мусор в БД не должен ломать переход времени. Ключ сняли
    (None или пусто) — снова TZ контейнера."""
    global _setting_timezone
    if isinstance(name, str) and _is_zone(name.strip()):
        _setting_timezone = name.strip()
    else:
        _setting_timezone = ""


def refresh_timezone_from_settings(store: object | None) -> None:
    """Прочитать settings.timezone из хранилища настроек (старт процесса, PUT).

    store того же типа, что у get_settings_store; ошибки не поднимаем — зона
    останется прежней (или TZ), хуже не будет."""
    if store is None:
        return
    try:
        from .settings_routes import read_setting_value

        remember_timezone(read_setting_value(store, "timezone"))  # type: ignore[arg-type]
    except Exception as exc:
        # Лог, а не тишина: иначе зона незаметно осталась бы прежней (TZ),
        # хотя админ её поменял, и система разошлась бы с интерфейсом молча.
        _LOGGER.debug("Зона из settings.timezone не прочитана (%s), остаётся прежняя", exc)


def reset_timezone_for_tests() -> None:
    """Сброс к состоянию «зона не задана» (только для изоляции pytest)."""
    global _setting_timezone, _env_timezone, _env_timezone_read
    _setting_timezone = ""
    _env_timezone = ""
    _env_timezone_read = False


def _is_sqlite(dbapi_connection: object) -> bool:
    return type(dbapi_connection).__module__.split(".")[0] == "sqlite3"


@event.listens_for(Engine, "checkout")
def _set_session_timezone(dbapi_connection, _connection_record, _proxy) -> None:
    """Часовая зона соединения Postgres — зона системы.

    Слушаем checkout, а не connect: SET TIME ZONE в PostgreSQL транзакционный, а
    пул откатывает соединение при возврате (reset_on_return=rollback) — на connect
    зона продержалась бы только до первого отката. На checkout она применяется
    перед выдачей соединения в работу, то есть ровно на ту транзакцию, которая
    сейчас будет выполнена.

    Слушатель на классе Engine, а не на конкретном движке: иначе про него надо
    помнить в каждом из одиннадцати create_engine, и рано или поздно появится
    забытый — с двумя разными правилами времени в двух углах системы.

    Значение идёт параметром (set_config), а не подстановкой в SQL: SET не
    принимает параметры, а валидация _is_zone выше пропускает только настоящие
    имена зон. SQLite (round-trip тесты) SET TIME ZONE не знает — пропускаем.
    Ошибка не поднимается: показ всё равно идёт от настройки, запрос падать не
    должен из-за настройки соединения."""
    if _is_sqlite(dbapi_connection):
        return
    name = current_timezone_name()
    if not name:
        return
    try:
        dbapi_connection.execute("SELECT set_config('TimeZone', %s, false)", (name,))
    except Exception as exc:
        # Лог обязателен: без него SQL (now()/CURRENT_DATE) молча считал бы в
        # Etc/UTC, пока Python-вычисления и интерфейс идут по зоне системы.
        _LOGGER.warning("Соединению Postgres не выставлена зона %s: %s", name, exc)
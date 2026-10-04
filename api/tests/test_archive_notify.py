# Тесты уведомления о выполненном бэкапе (ручной и регламентный):
# после успешного дампа письмо ставится в очередь каждому адресату
# archive_schedule (event=reglament), {{summary}} — имя файла и размер копии;
# notify=false/без адресатов/сбой очереди — бэкап не отменяется, сбой виден
# в аудите (archive.notify.skip). Сеть/БД не нужны: настройки — in-memory мок,
# очередь — записывающая, дамп — подменён (файл пишется в tmp).

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import archive  # noqa: E402
from app.archive import create_backup, maybe_backup_weekly  # noqa: E402
from app.audit import audit_log  # noqa: E402

MOMENT = datetime(2026, 10, 3, 20, 0, 28, tzinfo=timezone.utc)


class InMemorySettingsStore:
    """Мок хранилища настроек (сид-формат значений), как в test_reglament."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value


class RecordingQueue:
    """Мок очереди писем: запоминает enqueue-вызовы."""

    def __init__(self):
        self.enqueued = []

    def enqueue(self, message):
        self.enqueued.append(message)


class BrokenQueue:
    """Очередь, недоступная БД: enqueue всегда падает."""

    def enqueue(self, message):
        raise RuntimeError("queue unavailable")


def _schedule(**overrides):
    values = {
        "mode": "daily",
        "daily_time": "06:00",
        "notify": True,
        "subject": "Архивация OSWDocs",
        "body": "Сводка: {{summary}}",
        "recipients": ["a@example.com", "b@example.com"],
    }
    values.update(overrides)
    return values


def _store(schedule=None, last=None):
    data = {}
    if schedule is not None:
        data["archive_schedule"] = json.dumps(schedule, ensure_ascii=False)
    if last is not None:
        data["archive_backup_at"] = json.dumps(last.isoformat())
    return InMemorySettingsStore(data)


@pytest.fixture(autouse=True)
def _clean_audit():
    audit_log.clear_for_tests()
    yield
    audit_log.clear_for_tests()


@pytest.fixture
def fake_dump(monkeypatch):
    """Дамп без БД: пишет файл целиком в целевой путь."""

    def _dump(database_url, target, moment=None):
        target.write_text("-- тестовый дамп\n", encoding="utf-8")

    monkeypatch.setattr(archive, "dump_database", _dump)
    return _dump


def test_create_backup_enqueues_reglament_letter(tmp_path, fake_dump):
    """Ручной бэкап: каждому адресату archive_schedule — письмо event=reglament."""
    queue = RecordingQueue()
    store = _store(_schedule())
    target = create_backup(
        store,
        "postgresql://unused",
        str(tmp_path),
        MOMENT,
        queue=queue,
        smtp_from="sed@example.com",
    )
    assert target.is_file()
    assert [m.to for m in queue.enqueued] == ["a@example.com", "b@example.com"]
    assert all(m.event == "reglament" for m in queue.enqueued)
    assert queue.enqueued[0].subject == "sed@example.com Архивация OSWDocs"
    assert target.name in queue.enqueued[0].html
    assert "МБ" in queue.enqueued[0].html


def test_create_backup_updates_backup_marker(tmp_path, fake_dump):
    """Метка archive_backup_at пишется после дампа (единая для обоих запусков)."""
    store = _store(_schedule())
    create_backup(store, "postgresql://unused", str(tmp_path), MOMENT)
    assert json.loads(store.get("archive_backup_at")) == MOMENT.isoformat()


def test_create_backup_without_queue_sends_nothing(tmp_path, fake_dump):
    """Без очереди (queue=None) уведомление не отправляется — вызов без 500."""
    store = _store(_schedule())
    target = create_backup(store, "postgresql://unused", str(tmp_path), MOMENT)
    assert target.is_file()


def test_notify_false_sends_nothing(tmp_path, fake_dump):
    """notify=false — писем нет, бэкап создан."""
    queue = RecordingQueue()
    target = create_backup(
        _store(_schedule(notify=False)),
        "postgresql://unused",
        str(tmp_path),
        MOMENT,
        queue=queue,
    )
    assert target.is_file()
    assert queue.enqueued == []


def test_no_recipients_sends_nothing(tmp_path, fake_dump):
    """Пустой список адресатов — писем нет, бэкап создан."""
    queue = RecordingQueue()
    target = create_backup(
        _store(_schedule(recipients=[])),
        "postgresql://unused",
        str(tmp_path),
        MOMENT,
        queue=queue,
    )
    assert target.is_file()
    assert queue.enqueued == []


def test_queue_failure_does_not_fail_backup(tmp_path, fake_dump):
    """Сбой очереди не роняет бэкап; причина — в аудите без ПДн."""
    target = create_backup(
        _store(_schedule()),
        "postgresql://unused",
        str(tmp_path),
        MOMENT,
        queue=BrokenQueue(),
        smtp_from="sed@example.com",
    )
    assert target.is_file()
    skips = [e for e in audit_log.all() if e.action == "archive.notify.skip"]
    assert len(skips) == 1
    assert skips[0].entity_id == target.name


def test_maybe_backup_weekly_enqueues_when_due(tmp_path, fake_dump):
    """Регламентный бэкап «пора» — True и письмо по archive_schedule.

    Время «пора» определяется по системным часам, поэтому daily_time=00:00
    (always в прошелом) и метка вчера — «пора» в любой момент запуска теста.
    """
    queue = RecordingQueue()
    done = maybe_backup_weekly(
        _store(
            _schedule(daily_time="00:00"),
            last=datetime.now(timezone.utc) - timedelta(days=1),
        ),
        "postgresql://unused",
        str(tmp_path),
        queue=queue,
        smtp_from="sed@example.com",
    )
    assert done is True
    assert len(queue.enqueued) == 2
    assert queue.enqueued[0].event == "reglament"


def test_maybe_backup_weekly_not_due_no_letters(tmp_path, fake_dump):
    """Не «пора» (бэкап только что был) — дампа и писем нет."""
    queue = RecordingQueue()
    done = maybe_backup_weekly(
        _store(_schedule(), last=datetime.now(timezone.utc)),
        "postgresql://unused",
        str(tmp_path),
        queue=queue,
    )
    assert done is False
    assert queue.enqueued == []
    assert not (tmp_path / "backups").exists()


def test_maybe_backup_weekly_dump_failure_silent(tmp_path, monkeypatch):
    """Сбой дампа — False, тихо (worker не падает), писем нет."""

    def _boom(database_url, target, moment=None):
        raise RuntimeError("dump failed")

    monkeypatch.setattr(archive, "dump_database", _boom)
    queue = RecordingQueue()
    done = maybe_backup_weekly(
        _store(
            _schedule(daily_time="00:00"),
            last=datetime.now(timezone.utc) - timedelta(days=1),
        ),
        "postgresql://unused",
        str(tmp_path),
        queue=queue,
    )
    assert done is False
    assert queue.enqueued == []


# --- Ручной запуск: эндпоинт /archive/backup (админ) ---


@pytest.fixture
def archive_endpoint(tmp_path, fake_dump):
    """Боевой эндпоинт с офлайн-границами: настройки с расписанием, очередь —
    записывающая, каталог файлов — tmp."""
    from app.config import Settings, get_settings
    from app.main import app
    from app.mailer import get_mail_queue
    from app.settings_routes import get_settings_store

    store = InMemorySettingsStore(
        {
            "archive_schedule": json.dumps(_schedule(), ensure_ascii=False),
            "smtp_from": json.dumps("sed@example.com"),
        }
    )
    queue = RecordingQueue()
    app.dependency_overrides[get_settings_store] = lambda: store
    app.dependency_overrides[get_mail_queue] = lambda: queue
    app.dependency_overrides[get_settings] = lambda: Settings(
        ADMIN_GROUPS="SED_ADMINS",
        HR_GROUPS="SED_HR",
        FILES_DIR=str(tmp_path),
        DATABASE_URL="postgresql://unused",
    )
    yield queue
    for factory in (get_settings_store, get_mail_queue, get_settings):
        app.dependency_overrides.pop(factory, None)


def test_manual_backup_endpoint_enqueues_letter(client, admin_headers, archive_endpoint):
    """POST /archive/backup: бэкап создан, письмо по archive_schedule в очереди."""
    response = client.post("/archive/backup", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert len(archive_endpoint.enqueued) == 2
    message = archive_endpoint.enqueued[0]
    assert message.event == "reglament"
    assert message.subject == "sed@example.com Архивация OSWDocs"
    assert body["path"].endswith(".sql")


def test_manual_backup_endpoint_forbidden_for_hr(client, hr_headers, archive_endpoint):
    """Не админ — 403, писем нет (доступ к архивации только admin)."""
    response = client.post("/archive/backup", headers=hr_headers)
    assert response.status_code == 403
    assert archive_endpoint.enqueued == []
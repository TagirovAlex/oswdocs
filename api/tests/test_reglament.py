# Тесты уведомлений о выполненных регламентах (notify_schedule в onec_sync):
# письма ставятся в очередь каждому адресату расписания (event=reglament),
# {{summary}} подставляется в тело, notify=false/нет расписания — 0 писем.
# Сеть/БД не нужны: хранилище настроек — in-memory мок, очередь — записывающий.

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.onec_sync import notify_schedule  # noqa: E402


class InMemorySettingsStore:
    """Мок хранилища настроек (сид-формат значений), как в test_onec_sync."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key):
        return self._data.get(key)


class RecordingQueue:
    """Мок очереди писем: запоминает enqueue-вызовы."""

    def __init__(self):
        self.enqueued = []

    def enqueue(self, message):
        self.enqueued.append(message)


def _store(schedule):
    data = {}
    if schedule is not None:
        data["schedule_enterprises_sync"] = json.dumps(schedule, ensure_ascii=False)
    return InMemorySettingsStore(data)


def _schedule(**overrides):
    values = {
        "mode": "interval",
        "interval_hours": 3,
        "notify": True,
        "subject": "Регламент выполнен",
        "body": "Сводка: {{summary}}",
        "recipients": ["a@example.com", "b@example.com"],
    }
    values.update(overrides)
    return values


def test_notify_schedule_sends_to_each_recipient():
    """Каждому адресату — письмо event=reglament; тема с префиксом smtp_from."""
    queue = RecordingQueue()
    count = notify_schedule(
        _store(_schedule()),
        queue,
        "schedule_enterprises_sync",
        "sed@example.com",
        "предприятий: 3",
    )
    assert count == 2
    assert [m.to for m in queue.enqueued] == ["a@example.com", "b@example.com"]
    assert all(m.event == "reglament" for m in queue.enqueued)
    assert queue.enqueued[0].subject == "sed@example.com Регламент выполнен"
    assert "предприятий: 3" in queue.enqueued[0].html


def test_notify_schedule_summary_substituted():
    """{{summary}} в теле заменяется сводкой для каждого адресата."""
    queue = RecordingQueue()
    notify_schedule(
        _store(_schedule(recipients=["a@example.com"])),
        queue,
        "schedule_enterprises_sync",
        "",
        "связок создано: 5; просмотрено: 100",
    )
    assert len(queue.enqueued) == 1
    assert queue.enqueued[0].html == "Сводка: связок создано: 5; просмотрено: 100"


def test_notify_schedule_notify_false_returns_zero():
    """notify=false — писем нет (0), enqueue не вызывается."""
    queue = RecordingQueue()
    count = notify_schedule(
        _store(_schedule(notify=False)),
        queue,
        "schedule_enterprises_sync",
        "sed@example.com",
        "предприятий: 1",
    )
    assert count == 0
    assert queue.enqueued == []


def test_notify_schedule_no_schedule_returns_zero():
    """Расписания нет в settings — 0 писем."""
    queue = RecordingQueue()
    assert notify_schedule(_store(None), queue, "schedule_enterprises_sync", "", "x") == 0


def test_notify_schedule_empty_recipients_skipped():
    """Пустые строки в recipients пропускаются."""
    queue = RecordingQueue()
    count = notify_schedule(
        _store(_schedule(recipients=["a@example.com", "", "  "])),
        queue,
        "schedule_enterprises_sync",
        "",
        "x",
    )
    assert count == 1
    assert [m.to for m in queue.enqueued] == ["a@example.com"]
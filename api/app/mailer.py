# Очередь писем и шаблоны уведомлений (волна B3, offline на stdlib).
# Отправка — только через интерфейс Mailer (на стенде — SMTP Exchange,
# здесь — MockMailer). Очередь файловая (JSON), поэтому переживает
# рестарт worker-мока: новый экземпляр на том же файле видит те же письма.
# События v1: назначена/напоминание/эскалация/закрыта/возврат (имена событий —
# константы; тексты шаблонов — только из mail_templates/settings, не из кода).
# ПДн в письма не включаются (см. sanitize_context в docs.py).
# Зависимости стенда (python-docx-template/qrcode/LibreOffice) — раскомментирует стенд, offline только stdlib.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Protocol

from .docs import sanitize_context

# События уведомлений v1 (имена; тексты — в mail_templates из настроек).
EVENT_ASSIGNED = "assigned"  # назначена
EVENT_REMINDER = "reminder"  # напоминание
EVENT_ESCALATION = "escalation"  # эскалация
EVENT_CLOSED = "closed"  # закрыта
EVENT_RETURNED = "returned"  # возврат

KNOWN_EVENTS = frozenset({EVENT_ASSIGNED, EVENT_REMINDER, EVENT_ESCALATION, EVENT_CLOSED, EVENT_RETURNED})


def render_mail(template_html: str, context: Dict[str, object]) -> str:
    """Подставить {{var}} в HTML-шаблон письма (мини-Jinja, ПДн вычищены)."""
    from .docs import render_text  # локальный импорт: единый движок подстановки

    return render_text(template_html, context)


class Mailer(Protocol):
    """Граница отправки писем (на стенде — SMTP Exchange на mail из AD)."""

    def send(self, to: str, subject: str, html: str) -> None:
        """Отправить одно письмо; при сбое — бросить исключение (ретрай выше)."""
        ...  # pragma: no cover


def resolve_smtp_from(db_value: str | None, env_default: str) -> str:
    """Отправитель письма: значение из settings БД (smtp_from), иначе env SMTP_FROM."""
    return (db_value or "").strip() or env_default


@dataclass
class MailMessage:
    """Одно письмо в очереди (сериализуется в JSON целиком)."""

    id: str  # идентификатор письма (выдает вызывающий код/worker)
    to: str  # адрес из AD (mail), тексты — без ПДн
    subject: str
    html: str
    request_id: str = ""  # заявка-повод (для связки с QR/документом)
    event: str = EVENT_ASSIGNED  # событие v1
    attempts: int = 0  # число неудачных попыток
    max_attempts: int = 3  # лимит ретраев (из настроек на стенде)


class MockMailer:
    """Мок отправки: пишет в sent, умеет падать первые N вызовов (для ретраев)."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.sent: List[Dict[str, str]] = []

    def send(self, to: str, subject: str, html: str) -> None:
        """Отправить или упасть (первые fail_times вызовов — исключение)."""
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("мок SMTP недоступен (попытка %d)" % self.calls)
        self.sent.append({"to": to, "subject": subject, "html": html})


def build_mail(
    message_id: str,
    to: str,
    request_id: str,
    event: str,
    templates: Dict[str, str],
    context: Dict[str, object],
    subject_prefix: str = "",
) -> MailMessage:
    """Собрать письмо из mail_templates (текст шаблона — только из настроек).

    templates: словарь событие -> HTML-шаблон Jinja; subject_prefix — из настроек.
    """
    if event not in KNOWN_EVENTS:
        raise ValueError("неизвестное событие письма: %r" % event)
    try:
        template_html = templates[event]
    except KeyError:
        raise ValueError("нет шаблона письма для события %r" % event) from None
    html = render_mail(template_html, dict(context, request_id=request_id, event=event))
    subject = (subject_prefix + " " + event + " " + request_id).strip()
    return MailMessage(id=message_id, to=to, subject=subject, html=html, request_id=request_id, event=event)


class FileMailQueue:
    """Файловая очередь писем (JSON): переживает рестарт worker-мока.

    Формат файла: JSON-список писем со статусом pending/sent/dead.
    Каждый чих — перезапись файла целиком (объемы MVP малые).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._items: List[Dict[str, object]] = []
        self._load()

    def _load(self) -> None:
        """Прочитать файл очереди; нет файла — пустая очередь."""
        if not self._path.exists():
            self._items = []
            return
        raw = self._path.read_text(encoding="utf-8")
        if not raw.strip():
            self._items = []
            return
        data = json.loads(raw)
        self._items = data if isinstance(data, list) else []

    def _save(self) -> None:
        """Сохранить очередь на диск (атомарно через временный файл)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def enqueue(self, message: MailMessage) -> None:
        """Положить письмо в очередь (статус pending)."""
        item = asdict(message)
        item["status"] = "pending"
        self._items.append(item)
        self._save()

    def pending(self) -> List[MailMessage]:
        """Снимок ожидающих писем (только чтение)."""
        result: List[MailMessage] = []
        for item in self._items:
            if item.get("status") != "pending":
                continue
            data = {k: v for k, v in item.items() if k != "status"}
            result.append(MailMessage(**data))  # типы совпадают с asdict
        return result

    def pending_count(self) -> int:
        """Число ожидающих писем."""
        return sum(1 for item in self._items if item.get("status") == "pending")

    def mark_sent(self, message_id: str) -> None:
        """Пометить письмо отправленным (в dead не попадает, из pending уходит)."""
        for item in self._items:
            if item.get("id") == message_id:
                item["status"] = "sent"
        self._save()

    def mark_failed(self, message_id: str) -> None:
        """Неудача: attempts+1; при исчерпании лимита — статус dead."""
        for item in self._items:
            if item.get("id") == message_id:
                attempts = int(item.get("attempts", 0)) + 1
                item["attempts"] = attempts
                max_attempts = int(item.get("max_attempts", 3))
                if attempts >= max_attempts:
                    item["status"] = "dead"
        self._save()

    def dead_count(self) -> int:
        """Число писем, исчерпавших ретраи."""
        return sum(1 for item in self._items if item.get("status") == "dead")


@dataclass
class WorkerStats:
    """Итог одного прохода worker-мока."""

    sent: int = 0
    failed: int = 0
    dead: int = 0


class MockWorker:
    """Мок worker-а: разбирает очередь через Mailer с ретраями (по одному проходу)."""

    def run_once(self, queue: FileMailQueue, mailer: Mailer) -> WorkerStats:
        """Разослать все pending; сбой — mark_failed (ретрай следующим проходом)."""
        stats = WorkerStats()
        before_dead = queue.dead_count()
        for message in queue.pending():
            try:
                mailer.send(message.to, message.subject, message.html)
            except Exception:
                queue.mark_failed(message.id)
                stats.failed += 1
            else:
                queue.mark_sent(message.id)
                stats.sent += 1
        stats.dead = queue.dead_count() - before_dead
        return stats

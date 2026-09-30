# Очередь писем и шаблоны уведомлений (волна B3, offline на stdlib; W3a — боевые SMTP/БД).
# Отправка — только через интерфейс Mailer: на стенде SmtpMailer (smtplib,
# STARTTLS, параметры SMTP_* из env), в офлайн-тестах — MockMailer.
# Очередь: DbMailQueue (Postgres, таблица mail_queue из 0001) на стенде,
# FileMailQueue (JSON) — offline (переживает рестарт worker-мока).
# События v1: назначена/напоминание/эскалация/закрыта/возврат (имена событий —
# константы; тексты шаблонов — только из mail_templates/settings, не из кода).
# Получатель — mail из AD владельца шага/автора заявки (AdReader, только чтение);
# нет ридера/почты — письмо тихо пропускается (офлайн).
# ПДн в письма не включаются (см. sanitize_context в docs.py).

from __future__ import annotations

import json
import smtplib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Protocol

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

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


def resolve_smtp_host(db_value: object, env_default: str) -> str:
    """Хост SMTP-релея: значение из settings (smtp_host), иначе env SMTP_HOST."""
    return str(db_value or "").strip() or env_default


def resolve_smtp_port(db_value: object, env_default: int) -> int:
    """Порт SMTP: из settings (smtp_port), иначе env SMTP_PORT; невалидное — дефолт."""
    try:
        return int(db_value)
    except (TypeError, ValueError):
        return env_default


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
    templates: Dict[str, object],
    context: Dict[str, object],
    subject_prefix: str = "",
) -> MailMessage:
    """Собрать письмо из mail_templates (текст шаблона — только из настроек).

    templates: словарь событие -> HTML-шаблон Jinja (строка) либо
    событие -> {"subject": ..., "body_html": ...} (W3a: тема тоже из шаблона);
    subject_prefix (smtp_from) — из настроек.
    """
    if event not in KNOWN_EVENTS:
        raise ValueError("неизвестное событие письма: %r" % event)
    try:
        template = templates[event]
    except KeyError:
        raise ValueError("нет шаблона письма для события %r" % event)
    ctx = dict(context, request_id=request_id, event=event)
    if isinstance(template, str):
        template_html = template
        subject = (subject_prefix + " " + event + " " + request_id).strip()
    else:
        template_html = template.get("body_html", "") or ""
        subject = render_mail(template.get("subject", "") or "", ctx)
        if subject_prefix:
            subject = (subject_prefix + " " + subject).strip()
    html = render_mail(template_html, ctx)
    return MailMessage(
        id=message_id,
        to=to,
        subject=subject,
        html=html,
        request_id=request_id,
        event=event,
    )


def mail_templates_map(items: object) -> Dict[str, Dict[str, str]]:
    """mail_templates из settings -> {код события: {"subject": ..., "body_html": ...}}."""
    out: Dict[str, Dict[str, str]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not code:
            continue
        out[str(code)] = {
            "subject": str(item.get("subject", "") or ""),
            "body_html": str(item.get("body_html", "") or ""),
        }
    return out


def new_message_id() -> str:
    """Уникальный id письма (для очереди)."""
    return "mail-" + uuid.uuid4().hex


def recipient_mail(ad_reader: object | None, sam: str | None) -> str | None:
    """Почта из AD (только чтение); нет ридера/пользователя/почты — None."""
    if ad_reader is None or not sam:
        return None
    try:
        user = ad_reader.get_user(sam)
    except Exception:
        return None
    return getattr(user, "mail", "") or None


def manager_mail(ad_reader: object | None, sam: str | None) -> str | None:
    """Почта руководителя по цепочке manager (AD, только чтение) либо None."""
    if ad_reader is None or not sam:
        return None
    try:
        user = ad_reader.get_user(sam)
        manager_dn = getattr(user, "manager_dn", "") or ""
        if not manager_dn:
            return None
        manager = ad_reader.get_user_by_dn(manager_dn)
    except Exception:
        return None
    return getattr(manager, "mail", "") or None


def step_owner_mail(step: object, ad_reader: object | None) -> str | None:
    """Почта владельца шага: персональный assignee — по sam; группа — через
    групповой резолвер AD (get_user_mail_for_group на стенде), иначе None."""
    if ad_reader is None:
        return None
    if getattr(step, "assignee", None):
        return recipient_mail(ad_reader, step.assignee)
    group = getattr(step, "owner_group", None)
    resolver = getattr(ad_reader, "get_user_mail_for_group", None)
    if group and callable(resolver):
        try:
            return resolver(group) or None
        except Exception:
            return None
    return None


def enqueue_event(
    queue: "MailQueue",
    to: str | None,
    request_id: str,
    event: str,
    templates: object,
    context: Dict[str, object],
    subject_prefix: str = "",
    message_id: str | None = None,
) -> MailMessage | None:
    """Собрать письмо по mail_templates и положить в очередь.

    Нет получателя или нет шаблона события — None (письмо тихо пропускается:
    офлайн без AD/настройки). Возвращает MailMessage либо None.
    """
    if not to:
        return None
    tmpl = mail_templates_map(templates)
    if event not in tmpl:
        return None
    mid = message_id or new_message_id()
    try:
        message = build_mail(
            mid, to, request_id, event, tmpl, context, subject_prefix=subject_prefix
        )
    except ValueError:
        return None
    queue.enqueue(message)
    return message


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

    def has(self, request_id: str, event: str) -> bool:
        """Есть ли письмо-событие по заявке (дедупликация напоминаний)."""
        return any(
            item.get("request_id") == request_id and item.get("event") == event
            for item in self._items
        )


@dataclass
class WorkerStats:
    """Итог одного прохода worker-мока."""

    sent: int = 0
    failed: int = 0
    dead: int = 0


class MockWorker:
    """Мок worker-а: разбирает очередь через Mailer с ретраями (по одному проходу)."""

    def run_once(self, queue: MailQueue, mailer: Mailer) -> WorkerStats:
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


# ---------------------------------------------------------------------------
# W3a: интерфейс очереди + боевые реализации (SMTP, очередь в Postgres).
# ---------------------------------------------------------------------------


class MailQueue(Protocol):
    """Интерфейс очереди писем: единый для FileMailQueue и DbMailQueue."""

    def enqueue(self, message: MailMessage) -> None:
        """Положить письмо в очередь (статус queued/pending)."""
        ...

    def pending(self) -> List[MailMessage]:
        """Снимок ожидающих писем (только чтение)."""
        ...

    def pending_count(self) -> int:
        """Число ожидающих писем."""
        ...

    def mark_sent(self, message_id: str) -> None:
        """Пометить письмо отправленным."""
        ...

    def mark_failed(self, message_id: str) -> None:
        """Неудача: attempts+1; при исчерпании лимита — статус dead/failed."""
        ...

    def dead_count(self) -> int:
        """Число писем, исчерпавших ретраи."""
        ...

    def has(self, request_id: str, event: str) -> bool:
        """Есть ли письмо-событие по заявке (дедупликация напоминаний)."""
        ...


class SmtpMailer:
    """Реальная отправка через smtplib (STARTTLS); параметры — из env.

    From: значение settings smtp_from важнее env SMTP_FROM (resolve_smtp_from).
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        timeout: float = 15,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from = from_addr
        self._timeout = timeout

    def send(self, to: str, subject: str, html: str) -> None:
        """Отправить одно письмо (HTML); при сбое — исключение (ретрай выше)."""
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content("СЭД: просмотрите письмо в HTML-клиенте")
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(msg)


class MailQueueUnavailable(Exception):
    """Очередь писем (БД) недоступна — worker/эндпоинт отвечает 503, а не 500."""


class DbMailQueue:
    """Очередь писем в Postgres (таблица mail_queue из 0001).

    В payload JSONB хранится полный MailMessage (message_id/request_id/event/
    subject/html/attempts/max_attempts) — очередь единообразна с FileMailQueue.
    Статусы БД: queued (ожидает) / sent (отправлено) / failed (ретраи исчерпаны).
    """

    _INSERT_SQL = text(
        """
        INSERT INTO mail_queue (template_code, to_mail, payload)
        VALUES (:event, :to_mail, CAST(:payload AS jsonb))
        """
    )
    _PENDING_SQL = text(
        """
        SELECT id, to_mail, CAST(payload AS text) AS payload
        FROM mail_queue
        WHERE status = 'queued'
        ORDER BY id
        """
    )
    _SENT_SQL = text(
        "UPDATE mail_queue SET status = 'sent', sent_at = now() "
        "WHERE payload->>'message_id' = :message_id AND status = 'queued'"
    )
    _FAILED_SQL = text(
        """
        UPDATE mail_queue
        SET attempts = attempts + 1,
            last_error = :error,
            status = CASE
                WHEN attempts + 1 >= CAST(payload->>'max_attempts' AS integer)
                THEN 'failed' ELSE 'queued' END
        WHERE payload->>'message_id' = :message_id AND status = 'queued'
        """
    )
    _COUNT_SQL = text(
        "SELECT COUNT(*) FROM mail_queue WHERE status = :status"
    )
    _HAS_SQL = text(
        """
        SELECT 1 FROM mail_queue
        WHERE template_code = :event AND payload->>'request_id' = :request_id
        LIMIT 1
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    @staticmethod
    def _payload(message: MailMessage) -> str:
        return json.dumps(
            {
                "message_id": message.id,
                "request_id": message.request_id,
                "event": message.event,
                "subject": message.subject,
                "html": message.html,
                "attempts": message.attempts,
                "max_attempts": message.max_attempts,
            },
            ensure_ascii=False,
        )

    def enqueue(self, message: MailMessage) -> None:
        try:
            with self._session_factory() as session:
                session.execute(
                    self._INSERT_SQL,
                    {
                        "event": message.event,
                        "to_mail": message.to,
                        "payload": self._payload(message),
                    },
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise MailQueueUnavailable(f"Очередь писем недоступна: {exc}") from exc

    def pending(self) -> List[MailMessage]:
        try:
            with self._session_factory() as session:
                rows = session.execute(self._PENDING_SQL).all()
        except SQLAlchemyError as exc:
            raise MailQueueUnavailable(f"Очередь писем недоступна: {exc}") from exc
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row) -> MailMessage:
        payload = json.loads(row.payload or "{}")
        return MailMessage(
            id=payload.get("message_id", ""),
            to=row.to_mail,
            subject=payload.get("subject", ""),
            html=payload.get("html", ""),
            request_id=payload.get("request_id", ""),
            event=payload.get("event", EVENT_ASSIGNED),
            attempts=int(payload.get("attempts", 0) or 0),
            max_attempts=int(payload.get("max_attempts", 3) or 3),
        )

    def pending_count(self) -> int:
        return self._count("queued")

    def dead_count(self) -> int:
        return self._count("failed")

    def _count(self, status: str) -> int:
        try:
            with self._session_factory() as session:
                value = session.execute(self._COUNT_SQL, {"status": status}).scalar()
        except SQLAlchemyError as exc:
            raise MailQueueUnavailable(f"Очередь писем недоступна: {exc}") from exc
        return int(value or 0)

    def mark_sent(self, message_id: str) -> None:
        try:
            with self._session_factory() as session:
                session.execute(self._SENT_SQL, {"message_id": message_id})
                session.commit()
        except SQLAlchemyError as exc:
            raise MailQueueUnavailable(f"Очередь писем недоступна: {exc}") from exc

    def mark_failed(self, message_id: str) -> None:
        try:
            with self._session_factory() as session:
                session.execute(
                    self._FAILED_SQL, {"message_id": message_id, "error": "SMTP сбой"}
                )
                session.commit()
        except SQLAlchemyError as exc:
            raise MailQueueUnavailable(f"Очередь писем недоступна: {exc}") from exc

    def has(self, request_id: str, event: str) -> bool:
        try:
            with self._session_factory() as session:
                row = session.execute(
                    self._HAS_SQL,
                    {"request_id": request_id, "event": event},
                ).first()
        except SQLAlchemyError as exc:
            raise MailQueueUnavailable(f"Очередь писем недоступна: {exc}") from exc
        return row is not None


_db_mail_queue: DbMailQueue | None = None


def get_mail_queue() -> MailQueue:
    """Боевая очередь писем (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется FileMailQueue через dependency_overrides.
    """
    global _db_mail_queue
    if _db_mail_queue is None:
        from .config import get_settings

        _db_mail_queue = DbMailQueue(get_settings().DATABASE_URL)
    return _db_mail_queue


def get_mailer(store: "DbSettingsStore | None" = None) -> Mailer:
    """Боевой отправитель (SmtpMailer): хост/порт/отправитель — из settings
    (smtp_host/smtp_port/smtp_from, при отсутствии ключа — env SMTP_*);
    секреты SMTP_USER/SMTP_PASSWORD — только env (AGENTS.md п.3)."""
    from .config import get_settings
    from .settings_routes import DbSettingsStore, read_setting_value

    current = get_settings()
    if store is None:
        store = DbSettingsStore(current.DATABASE_URL)
    return SmtpMailer(
        host=resolve_smtp_host(
            read_setting_value(store, "smtp_host"), current.SMTP_HOST
        ),
        port=resolve_smtp_port(
            read_setting_value(store, "smtp_port"), current.SMTP_PORT
        ),
        username=current.SMTP_USER,
        password=current.SMTP_PASSWORD,
        from_addr=resolve_smtp_from(
            read_setting_value(store, "smtp_from"), current.SMTP_FROM
        ),
    )

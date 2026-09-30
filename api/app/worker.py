# Worker СЭД (W3a): однократный проход регламентных задач по заявкам.
# Запуск: `python -m app.worker` (команда compose у worker; управляет
# systemd-таймер/cron — проходы независимы, состояние — в БД/очереди).
# Задачи: просроченные шаги (expires_at < now) -> шаг «просрочен», заявка
# «На доработке», письмо «возврат» автору; напоминания за TTL/2 до дедлайна;
# эскалация по position_escalation (письмо руководителю). Письма собираются
# из mail_templates (settings) и ставятся в очередь (DbMailQueue на стенде),
# затем разбираются отправителем (SmtpMailer). Только чтение/обновление заявок
# через RequestsStore: никакой записи в 1С/AD.
# Хардкода шаблонов/TTL нет: значения — из settings (mail_templates,
# position_escalation, approval_ttl_days) и env (SMTP_*, APP_BASE_URL).

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .docs import request_url
from .mailer import (
    EVENT_ESCALATION,
    EVENT_REMINDER,
    EVENT_RETURNED,
    MailQueue,
    Mailer,
    MockWorker,
    enqueue_event,
    manager_mail,
    recipient_mail,
    step_owner_mail,
)
from .requests import IN_APPROVAL, REWORK, STEP_EXPIRED, STEP_PENDING


@dataclass
class WorkerResult:
    """Итог одного прохода worker-а."""

    expired: int = 0  # просроченных шагов -> «На доработке» + письмо «возврат»
    reminders: int = 0  # поставлено напоминаний
    escalations: int = 0  # поставлено эскалаций
    sent: int = 0  # отправлено писем из очереди
    failed: int = 0  # неудачных попыток отправки
    dead: int = 0  # писем, исчерпавших ретраи


def _reminder_before(approval_ttl_days: int) -> timedelta:
    """Окно напоминания: TTL/2 (напоминание за половину срока до дедлайна)."""
    return timedelta(hours=max(1, int(approval_ttl_days or 3) * 12))


def _escalation_recipient(ad_reader: object | None, request: object) -> str | None:
    """Почта руководителя для эскалации: менеджер владельца текущего шага
    (если assignee есть), иначе менеджер автора заявки (AD, только чтение)."""
    current = next(
        (s for s in request.steps if s.status == STEP_PENDING), None
    )
    if current is not None and getattr(current, "assignee", None):
        mail = manager_mail(ad_reader, current.assignee)
        if mail:
            return mail
    return manager_mail(ad_reader, request.created_by)


def _mail_context(request: object, base_url: str) -> dict:
    """Контекст письма (без ПДн; ссылка на заявку — из APP_BASE_URL)."""
    return {
        "fio": request.fio,
        "request_id": request.id,
        "url": request_url(base_url, request.id),
    }


def run_once(
    store,
    mail_queue: MailQueue,
    mailer: Mailer,
    mail_templates: object,
    position_escalation: dict,
    approval_ttl_days: int,
    ad_reader: object | None,
    base_url: str,
    smtp_from: str = "",
    now: datetime | None = None,
) -> WorkerResult:
    """Один проход: просрочки, напоминания, эскалация + разбор очереди писем.

    store — RequestsStore (на стенде DbRequestsStore, в тестах InMemory).
    now — инжектируемые часы для детерминированных тестов (UTC).
    """
    now = now or datetime.now(timezone.utc)
    result = WorkerResult()
    reminder_before = _reminder_before(approval_ttl_days)

    for request in store.list_all():
        if request.status != IN_APPROVAL:
            continue
        # Просрочки и напоминания — по ожидающим шагам.
        for step in request.steps:
            if step.status != STEP_PENDING:
                continue
            if step.expires_at < now:
                step.status = STEP_EXPIRED
                request.status = REWORK
                store.update(request)
                result.expired += 1
                to = recipient_mail(ad_reader, request.created_by)
                if to:
                    enqueue_event(
                        mail_queue,
                        to,
                        request.id,
                        EVENT_RETURNED,
                        mail_templates,
                        _mail_context(request, base_url),
                        subject_prefix=smtp_from,
                    )
                break  # заявка ушла на доработку: остальные шаги не трогаем
            if (
                now >= step.expires_at - reminder_before
                and not mail_queue.has(request.id, EVENT_REMINDER)
            ):
                to = step_owner_mail(step, ad_reader)
                if to and enqueue_event(
                    mail_queue,
                    to,
                    request.id,
                    EVENT_REMINDER,
                    mail_templates,
                    _mail_context(request, base_url),
                    subject_prefix=smtp_from,
                ):
                    result.reminders += 1
        if request.status != IN_APPROVAL:
            continue
        # Эскалация по должности увольняемого (position_escalation, часы).
        hours = request.escalation_hours or position_escalation.get(request.position)
        if not hours:
            continue
        current = next(
            (s for s in request.steps if s.status == STEP_PENDING), None
        )
        if (
            current is not None
            and current.expires_at > now
            and now >= current.expires_at - timedelta(hours=int(hours))
            and not mail_queue.has(request.id, EVENT_ESCALATION)
        ):
            to = _escalation_recipient(ad_reader, request)
            if to and enqueue_event(
                mail_queue,
                to,
                request.id,
                EVENT_ESCALATION,
                mail_templates,
                _mail_context(request, base_url),
                subject_prefix=smtp_from,
            ):
                result.escalations += 1

    # Разбор очереди: отправка с ретраями (по одному проходу).
    stats = MockWorker().run_once(mail_queue, mailer)
    result.sent = stats.sent
    result.failed = stats.failed
    result.dead = stats.dead
    return result


def _build_ad_reader() -> object | None:
    """Ридер AD для резолва mail получателей (только чтение); нет AD — None."""
    try:
        from .ad_reader import AdReader, AdReaderSettings, InMemoryCache, Ldap3Gateway

        ad_settings = AdReaderSettings.from_env()
        gateway = Ldap3Gateway(ad_settings)
        return AdReader(ad_settings, gateway=gateway, cache=InMemoryCache())
    except Exception:
        return None


def main() -> None:
    """Однократный проход на стенде: Postgres-хранилища, SMTP, AD (только чтение)."""
    from .config import get_settings
    from .mailer import (
        DbMailQueue,
        SmtpMailer,
        resolve_smtp_from,
        resolve_smtp_host,
        resolve_smtp_port,
        resolve_smtp_value,
    )
    from .requests_store import DbRequestsStore
    from .settings_routes import DbSettingsStore, SettingsUnavailable, read_setting_value

    settings = get_settings()
    try:
        settings_store = DbSettingsStore(settings.DATABASE_URL)
        smtp_from = resolve_smtp_from(
            read_setting_value(settings_store, "smtp_from"), settings.SMTP_FROM
        )
        smtp_host = resolve_smtp_host(
            read_setting_value(settings_store, "smtp_host"), settings.SMTP_HOST
        )
        smtp_port = resolve_smtp_port(
            read_setting_value(settings_store, "smtp_port"), settings.SMTP_PORT
        )
        smtp_user = resolve_smtp_value(
            read_setting_value(settings_store, "smtp_user"), settings.SMTP_USER
        )
        smtp_password = resolve_smtp_value(
            read_setting_value(settings_store, "smtp_password"), settings.SMTP_PASSWORD
        )
        mail_templates = read_setting_value(settings_store, "mail_templates") or []
        position_escalation = (
            read_setting_value(settings_store, "position_escalation") or {}
        )
        approval_ttl_days = int(
            read_setting_value(settings_store, "approval_ttl_days") or 3
        )
    except SettingsUnavailable as exc:
        raise SystemExit(f"Настройки недоступны (worker остановлен): {exc}") from exc

    # Еженедельная синхронизация справочника предприятий из 1С (Фаза 4):
    # тихо (сбой не валит проход), печать только при реальной синхронизации.
    from .onec_sync import maybe_sync_weekly

    try:
        if maybe_sync_weekly(settings_store):
            enterprises = read_setting_value(settings_store, "enterprises") or []
            print("sync: предприятий=%d" % len(enterprises))
    except Exception:
        pass

    store = DbRequestsStore(settings.DATABASE_URL)
    mail_queue = DbMailQueue(settings.DATABASE_URL)
    mailer = SmtpMailer(
        host=smtp_host,
        port=smtp_port,
        username=smtp_user,
        password=smtp_password,
        from_addr=smtp_from,
    )
    result = run_once(
        store,
        mail_queue,
        mailer,
        mail_templates,
        position_escalation,
        approval_ttl_days,
        _build_ad_reader(),
        settings.APP_BASE_URL,
        smtp_from=smtp_from,
    )
    print(
        "worker: просрочено=%d напоминаний=%d эскалаций=%d "
        "отправлено=%d ошибок=%d dead=%d"
        % (
            result.expired,
            result.reminders,
            result.escalations,
            result.sent,
            result.failed,
            result.dead,
        )
    )


if __name__ == "__main__":
    # Контейнерный worker (compose, restart: unless-stopped) должен оставаться
    # Up: проходы — циклически с паузой (интервал из env WORKER_POLL_SECONDS,
    # дефолт 60), иначе контейнер завершится после первого прохода и Docker
    # будет перезапускать его (статус Restarting вместо Up).
    import os
    import time

    interval = int(os.environ.get("WORKER_POLL_SECONDS", "60"))
    while True:
        main()
        time.sleep(interval)
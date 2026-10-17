# Worker СЭД (W3a): однократный проход регламентных задач по заявкам.
# Запуск: `python -m app.worker` (команда compose у worker; управляет
# systemd-таймер/cron — проходы независимы, состояние — в БД/очереди).
# Задачи: просроченные шаги (expires_at < now) -> письмо «просрочка»
# ответственным за шаг и их руководителю, статус заявки НЕ меняется
# (решение человека 2026-10-17: просрочка не уводит заявку в «На доработке»,
# шаг остаётся «ожидает» и отметку по нему можно поставить); напоминания за
# TTL/2 до дедлайна; эскалация по position_escalation (письмо руководителю).
# Письма собираются из mail_templates (settings) и ставятся в очередь (DbMailQueue
# на стенде), затем разбираются отправителем (SmtpMailer). Только чтение/обновление
# заявок через RequestsStore: никакой записи в 1С/AD.
# Хардкода шаблонов/TTL нет: значения — из settings (mail_templates,
# position_escalation, approval_ttl_days) и env (SMTP_*, APP_BASE_URL).

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .audit import AuditEvent, audit_log
from .docs import request_url
from .mailer import (
    EVENT_ESCALATION,
    EVENT_OVERDUE,
    EVENT_REMINDER,
    MailQueue,
    Mailer,
    MockWorker,
    enqueue_event,
    mail_templates_map,
    manager_mail,
    step_owner_mails,
)
from .requests import (
    IN_APPROVAL,
    STEP_PENDING,
    _current_pending_steps,
)
from .timezone import current_timezone


@dataclass
class WorkerResult:
    """Итог одного прохода worker-а."""

    expired: int = 0  # просроченных шагов, поставлено писем «просрочка»
    reminders: int = 0  # поставлено напоминаний
    escalations: int = 0  # поставлено эскалаций
    sent: int = 0  # отправлено писем из очереди
    failed: int = 0  # неудачных попыток отправки
    dead: int = 0  # писем, исчерпавших ретраи


def _reminder_before(approval_ttl_days: int, ttl_hours: int | None = None) -> timedelta:
    """Окно напоминания: половина срока шага (напоминание за TTL/2 до дедлайна).

    ttl_hours — свой срок шага (миграция 0026, снимок в request_steps): у шага
    со своим коротким сроком общее окно в днях било бы мимо дедлайна (шаг с
    сроком 8 часов получил бы напоминание за 1,5 суток до срока, то есть сразу).
    Нет своего срока — окно от общего approval_ttl_days."""
    hours = int(ttl_hours or 0)
    if hours <= 0:
        hours = int(approval_ttl_days or 3) * 24
    return timedelta(hours=max(1, hours // 2))


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


def _overdue_recipients(
    ad_reader: object | None, request: object, step: object
) -> list[str]:
    """Адресаты письма о просрочке: ответственные за просроченный шаг и их
    руководитель (решение человека 2026-10-17).

    Ответственные — те же, что у «назначена» (кому по шагу ещё предстоит
    отметиться; у группового шага — состав группы). Руководитель — менеджер
    ПЕРВОГО ответственного шага (у шага с несколькими ответственными это
    снимок `assignees`): в параллельном блоке просроченным может быть не первый
    шаг, и руководитель чужого шага в письме о просрочке был бы ошибкой.
    Персонального владельца нет (групповой шаг) — менеджер автора заявки. Без
    дублей: один и тот же человек и ответственный, и руководитель письма
    получает одно. AD недоступен — пустой список, письмо не ставится."""
    mails = step_owner_mails(step, ad_reader)
    assignee = getattr(step, "assignee", None)
    manager = (
        manager_mail(ad_reader, assignee) if assignee
        else manager_mail(ad_reader, request.created_by)
    )
    if manager and manager not in mails:
        mails.append(manager)
    return mails


def _overdue_detail(step: object) -> str:
    """Метка письма о просрочке: номер шага и его срок.

    Срок в метке обязателен: после повтора шага (reissue) или возврата шаг
    получает новое `expires_at`, и просрочка этого нового круга — новое
    событие, о котором адресаты должны узнать. Без срока дедупликация молчала
    бы на втором круге (строка в `mail_queue` не удаляется никогда)."""
    expires_at = getattr(step, "expires_at", None)
    return "step:%d@%s" % (
        getattr(step, "order", 0),
        expires_at.isoformat() if expires_at is not None else "-",
    )


def _log_overdue_skip(request_id: str, step: object, reason: str) -> None:
    """След в аудите: письмо о просрочке поставить нечем (нет шаблона, нет
    адресатов или не собралось). Без ПДн.

    Ключ — метка письма (номер шага и срок) и причина, поэтому новый круг по
    шагу с новым сроком даст свою строку, а не проглотится старой.

    Пишется ОДИН раз на ключ: `audit_log` — append-only (триггер запрещает
    UPDATE/DELETE), а worker ходит раз в минуту, так что повтор на каждом
    проходе забил бы журнал тысячами строк на каждый просроченный шаг. Дедуп по
    памяти процесса (`audit_log.all()`): рестарт worker-а даст максимум одну
    повторную строку — это приемлемо, письма всё равно дедуплицируются по БД."""
    detail = "event=%s %s reason=%s" % (EVENT_OVERDUE, _overdue_detail(step), reason)
    if any(
        e.action == "notify.skip" and e.entity_id == request_id and e.detail == detail
        for e in audit_log.all()
    ):
        return
    audit_log.append(
        AuditEvent(
            actor="system",
            action="notify.skip",
            entity="request",
            entity_id=request_id,
            detail=detail,
        )
    )


def _stamp(moment: datetime) -> str:
    """Момент в зоне компании (settings.timezone) в формате карточки/бланка."""
    if moment.tzinfo is None:
        return moment.strftime("%d.%m.%Y %H:%M")
    return moment.astimezone(current_timezone()).strftime("%d.%m.%Y %H:%M")


def _mail_context(request: object, base_url: str) -> dict:
    """Контекст письма (без ПДн; ссылка на заявку — из APP_BASE_URL).

    comment пустой: у писем worker-а (напоминание/эскалация/просрочка) решения
    согласующего нет — {{ comment }} подставится пустой строкой."""
    return {
        "fio": request.fio,
        "request_id": request.id,
        "url": request_url(base_url, request.id),
        "comment": "",
    }


def _overdue_context(request: object, base_url: str, step: object, now: datetime) -> dict:
    """Контекст письма о просрочке: общий плюс срок этапа и величина задержки.

    {{ due_at }} — плановый срок шага, {{ overdue_hours }} — на сколько часов он
    уже истёк (округление вверх, чтобы минимальная задержка была не «0»).
    Часы, а не дни: срок шага бывает короче суток (ttl_hours, миграция 0026)."""
    context = _mail_context(request, base_url)
    expires_at = getattr(step, "expires_at", None)
    if expires_at is None:
        return context
    context["due_at"] = _stamp(expires_at)
    seconds = max(0.0, (now - expires_at).total_seconds())
    context["overdue_hours"] = int(seconds // 3600) + (1 if seconds % 3600 else 0)
    return context


def run_once(
    store,
    mail_queue: MailQueue,
    mailer: Mailer,
    mail_templates: object,
    position_escalation: dict,
    approval_ttl_days: int,
    ad_reader: object | None,
    base_url: str,
    now: datetime | None = None,
) -> WorkerResult:
    """Один проход: просрочки, напоминания, эскалация + разбор очереди писем.

    Просрочка уведомляет, но ничего не меняет: ни статус шага, ни статус
    заявки, ни сроки (решение человека 2026-10-17).

    store — RequestsStore (на стенде DbRequestsStore, в тестах InMemory).
    now — инжектируемые часы для детерминированных тестов (UTC).
    """
    now = now or datetime.now(timezone.utc)
    result = WorkerResult()

    for request in store.list_all():
        if request.status != IN_APPROVAL:
            continue
        # Просрочки и напоминания — по ожидающим шагам АКТИВНОГО блока
        # (в параллельном блоке — все его шаги, а не только первый).
        for step in _current_pending_steps(request):
            if step.expires_at < now:
                # Просрочка (решение человека 2026-10-17): статус заявки и шага
                # НЕ меняем — шаг ждёт отметки дальше, заявка остаётся «На
                # согласовании». Сообщаем адресатам шага и их руководителю.
                # Метка дедупликации — номер шага И его срок: пока срок тот же,
                # письмо одно (worker ходит раз в минуту), а после повтора шага
                # или возврата новый круг со своим сроком — новое письмо.
                detail = _overdue_detail(step)
                if EVENT_OVERDUE not in mail_templates_map(mail_templates):
                    # Шаблона нет — не ходим ни в очередь, ни в AD: след в аудите
                    # обязателен (иначе письмо о просрочке пропало бы тихо), но
                    # писать его будем не каждый проход.
                    _log_overdue_skip(request.id, step, "no_template")
                    continue
                if mail_queue.has(request.id, EVENT_OVERDUE, detail):
                    continue
                recipients = [to for to in _overdue_recipients(ad_reader, request, step) if to]
                if not recipients:
                    _log_overdue_skip(request.id, step, "no_recipients")
                    continue
                context = _overdue_context(request, base_url, step, now)
                notified = 0
                for to in recipients:
                    if enqueue_event(
                        mail_queue,
                        to,
                        request.id,
                        EVENT_OVERDUE,
                        mail_templates,
                        context,
                        detail=detail,
                    ):
                        notified += 1
                if notified:
                    result.expired += 1
                    audit_log.append(
                        AuditEvent(
                            actor="system",
                            action="step.overdue",
                            entity="request",
                            entity_id=request.id,
                            detail="order=%d" % step.order,
                        )
                    )
                else:
                    # Адресаты есть, но письмо не собралось (enqueue_event вернул
                    # None) — причина другая, в аудите пишем её честно.
                    _log_overdue_skip(request.id, step, "build_failed")
                continue
            if (
                now >= step.expires_at - _reminder_before(
                    approval_ttl_days, getattr(step, "ttl_hours", None)
                )
                and not mail_queue.has(request.id, EVENT_REMINDER)
            ):
                # Напоминание адресатам шага: персональному — один, групповому —
                # все активные участники (очередь хранит по письму на строку).
                for to in step_owner_mails(step, ad_reader):
                    if to and enqueue_event(
                        mail_queue,
                        to,
                        request.id,
                        EVENT_REMINDER,
                        mail_templates,
                        _mail_context(request, base_url),
                    ):
                        result.reminders += 1
        # Эскалация по должности увольняемого (position_escalation, часы).
        hours = request.escalation_hours or position_escalation.get(request.position)
        if not hours:
            continue
        current = next(
            (s for s in _current_pending_steps(request) if s.status == STEP_PENDING),
            None,
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
        resolve_smtp_from_name,
        resolve_smtp_host,
        resolve_smtp_port,
        resolve_smtp_value,
    )
    from .requests_store import DbRequestsStore
    from .settings_routes import DbSettingsStore, SettingsUnavailable, read_setting_value
    from .timezone import refresh_timezone_from_settings

    settings = get_settings()
    try:
        settings_store = DbSettingsStore(settings.DATABASE_URL)
        # Зона системы — до любой работы с датами (почта, расписания, сроки):
        # worker не ходит в /auth/me и без этого остался бы на TZ контейнера.
        refresh_timezone_from_settings(settings_store)
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

    # Очередь писем нужна и регламентным уведомлениям, и проходу заявок ниже —
    # создаём заранее (одна БД-очередь на проход).
    mail_queue = DbMailQueue(settings.DATABASE_URL)

    # Регламентная синхронизация справочника предприятий из 1С: тихо (сбой не
    # валит проход); при реальной синхронизации — уведомление по расписанию
    # schedule_enterprises_sync, если оно настроено.
    from .onec_sync import maybe_sync_weekly, notify_schedule

    try:
        if maybe_sync_weekly(settings_store):
            enterprises = read_setting_value(settings_store, "enterprises") or []
            print("sync: предприятий=%d" % len(enterprises))
            notify_schedule(
                settings_store,
                mail_queue,
                "schedule_enterprises_sync",
                "предприятий: %d" % len(enterprises),
            )
    except Exception:
        pass

    # Регламентная синхронизация локального справочника сотрудников из 1С: тихо
    # (сбой не валит проход), как синк предприятий; метка employees_synced_at
    # пишется внутри при успехе. Запись — только в нашу таблицу employees.
    try:
        from .employee_sync import maybe_sync_employees_weekly
        from .link_store import DbLinksStore

        if maybe_sync_employees_weekly(
            settings_store, DbLinksStore(settings.DATABASE_URL)
        ):
            print("sync: справочник сотрудников синхронизирован")
    except Exception:
        pass

    # Регламентный проход по регистру кадровых данных 1С (ежедневно по
    # schedule_hr_dismissals_sync): пишет даты увольнения в employees, по которым
    # поиск сотрудников отсекает уволенных. Запись — только в нашу таблицу.
    try:
        from .employee_sync import maybe_sync_hr_daily

        if maybe_sync_hr_daily(settings_store):
            print("sync: даты увольнения из регистра кадровых данных обновлены")
    except Exception:
        pass

    # Регламентная автосвязка 1С↔AD по точному ФИО: тихо, толерантность как у
    # maybe_sync_weekly; запись — только связки у нас; при реальном проходе —
    # уведомление по расписанию schedule_ad_links_sync.
    try:
        from .ad_sync import maybe_sync_links_weekly
        from .employees import get_ad_reader, get_onec_client
        from .link_store import DbLinksStore

        # Уволенных не сопоставляем (дата увольнения — из регистра 1С, локальный
        # справочник). Справочник недоступен — пустой набор, проход идёт по всем.
        try:
            from .employee_sync import get_employee_sync_store

            dismissed = set(get_employee_sync_store(settings).dismissed_keys())
        except Exception:
            dismissed = set()
        sync_result = maybe_sync_links_weekly(
            settings_store,
            get_onec_client(settings, settings_store),
            get_ad_reader(),
            DbLinksStore(settings.DATABASE_URL),
            dismissed=dismissed,
        )
        if sync_result:
            print("sync: автосвязка 1С-AD выполнена")
            notify_schedule(
                settings_store,
                mail_queue,
                "schedule_ad_links_sync",
                "связок создано: %d; просмотрено: %d"
                % (sync_result.created, sync_result.scanned),
            )
    except Exception:
        pass

    # Регламентный синк состава групп AD в локальный кэш: тихо, толерантность
    # как у автосвязки; запись — только наши таблицы ad_group_members/
    # ad_group_sync_state; при реальном проходе — уведомление по расписанию
    # schedule_ad_groups_sync.
    try:
        from .ad_groups_cache import (
            get_groups_cache_store,
            maybe_sync_ad_groups_weekly,
        )
        from .employees import get_ad_reader

        if maybe_sync_ad_groups_weekly(
            settings_store,
            get_groups_cache_store(settings),
            get_ad_reader(),
        ):
            print("sync: состав групп AD синхронизирован")
            notify_schedule(
                settings_store,
                mail_queue,
                "schedule_ad_groups_sync",
                "состав групп AD обновлён",
            )
    except Exception:
        pass

    # Регламентный бэкап БД: тихо (сбой не валит проход), как у прочих
    # регламентов; расписание/каталог/шаблон/копии — из settings архивации
    # (archive_schedule/archive_backup_dir/archive_name_template/archive_keep_copies).
    # После успеха — уведомление по archive_schedule (notify/recipients/subject/body).
    try:
        from .archive import maybe_backup_weekly

        if maybe_backup_weekly(
            settings_store,
            settings.DATABASE_URL,
            settings.FILES_DIR,
            queue=mail_queue,
        ):
            print("archive: регламентный бэкап выполнен")
    except Exception:
        pass

    store = DbRequestsStore(settings.DATABASE_URL)
    mailer = SmtpMailer(
        host=smtp_host,
        port=smtp_port,
        username=smtp_user,
        password=smtp_password,
        from_addr=smtp_from,
        from_name=resolve_smtp_from_name(
            read_setting_value(settings_store, "smtp_from_name")
        ),
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

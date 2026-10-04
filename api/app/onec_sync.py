# Синхронизация справочника предприятий из 1С (Фаза C1).
# Список предприятий берётся ИЗ КАЖДОЙ БАЗЫ 1С (сущность организаций базы,
# в базе может быть несколько предприятий — ЗУП 3.х, справочник «Организации»).
# Синхронизация: еженедельно (worker, maybe_sync_weekly) и принудительно
# (POST /settings/enterprises/sync, кнопка в админке). Только чтение из 1С
# (HTTP GET), запись — только в settings (enterprises + onec_enterprise_bases +
# onec_enterprises_synced_at).

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import httpx

from .onec_client import build_entity_url, parse_collection
from .settings_routes import read_setting_value


class OnecSyncUnavailable(Exception):
    """Базы 1С не настроены или недоступны (503, не 500)."""


def http_get(url: str, user: str, password: str, timeout: float = 15.0) -> str:
    """GET источника предприятий: basic auth при заданном user, тело как текст."""
    headers = {}
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _organization(base: dict, item: dict) -> tuple[str, str]:
    """Код/название организации из записи OData по схеме базы.

    У ЗУП-справочника «Организации» кода (Code) нет — по умолчанию код =
    Ref_Key (GUID), имя = Description; поля настраиваются в карточке базы
    (organization_code_field / organization_name_field, напр. ИНН)."""
    code_field = str(base.get("organization_code_field") or "Ref_Key")
    name_field = str(base.get("organization_name_field") or "Description")
    code = str(item.get(code_field) or "").strip()
    name = str(item.get(name_field) or "").strip()
    return code, name


def sync_enterprises(store) -> list[dict]:
    """Опрос всех баз 1С: собрать предприятия (union) и маппинг предприятие→базы.

    Базы — settings.onec_bases (в каждой — OData-URL и сущность организаций).
    Результат пишется в settings.enterprises, settings.onec_enterprise_bases
    и settings.onec_enterprises_synced_at. Падение одной базы не валит остальные;
    если не ответила НИ ОДНА — OnecSyncUnavailable.
    """
    bases = read_setting_value(store, "onec_bases")
    if not isinstance(bases, list) or not bases:
        raise OnecSyncUnavailable("Базы 1С не настроены")
    enterprises: dict[str, dict] = {}
    mapping: dict[str, list[str]] = {}
    errors: list[str] = []
    ok = False
    for base in bases:
        if not isinstance(base, dict) or not base.get("url") or not base.get("code"):
            continue
        base_code = str(base["code"])
        entity = str(base.get("organization_entity") or "Catalog_Организации")
        url = build_entity_url(str(base["url"]), entity)
        try:
            text = http_get(url, str(base.get("user") or ""), str(base.get("password") or ""))
            items = parse_collection(text)
        except Exception as exc:  # падение одной базы — не валит остальные
            errors.append("%s: %s" % (base_code, exc))
            continue
        ok = True
        for item in items:
            code, name = _organization(base, item)
            if not code:
                continue
            if code not in enterprises:
                enterprises[code] = {"code": code, "name": name}
            if base_code not in mapping.setdefault(code, []):
                mapping[code].append(base_code)
    if not ok:
        raise OnecSyncUnavailable(
            "Ни одна база 1С не ответила: " + "; ".join(errors or ["баз нет"])
        )
    store.set("enterprises", json.dumps(list(enterprises.values()), ensure_ascii=False))
    store.set("onec_enterprise_bases", json.dumps(mapping, ensure_ascii=False))
    store.set(
        "onec_enterprises_synced_at",
        json.dumps(datetime.now(timezone.utc).isoformat()),
    )
    return list(enterprises.values())


def _weekly_due(moment: datetime, last: datetime | None) -> bool:
    """«Пора» при отсутствии/невалидном расписании: раз в 7 дней от last."""
    if last is None:
        return True
    return moment >= last + timedelta(days=7)


def _schedule_tz() -> tzinfo:
    """Часовой пояс расписаний: TZ из env (compose: Europe/Moscow), иначе
    локальный системный, иначе UTC. Значение — только чтение, не настройка."""
    name = (os.environ.get("TZ") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass  # неизвестное имя TZ — системная локальная зона
    return datetime.now().astimezone().tzinfo or timezone.utc


def due_schedule(schedule, last_raw: str | None, now: datetime | None = None) -> bool:
    """«Пора» ли выполнять регламентную операцию по расписанию.

    schedule — dict из settings (mode: interval|daily; interval_hours/daily_time);
    last_raw — строка ISO последнего выполнения либо None. Расписания нет/не dict
    или невалидно (интервал <1 ч, битое daily_time, неизвестный режим) — прежнее
    поведение: раз в 7 дней от last_raw (не «пора всегда», иначе worker гонял бы
    операцию каждый проход). Битое last — «пора» (True).
    daily_time — время ЛОКАЛЬНОЕ (_schedule_tz, на стенде Europe/Moscow): «06:00»
    = 06:00 МСК, а не 06:00 UTC (иначе регламент уезжал на +3 часа).
    now — инжектируемые часы для детерминированных тестов (UTC), иначе текущие.
    """
    moment = now or datetime.now(timezone.utc)
    last = None
    if isinstance(last_raw, str) and last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError:
            pass  # битое значение — считаем, что выполнения не было
    if not isinstance(schedule, dict) or not schedule:
        return _weekly_due(moment, last)
    mode = schedule.get("mode")
    if mode == "interval":
        try:
            interval = timedelta(hours=float(schedule.get("interval_hours") or 0))
        except (TypeError, ValueError):
            interval = timedelta(0)
        if interval <= timedelta():  # 0/пусто/отрицательное — расписание не настроено
            return _weekly_due(moment, last)
        if last is None:
            return True
        return moment >= last + interval
    if mode == "daily":
        try:
            hours, minutes = str(schedule.get("daily_time") or "").split(":", 1)
            tz = _schedule_tz()
            day = moment.astimezone(tz)  # календарный день — локальный (МСК)
            when = datetime(
                day.year, day.month, day.day,
                int(hours), int(minutes), tzinfo=tz,
            ).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return _weekly_due(moment, last)  # битое время — расписание не настроено
        if moment < when:
            return False  # сегодняшнее время ещё не наступило
        return last is None or last < when
    return _weekly_due(moment, last)  # неизвестный режим — раз в 7 дней


def notify_schedule(store, queue, schedule_key: str, smtp_from: str, summary: str) -> int:
    """Разослать уведомление о выполненном регламенте (event=reglament).

    Расписание — settings.schedule_key: notify=false или адресатов нет — 0.
    Письмо каждому адресату: тема subject (с префиксом smtp_from, как у прочих
    писем), тело body с подстановкой {{summary}}. Тексты — из настроек, не из кода.
    """
    from .mailer import EVENT_REGLAMENT, MailMessage, new_message_id  # лениво: избегаем циклов импорта

    schedule = read_setting_value(store, schedule_key)
    if not isinstance(schedule, dict) or not schedule.get("notify"):
        return 0
    recipients = schedule.get("recipients")
    if not isinstance(recipients, list):
        return 0
    subject = str(schedule.get("subject") or "")
    if smtp_from:
        subject = (smtp_from + " " + subject).strip()
    body = str(schedule.get("body") or "").replace("{{summary}}", summary)
    count = 0
    for address in recipients:
        if not isinstance(address, str) or not address.strip():
            continue
        queue.enqueue(
            MailMessage(
                id=new_message_id(),
                to=address.strip(),
                subject=subject,
                html=body,
                event=EVENT_REGLAMENT,
            )
        )
        count += 1
    return count


def maybe_sync_weekly(store) -> bool:
    """Регламентная синхронизация предприятий (worker): тихо, без сбоев.

    Базы не настроены — False; «не пора» по расписанию schedule_enterprises_sync
    (нет расписания — раз в 7 дней от onec_enterprises_synced_at) — False;
    иначе sync_enterprises (сбой не валит worker — False), успех — True."""
    bases = read_setting_value(store, "onec_bases")
    if not isinstance(bases, list) or not bases:
        return False
    schedule = read_setting_value(store, "schedule_enterprises_sync")
    last_raw = read_setting_value(store, "onec_enterprises_synced_at")
    if not due_schedule(schedule, last_raw):
        return False
    try:
        sync_enterprises(store)
        return True
    except Exception:
        return False

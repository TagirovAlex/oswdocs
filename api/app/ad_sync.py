# Автосвязка 1С↔AD по точному ФИО (Задача 2.3, решение пользователя).
#
# Для каждого предприятия → каждой базы → все сотрудники (пагинация $skip/$top);
# для каждого сотрудника — AD-кандидаты по нормализованному ФИО (displayName,
# регистр/пробелы не различаются). Уникальное совпадение с ОБЕИХ сторон
# (нет дублей ФИО в 1С-предприятии, нет нескольких AD-записей с этим
# displayName) и связки ещё нет → создать LinkRecord(verified=True, …) через
# LinksStore; иначе — пропустить (статус вычислится на чтении, см. ad_status).
#
# Правила: 1С/AD — ТОЛЬКО чтение (OneCClient.list_employees, AdReader.search_users);
# запись — только факт связки в нашу БД (LinksStore.save). Падение одной базы
# 1С / сбой AD не валят синхронизацию: ошибки копятся в result.errors,
# толерантность — как у maybe_sync_weekly (onec_sync.py).

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional

from .ad_reader import AdReader, AdUnavailable
from .link_store import LinksStore
from .onec_client import OneCBaseDown, OneCCircuitOpen, OneCClient, OneCError

if TYPE_CHECKING:
    from .link import LinkRecord


class AdSyncUnavailable(Exception):
    """Автосвязка невозможна (нет ридера AD) — 503, не 500."""


# Размер страницы выгрузки сотрудников (лимит одной OData-выдачи).
LIST_PAGE = 500


@dataclass
class AdSyncResult:
    """Итог одного прохода автосвязки (диагностика и кнопка «по требованию»)."""

    scanned: int = 0  # обработано карточек 1С (предприятия → базы → сотрудники)
    created: int = 0  # создано verified-связок
    skipped_linked: int = 0  # связка уже есть
    skipped_1c_duplicates: int = 0  # дубль ФИО в предприятии 1С
    skipped_ad_no_match: int = 0  # точного ФИО в AD нет
    skipped_ad_duplicates: int = 0  # несколько AD-записей с этим ФИО
    errors: List[str] = field(default_factory=list)  # падения баз/AD (не валят проход)


def _norm(text: str) -> str:
    """Нормализация ФИО для сравнения (регистр/пробелы не различаются)."""
    return " ".join(text.strip().lower().split())


def _list_enterprise(
    client: OneCClient,
    reader: AdReader,
    store: LinksStore,
    enterprise: str,
    result: AdSyncResult,
    now: str,
) -> None:
    """Обойти все базы предприятия: собрать сотрудников и связать уникальные."""
    from .link import LinkRecord, link_key

    cards: List = []
    for base_code in client.bases_for_enterprise(enterprise):
        cards.extend(_list_all(client, base_code, enterprise, result))
    # Дубли ФИО в пределах предприятия — ручная сверка (автосклейки нет).
    counts: dict[str, int] = {}
    for card in cards:
        key = _norm(card.fio)
        counts[key] = counts.get(key, 0) + 1
    for card in cards:
        result.scanned += 1
        key = link_key(card.enterprise, card.base_code, card.tab_num)
        try:
            if store.find(key) is not None:
                result.skipped_linked += 1
                continue
        except Exception:  # падение БД связок — ошибка уровня, не пропуск
            raise
        wanted = _norm(card.fio)
        if not wanted or counts.get(wanted, 0) > 1:
            result.skipped_1c_duplicates += 1
            continue
        try:
            candidates = reader.search_users(card.fio)
        except AdUnavailable as exc:
            result.errors.append("%s: AD: %s" % (enterprise, exc))
            result.skipped_ad_no_match += 1
            continue
        exact = [u for u in candidates if _norm(u.display_name) == wanted]
        if not exact:
            result.skipped_ad_no_match += 1
            continue
        if len(exact) > 1:
            result.skipped_ad_duplicates += 1
            continue
        ad_user = exact[0]
        # Зеркала ссылок связки в НАШИХ таблицах (one_c_bases/employee_base_map/
        # users) — нужны для внешних ключей link_1c_ad. В AD/1С не пишем.
        try:
            base_cfg = client.base_config(card.base_code)
            base = {
                "code": base_cfg.code,
                "enterprise": card.enterprise,
                "name": base_cfg.code,
                "odata_url": base_cfg.url,
            }
        except Exception:
            base = None  # база неизвестна — зеркалим только сотрудника и пользователя
        store.ensure_targets(
            base,
            {
                "enterprise": card.enterprise,
                "base_code": card.base_code,
                "tab_num": card.tab_num,
                "fio": card.fio,
                "dept_1c": card.dept,
                "position_1c": card.position,
                "employment_type": card.employment_type,
                "hire_date": card.hire_date or None,
            },
            {
                "sam": ad_user.sam,
                "fio_full": ad_user.display_name or ad_user.sam,
                "dept_ad": ad_user.department,
                "title_ad": ad_user.title,
                "manager_dn": ad_user.manager_dn,
                "mail": ad_user.mail,
            },
        )
        store.save(
            LinkRecord(
                enterprise=card.enterprise,
                base_code=card.base_code,
                tab_num=card.tab_num,
                key=key,
                sam=ad_user.sam,
                by="ad_sync",  # сервисный автор (без пользовательской сессии)
                at=now,
                verified=True,
                diverged=False,
                needs_manual_review=False,
                truth_source="1c",
            )
        )
        result.created += 1


def _list_all(
    client: OneCClient,
    base_code: str,
    enterprise: str,
    result: AdSyncResult,
) -> List:
    """Все сотрудники базы по предприятию страницами (толерантно к падению базы)."""
    cards: List = []
    skip = 0
    while True:
        try:
            page = client.list_employees(
                base_code, enterprise, skip=skip, top=LIST_PAGE
            )
        except (OneCBaseDown, OneCCircuitOpen) as exc:
            result.errors.append("%s/%s: %s" % (enterprise, base_code, exc))
            return cards
        except OneCError as exc:
            result.errors.append("%s/%s: %s" % (enterprise, base_code, exc))
            return cards
        except Exception as exc:  # сеть/прочее — изолируем, опрос продолжаем
            result.errors.append("%s/%s: %s: %s" % (enterprise, base_code, type(exc).__name__, exc))
            return cards
        if not page:
            break
        cards.extend(page)
        if len(page) < LIST_PAGE:
            break
        skip += len(page)
    return cards


def run_ad_sync(
    client: OneCClient,
    reader: AdReader,
    store: LinksStore,
    enterprises: List[str],
    now: Optional[str] = None,
) -> AdSyncResult:
    """Один проход автосвязки: предприятия → базы → сотрудники → verified-связки.

    enterprises — коды предприятий из settings (связка предприятие→базы — из
    синхронизации, client.bases_for_enterprise). now — инжектируемые часы для
    детерминированных тестов. Только чтение 1С/AD + запись связок в нашу БД."""
    result = AdSyncResult()
    moment = now or datetime.now(timezone.utc).isoformat()
    for enterprise in enterprises:
        try:
            _list_enterprise(client, reader, store, enterprise, result, moment)
        except Exception as exc:  # падение одной ветки не валит остальные
            result.errors.append("%s: %s" % (enterprise, exc))
    return result


def compute_ad_status(card, link: Optional["LinkRecord"], reader: Optional[AdReader]) -> str:
    """Статус стыковки 1С↔AD для карточки/списка (без записи в БД).

    linked — связка есть; match — точное уникальное совпадение ФИО в AD
    (ждёт синхронизации/подтверждения); no_match — совпадения нет/дубли
    (в т.ч. AD недоступен) — «синхронизация не прошла»."""
    if link is not None:
        return "linked"
    if reader is None:
        return "no_match"
    wanted = _norm(card.fio)
    if not wanted:
        return "no_match"
    try:
        exact = [
            u for u in reader.search_users(card.fio) if _norm(u.display_name) == wanted
        ]
    except Exception:
        return "no_match"
    return "match" if len(exact) == 1 else "no_match"


# --- Регламентный запуск из worker (толерантность как у maybe_sync_weekly) ----

_SYNC_INTERVAL = timedelta(days=7)


def maybe_sync_links_weekly(
    store, client: OneCClient, reader: Optional[AdReader], links_store: LinksStore
) -> bool:
    """Еженедельная автосвязка (worker): тихо, без сбоев.

    Последний запуск свежий (< 7 дней) — False; нет ридера AD — False;
    иначе run_ad_sync (сбой не валит worker — False), успех — True."""
    from .settings_routes import read_setting_value

    if reader is None:
        return False
    last_raw = read_setting_value(store, "ad_links_synced_at")
    if isinstance(last_raw, str) and last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if datetime.now(timezone.utc) - last < _SYNC_INTERVAL:
                return False
        except ValueError:
            pass  # битое значение — считаем, что синхронизации не было
    raw = read_setting_value(store, "enterprises")
    if not isinstance(raw, list) or not raw:
        return False
    try:
        enterprises = [str(item.get("code")) for item in raw if isinstance(item, dict)]
        result = run_ad_sync(client, reader, links_store, enterprises)
        store.set(
            "ad_links_synced_at",
            json.dumps(datetime.now(timezone.utc).isoformat()),
        )
        return result.scanned > 0 or result.created > 0
    except Exception:
        return False
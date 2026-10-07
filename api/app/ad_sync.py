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
from datetime import datetime, timezone
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

# Причины расхождений автосопоставления (выдача админу + фильтры UI).
REASON_ONE_C_DUPLICATE = "one_c_duplicate"    # в 1С несколько карточек с этим ФИО
REASON_AD_DUPLICATE = "ad_duplicate"          # в AD несколько записей с этим ФИО
REASON_NOT_IN_AD = "not_in_ad"                # в AD нет записи с таким ФИО


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
    # Расхождения для выдачи админу (миграция 0010): строки расхождений прохода.
    # Собираются всегда — их показывает и ручной запуск, и регламент.
    discrepancies: List[dict] = field(default_factory=list)
    # Сколько строк расхождений записано в нашу БД (складка прохода для сводки).
    discrepancies_saved: int = 0
    # Карточки уволенных пропущены (решение человека: сопоставляем тех, кто
    # сейчас работает; дата увольнения — из регистра кадровых данных 1С).
    skipped_dismissed: int = 0

    def counts_by_reason(self) -> dict:
        """Счётчики расхождений по причине (сводка прохода и UI)."""
        counts: dict[str, int] = {}
        for row in self.discrepancies:
            reason = str(row.get("reason") or "")
            counts[reason] = counts.get(reason, 0) + 1
        return counts


def _norm(text: str) -> str:
    """Нормализация ФИО для сравнения (регистр/пробелы не различаются)."""
    return " ".join(text.strip().lower().split())


def _matches_ad_profile(card: object, ad_user: object) -> bool:
    """Совпадает ли карточка 1С с кандидатом AD по должности или службе.

    Нужна для дублей ФИО в 1С (у человека две карточки — например, старая и
    новая): одна совпадает с должностью/службой из AD, другая — нет. Только
    совпадение по ФИО для склейки недостаточно, это разные учётные записи."""
    pairs = (
        (getattr(card, "position", ""), getattr(ad_user, "title", "")),
        (getattr(card, "dept", ""), getattr(ad_user, "department", "")),
    )
    for one_c_value, ad_value in pairs:
        left = _norm(str(one_c_value or ""))
        right = _norm(str(ad_value or ""))
        if left and right and left == right:
            return True
    return False


def _enrich_duplicate_cards(client, cards: list) -> list:
    """Догрузить должность/службу карточек-дублей из регистра кадровых данных.

    Справочник 1С (list_employees) отдаёт только ФИО и табельный номер — dept и
    position пустые, а без них дубли ФИО не различить: у человека две карточки
    (старая и новая) и обе подходят по ФИО. Нужные поля приходят только из
    регистра текущих кадровых данных (OneCClient.get_employee enrich).

    Обогащаем по одному разу на группу ФИО и только когда без этого не обойтись
    (найден единственный кандидат в AD), иначе проход станет слишком долгим.
    Ответ 1С здесь — лучшее усилие: при ошибке остаётся карточка из справочника."""
    enriched: list = []
    for card in cards:
        try:
            enriched.append(
                client.get_employee(card.base_code, card.tab_num, card.enterprise)
            )
        except Exception:  # 1С не ответила — работаем на данных справочника
            enriched.append(card)
    return enriched


def _pick_duplicate_card(cards: list, ad_user: object) -> object | None:
    """Карточка 1С среди дублей ФИО, однозначно подходящая под кандидата AD.

    Ровно одно совпадение по должности или службе — берём его; 0 или больше
    одного — None (остаёмся на ручной сверке, как раньше)."""
    matched = [card for card in cards if _matches_ad_profile(card, ad_user)]
    return matched[0] if len(matched) == 1 else None


def _record_discrepancy(
    result: "AdSyncResult",
    card: object,
    reason: str,
    ad_user: object | None = None,
    candidates: list | None = None,
    one_c_cards: list | None = None,
    recommended: bool = False,
) -> None:
    """Запомнить строку расхождения для выдачи админу (миграция 0010).

    Всё, что нужно человеку для решения, попадает в одну строку: логин и ФИО
    кандидата AD, его подразделение/должность и — для дублей ФИО в 1С —
    должность/служба самой карточки, чтобы было видно, какая подходит.
    В 1С/AD не пишем, только наша таблица расхождений."""
    result.discrepancies.append(
        {
            "enterprise": getattr(card, "enterprise", "") or "",
            "base_code": getattr(card, "base_code", "") or "",
            "tab_num": getattr(card, "tab_num", "") or "",
            "fio": getattr(card, "fio", "") or "",
            "reason": reason,
            "ad_sam": getattr(ad_user, "sam", None),
            "ad_fio": getattr(ad_user, "display_name", None),
            "ad_dept": getattr(ad_user, "department", None),
            "ad_title": getattr(ad_user, "title", None),
            "one_c_dept": getattr(card, "dept", None) or None,
            "one_c_position": getattr(card, "position", None) or None,
            # Рекомендация для массового подтверждения: должность/служба этой
            # карточки 1С совпадает с кандидатом AD — связка выглядит верной.
            "recommended": bool(recommended)
            or (
                ad_user is not None
                and _matches_ad_profile(card, ad_user)
                and not candidates
            ),
            # Прочие кандидаты AD при дубле ФИО и табельные номера остальных
            # карточек 1С этой группы — в detail (JSONB), колонки плоские.
            "candidates": list(candidates or []),
            "sibling_tabs": [getattr(item, "tab_num", "") for item in (one_c_cards or [])],
        }
    )


def _list_enterprise(
    client: OneCClient,
    reader: AdReader,
    store: LinksStore,
    enterprise: str,
    result: AdSyncResult,
    now: str,
    dismissed: set | None = None,
) -> None:
    """Обойти все базы предприятия: собрать сотрудников и связать уникальные."""
    from .link import LinkRecord, link_key

    cards: List = []
    for base_code in client.bases_for_enterprise(enterprise):
        cards.extend(_list_all(client, base_code, enterprise, result))
    # Дубли ФИО в пределах предприятия: группируем карточки, чтобы при совпадении
    # должности/службы с AD связать нужную (см. _pick_duplicate_card).
    groups: dict[str, list] = {}
    for card in cards:
        groups.setdefault(_norm(card.fio), []).append(card)
    counts: dict[str, int] = {key: len(items) for key, items in groups.items()}
    duplicates: dict[str, list] = {key: items for key, items in groups.items() if len(items) > 1}
    enriched: dict[str, list] = {}  # дублей, догруженных из регистра 1С
    for card in cards:
        result.scanned += 1
        key = link_key(card.enterprise, card.base_code, card.tab_num)
        # Уволенных не сопоставляем: связь и маршрут им не нужны, а в расхождениях
        # они только мешают разбору. Даты увольнения берём из нашего справочника
        # (их наполняет ежедневный проход по регистру кадровых данных).
        if dismissed and key in dismissed:
            result.skipped_dismissed += 1
            continue
        try:
            if store.find(key) is not None:
                result.skipped_linked += 1
                continue
        except Exception:  # падение БД связок — ошибка уровня, не пропуск
            raise
        wanted = _norm(card.fio)
        if not wanted:
            result.skipped_1c_duplicates += 1
            continue
        is_duplicate = counts.get(wanted, 0) > 1
        try:
            candidates = reader.search_users(card.fio)
        except AdUnavailable as exc:
            result.errors.append("%s: AD: %s" % (enterprise, exc))
            result.skipped_ad_no_match += 1
            continue
        exact = [u for u in candidates if _norm(u.display_name) == wanted]
        if not exact:
            # Дублей нет — человек просто не заведён в AD. Дубли ФИО в 1С — это
            # другая причина (разбор по ручной сверке), счётчик прежний.
            _record_discrepancy(
                result,
                card,
                REASON_ONE_C_DUPLICATE if is_duplicate else REASON_NOT_IN_AD,
                recommended=False,
            )
            if is_duplicate:
                result.skipped_1c_duplicates += 1
            else:
                result.skipped_ad_no_match += 1
            continue
        if len(exact) > 1:
            result.skipped_ad_duplicates += 1
            # Кандидатов несколько — выбирает человек (в строке видно их логины).
            _record_discrepancy(
                result,
                card,
                REASON_AD_DUPLICATE,
                ad_user=exact[0],
                candidates=[u.sam for u in exact],
                recommended=False,
            )
            continue
        ad_user = exact[0]
        if is_duplicate:
            # Дубли ФИО в 1С: связываем только ту карточку, чья должность или
            # служба совпадает с AD. Без этого у человека не будет AD-карточки,
            # а значит — ни службы, ни руководителя (маршрут не соберётся).
            # Справочник 1С не отдаёт должность и службу, поэтому карточки группы
            # догружаем из регистра кадровых данных (по одному разу на ФИО).
            if wanted not in enriched:
                enriched[wanted] = _enrich_duplicate_cards(
                    client, duplicates.get(wanted, [])
                )
            picked = _pick_duplicate_card(enriched[wanted], ad_user)
            if picked is None:
                # Ни одна карточка группы не подошла: админ выбирает вручную,
                # видя должность/службу каждой карточки и данные кандидата AD.
                for sibling in enriched[wanted]:
                    _record_discrepancy(
                        result,
                        sibling,
                        REASON_ONE_C_DUPLICATE,
                        ad_user=ad_user,
                        one_c_cards=enriched[wanted],
                        recommended=False,
                    )
                result.skipped_1c_duplicates += 1
                continue
            if link_key(picked.enterprise, picked.base_code, picked.tab_num) != key:
                # Связалась другая карточка группы: остальные показываем админу
                # отдельными строками (их автоматика не связала). Карточку,
                # которую свяжут сейчас, в расхождения не пишем.
                for sibling in enriched[wanted]:
                    if link_key(sibling.enterprise, sibling.base_code, sibling.tab_num) == link_key(
                        picked.enterprise, picked.base_code, picked.tab_num
                    ):
                        continue
                    _record_discrepancy(
                        result,
                        sibling,
                        REASON_ONE_C_DUPLICATE,
                        ad_user=ad_user,
                        one_c_cards=enriched[wanted],
                        recommended=False,
                    )
                result.skipped_1c_duplicates += 1
                continue
            card = picked  # связываем и сохраняем карточку с должностью/службой
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
    save_discrepancies: bool = True,
    dismissed: Optional[set] = None,
) -> AdSyncResult:
    """Один проход автосвязки: предприятия → базы → сотрудники → verified-связки.

    enterprises — коды предприятий из settings (связка предприятие→базы — из
    синхронизации, client.bases_for_enterprise). now — инжектируемые часы для
    детерминированных тестов. dismissed — ключи карточек уволенных (справочник employees): их пропускаем. Только чтение 1С/AD + запись связок и расхождений в нашу БД."""
    result = AdSyncResult()
    moment = now or datetime.now(timezone.utc).isoformat()
    for enterprise in enterprises:
        try:
            _list_enterprise(
                client, reader, store, enterprise, result, moment, dismissed
            )
        except Exception as exc:  # падение одной ветки не валит остальные
            result.errors.append("%s: %s" % (enterprise, exc))
    if save_discrepancies and result.discrepancies:
        # Расхождения — выдача админу (миграция 0010), список один и тот же для
        # ручного запуска и регламента. Падение записи не должно обнулять уже
        # созданные связки: отсюда ошибка в result, а не исключение.
        try:
            result.discrepancies_saved = store.replace_discrepancies(
                result.discrepancies
            )
        except Exception as exc:
            result.errors.append("расхождения не сохранены: %s" % exc)
    return result


def find_unique_ad_match(reader: Optional[AdReader], fio: str):
    """Уникальная запись AD с точным ФИО (регистр/пробелы не различаются).

    Несколько AD-записей с тем же displayName либо нет ридера — None
    (дубли/недоступность — ручная сверка)."""
    wanted = _norm(fio)
    if not wanted or reader is None:
        return None
    try:
        exact = [
            u for u in reader.search_users(fio) if _norm(u.display_name) == wanted
        ]
    except Exception:
        return None
    return exact[0] if len(exact) == 1 else None


def compute_ad_status(
    card,
    link: Optional["LinkRecord"],
    reader: Optional[AdReader],
    exact_match=None,
) -> str:
    """Статус стыковки 1С↔AD для карточки/списка (без записи в БД).

    linked — связка есть; match — точное уникальное совпадение ФИО в AD
    (ждёт синхронизации/подтверждения); no_match — совпадения нет/дубли
    (в т.ч. AD недоступен) — «синхронизация не прошла». exact_match — уже
    найденное совпадение (избегаем повторного LDAP-поиска в карточке)."""
    if link is not None:
        return "linked"
    if reader is None:
        return "no_match"
    wanted = _norm(card.fio)
    if not wanted:
        return "no_match"
    if exact_match is None:
        exact_match = find_unique_ad_match(reader, card.fio)
    return "match" if exact_match is not None else "no_match"


# --- Регламентный запуск из worker (толерантность как у maybe_sync_weekly) ----


def maybe_sync_links_weekly(
    store,
    client: OneCClient,
    reader: Optional[AdReader],
    links_store: LinksStore,
    dismissed: Optional[set] = None,
) -> "AdSyncResult | bool":
    """Регламентная автосвязка (worker): тихо, без сбоев.

    «Не пора» по расписанию schedule_ad_links_sync (нет расписания — раз в 7
    дней от ad_links_synced_at) — False; нет ридера AD — False; иначе
    run_ad_sync (сбой не валит worker — False). Возврат: результат прохода
    AdSyncResult (истина — сводка для уведомления), пустой проход — False."""
    from .onec_sync import due_schedule
    from .settings_routes import read_setting_value

    if reader is None:
        return False
    schedule = read_setting_value(store, "schedule_ad_links_sync")
    last_raw = read_setting_value(store, "ad_links_synced_at")
    if not due_schedule(schedule, last_raw):
        return False
    raw = read_setting_value(store, "enterprises")
    if not isinstance(raw, list) or not raw:
        return False
    try:
        enterprises = [str(item.get("code")) for item in raw if isinstance(item, dict)]
        result = run_ad_sync(
            client, reader, links_store, enterprises, dismissed=dismissed
        )
        store.set(
            "ad_links_synced_at",
            json.dumps(datetime.now(timezone.utc).isoformat()),
        )
        return result if (result.scanned > 0 or result.created > 0) else False
    except Exception:
        return False

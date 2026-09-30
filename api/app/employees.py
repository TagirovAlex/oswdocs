# Поиск сотрудников и объединенная карточка (волна B1).
# Поиск по ФИО/таб. номеру/логину в пределах предприятия поверх OneCClient
# волны A3 (resolver.search_enterprise). Живых LDAP/HTTP-вызовов здесь нет:
# транспорт 1С и шлюз AD подменяются моками через зависимости FastAPI.
# Ролевая обрезка: ОК/админы — полная карточка, владелец шага — урезанная
# без ПДн (без ФИО/почты/даты приема/остатка отпуска), без групп — 403
# (проверка групп — в deps.get_current_user, здесь не дублируется).
# При расхождении данных истина — всегда 1С (см. карточку и модуль link).

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .ad_reader import AdNotFound, AdReader, AdUnavailable, build_snapshot
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user, is_privileged
from .onec_client import (
    EmployeeCard,
    OneCBaseConfig,
    OneCBaseDown,
    OneCCircuitOpen,
    OneCClient,
    OneCError,
    OneCNotFound,
)
from .resolver import (
    UnknownEnterpriseError,
    is_snapshot_stale,
    make_snapshot_1c,
    search_enterprise,
)
from .settings_routes import (
    DbSettingsStore,
    get_settings_store,
    read_setting_value,
)

router = APIRouter(tags=["сотрудники"])


# ---------------------------------------------------------------------------
# Зависимости-интерфейсы (границы для моков волны A)
# ---------------------------------------------------------------------------

def _bases_from_settings(store: DbSettingsStore | None) -> dict[str, OneCBaseConfig]:
    """Базы 1С из settings (onec_bases) в формате OneCClient: код -> конфиг.

    Непустые enterprise/code обязательны; url/user/secret подставляются как
    есть (пустые допустимы — проверка доступности на транспорте)."""
    if store is None or not hasattr(store, "get"):
        return {}
    raw = read_setting_value(store, "onec_bases")
    bases: dict[str, OneCBaseConfig] = {}
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("enterprise") or not item.get("code"):
            continue
        code = str(item["code"])
        bases[code] = OneCBaseConfig(
            code=code,
            enterprise=str(item["enterprise"]),
            url=str(item.get("url") or ""),
            user=str(item.get("user") or ""),
            secret=str(item.get("password") or ""),
        )
    return bases


def get_onec_client(
    settings: Settings = Depends(get_settings),
    store: DbSettingsStore | None = Depends(get_settings_store),
) -> OneCClient:
    """Боевой клиент 1С: базы из settings (onec_bases), иначе из env; + кэш Redis.

    Без баз в настройках и в ONEC_BASES_JSON — 503 (прежнее поведение);
    кэшируется только успешный get_employee, падение Redis не валит чтение.
    """
    from .onec_cache import CachingOneCClient, RedisCardCache
    from .onec_client import load_bases_from_env

    bases = _bases_from_settings(store)
    if not bases:
        bases = load_bases_from_env()
    if not bases:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Клиент 1С не настроен (нет баз в ONEC_BASES_JSON)",
        )
    client = OneCClient(bases)
    cache = RedisCardCache(settings.REDIS_URL, settings.ONEC_CACHE_TTL)
    return CachingOneCClient(client, cache, ttl_seconds=settings.ONEC_CACHE_TTL)


def get_ad_reader() -> AdReader | None:
    """Ридер AD волны A4. Необязательный: без него — только данные 1С.

    В offline-тестах подменяется фейковым шлюзом; падения AD не кладут API.
    """
    return None


# ---------------------------------------------------------------------------
# Обрезка карточек по ролям
# ---------------------------------------------------------------------------

def _full_item(
    card: EmployeeCard,
    sam: str | None = None,
    duplicate: bool = False,
) -> dict:
    """Полная карточка для ОК/админов (все поля 1С + служебный логин связи)."""
    return {
        "enterprise": card.enterprise,
        "base_code": card.base_code,
        "tab_num": card.tab_num,
        "key": card.key(),
        "fio": card.fio,
        "dept": card.dept,
        "position": card.position,
        "employment_type": card.employment_type,
        "hire_date": card.hire_date,
        "vacation_balance": card.vacation_balance,
        "ad_sam": sam,
        "needs_manual_review": duplicate,
    }


def _trimmed_item(
    card: EmployeeCard,
    sam: str | None = None,
    duplicate: bool = False,
) -> dict:
    """Урезанная карточка для владельца (без ПДн: нет ФИО/таб.№/почты/отпуска/приема).

    Оставлены только служебные ключи (key, подразделение, должность) —
    достаточно для отметок по своим задачам (фильтр задач — в B2).
    Правило едино с фронтом (EmployeeBrief) и requests._public_view:
    таб.№ идентифицирует человека, поэтому владельцу не отдается.
    """
    return {
        "enterprise": card.enterprise,
        "base_code": card.base_code,
        "key": card.key(),
        "dept": card.dept,
        "position": card.position,
        "ad_sam": sam,
        "needs_manual_review": duplicate,
    }


def _norm(text: str) -> str:
    """Нормализация для сравнения (регистр/лишние пробелы не различаются)."""
    return " ".join(text.strip().lower().split())


def _duplicated_fios(cards: list[EmployeeCard]) -> set[str]:
    """Нормализованные ФИО, встретившиеся более одного раза (дубли на сверку)."""
    counts: dict[str, int] = {}
    for card in cards:
        key = _norm(card.fio)
        counts[key] = counts.get(key, 0) + 1
    return {fio for fio, count in counts.items() if count > 1}


def _match_query(card: EmployeeCard, sam: str | None, query: str) -> bool:
    """Совпадение запроса с ФИО, таб. номером или логином связанной записи."""
    needle = query.strip().lower()
    if not needle:
        return False
    if needle in card.fio.lower():
        return True
    if needle in card.tab_num.lower():
        return True
    if sam and needle in sam.lower():
        return True
    return False


def _link_sam_for(card: EmployeeCard) -> str | None:
    """Логин связанной AD-записи из хранилища связок (без падения без него)."""
    try:
        from .link import find_link  # локально против циклического импорта
    except Exception:
        return None
    try:
        found = find_link(card.enterprise, card.base_code, card.tab_num)
    except Exception:
        return None
    return found.sam if found is not None else None


# ---------------------------------------------------------------------------
# Поиск по предприятию
# ---------------------------------------------------------------------------

@router.get("/employees")
def search_employees(
    enterprise: str = Query(..., min_length=1, description="Предприятие из настроек"),
    q: str = Query(..., min_length=1, description="Подстрока ФИО, таб. номера или логина"),
    limit: int = Query(default=25, ge=1, le=100, description="Максимум записей в выдаче"),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    client: OneCClient = Depends(get_onec_client),
    reader: AdReader | None = Depends(get_ad_reader),
) -> dict:
    """Поиск сотрудников предприятия с ролевой обрезкой и изоляцией падения баз.

    Падение одной базы 1С не валит остальные: ошибки баз возвращаются списком,
    живые базы — обычным результатом. Дубли одного ФИО не склеиваются —
    помечаются флагом на ручную сверку ОК.
    """
    settings.ensure_read_only()
    try:
        found = search_enterprise(enterprise, q, client)
    except UnknownEnterpriseError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    # Добор по логину: точное совпадение sam в AD подтягивает связанную
    # карточку 1С (в 1С логина нет, связка — единственный мостик).
    extra: list[EmployeeCard] = []
    if reader is not None:
        try:
            ad_user = reader.get_user(q.strip())
        except (AdNotFound, AdUnavailable):
            ad_user = None
        except Exception:
            ad_user = None
        if ad_user is not None:
            try:
                from .link import find_links_by_sam  # локально против цикла

                for linked in find_links_by_sam(ad_user.sam):
                    if linked.enterprise != enterprise:
                        continue
                    try:
                        extra.append(client.get_employee(linked.base_code, linked.tab_num))
                    except (OneCNotFound, OneCBaseDown, OneCCircuitOpen, OneCError):
                        continue
                    except Exception:
                        continue
            except Exception:
                pass
    # Сводный список без повторов ключа; серверный фильтр 1С дублируем
    # локально (мок-транспорт в тестах может вернуть шире).
    merged: dict[str, EmployeeCard] = {}
    for card in list(found.cards) + extra:
        merged.setdefault(card.key(), card)
    cards = [c for c in merged.values() if _match_query(c, _link_sam_for(c), q)][:limit]
    duplicates = _duplicated_fios(cards)
    privileged = is_privileged(user)
    items = [
        (
            _full_item(card, _link_sam_for(card), _norm(card.fio) in duplicates)
            if privileged
            else _trimmed_item(card, _link_sam_for(card), _norm(card.fio) in duplicates)
        )
        for card in cards
    ]
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="employees.search",
            entity="employee",
            entity_id=enterprise,
            detail="найдено: %d" % len(items),
        )
    )
    return {
        "items": items,
        "errors": found.errors,
        "needs_manual_review": bool(duplicates),
    }


# ---------------------------------------------------------------------------
# Объединенная карточка со снапшотами (истина — 1С)
# ---------------------------------------------------------------------------

@router.get("/employees/card")
def employee_card(
    enterprise: str = Query(..., min_length=1),
    base_code: str = Query(..., min_length=1),
    tab_num: str = Query(..., min_length=1),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    client: OneCClient = Depends(get_onec_client),
    reader: AdReader | None = Depends(get_ad_reader),
) -> dict:
    """Карточка сотрудника: блок 1С (истина) + блок AD + связка и расхождения.

    ОК/админы видят полные снапшоты; владельцу — только обезличенный ключ,
    сроки и факт расхождения без значений ПДн. Падение AD не кладет запрос.
    """
    settings.ensure_read_only()
    try:
        card = client.get_employee(base_code, tab_num)
    except OneCNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (OneCCircuitOpen, OneCBaseDown) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except OneCError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    if card.enterprise != enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Карточка не принадлежит предприятию",
        )
    snapshot_1c = make_snapshot_1c(card)
    stale = is_snapshot_stale(snapshot_1c)
    # Дубли ФИО в пределах предприятия — на ручную сверку (автосклейки нет).
    try:
        same_name = search_enterprise(enterprise, card.fio, client)
        duplicate = (
            sum(1 for c in same_name.cards if _norm(c.fio) == _norm(card.fio)) > 1
        )
    except UnknownEnterpriseError:
        duplicate = False
    except Exception:
        duplicate = False
    # Связка и блок AD (только чтение; отсутствие связки — не ошибка).
    link_info: dict = {"linked": False}
    snapshot_ad: dict | None = None
    divergences: list[str] = []
    ad_error: str | None = None
    try:
        from .link import find_link  # локально против циклического импорта

        stored = find_link(enterprise, base_code, tab_num)
    except Exception:
        stored = None
    if stored is not None:
        link_info = {
            "linked": True,
            "sam": stored.sam,
            "by": stored.by,
            "at": stored.at,
            "verified": stored.verified,
        }
        if reader is not None:
            try:
                ad_user = reader.get_user(stored.sam)
                compared = build_snapshot(
                    {
                        "fio": card.fio,
                        "department": card.dept,
                        "title": card.position,
                    },
                    ad_user,
                )
                divergences = compared.divergences()
                snapshot_ad = {
                    "sam": ad_user.sam,
                    "display_name": ad_user.display_name,
                    "department": ad_user.department,
                    "title": ad_user.title,
                    "manager_dn": ad_user.manager_dn,
                    "mail": ad_user.mail,
                }
            except AdNotFound:
                ad_error = "запись AD не найдена"
            except AdUnavailable as exc:
                ad_error = str(exc)
            except Exception as exc:
                ad_error = str(exc)
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="employees.card.read",
            entity="employee",
            entity_id=card.key(),
        )
    )
    base: dict = {
        "key": card.key(),
        "enterprise": card.enterprise,
        "base_code": card.base_code,
        "tab_num": card.tab_num,
        "truth_source": "1c",
        "link": link_info,
        "divergences": divergences,
        "needs_manual_review": bool(duplicate or divergences),
        "snapshot_stale": stale,
    }
    if ad_error is not None:
        base["ad_error"] = ad_error
    if is_privileged(user):
        base.update(
            {
                "fio": card.fio,
                "dept": card.dept,
                "position": card.position,
                "employment_type": card.employment_type,
                "hire_date": card.hire_date,
                "vacation_balance": card.vacation_balance,
                "ad_sam": link_info.get("sam"),
                "snapshot_1c": snapshot_1c,
                "snapshot_ad": snapshot_ad,
            }
        )
        return base
    # Владельцу — обезличенно: без ФИО/таб.№/почты/отпуска/приема и без значений снапшотов.
    # Таб.№ убираем из base: идентифицирует человека (правило фронта/requests).
    base.pop("tab_num", None)
    base.update(
        {
            "dept": card.dept,
            "position": card.position,
            "ad_sam": link_info.get("sam"),
            "snapshot_1c": {
                "key": snapshot_1c.get("key"),
                "fetched_at": snapshot_1c.get("fetched_at"),
            },
            "snapshot_ad": None,
        }
    )
    return base

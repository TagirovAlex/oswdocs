# Поиск сотрудников и объединенная карточка (волна B1).
# Поиск по ФИО/таб. номеру/логину в пределах предприятия поверх OneCClient
# волны A3 (resolver.search_enterprise). Живых LDAP/HTTP-вызовов здесь нет:
# транспорт 1С и шлюз AD подменяются моками через зависимости FastAPI.
# Ролевая обрезка: ОК/админы — полная карточка, владелец шага — урезанная
# без ПДн (без ФИО/почты/дат приёма/увольнения), без групп — 403
# (проверка групп — в deps.get_current_user, здесь не дублируется).
# При расхождении данных истина — всегда 1С (см. карточку и модуль link).

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .ad_groups_cache import (
    CachedMember,
    GroupsCacheStore,
    GroupsCacheUnavailable,
    get_groups_cache_store,
    sync_ad_group_members,
)
from .ad_reader import AdNotFound, AdReader, AdUnavailable, build_snapshot
from .ad_sync import compute_ad_status, find_unique_ad_match
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user, is_privileged
from .employee_sync import (
    EmployeeSyncUnavailable,
    EmployeeSyncStore,
    get_employee_sync_store,
    sync_employees,
)
from .link_store import LinksStore, get_links_store
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
    search_enterprise_page,
)
from .settings_routes import (
    DbSettingsStore,
    SettingsUnavailable,
    get_settings_store,
    is_group_allowed_with_settings,
    read_setting_value,
)

router = APIRouter(tags=["сотрудники"])

# Размер страницы справочника по умолчанию (блок E): константа модуля, чтобы
# дефолт не хардкодить в Query; фронт может переопределить настройкой
# directory_page_size (settings БД, см. PLAN.md блок E).
DIRECTORY_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Зависимости-интерфейсы (границы для моков волны A)
# ---------------------------------------------------------------------------

def _bases_from_settings(store: DbSettingsStore | None) -> dict[str, OneCBaseConfig]:
    """Базы 1С из settings (onec_bases) в формате OneCClient: код -> конфиг.

    Код базы обязателен; поля схемы (сущности/поля OData) — с дефолтами ЗУП 3.х
    (правит ИТ по факту из базы). Маппинг предприятий — из синхронизации,
    не из конфига базы."""
    if store is None or not hasattr(store, "get"):
        return {}
    raw = read_setting_value(store, "onec_bases")
    bases: dict[str, OneCBaseConfig] = {}
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("code"):
            continue
        code = str(item["code"])
        bases[code] = OneCBaseConfig(
            code=code,
            url=str(item.get("url") or ""),
            user=str(item.get("user") or ""),
            secret=str(item.get("password") or ""),
            employee_entity=str(item.get("employee_entity") or "Catalog_Сотрудники"),
            organization_entity=str(item.get("organization_entity") or "Catalog_Организации"),
            employee_org_field=str(item.get("employee_org_field") or "ГоловнаяОрганизация_Key"),
            tab_num_field=str(item.get("tab_num_field") or "Code"),
            fio_field=str(item.get("fio_field") or "Description"),
            department_field=str(item.get("department_field") or "ТекущееПодразделение/Description"),
            position_field=str(item.get("position_field") or "ТекущаяДолжность/Description"),
            hire_date_field=str(item.get("hire_date_field") or "ДатаПриема"),
            termination_date_field=str(item.get("termination_date_field") or "ДатаУвольнения"),
            phone_field=str(item.get("phone_field") or ""),
            email_field=str(item.get("email_field") or ""),
            hr_entity=str(item.get("hr_entity") or "InformationRegister_ТекущиеКадровыеДанныеСотрудников"),
            hr_employee_field=str(item.get("hr_employee_field") or "Сотрудник_Key"),
            organization_code_field=str(item.get("organization_code_field") or "Ref_Key"),
            organization_name_field=str(item.get("organization_name_field") or "Description"),
        )
    return bases


def _enterprise_index_from_settings(store: DbSettingsStore | None) -> dict[str, list[str]] | None:
    """Маппинг предприятие→базы из синхронизации (settings.onec_enterprise_bases)."""
    if store is None or not hasattr(store, "get"):
        return None
    raw = read_setting_value(store, "onec_enterprise_bases")
    if isinstance(raw, dict):
        return {str(code): [str(b) for b in bases] for code, bases in raw.items()}
    return None


def get_onec_client(
    settings: Settings = Depends(get_settings),
    store: DbSettingsStore | None = Depends(get_settings_store),
) -> OneCClient:
    """Боевой клиент 1С: базы из settings (onec_bases), иначе из env; + кэш Redis.

    Маппинг предприятие→базы — из синхронизации (settings.onec_enterprise_bases),
    иначе производный от конфигов (совместимость). Без баз — 503; кэшируется
    только успешный get_employee, падение Redis не валит чтение.
    """
    from .onec_cache import CachingOneCClient, RedisCardCache
    from .onec_client import load_bases_from_env

    bases = _bases_from_settings(store)
    if not bases:
        bases = load_bases_from_env()
    if not bases:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Клиент 1С не настроен (нет баз в настройках 1С)",
        )
    client = OneCClient(bases, enterprise_index=_enterprise_index_from_settings(store))
    cache = RedisCardCache(settings.REDIS_URL, settings.ONEC_CACHE_TTL)
    return CachingOneCClient(client, cache, ttl_seconds=settings.ONEC_CACHE_TTL)


def get_ad_reader() -> AdReader | None:
    """Ридер AD (только чтение). Необязательный: без него — только данные 1С.

    На стенде — реальный Ldap3Gateway из env (как в auth.get_auth_service);
    сбой конфигурации AD — None (API не падает, AD-блок просто недоступен)."""
    try:
        from .ad_reader import (
            AdReader,
            AdReaderSettings,
            InMemoryCache,
            Ldap3Gateway,
            reader_secret_from_env,
        )

        current = get_settings()
        ad_settings = AdReaderSettings(
            ad_url=current.AD_URL,
            base_dn=current.AD_BASE_DN,
            reader_dn=current.AD_READER_DN,
            reader_secret=reader_secret_from_env(),
            cache_ttl_seconds=current.LDAP_CACHE_TTL,
            tls_validate=current.AD_TLS_VALIDATE,
            ca_certs_file=current.AD_CA_CERT,
        )
        gateway = Ldap3Gateway(ad_settings)
        return AdReader(ad_settings, gateway=gateway, cache=InMemoryCache())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Обрезка карточек по ролям
# ---------------------------------------------------------------------------

def _full_item(
    card: EmployeeCard,
    sam: str | None = None,
    duplicate: bool = False,
    ad_status: str = "no_match",
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
        "ad_sam": sam,
        "ad_status": ad_status,
        "needs_manual_review": duplicate,
    }


def _trimmed_item(
    card: EmployeeCard,
    sam: str | None = None,
    duplicate: bool = False,
    ad_status: str = "no_match",
) -> dict:
    """Урезанная карточка для владельца (без ПДн: нет ФИО/таб.№/почты/дат приёма-увольнения).

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
        "ad_status": ad_status,
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


def _link_for(card: EmployeeCard):
    """Связка 1С-AD из хранилища (без падения без него)."""
    try:
        from .link import find_link  # локально против циклического импорта
    except Exception:
        return None
    try:
        return find_link(card.enterprise, card.base_code, card.tab_num)
    except Exception:
        return None


def _link_sam_for(card: EmployeeCard) -> str | None:
    """Логин связанной AD-записи из хранилища связок (без падения без него)."""
    found = _link_for(card)
    return found.sam if found is not None else None


def _local_duplicated_fios(rows: list[dict]) -> set[str]:
    """Нормализованные ФИО из локального справочника, встретившиеся более раза."""
    counts: dict[str, int] = {}
    for row in rows:
        key = _norm(row["fio"])
        counts[key] = counts.get(key, 0) + 1
    return {fio for fio, count in counts.items() if count > 1}


def _local_item(row: dict, duplicate: bool, privileged: bool) -> dict:
    """Запись локального справочника -> элемент поиска (контракт как у живого пути).

    ad_sam/ad_status — из таблицы (NULL трактуется как 'no_match'); полей, которых
    в таблице нет (вид занятости/дата приёма), нет и в выдаче — как в _full_item."""
    ad_status = row.get("ad_status") or "no_match"
    item = {
        "enterprise": row["enterprise"],
        "base_code": row["base_code"],
        "key": "%s|%s|%s" % (row["enterprise"], row["base_code"], row["tab_num"]),
        "dept": row.get("department") or "",
        "position": row.get("position") or "",
        "ad_sam": row.get("ad_sam"),
        "ad_status": ad_status,
        "needs_manual_review": duplicate,
    }
    if privileged:
        item.update(
            {
                "tab_num": row["tab_num"],
                "fio": row["fio"],
                "employment_type": "",
                "hire_date": "",
            }
        )
    return item


# ---------------------------------------------------------------------------
# Поиск по предприятию
# ---------------------------------------------------------------------------

@router.get("/employees")
def search_employees(
    enterprise: str = Query(..., min_length=1, description="Предприятие из настроек"),
    q: str = Query(default="", max_length=200, description="Подстрока ФИО, таб. № или логина (пусто — весь список)"),
    page: int = Query(default=1, ge=1, description="Номер страницы (1-based)"),
    page_size: int = Query(
        default=DIRECTORY_PAGE_SIZE, ge=1, le=500, description="Размер страницы"
    ),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    client: OneCClient = Depends(get_onec_client),
    reader: AdReader | None = Depends(get_ad_reader),
    emp_store: EmployeeSyncStore = Depends(get_employee_sync_store),
) -> dict:
    """Справочник/поиск сотрудников предприятия с ролевой обрезкой.

    Источник — ЛОКАЛЬНАЯ таблица employees (синк из 1С + связка AD): фильтр по
    предприятию (точное) + подстрока ФИО/таб.№/должности/логина (без регистра),
    тот же контракт выдачи. Серверная пагинация локальной таблицы — page/
    page_size (LIMIT/OFFSET + COUNT(*) по тем же условиям); ответ дополнен
    total/page/page_size, совместимо с прежним (items остаётся). Пока таблица
    пуста (первый запуск, синк не прошёл) или БД справочника недоступна —
    фолбэк на живой поиск 1С (прежний путь, без доп. пейджинга 1С), total =
    len(items). q пустой — вернуть список (страницу page_size), для справочника.
    """
    settings.ensure_read_only()
    # Локальный справочник: если синк прошёл (в таблице есть строки) — читаем из
    # неё; пустая таблица и падение БД справочника — фолбэк на живой 1С.
    table_has_data = False
    local_rows: list[dict] = []
    local_total = 0
    try:
        table_has_data = emp_store.count() > 0
        if table_has_data:
            local_rows = emp_store.search(
                enterprise, q, page_size, (page - 1) * page_size
            )
            local_total = emp_store.count_matching(enterprise, q)
    except Exception:
        table_has_data = False  # БД справочника недоступна — живой поиск не ломаем
    if table_has_data:
        duplicates = _local_duplicated_fios(local_rows)
        privileged = is_privileged(user)
        items = [
            _local_item(row, duplicate=_norm(row["fio"]) in duplicates, privileged=privileged)
            for row in local_rows
        ]
        audit_log.append(
            AuditEvent(
                actor=user.sam,
                action="employees.search",
                entity="employee",
                entity_id=enterprise,
                detail="найдено: %d (локальный справочник)" % len(items),
            )
        )
        return {
            "items": items,
            "total": local_total,
            "page": page,
            "page_size": page_size,
            "errors": [],
            "needs_manual_review": bool(duplicates),
        }
    # Фолбэк: прежний живой поиск 1С (до первого синка). Страница — skip/top
    # (page/page_size запроса), total — сумма odata.count живых баз.
    try:
        found = search_enterprise_page(
            enterprise, q, client, skip=(page - 1) * page_size, top=page_size
        )
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
                        extra.append(
                            client.get_employee(linked.base_code, linked.tab_num, enterprise)
                        )
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
    # Пустой q — весь список (справочник), иначе фильтр по ФИО/таб.№/логину.
    # Обрезка до page_size (как раньше до limit); доп. пейджинга 1С нет —
    # total = len(items), page/page_size отдаём как запрошено.
    cards = [c for c in merged.values() if (not q.strip()) or _match_query(c, _link_sam_for(c), q)][:page_size]
    duplicates = _duplicated_fios(cards)
    privileged = is_privileged(user)
    items = []
    for card in cards:
        link = _link_for(card)
        sam = link.sam if link is not None else None
        ad_status = compute_ad_status(card, link, reader)
        item = (
            _full_item(card, sam, _norm(card.fio) in duplicates, ad_status)
            if privileged
            else _trimmed_item(card, sam, _norm(card.fio) in duplicates, ad_status)
        )
        items.append(item)
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="employees.search",
            entity="employee",
            entity_id=enterprise,
            detail="найдено: %d (живой поиск 1С, total=%d)" % (len(items), found.total),
        )
    )
    return {
        "items": items,
        "total": found.total,
        "page": page,
        "page_size": page_size,
        "errors": found.errors,
        "needs_manual_review": bool(duplicates),
    }


@router.post("/employees/sync")
def sync_employees_endpoint(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    links_store: LinksStore = Depends(get_links_store),
) -> dict:
    """Принудительная синхронизация локального справочника сотрудников из 1С: только admin.

    Источник не настроен/недоступен — 503 (не 500); успех —
    {"synced": N, "errors": [...], "at": ISO}. Запись — только в нашу таблицу
    employees, 1С — только чтение."""
    settings.ensure_read_only()
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Синхронизацию справочника запускает только админ",
        )
    try:
        result = sync_employees(settings_store, links_store)
    except EmployeeSyncUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="employees.sync",
            entity="employee",
            entity_id="employees",
            detail="synced=%d" % result["synced"],
        )
    )
    return {
        "synced": result["synced"],
        "errors": result["errors"],
        "at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Поиск AD по ФИО (для выбора исполнителей маршрута и ручной привязки, ОК)
# ---------------------------------------------------------------------------

@router.get("/ad/search")
def ad_search(
    q: str = Query(default="", max_length=200, description="Подстрока ФИО (displayName)"),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    reader: AdReader | None = Depends(get_ad_reader),
) -> dict:
    """Кандидаты AD по подстроке ФИО (исполнители маршрута/ручная привязка):
    только ОК, руководитель ОК и админ.

    Ридер AD не настроен — 503 (не 500); сбой каталога — 503. Возвращается
    минимальный набор (sam/displayName/депт/должность/mail) для выбора
    в карточке справочника. Только чтение AD."""
    settings.ensure_read_only()
    if user.role not in ("hr", "hr_admin", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Поиск AD доступен ОК и админу",
        )
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ридер AD не настроен (в offline — подмена фейковым шлюзом)",
        )
    q = (q or "").strip()
    if not q:
        return {"items": []}
    try:
        users = reader.search_users(q)
    except AdUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return {
        "items": [
            {
                "sam": u.sam,
                "display_name": u.display_name,
                "department": u.department,
                "title": u.title,
                "mail": u.mail,
            }
            for u in users
        ]
    }


# ---------------------------------------------------------------------------
# Состав группы AD (конструктор маршрута, ОК/админы)
# ---------------------------------------------------------------------------

@router.get("/ad/groups/{group}/members")
def ad_group_members(
    group: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: DbSettingsStore | None = Depends(get_settings_store),
    reader: AdReader | None = Depends(get_ad_reader),
    cache_store: GroupsCacheStore = Depends(get_groups_cache_store),
) -> dict:
    """Активные участники группы AD для конструктора маршрута и карточки
    заявки: ОК, руководитель ОК, админ и владельцы шагов (согласующие).

    Владельцам состав открыт решением владельца процесса: заявки запускает ОК,
    у согласующих доступ к данным по должности — иначе в карточке виден
    только код/название группы. Вход в систему уже требует членства
    в разрешённых группах, посторонних здесь нет.

    Группа обязана быть группой ручного конструктора шагов
    (is_group_allowed_with_settings: контент-ключ allowed_ad_groups либо
    префикс владельцев шагов STEP_GROUP_PREFIX) — иначе 403. Ключ
    access_groups (группы входа в систему) состав шага НЕ открывает.

    Состав берётся из локального кэша (таблица ad_group_members, синк —
    регламент worker + ручной POST /api/ad/groups/sync): каталог на чтение
    карточки не дёргается. Группы нет в кэше — живое чтение AD с записью
    в кэш (группа не найдена — 404); ридер AD не настроен/сбой каталога —
    503 (не 500). Набор полей тот же, что в /ad/search
    (sam/ФИО/депт/должность/mail). Записей в AD/1С нет, только чтение."""
    settings.ensure_read_only()
    if not (is_privileged(user) or user.role == "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Состав группы AD доступен участникам процесса",
        )
    name = (group or "").strip()
    if not is_group_allowed_with_settings(settings, store, name):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Группа не разрешена настройками (allowed_ad_groups — группы ручного конструктора шагов)",
        )
    try:
        synced, cached = cache_store.load(name)
    except GroupsCacheUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if synced:
        members = cached
    else:
        if reader is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ридер AD не настроен (в offline — подмена фейковым шлюзом)",
            )
        try:
            live = reader.group_members(name)
        except AdNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        except AdUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        members = live
        try:
            # Кэш пишем из уже прочитанного (без второго чтения AD).
            cache_store.save(
                name,
                [
                    CachedMember(
                        group_name=name,
                        sam=u.sam,
                        display_name=u.display_name or "",
                        department=u.department or None,
                        title=u.title or None,
                        mail=u.mail or None,
                    )
                    for u in live
                ],
            )
        except GroupsCacheUnavailable:
            pass  # кэш не записался — отдаём живое, следующий запрос повторит
    return {
        "items": [
            {
                "sam": u.sam,
                "display_name": u.display_name,
                "department": u.department,
                "title": u.title,
                "mail": u.mail,
            }
            for u in members
        ]
    }


@router.post("/ad/groups/sync")
def sync_ad_groups_endpoint(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    reader: AdReader | None = Depends(get_ad_reader),
    cache_store: GroupsCacheStore = Depends(get_groups_cache_store),
) -> dict:
    """Принудительная синхронизация состава групп AD в локальный кэш: только admin.

    Группы — справочник allowed_ad_groups из settings (пусто — 503 «группы не
    настроены»); ридер AD не настроен — 503 (не 500); успех —
    {"synced_groups": N, "members": M, "errors": [...], "at": ISO}. Запись —
    только в наши таблицы ad_group_members/ad_group_sync_state, AD — только
    чтение."""
    settings.ensure_read_only()
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Синхронизацию состава групп запускает только админ",
        )
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ридер AD не настроен (в offline — подмена фейковым шлюзом)",
        )
    from .settings_routes import _groups_with_names

    try:
        raw = read_setting_value(settings_store, "allowed_ad_groups")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    groups = [item["id"] for item in _groups_with_names(raw)] if isinstance(raw, list) else []
    if not groups:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Группы не настроены: заполните справочник групп",
        )
    try:
        result = sync_ad_group_members(reader, groups, cache_store)
    except GroupsCacheUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="ad_groups.sync",
            entity="ad_group",
            entity_id="ad_groups",
            detail="synced_groups=%d members=%d" % (result["synced_groups"], result["members"]),
        )
    )
    return {
        "synced_groups": result["synced_groups"],
        "members": result["members"],
        "errors": result["errors"],
        "at": datetime.now(timezone.utc).isoformat(),
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
        card = client.get_employee(base_code, tab_num, enterprise)
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
    # Уникальное точное совпадение ФИО в AD (без связки) — один поиск для
    # ad_status и блока ad (не гоняем LDAP дважды).
    exact_match = None
    if stored is None and reader is not None:
        exact_match = find_unique_ad_match(reader, card.fio)
    ad_status = compute_ad_status(card, stored, reader, exact_match)
    if is_privileged(user):
        # Блок AD для отображения: при связке — снапшот связанной записи,
        # без связки — уникальное точное совпадение ФИО (кандидат на привязку).
        ad_block: dict | None = snapshot_ad
        if stored is None and exact_match is not None:
            ad_block = {
                "sam": exact_match.sam,
                "display_name": exact_match.display_name,
                "department": exact_match.department,
                "title": exact_match.title,
                "manager_dn": exact_match.manager_dn,
                "mail": exact_match.mail,
            }
        base.update(
            {
                "fio": card.fio,
                "dept": card.dept,
                "position": card.position,
                "employment_type": card.employment_type,
                "hire_date": card.hire_date,
                "dismissal_date": card.dismissal_date,
                "phone": card.phone,
                "email": card.email,
                "ad_sam": link_info.get("sam"),
                "ad_status": ad_status,
                "ad": ad_block,
                "snapshot_1c": snapshot_1c,
                "snapshot_ad": snapshot_ad,
            }
        )
        return base
    # Владельцу — обезличенно: без ФИО/таб.№/почты/дат приёма-увольнения и без значений снапшотов.
    # Таб.№ убираем из base: идентифицирует человека (правило фронта/requests).
    base.pop("tab_num", None)
    base.update(
        {
            "dept": card.dept,
            "position": card.position,
            "ad_sam": link_info.get("sam"),
            "ad_status": ad_status,
            "snapshot_1c": {
                "key": snapshot_1c.get("key"),
                "fetched_at": snapshot_1c.get("fetched_at"),
            },
            "snapshot_ad": None,
        }
    )
    return base

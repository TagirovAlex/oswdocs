# Ручная связка записей 1С и AD (волна B1/4): link_1c_ad и снапшоты.
# Стыковка — только по полному ФИО + ручное подтверждение ОК (факт вызова).
# При любом расхождении истина — 1С; оба значения видны в снапшоте.
# Дубли одного ФИО не склеиваются автоматически — помечаются на ручную сверку.
# Хранилище — зависимость get_links_store: in-memory на офлайне/в тестах,
# Postgres (DbLinksStore) на стенде; логика эндпоинтов не зависит от реализации
# (интерфейс LinksStore в link_store.py).
# Записи в 1С/AD здесь нет: только чтение карточек и запись факта связки у нас.

from __future__ import annotations

import datetime as _dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .ad_reader import AdNotFound, AdUnavailable, build_snapshot
from .ad_sync import AdSyncUnavailable, run_ad_sync
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user, is_privileged
from .employee_sync import get_employee_sync_store
from .employees import get_ad_reader, get_onec_client
from .link_store import (
    InMemoryLinksStore,
    LinksStore,
    LinksUnavailable,
    get_links_store,
)
from .onec_client import (
    OneCBaseDown,
    OneCCircuitOpen,
    OneCClient,
    OneCError,
    OneCNotFound,
)
from .resolver import UnknownEnterpriseError, make_snapshot_1c, search_enterprise
from .settings_routes import (
    DbSettingsStore,
    SettingsUnavailable,
    get_settings_store,
    read_setting_value,
)
from .ad_reader import AdReader

router = APIRouter(tags=["связка 1С-AD"])


class LinkCreate(BaseModel):
    """Заявка ОК на ручную связку (явный составной ключ + логин AD)."""

    enterprise: str = Field(min_length=1, description="Предприятие из настроек")
    base_code: str = Field(min_length=1, description="Код базы 1С")
    tab_num: str = Field(min_length=1, description="Табельный номер в базе")
    sam: str = Field(min_length=1, description="Логин AD (sAMAccountName)")


class LinkRecord(BaseModel):
    """Факт ручной связки (решает ОК вручную, автосклейки нет)."""

    enterprise: str
    base_code: str
    tab_num: str
    key: str
    sam: str
    by: str
    at: str
    verified: bool = True
    diverged: bool = False
    needs_manual_review: bool = False
    truth_source: str = "1c"


# Офлайн-хранилище связок (тесты/локаль без БД); на стенде эндпоинты получают
# DbLinksStore через зависимость get_links_store (см. link_store.py).
_memory_links_store = InMemoryLinksStore()


def get_memory_links_store() -> InMemoryLinksStore:
    """Офлайн-хранилище связок (общий экземпляр для тестов и локали без БД)."""
    return _memory_links_store


def link_key(enterprise: str, base_code: str, tab_num: str) -> str:
    """Составной ключ связки (формат ключа карточки 1С)."""
    return "%s|%s|%s" % (enterprise, base_code, tab_num)


def _current_store() -> LinksStore:
    """Текущее хранилище связок: уважает dependency_overrides тестов
    (InMemoryLinksStore), иначе — боевой синглтон Postgres."""
    from .main import app  # локально против циклического импорта

    override = app.dependency_overrides.get(get_links_store)
    if override is not None:
        return override()
    return get_links_store(get_settings())


def find_link(enterprise: str, base_code: str, tab_num: str) -> LinkRecord | None:
    """Найти связку по составному ключу (только чтение хранилища)."""
    return _current_store().find(link_key(enterprise, base_code, tab_num))


def find_links_by_sam(sam: str) -> list[LinkRecord]:
    """Все связки логина (для добора поиска по логину)."""
    return _current_store().find_by_sam(sam)


def clear_for_tests() -> None:
    """Сброс офлайн-хранилища. Только для изоляции pytest; в прод-коде не вызывать."""
    _memory_links_store.reset()


def _norm(text: str) -> str:
    """Нормализация ФИО для сравнения (регистр/пробелы не различаются)."""
    return " ".join(text.strip().lower().split())


@router.post("/link_1c_ad", status_code=status.HTTP_201_CREATED)
def create_link(
    body: LinkCreate,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    client: OneCClient = Depends(get_onec_client),
    reader: AdReader | None = Depends(get_ad_reader),
    store: LinksStore = Depends(get_links_store),
) -> dict:
    """Создать/подтвердить связку вручную. Только админ (ОК/владельцам — 403)."""
    settings.ensure_read_only()
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Связку 1С-AD привязывает только админ",
        )
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ридер AD не настроен (в offline — подмена фейковым шлюзом)",
        )
    try:
        card = client.get_employee(body.base_code, body.tab_num, body.enterprise)
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
    if card.enterprise != body.enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Карточка не принадлежит предприятию",
        )
    try:
        ad_user = reader.get_user(body.sam.strip())
    except AdNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except AdUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    # Дубли ФИО в предприятии — в ручную сверку (автосклейки нет).
    try:
        same_name = search_enterprise(body.enterprise, card.fio, client)
        duplicate = (
            sum(1 for c in same_name.cards if _norm(c.fio) == _norm(card.fio)) > 1
        )
    except UnknownEnterpriseError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except Exception:
        duplicate = False
    # Сравнение ФИО: расхождение фиксируем, истина — значения 1С.
    compared = build_snapshot(
        {"fio": card.fio, "department": card.dept, "title": card.position},
        ad_user,
    )
    diverged = compared.divergences() != [] or _norm(card.fio) != _norm(
        ad_user.display_name
    )
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    record = LinkRecord(
        enterprise=body.enterprise,
        base_code=body.base_code,
        tab_num=body.tab_num,
        key=link_key(body.enterprise, body.base_code, body.tab_num),
        sam=ad_user.sam,
        by=user.sam,
        at=now,
        verified=True,
        diverged=bool(diverged),
        needs_manual_review=bool(duplicate or diverged),
    )
    try:
        # Зеркала ссылок связки в НАШИХ таблицах (one_c_bases/employee_base_map/
        # users) — нужны для внешних ключей link_1c_ad. В AD/1С не пишем.
        try:
            base_cfg = client.base_config(body.base_code)
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
        store.save(record)
    except LinksUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="link.create",
            entity="link_1c_ad",
            entity_id=record.key,
        )
    )
    return {
        "link": record.model_dump(),
        "truth_source": "1c",
        "divergences": compared.divergences(),
        "snapshot_1c": make_snapshot_1c(card),
        "snapshot_ad": {
            "sam": ad_user.sam,
            "display_name": ad_user.display_name,
            "department": ad_user.department,
            "title": ad_user.title,
            "manager_dn": ad_user.manager_dn,
            "mail": ad_user.mail,
        },
    }


@router.post("/link_1c_ad/sync")
def sync_links_endpoint(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    client: OneCClient = Depends(get_onec_client),
    reader: AdReader | None = Depends(get_ad_reader),
    store: LinksStore = Depends(get_links_store),
    settings_store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Автосвязка 1С↔AD по точному ФИО «по требованию»: только admin.

    Ридер AD не настроен — 503; предприятий нет — 409; падение хранилища
    связок/настроек — 503 (не 500). Запись — только связки в нашу БД,
    1С/AD — только чтение. Ответ — итог прохода (диагностика)."""
    settings.ensure_read_only()
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Автосвязку 1С-AD запускает только админ",
        )
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ридер AD не настроен (в offline — подмена фейковым шлюзом)",
        )
    try:
        raw = read_setting_value(settings_store, "enterprises")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    enterprises = [
        str(item["code"])
        for item in raw
        if isinstance(item, dict) and item.get("code")
    ] if isinstance(raw, list) else []
    if not enterprises:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Предприятия не настроены: выполните синхронизацию из 1С",
        )
    # Уволенных не сопоставляем: связь и маршрут им не нужны, а в расхождениях
    # они мешают разбору. Даты — из локального справочника (регистр 1С).
    dismissed = _dismissed_keys()
    try:
        result = run_ad_sync(client, reader, store, enterprises, dismissed=dismissed)
    except (LinksUnavailable, AdSyncUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    try:
        settings_store.set(
            "ad_links_synced_at",
            json.dumps(_dt.datetime.now(_dt.timezone.utc).isoformat()),
        )
    except SettingsUnavailable:
        pass  # метка не критична: сам проход выполнен
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="link.sync",
            entity="link_1c_ad",
            entity_id="ad_sync",
            detail="created=%d scanned=%d" % (result.created, result.scanned),
        )
    )
    return {
        "synced": True,
        "scanned": result.scanned,
        "created": result.created,
        "skipped_linked": result.skipped_linked,
        "skipped_1c_duplicates": result.skipped_1c_duplicates,
        "skipped_ad_no_match": result.skipped_ad_no_match,
        "skipped_ad_duplicates": result.skipped_ad_duplicates,
        "skipped_dismissed": result.skipped_dismissed,
        "errors": result.errors,
        # Выдача расхождений (миграция 0010): счётчики по причинам + сколько
        # строк записано. Подробности — GET /link_1c_ad/discrepancies.
        "discrepancies_saved": result.discrepancies_saved,
        "discrepancies": result.counts_by_reason(),
    }


class DiscrepancyOut(BaseModel):
    """Строка расхождения автосопоставления 1С↔AD (выдача админу).

    reason: one_c_duplicate (в 1С несколько карточек ФИО), ad_duplicate (в AD
    несколько записей ФИО), not_in_ad (в AD нет такого ФИО). can_confirm —
    можно ли подтвердить массово: у строки есть ровно один кандидат AD (sam),
    выбранный человеком по должности/службе. recommended — должность/служба
    карточки 1С совпадает с кандидатом AD (связка выглядит верной)."""

    key: str
    enterprise: str
    base_code: str
    tab_num: str
    fio: str
    reason: str
    ad_sam: str | None = None
    ad_fio: str | None = None
    ad_dept: str | None = None
    ad_title: str | None = None
    one_c_dept: str | None = None
    one_c_position: str | None = None
    recommended: bool = False
    can_confirm: bool = False
    candidates: list[str] = Field(default_factory=list)
    sibling_tabs: list[str] = Field(default_factory=list)
    detected_at: str | None = None
    resolved_at: str | None = None


def _discrepancy_out(row: dict) -> DiscrepancyOut:
    """Строка БД -> контракт выдачи; can_confirm — кандидат AD ровно один."""
    detail = row.get("detail") or {}
    sam = row.get("ad_sam")
    return DiscrepancyOut(
        key=row["key"],
        enterprise=row["enterprise"],
        base_code=row["base_code"],
        tab_num=row["tab_num"],
        fio=row["fio"],
        reason=row["reason"],
        ad_sam=sam,
        ad_fio=row.get("ad_fio"),
        ad_dept=row.get("ad_dept"),
        ad_title=row.get("ad_title"),
        one_c_dept=row.get("one_c_dept"),
        one_c_position=row.get("one_c_position"),
        recommended=bool(row.get("recommended")),
        # Кандидат один и известен — можно подтвердить пакетно; при нескольких
        # записях AD (ad_duplicate) sam не проставлен, выбор за человеком.
        can_confirm=bool(sam) and row["reason"] != "ad_duplicate",
        candidates=list(detail.get("candidates") or []),
        sibling_tabs=list(detail.get("sibling_tabs") or []),
        detected_at=row.get("detected_at"),
        resolved_at=row.get("resolved_at"),
    )


def _mark_directory_linked(row: dict, sam: str) -> None:
    """Проставить ad_sam строке локального справочника после подтверждения.

    Только наша БД; ошибка не должна срывать пакетное подтверждение."""
    try:
        from .main import app  # локально против циклического импорта

        override = app.dependency_overrides.get(get_employee_sync_store)
        store = (
            override() if override is not None
            else get_employee_sync_store(get_settings())
        )
        store.mark_linked(
            str(row.get("enterprise") or ""),
            str(row.get("tab_num") or ""),
            sam,
            str(row.get("base_code") or ""),
        )
    except Exception:
        pass


@router.get("/link_1c_ad/discrepancies")
def list_link_discrepancies(
    reason: str = Query(default="", max_length=40, description="Фильтр по причине"),
    q: str = Query(default="", max_length=200, description="Подстрока ФИО/таб.№/логина"),
    only_open: bool = Query(default=True, description="Только не подтверждённые"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: LinksStore = Depends(get_links_store),
) -> dict:
    """Расхождения автоматического сопоставления 1С↔AD: admin/sed_admin.

    Наполняет проход автосвязки (POST /link_1c_ad/sync или регламент по
    schedule_ad_links_sync). Здесь только чтение нашей таблицы; AD/1С не трогаем.
    Ответ: items + total + counts (счётчики по причинам) + page/page_size."""
    settings.ensure_read_only()
    if user.role not in ("admin", "sed_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Расхождения сопоставления доступны только администратору",
        )
    try:
        rows = store.list_discrepancies(
            reason=reason or None,
            query=q,
            only_open=only_open,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = store.count_discrepancies(
            reason=reason or None, query=q, only_open=only_open
        )
        counts = store.counts_by_reason()
    except LinksUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return {
        "items": [_discrepancy_out(row).model_dump() for row in rows],
        "counts": counts,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


class ConfirmDiscrepanciesIn(BaseModel):
    """Массовое подтверждение: список ключей карточек (из выдачи расхождений)."""

    keys: list[str] = Field(min_length=1, max_length=500)


@router.post("/link_1c_ad/discrepancies/confirm")
def confirm_link_discrepancies(
    body: ConfirmDiscrepanciesIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: LinksStore = Depends(get_links_store),
) -> dict:
    """Подтвердить расхождения пачкой: создать проверенные связки 1С↔AD.

    Связывается только то, где кандидат AD ровно один (у строки проставлен sam);
    при нескольких записях AD или отсутствии кандидата строка пропускается и
    возвращается в skipped с причиной — выбор делает человек. Запись — только
    связки в нашу БД (users/employee_base_map заполняются), AD/1С не пишутся.
    Ответ: {linked, skipped, errors, linked_sams}."""
    settings.ensure_read_only()
    if user.role not in ("admin", "sed_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Подтверждать сопоставление может только администратор",
        )
    try:
        rows = store.get_discrepancies(body.keys)
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        linked = 0
        skipped: list[str] = []
        errors: list[str] = []
        linked_sams: list[str] = []
        for row in rows:
            sam = (row.get("ad_sam") or "").strip()
            if not sam or row.get("reason") == "ad_duplicate":
                skipped.append(row["key"])
                continue
            try:
                # Зеркала ссылок — те же, что у прохода автосвязки: без них
                # внешние ключи link_1c_ad не создадутся.
                store.ensure_targets(
                    None,
                    {
                        "enterprise": row["enterprise"],
                        "base_code": row["base_code"],
                        "tab_num": row["tab_num"],
                        "fio": row["fio"],
                        "dept_1c": row.get("one_c_dept"),
                        "position_1c": row.get("one_c_position"),
                    },
                    {
                        "sam": sam,
                        "fio_full": row.get("ad_fio") or row["fio"],
                        "dept_ad": row.get("ad_dept"),
                        "title_ad": row.get("ad_title"),
                        "manager_dn": None,
                        "mail": None,
                    },
                )
                store.save(
                    LinkRecord(
                        enterprise=row["enterprise"],
                        base_code=row["base_code"],
                        tab_num=row["tab_num"],
                        key=row["key"],
                        sam=sam,
                        by=user.sam,
                        at=now,
                        verified=True,
                        diverged=False,
                        needs_manual_review=False,
                        truth_source="1c",
                    )
                )
            except Exception as exc:  # одна строка не должна валить пакет
                errors.append("%s: %s" % (row["key"], exc))
                continue
            # Строку справочника помечаем связанной сразу, иначе создание заявки
            # до следующего планового синка не увидит логин (как при подтверждении
            # в форме). Ошибка здесь не критична — синк справочника поправит.
            _mark_directory_linked(row, sam)
            linked += 1
            linked_sams.append(sam)
        if linked:
            store.resolve_discrepancies([row["key"] for row in rows if row.get("ad_sam")])
    except LinksUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="link.discrepancies_confirm",
            entity="link_1c_ad",
            entity_id="discrepancies",
            detail="linked=%d skipped=%d errors=%d" % (linked, len(skipped), len(errors)),
        )
    )
    return {
        "linked": linked,
        "skipped": skipped,
        "errors": errors,
        "linked_sams": linked_sams[:50],
    }


def _dismissed_keys() -> set:
    """Ключи уволенных карточек из локального справочника (best effort).

    Подмены границы (dependency_overrides) уважаем — иначе офлайн-прогоны пошли
    бы в боевой Postgres. Справочник недоступен — пустое множество: проход
    сопоставит всех, как раньше (лучше лишние расхождения, чем молча пропущенные
    связи)."""
    from .main import app  # локально против циклического импорта

    try:
        override = app.dependency_overrides.get(get_employee_sync_store)
        store = (
            override() if override is not None
            else get_employee_sync_store(get_settings())
        )
        return set(store.dismissed_keys())
    except Exception:
        return set()


class MyLinkOut(BaseModel):
    """Связка текущего пользователя для ссылки инициатора на свою карточку.

    Только свой ключ (табельный — свои ПДн, как ФИО в /auth/me); чужое
    подставить нельзя — sam берётся из сессии, не из параметров.
    is_current — работает ли сотрудник сейчас (нет dismissal_date в 1С):
    правило задачи K для дублей (несколько мест работы).
    """

    enterprise: str
    base_code: str
    tab_num: str
    key: str
    verified: bool = False
    is_current: bool | None = Field(
        default=None,
        description="Работает сейчас (True), уволен (False), неизвестно (None)",
    )


def _resolve_onec_client(settings, settings_store):
    """Клиент 1С для проверки текущей работы (is_current): подмена из
    dependency_overrides (тесты), иначе боевой; не настроен — None (fail-soft,
    is_current у всех связок будет None)."""
    from .employees import get_onec_client
    from .main import app  # локально против циклического импорта

    override = app.dependency_overrides.get(get_onec_client)
    if override is not None:
        try:
            return override()
        except Exception:
            return None
    try:
        return get_onec_client(settings, settings_store)
    except Exception:
        return None


def _is_currently_employed(client, enterprise: str, base_code: str, tab_num: str) -> bool | None:
    """Работает ли сотрудник сейчас: карточка 1С без dismissal_date — True,
    с датой увольнения — False. Карточки нет/1С недоступна/чужое предприятие —
    None (неизвестно, fail-soft: фронт откатится на старое правило)."""
    if client is None:
        return None
    try:
        card = client.get_employee(base_code, tab_num, enterprise)
    except Exception:
        return None
    if card.enterprise != enterprise:
        return None
    return not (card.dismissal_date or "").strip()


@router.get("/link_1c_ad/mine")
def read_my_links(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: LinksStore = Depends(get_links_store),
    settings_store: DbSettingsStore | None = Depends(get_settings_store),
) -> dict:
    """Связки текущего пользователя (инициатор → ссылка на свою карточку).

    Пусто — связки нет, фронт показывает инициатора текстом; несколько —
    неоднозначность (молчаливый выбор предприятия недопустим), фронт тоже
    показывает текст. Высокочастотное UI-чтение своих данных — аудит не пишем.
    """
    settings.ensure_read_only()
    try:
        records = store.find_by_sam(user.sam)
    except LinksUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    onec = _resolve_onec_client(settings, settings_store)
    return {
        "items": [
            MyLinkOut(
                enterprise=r.enterprise,
                base_code=r.base_code,
                tab_num=r.tab_num,
                key=r.key,
                verified=r.verified,
                is_current=_is_currently_employed(
                    onec, r.enterprise, r.base_code, r.tab_num
                ),
            ).model_dump()
            for r in records
        ]
    }


@router.get("/link_1c_ad")
def read_link(
    enterprise: str = Query(..., min_length=1),
    base_code: str = Query(..., min_length=1),
    tab_num: str = Query(..., min_length=1),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: LinksStore = Depends(get_links_store),
) -> dict:
    """Прочитать факт связки (без ПДн: только ключи, логин и флаги сверки)."""
    settings.ensure_read_only()
    try:
        stored = store.find(link_key(enterprise, base_code, tab_num))
    except LinksUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Связка не найдена"
        )
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="link.read",
            entity="link_1c_ad",
            entity_id=stored.key,
        )
    )
    return {"link": stored.model_dump(), "truth_source": "1c"}

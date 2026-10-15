# Справочник заместителей (миграция 0023) в двух контурах.
# Админский (решение человека 2026-10-14): GET/POST/PUT/DELETE
# /settings/substitutes. Записи заводятся администратором; право заместителя
# отметить шаг за оригинала резолвится в requests.py по этому справочнику,
# поэтому контракт записи отдаётся админке целиком (логины, срок, флаг писем).
# Гейт админских ручек: settings.ensure_read_only() (запись в AD в MVP
# запрещена) + require_block(BLOCK_SETTINGS_SUBSTITUTES) — справочник админская
# функция (сид блока в 0023).
#
# Личный кабинет заместителя (решение человека 2026-10-15): заместитель
# назначает себе САМ сотрудник — /substitutes/my (GET/POST/PUT/DELETE) и выбирает
# его через /substitutes/candidates. Блока прав на личном контуре нет (пять
# ручек): это личные записи человека, а не настройка. Гейт здесь один —
# принадлежность записи (principal_sam == логин сессии); чужая запись отвечает
# 404 наравне с отсутствующей (не 403 и не 409), иначе по коду ответа видно,
# что запись с таким id существует. Логин оригинала в теле ручек /my не
# принимается (422): он всегда берётся из сессии, иначе сотрудник назначил бы
# заместителя другому человеку.
#
# Подбор заместителя (решение человека 2026-10-15): /api/ad/search закрыт блоком
# employee_directory, поэтому выбрать заместителя было нечем — ручка отдаёт логин
# и ФИО из ЛОКАЛЬНОГО справочника сотрудников (employees, только чтение; AD и 1С
# не опрашиваются). Это новая видимость ФИО сотрудников для всех вошедших —
# решение человека. Аудита подбора нет намеренно: выдача содержит ФИО, а в
# журнале важен сам факт назначения — его и пишут substitutes.create/update/
# delete (без логинов и ФИО).
#
# Разбор ошибок: дубль пары (UNIQUE principal_sam/deputy_sam) — 409, это
# конфликт состояния; ошибки данных в теле запроса (один и тот же сотрудник,
# пустые логины, срок, заканчивающийся до начала) — 422; неизвестный id —
# 404; падение БД — 503 (как в соседних модулях).
#
# Сверка заместителя со справочником (личные ручки /my, ревью 2026-10-15): в
# личном контуре логин заместителя сверяется с employees — он обязан быть
# сотрудником со связанной учёткой, иначе резолвер замещения его не найдёт
# (422; справочник недоступен — 503, запись вслепую не создаётся). Админские
# ручки /settings/substitutes сверку НЕ делают: администратору доступен и
# заведённый вручную логин, и другое подразделение.
#
# Аудит (audit_log, entity = substitute): в details только id (entity_id),
# срок и признак писем — логины и ФИО сотрудников туда НЕ пишем (ПДн).
# Само замещение (отметка за оригинала) аудитируется в requests.py.

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user
from .employee_sync import (
    EmployeeSyncStore,
    EmployeeSyncUnavailable,
    get_employee_sync_store,
)
from .employees import DIRECTORY_PAGE_SIZE
from .permissions import BLOCK_SETTINGS_SUBSTITUTES, require_block
from .substitute_store import (
    Substitute,
    SubstituteNotFound,
    SubstitutePairTaken,
    SubstituteStore,
    SubstitutesUnavailable,
    get_substitute_store,
)

router = APIRouter(tags=["заместители"])

# Тексты ошибок данных записи и «записи нет» — общие для админского и личного
# контура: клиент получает один и тот же текст в обоих случаях.
_ERR_EMPTY_DEPUTY = "Логин заместителя не может быть пустым"
_ERR_SAME_EMPLOYEE = "Заместитель не может быть тем же сотрудником"
_ERR_NOT_FOUND = "Запись справочника заместителей не найдена"
# Префиксы пояснений аудита (срок и признак писем дописывает _period_detail).
_AUDIT_CREATED = "запись создана"
_AUDIT_UPDATED = "запись изменена"
_AUDIT_DELETED = "запись удалена"


def _check_period(valid_from: date, valid_to: date | None) -> None:
    """Срок записи: окончание раньше начала — ошибка данных (422).

    Проверка одна на обе схемы входа (админскую и личную): правило срока общее,
    поэтому POST и PUT отвечают одинаково."""
    if valid_to is not None and valid_to < valid_from:
        raise ValueError(
            "Окончание срока (%s) раньше начала (%s)" % (valid_to, valid_from)
        )


class SubstituteIn(BaseModel):
    """Запись справочника заместителей на вход (POST и PUT — полный набор полей).

    Логины — sAMAccountName, тот же ключ, что в employees/ad_sam: ФИО меняется,
    логин нет. valid_from обязателен, valid_to = None — «действует, пока не
    отменят»."""

    principal_sam: str = Field(description="Логин AD, которому замещают")
    deputy_sam: str = Field(description="Логин AD заместителя")
    valid_from: date = Field(description="Действует с даты")
    valid_to: date | None = Field(
        default=None, description="Действует по дату (None — пока не отменят)"
    )
    notify: bool = Field(
        default=True,
        description="Слать заместителю копию письма «назначена» по шагам оригинала",
    )
    note: str = Field(default="", description="Пояснение администратора (например, отпуск)")

    @model_validator(mode="after")
    def _check_pair(self) -> "SubstituteIn":
        """Проверки данных записи (422): логины непустые и разные, срок не
        вывернут. Это ошибка запроса, а не хранилища, поэтому она одна и та же
        для POST и PUT."""
        self.principal_sam = self.principal_sam.strip()
        self.deputy_sam = self.deputy_sam.strip()
        self.note = self.note.strip()
        if not self.principal_sam:
            raise ValueError("Логин сотрудника, которому замещают, не может быть пустым")
        if not self.deputy_sam:
            raise ValueError(_ERR_EMPTY_DEPUTY)
        if self.principal_sam.casefold() == self.deputy_sam.casefold():
            raise ValueError(_ERR_SAME_EMPLOYEE)
        _check_period(self.valid_from, self.valid_to)
        return self


class SubstituteOut(BaseModel):
    """Запись справочника наружу: даты и метки времени — ISO-строки JSON."""

    id: int
    principal_sam: str
    deputy_sam: str
    valid_from: date
    valid_to: date | None = None
    notify: bool
    note: str = ""
    created_by: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _to_out(record: Substitute) -> SubstituteOut:
    """Запись хранилища -> схема ответа (состав полей один в один)."""
    return SubstituteOut(
        id=record.id,
        principal_sam=record.principal_sam,
        deputy_sam=record.deputy_sam,
        valid_from=record.valid_from,
        valid_to=record.valid_to,
        notify=record.notify,
        note=record.note,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _require_substitutes(user: CurrentUser) -> None:
    """Справочник заместителей — блок settings_substitutes (админ, администратор
    СЭД; сид блока и выдача шаблонам — миграция 0023)."""
    require_block(
        user,
        BLOCK_SETTINGS_SUBSTITUTES,
        "Справочник заместителей доступен только администраторам",
    )


def _period_detail(record: Substitute, prefix: str) -> str:
    """Пояснение для аудита БЕЗ ПДн: только срок и признак писем.

    Логины и ФИО сотрудников в details не пишем — по ним и так видно, кому
    запись принадлежит (entity_id), а сам состав пары — ПДн."""
    until = record.valid_to.isoformat() if record.valid_to else "без срока"
    return "%s: %s—%s; письма заместителю: %s" % (
        prefix,
        record.valid_from.isoformat(),
        until,
        "да" if record.notify else "нет",
    )


def _audit_record(action: str, sub_id: int, detail: str, actor: str) -> None:
    """Событие аудита по записи справочника (entity = substitute, entity_id = id).

    Общий помощник админского и личного контуров: набор событий у них один и тот
    же (substitutes.create/update/delete), различается только actor."""
    audit_log.append(
        AuditEvent(
            actor=actor,
            action=action,
            entity="substitute",
            entity_id=str(sub_id),
            detail=detail,
        )
    )


@router.get("/settings/substitutes", response_model=list[SubstituteOut])
def list_substitutes(
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
) -> list[SubstituteOut]:
    """Весь справочник заместителей (блок settings_substitutes, иначе 403):
    включая записи с истёкшим сроком — админка их правит и удаляет.

    Хранилище недоступно — 503."""
    settings.ensure_read_only()
    _require_substitutes(user)
    try:
        records = store.list_all()
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return [_to_out(record) for record in records]


@router.post("/settings/substitutes", response_model=SubstituteOut, status_code=status.HTTP_201_CREATED)
def create_substitute(
    payload: SubstituteIn,
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
) -> SubstituteOut:
    """Создать запись справочника (блок settings_substitutes, иначе 403).

    422 — ошибки данных записи (логины, один и тот же сотрудник, срок);
    409 — такая пара уже есть (в справочнике одна запись на пару); 503 —
    хранилище недоступно. Аудит — substitutes.create (без логинов и ФИО)."""
    settings.ensure_read_only()
    _require_substitutes(user)
    try:
        record = store.create(
            principal_sam=payload.principal_sam,
            deputy_sam=payload.deputy_sam,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            notify=payload.notify,
            note=payload.note,
            actor=user.sam,
        )
    except SubstitutePairTaken as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit_record("substitutes.create", record.id, _period_detail(record, _AUDIT_CREATED), user.sam)
    return _to_out(record)


@router.put("/settings/substitutes/{sub_id}", response_model=SubstituteOut)
def update_substitute(
    sub_id: int,
    payload: SubstituteIn,
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
) -> SubstituteOut:
    """Изменить запись справочника целиком (блок settings_substitutes, иначе 403).

    422 — ошибки данных записи; 409 — пара занята другой записью; 404 — записи
    с таким id нет; 503 — хранилище недоступно. Аудит — substitutes.update."""
    settings.ensure_read_only()
    _require_substitutes(user)
    try:
        record = store.update(
            sub_id,
            principal_sam=payload.principal_sam,
            deputy_sam=payload.deputy_sam,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            notify=payload.notify,
            note=payload.note,
        )
    except SubstitutePairTaken as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except SubstituteNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit_record("substitutes.update", record.id, _period_detail(record, _AUDIT_UPDATED), user.sam)
    return _to_out(record)


@router.delete("/settings/substitutes/{sub_id}")
def delete_substitute(
    sub_id: int,
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Удалить запись справочника (блок settings_substitutes, иначе 403);
    не найдена — 404, хранилище недоступно — 503.

    Шаги согласования при этом не меняются: запись лишь снимала право
    заместителю отметить шаг за оригинала. Аудит — substitutes.delete."""
    settings.ensure_read_only()
    _require_substitutes(user)
    try:
        store.delete(sub_id)
    except SubstituteNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit_record("substitutes.delete", sub_id, _AUDIT_DELETED, user.sam)
    return {"id": sub_id, "deleted": True}


# ---------------------------------------------------------------------------
# Подбор заместителя: /substitutes/candidates — логин и ФИО из локального
# справочника сотрудников (любой вошедший, без блока прав)
# ---------------------------------------------------------------------------


# Минимум подстроки подбора: короче — совпадений слишком много, сотруднику
# такой список не помогает. Три символа (а не два) — на employees (23+ тысячи
# строк) двух букв дают сотни совпадений почти у любой фамилии, а ручка
# открыта каждому вошедшему: короткий запрос ещё и гоняет ILIKE по всей
# таблице (индексы pg_trgm, миграция 0024, всё равно дают выборку страницы).
_CANDIDATES_MIN_QUERY = 3
_ERR_QUERY_SHORT = "Введите не менее 3 символов ФИО или логина заместителя"
_CANDIDATES_PAGE_MAX = 100


def _candidate_item(row: dict) -> dict:
    """Строка локального справочника -> элемент подбора (sam = ad_sam)."""
    return {
        "sam": row["ad_sam"],
        "fio": row["fio"],
        "department": row.get("department") or None,
        "position": row.get("position") or None,
    }


@router.get("/substitutes/candidates")
def list_substitute_candidates(
    q: str = Query(default="", max_length=200, description="Подстрока ФИО или логина"),
    page: int = Query(default=1, ge=1, description="Номер страницы (1-based)"),
    page_size: int = Query(
        default=DIRECTORY_PAGE_SIZE,
        ge=1,
        le=_CANDIDATES_PAGE_MAX,
        description="Размер страницы",
    ),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    emp_store: EmployeeSyncStore = Depends(get_employee_sync_store),
) -> dict:
    """Кандидаты-заместители из локального справочника: любой вошедший, без
    блока прав (решение человека 2026-10-15).

    Источник — таблица employees (только чтение; AD и 1С не опрашиваются),
    отдаются только строки со связанной учёткой AD: заместителем может быть
    лишь тот, у кого есть логин. Текущий пользователь из выдачи исключён —
    себя заместителем не назначают (сервер всё равно отверг бы это 422), и
    показывать себя в выборе не надо.

    Ошибки: 401 — нет сессии; 422 — подстрока короче 3 символов; 503 — хранилище
    сотрудников недоступно. Аудита нет (см. шапку модуля)."""
    settings.ensure_read_only()
    needle = (q or "").strip()
    if len(needle) < _CANDIDATES_MIN_QUERY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_ERR_QUERY_SHORT,
        )
    try:
        rows, total = emp_store.search_linked(
            needle, page_size, (page - 1) * page_size
        )
    except EmployeeSyncUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:  # прочие сбои хранилища — тоже 503, а не 500
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Справочник сотрудников недоступен: %s" % type(exc).__name__,
        ) from exc
    mine = user.sam.strip().casefold()
    return {
        # total — по фильтру хранилища, а items без текущего пользователя:
        # на странице, где он попался, элементов на один меньше.
        "items": [
            _candidate_item(row)
            for row in rows
            if (row.get("ad_sam") or "").strip().casefold() != mine
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# Личный кабинет заместителя: /substitutes/my — свои записи, без блока прав
# ---------------------------------------------------------------------------


class MySubstituteIn(BaseModel):
    """Тело личных ручек /substitutes/my: тот же набор полей, что SubstituteIn,
    минус principal_sam (логин оригинала всегда берётся из сессии).

    valid_to = None — «действует, пока не отменят»."""

    deputy_sam: str = Field(description="Логин AD заместителя")
    valid_from: date = Field(description="Действует с даты")
    valid_to: date | None = Field(
        default=None, description="Действует по дату (None — пока не отменят)"
    )
    notify: bool = Field(
        default=True,
        description="Слать заместителю копию письма «назначена» по шагам оригинала",
    )
    note: str = Field(default="", description="Пояснение сотрудника (например, отпуск)")

    @model_validator(mode="before")
    @classmethod
    def _reject_principal(cls, data: Any) -> Any:
        """Логин оригинала в личной ручке не принимается (422): он всегда берётся
        из сессии, иначе сотрудник назначил бы заместителя другому человеку."""
        if isinstance(data, dict) and data.get("principal_sam") is not None:
            raise ValueError(
                "Логин сотрудника, которому замещают, здесь указывать нельзя: "
                "заместитель назначается себе самому"
            )
        return data

    @model_validator(mode="after")
    def _check_pair(self) -> "MySubstituteIn":
        """Проверки данных записи (422): логин заместителя непустой, срок не
        вывернут — те же правила, что у админской схемы.

        «Заместитель = сам пользователь» здесь не проверяется: логин оригинала в
        этой схеме неизвестен, его проверяет ручка (_reject_self_deputy)."""
        self.deputy_sam = self.deputy_sam.strip()
        self.note = self.note.strip()
        if not self.deputy_sam:
            raise ValueError(_ERR_EMPTY_DEPUTY)
        _check_period(self.valid_from, self.valid_to)
        return self


def _my_sam(user: CurrentUser) -> str:
    """Логин сессии как principal_sam личной записи (пробелы срезаны — резолвер
    замещения в requests.py сравнивает логины тоже без них)."""
    return user.sam.strip()


def _is_my_record(record: Substitute, sam: str) -> bool:
    """Запись личная: principal_sam совпадает с логином сессии (без учёта
    регистра — как в резолвере замещения requests.py)."""
    return record.principal_sam.strip().casefold() == sam.casefold()


def _reject_self_deputy(deputy_sam: str, sam: str) -> None:
    """Заместитель = сам пользователь — 422 (текст тот же, что у админской
    ручки). Проверяется до похода в хранилище: ошибка данных запроса."""
    if deputy_sam.casefold() == sam.casefold():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_ERR_SAME_EMPLOYEE,
        )


# Сверка заместителя личной записи со справочником сотрудников: логин ищем тем же
# поиском подбора (ILIKE по подстроке логина/ФИО), а решение принимаем по ТОЧНОМУ
# совпадению ad_sam без регистра — подстрока могла найти другого сотрудника.
_ERR_DEPUTY_UNKNOWN = (
    "Сотрудник с логином %s не найден в справочнике — выберите заместителя из подсказок"
)
_DEPUTY_LOOKUP_PAGE = DIRECTORY_PAGE_SIZE


def _require_known_deputy(emp_store: EmployeeSyncStore, deputy_sam: str) -> None:
    """Заместитель личной записи обязан быть сотрудником со связанной учёткой.

    Логин из подсказок подбора (/substitutes/candidates отдаёт только такие
    строки) проходит всегда. Логин, введённый руками, а также уволенный или не
    связанный с AD — 422 с понятным текстом: резолвер замещения потом такого
    заместителя не найдёт, и сотрудник узнал бы об этом только на согласовании.
    Админские ручки /settings/substitutes эту сверку НЕ делают: администратору
    доступен и заведённый вручную логин, и запись для другого подразделения.

    Справочник недоступен — 503 (fail-closed: запись вслепую не создаём)."""
    wanted = deputy_sam.strip().casefold()
    offset = 0
    try:
        while True:
            rows, total = emp_store.search_linked(
                deputy_sam, _DEPUTY_LOOKUP_PAGE, offset
            )
            for row in rows:
                if (row.get("ad_sam") or "").strip().casefold() == wanted:
                    return
            offset += _DEPUTY_LOOKUP_PAGE
            if not rows or offset >= total:
                break
    except EmployeeSyncUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:  # прочие сбои хранилища — тоже 503, а не 500
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Справочник сотрудников недоступен: %s" % type(exc).__name__,
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=_ERR_DEPUTY_UNKNOWN % deputy_sam.strip(),
    )


def _own_record(store: SubstituteStore, sub_id: int, sam: str) -> Substitute:
    """Запись для правки личной ручки — только своя; хранилище недоступно — 503.

    Чужая запись отвечает 404 наравне с отсутствующей: 403 или 409 выдали бы по
    коду ответа, что запись с таким id существует. Проверка ДО любых изменений
    (вызывающий идёт в store.update/delete только после неё)."""
    try:
        record = store.by_id(sub_id)
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if record is None or not _is_my_record(record, sam):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ERR_NOT_FOUND
        )
    return record


@router.get("/substitutes/my", response_model=list[SubstituteOut])
def list_my_substitutes(
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
) -> list[SubstituteOut]:
    """Свои записи заместителей — доступно любому вошедшему, без блока прав.

    Включая записи с истёкшим сроком: их надо видеть и удалять. Отбор по
    principal_sam делает хранилище (в личном контуре отдаём только свои записи),
    порядок — тот же, что в справочнике. Хранилище недоступно — 503."""
    settings.ensure_read_only()
    sam = _my_sam(user)
    try:
        records = store.list_by_principal(sam)
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return [_to_out(record) for record in records]


@router.post("/substitutes/my", response_model=SubstituteOut, status_code=status.HTTP_201_CREATED)
def create_my_substitute(
    payload: MySubstituteIn,
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
    emp_store: EmployeeSyncStore = Depends(get_employee_sync_store),
) -> SubstituteOut:
    """Назначить заместителя себе (любой вошедший, без блока прав): 201.

    principal_sam — всегда логин сессии, из тела он не принимается (422).
    422 — заместитель = сам пользователь, заместитель не найден в справочнике
    сотрудников со связанной учёткой (_require_known_deputy) или прочие ошибки
    данных записи; 409 — такая пара уже есть; 503 — хранилище недоступно.
    Аудит — substitutes.create (без логинов и ФИО)."""
    settings.ensure_read_only()
    sam = _my_sam(user)
    _reject_self_deputy(payload.deputy_sam, sam)
    _require_known_deputy(emp_store, payload.deputy_sam)
    try:
        record = store.create(
            principal_sam=sam,
            deputy_sam=payload.deputy_sam,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            notify=payload.notify,
            note=payload.note,
            actor=user.sam,
        )
    except SubstitutePairTaken as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit_record(
        "substitutes.create", record.id, _period_detail(record, _AUDIT_CREATED), user.sam
    )
    return _to_out(record)


@router.put("/substitutes/my/{sub_id}", response_model=SubstituteOut)
def update_my_substitute(
    sub_id: int,
    payload: MySubstituteIn,
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
    emp_store: EmployeeSyncStore = Depends(get_employee_sync_store),
) -> SubstituteOut:
    """Изменить СВОЮ запись заместителя (любой вошедший, без блока прав).

    Записи нет или она чужая — 404 (одинаково, чтобы по коду ответа не было
    видно, что запись существует); 422 — ошибки данных записи, в том числе
    заместитель вне справочника сотрудников со связанной учёткой (сверка идёт
    ПОСЛЕ гейта принадлежности, чтобы чужая запись не превращалась в 422 с
    подсказкой по справочнику); 409 — пара занята другой записью; 503 —
    хранилище недоступно. expect_principal в store.update закрывает гонку:
    принадлежность записи проверяется ещё раз в самом UPDATE. Аудит —
    substitutes.update."""
    settings.ensure_read_only()
    sam = _my_sam(user)
    _reject_self_deputy(payload.deputy_sam, sam)
    _own_record(store, sub_id, sam)  # гейт принадлежности (404) — до изменения
    _require_known_deputy(emp_store, payload.deputy_sam)
    try:
        record = store.update(
            sub_id,
            principal_sam=sam,
            deputy_sam=payload.deputy_sam,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            notify=payload.notify,
            note=payload.note,
            expect_principal=sam,
        )
    except SubstitutePairTaken as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except SubstituteNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit_record(
        "substitutes.update", record.id, _period_detail(record, _AUDIT_UPDATED), user.sam
    )
    return _to_out(record)


@router.delete("/substitutes/my/{sub_id}")
def delete_my_substitute(
    sub_id: int,
    user: CurrentUser = Depends(get_current_user),
    store: SubstituteStore = Depends(get_substitute_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Удалить СВОЮ запись заместителя (любой вошедший, без блока прав).

    Записи нет или она чужая — 404 (одинаково); хранилище недоступно — 503.
    expect_principal в store.delete закрывает гонку (принадлежность записи
    проверяется ещё раз в самом DELETE). Шаги согласования при этом не
    меняются: запись лишь снимала право заместителю отметить шаг за
    оригинала. Аудит — substitutes.delete."""
    settings.ensure_read_only()
    sam = _my_sam(user)
    record = _own_record(store, sub_id, sam)
    try:
        store.delete(sub_id, expect_principal=sam)
    except SubstituteNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except SubstitutesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit_record("substitutes.delete", record.id, _AUDIT_DELETED, user.sam)
    return {"id": sub_id, "deleted": True}

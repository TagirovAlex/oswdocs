# CRUD справочника заместителей (миграция 0023): GET/POST/PUT/DELETE
# /settings/substitutes. Записи заводятся администратором; право заместителя
# отметить шаг за оригинала резолвится в requests.py по этому справочнику,
# поэтому контракт записи отдаётся админке целиком (логины, срок, флаг писем).
#
# Гейт на всех четырёх ручках: settings.ensure_read_only() (запись в AD в MVP
# запрещена) + require_block(BLOCK_SETTINGS_SUBSTITUTES) — справочник
# админская функция (решение человека 2026-10-14, сид блока в 0023).
#
# Разбор ошибок: дубль пары (UNIQUE principal_sam/deputy_sam) — 409, это
# конфликт состояния; ошибки данных в теле запроса (один и тот же сотрудник,
# пустые логины, срок, заканчивающийся до начала) — 422; неизвестный id —
# 404; падение БД — 503 (как в соседних модулях).
#
# Аудит (audit_log, entity = substitute): в details только id (entity_id),
# срок и признак писем — логины и ФИО сотрудников туда НЕ пишем (ПДн).
# Само замещение (отметка за оригинала) аудитируется в requests.py.

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user
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
            raise ValueError("Логин заместителя не может быть пустым")
        if self.principal_sam.casefold() == self.deputy_sam.casefold():
            raise ValueError("Заместитель не может быть тем же сотрудником")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(
                "Окончание срока (%s) раньше начала (%s)" % (self.valid_to, self.valid_from)
            )
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
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="substitutes.create",
            entity="substitute",
            entity_id=str(record.id),
            detail=_period_detail(record, "запись создана"),
        )
    )
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
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="substitutes.update",
            entity="substitute",
            entity_id=str(record.id),
            detail=_period_detail(record, "запись изменена"),
        )
    )
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
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="substitutes.delete",
            entity="substitute",
            entity_id=str(sub_id),
            detail="запись удалена",
        )
    )
    return {"id": sub_id, "deleted": True}

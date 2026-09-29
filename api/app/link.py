# Ручная связка записей 1С и AD (волна B1): link_1c_ad и снапшоты.
# Стыковка — только по полному ФИО + ручное подтверждение ОК (факт вызова).
# При любом расхождении истина — 1С; оба значения видны в снапшоте.
# Дубли одного ФИО не склеиваются автоматически — помечаются на ручную сверку.
# Хранилище — в памяти процесса (offline); персистентность в таблице link_1c_ad
# подменится без смены вызовов find_link/find_links_by_sam/clear_for_tests.
# Записи в 1С/AD здесь нет: только чтение карточек и запись факта связки у нас.

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .ad_reader import AdNotFound, AdUnavailable, build_snapshot
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user, is_privileged
from .employees import get_ad_reader, get_onec_client
from .onec_client import (
    OneCBaseDown,
    OneCCircuitOpen,
    OneCClient,
    OneCError,
    OneCNotFound,
)
from .resolver import UnknownEnterpriseError, make_snapshot_1c, search_enterprise
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


# Хранилище связок в памяти (ключ — составной enterprise|base|tab).
_LINKS: dict[str, LinkRecord] = {}


def link_key(enterprise: str, base_code: str, tab_num: str) -> str:
    """Составной ключ связки (формат ключа карточки 1С)."""
    return "%s|%s|%s" % (enterprise, base_code, tab_num)


def find_link(enterprise: str, base_code: str, tab_num: str) -> LinkRecord | None:
    """Найти связку по составному ключу (только чтение хранилища)."""
    return _LINKS.get(link_key(enterprise, base_code, tab_num))


def find_links_by_sam(sam: str) -> list[LinkRecord]:
    """Все связки логина (для добора поиска по логину)."""
    wanted = sam.strip().lower()
    return [rec for rec in _LINKS.values() if rec.sam.strip().lower() == wanted]


def clear_for_tests() -> None:
    """Сброс хранилища. Только для изоляции pytest; в прод-коде не вызывать."""
    _LINKS.clear()


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
) -> dict:
    """Создать/подтвердить связку вручную. Только ОК/админы; владельцам — 403."""
    settings.ensure_read_only()
    if not is_privileged(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Связку 1С-AD подтверждает только ОК",
        )
    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ридер AD не настроен (в offline — подмена фейковым шлюзом)",
        )
    try:
        card = client.get_employee(body.base_code, body.tab_num)
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
    _LINKS[record.key] = record
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


@router.get("/link_1c_ad")
def read_link(
    enterprise: str = Query(..., min_length=1),
    base_code: str = Query(..., min_length=1),
    tab_num: str = Query(..., min_length=1),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Прочитать факт связки (без ПДн: только ключи, логин и флаги сверки)."""
    settings.ensure_read_only()
    stored = find_link(enterprise, base_code, tab_num)
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

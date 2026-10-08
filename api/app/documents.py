# Документы заявок (W3a): мета сгенерированных бегунков (таблица documents из
# 0001) + эндпоинты печати и выдачи PDF. Файлы генерирует docs.generate_bypass
# (модульная функция — тесты подменяют её, на стенде — python-docx + LibreOffice
# + qrcode). Оформление бланка собирается из данных заявки и снимка бланка
# (docs.build_blank_document, макет blank_layout); файлы-шаблоны .docx и ключ
# настроек doc_templates удалены — печати нечего читать.
# Печать — только ОК/админы; чтение мета/PDF — ОК/админы и владелец своего шага.
# ПДн владельцу не светят: отдаются только пути/версии/QR, PDF не парсится.

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user
from .docs import (
    build_bypass_context,
    generate_bypass,
    resolve_blank_layout,
)
from .requests import (
    _get_request_or_404,
    _is_step_viewer,
    _restore_step_owner_kinds,
    _RosterResolver,
    _routing_store_or_none,
    _stage_owner_kinds,
)
from .requests_store import RequestsStore, RequestsUnavailable, get_requests_store
from .settings_routes import (
    DbSettingsStore,
    SettingsUnavailable,
    get_settings_store,
    read_setting_value,
)

router = APIRouter(tags=["документы"])


class DocumentsUnavailable(Exception):
    """Хранилище документов (БД) недоступно — роутер отвечает 503, а не 500."""


class DocumentRecord(BaseModel):
    """Мета бегунка заявки (таблица documents: version, пути, QR, автор)."""

    request_id: str = Field(description="Бизнес-номер заявки REQ-XXXX")
    version: str = Field(description="Версия бегунка v1/v2/...")
    docx_path: str | None = Field(default=None, description="Путь к DOCX в volume")
    pdf_path: str | None = Field(default=None, description="Путь к PDF в volume")
    qr_payload: str | None = Field(default=None, description="URL заявки в QR")
    created_by: str = Field(default="", description="Кто инициировал печать (sam)")
    created_at: datetime | None = Field(default=None, description="Момент генерации")


class DocumentOut(BaseModel):
    """Мета бегунка наружу (версии/пути/QR; без ПДн)."""

    version: str
    docx_path: str | None = None
    pdf_path: str | None = None
    qr_payload: str | None = None
    created_at: str | None = None
    created_by: str = ""


class DocumentsStore(Protocol):
    """Интерфейс хранилища документов: единый для in-memory и Postgres."""

    def create(self, record: DocumentRecord) -> None:
        """Сохранить запись бегунка (UNIQUE(request_id, version))."""
        ...

    def list_by_request(self, request_id: str) -> list[DocumentRecord]:
        """Все версии бегунков заявки по порядку."""
        ...

    def get(self, request_id: str, version: str) -> DocumentRecord | None:
        """Конкретная версия бегунка либо None."""
        ...


def version_number(version: str) -> int:
    """'v1' -> 1 (битое значение — 0, для сортировки версий)."""
    if version.startswith("v") and version[1:].isdigit():
        return int(version[1:])
    return 0


def next_version_label(existing: list[DocumentRecord]) -> str:
    """Следующая версия бегунка: v1 — первая, v2/v3 — повторы."""
    return "v" + str(max((version_number(r.version) for r in existing), default=0) + 1)


class InMemoryDocumentsStore:
    """Офлайн-хранилище документов (dict), интерфейс DocumentsStore."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], DocumentRecord] = {}

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest."""
        self._records.clear()

    def create(self, record: DocumentRecord) -> None:
        self._records[(record.request_id, record.version)] = record

    def list_by_request(self, request_id: str) -> list[DocumentRecord]:
        records = [r for (rid, _), r in self._records.items() if rid == request_id]
        return sorted(records, key=lambda r: version_number(r.version))

    def get(self, request_id: str, version: str) -> DocumentRecord | None:
        return self._records.get((request_id, version))


class DbDocumentsStore:
    """Хранилище документов в Postgres (таблица documents из 0001).

    Заявка в модели — бизнес-номер REQ-XXXX (code), в БД FK на внутренний id
    dismissal_requests; перевод — запросом по code (как DbRequestsStore).
    Ошибки БД оборачиваются в DocumentsUnavailable (503).
    """

    _SELECT_REQUEST_ID_BY_CODE = text(
        "SELECT id FROM dismissal_requests WHERE code = :code"
    )
    _INSERT = text(
        """
        INSERT INTO documents (
          request_id, version, docx_path, pdf_path, qr_payload, created_by
        )
        VALUES (
          :request_id, :version, :docx_path, :pdf_path, :qr_payload, :created_by
        )
        """
    )
    _SELECT_BY_REQUEST = text(
        """
        SELECT version, docx_path, pdf_path, qr_payload, created_by, created_at
        FROM documents
        WHERE request_id = :request_id
        ORDER BY version
        """
    )
    _SELECT_ONE = text(
        """
        SELECT version, docx_path, pdf_path, qr_payload, created_by, created_at
        FROM documents
        WHERE request_id = :request_id AND version = :version
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def _build(self, request_id: str, row) -> DocumentRecord:
        return DocumentRecord(
            request_id=request_id,
            version=row.version,
            docx_path=row.docx_path,
            pdf_path=row.pdf_path,
            qr_payload=row.qr_payload,
            created_by=row.created_by or "",
            created_at=row.created_at,
        )

    def create(self, record: DocumentRecord) -> None:
        try:
            with self._session_factory() as session:
                internal = session.execute(
                    self._SELECT_REQUEST_ID_BY_CODE, {"code": record.request_id}
                ).first()
                if internal is None:
                    raise DocumentsUnavailable(
                        f"Заявка {record.request_id} не найдена в хранилище"
                    )
                session.execute(
                    self._INSERT,
                    {
                        "request_id": internal[0],
                        "version": record.version,
                        "docx_path": record.docx_path,
                        "pdf_path": record.pdf_path,
                        "qr_payload": record.qr_payload,
                        "created_by": record.created_by or None,
                    },
                )
                session.commit()
        except DocumentsUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise DocumentsUnavailable(
                f"Хранилище документов недоступно: {exc}"
            ) from exc

    def list_by_request(self, request_id: str) -> list[DocumentRecord]:
        try:
            with self._session_factory() as session:
                internal = session.execute(
                    self._SELECT_REQUEST_ID_BY_CODE, {"code": request_id}
                ).first()
                if internal is None:
                    return []
                rows = session.execute(
                    self._SELECT_BY_REQUEST, {"request_id": internal[0]}
                ).all()
        except SQLAlchemyError as exc:
            raise DocumentsUnavailable(
                f"Хранилище документов недоступно: {exc}"
            ) from exc
        return [self._build(request_id, row) for row in rows]

    def get(self, request_id: str, version: str) -> DocumentRecord | None:
        try:
            with self._session_factory() as session:
                internal = session.execute(
                    self._SELECT_REQUEST_ID_BY_CODE, {"code": request_id}
                ).first()
                if internal is None:
                    return None
                row = session.execute(
                    self._SELECT_ONE,
                    {"request_id": internal[0], "version": version},
                ).first()
        except SQLAlchemyError as exc:
            raise DocumentsUnavailable(
                f"Хранилище документов недоступно: {exc}"
            ) from exc
        if row is None:
            return None
        return self._build(request_id, row)


_db_documents_store: DbDocumentsStore | None = None


def get_documents_store(
    settings: Settings = Depends(get_settings),
) -> DocumentsStore:
    """Боевое хранилище документов (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется InMemoryDocumentsStore через
    dependency_overrides (как get_requests_store/get_settings_store).
    """
    global _db_documents_store
    if _db_documents_store is None:
        _db_documents_store = DbDocumentsStore(settings.DATABASE_URL)
    return _db_documents_store


def _require_hr(user: CurrentUser) -> None:
    """Печать бегунка — только разрешенной группе (ОК/руководители ОК/админы), иначе 403."""
    if user.role not in ("hr", "hr_admin", "admin"):
        raise HTTPException(
            status_code=403, detail="Печать бегунка — только разрешенной группе"
        )


def _can_view(
    request: object, user: CurrentUser, roster: _RosterResolver | None = None
) -> bool:
    """Доступ к документам: ОК/руководители ОК/админы или владелец одного из шагов заявки.

    Правило шага то же, что у карточки заявки (requests._is_step_viewer):
    группа-владелец, персональный исполнитель либо участник состава этапа
    (owner_kind = stage_roster). Резолвер состава не передан — строится здесь
    же, вместе с восстановлением owner_kind шага из справочника (в request_steps
    такой колонки нет, см. requests._restore_step_owner_kinds)."""
    if user.role in ("hr", "hr_admin", "admin"):
        return True
    if roster is None:
        routing_store = _routing_store_or_none()
        _restore_step_owner_kinds(request, _stage_owner_kinds(routing_store))
        roster = _RosterResolver(routing_store)
    return any(_is_step_viewer(step, user, roster) for step in request.steps)


def _enterprise_name(settings_store: DbSettingsStore, code: object) -> str:
    """Название предприятия из справочника settings.enterprises (для бланка).

    Нет справочника/кода в нём — сам код (бланк не должен пустовать).
    """
    try:
        raw = read_setting_value(settings_store, "enterprises")
    except SettingsUnavailable:
        return str(code or "")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and str(item.get("code") or "") == str(code or ""):
                name = str(item.get("name") or "").strip()
                if name:
                    return name
    return str(code or "")


def _fill_step_fio(context: dict, routing_store: object | None) -> None:
    """Подставить ФИО ответственных шагов (из зеркала AD users).

    На бланке колонка «Ответственный»: должность берётся из этапа, ФИО — логины
    ответственных снимка (assignees), разложенные здесь по одному на
    ответственного в assignee_names (у шага их может быть несколько);
    прежнее поле fio — ФИО первого. Пустой снимок (групповой этап) — ФИО нет:
    на бумаге группу AD пишут от руки, как в исходных бланках. Зеркало
    недоступно или логина нет в users — оставляем логин (печать не падает).
    Один логин на нескольких шагах карточку читаем один раз."""
    if routing_store is None:
        return
    cards: dict[str, dict | None] = {}
    for step in context.get("steps") or []:
        logins = [
            str(sam).strip()
            for sam in (step.get("assignees") or [])
            if str(sam).strip()
        ]
        if not logins:
            single = str(step.get("assignee") or "").strip()
            logins = [single] if single else []
        if not logins:
            continue
        names = []
        for sam in logins:
            if sam not in cards:
                try:
                    cards[sam] = routing_store.user_card(sam)
                except Exception:
                    cards[sam] = None
            name = str((cards[sam] or {}).get("fio_full") or "").strip()
            names.append(name or sam)
        step["assignee_names"] = names
        if not step.get("fio"):
            step["fio"] = names[0]


@router.post("/requests/{request_id}/print")
def print_bypass(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    settings_store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Печать бланка (ОК/админ): печатная форма ТЕКУЩЕГО состояния заявки.

    Документ собирается из данных заявки и снимка бланка
    (docs.build_blank_document: макет blank_layout office|line, шапка, таблица
    шагов с ВСЕМИ ответственными каждого шага, QR на заявку). Файлы-шаблоны
    .docx и ключ настроек doc_templates удалены; вид бланка службы AD
    (blank_kind) в выборе оформления больше не участвует. Пустые шаги — 422,
    а не пустой документ.

    Вариант 1 (решение пользователя): версии не накапливаются и документы в БД
    НЕ пишутся. PDF отдаётся base64 в ответе, временные файлы (docx/pdf/qr)
    удаляются. Офлайн/нет LibreOffice — {"generated": false, "reason": ...} (не 500).
    """
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    context = build_bypass_context(request)
    # Макет бланка — из снимка заявки (office по умолчанию), не по службе AD.
    blank_kind = resolve_blank_layout(context.get("blank_layout"))
    if not context.get("steps"):
        raise HTTPException(
            status_code=422,
            detail="В заявке нет шагов маршрута: бланк печатать нечего",
        )
    # ФИО ответственных этапов подставляем из зеркала AD (в колонку «Ответственный»).
    _fill_step_fio(context, _routing_store_or_none())
    # Предприятие — названием из справочника (код 1С в бланке нечитаем).
    context["enterprise"] = _enterprise_name(settings_store, request.enterprise)
    result = generate_bypass(
        request_id=request.id,
        version="current",
        context=context,
        base_url=settings.APP_BASE_URL,
        files_dir=settings.FILES_DIR,
    )
    if not result.generated:
        return {"generated": False, "reason": result.reason, "pdf_b64": None}
    try:
        pdf_bytes = Path(result.pdf_path).read_bytes()
    except OSError as exc:
        return {"generated": False, "reason": f"PDF не найден после генерации: {exc}", "pdf_b64": None}
    # Временные файлы не оставляем (печатная форма не хранится).
    for path in (result.docx_path, result.pdf_path):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
    qr_path = Path(settings.FILES_DIR) / f"bypass_{request.id}_vcurrent_qr.png"
    try:
        qr_path.unlink(missing_ok=True)
    except OSError:
        pass
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="document.print",
            entity="request",
            entity_id=request.id,
            detail="печать бланка (вариант 1, без сохранения версии)",
        )
    )
    return {
        "generated": True,
        "reason": None,
        "pdf_b64": base64.b64encode(pdf_bytes).decode("ascii"),
        # Макет напечатанного бланка (office/line) — из снимка заявки; для UI
        # и разбора «почему тот бланок».
        "blank_kind": blank_kind,
    }


@router.get("/documents/{request_id}", response_model=list[DocumentOut])
def list_documents(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    doc_store: DocumentsStore = Depends(get_documents_store),
) -> list[DocumentOut]:
    """Мета документов заявки (версии/пути/QR): ОК/админы и владелец своего шага."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not _can_view(request, user):
        raise HTTPException(status_code=403, detail="Нет доступа к документам заявки")
    try:
        records = doc_store.list_by_request(request.id)
    except DocumentsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="document.read",
            entity="document",
            entity_id=request.id,
            detail=f"versions={len(records)}",
        )
    )
    return [
        DocumentOut(
            version=r.version,
            docx_path=r.docx_path,
            pdf_path=r.pdf_path,
            qr_payload=r.qr_payload,
            created_at=r.created_at.isoformat() if r.created_at else None,
            created_by=r.created_by,
        )
        for r in records
    ]


@router.get("/documents/{request_id}/pdf")
def download_pdf(
    request_id: str,
    version: str = Query(default="v1", description="Версия бегунка (v1/v2/...)"),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    doc_store: DocumentsStore = Depends(get_documents_store),
):
    """PDF бегунка: ОК/админы и владелец своего шага; 404 — нет версии/файла."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not _can_view(request, user):
        raise HTTPException(status_code=403, detail="Нет доступа к документам заявки")
    try:
        record = doc_store.get(request.id, version)
    except DocumentsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if record is None or not record.pdf_path:
        raise HTTPException(status_code=404, detail="Документ не найден")
    pdf_path = Path(record.pdf_path)
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="PDF-файл не найден")
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="document.download",
            entity="document",
            entity_id=request.id,
            detail=f"version={version}",
        )
    )
    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=f"bypass_{request.id}_{version}.pdf",
    )
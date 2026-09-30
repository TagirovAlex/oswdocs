# Скан-вложения заявок (W5a): загрузка/чтение вложений с лимитами из settings.
# Файл — в volume FILES_DIR/attachments/{request_id}/ (интерфейс FilesStore,
# боевое — ФС, тесты — InMemoryFilesStore); мета — таблица attachments (0001)
# через AttachmentsStore (DbAttachmentsStore/InMemoryAttachmentsStore).
# Лимит размера — scan_max_mb, MIME-allowlist — scan_allowed_types из settings
# (нет ключа/пусто -> 409 «лимит не задан»); превышение размера -> 413,
# тип вне allowlist -> 415. Доступ — как у заявки: ОК/админы и владелец своего
# шага могут грузить/читать вложения своей заявки; ПДн внутри файла —
# ответственность загрузившего, мета без ПДн.

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user
from .requests import _get_request_or_404
from .requests_store import RequestsStore, RequestsUnavailable, get_requests_store
from .settings_routes import (
    DbSettingsStore,
    SettingsUnavailable,
    get_settings_store,
    read_setting_value,
)

router = APIRouter(tags=["вложения"])


class AttachmentsUnavailable(Exception):
    """Хранилище мета вложений (БД) недоступно — роутер отвечает 503, а не 500."""


class AttachmentRecord(BaseModel):
    """Мета скана (таблица attachments 0001: путь/имя/mime/размер/автор/время)."""

    id: int | None = Field(default=None, description="Внутренний id вложения")
    request_id: str = Field(description="Бизнес-номер заявки REQ-XXXX")
    file_path: str = Field(description="Путь к файлу в volume (в мета наружу не идет)")
    file_name: str = Field(description="Имя файла (санитизированное)")
    mime: str | None = Field(default=None, description="MIME-тип (из allowlist)")
    size_bytes: int | None = Field(default=None, description="Размер файла в байтах")
    uploaded_by: str = Field(default="", description="Кто загрузил (sam)")
    uploaded_at: datetime | None = Field(default=None, description="Момент загрузки")


class AttachmentOut(BaseModel):
    """Мета вложения наружу: без file_path (внутренний путь), без ПДн."""

    id: int
    request_id: str
    file_name: str
    mime: str | None = None
    size_bytes: int | None = None
    uploaded_by: str = ""
    uploaded_at: str | None = None


# ---------------------------------------------------------------------------
# Хранилище файлов: боевое — ФС (FILES_DIR), тестовое — in-memory
# ---------------------------------------------------------------------------

class FilesStore(Protocol):
    """Граница хранилища файлов вложений (боевое — ФС, тесты — память)."""

    def save(self, request_id: str, file_name: str, content: bytes) -> str:
        """Сохранить содержимое, вернуть путь для мета."""
        ...  # pragma: no cover

    def read(self, file_path: str) -> bytes | None:
        """Содержимое файла по пути из мета либо None (файла нет)."""
        ...  # pragma: no cover

    def real_path(self, file_path: str) -> str | None:
        """Путь на диске для FileResponse либо None (in-memory/файла нет)."""
        ...  # pragma: no cover


class FilesystemFilesStore:
    """Боевое хранилище: каталог FILES_DIR/attachments/{request_id}/ (volume)."""

    def __init__(self, files_dir: str) -> None:
        self._root = Path(files_dir) / "attachments"

    def _target(self, request_id: str, file_name: str) -> Path:
        # UUID в имени — от коллизий одноименных файлов; имя — только basename.
        return self._root / request_id / f"{uuid4().hex}_{file_name}"

    def save(self, request_id: str, file_name: str, content: bytes) -> str:
        path = self._target(request_id, file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path)

    def read(self, file_path: str) -> bytes | None:
        path = Path(file_path)
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def real_path(self, file_path: str) -> str | None:
        path = Path(file_path)
        return str(path) if path.is_file() else None


class InMemoryFilesStore:
    """Тестовое хранилище: содержимое в памяти, real_path — None (нет ФС)."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest."""
        self._files.clear()

    def save(self, request_id: str, file_name: str, content: bytes) -> str:
        path = f"/inmemory/attachments/{request_id}/{file_name}"
        self._files[path] = content
        return path

    def read(self, file_path: str) -> bytes | None:
        return self._files.get(file_path)

    def real_path(self, file_path: str) -> str | None:
        return None


def get_files_store(settings: Settings = Depends(get_settings)) -> FilesStore:
    """Боевое хранилище файлов: каталог FILES_DIR/attachments (volume files)."""
    return FilesystemFilesStore(settings.FILES_DIR)


# ---------------------------------------------------------------------------
# Хранилище мета: in-memory (тесты) и Postgres (таблица attachments из 0001)
# ---------------------------------------------------------------------------

class AttachmentsStore(Protocol):
    """Интерфейс хранилища мета вложений: единый для in-memory и Postgres."""

    def create(self, record: AttachmentRecord) -> AttachmentRecord:
        """Сохранить мету, вернуть запись с id."""
        ...  # pragma: no cover

    def list_by_request(self, request_id: str) -> list[AttachmentRecord]:
        """Все вложения заявки (по бизнес-номеру)."""
        ...  # pragma: no cover

    def get(self, attachment_id: int) -> AttachmentRecord | None:
        """Вложение по id либо None."""
        ...  # pragma: no cover


class InMemoryAttachmentsStore:
    """Офлайн-хранилище мета (dict), интерфейс AttachmentsStore."""

    def __init__(self) -> None:
        self._records: dict[int, AttachmentRecord] = {}
        self._seq = 0

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest."""
        self._records.clear()
        self._seq = 0

    def create(self, record: AttachmentRecord) -> AttachmentRecord:
        self._seq += 1
        record.id = self._seq
        if record.uploaded_at is None:
            record.uploaded_at = datetime.now(timezone.utc)
        self._records[record.id] = record
        return record

    def list_by_request(self, request_id: str) -> list[AttachmentRecord]:
        return [r for r in self._records.values() if r.request_id == request_id]

    def get(self, attachment_id: int) -> AttachmentRecord | None:
        return self._records.get(attachment_id)


class DbAttachmentsStore:
    """Мета вложений в Postgres (таблица attachments из 0001).

    Заявка в модели — бизнес-номер REQ-XXXX (code), в БД FK на внутренний id
    dismissal_requests; перевод — запросом по code (как DbDocumentsStore).
    Ошибки БД оборачиваются в AttachmentsUnavailable (503).
    """

    _SELECT_REQUEST_ID_BY_CODE = text(
        "SELECT id FROM dismissal_requests WHERE code = :code"
    )
    _SELECT_CODE_BY_ID = text(
        "SELECT code FROM dismissal_requests WHERE id = :id"
    )
    _INSERT = text(
        """
        INSERT INTO attachments (
          request_id, file_path, file_name, mime, size_bytes, uploaded_by
        )
        VALUES (
          :request_id, :file_path, :file_name, :mime, :size_bytes, :uploaded_by
        )
        RETURNING id, uploaded_at
        """
    )
    _SELECT_BY_REQUEST = text(
        """
        SELECT id, file_path, file_name, mime, size_bytes, uploaded_by, uploaded_at
        FROM attachments
        WHERE request_id = :request_id
        ORDER BY id
        """
    )
    _SELECT_ONE = text(
        """
        SELECT request_id, file_path, file_name, mime, size_bytes, uploaded_by, uploaded_at
        FROM attachments
        WHERE id = :attachment_id
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def _build(self, row, request_id: str) -> AttachmentRecord:
        return AttachmentRecord(
            id=row.id,
            request_id=request_id,
            file_path=row.file_path,
            file_name=row.file_name,
            mime=row.mime,
            size_bytes=row.size_bytes,
            uploaded_by=row.uploaded_by or "",
            uploaded_at=row.uploaded_at,
        )

    def create(self, record: AttachmentRecord) -> AttachmentRecord:
        try:
            with self._session_factory() as session:
                internal = session.execute(
                    self._SELECT_REQUEST_ID_BY_CODE, {"code": record.request_id}
                ).first()
                if internal is None:
                    raise AttachmentsUnavailable(
                        f"Заявка {record.request_id} не найдена в хранилище"
                    )
                row = session.execute(
                    self._INSERT,
                    {
                        "request_id": internal[0],
                        "file_path": record.file_path,
                        "file_name": record.file_name,
                        "mime": record.mime,
                        "size_bytes": record.size_bytes,
                        "uploaded_by": record.uploaded_by or None,
                    },
                ).first()
                session.commit()
        except AttachmentsUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise AttachmentsUnavailable(
                f"Хранилище вложений недоступно: {exc}"
            ) from exc
        record.id = row.id
        record.uploaded_at = row.uploaded_at
        return record

    def list_by_request(self, request_id: str) -> list[AttachmentRecord]:
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
            raise AttachmentsUnavailable(
                f"Хранилище вложений недоступно: {exc}"
            ) from exc
        return [self._build(row, request_id) for row in rows]

    def get(self, attachment_id: int) -> AttachmentRecord | None:
        try:
            with self._session_factory() as session:
                row = session.execute(
                    self._SELECT_ONE, {"attachment_id": attachment_id}
                ).first()
                if row is None:
                    return None
                request = session.execute(
                    self._SELECT_CODE_BY_ID, {"id": row.request_id}
                ).first()
        except SQLAlchemyError as exc:
            raise AttachmentsUnavailable(
                f"Хранилище вложений недоступно: {exc}"
            ) from exc
        if request is None:
            return None
        return self._build(row, request[0])


_db_attachments_store: DbAttachmentsStore | None = None


def get_attachments_store(
    settings: Settings = Depends(get_settings),
) -> AttachmentsStore:
    """Боевое хранилище мета вложений (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется InMemoryAttachmentsStore через
    dependency_overrides (как get_requests_store/get_settings_store).
    """
    global _db_attachments_store
    if _db_attachments_store is None:
        _db_attachments_store = DbAttachmentsStore(settings.DATABASE_URL)
    return _db_attachments_store


# ---------------------------------------------------------------------------
# Роли и политика скана (лимиты — только из settings, хардкода нет)
# ---------------------------------------------------------------------------

def _can_access(request: object, user: CurrentUser) -> bool:
    """Доступ к вложениям заявки: ОК/руководители ОК/админы или владелец одного из шагов."""
    if user.role in ("hr", "hr_admin", "admin"):
        return True
    return any(
        s.owner_group in user.groups or (s.assignee and s.assignee == user.sam)
        for s in request.steps
    )


def _safe_file_name(name: str | None) -> str:
    """Имя файла: только basename (защита от path traversal), пустое — заглушка."""
    base = Path(name or "").name.strip()
    return base or "scan"


def _scan_limits(settings_store: DbSettingsStore) -> tuple[int | None, list[str] | None]:
    """Лимит МБ и MIME-allowlist из settings; None — ключа нет/битое значение."""
    try:
        max_mb = read_setting_value(settings_store, "scan_max_mb")
        raw_types = read_setting_value(settings_store, "scan_allowed_types")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    max_mb_value = max_mb if isinstance(max_mb, int) else None
    allowed = None
    if isinstance(raw_types, list):
        allowed = [t.strip().lower() for t in raw_types if isinstance(t, str) and t.strip()]
    return max_mb_value, allowed


def _to_out(record: AttachmentRecord) -> AttachmentOut:
    """Мета наружу: без file_path (внутренний путь), uploaded_at — строкой."""
    return AttachmentOut(
        id=record.id,
        request_id=record.request_id,
        file_name=record.file_name,
        mime=record.mime,
        size_bytes=record.size_bytes,
        uploaded_by=record.uploaded_by,
        uploaded_at=record.uploaded_at.isoformat() if record.uploaded_at else None,
    )


# ---------------------------------------------------------------------------
# Эндпоинты: загрузка/список/файл
# ---------------------------------------------------------------------------

@router.post("/requests/{request_id}/attachments", response_model=AttachmentOut, status_code=201)
def upload_attachment(
    request_id: str,
    file: UploadFile = File(..., description="Скан заявления (multipart)"),
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    att_store: AttachmentsStore = Depends(get_attachments_store),
    files_store: FilesStore = Depends(get_files_store),
    settings_store: DbSettingsStore = Depends(get_settings_store),
) -> AttachmentOut:
    """Загрузка скана: hr/admin/владелец своего шага; лимиты — из settings.

    409 — лимит размера/типы не заданы; 413 — файл больше scan_max_mb;
    415 — MIME вне scan_allowed_types. Файл — в volume, мета — таблица attachments.
    """
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not _can_access(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к вложениям заявки"
        )
    max_mb, allowed_types = _scan_limits(settings_store)
    if max_mb is None:
        raise HTTPException(
            status_code=409, detail="Лимит размера скана не задан (scan_max_mb)"
        )
    if not allowed_types:
        raise HTTPException(
            status_code=409, detail="Разрешенные типы сканов не заданы (scan_allowed_types)"
        )
    mime = (file.content_type or "").strip().lower()
    if mime not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Тип файла не разрешен (разрешены: {', '.join(allowed_types)})",
        )
    content = file.file.read()
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Скан больше лимита {max_mb} МБ",
        )
    file_name = _safe_file_name(file.filename)
    stored_path = files_store.save(request.id, file_name, content)
    try:
        record = att_store.create(
            AttachmentRecord(
                request_id=request.id,
                file_path=stored_path,
                file_name=file_name,
                mime=mime,
                size_bytes=len(content),
                uploaded_by=user.sam,
                uploaded_at=datetime.now(timezone.utc),
            )
        )
    except AttachmentsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="attachment.upload",
            entity="attachment",
            entity_id=request.id,
            detail=f"bytes={len(content)}",
        )
    )
    return _to_out(record)


@router.get("/requests/{request_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    att_store: AttachmentsStore = Depends(get_attachments_store),
) -> list[AttachmentOut]:
    """Мета вложений заявки: ОК/админы и владелец своего шага (без ПДн)."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not _can_access(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к вложениям заявки"
        )
    try:
        records = att_store.list_by_request(request.id)
    except AttachmentsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="attachment.read",
            entity="attachment",
            entity_id=request.id,
            detail=f"count={len(records)}",
        )
    )
    return [_to_out(r) for r in records]


@router.get("/attachments/{attachment_id}/file")
def download_attachment(
    attachment_id: int,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    att_store: AttachmentsStore = Depends(get_attachments_store),
    files_store: FilesStore = Depends(get_files_store),
):
    """Файл вложения: ОК/админы и владелец своего шага; 404 — нет/файла нет."""
    settings.ensure_read_only()
    try:
        record = att_store.get(attachment_id)
    except AttachmentsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Вложение не найдено")
    try:
        request = _get_request_or_404(store, record.request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not _can_access(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к вложениям заявки"
        )
    media_type = record.mime or "application/octet-stream"
    real_path = files_store.real_path(record.file_path)
    if real_path is not None:
        audit_log.append(
            AuditEvent(
                actor=user.sam,
                action="attachment.download",
                entity="attachment",
                entity_id=record.request_id,
                detail=f"id={record.id}",
            )
        )
        return FileResponse(
            real_path, media_type=media_type, filename=record.file_name
        )
    content = files_store.read(record.file_path)
    if content is None:
        raise HTTPException(status_code=404, detail="Файл вложения не найден")
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="attachment.download",
            entity="attachment",
            entity_id=record.request_id,
            detail=f"id={record.id}",
        )
    )
    return Response(content=content, media_type=media_type)
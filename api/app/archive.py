# Архивация БД СЭД (блок F): ручной и регламентный бэкапы в каталог настроек.
# Дамп — БЕЗ внешних бинарей (pg_dump в образе api может отсутствовать): через
# psycopg2 (есть в requirements) COPY (SELECT * FROM <t>) TO STDOUT по таблицам
# схемы public из information_schema; в текстовый файл с заголовком по каждой
# таблице (-- таблица, время). Параллельных/системных команд не запускается.
# Файл — валидный COPY-дамп; RESTORE НЕ реализован намеренно (восстановление
# вручную: psql / COPY FROM).
# Настройки — JSON в settings (без хардкода, читаются через read_setting_value):
# archive_backup_dir (место хранения; по умолчанию — подкаталог FILES_DIR),
# archive_name_template (шаблон имени, {ts} — метка времени), archive_keep_copies
# (int >= 1), archive_schedule (mode interval|daily, как schedule_enterprises_sync);
# техническая метка archive_backup_at (последний бэкап, read-only).
# Уведомление о выполненном бэкапе (ручном и регламентном) — по archive_schedule
# (notify/recipients/subject/body, event=reglament), как у прочих регламентов.
# Доступ — только admin (как настройки админки); БД/каталог недоступны — 503.

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user
from .mailer import MailQueue, get_mail_queue, resolve_smtp_from
from .settings_routes import (
    DbSettingsStore,
    SettingsUnavailable,
    get_settings_store,
    read_setting_value,
)

router = APIRouter(tags=["архивация"])


class ArchiveUnavailable(Exception):
    """Бэкап не выполнен (БД/ФС недоступны) — роутер отвечает 503, а не 500."""


# --- Настройки архивации: значение из БД либо дефолт (значения — не в коде) ---


def _default_backup_dir(files_dir: str) -> str:
    """Каталог по умолчанию: подкаталог FILES_DIR (права контейнера api/worker)."""
    return str(Path(files_dir) / "backups")


def _backup_dir(store: DbSettingsStore, files_dir: str) -> Path:
    """Каталог хранения бэкапов: archive_backup_dir либо FILES_DIR/backups."""
    raw = read_setting_value(store, "archive_backup_dir")
    if isinstance(raw, str) and raw.strip():
        return Path(raw.strip())
    return Path(_default_backup_dir(files_dir))


def _name_template(store: DbSettingsStore) -> str:
    """Шаблон имени файла: archive_name_template ({ts} — метка времени) либо дефолт."""
    raw = read_setting_value(store, "archive_name_template")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "sed_backup_{ts}.sql"


def _keep_copies(store: DbSettingsStore) -> int:
    """Сколько хранить копий: archive_keep_copies (>=1) либо дефолт 10."""
    raw = read_setting_value(store, "archive_keep_copies")
    try:
        copies = int(raw)
    except (TypeError, ValueError):
        copies = 0
    return copies if copies >= 1 else 10


# --- Дамп БД: psycopg2 COPY, без внешних бинарей и системных команд ---


def _pg_connect(database_url: str):
    """Соединение psycopg2 из DATABASE_URL (компоненты через make_url,
    параметры соединения из query-строки URL сохраняются)."""
    from sqlalchemy.engine.url import make_url

    url = make_url(database_url)
    params = dict(url.query)
    params.update(
        host=url.host,
        port=url.port,
        dbname=url.database,
        user=url.username,
        password=url.password,
        connect_timeout=5,
    )
    import psycopg2

    return psycopg2.connect(**params)


def dump_database(database_url: str, target: Path, moment: datetime | None = None) -> None:
    """Дамп всех таблиц (schema public) в файл: COPY (SELECT * FROM <t>) TO STDOUT.

    Заголовок файла и каждой таблицы — SQL-комментарием (-- таблица, время).
    При сбое частичный файл удаляется, ошибка — ArchiveUnavailable."""
    moment = moment or datetime.now(timezone.utc)
    try:
        connection = _pg_connect(database_url)
    except Exception as exc:
        raise ArchiveUnavailable("БД недоступна: %s" % exc) from exc
    try:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                )
                tables = [row[0] for row in cursor.fetchall()]
            with open(target, "w", encoding="utf-8", newline="") as out:
                # Метка в заголовке — в локальном времени контейнера (TZ),
                # как и имя файла, чтобы дата бэкапа совпадала с хостом.
                local = moment.astimezone()
                out.write("-- SED backup %s\n" % local.isoformat())
                with connection.cursor() as cursor:
                    for table in tables:
                        out.write(
                            "\n-- таблица: %s (%s)\n" % (table, local.isoformat())
                        )
                        # Кавычки в имени таблицы удваиваем (защита от инъекций).
                        quoted = table.replace('"', '""')
                        cursor.copy_expert(
                            'COPY (SELECT * FROM "%s") TO STDOUT' % quoted, out
                        )
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise ArchiveUnavailable("Дамп не выполнен: %s" % exc) from exc
    finally:
        connection.close()


# --- Создание бэкапа, подчистка старых копий, список ---


def _cleanup(backup_dir: Path, keep: int) -> None:
    """Удалить старые бэкапы сверх keep (свежие по mtime/имени остаются)."""
    files = [p for p in backup_dir.iterdir() if p.is_file()]
    files.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass  # файл не удалили — не роняем сам бэкап


def _notify_backup(
    store: DbSettingsStore,
    queue: MailQueue,
    smtp_from: str,
    target: Path,
) -> None:
    """Уведомление о выполненном бэкапе по archive_schedule (event=reglament).

    Адресаты/тема/текст — из настроек расписания; в тело подставляется сводка
    ({{summary}}): имя файла и размер копии. Тихо, как у прочих регламентов:
    notify=false/нет адресатов — писем нет; сбой уведомления не отменяет уже
    созданный бэкап (в аудит — archive.notify.skip, без ПДн)."""
    from .onec_sync import notify_schedule  # лениво: избегаем циклов импорта

    try:
        size_mb = target.stat().st_size / (1024 * 1024)
        notify_schedule(
            store,
            queue,
            "archive_schedule",
            smtp_from,
            "бэкап создан: %s; размер: %.1f МБ" % (target.name, size_mb),
        )
    except Exception:
        audit_log.append(
            AuditEvent(
                actor="system",
                action="archive.notify.skip",
                entity="archive",
                entity_id=target.name,
            )
        )


def create_backup(
    store: DbSettingsStore,
    database_url: str,
    files_dir: str,
    moment: datetime | None = None,
    queue: MailQueue | None = None,
    smtp_from: str = "",
) -> Path:
    """Ручной/регламентный бэкап: дамп БД + подчистка старых копий.

    Каталог/шаблон/число копий — из settings (дефолты выше). Метка
    archive_backup_at обновляется после успешного дампа (единый «последний
    бэкап» для ручного и регламентного запуска); сбой записи метки не отменяет
    сам бэкап. При переданной очереди queue после успеха уходит уведомление по
    archive_schedule (адресаты/тема/текст — из настроек). Возвращает путь
    созданного файла; ошибка — ArchiveUnavailable/SettingsUnavailable."""
    moment = moment or datetime.now(timezone.utc)
    backup_dir = _backup_dir(store, files_dir)
    keep = _keep_copies(store)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArchiveUnavailable("Каталог бэкапов недоступен: %s" % exc) from exc
    # Только имя файла (basename): шаблон не должен уводить за пределы каталога.
    # Метка {ts} — в локальном времени контейнера (TZ), чтобы имя файла
    # совпадало с локальной датой хоста (бэкапы в MSK, а не в UTC).
    name = Path(
        _name_template(store).replace(
            "{ts}", moment.astimezone().strftime("%Y%m%d_%H%M%S")
        )
    ).name
    target = backup_dir / name
    dump_database(database_url, target, moment)
    _cleanup(backup_dir, keep)
    try:
        store.set("archive_backup_at", json.dumps(moment.isoformat()))
    except SettingsUnavailable:
        pass  # техническая метка: бэкап уже создан
    if queue is not None:
        _notify_backup(store, queue, smtp_from, target)
    return target


def list_backups(backup_dir: Path) -> list[dict]:
    """Список бэкапов каталога: имя, размер (байт), дата (mtime, локальное
    время контейнера — как имя файла, чтобы совпадало с локальной датой).

    Файлами бэкапов считаются все регулярные файлы каталога (каталог выделен
    настройкой archive_backup_dir под хранение бэкапов)."""
    items = []
    if not backup_dir.is_dir():
        return items
    for path in backup_dir.iterdir():
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "date": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            }
        )
    items.sort(key=lambda item: item["name"])
    return items


def maybe_backup_weekly(
    store: DbSettingsStore,
    database_url: str,
    files_dir: str,
    moment: datetime | None = None,
    queue: MailQueue | None = None,
    smtp_from: str = "",
) -> bool:
    """Регламентный бэкап (worker): тихо, без сбоев.

    «Не пора» по расписанию archive_schedule (нет расписания — раз в 7 дней от
    archive_backup_at) — False; иначе create_backup (сбой не валит worker —
    False), успех — True. При переданной очереди queue после успеха уходит
    уведомление по archive_schedule."""
    from .onec_sync import due_schedule  # лениво: избегаем циклов импорта

    schedule = read_setting_value(store, "archive_schedule")
    last_raw = read_setting_value(store, "archive_backup_at")
    if not due_schedule(schedule, last_raw):
        return False
    try:
        create_backup(
            store,
            database_url,
            files_dir,
            moment,
            queue=queue,
            smtp_from=smtp_from,
        )
        return True
    except Exception:
        return False


# --- Эндпоинты (внешние /api/archive*, префикс /api срезает nginx) ---


class ArchiveSettingsIn(BaseModel):
    """Тело PUT /api/archive: частичное обновление настроек архивации
    (пишутся только присутствующие ключи, как PUT /settings)."""

    archive_backup_dir: str | None = Field(
        default=None, description="Каталог хранения бэкапов"
    )
    archive_name_template: str | None = Field(
        default=None,
        description="Шаблон имени файла бэкапа ({ts} — метка времени)",
    )
    archive_keep_copies: int | None = Field(
        default=None, description="Сколько копий бэкапов хранить (>=1)"
    )
    archive_schedule: dict | None = Field(
        default=None,
        description=(
            "Расписание регламентного бэкапа (mode interval|daily, "
            "как schedule_enterprises_sync)"
        ),
    )


def _non_empty(value: object) -> bool:
    """Значение — непустая строка (после обрезки пробелов)."""
    return isinstance(value, str) and bool(value.strip())


def _require_admin(user: CurrentUser) -> None:
    """Архивация — админка: строго admin (как настройки), иначе 403."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Архивация доступна только администраторам",
        )


def _archive_settings(store: DbSettingsStore, settings: Settings) -> dict:
    """Эффективные настройки архивации: значение из БД либо дефолт + метка."""
    return {
        "archive_backup_dir": str(_backup_dir(store, settings.FILES_DIR)),
        "archive_name_template": _name_template(store),
        "archive_keep_copies": _keep_copies(store),
        "archive_schedule": read_setting_value(store, "archive_schedule"),
        "archive_backup_at": read_setting_value(store, "archive_backup_at"),
    }


@router.get("/archive")
def read_archive_settings(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Настройки архивации: только admin, иначе 403; БД недоступна — 503."""
    _require_admin(user)
    try:
        values = _archive_settings(store, settings)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.read",
            entity="settings",
            entity_id="archive",
        )
    )
    return values


@router.put("/archive")
def update_archive_settings(
    payload: ArchiveSettingsIn,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Сохранить настройки архивации: только admin, иначе 403.

    Валидация: каталог/шаблон непустые, число копий >=1 (иначе 422);
    частичное обновление (пишутся только присутствующие ключи); ответ —
    полное эффективное состояние. RESTORE не реализован (вручную)."""
    _require_admin(user)
    updates = payload.model_dump(mode="json", exclude_unset=True)
    if "archive_backup_dir" in updates and not _non_empty(
        updates["archive_backup_dir"]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Каталог хранения бэкапов не может быть пустым",
        )
    if "archive_name_template" in updates and not _non_empty(
        updates["archive_name_template"]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Шаблон имени файла не может быть пустым",
        )
    if "archive_keep_copies" in updates and updates["archive_keep_copies"] < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Число хранимых копий должно быть >= 1",
        )
    stored = {
        key: json.dumps(value) for key, value in updates.items()
    }
    try:
        store.set_many(stored)
        values = _archive_settings(store, settings)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.update",
            entity="settings",
            entity_id="archive",
            detail=",".join(updates),
        )
    )
    return values


@router.post("/archive/backup")
def run_archive_backup(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
    settings: Settings = Depends(get_settings),
    mail_queue: MailQueue = Depends(get_mail_queue),
) -> dict:
    """Ручной бэкап (только admin): дамп БД в каталог настроек, подчистка
    старых копий сверх archive_keep_copies; ответ {ok, path, files}.

    После успеха письмо по archive_schedule (notify/recipients/subject/body) —
    так же, как при регламентном бэкапе; сбой уведомления бэкап не отменяет.
    БД/каталог недоступны — 503 (не 500). RESTORE не реализован
    (восстановление вручную)."""
    _require_admin(user)
    try:
        smtp_from = resolve_smtp_from(
            read_setting_value(store, "smtp_from"), settings.SMTP_FROM
        )
        target = create_backup(
            store,
            settings.DATABASE_URL,
            settings.FILES_DIR,
            queue=mail_queue,
            smtp_from=smtp_from,
        )
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ArchiveUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="archive.backup",
            entity="archive",
            entity_id=target.name,
        )
    )
    return {"ok": True, "path": str(target), "files": list_backups(target.parent)}


@router.get("/archive/files")
def list_archive_files(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Список сохранённых бэкапов (имя, размер, дата): только admin, иначе 403.

    Каталог — archive_backup_dir (дефолт — FILES_DIR/backups); БД недоступна —
    503. RESTORE не реализован (восстановление вручную)."""
    _require_admin(user)
    try:
        backup_dir = _backup_dir(store, settings.FILES_DIR)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="archive.files",
            entity="archive",
            entity_id="files",
        )
    )
    return list_backups(backup_dir)


def _backup_path(backup_dir: Path, name: str) -> Path | None:
    """Путь к бэкапу внутри каталога: только basename, без ухода за пределы.

    Если имя уводит за пределы каталога (или resolve недоступен) — None,
    вызывающий отвечает 404."""
    candidate = backup_dir / Path(name).name
    try:
        inside = candidate.resolve().is_relative_to(backup_dir.resolve())
    except OSError:
        inside = False
    return candidate if inside else None


@router.get("/archive/files/{name}/download")
def download_archive_file(
    name: str,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Скачать файл бэкапа (только admin): attachment, имя — basename.

    Имя за пределами каталога или файл отсутствует — 404; БД недоступна — 503."""
    _require_admin(user)
    try:
        backup_dir = _backup_dir(store, settings.FILES_DIR)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    target = _backup_path(backup_dir, name)
    if target is None or not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл бэкапа не найден",
        )
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="archive.download",
            entity="archive",
            entity_id=target.name,
        )
    )
    return FileResponse(
        str(target), media_type="application/octet-stream", filename=target.name
    )


@router.delete("/archive/files/{name}")
def delete_archive_file(
    name: str,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Удалить файл бэкапа (только admin): имя за пределами каталога или файла
    нет — 404; успех — {ok: true}. БД недоступна — 503."""
    _require_admin(user)
    try:
        backup_dir = _backup_dir(store, settings.FILES_DIR)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    target = _backup_path(backup_dir, name)
    if target is None or not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл бэкапа не найден",
        )
    target.unlink()
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="archive.delete",
            entity="archive",
            entity_id=target.name,
        )
    )
    return {"ok": True}
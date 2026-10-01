# Прикладные настройки (админка): GET/PUT /settings (только admin, все ключи)
# и GET/PUT /settings/content (admin + руководитель ОК, только контент-ключи).
# Хранилище — таблица settings в Postgres (JSONB value), общение строкой
# в «сид-формате» (db/seeds/settings.sql): int '3', bool 'true', строка '"..."'.
# Контракт B2: GET/PUT охватывают все прикладные ключи (см. SETTINGS_KEYS);
# технический ключ sed_ou из сида админка не трогает.
# Падение БД -> 503, а не 500 (как RedisSessionStore -> SessionUnavailable в auth).
# Зависимость get_settings_store() НЕ конфликтует с get_settings из config.py.

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user

router = APIRouter(tags=["настройки"])


class SettingsUnavailable(Exception):
    """Хранилище настроек (БД) недоступно — роутер отвечает 503, а не 500."""


# Прикладные ключи админки (состав — контракт B2 GET/PUT /settings, дополнен
# W3a: doc_templates/mail_templates — бегунки и письма; W5a: scan_allowed_types —
# MIME-allowlist сканов; SMTP: smtp_host/smtp_port/smtp_from/smtp_user/smtp_password —
# параметры релея из settings; пароль маскируется в GET и пишется только при вводе).
# Фаза 2: ключи разделены на КОНТЕНТ (руководитель ОК + админ) и ИНФРА (только админ);
# SETTINGS_KEYS — полный набор (контент + инфра).
CONTENT_KEYS: tuple[str, ...] = (
    "approval_ttl_days",
    "require_comment",
    "require_paper_signature",
    "enterprises",
    "allowed_ad_groups",
    "position_to_category",
    "position_escalation",
    "templates",
    "doc_templates",
    "mail_templates",
)

INFRA_KEYS: tuple[str, ...] = (
    "session_ttl_minutes",
    "scan_retention_days",
    "scan_max_mb",
    "scan_allowed_types",
    "smtp_host",
    "smtp_port",
    "smtp_from",
    "smtp_user",
    "smtp_password",
    "onec_bases",
    "onec_enterprises_synced_at",
)

# SETTINGS_KEYS: полный набор (контент + инфра). Поле onec_enterprises_synced_at
# читается GET /settings (read-only, пишет только синхронизация) — в SettingsPayload
# его НЕТ, админ изменить не может.
SETTINGS_KEYS: tuple[str, ...] = CONTENT_KEYS + INFRA_KEYS

# Маска пароля SMTP в GET /settings: наружу отдаём только признак «задан/не задан»,
# само значение — только запись (PUT) при явном вводе нового пароля.
SMTP_PASSWORD_MASK = "********"


def _mask_smtp_password(value: object) -> object:
    """Пароль SMTP в ответе: маска если задан, иначе None (значение не отдаём)."""
    return SMTP_PASSWORD_MASK if isinstance(value, str) and value else None


def _mask_onec_bases(value: object) -> object:
    """Пароли баз 1С в ответе: для каждого элемента списка password заменяется
    на маску если задан, иначе None (значение не отдаём)."""
    if not isinstance(value, list):
        return value
    masked = []
    for item in value:
        if not isinstance(item, dict):
            masked.append(item)
            continue
        row = dict(item)
        password = row.get("password")
        row["password"] = SMTP_PASSWORD_MASK if isinstance(password, str) and password else None
        masked.append(row)
    return masked


def _merge_onec_bases(incoming: list[dict], stored: object) -> list[dict]:
    """Слить пароли баз 1С при PUT: пустое значение/маска входящего password —
    сохранить текущий из БД (по коду базы); реальное значение — записать;
    без совпадения — оставить как есть (пустой пароль)."""
    saved_by_code: dict[str, object] = {}
    if isinstance(stored, list):
        for item in stored:
            if isinstance(item, dict) and item.get("code"):
                saved_by_code[str(item["code"])] = item.get("password")
    merged = []
    for item in incoming:
        row = dict(item)
        password = row.get("password")
        if password in (None, "", SMTP_PASSWORD_MASK):
            row["password"] = saved_by_code.get(str(row.get("code") or ""))
        merged.append(row)
    return merged


def _to_stored(value: object) -> str:
    """Типизированное значение в сид-формат строки: json.dumps дает
    '3'/'true'/'\"sed@example.com\"' — тот же формат, что в сидах."""
    return json.dumps(value)


def _from_stored(raw: str | None) -> object:
    """Строка сид-формата в типизированное значение; ключа нет (или битое
    значение) — None. Дефолтов в коде нет: значения живут только в БД
    (AGENTS.md п.3), отсутствующий ключ админ заполняет через PUT."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # JSONB всегда валиден, ветка чисто защитная.
        return None


def read_setting_value(store: DbSettingsStore, key: str) -> object:
    """Типизированное значение ключа настроек для прикладных модулей
    (бланки/письма/worker): паттерн ключ -> сид-строка -> значение.
    Ключа нет в БД — None; падение БД — SettingsUnavailable (503)."""
    return _from_stored(store.get(key))


class DbSettingsStore:
    """Хранилище прикладных настроек в Postgres (таблица settings: key/value).

    Значения — JSONB; наружу и внутрь — строки сид-формата. Ошибки БД
    оборачиваются в SettingsUnavailable (503), как SessionUnavailable в auth.
    """

    _GET_SQL = text("SELECT CAST(value AS text) FROM settings WHERE key = :key")
    _GET_MANY_SQL = text(
        "SELECT key, CAST(value AS text) FROM settings WHERE key IN :keys"
    ).bindparams(bindparam("keys", expanding=True))
    _UPSERT_SQL = text(
        """
        INSERT INTO settings (key, value) VALUES (:key, CAST(:value AS jsonb))
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def get(self, key: str) -> str | None:
        """Значение ключа в сид-формате либо None (ключа нет в БД)."""
        try:
            with self._session_factory() as session:
                row = session.execute(self._GET_SQL, {"key": key}).first()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        """Записать ключ (upsert); value — строка сид-формата."""
        try:
            with self._session_factory() as session:
                session.execute(self._UPSERT_SQL, {"key": key, "value": value})
                session.commit()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc

    def get_many(self, keys: Sequence[str]) -> dict[str, str]:
        """Значения по ключам: {key: строка сид-формата}; отсутствующих нет."""
        try:
            with self._session_factory() as session:
                rows = session.execute(self._GET_MANY_SQL, {"keys": list(keys)}).all()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return {row[0]: row[1] for row in rows}

    def set_many(self, values: Mapping[str, str]) -> None:
        """Записать несколько ключей одним батчем (upsert)."""
        try:
            with self._session_factory() as session:
                for key, value in values.items():
                    session.execute(self._UPSERT_SQL, {"key": key, "value": value})
                session.commit()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc


_db_store: DbSettingsStore | None = None


def get_settings_store(settings: Settings = Depends(get_settings)) -> DbSettingsStore:
    """Боевое хранилище настроек (Postgres): один движок на процесс.

    В офлайн-тестах подменяется in-memory моком через dependency_overrides
    (аналог get_auth_service/get_onec_client). Имя не конфликтует с
    get_settings из config.py (тот — pydantic-Settings из env).
    """
    global _db_store
    if _db_store is None:
        _db_store = DbSettingsStore(settings.DATABASE_URL)
    return _db_store


class EnterpriseItem(BaseModel):
    """Предприятие справочника: пара code+name (формат сида settings.enterprises)."""

    code: str = Field(description="Код предприятия (составной ключ сотрудника)")
    name: str = Field(description="Название предприятия")


class TemplateStepItem(BaseModel):
    """Шаг шаблона маршрута (контракт B2: группа + опциональные резолвер/флаг)."""

    owner_group: str = Field(description="Группа-владелец шага из settings")
    resolver: str | None = Field(default=None, description="Резолвер исполнителя")
    require_comment: bool | None = Field(
        default=None, description="Комментарий обязателен даже при согласии"
    )


class TemplateItem(BaseModel):
    """Шаблон маршрута: служба + категория → шаги (формат settings.templates)."""

    service: str = Field(description="Служба увольняемого (поле 1С)")
    category: str = Field(description="Категория (МОЛ/линейный/руководитель)")
    steps: list[TemplateStepItem] = Field(description="Шаги шаблона по порядку")


class DocTemplateItem(BaseModel):
    """Шаблон бегунка (W3a): служба + категория → текст-шаблон DOCX (Jinja)."""

    service: str = Field(description="Служба увольняемого (поле 1С)")
    category: str = Field(description="Категория (МОЛ/линейный/руководитель)")
    body: str = Field(description="Тело бегунка с плейсхолдерами {{ fio }} и др.")


class MailTemplateItem(BaseModel):
    """Шаблон письма (W3a): код события v1 + тема и Jinja-HTML тело."""

    code: str = Field(description="Событие v1: assigned/reminder/escalation/closed/returned")
    subject: str = Field(description="Тема письма (Jinja-подобная)")
    body_html: str = Field(description="HTML-тело письма (Jinja-подобное)")


class OnecBaseItem(BaseModel):
    """Подключение к базе 1С (OData) + схема сущностей/полей (дефолты под ЗУП 3.х).

    В базе может быть несколько предприятий — список предприятий и маппинг
    «предприятие→базы» собирает синхронизация (POST /settings/enterprises/sync)."""

    code: str = Field(description="Код базы 1С (base_code, уникален)")
    name: str = Field(description="Название базы")
    url: str = Field(description="OData-URL публикации базы (до /odata/standard.odata/)")
    user: str = Field(default="", description="Сервисная УЗ чтения (роль OData)")
    password: str | None = Field(
        default=None, description="Пароль УЗ (маскируется в GET, пишется при вводе)"
    )
    employee_entity: str = Field(
        default="Catalog_Сотрудники",
        description="Сущность сотрудников OData (справочник; таб.№ = Code, ФИО = Description)",
    )
    organization_entity: str = Field(
        default="Catalog_Организации", description="Сущность организаций (предприятий) OData"
    )
    employee_org_field: str = Field(
        default="ГоловнаяОрганизация_Key",
        description="Поле предприятия в справочнике сотрудников (код = Ref_Key организации)",
    )
    tab_num_field: str = Field(default="Code", description="Поле таб.№ (в Catalog_Сотрудники)")
    fio_field: str = Field(
        default="Description", description="Поле ФИО (в Catalog_Сотрудники)"
    )
    department_field: str = Field(
        default="ТекущееПодразделение/Description",
        description="Поле подразделения (регистр кадровых данных, $expand)",
    )
    position_field: str = Field(
        default="ТекущаяДолжность/Description",
        description="Поле должности (регистр кадровых данных, $expand)",
    )
    hire_date_field: str = Field(
        default="ДатаПриема", description="Поле даты приёма (регистр кадровых данных)"
    )
    termination_date_field: str = Field(
        default="ДатаУвольнения",
        description="Поле даты увольнения (регистр кадровых данных)",
    )
    hr_entity: str = Field(
        default="InformationRegister_ТекущиеКадровыеДанныеСотрудников",
        description="Регистр текущих кадровых данных (второй запрос карточки)",
    )
    hr_employee_field: str = Field(
        default="Сотрудник_Key", description="Поле сотрудника (Ref_Key) в регистре кадровых данных"
    )
    organization_code_field: str = Field(
        default="Ref_Key", description="Поле кода организации (у ЗУП-«Организаций» кода нет — Ref_Key/ИНН)"
    )
    organization_name_field: str = Field(
        default="Description", description="Поле названия организации"
    )


class SettingsPayload(BaseModel):
    """Тело GET/PUT /settings: все прикладные ключи; в PUT все опциональны
    (частичное обновление — пишутся только присутствующие в теле ключи)."""

    session_ttl_minutes: int | None = Field(
        default=None, description="TTL сессии в минутах (10 ч = 600; иначе env SESSION_TTL_MINUTES)"
    )
    approval_ttl_days: int | None = Field(
        default=None, description="Срок отметки шага в днях"
    )
    scan_retention_days: int | None = Field(
        default=None, description="Срок хранения сканов в днях"
    )
    scan_max_mb: int | None = Field(
        default=None, description="Максимальный размер скана в МБ"
    )
    scan_allowed_types: list[str] | None = Field(
        default=None, description="MIME-allowlist типов сканов (JSON-массив)"
    )
    require_paper_signature: bool | None = Field(
        default=None, description="Нужна ли бумажная подпись"
    )
    smtp_host: str | None = Field(
        default=None, description="Хост SMTP-релея (из settings, иначе env SMTP_HOST)"
    )
    smtp_port: int | None = Field(
        default=None, description="Порт SMTP-релея (из settings, иначе env SMTP_PORT)"
    )
    smtp_from: str | None = Field(
        default=None, description="Отправитель уведомлений (SMTP FROM)"
    )
    smtp_user: str | None = Field(
        default=None, description="Логин SMTP-релея (пусто — без авторизации)"
    )
    smtp_password: str | None = Field(
        default=None,
        description=(
            "Пароль SMTP-релея: пишется только при вводе нового значения; "
            "пусто/маска в PUT — сохранить текущий; в GET — маска или null"
        ),
    )
    require_comment: bool | None = Field(
        default=None, description="Комментарий обязателен на шаге всегда"
    )
    enterprises: list[EnterpriseItem] | None = Field(
        default=None, description="Предприятия (код+название)"
    )
    allowed_ad_groups: list[str] | None = Field(
        default=None, description="Группы ручного конструктора шагов"
    )
    position_to_category: dict[str, str] | None = Field(
        default=None, description="Должность 1С → категория"
    )
    position_escalation: dict[str, int] | None = Field(
        default=None, description="Должность → часы эскалации"
    )
    templates: list[TemplateItem] | None = Field(
        default=None, description="Шаблоны маршрутов (служба+категория→шаги)"
    )
    doc_templates: list[DocTemplateItem] | None = Field(
        default=None, description="Шаблоны бегунков (служба+категория→тело DOCX)"
    )
    mail_templates: list[MailTemplateItem] | None = Field(
        default=None, description="Шаблоны писем (код события→тема+HTML-тело)"
    )
    onec_bases: list[OnecBaseItem] | None = Field(
        default=None, description="Подключения к базам 1С (OData, пароль маскируется)"
    )


class ContentSettingsPayload(BaseModel):
    """Тело GET/PUT /settings/content: только контент-ключи (руководитель ОК
    + админ); в PUT все опциональны (частичное обновление). Чужие ключи
    (инфра) в теле игнорируются pydantic — не 422 (клиент шлёт один объект)."""

    approval_ttl_days: int | None = Field(
        default=None, description="Срок отметки шага в днях"
    )
    require_comment: bool | None = Field(
        default=None, description="Комментарий обязателен на шаге всегда"
    )
    require_paper_signature: bool | None = Field(
        default=None, description="Нужна ли бумажная подпись"
    )
    enterprises: list[EnterpriseItem] | None = Field(
        default=None, description="Предприятия (код+название)"
    )
    allowed_ad_groups: list[str] | None = Field(
        default=None, description="Группы ручного конструктора шагов"
    )
    position_to_category: dict[str, str] | None = Field(
        default=None, description="Должность 1С → категория"
    )
    position_escalation: dict[str, int] | None = Field(
        default=None, description="Должность → часы эскалации"
    )
    templates: list[TemplateItem] | None = Field(
        default=None, description="Шаблоны маршрутов (служба+категория→шаги)"
    )
    doc_templates: list[DocTemplateItem] | None = Field(
        default=None, description="Шаблоны бегунков (служба+категория→тело DOCX)"
    )
    mail_templates: list[MailTemplateItem] | None = Field(
        default=None, description="Шаблоны писем (код события→тема+HTML-тело)"
    )


def _require_admin(user: CurrentUser) -> None:
    """Настройки — строго роль admin (ОК/владельцам 403; is_privileged не подходит)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Настройки доступны только администраторам",
        )


def _require_hr(user: CurrentUser) -> None:
    """Справочники Волны 1 (предприятия/группы шагов) — только ОК,
    руководителям ОК и админам."""
    if user.role not in ("hr", "hr_admin", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Справочники доступны только разрешенной группе",
        )


def _require_content_admin(user: CurrentUser) -> None:
    """Контент-настройки — роли admin и руководитель ОК (hr_admin), иначе 403."""
    if user.role not in ("admin", "hr_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Контент-настройки — руководителям ОК и админам",
        )


def _settings_dict(store: DbSettingsStore) -> dict:
    """Типизированный словарь настроек из хранилища (None — ключа нет в БД)."""
    raw = store.get_many(SETTINGS_KEYS)
    values = {key: _from_stored(raw.get(key)) for key in SETTINGS_KEYS}
    # Пароль SMTP наружу не отдаём: только признак «задан/не задан».
    values["smtp_password"] = _mask_smtp_password(values.get("smtp_password"))
    # Пароли баз 1С наружу не отдаём: маска/None (аналог smtp_password).
    values["onec_bases"] = _mask_onec_bases(values.get("onec_bases"))
    return values


def _content_dict(store: DbSettingsStore) -> dict:
    """Типизированный словарь контент-настроек из хранилища (None — ключа нет в БД).
    Пароль SMTP не входит в CONTENT_KEYS — маскирование не нужно."""
    raw = store.get_many(CONTENT_KEYS)
    return {key: _from_stored(raw.get(key)) for key in CONTENT_KEYS}


@router.get("/settings")
def read_settings(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Прикладные настройки админки: только admin, иначе 403; БД недоступна — 503."""
    _require_admin(user)
    try:
        values = _settings_dict(store)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.read",
            entity="settings",
            entity_id=",".join(SETTINGS_KEYS),
        )
    )
    return values


@router.put("/settings")
def update_settings(
    payload: SettingsPayload,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Обновить настройки: только admin; все ключи опциональны (частичное
    обновление — пишутся только присутствующие), ответ — полное состояние."""
    _require_admin(user)
    # exclude_unset: только явно переданные ключи (в т.ч. внутри шаблонов);
    # дефолтов нет — отсутствующий ключ в БД остается как был.
    updates = payload.model_dump(mode="json", exclude_unset=True)
    # Пароль SMTP: пустое значение/маска = «не менять» (текущий сохраняется);
    # реальное значение — только явный ввод нового пароля.
    if updates.get("smtp_password") in (None, "", SMTP_PASSWORD_MASK):
        updates.pop("smtp_password", None)
    try:
        # Пароли баз 1С: пустое значение/маска = сохранить текущий из БД.
        if "onec_bases" in updates:
            updates["onec_bases"] = _merge_onec_bases(
                updates["onec_bases"], _from_stored(store.get("onec_bases"))
            )
        stored = {key: _to_stored(value) for key, value in updates.items()}
        store.set_many(stored)
        values = _settings_dict(store)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.update",
            entity="settings",
            entity_id=",".join(SETTINGS_KEYS),
            detail=",".join(updates),
        )
    )
    return values


@router.get("/settings/content")
def read_content_settings(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Контент-настройки (руководитель ОК + админ): только CONTENT_KEYS,
    иначе 403; БД недоступна — 503."""
    _require_content_admin(user)
    try:
        values = _content_dict(store)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.read",
            entity="settings",
            entity_id=",".join(CONTENT_KEYS),
        )
    )
    return values


@router.put("/settings/content")
def update_content_settings(
    payload: ContentSettingsPayload,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Обновить контент-настройки (руководитель ОК + админ): частичное
    обновление только CONTENT_KEYS (чужие ключи в теле игнорируются),
    ответ — полное состояние контента."""
    _require_content_admin(user)
    # exclude_unset: только явно переданные ключи; инфра-ключи в теле pydantic
    # игнорирует (extra) — в хранилище не пишутся.
    updates = payload.model_dump(mode="json", exclude_unset=True)
    stored = {key: _to_stored(value) for key, value in updates.items()}
    try:
        store.set_many(stored)
        values = _content_dict(store)
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.update",
            entity="settings",
            entity_id=",".join(CONTENT_KEYS),
            detail=",".join(updates),
        )
    )
    return values


@router.post("/settings/enterprises/sync")
def sync_enterprises_endpoint(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Принудительная синхронизация предприятий из 1С: только admin.

    Источник не настроен/недоступен/битый ответ — 503 (не 500); успех —
    {"synced": true, "count": N, "enterprises": [...]}. Парсинг — в onec_sync.
    """
    _require_admin(user)
    from .onec_sync import OnecSyncUnavailable, sync_enterprises

    try:
        items = sync_enterprises(store)
    except OnecSyncUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="settings.update",
            entity="settings",
            entity_id="enterprises.sync",
            detail="count=%d" % len(items),
        )
    )
    return {"synced": True, "count": len(items), "enterprises": items}


@router.get("/enterprises")
def list_enterprises(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> list[dict]:
    """Предприятия (Волна 1): ОК/админы, иначе 403; БД недоступна — 503.
    Значения — только из таблицы settings (ключ enterprises), хардкода нет."""
    _require_hr(user)
    try:
        raw = store.get("enterprises")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    values = _from_stored(raw)
    items = [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="enterprises.read",
            entity="enterprise",
            entity_id="enterprises",
        )
    )
    return items


@router.get("/step-groups")
def list_step_groups(
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> list[str]:
    """Группы ручного конструктора шагов (Волна 1): ОК/админы, иначе 403;
    значения — из таблицы settings (ключ allowed_ad_groups), хардкода нет."""
    _require_hr(user)
    try:
        raw = store.get("allowed_ad_groups")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    values = _from_stored(raw)
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str)]
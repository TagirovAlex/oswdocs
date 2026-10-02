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
import os
from collections.abc import Mapping, Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user

router = APIRouter(tags=["настройки"])


class SettingsUnavailable(Exception):
    """Хранилище настроек (БД) недоступно — роутер отвечает 503, а не 500."""


class DocTypeConflict(Exception):
    """Код вида документа уже занят (409): вторичный ключ doc_types.code."""


# Прикладные ключи админки (состав — контракт B2 GET/PUT /settings, дополнен
# W3a: doc_templates/mail_templates — бегунки и письма; W5a: scan_allowed_types —
# MIME-allowlist сканов; SMTP: smtp_host/smtp_port/smtp_from/smtp_user/smtp_password —
# параметры релея из settings; пароль маскируется в GET и пишется только при вводе).
# Фаза 2: ключи разделены на КОНТЕНТ (руководитель ОК + админ) и ИНФРА (только админ);
# SETTINGS_KEYS — полный набор (контент + инфра).
# Группы доступа: access_groups (инфра, правит ТОЛЬКО admin) — AD-группы, дающие
# вход в систему (дополняют env ALLOWED_AD_GROUPS); allowed_ad_groups остаётся
# контент-ключом (правит руководитель ОК) — это группы ручного конструктора
# шагов (GET /step-groups), входа они НЕ расширяют. Ключи ролей
# sed_admin_groups/admin_groups/hr_groups/hr_admin_groups (инфра) — группы ролей
# из БД с фолбэком на env (SED_ADMIN_GROUPS/ADMIN_GROUPS/HR_GROUPS/
# HR_ADMIN_GROUPS).
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
    "access_groups",
    "sed_admin_groups",
    "admin_groups",
    "hr_groups",
    "hr_admin_groups",
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
    "ad_links_synced_at",
    "schedule_enterprises_sync",
    "schedule_ad_links_sync",
)

# SETTINGS_KEYS: полный набор (контент + инфра). Поля onec_enterprises_synced_at
# и ad_links_synced_at читаются GET /settings (read-only, пишут только
# синхронизации) — в SettingsPayload их НЕТ, админ изменить не может.
SETTINGS_KEYS: tuple[str, ...] = CONTENT_KEYS + INFRA_KEYS

# Ключи групп ролей: значение из БД — единственный источник, ключа нет или БД
# недоступна — env (аддитивно к DbSettingsStore, контракт не меняем).
ROLE_GROUP_KEYS: tuple[str, ...] = (
    "sed_admin_groups",
    "admin_groups",
    "hr_groups",
    "hr_admin_groups",
)

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


def _groups_with_names(raw: object) -> list[dict[str, str]]:
    """Список групп из значения настройки: объекты {id, name} (блок B);
    строки старого формата читаются как id=name. Пустые/битые отбрасываются."""
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
            if value:
                items.append({"id": value, "name": value})
        elif isinstance(item, dict):
            group_id = item.get("id")
            name = item.get("name")
            if isinstance(group_id, str) and group_id.strip():
                items.append(
                    {"id": group_id.strip(), "name": str(name or group_id).strip()}
                )
    return items


def _clean_groups(raw: object) -> set[str]:
    """Список AD-групп из значения настройки: строки либо объекты {id, name}
    (значим id), только непустые без пробелов вокруг; битые отбрасываются."""
    return {item["id"] for item in _groups_with_names(raw)}


def resolve_allowed_groups(
    settings: Settings, store: DbSettingsStore | None = None
) -> set[str]:
    """Группы, дающие ВХОД в систему: объединение env-набора
    (settings.allowed_groups — ALLOWED_AD_GROUPS плюс bootstrap-группы ролей)
    и инфра-ключа access_groups из настроек БД (его правит только админ).

    Контент-ключ allowed_ad_groups входа НЕ расширяет — это группы ручного
    конструктора шагов (см. resolve_step_groups). БД недоступна — только env:
    вход/доступ не валим (как session_ttl в auth).
    """
    allowed = set(settings.allowed_groups)
    if store is None:
        return allowed
    try:
        raw = read_setting_value(store, "access_groups")
    except SettingsUnavailable:
        return allowed
    return allowed | _clean_groups(raw)


def resolve_step_groups(
    settings: Settings, store: DbSettingsStore | None = None
) -> set[str]:
    """Группы ручного конструктора шагов: контент-ключ allowed_ad_groups из БД
    (его правит руководитель ОК) плюс env-набор как bootstrap.

    БД недоступна — только env: просмотр состава группы не валим.
    """
    groups = set(settings.allowed_groups)
    if store is None:
        return groups
    try:
        raw = read_setting_value(store, "allowed_ad_groups")
    except SettingsUnavailable:
        return groups
    return groups | _clean_groups(raw)


def _env_sed_admin_groups() -> set[str]:
    """Группы администраторов СЭД из env (SED_ADMIN_GROUPS): запятая-разделитель,
    без пустых (то же правило, что settings.admin_groups из config.py).

    config.py вне границ правки backend-settings, поэтому фолбэк читаем из
    os.environ, а не из свойства Settings (контракт фолбэка тот же)."""
    return {
        g.strip()
        for g in os.environ.get("SED_ADMIN_GROUPS", "").split(",")
        if g.strip()
    }


def resolve_role_groups(
    settings: Settings, store: DbSettingsStore | None = None
) -> dict[str, set[str]]:
    """Группы ролей из настроек БД с фолбэком на env.

    Правило приоритета: значение ключа в БД есть — используем ТОЛЬКО его (env
    не дополняет, иначе отзыв группы админом в БД не действовал бы); ключа нет
    либо БД недоступна/не поддерживает чтение — env (SED_ADMIN_GROUPS/
    ADMIN_GROUPS/HR_GROUPS/HR_ADMIN_GROUPS). Одно чтение БД на все четыре
    ключа; значение не-массив считаем отсутствующим (фолбэк на env).
    """
    groups: dict[str, set[str]] = {
        "sed_admin_groups": _env_sed_admin_groups(),
        "admin_groups": set(settings.admin_groups),
        "hr_groups": set(settings.hr_groups),
        "hr_admin_groups": set(settings.hr_admin_groups),
    }
    if store is None:
        return groups
    try:
        raw = store.get_many(ROLE_GROUP_KEYS)
    except (SettingsUnavailable, AttributeError, TypeError):
        return groups
    for key in ROLE_GROUP_KEYS:
        value = _from_stored(raw.get(key))
        if isinstance(value, list):
            groups[key] = _clean_groups(value)
    return groups


def is_group_allowed_with_settings(
    settings: Settings, store: DbSettingsStore | None, group: str
) -> bool:
    """Группа доступна для конструктора шагов: в списке групп шагов
    (env + контент-ключ allowed_ad_groups) либо по префиксу групп владельцев
    шагов STEP_GROUP_PREFIX. Иначе группа входа в систему НЕ даёт.

    Один вызов — одно чтение настроек; в цикле считай набор заранее
    через resolve_step_groups.
    """
    name = (group or "").strip()
    if not name:
        return False
    if name in resolve_step_groups(settings, store):
        return True
    return bool(settings.STEP_GROUP_PREFIX) and name.startswith(
        settings.STEP_GROUP_PREFIX
    )


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

    # Виды документов (таблица doc_types, миграция 0003): единый источник правды,
    # контентного ключа settings больше нет. Наружу — кортеж полей справочника.
    _DOC_TYPES_COLUMNS = "code, name, is_active, sort_order, num"
    _DOC_TYPES_SELECT = text(
        f"SELECT {_DOC_TYPES_COLUMNS} FROM doc_types ORDER BY sort_order, code"
    )
    _DOC_TYPES_SELECT_BY_CODE = text(
        f"SELECT {_DOC_TYPES_COLUMNS} FROM doc_types WHERE code = :code"
    )
    _DOC_TYPES_INSERT = text(
        """
        INSERT INTO doc_types (name, is_active, sort_order, created_at, updated_at)
        VALUES (:name, true, :sort_order, now(), now())
        RETURNING code
        """
    )
    _DOC_TYPES_UPDATE = text(
        """
        UPDATE doc_types
        SET name = COALESCE(:name, name),
            is_active = COALESCE(:is_active, is_active),
            sort_order = COALESCE(:sort_order, sort_order),
            updated_at = now()
        WHERE code = :code
        """
    )
    _DOC_TYPES_DELETE = text("DELETE FROM doc_types WHERE code = :code")
    _DOC_TYPES_REFCOUNT = text(
        "SELECT count(*) FROM dismissal_requests WHERE doc_type_code = :code"
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

    @staticmethod
    def _doc_type_dict(row) -> dict:
        """Строка doc_types -> словарь контракта (code/name/is_active/sort_order/num)."""
        return {
            "code": row.code,
            "name": row.name,
            "is_active": row.is_active,
            "sort_order": row.sort_order,
            "num": row.num,
        }

    def list_doc_types(self) -> list[dict]:
        """Все виды документов (включая отключенные), по sort_order/code."""
        try:
            with self._session_factory() as session:
                rows = session.execute(self._DOC_TYPES_SELECT).all()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return [self._doc_type_dict(row) for row in rows]

    def get_doc_type(self, code: str) -> dict | None:
        """Вид документа по коду либо None (нет в таблице)."""
        try:
            with self._session_factory() as session:
                row = session.execute(
                    self._DOC_TYPES_SELECT_BY_CODE, {"code": code}
                ).first()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return self._doc_type_dict(row) if row is not None else None

    def create_doc_type(self, name: str, sort_order: int) -> dict:
        """Создать вид документа (активным по умолчанию); код — автонумерация
        (DEFAULT из последовательности doc_types_num_seq, миграция 0005)."""
        try:
            with self._session_factory() as session:
                row = session.execute(
                    self._DOC_TYPES_INSERT,
                    {"name": name, "sort_order": sort_order},
                ).first()
                session.commit()
        except IntegrityError as exc:
            raise DocTypeConflict("Вид документа не создан (конфликт кода)") from exc
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return self.get_doc_type(row.code)

    def update_doc_type(
        self,
        code: str,
        name: str | None,
        is_active: bool | None,
        sort_order: int | None,
    ) -> dict | None:
        """Частичное обновление вида документа (None-поля не меняются);
        None — кода нет в таблице."""
        try:
            with self._session_factory() as session:
                result = session.execute(
                    self._DOC_TYPES_UPDATE,
                    {"code": code, "name": name, "is_active": is_active, "sort_order": sort_order},
                )
                session.commit()
                if result.rowcount == 0:
                    return None
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return self.get_doc_type(code)

    def doc_type_in_use(self, code: str) -> bool:
        """Есть ли ссылки на вид в заявках (колонка doc_type_code)."""
        try:
            with self._session_factory() as session:
                count = session.execute(
                    self._DOC_TYPES_REFCOUNT, {"code": code}
                ).scalar()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return bool(count)

    def delete_doc_type(self, code: str) -> bool:
        """Физическое удаление вида документа; False — кода нет."""
        try:
            with self._session_factory() as session:
                result = session.execute(self._DOC_TYPES_DELETE, {"code": code})
                session.commit()
        except SQLAlchemyError as exc:
            raise SettingsUnavailable(f"Хранилище настроек недоступно: {exc}") from exc
        return result.rowcount > 0


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


class DocTypeItem(BaseModel):
    """Вид документа: строка таблицы doc_types (миграция 0003).

    Единый источник видов — таблица, контентного ключа settings больше нет."""

    code: str = Field(description="Код вида документа (уникален)")
    num: int = Field(description="Автономер вида (BIGSERIAL, миграция 0005)")
    name: str = Field(description="Название вида документа")
    is_active: bool = Field(description="Активен ли вид (активные — в селекте формы)")
    sort_order: int = Field(description="Порядок сортировки в селекте")


class DocTypeCreateIn(BaseModel):
    """Создание вида документа (только admin): код назначается автоматически
    (автонумерация, миграция 0005), name обязателен, code в запросе не передаётся."""

    name: str = Field(description="Название вида документа")
    sort_order: int | None = Field(
        default=None, description="Порядок сортировки (по умолчанию 0)"
    )


class DocTypeUpdateIn(BaseModel):
    """Правка вида документа (только admin): все поля опциональны (частичное)."""

    name: str | None = Field(default=None, description="Название вида документа")
    is_active: bool | None = Field(
        default=None, description="Активен ли вид (false — мягкое отключение)"
    )
    sort_order: int | None = Field(default=None, description="Порядок сортировки")


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
    phone_field: str = Field(
        default="", description="Поле телефона сотрудника (OData; пока не опубликовано)"
    )
    email_field: str = Field(
        default="", description="Поле e-mail сотрудника (OData; пока не опубликовано)"
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
    access_groups: list[str] | None = Field(
        default=None,
        description=(
            "AD-группы, дающие вход в систему (инфра-ключ, правит только админ; "
            "дополняют env ALLOWED_AD_GROUPS)"
        ),
    )
    admin_groups: list[str] | None = Field(
        default=None, description="Группы администраторов (фолбэк — env ADMIN_GROUPS)"
    )
    sed_admin_groups: list[str] | None = Field(
        default=None,
        description="Группы администраторов СЭД (фолбэк — env SED_ADMIN_GROUPS)",
    )
    hr_groups: list[str] | None = Field(
        default=None, description="Группы ОК (фолбэк — env HR_GROUPS)"
    )
    hr_admin_groups: list[str] | None = Field(
        default=None,
        description="Группы руководителей ОК (фолбэк — env HR_ADMIN_GROUPS)",
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
    schedule_enterprises_sync: dict | None = Field(
        default=None,
        description=(
            "Расписание синхронизации предприятий из 1С (регламент worker): "
            "mode interval/daily, уведомление notify/recipients/subject/body"
        ),
    )
    schedule_ad_links_sync: dict | None = Field(
        default=None,
        description=(
            "Расписание автосвязки 1С↔AD (регламент worker): "
            "mode interval/daily, уведомление notify/recipients/subject/body"
        ),
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


def _effective_role_values(settings: Settings, values: dict) -> None:
    """Эффективные значения ключей ролей в ответе GET/PUT /settings (на месте).

    В БД значение есть — отдаём его (даже пустой список: админ снял все группы),
    ключа нет — фолбэк на env (bootstrap). Порядок стабильный (сортировка).
    """
    env_fallback = resolve_role_groups(settings, None)
    for key in ROLE_GROUP_KEYS:
        stored = values.get(key)
        if isinstance(stored, list):
            values[key] = sorted(_clean_groups(stored))
        else:
            values[key] = sorted(env_fallback[key])


def _settings_dict(store: DbSettingsStore, settings: Settings) -> dict:
    """Типизированный словарь настроек из хранилища (None — ключа нет в БД).

    Ключи групп ролей отдаются эффективными (БД, иначе env) — админ видит то,
    что реально применяется; access_groups — как есть (нет ключа = None)."""
    raw = store.get_many(SETTINGS_KEYS)
    values = {key: _from_stored(raw.get(key)) for key in SETTINGS_KEYS}
    _effective_role_values(settings, values)
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
    settings: Settings = Depends(get_settings),
) -> dict:
    """Прикладные настройки админки: только admin, иначе 403; БД недоступна — 503.

    Группы ролей отдаются эффективными (значение из БД, иначе env)."""
    _require_admin(user)
    try:
        values = _settings_dict(store, settings)
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
    settings: Settings = Depends(get_settings),
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
        values = _settings_dict(store, settings)
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
) -> list[dict]:
    """Группы ручного конструктора шагов (Волна 1): ОК/админы, иначе 403;
    значения — из таблицы settings (ключ allowed_ad_groups), хардкода нет.
    Формат — объекты {id, name}: строки старого формата читаются как id=name."""
    _require_hr(user)
    try:
        raw = store.get("allowed_ad_groups")
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return _groups_with_names(_from_stored(raw))


# --- Виды документов (таблица doc_types, миграция 0003) ---
# Единый источник правды — таблица, контентного ключа settings больше нет.
# Чтение — любому аутентифицированному (селект формы заявки), правка — admin.

@router.get("/doc-types")
def list_doc_types(
    active_only: bool = True,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> list[dict]:
    """Виды документов из таблицы doc_types: любой аутентифицированный.

    active_only=true (по умолчанию) — только активные (селект формы);
    active_only=false — все, включая отключенные (редактор админки).
    БД недоступна — 503."""
    try:
        items = store.list_doc_types()
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if active_only:
        items = [item for item in items if item["is_active"]]
    return items


@router.post("/doc-types", status_code=status.HTTP_201_CREATED)
def create_doc_type(
    payload: DocTypeCreateIn,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Создать вид документа (только admin): код назначается автоматически
    (автонумерация), name — обязательное, code в запросе игнорируется."""
    _require_admin(user)
    try:
        item = store.create_doc_type(payload.name, payload.sort_order or 0)
    except DocTypeConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="doc_types.create",
            entity="doc_type",
            entity_id=item["code"],
        )
    )
    return item


@router.patch("/doc-types/{code}")
def update_doc_type(
    code: str,
    payload: DocTypeUpdateIn,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Изменить вид документа (только admin): частичное обновление полей.

    Мягкое отключение — PATCH с is_active=false: для видов, на которые есть
    ссылки в заявках, физическое удаление запрещено (см. DELETE)."""
    _require_admin(user)
    updates = payload.model_dump(mode="json", exclude_unset=True)
    try:
        item = store.update_doc_type(
            code,
            updates.get("name"),
            updates.get("is_active"),
            updates.get("sort_order"),
        )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Вид документа не найден",
            )
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="doc_types.update",
            entity="doc_type",
            entity_id=code,
            detail=",".join(updates),
        )
    )
    return item


@router.delete("/doc-types/{code}")
def delete_doc_type(
    code: str,
    user: CurrentUser = Depends(get_current_user),
    store: DbSettingsStore = Depends(get_settings_store),
) -> dict:
    """Удалить вид документа (только admin).

    Физическое удаление разрешено, только если на код нет ссылок в заявках
    (doc_type_code). Если ссылки есть — 409: историю заявок не рвём внешним
    ключом, для «занятых» видов доступно только мягкое отключение
    (PATCH /doc-types/{code} с is_active=false)."""
    _require_admin(user)
    try:
        if store.doc_type_in_use(code):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Вид документа используется заявками: физическое удаление "
                    "запрещено, доступно только мягкое отключение "
                    "(PATCH is_active=false)"
                ),
            )
        if not store.delete_doc_type(code):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Вид документа не найден",
            )
    except SettingsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="doc_types.delete",
            entity="doc_type",
            entity_id=code,
        )
    )
    return {"deleted": code}

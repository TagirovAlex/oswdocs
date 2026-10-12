# Справочник прав доступа (волна F2): единица прав — блок функции, а не роль
# (решение человека 2026-10-11, спека — PLAN.md «МОДЕЛЬ ПРАВ ДОСТУПА»).
#
# Хранение — таблицы perm_blocks/perm_templates/perm_template_blocks/
# perm_template_groups из миграции 0020. Права пользователя = ОБЪЕДИНЕНИЕ блоков
# всех шаблонов, чьи группы AD пересекаются с его memberOf (несколько шаблонов —
# объединение, как решил человек). Шаблон «owner» особый: владение шагом
# определяется назначением шага, а не членством в группе, поэтому группы у него
# пустые намеренно, а его блоки (список заявок/карточка/отметки) положены любому
# аутентифицированному пользователю.
#
# ОБЛАСТЬ ВИДИМОСТИ («только свои заявки», «только свой шаг») резолва НЕ касается:
# она остаётся в существующем коде (requests._is_participant/_is_step_viewer,
# documents._can_view) — здесь проверяется только право на функцию.
#
# FAIL-CLOSED: справочник недоступен (ошибка БД/миграции) — ни один блок не
# выдаётся (PermissionsUnavailable -> 503, а не тихий возврат к ролям).
# ВХОД в систему от справочника прав не зависит: он остаётся на access_groups/
# env (см. settings_routes.resolve_allowed_groups), поэтому при недоступном
# справочнике пользователь входит в систему, но функции закрыты. Исключение —
# роль для фронта (/auth/me): она выводится из совпавших шаблонов по приоритету
# sed_admin > admin > hr_admin > hr, а если не совпал ни один (или справочник
# недоступен) — owner, то есть минимальная роль. Контракт /auth/me не меняется.
#
# Настройки (имена групп, состав блоков) в коде не зашиты: справочник — источник
# правды, коды блоков ниже — стабильные идентификаторы (как коды событий/ролей),
# по ним гейты и ссылаются на строки справочника.
#
# Хранилище: DbPermissionsStore (Postgres, транзакционно, ошибки БД ->
# PermissionsUnavailable) и InMemoryPermissionsStore (офлайн-прогонов/тестов).
# Ошибка запроса данных (неизвестный блок в составе шаблона) — HTTPException
# 422 прямо в хранилище: это ошибка данных, а не хранилища, и текст должен быть
# один независимо от вызывающего (как в routing_store).

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator, Protocol

from fastapi import Depends, HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings

# --- Стабильные идентификаторы блоков (значения — строки справочника) ---

BLOCK_REQUESTS_LIST = "requests_list"
BLOCK_REQUEST_CARD = "request_card"
BLOCK_REQUEST_CREATE = "request_create"
BLOCK_STEP_DECIDE = "step_decide"
BLOCK_PRINT_BLANK = "print_blank"
BLOCK_REQUEST_DELETE = "request_delete"
BLOCK_EMPLOYEE_DIRECTORY = "employee_directory"
BLOCK_LINK_MATCH = "link_match"
BLOCK_SETTINGS_CONTENT = "settings_content"
BLOCK_SETTINGS_INFRA = "settings_infra"
BLOCK_SETTINGS_BLANKS = "settings_blanks"
BLOCK_ARCHIVE_BACKUPS = "archive_backups"
BLOCK_SED_ADMIN_PANEL = "sed_admin_panel"

# Шаблон «владелец шага»: по устройству без групп AD (владение шагом — от
# назначения шага), поэтому его блоки положены любому аутентифицированному.
BASE_TEMPLATE_CODE = "owner"

# Состав шаблона «владелец шага» ограничен этими блоками (миграция 0020):
# он совпадает ВСЕГДА, поэтому любой лишний блок в нём раздался бы всем
# вошедшим — от просмотра списка до инфраструктурных настроек и бэкапов.
# Проверка на сервере, а не только в UI: одна ошибка администратора не должна
# раздавать права всем.
BASE_TEMPLATE_BLOCKS: frozenset[str] = frozenset({
    BLOCK_REQUESTS_LIST,
    BLOCK_REQUEST_CARD,
    BLOCK_STEP_DECIDE,
})

# Роль для фронта выводится из совпавших шаблонов по этому приоритету
# (старший сверху); ни один не совпал — владелец шага, как и раньше.
ROLE_PRIORITY: tuple[str, ...] = ("sed_admin", "admin", "hr_admin", "hr")
ROLE_FALLBACK = "owner"

# Текст отказа по умолчанию: в гейте зовётся с понятным описанием функции.
DEFAULT_DENIED_DETAIL = "Недостаточно прав: справочник прав доступа не даёт этой функции"


class PermissionsUnavailable(Exception):
    """Справочник прав доступа (БД) недоступен — блок НЕ выдаётся (fail-closed)."""


@dataclass(frozen=True)
class PermissionBlock:
    """Строка справочника блоков (perm_blocks)."""

    code: str
    title: str
    description: str | None = None
    sort_order: int = 0
    active: bool = True


@dataclass(frozen=True)
class PermissionTemplate:
    """Шаблон прав: код, состав блоков и группы AD (perm_templates + 2 таблицы)."""

    code: str
    title: str
    description: str | None = None
    protected: bool = False
    sort_order: int = 0
    blocks: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionCatalog:
    """Снимок справочника целиком: блоки и шаблоны с составом и группами."""

    blocks: tuple[PermissionBlock, ...] = ()
    templates: tuple[PermissionTemplate, ...] = ()

    def block_codes(self) -> set[str]:
        """Коды блоков справочника (для проверки состава шаблона)."""
        return {block.code for block in self.blocks}

    def template(self, code: str) -> PermissionTemplate | None:
        """Шаблон по коду либо None (нет в справочнике)."""
        for template in self.templates:
            if template.code == code:
                return template
        return None

    def with_template(self, template: PermissionTemplate) -> PermissionCatalog:
        """Копия справочника с заменённым шаблоном (проверка «а не потеряю ли
        доступ» ДО записи — без обращения к БД)."""
        return replace(
            self,
            templates=tuple(
                template if item.code == template.code else item
                for item in self.templates
            ),
        )


@dataclass(frozen=True)
class UserPermissions:
    """Права пользователя: блоки, совпавшие шаблоны и роль для /auth/me."""

    blocks: frozenset[str]
    templates: tuple[str, ...]
    role: str


def clean_codes(values: Iterable[object]) -> tuple[str, ...]:
    """Коды/имена из payload: обрезка краёв, пустые и повторы уходят."""
    codes: list[str] = []
    for value in values or ():
        code = str(value or "").strip()
        if code and code not in codes:
            codes.append(code)
    return tuple(codes)


class PermissionsStore(Protocol):
    """Интерфейс хранилища справочника прав: единый для Postgres и памяти."""

    def load_catalog(self) -> PermissionCatalog:
        """Весь справочник одним снимком (блоки, шаблоны, состав, группы)."""
        ...  # pragma: no cover

    def replace_template(
        self,
        code: str,
        block_codes: Sequence[str],
        group_names: Sequence[str],
    ) -> PermissionTemplate | None:
        """Состав шаблона целиком (блоки и группы) одной транзакцией.

        None — шаблона с таким кодом нет; неизвестный блок — 422."""
        ...  # pragma: no cover


class DbPermissionsStore:
    """Справочник прав в Postgres (миграция 0020), слой доступа — raw SQL.

    Ошибки БД оборачиваются в PermissionsUnavailable (fail-closed: блок не
    выдаётся), как RoutingUnavailable в routing_store и SettingsUnavailable в
    settings_routes. Запись — только в свои таблицы справочника.
    """

    _SELECT_BLOCKS = text(
        """
        SELECT code, title, description, sort_order, active
        FROM perm_blocks
        ORDER BY sort_order, code
        """
    )
    _SELECT_TEMPLATES = text(
        """
        SELECT code, title, description, protected, sort_order
        FROM perm_templates
        ORDER BY sort_order, code
        """
    )
    _SELECT_TEMPLATE_BLOCKS = text(
        """
        SELECT t.code AS template_code, b.code AS block_code
        FROM perm_template_blocks tb
        JOIN perm_templates t ON t.id = tb.template_id
        JOIN perm_blocks b ON b.id = tb.block_id
        """
    )
    _SELECT_TEMPLATE_GROUPS = text(
        """
        SELECT t.code AS template_code, g.group_name
        FROM perm_template_groups g
        JOIN perm_templates t ON t.id = g.template_id
        ORDER BY t.code, g.group_name
        """
    )
    _SELECT_TEMPLATE_ID = text("SELECT id FROM perm_templates WHERE code = :code")
    _SELECT_BLOCK_IDS = text(
        """
        SELECT code, id FROM perm_blocks
        WHERE code = ANY(CAST(:codes AS text[]))
        """
    )
    _DELETE_TEMPLATE_BLOCKS = text(
        "DELETE FROM perm_template_blocks WHERE template_id = :template_id"
    )
    _DELETE_TEMPLATE_GROUPS = text(
        "DELETE FROM perm_template_groups WHERE template_id = :template_id"
    )
    _INSERT_TEMPLATE_BLOCK = text(
        """
        INSERT INTO perm_template_blocks (template_id, block_id)
        VALUES (:template_id, :block_id)
        """
    )
    _INSERT_TEMPLATE_GROUP = text(
        """
        INSERT INTO perm_template_groups (template_id, group_name)
        VALUES (:template_id, :group_name)
        ON CONFLICT (template_id, group_name) DO NOTHING
        """
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    @contextmanager
    def _session(self) -> Iterator[object]:
        """Сессия с обёрткой ошибок БД в PermissionsUnavailable (fail-closed)."""
        try:
            with self._session_factory() as session:
                yield session
        except SQLAlchemyError as exc:
            raise PermissionsUnavailable(
                f"Справочник прав доступа недоступен: {exc}"
            ) from exc

    def load_catalog(self) -> PermissionCatalog:
        """Снимок справочника: четыре выборки в одной сессии (без N+1)."""
        with self._session() as session:
            block_rows = session.execute(self._SELECT_BLOCKS).all()
            template_rows = session.execute(self._SELECT_TEMPLATES).all()
            template_blocks = session.execute(self._SELECT_TEMPLATE_BLOCKS).all()
            template_groups = session.execute(self._SELECT_TEMPLATE_GROUPS).all()
        blocks_by_template: dict[str, list[str]] = {}
        for row in template_blocks:
            blocks_by_template.setdefault(row.template_code, []).append(row.block_code)
        groups_by_template: dict[str, list[str]] = {}
        for row in template_groups:
            groups_by_template.setdefault(row.template_code, []).append(row.group_name)
        return PermissionCatalog(
            blocks=tuple(
                PermissionBlock(
                    code=row.code,
                    title=row.title,
                    description=row.description,
                    sort_order=int(row.sort_order or 0),
                    active=bool(row.active),
                )
                for row in block_rows
            ),
            templates=tuple(
                PermissionTemplate(
                    code=row.code,
                    title=row.title,
                    description=row.description,
                    protected=bool(row.protected),
                    sort_order=int(row.sort_order or 0),
                    blocks=tuple(sorted(blocks_by_template.get(row.code, ()))),
                    groups=tuple(sorted(groups_by_template.get(row.code, ()))),
                )
                for row in template_rows
            ),
        )

    def replace_template(
        self,
        code: str,
        block_codes: Sequence[str],
        group_names: Sequence[str],
    ) -> PermissionTemplate | None:
        """Заменить состав шаблона (блоки и группы) одной транзакцией.

        Неизвестный блок — 422 (ошибка данных, состав не меняется); отсутствие
        шаблона — None (роутер отдаёт 404)."""
        wanted = clean_codes(block_codes)
        groups = clean_codes(group_names)
        with self._session() as session:
            row = session.execute(self._SELECT_TEMPLATE_ID, {"code": code}).first()
            if row is None:
                return None
            template_id = int(row[0])
            known: dict[str, int] = {}
            if wanted:
                known = {
                    item.code: int(item.id)
                    for item in session.execute(
                        self._SELECT_BLOCK_IDS, {"codes": list(wanted)}
                    ).all()
                }
            unknown = [block for block in wanted if block not in known]
            if unknown:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Неизвестные блоки прав: %s" % ", ".join(unknown),
                )
            session.execute(
                self._DELETE_TEMPLATE_BLOCKS, {"template_id": template_id}
            )
            session.execute(
                self._DELETE_TEMPLATE_GROUPS, {"template_id": template_id}
            )
            for block_code in wanted:
                session.execute(
                    self._INSERT_TEMPLATE_BLOCK,
                    {"template_id": template_id, "block_id": known[block_code]},
                )
            for group_name in groups:
                session.execute(
                    self._INSERT_TEMPLATE_GROUP,
                    {"template_id": template_id, "group_name": group_name},
                )
            session.commit()
        return self.load_catalog().template(code)


class InMemoryPermissionsStore:
    """Офлайн-хранилище справочника прав (dict), интерфейс PermissionsStore.

    Пустое хранилище = «справочника нет»: резолв даёт пустые права (fail-closed).
    Наполняется фикстурой тестов зеркалом состояния после миграции 0020."""

    def __init__(self, catalog: PermissionCatalog | None = None) -> None:
        self._catalog = catalog or PermissionCatalog()

    def load_catalog(self) -> PermissionCatalog:
        return self._catalog

    def set_catalog(self, catalog: PermissionCatalog) -> None:
        """Подменить снимок справочника целиком (тесты/fixtures)."""
        self._catalog = catalog

    def replace_template(
        self,
        code: str,
        block_codes: Sequence[str],
        group_names: Sequence[str],
    ) -> PermissionTemplate | None:
        current = self._catalog.template(code)
        if current is None:
            return None
        wanted = clean_codes(block_codes)
        unknown = [block for block in wanted if block not in self._catalog.block_codes()]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Неизвестные блоки прав: %s" % ", ".join(unknown),
            )
        if current.code == BASE_TEMPLATE_CODE:
            # Совпадает всегда → лишний блок раздался бы всем вошедшим.
            extra = sorted(set(wanted) - BASE_TEMPLATE_BLOCKS)
            if extra:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "Шаблон «владелец шага» совпадает с любым вошедшим, "
                        "поэтому в нём только блоки: %s. Не лишние: %s"
                        % (
                            ", ".join(sorted(BASE_TEMPLATE_BLOCKS)),
                            ", ".join(extra),
                        )
                    ),
                )
        if current.protected:
            # protected = шаблон администратора нельзя очистить (миграция 0020):
            # иначе можно было бы лишить систему последнего администратора, и
            # правило «нельзя снять права у себя» (keeps_block_after_change) уже
            # не спасло бы от потери доступа ко всем.
            if not wanted:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Защищённый шаблон не может остаться без блоков прав",
                )
        updated = PermissionTemplate(
            code=current.code,
            title=current.title,
            description=current.description,
            protected=current.protected,
            sort_order=current.sort_order,
            blocks=wanted,
            groups=clean_codes(group_names),
        )
        self._catalog = self._catalog.with_template(updated)
        return updated


_db_store: DbPermissionsStore | None = None


def get_permissions_store(settings: Settings = Depends(get_settings)) -> PermissionsStore:
    """Боевое хранилище справочника прав (Postgres): один движок на процесс.

    В офлайн-тестах подменяется in-memory через dependency_overrides (как
    get_settings_store/get_routing_store)."""
    global _db_store
    if _db_store is None:
        _db_store = DbPermissionsStore(settings.DATABASE_URL)
    return _db_store


def permissions_store_or_none(settings: Settings | None = None) -> PermissionsStore | None:
    """Хранилище прав для гейтов вне зависимостей FastAPI.

    Подмена берётся из dependency_overrides (офлайн-тесты), иначе боевое
    хранилище. Ошибка создания — None: гейт трактует это как «справочник
    недоступен» и закрывает доступ (fail-closed)."""
    try:
        from .main import app  # локальный импорт: приложение импортирует роутеры

        overrides = getattr(app, "dependency_overrides", {})
        factory = overrides.get(get_permissions_store)
        if factory is not None:
            return factory()
        return get_permissions_store(settings or get_settings())
    except Exception:
        return None


# --- Резолв прав ---

def resolve_permissions(
    groups: Sequence[str], catalog: PermissionCatalog
) -> UserPermissions:
    """Права по группам memberOf: объединение блоков совпавших шаблонов.

    Совпадение — пересечение групп пользователя с группами шаблона. Шаблон
    владельца шага (код owner) совпадает всегда: владение шагом определяется
    назначением, а не группой, поэтому его блоки положены любому
    аутентифицированному. Неактивные блоки справочника не выдаются.
    Область видимости («только свои заявки/свой шаг») здесь не проверяется —
    она остаётся в существующем коде участников заявки."""
    group_set = {str(group or "").strip() for group in groups or ()}
    active = {block.code for block in catalog.blocks if block.active}
    matched: list[str] = []
    codes: set[str] = set()
    for template in sorted(catalog.templates, key=lambda item: item.sort_order):
        if template.code != BASE_TEMPLATE_CODE:
            if not set(template.groups) & group_set:
                continue
        matched.append(template.code)
        codes.update(template.blocks)
    blocks = frozenset(code for code in codes if code in active)
    return UserPermissions(
        blocks=blocks,
        templates=tuple(matched),
        role=role_for_templates(matched),
    )


def role_for_templates(template_codes: Sequence[str]) -> str:
    """Роль для /auth/me: старший из совпавших шаблонов по ROLE_PRIORITY."""
    matched = set(template_codes or ())
    for code in ROLE_PRIORITY:
        if code in matched:
            return code
    return ROLE_FALLBACK


def role_for_groups(
    groups: Sequence[str],
    settings: Settings | None = None,
    store: PermissionsStore | None = None,
) -> str:
    """Роль пользователя для /auth/me по его группам memberOf.

    Справочник недоступен — минимальная роль (владелец шага): контракт /auth/me
    не ломается, а права при этом всё равно закрыты (fail-closed на гейтах)."""
    catalog_store = (
        store if store is not None else permissions_store_or_none(settings)
    )
    if catalog_store is None:
        return ROLE_FALLBACK
    try:
        return resolve_permissions(groups, catalog_store.load_catalog()).role
    except PermissionsUnavailable:
        return ROLE_FALLBACK


def user_permissions(
    user, store: PermissionsStore | None = None, settings: Settings | None = None
) -> UserPermissions:
    """Права пользователя по справочнику; ошибка БД — PermissionsUnavailable.

    Справочник недоступен (None/ошибка) — исключение, а не пустые права «по
    умолчанию»: гейт превращает его в отказ (fail-closed)."""
    catalog_store = store if store is not None else permissions_store_or_none(settings)
    if catalog_store is None:
        raise PermissionsUnavailable("Справочник прав доступа недоступен")
    groups = getattr(user, "groups", None) or []
    return resolve_permissions(groups, catalog_store.load_catalog())


def require_block(
    user,
    code: str,
    detail: str = DEFAULT_DENIED_DETAIL,
    store: PermissionsStore | None = None,
    settings: Settings | None = None,
) -> None:
    """Единая проверка доступа по коду блока; нет блока — 403.

    Справочник недоступен — 503, а не 403 и не 500: доступ закрыт (fail-closed),
    но видно, что это недоступность хранилища, а не «нет прав»."""
    try:
        blocks = user_permissions(user, store, settings).blocks
    except PermissionsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if code not in blocks:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail or DEFAULT_DENIED_DETAIL,
        )


def has_block(
    user,
    code: str,
    store: PermissionsStore | None = None,
    settings: Settings | None = None,
) -> bool:
    """Есть ли у пользователя блок права — БЕЗ отказа (для показа, а не гейта).

    Там, где нет блока, ничего не показываем, но 403 пользователю не возвращаем:
    это витрина (счётчики папок, вкладки настроек), а не операция. Справочник
    недоступен — False: показывать нечего, показывать будем после починки."""
    try:
        return code in user_permissions(user, store, settings).blocks
    except PermissionsUnavailable:
        return False


def keeps_block_after_change(
    catalog: PermissionCatalog,
    user_groups: Sequence[str],
    changed: PermissionTemplate,
    code: str,
) -> bool:
    """Останется ли блок у пользователя, если шаблон заменить на changed.

    Нужно правилу «нельзя снять права у себя» (409): считаем права на копии
    справочника ДО записи, поэтому отказ не оставляет состав наполовину
    изменённым."""
    before = resolve_permissions(user_groups, catalog)
    if code not in before.blocks:
        # Блока и так не было — снять его (добавить) нечего.
        return True
    return code in resolve_permissions(user_groups, catalog.with_template(changed)).blocks
# Заявки на увольнение и маршруты согласования (волна B1, offline).
# Хранилище — зависимость get_requests_store: in-memory на офлайне/в тестах,
# Postgres (DbRequestsStore) на стенде; логика эндпоинтов не зависит от
# реализации (интерфейс RequestsStore в requests_store.py).
# Прикладные настройки (position_escalation, approval_ttl_days,
# require_comment) — из таблицы settings на стенде (волна A1);
# здесь — injectable-заглушка get_route_settings (дефолты нейтральные,
# реальные значения — только через settings/тестовые оверрайды).
# Статусы README п.1: Черновик → На согласовании → На доработке →
# Согласовано → К исполнению → Завершено / Отклонено / Отозвано.

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, SedAdminUser, get_current_user
from .docs import request_url
from .employees import get_ad_reader
from .employee_sync import (
    EmployeeSyncStore,
    get_employee_sync_store,
)
from .link_store import get_links_store
from .mailer import (
    EVENT_ASSIGNED,
    EVENT_CLOSED,
    EVENT_RETURNED,
    MailQueue,
    enqueue_event,
    get_mail_queue,
    recipient_mail,
    resolve_smtp_from,
    step_owner_mails,
)
from .requests_store import (
    InMemoryRequestsStore,
    RequestsStore,
    RequestsUnavailable,
    get_requests_store,
)
from .routing import (
    REASON_DEFAULT_PROFILE,
    REASON_PROFILE_NOT_FOUND,
    REASON_SERVICE_NOT_REGISTERED,
    RoutePick,
    _join_reason,
    apply_dismissals_and_additions,
    pick_blank_steps,
    pick_profile,
    stage_executor,
)
from .routing_store import DbRoutingStore, RoutingUnavailable, get_routing_store
from .settings_routes import (
    DbSettingsStore,
    _groups_with_names,
    get_settings_store,
    read_setting_value,
)

router = APIRouter(tags=["requests"])

# --- Статусы заявки (строки как в README п.1) ---
DRAFT = "Черновик"
IN_APPROVAL = "На согласовании"
REWORK = "На доработке"
AGREED = "Согласовано"
TO_EXECUTION = "К исполнению"
DONE = "Завершено"
REJECTED = "Отклонено"
REVOKED = "Отозвано"

# --- Статусы шага ---
STEP_PENDING = "ожидает"
STEP_APPROVED = "согласован"
STEP_REJECTED = "отклонен"
STEP_RETURNED = "возвращен"
STEP_EXPIRED = "просрочен"

# --- Резолверы шагов (подмножество скилла approval-templates) ---
RESOLVERS = (
    "ad_direct_manager",
    "ad_manager_N",
    "by_position",
    "by_group",
    "by_user",
    "hr_owner",
)


class RouteSettings(BaseModel):
    """Прикладные настройки маршрута (на стенде — строка таблицы settings).

    Ключи templates/position_to_category сняты (решение человека 2026-10-08,
    самостоятельный бланк): маршрут задаёт бланк (его шаги) либо ручной
    конструктор blocks/steps, а подбор по службе — запасной путь за настройкой
    blank_autopick."""

    approval_ttl_days: int = Field(default=3, description="TTL отметок шагов в днях")
    position_escalation: dict[str, int] = Field(
        default_factory=dict, description="Должность → часы эскалации (пусто=выкл)"
    )


# Глобал offline-заглушки (стенд подменит чтением settings из БД).
_ROUTE_SETTINGS = RouteSettings()


def get_route_settings() -> RouteSettings:
    """Настройки маршрута (зависимость для оверрайда в тестах/на стенде)."""
    return _ROUTE_SETTINGS


class StepSpec(BaseModel):
    """Шаг ручного конструктора (все исполнители — группы/резолверы из settings)."""

    owner_group: str | None = Field(
        default=None, description="Группа-владелец шага (для sam-шага подставится логин)"
    )
    resolver: str = "by_group"
    require_comment: bool = False
    assignee: str | None = Field(
        default=None, description="Персональный исполнитель (замена руководителя)"
    )
    sam: str | None = Field(
        default=None, description="Исполнитель AD (по выбору ОК): резолвер by_user, owner_group = sam"
    )

    @model_validator(mode="after")
    def _check_executor(self) -> "StepSpec":
        """Шаг обязан иметь исполнителя: sam или assignee (персональный) либо
        owner_group (групповой, resolver=by_group).

        Проверка в схеме, а не в _build_steps: без неё шаг без исполнителя доходил
        до _Step.owner_group (обязателен) и ронял POST /requests с 500 вместо 422.
        assignee — равнозначный персональный исполнитель (замена руководителя по
        шагу). Пустые значения (пробелы) приравниваются к отсутствующим, поэтому
        нормализуются в None — _build_steps работает с ними как с отсутствующими."""
        self.owner_group = (self.owner_group or "").strip() or None
        self.sam = (self.sam or "").strip() or None
        self.assignee = (self.assignee or "").strip() or None
        if not (self.sam or self.assignee or self.owner_group):
            raise ValueError(
                "Шаг маршрута: укажите группу-владельца (owner_group)"
                " или персонального исполнителя (sam/assignee)"
            )
        return self


class RouteBlockSpec(BaseModel):
    """Блок маршрута: последовательный (строго по порядку) либо параллельный
    (шаги блока — одновременно, любой порядок отметок)."""

    mode: Literal["sequential", "parallel"] = "sequential"
    steps: list[StepSpec] = Field(description="Шаги блока")


class CreateRequestIn(BaseModel):
    """Создание заявки от ОК (истина по полям 1С, категория — решает ОК)."""

    enterprise: str
    fio: str = Field(description="ФИО сотрудника (из 1С при наличии баз, иначе вводит ОК)")
    tab_num: str = Field(description="Табельный номер (ПДн, только ОК)")
    department: str = Field(description="Служба увольняемого (поле 1С)")
    position: str = Field(description="Должность увольняемого (поле 1С)")
    category: str | None = Field(
        default=None, description="Категория заявки (задаёт ОК; из справочника не выводится)"
    )
    subject: str = Field(description="Тема заявки (карточка)")
    content: str = Field(description="Содержание заявки (карточка)")
    doc_type_code: str | None = Field(
        default=None,
        description="Вид документа из doc_types; передан — обязан существовать",
    )
    blank_id: int | None = Field(
        default=None,
        description=(
            "Бланк из справочника blanks (выбирает ОК); в режиме auto его шаги "
            "становятся маршрутом заявки, в custom — не обязателен"
        ),
    )
    manager: str | None = Field(
        default=None, description="Замена руководителя (sam) для шагов ad_direct_manager"
    )
    steps: list[StepSpec] | None = Field(
        default=None, description="Ручной маршрут (обязателен, если шаблона нет)"
    )
    blocks: list[RouteBlockSpec] | None = Field(
        default=None, description="Маршрут блоками (приоритетнее steps)"
    )
    route_mode: Literal["auto", "custom"] = Field(
        default="auto",
        description=(
            "auto — маршрут собирается из справочников по службе сотрудника; "
            "custom — ручной маршрут (blocks/steps). Явные blocks/steps всегда "
            "приоритетнее: это осознанно заданный ОК маршрут"
        ),
    )
    dismissed_stages: list[str] = Field(
        default_factory=list, description="Коды этапов, снятых ОК из маршрута (auto)"
    )
    dismissed_step_orders: list[int] = Field(
        default_factory=list,
        description=(
            "Номера (step_order) шагов бланка, снятых ОК из маршрута (auto); "
            "у шага бланка нет кода этапа, поэтому его снимают по номеру"
        ),
    )
    added_stages: list[str] = Field(
        default_factory=list, description="Коды этапов, добавленных ОК в конец маршрута (auto)"
    )
    ad_sam: str | None = Field(
        default=None, description="Логин AD сотрудника (если известен) — источник данных карточки"
    )
    base_code: str | None = Field(
        default=None, description="Код базы 1С сотрудника — для поиска связки 1С↔AD"
    )


class DecisionIn(BaseModel):
    """Отметка владельца шага (решение + дата + автор + комментарий)."""

    decision: Literal["approve", "reject", "return"]
    comment: str | None = None


class UpdateRequestIn(BaseModel):
    """Правка полей карточки (тема/содержание/вид документа) — только sed_admin."""

    subject: str | None = None
    content: str | None = None
    doc_type_code: str | None = None


class RollbackIn(BaseModel):
    """Откат маршрута на шаг и все последующие — только sed_admin."""

    to_step_id: str = Field(description="order шага, с которого переоткрыть маршрут")


class CommentIn(BaseModel):
    """Новый комментарий по заявке (автор/участники/ОК)."""

    body: str = Field(description="Текст комментария (не пустой)")
    step_id: str | None = Field(
        default=None, description="Привязка к шагу (order) либо null — к заявке"
    )
    kind: Literal["request", "step"] = "request"


class CommentOut(BaseModel):
    """Комментарий заявки (author — sam-логин, ПДн-обрезка не требуется)."""

    id: int
    request_id: str
    author: str | None = None
    body: str
    at: str
    kind: str
    step_id: str | None = None


class StepsReplaceIn(BaseModel):
    """Правка маршрута по ходу (только разрешенная группа, все — в audit_log)."""

    steps: list[StepSpec] = Field(default_factory=list)
    blocks: list[RouteBlockSpec] | None = Field(
        default=None, description="Правка блочного маршрута (приоритетнее steps)"
    )
    reason: str | None = Field(default=None, description="Причина правки (без ПДн)")
    manager: str | None = Field(
        default=None, description="Замена руководителя для шагов ad_direct_manager"
    )


class StepApprovalOut(BaseModel):
    """Отметка ответственного по шагу (миграция 0013).

    sam — это sAMAccountName, поэтому чужие логины непривилегированному не
    отдаются (sam=None у остальных), как assignee/done_by в StepOut."""

    sam: str | None = None
    at: str = ""
    decision: str = ""
    comment: str | None = None


class StepOut(BaseModel):
    """Шаг заявки (исполнители — группы/sam; ФИО согласующего — из AD)."""

    order: int
    owner_group: str
    resolver: str
    assignee: str | None = None
    owner_name: str | None = Field(
        default=None,
        description=(
            "Наименование владельца шага: персональный — ФИО из AD, "
            "групповой — наименование группы из settings (если известно)"
        ),
    )
    owner_duty: str | None = Field(
        default=None,
        description="Должность персонального исполнителя из AD (title); у группового — None",
    )
    employee_key: str | None = Field(
        default=None,
        description="Составной ключ сотрудника шага (enterprise|base_code|tab_num), только привилегированным",
    )
    emp_enterprise: str | None = Field(default=None)
    emp_base_code: str | None = Field(default=None)
    emp_tab_num: str | None = Field(default=None)
    status: str
    require_comment: bool = False
    expires_at: str
    done_by: str | None = None
    done_at: str | None = None
    comment: str | None = None
    can_act: bool = Field(
        default=False,
        description="Может ли текущий пользователь поставить отметку прямо сейчас",
    )
    stage_title: str | None = Field(
        default=None, description="Наименование этапа маршрута (снимок из справочника)"
    )
    stage_code: str | None = Field(
        default=None, description="Код этапа маршрута (снимок из справочника)"
    )
    # Несколько ответственных у шага (миграция 0013): снимок логинов, режим шага
    # и собранные отметки. assignee — первый из assignees (прежний UI/выборки).
    assignees: list[str] = Field(
        default_factory=list,
        description="Логины ответственных шага (снимок на момент выдачи заявки)",
    )
    approvals: list[StepApprovalOut] = Field(
        default_factory=list, description="Собранные отметки ответственных по шагу"
    )
    approval_mode: str | None = Field(
        default=None,
        description="Режим шага: sequential (закрывают все ответственные) либо parallel (любой)",
    )
    optional: bool = Field(
        default=True,
        description="Шаг можно снять из маршрута (снимок признака из справочника)",
    )
    approved_count: int = Field(default=0, description="Сколько ответственных согласовали")
    assignee_count: int = Field(default=0, description="Сколько ответственных у шага")


class RequestOut(BaseModel):
    """Заявка (ПДн tab_num — только ОК/админам, см. _public_view)."""

    id: str
    status: str
    route_origin: str
    profile_id: int | None = Field(
        default=None, description="Профиль маршрута заявки (снимок подбора; null — ручной маршрут)"
    )
    profile_name: str | None = Field(
        default=None, description="Наименование профиля маршрута (для показа в UI)"
    )
    service_id: int | None = Field(default=None, description="Служба заявки из справочника")
    service_name: str | None = Field(
        default=None, description="Служба заявки (значение department при подборе по справочникам)"
    )
    is_manager: bool = Field(
        default=False,
        description="Руководитель ли сотрудник заявки (по карточке AD); в БД не хранится",
    )
    enterprise: str | None = None
    enterprise_name: str | None = Field(
        default=None, description="Название предприятия из settings.enterprises"
    )
    employee_key: str | None = Field(default=None, description="Составной ключ сотрудника заявки, только привилегированным")
    tab_num: str | None = None
    fio: str | None = None
    department: str
    position: str
    category: str | None = None
    subject: str | None = None
    content: str | None = None
    doc_type_code: str | None = None
    doc_type_name: str | None = Field(
        default=None,
        description="Наименование вида документа (для показа вместо кода)",
    )
    blank_id: int | None = Field(
        default=None, description="Бланк заявки (снимок выбора ОК, null — без бланка)"
    )
    blank_name: str | None = Field(
        default=None, description="Наименование бланка на момент выдачи заявки (снимок)"
    )
    blank_version: int | None = Field(
        default=None, description="Версия состава шагов бланка на момент выдачи (снимок)"
    )
    blank_header_html: str | None = Field(
        default=None, description="Шапка бланка на момент выдачи (HTML, снимок)"
    )
    blank_footer_lines: list[str] = Field(
        default_factory=list, description="Подвал бланка на момент выдачи (строки, снимок)"
    )
    escalation_hours: int | None = None
    created_by: str | None = Field(
        default=None,
        description="Автор заявки (sAMAccountName) — только привилегированным",
    )
    steps: list[StepOut]


class _Step(BaseModel):
    """Внутренний шаг (хранится в памяти, в БД — таблица request_steps)."""

    order: int
    owner_group: str
    resolver: str = "by_group"
    assignee: str | None = None
    status: str = STEP_PENDING
    require_comment: bool = False
    expires_at: datetime
    done_by: str | None = None
    done_at: datetime | None = None
    comment: str | None = None
    # Снимок этапа справочника (шаги маршрута, собранного по профилю): снимок
    # нужен печати/карточке и не пересобирается при правке справочников.
    # owner_kind в БД не хранится (колонки нет) — после перечитки заявки из
    # Postgres признак теряется, права по реестру этапа тогда не проверяются.
    owner_kind: str | None = None
    stage_id: int | None = None
    stage_code: str | None = None
    stage_title: str | None = None
    stage_lines: list[str] = Field(default_factory=list)
    profile_step_id: int | None = None
    # Снимок нескольких ответственных шага (миграция 0013): логины ответственных
    # и режим шага плюс собранные отметки [{sam, at, decision, comment}].
    # assignee — первый из assignees (обратная совместимость UI/выборок/писем);
    # пустой assignees — групповой шаг (действует owner_group) или шаг реестра
    # без состава. Правка даётся по mode: parallel закрывает любая отметка,
    # sequential — все ответственные (режим по умолчанию).
    assignees: list[str] = Field(default_factory=list)
    approval_mode: str | None = None
    approvals: list[dict] = Field(default_factory=list)
    # Снимок признака «шаг можно снять» из справочника (optional шага бланка или
    # этапа профиля; миграция 0015 хранит его в request_steps). Правка справочника
    # уже выданную заявку не меняет. Дефолт TRUE — как у колонки в миграции 0015:
    # шаг выдан до неё не блокируется и снять его можно.
    optional: bool = True


class _Request(BaseModel):
    """Внутренняя заявка (в БД — dismissal_requests + request_steps)."""

    id: str
    status: str = DRAFT
    route_origin: str = "custom"
    enterprise: str
    fio: str
    tab_num: str
    department: str
    position: str
    category: str | None = None
    subject: str | None = None
    content: str | None = None
    doc_type_code: str | None = None
    escalation_hours: int | None = None
    created_by: str
    steps: list[_Step] = Field(default_factory=list)
    # Снимок подбора маршрута по справочникам (миграция 0008); is_manager —
    # признак из карточки AD для предпросмотра/аудита, в БД не хранится.
    profile_id: int | None = None
    service_id: int | None = None
    service_name: str | None = None
    is_manager: bool = False
    # Снимок выбранного бланка (миграции 0012/0014): ссылка на справочник плюс
    # копия названия/версии и текстов печати (шапка/подвал) — правка
    # справочника не меняет уже выданную заявку (её шаги хранят снимок текстов
    # шага в request_steps). Заполняется в режиме auto; в custom бланк не
    # обязателен.
    blank_id: int | None = None
    blank_name: str | None = None
    blank_version: int | None = None
    blank_header_html: str | None = None
    blank_footer_lines: list[str] = Field(default_factory=list)


# --- Офлайн-хранилище (тесты/локаль без БД); на стенде эндпоинты получают
# DbRequestsStore через зависимость get_requests_store (см. requests_store.py).
_requests_store = InMemoryRequestsStore()


def get_memory_requests_store() -> InMemoryRequestsStore:
    """Офлайн-хранилище заявок (общий экземпляр для тестов и локали без БД)."""
    return _requests_store


def reset_state_for_tests() -> None:
    """Сброс офлайн-хранилища. Только для изоляции pytest."""
    _requests_store.reset()


def _utcnow() -> datetime:
    """Текущее время UTC (единая точка для TTL)."""
    return datetime.now(timezone.utc)


def _is_hr(user: CurrentUser) -> bool:
    """Разрешенная группа для конструктора/правок: ОК, руководители ОК и админы."""
    return user.role in ("hr", "hr_admin", "admin")


def _require_hr(user: CurrentUser) -> None:
    """Ручной конструктор и правки — только разрешенной группе, иначе 403."""
    if not _is_hr(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ручной маршрут и правки — только разрешенной группе",
        )


# --- Кодирование блоков маршрута в step_order (без смены схемы БД) ---
# Блок кодируется в колонке step_order: блок*1000 + (100 если параллельный) + позиция.
# Позиция 1..99, блоки — с 0; параллельный блок помечается сотней в младшем разряде.
BLOCK_ORDER_BASE = 1000  # база номера блока
PARALLEL_MARK = 100  # признак параллельного блока в order


def _order_for(block: int, pos: int, parallel: bool) -> int:
    """step_order блока: блок*1000 + (100 если параллельный) + позиция (1..99)."""
    return block * BLOCK_ORDER_BASE + (PARALLEL_MARK if parallel else 0) + pos


def _block_info(order: int) -> tuple[int, bool, int]:
    """(индекс блока, параллельный?, позиция в блоке) из step_order."""
    return (order // BLOCK_ORDER_BASE, (order % BLOCK_ORDER_BASE) // PARALLEL_MARK == 1, order % 100)


# Режим шага с несколькими ответственными (миграция 0013): sequential — закрывают
# все ответственные, parallel — «кто-то один» (параллельный блок или режим шага
# бланка). Порядок в коде не зашивается: режим приходит снимком в шаге.
STEP_APPROVAL_SEQUENTIAL = "sequential"
STEP_APPROVAL_PARALLEL = "parallel"


def _approval_mode(value: object) -> str:
    """Режим шага из значения справочника: parallel|sequential; всё прочее —
    sequential («все ответственные» — безопасный режим по умолчанию)."""
    mode = str(value or "").strip().casefold()
    return mode if mode in (STEP_APPROVAL_SEQUENTIAL, STEP_APPROVAL_PARALLEL) else (
        STEP_APPROVAL_SEQUENTIAL
    )


def _mode_for_order(order: int) -> str:
    """Режим ручного шага из его блока: параллельный блок — любой ответственный,
    последовательный (и плоский список шагов) — все."""
    return STEP_APPROVAL_PARALLEL if _block_info(order)[1] else STEP_APPROVAL_SEQUENTIAL


def _executor_kind(value: object) -> str:
    """Вид исполнителя шага бланка: ad_group/manager_ad, всё прочее и пустое —
    people (согласующие назначает человек списком логинов)."""
    kind = str(value or "").strip().casefold()
    if kind in ("ad_group", "manager_ad"):
        return kind
    return "people"


def _blank_assignees(value: object) -> list[str]:
    """Логины согласующих из строки шага бланка: обрезка краёв, без пустых и
    повторов (порядок снимка сохраняется)."""
    assignees: list[str] = []
    for item in value or []:
        login = str(item or "").strip()
        if login and login not in assignees:
            assignees.append(login)
    return assignees


def _needs_manager(row: dict) -> bool:
    """Требует ли строка маршрута руководителя сотрудника (manager_ad).

    Строка шага бланка — по executor_kind, строка этапа маршрута — по owner_kind."""
    if _is_blank_step(row):
        return _executor_kind(row.get("executor_kind")) == "manager_ad"
    return str(row.get("owner_kind") or "") == "manager_ad"


def _build_steps(
    specs: list[StepSpec],
    ttl_days: int,
    manager: str | None,
    now: datetime,
    blocks: list[RouteBlockSpec] | None = None,
) -> list[_Step]:
    """Сборка шагов с expires_at = now + TTL (замена руководителя — в assignee).

    blocks — маршрут блоками (последовательный/параллельный); приоритетнее
    плоского списка specs. Персональный исполнитель шага — sam (исполнитель AD по
    выбору ОК): резолвер by_user, владелец группы = sam. assignee (замена
    руководителя) резолвер не меняет, а подстановка manager для ad_direct_manager
    важнее assignee. owner_group шага обязателен в _Step: это sam, иначе группа
    шага, иначе assignee (персональный шаг без группы).

    Режим шага (миграция 0013) снимается с блока: параллельный блок — любой из
    ответственных закрывает шаг, последовательный — все; у группового шага
    персональных исполнителей нет, поэтому assignees пуст."""
    steps: list[_Step] = []
    if blocks:
        for block_index, block in enumerate(blocks):
            for pos, spec in enumerate(block.steps, start=1):
                resolver = spec.resolver if spec.resolver in RESOLVERS else "by_group"
                assignee = getattr(spec, "assignee", None)
                if spec.sam:
                    resolver = "by_user"
                    assignee = spec.sam
                elif resolver == "ad_direct_manager" and manager:
                    assignee = manager
                order = _order_for(block_index, pos, block.mode == "parallel")
                steps.append(
                    _Step(
                        order=order,
                        owner_group=spec.sam or spec.owner_group or spec.assignee,
                        resolver=resolver,
                        assignee=assignee,
                        status=STEP_PENDING,
                        require_comment=spec.require_comment,
                        expires_at=now + timedelta(days=ttl_days),
                        assignees=[assignee] if assignee else [],
                        approval_mode=_mode_for_order(order),
                    )
                )
        return steps
    for index, spec in enumerate(specs):
        personal = spec.sam
        spec_assignee = spec.assignee
        resolver = spec.resolver if spec.resolver in RESOLVERS else "by_group"
        assignee = spec_assignee
        if personal:
            resolver = "by_user"
            assignee = personal
        elif resolver == "ad_direct_manager" and manager:
            assignee = manager
        order = index + 1
        steps.append(
            _Step(
                order=order,
                owner_group=personal or spec.owner_group or spec_assignee,
                resolver=resolver,
                assignee=assignee,
                status=STEP_PENDING,
                require_comment=spec.require_comment,
                expires_at=now + timedelta(days=ttl_days),
                assignees=[assignee] if assignee else [],
                approval_mode=_mode_for_order(order),
            )
        )
    return steps


def _same_executor(left: _Step, right: _Step) -> bool:
    """Тот же исполнитель шага (владелец, резолвер, персональный исполнитель).

    Признак, по которому снимок этапа одного шага можно отдать другому: текст и
    название этапа относятся именно к этому исполнителю, а не к позиции в
    маршруте (позиция при пересборке меняется)."""
    return (
        left.owner_group == right.owner_group
        and left.resolver == right.resolver
        and left.assignee == right.assignee
    )


def _carry_stage_snapshot(fresh: list[_Step], previous: list[_Step]) -> None:
    """Перенести снимок этапа на пересобранные шаги (текст печати, название).

    Правка маршрута пересобирает ожидающие шаги через _build_steps, а тот создаёт
    шаг только с исполнительскими полями — без stage_lines/stage_title печать
    бланка потеряла бы текст ещё не пройденных шагов (README: печать идёт по
    снимку выданной заявки). Переносим снимок только при совпадении исполнителя
    и один раз: чужой текст шага хуже никакого.

    previous — ожидавшие шаги заявки до правки (в порядке маршрута)."""
    unused = list(previous)
    for step in fresh:
        for index, old in enumerate(unused):
            if _same_executor(old, step):
                step.owner_kind = old.owner_kind
                step.stage_id = old.stage_id
                step.stage_code = old.stage_code
                step.stage_title = old.stage_title
                step.stage_lines = list(old.stage_lines)
                step.profile_step_id = old.profile_step_id
                step.optional = old.optional
                del unused[index]
                break


# --- Маршрут из справочников (route_mode = auto) ---
# Человекочитаемые причины подбора профиля (ключи reason из app.routing).
_ROUTE_REASON_TEXT = {
    REASON_SERVICE_NOT_REGISTERED: "служба не заведена",
    REASON_PROFILE_NOT_FOUND: "профиль не найден",
    REASON_DEFAULT_PROFILE: "не задан профиль по умолчанию",
}


def _int_or_none(value: object) -> int | None:
    """Целое из значения справочника; нечисло/пусто — None (id в снимке этапа)."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ad_or_body(ad_value: object, body_value: str) -> str:
    """Значение с приоритетом AD: пустое из AD не затирает то, что пришло в теле."""
    text = str(ad_value or "").strip()
    return text or str(body_value or "")


def _routing_store_or_none() -> DbRoutingStore | None:
    """Хранилище справочников маршрута: подмена зависимости в тестах, иначе боевое.

    Получить не удалось (нет БД/настроек) — None: выдача заявки не падает (fail-soft,
    как карта предприятий и групп в _public_view)."""
    try:
        return _resolve_dependency(get_routing_store, get_settings())
    except Exception:
        return None


def _profile_id(profile: dict | None) -> int:
    """id профиля из строки справочника (0 — профиля нет)."""
    return _int_or_none((profile or {}).get("id")) or 0


def _profile_names_map(store: DbRoutingStore | None) -> dict[int, str]:
    """Карта «id профиля → наименование» (для RequestOut.profile_name).

    Хранилище недоступно — пустая карта (profile_name тогда None, без 500)."""
    if store is None:
        return {}
    try:
        profiles = store.list_profiles()
    except Exception:
        return {}
    names: dict[int, str] = {}
    for item in profiles or []:
        if not isinstance(item, dict):
            continue
        profile_id = _int_or_none(item.get("id"))
        name = str(item.get("name") or "").strip()
        if profile_id and name:
            names[profile_id] = name
    return names


class _RosterResolver:
    """Состав этапов (owner_kind = stage_roster) с кэшем на один вызов ответа.

    Один SELECT на этап и не больше: состав спрашивается для каждого шага
    снимка (owner_kind = stage_roster) при выдаче карточки/решении по шагу.
    Хранилище недоступно — пустой состав (fail-soft: карточка заявки не должна
    падать из-за справочников; решение по такому шагу будет отклонено как
    чужое)."""

    def __init__(self, store: DbRoutingStore | None) -> None:
        self._store = store
        self._cache: dict[int, list[str]] = {}

    def sams(self, stage_id: int | None) -> list[str]:
        """Активные логины состава этапа (пусто — этапа нет/хранилище недоступно)."""
        if not stage_id or self._store is None:
            return []
        key = int(stage_id)
        if key not in self._cache:
            try:
                rows = self._store.list_stage_assignees(key, active_only=True)
            except Exception:
                rows = []
            self._cache[key] = [
                str(row.get("sam") or "").strip()
                for row in rows or []
                if isinstance(row, dict) and str(row.get("sam") or "").strip()
            ]
        return self._cache[key]

    def owns(self, step: _Step, sam: str) -> bool:
        """Входит ли сотрудник в состав этапа шага (только owner_kind = stage_roster).

        Сравнение логинов — без учёта регистра и пробелов, как в app.routing
        can_user_act (состав этапа задаётся в справочнике вручную)."""
        if getattr(step, "owner_kind", None) != "stage_roster":
            return False
        wanted = str(sam or "").strip().casefold()
        if not wanted:
            return False
        return any(item.casefold() == wanted for item in self.sams(step.stage_id))


def _stage_owner_kinds(store: DbRoutingStore | None) -> dict[int, str]:
    """Карта «id этапа → owner_kind» из справочника (ОДИН SELECT на вызов ответа).

    Колонки owner_kind у шага заявки в БД нет (признак живёт в
    approval_stages), поэтому шаг, перечитанный из Postgres, его не знает, а
    право по реестру этапа (owner_kind = stage_roster) без него не проверяется.
    Справочник пуст/недоступен — пустая карта: тогда признак остаётся таким, каким
    пришёл (fail-soft, как состав этапа у _RosterResolver)."""
    if store is None:
        return {}
    try:
        rows = store.list_stages()
    except Exception:
        return {}
    kinds: dict[int, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stage_id = _int_or_none(row.get("id"))
        kind = str(row.get("owner_kind") or "").strip()
        if stage_id and kind:
            kinds[stage_id] = kind
    return kinds


def _restore_step_owner_kinds(request: _Request, kinds: dict[int, str]) -> None:
    """Восстановить owner_kind шагов заявки из справочника по stage_id (in-place).

    Уже заполненный owner_kind не трогаем: снимок на момент создания заявки
    описывает, на кого этап тогда назначали, и не должен меняться задним числом
    после правки справочника. Шаги ручного маршрута (stage_id пуст) не меняются.
    """
    if not kinds:
        return
    for step in request.steps:
        if step.owner_kind or not step.stage_id:
            continue
        kind = kinds.get(step.stage_id)
        if kind:
            step.owner_kind = kind


def _route_reason_detail(picked: RoutePick) -> str:
    """Текст 422 о неподобранном маршруте: причина подбора и её машинный код.

    Пометки reason (Dismissed=..., Added=...) сохраняются в скобках, чтобы по
    логу/API было видно, что именно ОК снял/добавил."""
    reason = str(picked.reason or "")
    base = reason.split("+")[0]
    return "Маршрут из справочников не собран: %s (%s)" % (
        _ROUTE_REASON_TEXT.get(base, base or "причина неизвестна"),
        reason or "причина неизвестна",
    )


def _employee_sam(body: "CreateRequestIn | RoutePreviewIn") -> str | None:
    """Логин AD увольняемого: из тела, затем связка 1С↔AD, затем локальный
    справочник сотрудников (employee_base_map.ad_sam).

    Ни один источник не обязателен: без логина карточка AD не читается и данные
    берутся из тела (подбор маршрута идёт по службе из тела). Связка ищется по
    составному ключу enterprise|base_code|tab_num (тот же формат, что
    link.py::link_key; base_code пуст — по нему не ищем). Тело предпросмотра
    маршрута (RoutePreviewIn) отдаёт те же поля идентификации сотрудника."""
    direct = str(body.ad_sam or "").strip()
    if direct:
        return direct
    key = _employee_key(body.enterprise, body.base_code, body.tab_num)
    if key and body.base_code:
        try:
            record = _resolve_dependency(get_links_store, get_settings()).find(key)
        except Exception:
            record = None
        if record is not None and record.sam:
            return record.sam
    try:
        rows = _resolve_dependency(get_employee_sync_store, get_settings()).find_by_people(
            body.enterprise, [], [body.tab_num]
        )
    except Exception:
        return None
    # Неоднозначный табельный номер (человек в двух базах) логина не даёт:
    # молча взяли бы чужую карточку.
    if len(rows) == 1:
        return str(rows[0].get("ad_sam") or "").strip() or None
    return None


def _manager_sam_from_dn(
    manager_dn: str, store: DbRoutingStore, ad_reader: object | None
) -> tuple[str | None, str | None]:
    """Руководитель сотрудника по DN: (sam, ФИО).

    Истина — AD: get_user_by_dn по DN руководителя. Фолбэк — локальный
    best-effort по зеркалу users (manager_sam_by_dn ищет совпадение строки
    manager_dn, а не сотрудника по своему DN, поэтому это запасной путь).
    AD недоступен и в зеркале пусто — (None, None): этап manager_ad остаётся
    незакрываемым, предпросмотр покажет blocked_reason, создание даст 422."""
    dn = (manager_dn or "").strip()
    if not dn:
        return None, None
    if ad_reader is not None:
        try:
            manager = ad_reader.get_user_by_dn(dn)
        except Exception:
            manager = None
        if manager is not None:
            return (
                str(getattr(manager, "sam", "") or "").strip() or None,
                str(getattr(manager, "display_name", "") or "").strip() or None,
            )
    try:
        sam = store.manager_sam_by_dn(dn)
    except Exception:
        sam = None
    return (sam or None), _owner_display_name(ad_reader, sam)


def _step_from_stage(
    stage_row: dict,
    order: int,
    manager_sam: str | None,
    ttl_days: int,
    now: datetime,
    roster_sams: list[str] | None = None,
) -> _Step:
    """Шаг заявки из строки этапа: исполнитель по owner_kind + снимок этапа.

    owner_group шага обязателен (_Step), поэтому он берётся из этапа, иначе —
    персональный исполнитель (как в _build_steps), иначе код этапа. Этап
    manager_ad без руководителя (ни в AD, ни замена от ОК) — 422: назначать
    такой шаг не на кого.

    Несколько ответственных (миграция 0013): этапу реестра (owner_kind =
    stage_roster) достаётся снимок состава — все активные участники, assignee
    первый (прежние выборки, печать и письма продолжают работать); прочие этапы
    — список из одного исполнителя, у группового шага (owner_group) список
    пуст. Режим шага — из строки этапа (blank_steps.approval_mode), иначе
    sequential («все ответственные»). Пустой состав реестра — шаг закрыть некому,
    он остаётся заблокированным, как и до разделения режимов."""
    owner_kind = str(stage_row.get("owner_kind") or "ad_group")
    resolver, assignee = stage_executor(stage_row, manager_sam)
    stage_code = str(stage_row.get("code") or stage_row.get("stage_code") or "")
    if owner_kind == "manager_ad" and not assignee:
        raise HTTPException(
            status_code=422,
            detail=(
                "Не найден руководитель сотрудника в AD, укажите замену "
                "(manager) для этапа manager_ad"
            ),
        )
    override = stage_row.get("require_comment_override")
    require_comment = (
        bool(stage_row.get("require_comment")) if override is None else bool(override)
    )
    # Снимок «этап можно снять»: optional_override шага профиля, иначе optional
    # этапа (как в app.routing pick_profile) — миграция 0015.
    optional_override = stage_row.get("optional_override")
    optional = (
        bool(stage_row.get("optional")) if optional_override is None else bool(optional_override)
    )
    if owner_kind == "stage_roster":
        assignees = [str(sam).strip() for sam in (roster_sams or []) if str(sam).strip()]
    else:
        assignees = [assignee] if assignee else []
    return _Step(
        order=order,
        owner_group=str(stage_row.get("owner_group") or assignee or stage_code),
        resolver=resolver,
        assignee=assignee or (assignees[0] if assignees else None),
        require_comment=require_comment,
        expires_at=now + timedelta(days=ttl_days),
        owner_kind=owner_kind,
        stage_id=_int_or_none(stage_row.get("stage_id", stage_row.get("id"))),
        stage_code=stage_code or None,
        stage_title=str(stage_row.get("stage_title") or stage_row.get("title") or "") or None,
        stage_lines=[
            str(line)
            for line in (stage_row.get("stage_lines") or [])
            if isinstance(line, str) and line.strip()
        ],
        profile_step_id=_int_or_none(stage_row.get("profile_step_id")),
        assignees=assignees,
        approval_mode=_approval_mode(stage_row.get("approval_mode")),
        optional=optional,
    )


def _is_blank_step(row: dict) -> bool:
    """Строка это самостоятельный шаг бланка, а не этап маршрута.

    Различающий признак — executor_kind: у шага бланка он есть всегда (вид
    исполнителя people/ad_group/manager_ad), у строки этапа маршрута его нет
    (там owner_kind)."""
    return isinstance(row, dict) and "executor_kind" in row


def _step_from_blank_step(
    blank_step: dict,
    order: int,
    manager_sam: str | None,
    ttl_days: int,
    now: datetime,
) -> _Step:
    """Шаг заявки из строки шага бланка: свой текст и свой исполнитель.

    executor_kind — вид исполнителя шага бланка (миграция 0014):
    people — список логинов согласующих (assignees), ad_group — группа AD
    (owner_group), manager_ad — руководитель сотрудника в AD (замена
    руководителя от ОК приоритетнее); без руководителя — 422, назначать такой
    шаг не на кого.

    owner_group шага обязателен (_Step): у персонального шага это первый
    ответственный (как в _build_steps), у группового — группа AD, у
    manager_ad — руководитель. Режим шага — из строки бланка, иначе sequential
    («все ответственные»)."""
    kind = _executor_kind(blank_step.get("executor_kind"))
    assignees = _blank_assignees(blank_step.get("assignees"))
    owner_group = str(blank_step.get("owner_group") or "").strip()
    assignee: str | None = None
    resolver = "by_group"
    if kind == "people":
        # Персональный шаг: резолвер by_user, ответственные — все из снимка,
        # assignee — первый (прежние выборки, печать и письма).
        resolver = "by_user"
        assignee = assignees[0] if assignees else None
    elif kind == "manager_ad":
        resolver = "ad_direct_manager"
        assignee = str(manager_sam or "").strip() or None
        if not assignee:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Не найден руководитель сотрудника в AD, укажите замену "
                    "(manager) для шага manager_ad"
                ),
            )
        assignees = [assignee]
    title = str(blank_step.get("title") or "").strip()
    return _Step(
        order=order,
        owner_group=owner_group or assignee or title or f"blank-step-{order}",
        resolver=resolver,
        assignee=assignee or (assignees[0] if assignees else None),
        require_comment=bool(blank_step.get("require_comment")),
        expires_at=now + timedelta(days=ttl_days),
        owner_kind=kind,
        stage_title=title or None,
        stage_lines=[
            str(line)
            for line in (blank_step.get("stage_lines") or [])
            if isinstance(line, str) and line.strip()
        ],
        assignees=assignees,
        approval_mode=_approval_mode(blank_step.get("approval_mode")),
        # Снимок «шаг можно снять» (миграция 0015): правка справочника уже
        # выданную заявку не меняет.
        optional=bool(blank_step.get("optional")),
    )


def _missing_ad_card_detail(body: "CreateRequestIn") -> str:
    """Текст 422 «нет карточки AD» с учётом реальной причины.

    Человек может быть в AD, но без подтверждённой связи — тогда достаточно
    подтвердить связь (кнопка в предпросмотре), а не идти в другой раздел."""
    fio = _employee_fio(body, body.tab_num)
    candidates = _ad_candidates(_resolve_dependency(get_ad_reader), fio)
    if len(candidates) == 1:
        return (
            "Связь 1С↔AD не оформлена, хотя в AD есть «%s» (%s). Подтвердите связь в "
            "предпросмотре — служба и руководитель подтянутся. Иначе маршрут по "
            "профилю службы собрать нельзя: переключите режим на «Вручную»."
            % (candidates[0].display_name, candidates[0].sam)
        )
    if len(candidates) > 1:
        return (
            "В AD несколько записей с этим ФИО — выберите сотрудника в разделе "
            "«Сопоставление 1С↔AD» или переключите режим на «Вручную»: маршрут по "
            "профилю службы собрать нельзя."
        )
    return (
        "Сотрудник не найден в AD — маршрут по профилю службы собрать нельзя. "
        "Оформите связь 1С↔AD (раздел «Сопоставление 1С↔AD») или переключите "
        "режим на «Вручную» и задайте маршрут самостоятельно."
    )


class LinkEmployeeIn(BaseModel):
    """Подтверждение связи 1С↔AD при создании заявки."""

    enterprise: str
    tab_num: str
    base_code: str | None = None
    fio: str | None = None


@router.post("/requests/route/link-employee")
def link_employee(
    body: LinkEmployeeIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    links_store: object = Depends(get_links_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> dict:
    """Оформить связь 1С↔AD по сотруднику (роль как у создания заявки).

    Кандидат в AD должен быть ровно один: при нескольких записях 422 со списком
    логинов — выбор делает человек (раздел «Сопоставление 1С↔AD»). Запись —
    только связка в нашей БД (плюс зеркала users/employee_base_map для внешних
    ключей); AD и 1С не пишутся. Действие аудируется как link.create."""
    settings.ensure_read_only()
    _require_hr(user)
    if ad_reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ридер AD не настроен",
        )
    fio = str(body.fio or "").strip() or _employee_fio(body, body.tab_num)
    candidates = _ad_candidates(ad_reader, fio)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="В AD нет записи с ФИО «%s» — подтверждать нечего." % (fio or "?"),
        )
    if len(candidates) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "В AD несколько записей с ФИО «%s» (%s) — выберите сотрудника вручную "
                "в разделе «Сопоставление 1С↔AD»."
                % (fio, ", ".join(sorted(c.sam for c in candidates)))
            ),
        )
    from .link import LinkRecord, link_key  # локально против циклического импорта

    key = link_key(body.enterprise, body.base_code or "", body.tab_num)
    try:
        record = links_store.find(key)
    except Exception:
        record = None
    ad_user = candidates[0]
    now = datetime.now(timezone.utc).isoformat()
    if record is not None and record.sam:
        if record.sam != ad_user.sam:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Связь уже оформлена на другого сотрудника (%s). Изменить её можно "
                    "в разделе «Сопоставление 1С↔AD»." % record.sam
                ),
            )
        return {
            "linked": True,
            "already": True,
            "sam": record.sam,
            "fio": ad_user.display_name,
            "dept_ad": ad_user.department,
            "title_ad": ad_user.title,
        }
    try:
        links_store.ensure_targets(
            None,
            {
                "enterprise": body.enterprise,
                "base_code": body.base_code or "",
                "tab_num": body.tab_num,
                "fio": fio,
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
        links_store.save(
            LinkRecord(
                enterprise=body.enterprise,
                base_code=body.base_code or "",
                tab_num=body.tab_num,
                key=key,
                sam=ad_user.sam,
                by=user.sam,
                at=now,
                verified=True,
                diverged=False,
                needs_manual_review=False,
                truth_source="1c",
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Связь не сохранена: %s" % exc,
        ) from exc
    # Строку справочника помечаем связанной сразу: иначе следующий предпросмотр
    # не увидит логин (ad_sam) и снова предложит подтвердить связь. Ошибка
    # здесь не критична — плановый синк справочника поправит значение.
    try:
        _resolve_dependency(get_employee_sync_store, get_settings()).mark_linked(
            body.enterprise, body.tab_num, ad_user.sam, body.base_code or ""
        )
    except Exception:
        pass
    audit_log.append(
        AuditEvent(
            actor=user.sam,
            action="link.create",
            entity="link_1c_ad",
            entity_id=key,
            detail="sam=%s (подтверждено при создании заявки)" % ad_user.sam,
        )
    )
    return {
        "linked": True,
        "already": False,
        "sam": ad_user.sam,
        "fio": ad_user.display_name,
        "dept_ad": ad_user.department,
        "title_ad": ad_user.title,
    }


# --- Бланки (миграция 0012): выбор бланка сотрудником ОК ---
# Бланк выбирает человек при создании заявки (шаги и ответственные — его состава).
# Подбор по службе (pick_profile) остался запасным механизмом за настройкой
# blank_autopick: ключа нет или значение не "on" — автоподстановка выключена.
BLANK_AUTOPICK_SETTING = "blank_autopick"
BLANK_SOURCE_CHOSEN = "chosen"  # бланк выбрал ОК
BLANK_SOURCE_AUTOPICK = "autopick"  # бланк подобран по службе (запасной путь)


def _blank_autopick_enabled() -> bool:
    """Включена ли автоподстановка бланка по службе (настройка blank_autopick).

    Включает только значение "on" (регистр и пробелы не важны). Ключа нет,
    значение другое или хранилище настроек недоступно — выключена (fail-soft,
    как остальные чтения настроек): тогда бланк выбирает ОК. Дефолт в коде не
    зашит — отсутствие ключа трактуется как "off"."""
    try:
        store = _resolve_dependency(get_settings_store, get_settings())
        raw = read_setting_value(store, BLANK_AUTOPICK_SETTING)
    except Exception:
        return False
    return str(raw or "").strip().casefold() == "on"


def _available_blanks(store: DbRoutingStore | None) -> list[dict]:
    """Активные бланки справочника для выбора при создании заявки.

    Офлайн-заглушки без list_blanks (conftest) и недоступное хранилище — пустой
    список: выбирать тогда нечего, и предпросмотр не требует выбора (fallback на
    подбор по службе)."""
    reader = getattr(store, "list_blanks", None)
    if not callable(reader):
        return []
    try:
        rows = reader(active_only=True)
    except RoutingUnavailable:
        raise
    except Exception:
        return []
    return [row for row in (rows or []) if isinstance(row, dict)]


def _blank_list_text(blanks: list[dict]) -> str:
    """Перечень доступных бланков одной строкой для текстов 422 (без ПДн)."""
    parts: list[str] = []
    for item in blanks or []:
        name = str(item.get("name") or item.get("code") or "").strip()
        if not name:
            continue
        count = _int_or_none(item.get("step_count"))
        parts.append("«%s»%s" % (name, "" if count is None else " (шагов: %d)" % count))
    return " Доступные бланки: %s." % ", ".join(parts) if parts else ""


def _blank_row(store: DbRoutingStore, blank_id: int) -> dict | None:
    """Строка бланка по id; заглушка без blank_by_id — None (как бланка нет)."""
    reader = getattr(store, "blank_by_id", None)
    if not callable(reader):
        return None
    return reader(blank_id)


def _blank_step_rows(store: DbRoutingStore, blank: dict) -> list[dict]:
    """Свои шаги бланка из справочника (без этапа); заглушка без метода — []."""
    reader = getattr(store, "list_blank_steps", None)
    if not callable(reader):
        return []
    rows = reader(int(blank.get("id") or 0))
    return [row for row in (rows or []) if isinstance(row, dict)]


def _blank_or_422(store: DbRoutingStore, blank_id: int) -> dict:
    """Бланк маршрута: существует, активен и не пуст, иначе 422 с понятным текстом.

    Пустой бланк (шагов нет) выбрать нельзя: маршрут заявке нечем задать. Счётчик
    шагов пустым не приходит (step_count из blank_by_id); None — офлайн-заглушка
    без счётчика, тогда проверка не выполняется (fail-soft)."""
    blank = _blank_row(store, blank_id)
    if not blank:
        raise HTTPException(
            status_code=422,
            detail="Бланк не найден (blank_id=%s) — выберите бланк из списка.%s"
            % (blank_id, _blank_list_text(_available_blanks(store))),
        )
    if not blank.get("active"):
        raise HTTPException(
            status_code=422,
            detail="Бланк «%s» отключён — выберите другой.%s"
            % (
                str(blank.get("name") or blank.get("code") or "?"),
                _blank_list_text(_available_blanks(store)),
            ),
        )
    steps = _int_or_none(blank.get("step_count"))
    if steps == 0:
        raise HTTPException(
            status_code=422,
            detail="Бланк «%s» без шагов — выбрать другой.%s"
            % (
                str(blank.get("name") or blank.get("code") or "?"),
                _blank_list_text(_available_blanks(store)),
            ),
        )
    return blank


def _blank_required_detail(blanks: list[dict]) -> str:
    """422 «бланк не выбран»: два варианта (выбрать бланк ИЛИ задать маршрут
    вручную) и список доступных бланков."""
    return (
        "Выберите бланк — он задаёт список шагов и ответственных, — либо задайте "
        "маршрут вручную (route_mode=custom с blocks/steps). Автоподстановка "
        "бланка по службе выключена.%s" % _blank_list_text(blanks)
    )


def _blank_required_422(store: DbRoutingStore) -> None:
    """Бланк не выбран, автоподстановка по службе выключена — 422 с вариантами.

    Единая точка для предпросмотра и создания: условие и текст обязаны
    совпадать, иначе ОК увидит в предпросмотре маршрут и упрётся в отказ
    только при отправке."""
    raise HTTPException(
        status_code=422, detail=_blank_required_detail(_available_blanks(store))
    )


def _dismiss_blank_steps(picked: RoutePick, orders: list[int]) -> RoutePick:
    """Снятие шага бланка по номеру шага (dismissed_step_orders).

    У шага бланка нет кода этапа, поэтому dismissed_stages его не касается:
    шаг снимают по номеру blank_steps.step_order — тем же, что сотрудник ОК
    задаёт в справочнике (решение человека 2026-10-08). Снятие идёт по
    шагам бланка; этапы профиля и ручного маршрута по-прежнему снимаются
    кодами (dismissed_stages) и добавление — added_stages.

    Валидация строгая, молчаливого пропуска нет: сотрудник ОК отправил бы
    маршрут не тот, что показал в предпросмотре.
      — номер не входит в состав шагов бланка — 422 с перечнем номеров;
      — номер указывает на обязательный шаг (optional=false) — 422: снять
        его нельзя, снимок optional в предпросмотре об этом говорит;
      — сняты все шаги — 422: заявке нечем задавать маршрут.
    Причина подбора дополняется пометкой DismissedStep=номера (как
    Dismissed=коды у этапов).
    """
    wanted = {int(item) for item in (orders or [])}
    if not wanted:
        return picked
    blank_orders = {
        _int_or_none(stage.get("step_order"))
        for stage, _ in picked.stages
        if _is_blank_step(stage)
    }
    blank_orders.discard(None)
    known = ", ".join(str(item) for item in sorted(blank_orders)) or "—"
    unknown = sorted(order for order in wanted if order not in blank_orders)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                "Шаги %s в маршруте нет — снять можно шаги бланка с номерами %s. "
                "Обновите предпросмотр маршрута."
                % (
                    ", ".join(str(item) for item in unknown),
                    known,
                )
            ),
        )
    kept: list[tuple[dict, bool]] = []
    dropped: list[int] = []
    for stage, optional in picked.stages:
        order = _int_or_none(stage.get("step_order")) if _is_blank_step(stage) else None
        if order in wanted:
            if not optional:
                title = str(
                    stage.get("title") or stage.get("stage_title") or ""
                ).strip() or "без названия"
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Шаг №%d («%s») обязательный — снять его нельзя."
                        % (int(order), title)
                    ),
                )
            dropped.append(int(order))  # type: ignore[arg-type]
        else:
            kept.append((stage, optional))
    if not kept:
        raise HTTPException(
            status_code=422,
            detail=(
                "Сняты все шаги маршрута — верните хотя бы один шаг "
                "(dismissed_step_orders пуст должен быть)."
            ),
        )
    return RoutePick(
        profile=picked.profile,
        service=picked.service,
        reason=_join_reason(
            picked.reason,
            "DismissedStep=%s" % ",".join(str(item) for item in sorted(dropped)),
        ),
        stages=kept,
    )


def _auto_route(
    store: DbRoutingStore,
    body: CreateRequestIn,
    card: dict | None,
    route: RouteSettings,
    now: datetime,
) -> dict:
    """Маршрут заявки из справочников по службе увольняемого.

    Возвращает снимок подбора: steps, profile_id, service_id, department,
    position, service_name. Значения сотрудника — из карточки AD (пустые не
    затирают тело); табельный номер и дата приёма остаются из 1С.

    Профиль не подобран — 422 с причиной подбора: молча уходить в ручной
    маршрут нельзя (заявка получила бы не тот маршрут, который заказан). Исключение
    — выбранный бланк (blank_id): он задаёт шаги сам, а профиль службы остаётся
    справочной подсказкой и без него маршрут собирается. Бланк не выбран и
    автоподстановка по службе выключена (blank_autopick) — 422 с вариантами
    (выбрать бланк или задать маршрут вручную): тихо подставлять профиль нельзя.

    Подбор профиля идёт в два вызова pick_profile: список этапов справочника
    читается по id профиля (list_profile_steps), а профиль выбирается по службе.
    Повторный вызов на тех же данных детерминирован (app.routing — чистая)."""
    department = _ad_or_body((card or {}).get("dept_ad"), body.department)
    position = _ad_or_body((card or {}).get("title_ad"), body.position)
    # Без карточки AD маршрут по профилю не собрать: профиль по умолчанию здесь
    # был бы молчаливой подменой. Служба пустая при живой карточке — тоже: подбор
    # идёт по службе. В обоих случаях ОК должен увидеть, что делать.
    if card is None:
        raise HTTPException(
            status_code=422,
            detail=_missing_ad_card_detail(body),
        )
    if not department.strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "Служба сотрудника не определена в AD — маршрут по профилю собрать "
                "нельзя. Заполните подразделение в AD или переключите режим на "
                "«Вручную»."
            ),
        )
    services = store.list_services()
    profiles = store.list_profiles()
    picked = pick_profile(department, services, profiles, [])
    blank: dict | None = None
    if body.blank_id is not None:
        # Бланк выбрал ОК: он задаёт шаги и ответственные, поэтому профиль службы
        # — только справочная подсказка, и его отсутствие маршруту не мешает.
        blank = _blank_or_422(store, body.blank_id)
        picked = pick_blank_steps(picked, _blank_step_rows(store, blank))
        if not picked.stages:
            # Состав пуст (счётчик шагов в заглушке может отсутствовать) — маршрут
            # заявке задавать нечем.
            raise HTTPException(
                status_code=422,
                detail="Бланк «%s» без шагов — выберите другой.%s"
                % (
                    str(blank.get("name") or blank.get("code") or "?"),
                    _blank_list_text(_available_blanks(store)),
                ),
            )
    elif not _blank_autopick_enabled():
        # Бланк не выбран и автоподстановка выключена: маршрут по справочникам
        # молча собирать нельзя (заявка получила бы не тот маршрут, который
        # заказан). Понятный 422 с обоими вариантами: выбрать бланк или задать
        # маршрут вручную (текст тот же, что в предпросмотре).
        _blank_required_422(store)
    elif picked.profile is None:
        raise HTTPException(status_code=422, detail=_route_reason_detail(picked))
    else:
        picked = pick_profile(
            department,
            services,
            profiles,
            store.list_profile_steps(_profile_id(picked.profile)),
        )
    picked = apply_dismissals_and_additions(
        picked,
        body.dismissed_stages,
        body.added_stages,
        store.list_stages() if body.added_stages else [],
    )
    # Снятие шага бланка по номеру шага (у него нет кода этапа — dismissed_stages
    # его не касается). Применяется после снятий по кодам: номера относятся к
    # маршруту без снятых этапов.
    picked = _dismiss_blank_steps(picked, body.dismissed_step_orders)
    manager_dn = str((card or {}).get("manager_dn") or "").strip()
    manager_sam: str | None = str(body.manager or "").strip() or None
    if manager_sam is None and manager_dn:
        manager_sam = _manager_sam_from_dn(
            manager_dn, store, _resolve_dependency(get_ad_reader)
        )[0]
    if manager_sam is None and any(
        _needs_manager(stage_row) for stage_row, _optional in picked.stages
    ):
        # Причина важна для ОК: подсказываем именно действие, а не «укажите замену»
        # без указания, где её указать.
        cause = (
            "в AD не указан руководитель"
            if not manager_dn
            else "руководитель из AD не читается (запись не найдена или каталог недоступен)"
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"Руководитель сотрудника не определён: {cause}. "
                "Выберите его вручную в блоке «Руководитель» формы "
                "(или передайте manager — логин AD)."
            ),
        )
    # Ответственные этапа-реестра — снимок состава на момент выдачи заявки
    # (миграция 0013): один SELECT на этап, кэш на этот вызов. У шага бланка
    # реестра нет — согласующие задаёт сам шаг (assignees).
    roster = _RosterResolver(store)
    steps = [
        _step_from_blank_step(stage_row, index, manager_sam, route.approval_ttl_days, now)
        if _is_blank_step(stage_row)
        else _step_from_stage(
            stage_row,
            index,
            manager_sam,
            route.approval_ttl_days,
            now,
            roster_sams=(
                roster.sams(_int_or_none(stage_row.get("stage_id", stage_row.get("id"))))
                if str(stage_row.get("owner_kind") or "") == "stage_roster"
                else None
            ),
        )
        for index, (stage_row, _optional) in enumerate(picked.stages, start=1)
    ]
    return {
        "steps": steps,
        "profile_id": _profile_id(picked.profile) or None,
        "service_id": _int_or_none((picked.service or {}).get("id")),
        "department": department,
        "position": position,
        "service_name": department,
        # Снимок выбранного бланка (без бланка — пусто, маршрут подобран по службе).
        "blank_id": _int_or_none((blank or {}).get("id")),
        "blank_name": str((blank or {}).get("name") or "") or None,
        "blank_version": _int_or_none((blank or {}).get("version")),
        "blank_header_html": str((blank or {}).get("header_html") or "") or None,
        "blank_footer_lines": [
            str(line).strip()
            for line in ((blank or {}).get("footer_lines") or [])
            if isinstance(line, str) and line.strip()
        ],
    }


def _auto_route_entry(body: CreateRequestIn, route: RouteSettings, now: datetime) -> dict:
    """Подбор маршрута по справочникам: логин AD → карточка сотрудника → маршрут.

    Справочники недоступны — 503 (RoutingUnavailable), как LinksUnavailable в
    link.py: это поломка хранилища, а не ошибка запроса."""
    store = _routing_store_or_none()
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище справочников маршрута недоступно",
        )
    sam = _employee_sam(body)
    try:
        card = store.user_card(sam) if sam else None
        picked = _auto_route(store, body, card, route, now)
        picked["is_manager"] = store.is_manager(sam) if sam else False
    except RoutingUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return picked


def _get_request_or_404(store: RequestsStore, request_id: str) -> _Request:
    """Заявка по id, иначе 404 (без ПДн в ошибке)."""
    request = store.get(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return request


def _current_pending_steps(request: _Request) -> list[_Step]:
    """Ожидающие шаги текущего блока маршрута.

    Блоки обрабатываются по возрастанию индекса; внутри блока — по order.
    Параллельный блок — все ожидающие шаги сразу; последовательный — только
    первый ожидающий. Завершённые блоки пропускаются; пусто — []."""
    pending_by_block: dict[int, list[_Step]] = {}
    for step in request.steps:
        if step.status != STEP_PENDING:
            continue
        block_index, _, _ = _block_info(step.order)
        pending_by_block.setdefault(block_index, []).append(step)
    for block_index in sorted(pending_by_block):
        block_steps = sorted(pending_by_block[block_index], key=lambda s: s.order)
        _, parallel, _ = _block_info(block_steps[0].order)
        if parallel:
            return block_steps
        return [block_steps[0]]
    return []


def _current_pending(request: _Request) -> _Step | None:
    """Первый ожидающий шаг текущего блока (строгая очередность внутри блока)."""
    steps = _current_pending_steps(request)
    return steps[0] if steps else None


def _record_approval(
    step: _Step, sam: str, decision: str, at: datetime, comment: str | None
) -> None:
    """Оставить отметку ответственного по шагу (миграция 0013).

    Хранится последнее решение каждого ответственного: повторная отметка того же
    sam заменяет прежнюю, поэтому approve и reject одного человека не могут
    стоять рядом. Только логин (sAMAccountName), без ФИО — ФИО подставляет
    выдача по логинам."""
    login = str(sam or "").strip()
    if not login:
        return
    step.approvals = [
        item for item in step.approvals if str(item.get("sam") or "") != login
    ] + [
        {
            "sam": login,
            "at": at.isoformat(),
            "decision": decision,
            "comment": (comment or "").strip() or None,
        }
    ]


def _approvals_by_sam(step: _Step) -> dict[str, dict]:
    """Отметки шага по логинам: {sam: {sam, at, decision, comment}} (последняя)."""
    return {
        str(item.get("sam") or ""): item
        for item in step.approvals
        if isinstance(item, dict) and str(item.get("sam") or "")
    }


def _approved_sams(step: _Step) -> set[str]:
    """Логины ответственных, согласовавших шаг (сравнение без учёта регистра:
    реестр и логин AD могли различаться написанием)."""
    return {
        sam.casefold()
        for sam, item in _approvals_by_sam(step).items()
        if str(item.get("decision") or "") == "approve"
    }


def _already_approved(step: _Step, sam: str) -> bool:
    """Оставлял ли этот ответственный свою отметку согласия по шагу."""
    item = _approvals_by_sam(step).get(str(sam or ""))
    return bool(item) and str(item.get("decision") or "") == "approve"


def _closes_on_approve(step: _Step) -> bool:
    """Закрывает ли новая отметка согласия шаг.

    Режим parallel («кто-то один») — закрывает любая отметка. Режим sequential
    (и пустой режим у шага, выданного до миграции) — закрывают все
    ответственные из снимка; снимка нет (групповой шаг, действует owner_group) —
    отметка закрывает шаг, как до разделения режимов."""
    if step.approval_mode == STEP_APPROVAL_PARALLEL:
        return True
    if not step.assignees:
        return True
    approved = _approved_sams(step)
    return all(str(sam).strip().casefold() in approved for sam in step.assignees)


def _clear_approvals(step: _Step) -> None:
    """Сбросить отметки шага: шаг снова в работе (переоткрытие блока, повтор
    просроченного) — старые отметки к новому кругу не относятся."""
    step.approvals = []


def _owns_step(
    step: _Step, user: CurrentUser, roster: _RosterResolver | None = None
) -> bool:
    """Владелец шага по действующим правилам: любой из ответственных снимка
    (assignees, миграция 0013), иначе персональный assignee — только он (sam
    сравнивается ровно как раньше, регистр НЕ нормализуется — ослабление
    сравнения расширило бы доступ), иначе любой из группы-владельца шага, иначе
    участник состава этапа (owner_kind = stage_roster, состав — из справочника)."""
    if step.assignees:
        return any(user.sam == item for item in step.assignees)
    if step.assignee:
        return user.sam == step.assignee
    if step.owner_group and step.owner_group in user.groups:
        return True
    return roster is not None and roster.owns(step, user.sam)


def _check_step_owner(
    step: _Step, user: CurrentUser, roster: _RosterResolver | None = None
) -> None:
    """Отметку ставит владелец: персональный assignee — только он, иначе любой из группы
    или участник состава этапа.

    Правило вынесено в _owns_step (его же использует can_act), тексты 403 и коды
    ответов прежние."""
    if _owns_step(step, user, roster):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Шаг назначен другому исполнителю"
            if step.assignee
            else "Нет доступа: шаг чужой группы"
        ),
    )


def _check_comment(step: _Step, decision: str, comment: str | None) -> None:
    """Комментарий: при отказе/возврате — всегда обязателен, при согласии — по флагу шага."""
    text = (comment or "").strip()
    if decision in ("reject", "return") and not text:
        raise HTTPException(
            status_code=422, detail="При отказе/возврате комментарий обязателен"
        )
    if decision == "approve" and step.require_comment and not text:
        raise HTTPException(
            status_code=422, detail="Шаг требует комментарий даже при согласии"
        )


def _can_act(
    request: _Request,
    step: _Step,
    user: CurrentUser,
    pending_orders: set[int],
    now: datetime,
    roster: _RosterResolver | None = None,
) -> bool:
    """Может ли пользователь поставить отметку по шагу прямо сейчас.

    Условия те же, что проверяет decide_step: заявка «На согласовании», шаг
    ожидает, шаг в текущем блоке маршрута (_current_pending_steps), TTL не истек,
    пользователь — владелец шага (_owns_step) и ещё не оставлял свою отметку
    согласия (повтор API отклонил бы 409). Послаблений по роли нет: админ/
    ОК не «могут всё» — иначе фронт покажет кнопку, которую API отклонит
    (403/409/410). Считается для всех ролей, у не-владельца просто False.
    """
    if request.status != IN_APPROVAL or step.status != STEP_PENDING:
        return False
    if step.order not in pending_orders:
        return False
    if step.expires_at <= now:
        return False
    if not _owns_step(step, user, roster):
        return False
    return not _already_approved(step, user.sam)


def _resolve_dependency(factory, *args):
    """Зависимость вне эндпоинта: подмена из dependency_overrides (тесты/офлайн),
    иначе боевая фабрика (как _current_store в link.py)."""
    from .main import app  # локально против циклического импорта

    override = app.dependency_overrides.get(factory)
    if override is not None:
        return override()
    return factory(*args)


def _owner_display_name(ad_reader: object | None, assignee: str | None) -> str | None:
    """ФИО согласующего по данным AD (только чтение, get_user по sAMAccountName).

    Пустой assignee (групповой шаг) — None: персонального исполнителя нет,
    группа уже отдается в owner_group. AdNotFound/AD недоступен/любая ошибка —
    None (fail-soft): падение AD не должно ронять выдачу заявки.
    """
    if not assignee or ad_reader is None:
        return None
    try:
        card = ad_reader.get_user(assignee)
    except Exception:
        return None
    return (getattr(card, "display_name", None) or "").strip() or None


def _owner_duty(ad_reader: object | None, assignee: str | None) -> str | None:
    """Должность согласующего по данным AD (title из той же карточки, что ФИО).

    Тот же fail-soft и та же видимость по ролям, что у owner_name (должность —
    данные того же уровня, что ФИО). Групповой шаг — None (должность там —
    наименование группы из settings).
    """
    if not assignee or ad_reader is None:
        return None
    try:
        card = ad_reader.get_user(assignee)
    except Exception:
        return None
    return (getattr(card, "title", None) or "").strip() or None


def _enterprise_names_map() -> dict[str, str]:
    """Карта «код предприятия → название» из ключа настроек enterprises.

    Формат — список пар code/name (EnterpriseItem в settings_routes). Дефолтов
    в коде нет: ключа нет, хранилище недоступно (SettingsUnavailable, офлайн)
    или формат неожиданный — пустая карта, тогда enterprise_name в ответе None
    (без 500).
    """
    try:
        store = _resolve_dependency(get_settings_store, get_settings())
        raw = read_setting_value(store, "enterprises")
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}
    names: dict[str, str] = {}
    for item in raw:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if code and name:
                names[code] = name
    return names


def _step_group_names_map() -> dict[str, str]:
    """Карта «id группы → наименование» из контент-ключа allowed_ad_groups.

    Формат — объекты {id, name} (блок B); строки старого формата читаются как
    id=name. Ключа нет, хранилище недоступно или формат неожиданный — пустая
    карта, тогда owner_name у группового шага None (без 500).
    """
    try:
        store = _resolve_dependency(get_settings_store, get_settings())
        raw = read_setting_value(store, "allowed_ad_groups")
    except Exception:
        return {}
    return {item["id"]: item["name"] for item in _groups_with_names(raw)}


def _doc_type_names_map() -> dict[str, str]:
    """Карта «код вида документа → наименование» из таблицы doc_types.

    Чтение через хранилище заявок (RequestsStore.get_doc_types) — тот же
    источник, что валидация doc_type_code. Сбой/пусто/формат неожиданный —
    пустая карта, тогда doc_type_name в ответе None (без 500).
    """
    try:
        store = _resolve_dependency(get_requests_store, get_settings())
        items = store.get_doc_types()
    except Exception:
        return {}
    if not isinstance(items, list):
        return {}
    names: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if code and name:
                names[code] = name
    return names


def _step_owner_name(
    ad_reader: object | None,
    assignee: str | None,
    group_names: dict[str, str] | None,
    owner_group: str,
) -> str | None:
    """Наименование владельца шага: персональный — ФИО из AD, групповой —
    наименование группы из settings (allowed_ad_groups), когда известно."""
    if assignee:
        return _owner_display_name(ad_reader, assignee)
    if group_names:
        return group_names.get(owner_group)
    return None


def _employee_key(
    enterprise: str | None, base_code: str | None, tab_num: str | None
) -> str | None:
    """Ключ карточки сотрудника: enterprise|base_code|tab_num (как windows.tsx).

    Без предприятия или табельного номера ключа нет — ссылка была бы битой."""
    if not enterprise or not tab_num:
        return None
    return "|".join([enterprise.strip(), (base_code or "").strip(), tab_num.strip()])


def _unique_keys(values: list[str | None]) -> list[str]:
    """Значения без пустых и повторов (порядок сохраняется).

    Для пакетного резолва: в справочник уходит один вхождение табельного номера
    или логина, сколько бы заявок и шагов их ни повторяли."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _employee_keys(
    requests_list: list[_Request],
    emp_store: EmployeeSyncStore | None = None,
) -> tuple[dict[str, str | None], dict[tuple[str, int], dict[str, str | None]]]:
    """Пакетный резолв ключей карточек сотрудников по локальному справочнику.

    Один вызов find_by_people на предприятие: по всем табельным номерам заявок и
    всем логинам исполнителей (assignee) шагов — без обращения к employees на
    шаг. Ключ ставится только при ЕДИНСТВЕННОМ совпадении: неоднозначный
    табельный номер или логин (человек числится в двух базах) ключа не даёт,
    иначе ссылка увела бы на чужую карточку.

    Возврат: (request_keys, step_keys), где request_keys — {id заявки: ключ},
    step_keys — {(id заявки, order шага): {employee_key, emp_enterprise,
    emp_base_code, emp_tab_num}} (шаг ключуется в паре с заявкой: order у шагов
    разных заявок совпадает).

    Fail-soft: справочник недоступен (EmployeeSyncUnavailable) или заявок нет —
    пустые словари, выдача списка не роняется."""
    request_keys: dict[str, str | None] = {}
    step_keys: dict[tuple[str, int], dict[str, str | None]] = {}
    if not requests_list:
        return request_keys, step_keys
    if emp_store is None:
        try:
            emp_store = _resolve_dependency(get_employee_sync_store, get_settings())
        except Exception:
            return request_keys, step_keys
    # Табельные номера и логины группируем по предприятию: employees — таблица
    # с предприятием в ключе, поэтому один запрос на предприятие.
    tabs: dict[str, list[str | None]] = {}
    sams: dict[str, list[str | None]] = {}
    for request in requests_list:
        enterprise = (request.enterprise or "").strip()
        if not enterprise:
            continue
        tabs.setdefault(enterprise, []).append(request.tab_num)
        sams.setdefault(enterprise, []).extend(
            step.assignee for step in request.steps if step.assignee
        )
    for enterprise in sorted(set(tabs) | set(sams)):
        enterprise_tabs = _unique_keys(tabs.get(enterprise, []))
        enterprise_sams = _unique_keys(sams.get(enterprise, []))
        if not enterprise_tabs and not enterprise_sams:
            continue
        try:
            rows = emp_store.find_by_people(enterprise, enterprise_sams, enterprise_tabs)
        except Exception:
            # Справочник — мягкая зависимость: недоступен (EmployeeSyncUnavailable)
            # или сломался иначе — выдача списка идёт без ключей, как и _step_owner_name.
            continue
        by_tab: dict[str, list[dict]] = {}
        by_sam: dict[str, list[dict]] = {}
        for row in rows:
            tab = str(row.get("tab_num") or "").strip().lower()
            if tab:
                by_tab.setdefault(tab, []).append(row)
            sam = str(row.get("ad_sam") or "").strip().lower()
            if sam:
                by_sam.setdefault(sam, []).append(row)
        for request in requests_list:
            if (request.enterprise or "").strip() != enterprise:
                continue
            hits = by_tab.get(str(request.tab_num or "").strip().lower(), [])
            if len(hits) == 1:
                request_keys[request.id] = _employee_key(
                    enterprise, hits[0].get("base_code"), hits[0].get("tab_num")
                )
            for step in request.steps:
                if not step.assignee:
                    continue
                step_hits = by_sam.get(step.assignee.strip().lower(), [])
                if len(step_hits) == 1:
                    row = step_hits[0]
                    step_keys[(request.id, step.order)] = {
                        "employee_key": _employee_key(
                            enterprise, row.get("base_code"), row.get("tab_num")
                        ),
                        "emp_enterprise": enterprise,
                        "emp_base_code": row.get("base_code"),
                        "emp_tab_num": row.get("tab_num"),
                    }
    return request_keys, step_keys


def _public_view(
    request: _Request,
    user: CurrentUser,
    *,
    ad_reader: object | None = None,
    enterprise_names: dict[str, str] | None = None,
    group_names: dict[str, str] | None = None,
    doc_type_names: dict[str, str] | None = None,
    employee_keys: dict[str, str | None] | None = None,
    step_employee_keys: dict[tuple[str, int], dict[str, str | None]] | None = None,
    routing_store: DbRoutingStore | None = None,
    profile_names: dict[int, str] | None = None,
    stage_kinds: dict[int, str] | None = None,
) -> RequestOut:
    """Ролевая обрезка: ОК/админы — всё, владелец — без tab_num (ПДн).

    ad_reader, enterprise_names, group_names и doc_type_names резолвятся ОДИН раз
    на вызов и переиспользуются для всех шагов (list_requests поднимает их над
    циклом по заявкам, чтобы на списке не было ни одного лишнего обращения к
    AD/БД на шаг). employee_keys и step_employee_keys — результат пакетного
    резолва _employee_keys на всю выборку (ключи карточек сотрудников для ссылок);
    без них поля employee_key/emp_* остаются None.

    Справочники маршрута (routing_store) — по требованию: резолвятся один раз на
    вызов (состав этапов с кэшем по stage_id и наименование профиля), и только
    когда в заявке есть снимок подбора. Недоступны — fail-soft: profile_name
    None, состав этапа пуст (can_act у реестрного шага тогда False).
    stage_kinds — карта «id этапа → owner_kind», прочитанная вызывающим (список
    заявок читает справочник один раз на выборку); без неё справочник читается
    здесь. Нужна для шагов, перечитанных из Postgres: owner_kind в БД не
    хранится (см. _restore_step_owner_kinds).

    ПДн по ролям: enterprise_name (как enterprise/tab_num/fio) — только
    привилегированным, остальным None; owner_name (ФИО согласующего/наименование
    группы) и owner_duty (должность персонального исполнителя) — всем
    авторизованным, сотруднику полезно видеть, кто согласует;
    assignee, created_by и done_by — это sAMAccountName, поэтому скрываются всем,
    кроме привилегированных (assignee владельцу шага остаётся — это его собственный
    логин); по той же причине из assignees непривилегированному отдаётся только
    его собственный логин, а в approvals — только его отметки (sam=None у
    остальных, сами решения и комментарии видны); can_act считается всегда и
    для всех ролей. Ключи сотрудников
    (employee_key/emp_*) — производные от ПДн (табельный номер), поэтому только
    привилегированным.
    """
    privileged = user.role in ("hr", "hr_admin", "admin")
    reader = ad_reader if ad_reader is not None else _resolve_dependency(get_ad_reader)
    # Карта предприятий нужна только привилегированным (остальным enterprise_name
    # не отдается) — при непривилегированном запросе БД настроек не трогаем.
    names = enterprise_names if enterprise_names is not None else (
        _enterprise_names_map() if privileged else {}
    )
    groups = group_names if group_names is not None else _step_group_names_map()
    doc_names = doc_type_names if doc_type_names is not None else _doc_type_names_map()
    now = _utcnow()
    pending_orders = {s.order for s in _current_pending_steps(request)}
    # Справочники маршрута: состояние маршрута — не ПДн, поэтому резолвим для всех.
    route_store = routing_store if routing_store is not None else _routing_store_or_none()
    roster = _RosterResolver(route_store)
    # owner_kind шага в БД не хранится — восстанавливаем из справочника, иначе
    # право по реестру этапа (assignee в ответе, can_act) не проверялось бы.
    _restore_step_owner_kinds(
        request,
        stage_kinds if stage_kinds is not None else _stage_owner_kinds(route_store),
    )
    profile_labels = (
        profile_names
        if profile_names is not None
        else (_profile_names_map(route_store) if request.profile_id else {})
    )
    steps: list[StepOut] = []
    for s in sorted(request.steps, key=lambda x: x.order):
        # Данные сотрудника шага — из пакета резолва (пусто у непривилегированных).
        emp = (step_employee_keys or {}).get((request.id, s.order), {}) if privileged else {}
        steps.append(
            StepOut(
                order=s.order,
                owner_group=s.owner_group,
                resolver=s.resolver,
                assignee=s.assignee if privileged or s.assignee == user.sam else None,
                owner_name=_step_owner_name(reader, s.assignee, groups, s.owner_group),
                owner_duty=_owner_duty(reader, s.assignee),
                employee_key=emp.get("employee_key"),
                emp_enterprise=emp.get("emp_enterprise"),
                emp_base_code=emp.get("emp_base_code"),
                emp_tab_num=emp.get("emp_tab_num"),
                status=s.status,
                require_comment=s.require_comment,
                expires_at=s.expires_at.isoformat(),
                done_by=s.done_by if privileged else None,
                done_at=s.done_at.isoformat() if s.done_at else None,
                comment=s.comment,
                can_act=_can_act(request, s, user, pending_orders, now, roster),
                stage_title=s.stage_title,
                stage_code=s.stage_code,
                assignees=(
                    s.assignees
                    if privileged
                    else ([user.sam] if user.sam in s.assignees else [])
                ),
                approvals=[
                    StepApprovalOut(
                        sam=(
                            str(item.get("sam") or "")
                            if privileged or str(item.get("sam") or "") == user.sam
                            else None
                        ),
                        at=str(item.get("at") or ""),
                        decision=str(item.get("decision") or ""),
                        comment=item.get("comment"),
                    )
                    for item in s.approvals
                ],
                approval_mode=s.approval_mode,
                optional=s.optional,
                approved_count=len(_approved_sams(s)),
                assignee_count=len(s.assignees),
            )
        )
    return RequestOut(
        id=request.id,
        status=request.status,
        route_origin=request.route_origin,
        profile_id=request.profile_id,
        profile_name=profile_labels.get(request.profile_id) if request.profile_id else None,
        service_id=request.service_id,
        service_name=request.service_name,
        is_manager=request.is_manager,
        enterprise=request.enterprise if privileged else None,
        enterprise_name=names.get(request.enterprise) if privileged else None,
        employee_key=(employee_keys or {}).get(request.id) if privileged else None,
        tab_num=request.tab_num if privileged else None,
        fio=request.fio if privileged else None,
        department=request.department,
        position=request.position,
        category=request.category,
        subject=request.subject,
        content=request.content,
        doc_type_code=request.doc_type_code,
        doc_type_name=doc_names.get(request.doc_type_code) if request.doc_type_code else None,
        blank_id=request.blank_id,
        blank_name=request.blank_name,
        blank_version=request.blank_version,
        blank_header_html=request.blank_header_html,
        blank_footer_lines=list(request.blank_footer_lines or []),
        escalation_hours=request.escalation_hours,
        created_by=request.created_by if privileged else None,
        steps=steps,
    )


def _audit(
    actor: str,
    action: str,
    entity_id: str,
    detail: str = "",
    details: dict | None = None,
) -> None:
    """Запись в append-only журнал (пояснения без ПДн).

    details — структурированные сведения («было/стало», id затронутых шагов):
    в in-memory журнале сохраняются JSON-строкой в detail (табличная колонка
    audit_log.details JSONB заполнится при персистентности аудита, волна B)."""
    text = detail
    if details is not None:
        text = json.dumps(details, ensure_ascii=False, default=str)
    audit_log.append(
        AuditEvent(actor=actor, action=action, entity="request", entity_id=entity_id, detail=text)
    )


def _is_step_viewer(step: _Step, user: CurrentUser, roster: _RosterResolver | None = None) -> bool:
    """Видит ли пользователь шаг заявки (только чтение карточки/списка).

    Группа-владелец шага, любой ответственный из снимка (assignees, миграция
    0013), персональный исполнитель либо участник активного состава этапа
    (owner_kind = stage_roster, состав — из справочника). Отличие от _owns_step
    (право поставить отметку): здесь персональный исполнитель НЕ отменяет
    видимость по группе — правила чтения прежние, добавлены только реестрный
    случай и снимок ответственных (кто может согласовать, тот видит карточку,
    даже если справочники недоступны)."""
    if step.owner_group and step.owner_group in user.groups:
        return True
    if step.assignees and user.sam in step.assignees:
        return True
    if step.assignee and step.assignee == user.sam:
        return True
    return roster is not None and roster.owns(step, user.sam)


def _is_participant(
    request: _Request, user: CurrentUser, roster: _RosterResolver | None = None
) -> bool:
    """Участник заявки: ОК/админ/администратор СЭД, инициатор или владелец шага.

    Минимальная проверка доступа к карточке (комментарии/история) по образцу
    _can_view в documents.py, плюс инициатор заявки (создатель видит свою карточку
    даже без шагов-владельцев). Участник состава этапа (owner_kind = stage_roster)
    — тоже участник: реестр назначает ему этап, как группе-владельцу. Резолвер не
    передан — он строится здесь же (owner_kind шага восстанавливается из
    справочника, см. _restore_step_owner_kinds)."""
    if user.role in ("hr", "hr_admin", "admin", "sed_admin"):
        return True
    if request.created_by == user.sam:
        return True
    if roster is None:
        store = _routing_store_or_none()
        _restore_step_owner_kinds(request, _stage_owner_kinds(store))
        roster = _RosterResolver(store)
    return any(_is_step_viewer(step, user, roster) for step in request.steps)


def _previous_block_steps(request: _Request, order: int) -> list[_Step]:
    """Шаги предыдущего блока маршрута — цель возврата (решение владельца, п.3).

    Предыдущий блок — максимальный block_index строго меньше блока текущего шага
    (кодирование блока: _block_info), у которого есть шаги. Пустой список —
    предыдущего блока нет (первый блок): возврат уходит автору на доработку."""
    current_block, _, _ = _block_info(order)
    by_block: dict[int, list[_Step]] = {}
    for step in request.steps:
        by_block.setdefault(_block_info(step.order)[0], []).append(step)
    earlier = [index for index in by_block if index < current_block]
    if not earlier:
        return []
    return sorted(by_block[max(earlier)], key=lambda s: s.order)


def _reopen_returned_if_current(
    request: _Request, route: RouteSettings, now: datetime
) -> list[_Step]:
    """Возврат доведён до возвращённого блока — снова открыть его шаги.

    После возврата («return») предыдущий блок переоткрывается, а шаг, по которому
    вернули, остаётся STEP_RETURNED. Когда в маршруте не остаётся ожидающих шагов
    (предыдущий блок снова согласован), самый ранний блок с возвращёнными шагами
    становится текущим: они снова STEP_PENDING с новым TTL — заявка продолжает
    маршрут, а владельцы попадают в fresh-рассылку «назначена»."""
    if _current_pending_steps(request):
        return []
    returned_by_block: dict[int, list[_Step]] = {}
    for step in request.steps:
        if step.status != STEP_RETURNED:
            continue
        block_index, _, _ = _block_info(step.order)
        returned_by_block.setdefault(block_index, []).append(step)
    if not returned_by_block:
        return []
    reopened = sorted(returned_by_block[min(returned_by_block)], key=lambda s: s.order)
    for step in reopened:
        step.status = STEP_PENDING
        step.done_by = None
        step.done_at = None
        step.comment = None
        # Отметки прошлого круга к новому не относятся: шаг ждёт всех заново.
        _clear_approvals(step)
        step.expires_at = now + timedelta(days=route.approval_ttl_days)
    return reopened


def _mail_context(request: _Request, settings: Settings) -> dict[str, object]:
    """Контекст письма по заявке (как у worker: без ПДн, ссылка из APP_BASE_URL)."""
    return {
        "fio": request.fio,
        "request_id": request.id,
        "url": request_url(settings.APP_BASE_URL, request.id),
    }


def _notify_skip(request: _Request, event: str, reason: str) -> None:
    """Пропуск уведомления в аудит: причина без ПДн (адресаты/настройки не резолвятся)."""
    _audit("system", "notify.skip", request.id, f"{event} {reason}")


def _notify_assigned(
    request: _Request,
    queue: MailQueue,
    settings_store: DbSettingsStore,
    ad_reader: object | None,
    settings: Settings,
    steps: list[_Step] | None = None,
) -> None:
    """Письмо «назначена» владельцам шагов (W3a): подача, переход этапа,
    возврат на предыдущий блок, повтор просроченного шага.

    steps=None — текущий открытый блок (_current_pending_steps), иначе явно
    заданные шаги. Получатели — mail из AD (только чтение): у персонального шага
    один адресат, у группового — все активные участники группы (очередь
    mail_queue хранит по одному письму на строку, поэтому рассылка разворачивается
    здесь). Шаблон — из mail_templates/settings. Любой сбой (офлайн без AD/БД, нет
    шаблона) не роняет решение по заявке: пропуск пишется в аудит (notify.skip).
    """
    targets = list(steps) if steps is not None else _current_pending_steps(request)
    if ad_reader is None:
        _notify_skip(request, EVENT_ASSIGNED, "no_ad_reader")
        return
    try:
        recipients = [to for step in targets for to in step_owner_mails(step, ad_reader)]
    except Exception as exc:
        _notify_skip(request, EVENT_ASSIGNED, f"resolve_error={type(exc).__name__}")
        return
    if not recipients:
        _notify_skip(request, EVENT_ASSIGNED, "no_recipients")
        return
    # Шаблон письма и адрес отправителя не зависят от шага — читаем один раз
    # на заявку (иначе SELECT на каждый шаг); без адресатов настройки не читаем вовсе.
    try:
        templates = read_setting_value(settings_store, "mail_templates")
        smtp_from = read_setting_value(settings_store, "smtp_from")
    except Exception as exc:
        _notify_skip(request, EVENT_ASSIGNED, f"settings_error={type(exc).__name__}")
        return
    sent = 0
    try:
        for to in recipients:
            if enqueue_event(
                queue,
                to,
                request.id,
                EVENT_ASSIGNED,
                templates or [],
                _mail_context(request, settings),
                subject_prefix=resolve_smtp_from(smtp_from, settings.SMTP_FROM),
            ):
                sent += 1
    except Exception as exc:
        _notify_skip(request, EVENT_ASSIGNED, f"queue_error={type(exc).__name__}")
        return
    if not sent:
        # Адресаты есть, но ни одного письма — нет шаблона события в mail_templates.
        _notify_skip(request, EVENT_ASSIGNED, "no_template")


def _notify_author_closed(
    request: _Request,
    queue: MailQueue,
    settings_store: DbSettingsStore,
    ad_reader: object | None,
    settings: Settings,
) -> None:
    """Письмо «закрыта» (EVENT_CLOSED) автору заявки при DONE.

    Шаблон события — из mail_templates; нет шаблона/адресата/настроек — письмо
    тихо пропускается (notify.skip), как у «назначена»/«возврат». При отзыве
    (REVOKED) и отказе письма нет — закрытие там не наступило.
    """
    to = recipient_mail(ad_reader, request.created_by)
    if not to:
        _notify_skip(request, EVENT_CLOSED, "no_recipients")
        return
    try:
        templates = read_setting_value(settings_store, "mail_templates")
        smtp_from = read_setting_value(settings_store, "smtp_from")
    except Exception as exc:
        _notify_skip(request, EVENT_CLOSED, f"settings_error={type(exc).__name__}")
        return
    try:
        sent = enqueue_event(
            queue,
            to,
            request.id,
            EVENT_CLOSED,
            templates or [],
            _mail_context(request, settings),
            subject_prefix=resolve_smtp_from(smtp_from, settings.SMTP_FROM),
        )
    except Exception as exc:
        _notify_skip(request, EVENT_CLOSED, f"queue_error={type(exc).__name__}")
        return
    if not sent:
        _notify_skip(request, EVENT_CLOSED, "no_template")


def _notify_author_returned(
    request: _Request,
    queue: MailQueue,
    settings_store: DbSettingsStore,
    ad_reader: object | None,
    settings: Settings,
) -> None:
    """Письмо «возврат» автору заявки — заявка ушла на доработку (REWORK).

    Ставится, когда возвращать некуда (предыдущего блока нет). Best-effort:
    сбой AD/настроек/очереди не роняет отметку — пишется notify.skip."""
    to = recipient_mail(ad_reader, request.created_by)
    if not to:
        _notify_skip(request, EVENT_RETURNED, "no_recipients")
        return
    try:
        templates = read_setting_value(settings_store, "mail_templates")
        smtp_from = read_setting_value(settings_store, "smtp_from")
    except Exception as exc:
        _notify_skip(request, EVENT_RETURNED, f"settings_error={type(exc).__name__}")
        return
    try:
        sent = enqueue_event(
            queue,
            to,
            request.id,
            EVENT_RETURNED,
            templates or [],
            _mail_context(request, settings),
            subject_prefix=resolve_smtp_from(smtp_from, settings.SMTP_FROM),
        )
    except Exception as exc:
        _notify_skip(request, EVENT_RETURNED, f"queue_error={type(exc).__name__}")
        return
    if not sent:
        _notify_skip(request, EVENT_RETURNED, "no_template")


# --- Предпросмотр маршрута (read-only, до создания заявки) ---


class RoutePreviewIn(BaseModel):
    """Идентификация сотрудника для предпросмотра маршрута.

    Поля те же, что у CreateRequestIn в части идентификации сотрудника
    (предпросмотр не создаёт заявку, поэтому тема/содержание/вид документа не
    нужны)."""

    enterprise: str
    tab_num: str = Field(description="Табельный номер (ПДн, только ОК)")
    base_code: str | None = Field(default=None, description="Код базы 1С сотрудника")
    ad_sam: str | None = Field(default=None, description="Логин AD сотрудника")
    department: str | None = Field(
        default=None, description="Служба увольняемого (подбор профиля, если нет AD)"
    )
    position: str | None = Field(
        default=None, description="Должность сотрудника (её не задаёт ОК)"
    )
    manager: str | None = Field(
        default=None, description="Замена руководителя (sam) — приоритет над руководителем из AD"
    )
    blank_id: int | None = Field(
        default=None,
        description=(
            "Бланк из справочника (выбирает ОК): предпросмотр показывает его шаги "
            "и ответственных; не задан — подбор по службе, если он включён"
        ),
    )
    dismissed_stages: list[str] = Field(
        default_factory=list, description="Коды этапов, снятых из маршрута"
    )
    dismissed_step_orders: list[int] = Field(
        default_factory=list,
        description="Номера (step_order) шагов бланка, снятых из маршрута (auto)",
    )
    added_stages: list[str] = Field(
        default_factory=list, description="Коды этапов, добавленных в конец маршрута"
    )


class RoutePreviewProfileOut(BaseModel):
    """Профиль маршрута предпросмотра (id/code/name — как в справочнике)."""

    id: int
    code: str
    name: str


class RoutePreviewServiceOut(BaseModel):
    """Служба заявки предпросмотра (id/название/вид бланка)."""

    id: int
    dept_name: str
    blank_kind: str | None = Field(default=None, description="Вид бланка печати (office/line)")


class RoutePreviewStageOut(BaseModel):
    """Этап маршрута предпросмотра: снимок этапа, исполнитель и причина блокировки.

    blocked_reason — почему этап нельзя закрыть (руководитель не найден в AD,
    группа-владелец не задана, состав этапа пуст), иначе None."""

    stage_id: int | None = None
    code: str | None = None
    title: str | None = None
    stage_lines: list[str] = Field(default_factory=list)
    owner_kind: str = Field(default="ad_group", description="Источник исполнителя шага")
    step_order: int | None = Field(
        default=None,
        description=(
            "Номер шага бланка (blank_steps.step_order) — по нему снимают шаг "
            "бланка; у этапа маршрута null (его снимают по коду)"
        ),
    )
    owner_group: str | None = None
    owner_name: str | None = Field(
        default=None,
        description=(
            "Наименование исполнителя: руководитель — ФИО из AD, группа — "
            "наименование из allowed_ad_groups, реестр — ФИО участников этапа"
        ),
    )
    optional: bool = False
    blocked_reason: str | None = None


class RoutePreviewLinkOut(BaseModel):
    """Кандидат AD для подтверждения связи при создании заявки.

    Показываем ОК, к кому привяжется карточка 1С: логин, ФИО, служба, должность.
    Руководитель нужен, чтобы после подтверждения маршрут собрался сразу."""

    sam: str
    fio: str | None = None
    dept_ad: str | None = None
    title_ad: str | None = None
    manager_sam: str | None = None


class RoutePreviewBlankOut(BaseModel):
    """Снимок выбранного бланка в предпросмотре (без ПДн).

    version — версия состава шагов на момент выбора: в заявку пишется тот же
    снимок, поэтому правка справочника не меняет уже выданную заявку."""

    id: int
    code: str
    name: str
    version: int | None = None
    step_count: int = 0


class RoutePreviewOut(BaseModel):
    """Предпросмотр маршрута: подбор профиля/этапов и исполнители по этапам."""

    profile: RoutePreviewProfileOut | None = None
    service: RoutePreviewServiceOut | None = None
    reason: str = Field(description="Причина подбора (reason из app.routing)")
    stages: list[RoutePreviewStageOut] = Field(default_factory=list)
    # blank: выбранный бланк (снимок blank) либо, при подборе по службе, прежнее
    # значение — вид бланка печати из справочника служб (office/line).
    blank: RoutePreviewBlankOut | str | None = Field(
        default=None, description="Снимок выбранного бланка либо вид бланка печати (office/line)"
    )
    blank_source: str | None = Field(
        default=None,
        description=(
            "Откуда взят маршрут: chosen — бланк выбрал ОК, autopick — подобран "
            "по службе (запасной механизм, настройка blank_autopick)"
        ),
    )
    # Пояснение для ОК: например, сотрудник не найден в AD (карточки нет —
    # служба неизвестна, бланк печати пойдёт по умолчанию). Пусто — всё в порядке.
    notice: str | None = Field(default=None, description="Предупреждение для ОК")
    # Состояние связи 1С↔AD выбранного сотрудника:
    #   linked    — связь оформлена, служба и руководитель берутся из AD;
    #   need_link — в AD ровно одна запись с этим ФИО, связь не подтверждена
    #               (её подтверждает ОК кнопкой — POST /requests/route/link-employee);
    #   ambiguous — в AD несколько записей с этим ФИО, выбор за человеком;
    #   absent    — в AD нет записи с этим ФИО.
    # None — сотрудник не выбран или AD недоступен.
    link_state: str | None = Field(default=None, description="Состояние связи 1С↔AD")
    link_candidate: RoutePreviewLinkOut | None = Field(
        default=None, description="Кандидат AD для подтверждения связи"
    )


class _FioResolver:
    """ФИО по логину AD из зеркала users с кэшем на один вызов предпросмотра.

    Только чтение. Fail-soft: карточки нет (или хранилище недоступно) — отдаётся
    сам логин: предпросмотр показывает ОК, кому назначен этап."""

    def __init__(self, store: DbRoutingStore | None) -> None:
        self._store = store
        self._cache: dict[str, str] = {}

    def fio(self, sam: str) -> str:
        """ФИО сотрудника по логину (пусто — логин пуст)."""
        key = str(sam or "").strip()
        if not key:
            return ""
        if key not in self._cache:
            try:
                card = self._store.user_card(key) if self._store is not None else None
            except Exception:
                card = None
            self._cache[key] = str((card or {}).get("fio_full") or "").strip() or key
        return self._cache[key]

    def roster_name(self, roster: _RosterResolver, stage_id: int | None) -> str:
        """ФИО активного состава этапа через запятую (пусто — состав не задан)."""
        names = [self.fio(sam) for sam in roster.sams(stage_id)]
        return ", ".join(name for name in names if name)


def _preview_manager(
    store: DbRoutingStore, ad_reader: object | None, card: dict | None
) -> tuple[str | None, str | None]:
    """Руководитель сотрудника для этапов manager_ad: (sam, ФИО).

    Разрешение — в _manager_sam_from_dn (сначала AD по DN, иначе зеркало users)."""
    manager_dn = str((card or {}).get("manager_dn") or "").strip()
    return _manager_sam_from_dn(manager_dn, store, ad_reader)


def _preview_stages(
    picked: RoutePick,
    manager_sam: str | None,
    manager_name: str | None,
    group_names: dict[str, str],
    roster: _RosterResolver,
    fio: _FioResolver,
) -> list[RoutePreviewStageOut]:
    """Этапы предпросмотра: исполнитель и причина блокировки по каждому шагу.

    Исполнитель по виду шага: у шага бланка — executor_kind (people — ФИО
    согласующих, ad_group — наименование группы из allowed_ad_groups, manager_ad
    — ФИО руководителя из AD), у этапа маршрута — owner_kind (ad_group —
    наименование группы, stage_roster — ФИО состава этапа). Шаг, который нечем
    закрыть, остаётся в ответе с blocked_reason (в отличие от создания заявки,
    где manager_ad без руководителя даёт 422)."""
    stages: list[RoutePreviewStageOut] = []
    for stage_row, optional in picked.stages:
        # Шаг бланка несёт executor_kind, этап маршрута — owner_kind; в ответе
        # оба идут в owner_kind (источник исполнителя).
        is_blank_step = _is_blank_step(stage_row)
        owner_kind = (
            _executor_kind(stage_row.get("executor_kind"))
            if is_blank_step
            else str(stage_row.get("owner_kind") or "ad_group")
        )
        owner_group = str(stage_row.get("owner_group") or "").strip() or None
        stage_id = _int_or_none(stage_row.get("stage_id", stage_row.get("id")))
        assignees = _blank_assignees(stage_row.get("assignees"))
        blocked_reason: str | None = None
        if owner_kind == "manager_ad":
            owner_name = manager_name
            if not manager_sam:
                blocked_reason = (
                    "Не найден руководитель сотрудника в AD — шаг нельзя закрыть "
                    "(укажите замену)"
                )
        elif owner_kind == "people":
            owner_name = (
                ", ".join(name for name in (fio.fio(sam) for sam in assignees) if name)
                or None
            )
            if not assignees:
                blocked_reason = "Не заданы согласующие шага (assignees)"
        elif owner_kind == "stage_roster":
            owner_name = fio.roster_name(roster, stage_id) or None
            if not owner_name:
                blocked_reason = "Не задан состав этапа (stage_assignees)"
        else:
            owner_name = group_names.get(owner_group) if owner_group else None
            if not owner_group:
                blocked_reason = "Не задана группа-владелец шага (owner_group)"
        stages.append(
            RoutePreviewStageOut(
                stage_id=stage_id,
                code=str(stage_row.get("code") or stage_row.get("stage_code") or "") or None,
                step_order=(
                    _int_or_none(stage_row.get("step_order")) if is_blank_step else None
                ),
                title=str(stage_row.get("stage_title") or stage_row.get("title") or "") or None,
                stage_lines=[
                    str(line)
                    for line in (stage_row.get("stage_lines") or [])
                    if isinstance(line, str) and line.strip()
                ],
                owner_kind=owner_kind,
                owner_group=owner_group,
                owner_name=owner_name,
                optional=optional,
                blocked_reason=blocked_reason,
            )
        )
    return stages


LINK_STATE_LINKED = "linked"        # связь оформлена
LINK_STATE_NEED_LINK = "need_link"  # кандидат AD один, связь не подтверждена
LINK_STATE_AMBIGUOUS = "ambiguous"  # в AD несколько записей с этим ФИО
LINK_STATE_ABSENT = "absent"        # в AD нет записи с таким ФИО


def _employee_fio(body: "CreateRequestIn | RoutePreviewIn", tab_num: str | None) -> str:
    """ФИО сотрудника из тела, иначе — из локального справочника по таб. №.

    Нужно, когда связи 1С↔AD ещё нет: без ФИО в AD не найти кандидата."""
    direct = str(getattr(body, "fio", "") or "").strip()
    if direct:
        return direct
    if not tab_num:
        return ""
    try:
        rows = _resolve_dependency(get_employee_sync_store, get_settings()).find_by_people(
            body.enterprise, [], [tab_num]
        )
    except Exception:
        return ""
    if len(rows) != 1:  # неоднозначный табельный номер — ФИО не подставляем
        return ""
    return str(rows[0].get("fio") or "").strip()


def _ad_candidates(ad_reader: object | None, fio: str) -> list:
    """Кандидаты AD по ФИО: точное совпадение displayName (регистр/пробелы — нет)."""
    from .ad_sync import _norm  # лениво: сетевой модуль без нужды не тянем

    if not fio or ad_reader is None:
        return []
    wanted = _norm(fio)
    try:
        return [u for u in ad_reader.search_users(fio) if _norm(u.display_name) == wanted]
    except Exception:  # AD недоступен — предпросмотр не блокируем
        return []


def _link_state(
    body: "CreateRequestIn | RoutePreviewIn",
    store: DbRoutingStore | None,
    sam: str | None,
    ad_reader: object | None,
) -> tuple[str | None, RoutePreviewLinkOut | None]:
    """Состояние связи 1С↔AD по сотруднику и кандидат AD для подтверждения.

    Нужно, чтобы отличать «человека нет в AD» (нужно завести учётку) от «человек
    в AD есть, но связь не подтверждена»: во втором случае ОК подтверждает связь
    кнопкой, и маршрут собирается сразу. Только чтение AD и наших зеркал."""
    if ad_reader is None:
        return None, None
    # sam есть — связь уже оформлена (тело, связка 1С↔AD или ad_sam справочника):
    # подтверждать нечего, дальше маршрут собирается по карточке AD.
    if sam:
        return LINK_STATE_LINKED, None
    fio = _employee_fio(body, body.tab_num)
    candidates = _ad_candidates(ad_reader, fio)
    if not fio:
        return None, None
    if not candidates:
        return LINK_STATE_ABSENT, None
    if len(candidates) > 1:
        return LINK_STATE_AMBIGUOUS, None
    ad_user = candidates[0]
    manager_sam = None
    if store is not None:
        manager_sam = store.manager_sam_by_dn(ad_user.manager_dn or "")
    return LINK_STATE_NEED_LINK, RoutePreviewLinkOut(
        sam=ad_user.sam,
        fio=ad_user.display_name,
        dept_ad=ad_user.department,
        title_ad=ad_user.title,
        manager_sam=manager_sam,
    )


def _route_preview(body: RoutePreviewIn, ad_reader: object | None) -> RoutePreviewOut:
    """Маршрут заявки по справочникам без создания заявки (предпросмотр для ОК).

    Выбранный бланк (blank_id) — основной путь: шаги и ответственные берутся из
    его состава (blank_steps), профиль службы остаётся справочной подсказкой.
    Бланк не выбран — маршрут подбирается по службе (pick_profile), но только если
    включена автоподстановка (настройка blank_autopick): иначе 422 со списком
    доступных бланков. Ничего не пишется (заявки, справочники, аудит) — ответ
    собирается из чтений.

    Fail-soft: справочники пустые — reason подбора и пустой список этапов, без 422
    (в отличие от создания, где молчаливый ручной маршрут опасен)."""
    store = _routing_store_or_none()
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище справочников маршрута недоступно",
        )
    # Бланк выбирает ОК. Не выбран и автоподстановка по службе выключена — маршрут
    # заявке задавать нечем: 422 с тем же текстом и кодом, что при создании
    # (см. _blank_required_422 и _auto_route) — предпросмотр не должен обещать
    # маршрут, который создание не примет.
    blank: dict | None = None
    blank_rows: list[dict] = []
    if body.blank_id is not None:
        blank = _blank_or_422(store, body.blank_id)
        blank_rows = _blank_step_rows(store, blank)
    elif not _blank_autopick_enabled():
        _blank_required_422(store)
    sam = _employee_sam(body)
    card = store.user_card(sam) if sam else None
    department = _ad_or_body((card or {}).get("dept_ad"), body.department or "")
    # Нет карточки AD или пустая служба — маршрут по профилю службы не собрать:
    # объясняем это в предпросмотре (создание в auto вернёт 422, см. _auto_route).
    link_state, link_candidate = _link_state(body, store, sam, ad_reader)
    notice: str | None = None
    if card is None and link_state == LINK_STATE_NEED_LINK:
        notice = (
            "Связь 1С↔AD не оформлена, хотя в AD есть «%s» (%s): подтвердите связь — "
            "служба и руководитель подтянутся, и маршрут соберётся."
            % (link_candidate.fio or link_candidate.sam, link_candidate.sam)
        )
    elif card is None and link_state == LINK_STATE_AMBIGUOUS:
        notice = (
            "В AD несколько записей с этим ФИО — выберите сотрудника в разделе "
            "«Сопоставление 1С↔AD» или переключите маршрут на «Вручную»."
        )
    elif card is None or not department.strip():
        # Про бланк печати говорим честно: выбранный бланк печатается своим
        # макетом, а не «по умолчанию».
        blank_tail = (
            "маршрут по службе не подбирается, но шаги взяты из выбранного бланка."
            if blank is not None
            else "маршрут по службе не подбирается, печать пойдёт бланком по умолчанию."
        )
        notice = (
            "Сотрудник не найден в AD (нет карточки или не указана служба): %s" % blank_tail
        )
    services = store.list_services()
    profiles = store.list_profiles()
    picked = pick_profile(department, services, profiles, [])
    if picked.profile is not None:
        picked = pick_profile(
            department,
            services,
            profiles,
            store.list_profile_steps(_profile_id(picked.profile)),
        )
    if blank is not None:
        picked = pick_blank_steps(picked, blank_rows)
    picked = apply_dismissals_and_additions(
        picked,
        body.dismissed_stages,
        body.added_stages,
        store.list_stages() if body.added_stages else [],
    )
    # Снятие шага бланка по номеру шага — тем же кодом, что в создании заявки
    # (предпросмотр не должен обещать маршрут, который создание не примет).
    picked = _dismiss_blank_steps(picked, body.dismissed_step_orders)
    fio_resolver = _FioResolver(store)
    manager_sam, manager_name = _preview_manager(store, ad_reader, card)
    # Замена руководителя, выбранная ОК, приоритетнее того, что нашли в AD:
    # так предпросмотр показывает именно того, кто пойдёт в маршрут.
    override = str(body.manager or "").strip()
    if override:
        manager_sam = override
        manager_name = fio_resolver.fio(override) or override
    profile = (
        RoutePreviewProfileOut(
            id=_profile_id(picked.profile),
            code=str(picked.profile.get("code") or ""),
            name=str(picked.profile.get("name") or ""),
        )
        if picked.profile is not None
        else None
    )
    service_id = _int_or_none((picked.service or {}).get("id"))
    service_blank_kind = str((picked.service or {}).get("blank_kind") or "").strip() or None
    service = (
        RoutePreviewServiceOut(
            id=service_id,
            dept_name=str(picked.service.get("dept_name") or ""),
            blank_kind=service_blank_kind,
        )
        if picked.service is not None and service_id
        else None
    )
    return RoutePreviewOut(
        profile=profile,
        service=service,
        reason=picked.reason or REASON_PROFILE_NOT_FOUND,
        stages=_preview_stages(
            picked,
            manager_sam,
            manager_name,
            _step_group_names_map(),
            _RosterResolver(store),
            fio_resolver,
        ),
        # Выбранный бланк — снимок; при подборе по службе прежнее значение (вид
        # бланка печати из справочника служб), контракт поля не меняется.
        blank=(
            RoutePreviewBlankOut(
                id=int(blank.get("id") or 0),
                code=str(blank.get("code") or ""),
                name=str(blank.get("name") or ""),
                version=_int_or_none(blank.get("version")),
                step_count=len(blank_rows),
            )
            if blank is not None
            else service_blank_kind
        ),
        blank_source=BLANK_SOURCE_CHOSEN if blank is not None else BLANK_SOURCE_AUTOPICK,
        notice=notice,
        link_state=link_state,
        link_candidate=link_candidate,
    )


class RouteBlankOut(BaseModel):
    """Бланк для выбора при создании заявки (без ПДн: состав и оформление)."""

    id: int
    code: str
    name: str
    description: str | None = None
    step_count: int = 0
    autopick: bool = Field(
        default=False,
        description="Автоподстановка бланка по службе включена (настройка blank_autopick)",
    )


@router.post("/requests/route/preview", response_model=RoutePreviewOut)
def preview_route(
    body: RoutePreviewIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RoutePreviewOut:
    """Предпросмотр маршрута до создания заявки (роль как у создания заявки).

    Ничего не сохраняется и не аудируется: состав выбранного бланка (или подбор
    профиля/этапов по справочникам, если бланк не выбран), исполнитель по каждому
    этапу и вид бланка. Путь объявлен до /requests/{request_id}/..., чтобы не
    конфликтовать с заявкой по id."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        return _route_preview(body, ad_reader)
    except RoutingUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/requests/route/blanks", response_model=list[RouteBlankOut])
def list_route_blanks(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> list[RouteBlankOut]:
    """Доступные бланки для селекта ОК (роль как у создания заявки).

    Только активные бланки с шагами (step_count > 0): пустой бланк выбрать
    нельзя — маршрут заявке нечем задать. autopick (состояние запасного механизма
    подбора по службе) повторяется в каждой строке — форма показывает по нему,
    что маршрут без выбора бланка собран не будет. Путь объявлен до
    /requests/{request_id}/..., чтобы не конфликтовать с заявкой по id."""
    settings.ensure_read_only()
    _require_hr(user)
    store = _routing_store_or_none()
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Хранилище справочников маршрута недоступно",
        )
    autopick = _blank_autopick_enabled()
    try:
        blanks = _available_blanks(store)
    except RoutingUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return [
        RouteBlankOut(
            id=int(item.get("id") or 0),
            code=str(item.get("code") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or "") or None,
            step_count=_int_or_none(item.get("step_count")) or 0,
            autopick=autopick,
        )
        for item in blanks
        if item.get("id") and (_int_or_none(item.get("step_count")) or 0) > 0
    ]


@router.post("/requests", response_model=RequestOut, status_code=201)
def create_request(
    body: CreateRequestIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    route: RouteSettings = Depends(get_route_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Создание заявки от ОК: маршрут из бланка (auto), иначе ручной конструктор.

    В режиме auto выбранный ОК бланк (blank_id) задаёт шаги маршрута, а его
    снимок (blank_id/blank_name/blank_version + шапка/подвал) пишется
    в заявку. Бланк не выбран и автоподстановка по службе выключена — 422 с
    вариантами (см. _auto_route); в custom маршрут задаёт конструктор, бланк
    указывать не обязательно."""
    settings.ensure_read_only()
    _require_hr(user)
    if body.doc_type_code:
        # Вид документа — из таблицы doc_types: передан — обязан существовать.
        try:
            known = store.get_doc_type(body.doc_type_code)
        except RequestsUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        if known is None:
            raise HTTPException(
                status_code=422, detail="Неизвестный вид документа (doc_type_code)"
            )
    now = _utcnow()
    # Категория задаёт ОК: справочник «должность → категория» (position_to_category)
    # снят вместе с шаблонами маршрута, выводить категорию больше не из чего.
    category = body.category
    # Снимок подбора маршрута по справочникам (route_mode = auto); для ручного
    # маршрута остаётся None — тогда поля profile/service в ответе пустые.
    auto: dict | None = None
    if body.blocks is not None:
        # Явный конструктор ОК (блоками) — приоритетнее steps.
        for block in body.blocks:
            if len(block.steps) > 99:
                raise HTTPException(
                    status_code=422,
                    detail="Блок маршрута больше 99 шагов — разбейте на несколько блоков",
                )
        if not body.blocks or all(not b.steps for b in body.blocks):
            raise HTTPException(
                status_code=422, detail="Маршрут пуст: добавьте блок с исполнителями"
            )
        steps = _build_steps([], route.approval_ttl_days, body.manager, now, blocks=body.blocks)
        origin = "custom"
    else:
        if body.steps:
            # Ручной маршрут от разрешенной группы — только явными шагами ОК.
            steps = _build_steps(body.steps, route.approval_ttl_days, body.manager, now)
            origin = "custom"
        elif body.route_mode == "auto":
            # Маршрут из справочников: выбранный бланк (его шаги) либо запасной
            # подбор по службе за blank_autopick. Маршрут не собрался — 422 с
            # причиной (см. _auto_route), молчаливого отката в ручной маршрут нет.
            auto = _auto_route_entry(body, route, now)
            steps = auto["steps"]
            # Маршрут собран из справочника: origin=template (в БД CHECK
            # migration 0001 допускает только 'template'/'manual').
            origin = "template"
        else:
            raise HTTPException(
                status_code=422,
                detail="Маршрут не задан: выберите бланк или задайте маршрут вручную (steps)",
            )
    # Данные сотрудника для auto — из карточки AD (пустые не затирают тело).
    # Категорию задаёт ОК, эскалация считается выше по должности 1С тела
    # (справочник эскалации ключуется должностью 1С, а не должностью из AD).
    department = auto["department"] if auto else body.department
    position = auto["position"] if auto else body.position
    try:
        request = _Request(
            id=store.next_id(),
            status=DRAFT,
            route_origin=origin,
            enterprise=body.enterprise,
            fio=body.fio,
            tab_num=body.tab_num,
            department=department,
            position=position,
            category=category,
            subject=body.subject,
            content=body.content,
            doc_type_code=body.doc_type_code,
            escalation_hours=route.position_escalation.get(body.position),
            created_by=user.sam,
            steps=steps,
            profile_id=auto["profile_id"] if auto else None,
            service_id=auto["service_id"] if auto else None,
            service_name=auto["service_name"] if auto else None,
            is_manager=auto["is_manager"] if auto else False,
            # Снимок выбранного бланка (auto): правка справочника не должна
            # менять уже выданную заявку.
            blank_id=auto["blank_id"] if auto else None,
            blank_name=auto["blank_name"] if auto else None,
            blank_version=auto["blank_version"] if auto else None,
            blank_header_html=auto["blank_header_html"] if auto else None,
            blank_footer_lines=list(auto["blank_footer_lines"] if auto else []),
        )
        store.create(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "request.create", request.id, f"origin={origin}")
    return _public_view(request, user)


@router.get("/requests", response_model=list[RequestOut])
def list_requests(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> list[RequestOut]:
    """Список: ОК/админы — все, владелец — только свои шаги (урезанные без ПДн)."""
    settings.ensure_read_only()
    try:
        requests = store.list_all()
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    # Резолв AD/предприятий/групп/видов — один раз над циклом (иначе запрос на
    # каждый шаг).
    ad_reader = _resolve_dependency(get_ad_reader)
    enterprise_names = _enterprise_names_map() if _is_hr(user) else {}
    group_names = _step_group_names_map()
    doc_type_names = _doc_type_names_map()
    # Справочники маршрута — один раз на выборку (состав этапов с кэшем по
    # stage_id и наименование профиля иначе читались бы на каждую заявку).
    routing_store = _routing_store_or_none()
    profile_names = _profile_names_map(routing_store)
    # owner_kind шагов и состав этапов — по всей выборке: справочник этапов
    # читается один раз (иначе на каждую заявку), а реестр кэшируется на вызов.
    stage_kinds = _stage_owner_kinds(routing_store)
    roster = _RosterResolver(routing_store)
    for item in requests:
        _restore_step_owner_kinds(item, stage_kinds)
    # Ключи карточек сотрудников (ссылки на карточку): пакетно по всей выборке,
    # только привилегированным (в _public_view обрезка по роли).
    employee_keys, step_employee_keys = _employee_keys(requests)
    if _is_hr(user):
        return [
            _public_view(
                r,
                user,
                ad_reader=ad_reader,
                enterprise_names=enterprise_names,
                group_names=group_names,
                doc_type_names=doc_type_names,
                employee_keys=employee_keys,
                step_employee_keys=step_employee_keys,
                routing_store=routing_store,
                profile_names=profile_names,
                stage_kinds=stage_kinds,
            )
            for r in requests
        ]
    mine = [
        r for r in requests
        if any(_is_step_viewer(s, user, roster) for s in r.steps)
    ]
    return [
        _public_view(
            r,
            user,
            ad_reader=ad_reader,
            enterprise_names=enterprise_names,
            group_names=group_names,
            doc_type_names=doc_type_names,
            employee_keys=employee_keys,
            step_employee_keys=step_employee_keys,
            routing_store=routing_store,
            profile_names=profile_names,
            stage_kinds=stage_kinds,
        )
        for r in mine
    ]


class FolderOut(BaseModel):
    """Папка списка заявок: id + русский заголовок + счетчик заявок."""

    id: str
    title: str
    count: int


@router.get("/folders", response_model=list[FolderOut])
def list_folders(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> list[FolderOut]:
    """Счетчики папок (Волна 1): всем авторизованным; владельцу — только mine."""
    settings.ensure_read_only()
    try:
        requests = store.list_all()
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    # Счетчик «Мои задачи» — то же правило видимости, что у выборки
    # list_requests.mine (в т.ч. участник состава этапа).
    routing_store = _routing_store_or_none()
    roster = _RosterResolver(routing_store)
    stage_kinds = _stage_owner_kinds(routing_store)
    for item in requests:
        _restore_step_owner_kinds(item, stage_kinds)
    folders: list[FolderOut] = []
    if _is_hr(user):
        folders = [
            FolderOut(
                id="agreement",
                title="На согласовании",
                count=sum(1 for r in requests if r.status == IN_APPROVAL),
            ),
            FolderOut(
                id="revision",
                title="На доработке",
                count=sum(1 for r in requests if r.status == REWORK),
            ),
            FolderOut(
                id="execution",
                title="К исполнению",
                count=sum(1 for r in requests if r.status in (AGREED, TO_EXECUTION)),
            ),
            FolderOut(
                id="done",
                title="Завершённые",
                count=sum(
                    1 for r in requests if r.status in (DONE, REJECTED, REVOKED)
                ),
            ),
            FolderOut(
                id="draft",
                title="Черновики",
                count=sum(1 for r in requests if r.status == DRAFT),
            ),
        ]
    folders.append(
        FolderOut(
            id="mine",
            title="Мои задачи",
            count=sum(
                1 for r in requests
                if any(_is_step_viewer(s, user, roster) for s in r.steps)
            ),
        )
    )
    return folders


@router.get("/requests/{request_id}", response_model=RequestOut)
def get_request(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Карточка заявки с ролевой обрезкой ПДн."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if not _is_hr(user):
        routing_store = _routing_store_or_none()
        # owner_kind шага в БД не хранится — без восстановления из справочника
        # участник состава этапа не считался бы владельцем шага (403).
        _restore_step_owner_kinds(request, _stage_owner_kinds(routing_store))
        if not any(
            _is_step_viewer(s, user, _RosterResolver(routing_store))
            for s in request.steps
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    return _public_view(request, user)


@router.delete("/requests/{request_id}")
def delete_request(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> dict:
    """Удалить заявку (только админ; для тестового периода). Удаление необратимо."""
    settings.ensure_read_only()
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Удаление заявок — только админ",
        )
    try:
        _get_request_or_404(store, request_id)
        store.delete(request_id)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "request.delete", request_id)
    return {"deleted": request_id}


@router.post("/requests/{request_id}/submit", response_model=RequestOut)
def submit_request(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    route: RouteSettings = Depends(get_route_settings),
    mail_queue: MailQueue = Depends(get_mail_queue),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RequestOut:
    """Черновик/На доработке → На согласовании (только ОК-автор, нужны шаги).

    Из «На доработке» возврат идёт первому согласующему без решения: шаги со
    статусом «отклонён»/«возвращён» снова ожидают с новым сроком, уже
    согласованные шаги остаются закрытыми (их решения сохраняются). Причину
    отказа в comment/approvals шага не сбрасываем — она и есть суть доработки,
    и в audit_log она бы не осталась; отметки просроченного шага, наоборот,
    сбрасываются (как в reissue): они относятся к истёкшему кругу. Если маршрут
    правили через PATCH /requests/{id}/steps, заявка переведена в Черновик и
    ветка не выполняется: там маршрут уже задан новыми ожидающими шагами.
    Срок ожидающих шагов поздних блоков обновляется тем же кругом — иначе
    затянувшаяся доработка открыла бы следующий блок уже просроченным.

    При подаче ставится письмо «назначена» владельцу первого шага (W3a);
    офлайн/нет AD/шаблона — уведомление тихо пропускается.
    """
    settings.ensure_read_only()
    _require_hr(user)
    reopened: list[int] = []
    try:
        request = _get_request_or_404(store, request_id)
        if request.status not in (DRAFT, REWORK):
            raise HTTPException(status_code=409, detail="Подать можно только из Черновика/На доработке")
        if not request.steps:
            raise HTTPException(status_code=422, detail="Маршрут пуст: добавьте шаги")
        now = _utcnow()
        if request.status == REWORK:
            # Шаги без решения (отказ/возврат/просрочка) снова ожидают: иначе
            # после подачи не осталось бы ожидающего шага и заявка висела бы
            # в «На согласовании» без единой доступной кнопки.
            for step in request.steps:
                expired = step.status == STEP_EXPIRED
                if step.status not in (STEP_REJECTED, STEP_RETURNED, STEP_EXPIRED):
                    continue
                step.status = STEP_PENDING
                step.done_by = None
                step.done_at = None
                step.expires_at = now + timedelta(days=route.approval_ttl_days)
                if expired:
                    # Отметки истёкшего круга к новому сроку не относятся (как в
                    # reissue): оставленная approve закрыла бы шаг по старой
                    # отметке, а сам согласовавший получил бы 409 «Вы уже
                    # согласовали этот шаг».
                    step.comment = None
                    _clear_approvals(step)
                reopened.append(step.order)
            # Срок поздних блоков тоже обновляем: доработка могла занять дольше
            # approval_ttl_days, и тогда следующий блок открылся бы уже
            # просроченным — can_act у всех был бы false до срабатывания воркера.
            for step in request.steps:
                if step.status == STEP_PENDING:
                    step.expires_at = now + timedelta(days=route.approval_ttl_days)
        request.status = IN_APPROVAL
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(
        user.sam,
        "request.submit",
        request.id,
        ("reopened=" + ",".join(str(order) for order in reopened)) if reopened else "",
    )
    _notify_assigned(request, mail_queue, settings_store, ad_reader, settings)
    # Ридер AD передан зависимостью; при ad_reader=None (AD недоступен) _public_view
    # резолвит его повторно (fail-soft) — без ФИО согласующего, но ответ 200.
    return _public_view(request, user, ad_reader=ad_reader)


@router.post("/requests/{request_id}/withdraw", response_model=RequestOut)
def withdraw_request(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Отзыв заявки → Отозвано (только разрешенная группа)."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        if request.status in (DONE, REJECTED, REVOKED):
            raise HTTPException(status_code=409, detail="Заявка уже закрыта")
        request.status = REVOKED
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "request.withdraw", request.id, "")
    return _public_view(request, user)


# Отзыв заявки принимается и POST (как остальные действия над заявкой), и PATCH —
# QA-проверка зовёт `curl -X PATCH /requests/{id}/cancel`. Два декоратора вместо
# api_route(methods=[...]): у api_route оба метода получают один operation_id, и
# сборка схемы OpenAPI сыпет Duplicate Operation ID. Тело и аудит те же, поэтому
# отзыв через любой из методов не может дать разные статусы.
@router.post("/requests/{request_id}/cancel", response_model=RequestOut)
@router.patch("/requests/{request_id}/cancel", response_model=RequestOut)
def cancel_request(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Отзыв заявки → Отозвано: синоним POST /requests/{id}/withdraw.

    Клиент и QA-проверки зовут отзыв «cancel». Логика, аудит и проверка закрытой
    заявки — общие с /withdraw, поэтому отозванная заявка не может получить
    разные статусы через два входа."""
    return withdraw_request(request_id, user=user, settings=settings, store=store)


@router.post("/requests/{request_id}/to-execution", response_model=RequestOut)
def to_execution(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Согласовано → К исполнению (только разрешенная группа)."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        if request.status != AGREED:
            raise HTTPException(status_code=409, detail="К исполнению — только из Согласовано")
        request.status = TO_EXECUTION
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "request.to_execution", request.id, "")
    return _public_view(request, user)


@router.post("/requests/{request_id}/finish", response_model=RequestOut)
def finish_request(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    mail_queue: MailQueue = Depends(get_mail_queue),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RequestOut:
    """К исполнению → Завершено (только разрешенная группа).

    При закрытии ставится письмо «закрыта» (EVENT_CLOSED) автору заявки; нет
    шаблона/адресата/настроек — тихо пропускается (notify.skip)."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        if request.status != TO_EXECUTION:
            raise HTTPException(status_code=409, detail="Завершить — только из К исполнению")
        request.status = DONE
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "request.finish", request.id, "")
    _notify_author_closed(request, mail_queue, settings_store, ad_reader, settings)
    return _public_view(request, user, ad_reader=ad_reader)


@router.post("/requests/{request_id}/steps/{order}/decision", response_model=RequestOut)
def decide_step(
    request_id: str,
    order: int,
    body: DecisionIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
    route: RouteSettings = Depends(get_route_settings),
    mail_queue: MailQueue = Depends(get_mail_queue),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RequestOut:
    """Отметка владельца: согласие/отказ/возврат (комментарий по require_comment).

    Отказ — заявка инициатору на доработку (REWORK) независимо от позиции шага:
    маршрут сам не переоткрывается, решает инициатор (комментарии + правка шагов
    либо повторная отправка), уже полученные согласия сохраняются. Шаг при этом
    остаётся «отклонен» (в отличие от «возвращен»).

    Несколько ответственных (миграция 0013): право даёт снимок assignees, своя
    отметка согласия повторно не принимается (409). Согласие закрывает шаг, если
    режим параллельный или согласовали все ответственные; иначе шаг остаётся
    «ожидает» (заявка не двигается, уведомление не уходит, аудит
    step.approve_partial). Отказ и возврат действуют сразу от любого
    ответственного — они не требуют согласия остальных.

    Уведомление «назначена»: при согласии — новые ожидающие шаги следующего
    блока (без дублей внутри параллельного блока), при возврате — владельцы
    переоткрытого предыдущего блока, а при отказе и если возвращать некуда —
    автор заявки («возврат», заявка на доработке)."""
    settings.ensure_read_only()
    reopened: list[_Step] | None = None
    before_orders: set[int] = set()
    step_closed = False
    # Справочники маршрута для этого решения: состав этапов (owner_kind =
    # stage_roster, кэш на вызов) и owner_kind шага, которого в request_steps нет.
    routing_store = _routing_store_or_none()
    roster = _RosterResolver(routing_store)
    try:
        request = _get_request_or_404(store, request_id)
        _restore_step_owner_kinds(request, _stage_owner_kinds(routing_store))
        if request.status != IN_APPROVAL:
            raise HTTPException(status_code=409, detail="Отметки — только в статусе На согласовании")
        step = next((s for s in request.steps if s.order == order), None)
        if step is None:
            raise HTTPException(status_code=404, detail="Шаг не найден")
        if step.status != STEP_PENDING:
            raise HTTPException(status_code=409, detail="Шаг уже закрыт")
        _check_step_owner(step, user, roster)
        if body.decision == "approve" and _already_approved(step, user.sam):
            raise HTTPException(status_code=409, detail="Вы уже согласовали этот шаг")
        now = _utcnow()
        # Просрочка TTL — шаг в просроченные, заявка на доработку, решение отклоняется.
        if now > step.expires_at:
            step.status = STEP_EXPIRED
            request.status = REWORK
            store.update(request)
            _audit(user.sam, "step.expired", request.id, f"order={order}")
            raise HTTPException(
                status_code=410, detail="Срок шага истек: нужен повтор (reissue)"
            )
        # Ожидающие шаги блока ДО отметки — по ним позже считаются новые (переход этапа).
        before_orders = {s.order for s in _current_pending_steps(request)}
        if order not in before_orders:
            raise HTTPException(
                status_code=409,
                detail="Шаг не в текущем блоке маршрута (строгий порядок/параллельный блок)",
            )
        _check_comment(step, body.decision, body.comment)
        _record_approval(step, user.sam, body.decision, now, body.comment)
        if body.decision == "approve":
            if not _closes_on_approve(step):
                # Отметка учтена, но шаг ждёт остальных ответственных: заявка не
                # двигается, done_by/done_at шага не заполняются, письмо
                # «назначена» не уходит (шаг не перешёл дальше).
                _audit(
                    user.sam,
                    "step.approve_partial",
                    request.id,
                    "order=%d progress=%d/%d"
                    % (order, len(_approved_sams(step)), len(step.assignees)),
                )
            else:
                step.status = STEP_APPROVED
                step.done_by = user.sam
                step.done_at = now
                step.comment = (body.comment or "").strip() or None
                step_closed = True
                _audit(user.sam, "step.approve", request.id, f"order={order}")
                if all(s.status == STEP_APPROVED for s in request.steps):
                    request.status = AGREED
                else:
                    # Предыдущий блок снова согласован — возвращённые шаги позднего
                    # блока возвращаются в работу (иначе заявка зависла бы без
                    # ожидающих шагов), уведомление уйдёт им как fresh.
                    _reopen_returned_if_current(request, route, now)
        elif body.decision == "reject":
            step.status = STEP_REJECTED
            step.done_by = user.sam
            step.done_at = now
            step.comment = (body.comment or "").strip() or None
            _audit(user.sam, "step.reject", request.id, f"order={order}")
            # Отказ — заявка инициатору на доработку (REWORK), маршрут не
            # переоткрывается автоматически: инициатор смотрит причину, пишет
            # комментарии и правит шаги (PATCH /requests/{id}/steps) либо просто
            # отправляет заявку заново. Уже полученные согласия предыдущих
            # согласующих при этом сохраняются — шаги «согласован» не трогаем.
            request.status = REWORK
        else:
            step.status = STEP_RETURNED
            step.done_by = user.sam
            step.done_at = now
            step.comment = (body.comment or "").strip() or None
            _audit(user.sam, "step.return", request.id, f"order={order}")
            # Возврат — на предыдущий блок: его шаги снова в работе с новым TTL
            # (route.approval_ttl_days), заявка остаётся на согласовании. Нет
            # предыдущего блока — заявка на доработку, уведомляется автор.
            reopened = _previous_block_steps(request, order)
            if reopened:
                for reopened_step in reopened:
                    reopened_step.status = STEP_PENDING
                    reopened_step.done_by = None
                    reopened_step.done_at = None
                    reopened_step.comment = None
                    _clear_approvals(reopened_step)
                    reopened_step.expires_at = now + timedelta(days=route.approval_ttl_days)
                request.status = IN_APPROVAL
            else:
                request.status = REWORK
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if body.decision == "approve":
        # Переход этапа: письмо только НОВЫМ ожидающим шагам (в параллельном
        # блоке остальные уже получили письмо при входе в блок — дублей нет).
        # Шаг, ждущий остальных ответственных (отметка учтена, но не закрыта),
        # следующих шагов не открывает — письма не будет.
        if step_closed and request.status == IN_APPROVAL:
            fresh = [
                s for s in _current_pending_steps(request) if s.order not in before_orders
            ]
            if fresh:
                _notify_assigned(
                    request, mail_queue, settings_store, ad_reader, settings, steps=fresh
                )
    elif body.decision in ("return", "reject"):
        if reopened:
            _notify_assigned(
                request, mail_queue, settings_store, ad_reader, settings, steps=reopened
            )
        else:
            _notify_author_returned(
                request, mail_queue, settings_store, ad_reader, settings
            )
    return _public_view(request, user)


@router.post("/requests/{request_id}/steps/{order}/reissue", response_model=RequestOut)
def reissue_step(
    request_id: str,
    order: int,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    route: RouteSettings = Depends(get_route_settings),
    store: RequestsStore = Depends(get_requests_store),
    mail_queue: MailQueue = Depends(get_mail_queue),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RequestOut:
    """Повтор просроченного шага: новый TTL, снова в работу (только ОК).

    Возвращённый в работу шаг — уведомление «назначена» его владельцу(ям)."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        step = next((s for s in request.steps if s.order == order), None)
        if step is None:
            raise HTTPException(status_code=404, detail="Шаг не найден")
        if step.status != STEP_EXPIRED:
            raise HTTPException(status_code=409, detail="Повтор — только для просроченного шага")
        step.status = STEP_PENDING
        step.done_by = None
        step.done_at = None
        step.comment = None
        # Повторный круг: отметки прежнего срока к нему не относятся.
        _clear_approvals(step)
        step.expires_at = _utcnow() + timedelta(days=route.approval_ttl_days)
        request.status = IN_APPROVAL
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "step.reissue", request.id, f"order={order}")
    _notify_assigned(
        request, mail_queue, settings_store, ad_reader, settings, steps=[step]
    )
    return _public_view(request, user)


@router.patch("/requests/{request_id}/steps", response_model=RequestOut)
def replace_steps(
    request_id: str,
    body: StepsReplaceIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    route: RouteSettings = Depends(get_route_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Правка шагов (включая замену руководителя) — только разрешенная группа + audit.

    Закрытые шаги (с решениями) сохраняются как есть, ожидающие пересобираются
    из payload; снимок этапа (текст/название для печати) переносится на шаги с
    тем же исполнителем, чтобы печать бланка не потеряла текст."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        if request.status in (DONE, REJECTED, REVOKED):
            raise HTTPException(status_code=409, detail="Закрытая заявка не правится")
        # Блочный маршрут: блок >= 1 (order >= 1000) или параллельный блок 0
        # (order 101..199). Одиночный последовательный блок неотличим от плоского
        # списка, поэтому его правка плоскими шагами структуру не разрушает.
        if body.blocks is None and any(s.order > PARALLEL_MARK for s in request.steps):
            # Блочный маршрут нельзя переписать плоским списком: перенумерация
            # разрушила бы блоки. Нужен payload blocks (см. ветку ниже).
            raise HTTPException(
                status_code=409,
                detail="Правка блочного маршрута — передайте blocks (плоские steps не подходят)",
            )
        now = _utcnow()
        # Закрытые шаги сохраняем, ожидающие — заменяем новым набором.
        kept = [s for s in request.steps if s.status != STEP_PENDING]
        pending_before = [s for s in request.steps if s.status == STEP_PENDING]
        if body.blocks is not None:
            # Блочная правка: новые блоки продолжают нумерацию после уже
            # существующих, чтобы order не столкнулся с сохранёнными шагами.
            for block in body.blocks:
                if len(block.steps) > 99:
                    raise HTTPException(
                        status_code=422,
                        detail="Блок маршрута больше 99 шагов — разбейте на несколько блоков",
                    )
            if not body.blocks or all(not b.steps for b in body.blocks):
                raise HTTPException(
                    status_code=422, detail="Маршрут пуст: добавьте блок с исполнителями"
                )
            # Нумерацию продолжаем после РЕАЛЬНО сохранённых (закрытых) шагов:
            # плоский order 1..N неотличим от блока 0, и без этого новый order
            # совпал бы с закрытым, а decide_step всегда попадал бы в закрытый.
            existing = [s.order // BLOCK_ORDER_BASE for s in kept]
            fresh = _build_steps(
                [], route.approval_ttl_days, body.manager, now, blocks=body.blocks
            )
            shift = (max(existing) + 1 if existing else 0) * BLOCK_ORDER_BASE
            for step in fresh:
                step.order += shift
        else:
            if not body.steps:
                raise HTTPException(status_code=422, detail="Список шагов не может быть пустым")
            fresh = _build_steps(body.steps, route.approval_ttl_days, body.manager, now)
            base = len(kept)
            for index, step in enumerate(fresh):
                step.order = base + index + 1
        request.steps = sorted(kept + fresh, key=lambda s: s.order)
        # Текст и название этапа печати переносим на пересобранные шаги с тем же
        # исполнителем: иначе сохранение маршрута обнулило бы текст ещё не
        # пройденных шагов (печать идёт по снимку выданной заявки).
        _carry_stage_snapshot(fresh, pending_before)
        request.route_origin = "custom"
        if request.status == REWORK:
            request.status = DRAFT
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "steps.patch", request.id, (body.reason or "")[:200])
    return _public_view(request, user)


# --- Комментарии (таблица request_comments, решения пользователя 2026-10-02) ---


@router.get("/requests/{request_id}/comments", response_model=list[CommentOut])
def list_comments(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> list[CommentOut]:
    """Комментарии заявки — участникам (ОК/админы, инициатор, владельцы шагов)."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
        comments = store.list_comments(request.id)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if not _is_participant(request, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    return [CommentOut(**c) for c in comments]


@router.post("/requests/{request_id}/comments", response_model=list[CommentOut])
def add_comment(
    request_id: str,
    body: CommentIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> list[CommentOut]:
    """Добавить комментарий (автор/участники/ОК), вернуть полный список."""
    settings.ensure_read_only()
    if not body.body.strip():
        raise HTTPException(status_code=422, detail="Комментарий не может быть пустым")
    try:
        request = _get_request_or_404(store, request_id)
        if not _is_participant(request, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке"
            )
        store.add_comment(request.id, user.sam, body.body, body.kind, body.step_id)
        comments = store.list_comments(request.id)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return [CommentOut(**c) for c in comments]


# --- История изменений (append-only audit_log) ---


@router.get("/requests/{request_id}/history")
def get_history(
    request_id: str,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> list[dict]:
    """История заявки участникам: события request/document/attachment по id, по at."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
        if not _is_participant(request, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке"
            )
        events: list[dict] = []
        for entity in ("request", "document", "attachment"):
            events.extend(store.get_history(entity, request.id))
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    events.sort(key=lambda e: e["at"])
    return events


# --- Правка карточки и откат маршрута (только администратор СЭД) ---


@router.patch("/requests/{request_id}", response_model=RequestOut)
def update_request(
    request_id: str,
    body: UpdateRequestIn,
    user: CurrentUser = Depends(SedAdminUser),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Правка темы/содержания/вида документа — только sed_admin, в audit_log."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
        if request.status in (DONE, REJECTED, REVOKED):
            raise HTTPException(status_code=409, detail="Закрытая заявка не правится")
        if body.doc_type_code is not None:
            if store.get_doc_type(body.doc_type_code) is None:
                raise HTTPException(
                    status_code=422, detail="Неизвестный вид документа (doc_type_code)"
                )
        changes: dict[str, tuple[str | None, str | None]] = {}
        if body.subject is not None:
            changes["subject"] = (request.subject, body.subject)
            request.subject = body.subject
        if body.content is not None:
            changes["content"] = (request.content, body.content)
            request.content = body.content
        if body.doc_type_code is not None:
            changes["doc_type_code"] = (request.doc_type_code, body.doc_type_code)
            request.doc_type_code = body.doc_type_code
        if not changes:
            raise HTTPException(status_code=422, detail="Нет полей для обновления")
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(
        user.sam,
        "request.update",
        request.id,
        "",
        details={
            "was": {k: v[0] for k, v in changes.items()},
            "became": {k: v[1] for k, v in changes.items()},
        },
    )
    return _public_view(request, user)


@router.post("/requests/{request_id}/rollback", response_model=RequestOut)
def rollback_request(
    request_id: str,
    body: RollbackIn,
    user: CurrentUser = Depends(SedAdminUser),
    settings: Settings = Depends(get_settings),
    route: RouteSettings = Depends(get_route_settings),
    store: RequestsStore = Depends(get_requests_store),
    mail_queue: MailQueue = Depends(get_mail_queue),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RequestOut:
    """Откат на выбранный шаг и все последующие — только sed_admin.

    Переоткрываются шаги с order >= целевого (status=STEP_PENDING, сброс
    done_by/done_at/comment, новый TTL по approval_ttl_days), заявка снова
    «На согласовании», затронутые согласующие уведомляются повторно."""
    settings.ensure_read_only()
    try:
        target_order = int(body.to_step_id)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="to_step_id должен быть числом (order шага)"
        )
    try:
        request = _get_request_or_404(store, request_id)
        step = next((s for s in request.steps if s.order == target_order), None)
        if step is None:
            raise HTTPException(status_code=404, detail="Шаг не найден")
        now = _utcnow()
        affected = [s for s in request.steps if s.order >= target_order]
        for s in affected:
            s.status = STEP_PENDING
            s.done_by = None
            s.done_at = None
            s.comment = None
            s.expires_at = now + timedelta(days=route.approval_ttl_days)
        request.status = IN_APPROVAL
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(
        user.sam,
        "request.rollback",
        request.id,
        "",
        details={"steps": [s.order for s in affected]},
    )
    _notify_assigned(
        request, mail_queue, settings_store, ad_reader, settings, steps=affected
    )
    return _public_view(request, user)

# Заявки на увольнение и маршруты согласования (волна B1, offline).
# Хранилище — зависимость get_requests_store: in-memory на офлайне/в тестах,
# Postgres (DbRequestsStore) на стенде; логика эндпоинтов не зависит от
# реализации (интерфейс RequestsStore в requests_store.py).
# Прикладные настройки (position_to_category/escalation, approval_ttl_days,
# require_comment, templates) — из таблицы settings на стенде (волна A1);
# здесь — injectable-заглушка get_route_settings (дефолты нейтральные,
# реальные значения — только через settings/тестовые оверрайды).
# Статусы README п.1: Черновик → На согласовании → На доработке →
# Согласовано → К исполнению → Завершено / Отклонено / Отозвано.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, get_current_user
from .docs import request_url
from .employees import get_ad_reader
from .mailer import (
    EVENT_ASSIGNED,
    MailQueue,
    enqueue_event,
    get_mail_queue,
    resolve_smtp_from,
    step_owner_mails,
)
from .requests_store import (
    InMemoryRequestsStore,
    RequestsStore,
    RequestsUnavailable,
    get_requests_store,
)
from .settings_routes import (
    DbSettingsStore,
    SettingsUnavailable,
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


class RouteStepTemplate(BaseModel):
    """Шаг шаблона из настроек (без ПДн: только группа/резолвер/флаги)."""

    owner_group: str = Field(description="Группа-владелец шага из settings")
    resolver: str = Field(default="by_group", description="Резолвер исполнителя")
    require_comment: bool = Field(default=False, description="Комментарий обязателен даже при согласии")


class RouteTemplate(BaseModel):
    """Шаблон маршрута: служба + категория → шаги."""

    service: str = Field(description="Служба увольняемого (поле 1С)")
    category: str = Field(description="Категория (МОЛ/линейный/руководитель)")
    steps: list[RouteStepTemplate] = Field(description="Шаги шаблона по порядку")


class RouteSettings(BaseModel):
    """Прикладные настройки маршрута (на стенде — строка таблицы settings)."""

    approval_ttl_days: int = Field(default=3, description="TTL отметок шагов в днях")
    position_to_category: dict[str, str] = Field(
        default_factory=dict, description="Должность 1С → категория"
    )
    position_escalation: dict[str, int] = Field(
        default_factory=dict, description="Должность → часы эскалации (пусто=выкл)"
    )
    templates: list[RouteTemplate] = Field(default_factory=list)


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
        default=None, description="Категория; пусто — вывести из position_to_category"
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


class DecisionIn(BaseModel):
    """Отметка владельца шага (решение + дата + автор + комментарий)."""

    decision: Literal["approve", "reject", "return"]
    comment: str | None = None


class StepsReplaceIn(BaseModel):
    """Правка маршрута по ходу (только разрешенная группа, все — в audit_log)."""

    steps: list[StepSpec]
    reason: str | None = Field(default=None, description="Причина правки (без ПДн)")
    manager: str | None = Field(
        default=None, description="Замена руководителя для шагов ad_direct_manager"
    )


class StepOut(BaseModel):
    """Шаг заявки (исполнители — группы/sam; ФИО согласующего — из AD)."""

    order: int
    owner_group: str
    resolver: str
    assignee: str | None = None
    owner_name: str | None = Field(
        default=None,
        description="ФИО согласующего по данным AD; у группового шага — null",
    )
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


class RequestOut(BaseModel):
    """Заявка (ПДн tab_num — только ОК/админам, см. _public_view)."""

    id: str
    status: str
    route_origin: str
    enterprise: str | None = None
    enterprise_name: str | None = Field(
        default=None, description="Название предприятия из settings.enterprises"
    )
    tab_num: str | None = None
    fio: str | None = None
    department: str
    position: str
    category: str | None = None
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
    escalation_hours: int | None = None
    created_by: str
    steps: list[_Step] = Field(default_factory=list)


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


def _resolve_category(route: RouteSettings, position: str, explicit: str | None) -> str | None:
    """Категория: явная от ОК важнее; подсказка из position_to_category — иначе None."""
    if explicit:
        return explicit
    return route.position_to_category.get(position)


def _find_template(route: RouteSettings, service: str, category: str | None) -> RouteTemplate | None:
    """Подбор шаблона только по службе+категории (поля 1С); нет — None (fallback)."""
    if not category:
        return None
    for template in route.templates:
        if template.service == service and template.category == category:
            return template
    return None


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


def _build_steps(
    specs: list[StepSpec] | list[RouteStepTemplate],
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
    """
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
                steps.append(
                    _Step(
                        order=_order_for(block_index, pos, block.mode == "parallel"),
                        owner_group=spec.sam or spec.owner_group or spec.assignee,
                        resolver=resolver,
                        assignee=assignee,
                        status=STEP_PENDING,
                        require_comment=spec.require_comment,
                        expires_at=now + timedelta(days=ttl_days),
                    )
                )
        return steps
    for index, spec in enumerate(specs):
        # sam/assignee есть только у ручного шага (у RouteStepTemplate из шаблона
        # маршрута таких полей нет) — поэтому getattr.
        personal = getattr(spec, "sam", None)
        spec_assignee = getattr(spec, "assignee", None)
        resolver = spec.resolver if spec.resolver in RESOLVERS else "by_group"
        assignee = spec_assignee
        if personal:
            resolver = "by_user"
            assignee = personal
        elif resolver == "ad_direct_manager" and manager:
            assignee = manager
        steps.append(
            _Step(
                order=index + 1,
                owner_group=personal or spec.owner_group or spec_assignee,
                resolver=resolver,
                assignee=assignee,
                status=STEP_PENDING,
                require_comment=spec.require_comment,
                expires_at=now + timedelta(days=ttl_days),
            )
        )
    return steps


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


def _owns_step(step: _Step, user: CurrentUser) -> bool:
    """Владелец шага по действующим правилам: персональный assignee — только он
    (sam сравнивается ровно как раньше, регистр НЕ нормализуется — ослабление
    сравнения расширило бы доступ), иначе любой из группы-владельца шага."""
    if step.assignee:
        return user.sam == step.assignee
    return step.owner_group in user.groups


def _check_step_owner(step: _Step, user: CurrentUser) -> None:
    """Отметку ставит владелец: персональный assignee — только он, иначе любой из группы.

    Правило вынесено в _owns_step (его же использует can_act), тексты 403 и коды
    ответов прежние."""
    if _owns_step(step, user):
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
) -> bool:
    """Может ли пользователь поставить отметку по шагу прямо сейчас.

    Условия те же, что проверяет decide_step: заявка «На согласовании», шаг
    ожидает, шаг в текущем блоке маршрута (_current_pending_steps), TTL не истек,
    пользователь — владелец шага (_owns_step). Послаблений по роли нет: админ/
    ОК не «могут всё» — иначе фронт покажет кнопку, которую API отклонит
    (403/409/410). Считается для всех ролей, у не-владельца просто False.
    """
    if request.status != IN_APPROVAL or step.status != STEP_PENDING:
        return False
    if step.order not in pending_orders:
        return False
    if step.expires_at <= now:
        return False
    return _owns_step(step, user)


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


def _public_view(
    request: _Request,
    user: CurrentUser,
    *,
    ad_reader: object | None = None,
    enterprise_names: dict[str, str] | None = None,
) -> RequestOut:
    """Ролевая обрезка: ОК/админы — всё, владелец — без tab_num (ПДн).

    ad_reader и enterprise_names резолвятся ОДИН раз на вызов и переиспользуются
    для всех шагов (list_requests поднимает их над циклом по заявкам, чтобы на
    списке не было ни одного лишнего обращения к AD/БД на шаг).

    ПДн по ролям: enterprise_name (как enterprise/tab_num/fio) — только
    привилегированным, остальным None; owner_name (ФИО согласующего) — всем
    авторизованным, сотруднику полезно видеть, кто согласует; assignee, created_by
    и done_by — это sAMAccountName, поэтому скрываются всем, кроме привилегированных
    (assignee владельцу шага остаётся — это его собственный логин); can_act
    считается всегда и для всех ролей.
    """
    privileged = user.role in ("hr", "hr_admin", "admin")
    reader = ad_reader if ad_reader is not None else _resolve_dependency(get_ad_reader)
    # Карта предприятий нужна только привилегированным (остальным enterprise_name
    # не отдается) — при непривилегированном запросе БД настроек не трогаем.
    names = enterprise_names if enterprise_names is not None else (
        _enterprise_names_map() if privileged else {}
    )
    now = _utcnow()
    pending_orders = {s.order for s in _current_pending_steps(request)}
    steps = [
        StepOut(
            order=s.order,
            owner_group=s.owner_group,
            resolver=s.resolver,
            assignee=s.assignee if privileged or _owns_step(s, user) else None,
            owner_name=_owner_display_name(reader, s.assignee),
            status=s.status,
            require_comment=s.require_comment,
            expires_at=s.expires_at.isoformat(),
            done_by=s.done_by if privileged else None,
            done_at=s.done_at.isoformat() if s.done_at else None,
            comment=s.comment,
            can_act=_can_act(request, s, user, pending_orders, now),
        )
        for s in sorted(request.steps, key=lambda x: x.order)
    ]
    return RequestOut(
        id=request.id,
        status=request.status,
        route_origin=request.route_origin,
        enterprise=request.enterprise if privileged else None,
        enterprise_name=names.get(request.enterprise) if privileged else None,
        tab_num=request.tab_num if privileged else None,
        fio=request.fio if privileged else None,
        department=request.department,
        position=request.position,
        category=request.category,
        escalation_hours=request.escalation_hours,
        created_by=request.created_by if privileged else None,
        steps=steps,
    )


def _audit(actor: str, action: str, entity_id: str, detail: str = "") -> None:
    """Запись в append-only журнал (пояснения без ПДн)."""
    audit_log.append(
        AuditEvent(actor=actor, action=action, entity="request", entity_id=entity_id, detail=detail)
    )


def _notify_assigned(
    request: _Request,
    queue: MailQueue,
    settings_store: DbSettingsStore,
    ad_reader: object | None,
    settings: Settings,
) -> None:
    """Письмо «назначена» владельцам первого шага при submit (W3a).

    Получатели — mail из AD (только чтение): у персонального шага один адресат,
    у группового — все активные участники группы (очередь mail_queue хранит по
    одному письму на строку, поэтому рассылка разворачивается здесь). Шаблон — из
    mail_templates/settings. Любой сбой (офлайн без AD/БД, нет шаблона) тихо
    пропускается: уведомление не должно валить подачу заявки.
    """
    try:
        # Всем исполнителям активного блока (в параллельном — все шаги блока).
        recipients = [
            to
            for step in _current_pending_steps(request)
            for to in step_owner_mails(step, ad_reader)
        ]
        if not recipients:
            return
        # Шаблон письма и адрес отправителя не зависят от шага — читаем один раз
        # на заявку (иначе SELECT на каждый шаг активного блока); без адресатов
        # настройки не читаем вовсе.
        templates = read_setting_value(settings_store, "mail_templates")
        smtp_from = read_setting_value(settings_store, "smtp_from")
        for to in recipients:
            enqueue_event(
                queue,
                to,
                request.id,
                EVENT_ASSIGNED,
                templates or [],
                {
                    "fio": request.fio,
                    "request_id": request.id,
                    "url": request_url(settings.APP_BASE_URL, request.id),
                },
                subject_prefix=resolve_smtp_from(smtp_from, settings.SMTP_FROM),
            )
    except Exception:
        return


@router.post("/requests", response_model=RequestOut, status_code=201)
def create_request(
    body: CreateRequestIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    route: RouteSettings = Depends(get_route_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Создание заявки от ОК: шаблон по службе/категории, иначе ручной конструктор."""
    settings.ensure_read_only()
    _require_hr(user)
    now = _utcnow()
    category = _resolve_category(route, body.position, body.category)
    if body.blocks is not None:
        # Явный конструктор ОК (блоками) — приоритетнее шаблона и steps.
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
        template = _find_template(route, body.department, category)
        if template is not None:
            steps = _build_steps(template.steps, route.approval_ttl_days, body.manager, now)
            origin = "template"
        else:
            # Fallback без шаблона — только ручной маршрут от разрешенной группы.
            if not body.steps:
                raise HTTPException(
                    status_code=422,
                    detail="Шаблон не найден: задайте ручной маршрут (steps)",
                )
            steps = _build_steps(body.steps, route.approval_ttl_days, body.manager, now)
            origin = "custom"
    try:
        request = _Request(
            id=store.next_id(),
            status=DRAFT,
            route_origin=origin,
            enterprise=body.enterprise,
            fio=body.fio,
            tab_num=body.tab_num,
            department=body.department,
            position=body.position,
            category=category,
            escalation_hours=route.position_escalation.get(body.position),
            created_by=user.sam,
            steps=steps,
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
    # Резолв AD/предприятий — один раз над циклом (иначе запрос на каждый шаг).
    ad_reader = _resolve_dependency(get_ad_reader)
    enterprise_names = _enterprise_names_map() if _is_hr(user) else {}
    if _is_hr(user):
        return [
            _public_view(r, user, ad_reader=ad_reader, enterprise_names=enterprise_names)
            for r in requests
        ]
    mine = [
        r
        for r in requests
        if any(
            s.owner_group in user.groups or (s.assignee and s.assignee == user.sam)
            for s in r.steps
        )
    ]
    return [
        _public_view(r, user, ad_reader=ad_reader, enterprise_names=enterprise_names)
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
                1
                for r in requests
                if any(
                    s.owner_group in user.groups
                    or (s.assignee and s.assignee == user.sam)
                    for s in r.steps
                )
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
    if not _is_hr(user) and not any(
        s.owner_group in user.groups or (s.assignee and s.assignee == user.sam)
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
    mail_queue: MailQueue = Depends(get_mail_queue),
    settings_store: DbSettingsStore = Depends(get_settings_store),
    ad_reader: object | None = Depends(get_ad_reader),
) -> RequestOut:
    """Черновик/На доработке → На согласовании (только ОК-автор, нужны шаги).

    При подаче ставится письмо «назначена» владельцу первого шага (W3a);
    офлайн/нет AD/шаблона — уведомление тихо пропускается.
    """
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        if request.status not in (DRAFT, REWORK):
            raise HTTPException(status_code=409, detail="Подать можно только из Черновика/На доработке")
        if not request.steps:
            raise HTTPException(status_code=422, detail="Маршрут пуст: добавьте шаги")
        request.status = IN_APPROVAL
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "request.submit", request.id, "")
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
) -> RequestOut:
    """К исполнению → Завершено (только разрешенная группа)."""
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
    return _public_view(request, user)


@router.post("/requests/{request_id}/steps/{order}/decision", response_model=RequestOut)
def decide_step(
    request_id: str,
    order: int,
    body: DecisionIn,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Отметка владельца: согласие/отказ/возврат (комментарий по require_comment)."""
    settings.ensure_read_only()
    try:
        request = _get_request_or_404(store, request_id)
        if request.status != IN_APPROVAL:
            raise HTTPException(status_code=409, detail="Отметки — только в статусе На согласовании")
        step = next((s for s in request.steps if s.order == order), None)
        if step is None:
            raise HTTPException(status_code=404, detail="Шаг не найден")
        if step.status != STEP_PENDING:
            raise HTTPException(status_code=409, detail="Шаг уже закрыт")
        _check_step_owner(step, user)
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
        if order not in {s.order for s in _current_pending_steps(request)}:
            raise HTTPException(
                status_code=409,
                detail="Шаг не в текущем блоке маршрута (строгий порядок/параллельный блок)",
            )
        _check_comment(step, body.decision, body.comment)
        step.done_by = user.sam
        step.done_at = now
        step.comment = (body.comment or "").strip() or None
        if body.decision == "approve":
            step.status = STEP_APPROVED
            _audit(user.sam, "step.approve", request.id, f"order={order}")
            if all(s.status == STEP_APPROVED for s in request.steps):
                request.status = AGREED
        elif body.decision == "reject":
            step.status = STEP_REJECTED
            request.status = REJECTED
            _audit(user.sam, "step.reject", request.id, f"order={order}")
        else:
            step.status = STEP_RETURNED
            request.status = REWORK
            _audit(user.sam, "step.return", request.id, f"order={order}")
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return _public_view(request, user)


@router.post("/requests/{request_id}/steps/{order}/reissue", response_model=RequestOut)
def reissue_step(
    request_id: str,
    order: int,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    route: RouteSettings = Depends(get_route_settings),
    store: RequestsStore = Depends(get_requests_store),
) -> RequestOut:
    """Повтор просроченного шага: новый TTL, снова в работу (только ОК)."""
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
        step.expires_at = _utcnow() + timedelta(days=route.approval_ttl_days)
        request.status = IN_APPROVAL
        store.update(request)
    except RequestsUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    _audit(user.sam, "step.reissue", request.id, f"order={order}")
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
    """Правка шагов (включая замену руководителя) — только разрешенная группа + audit."""
    settings.ensure_read_only()
    _require_hr(user)
    try:
        request = _get_request_or_404(store, request_id)
        if request.status in (DONE, REJECTED, REVOKED):
            raise HTTPException(status_code=409, detail="Закрытая заявка не правится")
        if any(s.order >= BLOCK_ORDER_BASE for s in request.steps):
            # Плоская перенумерация шагов разрушила бы блоки (последовательный/
            # параллельный). Правка блочного маршрута — отдельная задача.
            raise HTTPException(
                status_code=409,
                detail="Правка маршрута с блоками не поддерживается (создайте заново)",
            )
        if not body.steps:
            raise HTTPException(status_code=422, detail="Список шагов не может быть пустым")
        now = _utcnow()
        # Закрытые шаги сохраняем, ожидающие — заменяем новым набором.
        kept = [s for s in request.steps if s.status != STEP_PENDING]
        fresh = _build_steps(body.steps, route.approval_ttl_days, body.manager, now)
        base = len(kept)
        for index, step in enumerate(fresh):
            step.order = base + index + 1
        request.steps = sorted(kept + fresh, key=lambda s: s.order)
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

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
from pydantic import BaseModel, Field

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
    step_owner_mail,
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

    owner_group: str
    resolver: str = "by_group"
    require_comment: bool = False
    assignee: str | None = Field(
        default=None, description="Персональный исполнитель (замена руководителя)"
    )


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
    """Шаг заявки (исполнители — группы/sam без ФИО)."""

    order: int
    owner_group: str
    resolver: str
    assignee: str | None = None
    status: str
    require_comment: bool = False
    expires_at: str
    done_by: str | None = None
    done_at: str | None = None
    comment: str | None = None


class RequestOut(BaseModel):
    """Заявка (ПДн tab_num — только ОК/админам, см. _public_view)."""

    id: str
    status: str
    route_origin: str
    enterprise: str | None = None
    tab_num: str | None = None
    fio: str | None = None
    department: str
    position: str
    category: str | None = None
    escalation_hours: int | None = None
    created_by: str
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


def _build_steps(
    specs: list[StepSpec] | list[RouteStepTemplate],
    ttl_days: int,
    manager: str | None,
    now: datetime,
) -> list[_Step]:
    """Сборка шагов с expires_at = now + TTL (замена руководителя — в assignee)."""
    steps: list[_Step] = []
    for index, spec in enumerate(specs):
        resolver = spec.resolver if spec.resolver in RESOLVERS else "by_group"
        assignee = getattr(spec, "assignee", None)
        if resolver == "ad_direct_manager" and manager:
            assignee = manager
        steps.append(
            _Step(
                order=index + 1,
                owner_group=spec.owner_group,
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


def _current_pending(request: _Request) -> _Step | None:
    """Первый ожидающий шаг по порядку (строгая очередность отметок)."""
    for step in sorted(request.steps, key=lambda s: s.order):
        if step.status == STEP_PENDING:
            return step
    return None


def _check_step_owner(step: _Step, user: CurrentUser) -> None:
    """Отметку ставит владелец: персональный assignee — только он, иначе любой из группы."""
    if step.assignee:
        if user.sam != step.assignee:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Шаг назначен другому исполнителю",
            )
        return
    if step.owner_group not in user.groups:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа: шаг чужой группы",
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


def _public_view(request: _Request, user: CurrentUser) -> RequestOut:
    """Ролевая обрезка: ОК/админы — всё, владелец — без tab_num (ПДн)."""
    privileged = user.role in ("hr", "hr_admin", "admin")
    steps = [
        StepOut(
            order=s.order,
            owner_group=s.owner_group,
            resolver=s.resolver,
            assignee=s.assignee,
            status=s.status,
            require_comment=s.require_comment,
            expires_at=s.expires_at.isoformat(),
            done_by=s.done_by,
            done_at=s.done_at.isoformat() if s.done_at else None,
            comment=s.comment,
        )
        for s in sorted(request.steps, key=lambda x: x.order)
    ]
    return RequestOut(
        id=request.id,
        status=request.status,
        route_origin=request.route_origin,
        enterprise=request.enterprise if privileged else None,
        tab_num=request.tab_num if privileged else None,
        fio=request.fio if privileged else None,
        department=request.department,
        position=request.position,
        category=request.category,
        escalation_hours=request.escalation_hours,
        created_by=request.created_by,
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
    """Письмо «назначена» владельцу первого шага при submit (W3a).

    Получатель — mail из AD (только чтение); шаблон — из mail_templates/settings.
    Любой сбой (офлайн без AD/БД, нет шаблона) тихо пропускается: уведомление
    не должно валить подачу заявки.
    """
    try:
        step = _current_pending(request)
        if step is None:
            return
        templates = read_setting_value(settings_store, "mail_templates")
        smtp_from = read_setting_value(settings_store, "smtp_from")
        to = step_owner_mail(step, ad_reader)
        if not to:
            return
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
    if _is_hr(user):
        return [_public_view(r, user) for r in requests]
    mine = [r for r in requests if any(s.owner_group in user.groups for s in r.steps)]
    return [_public_view(r, user) for r in mine]


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
        ]
    folders.append(
        FolderOut(
            id="mine",
            title="Мои задачи",
            count=sum(
                1
                for r in requests
                if any(s.owner_group in user.groups for s in r.steps)
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
    if not _is_hr(user) and not any(s.owner_group in user.groups for s in request.steps):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к заявке")
    return _public_view(request, user)


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
    return _public_view(request, user)


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
        current = _current_pending(request)
        if current is None or current.order != order:
            raise HTTPException(status_code=409, detail="Шаги закрываются строго по порядку")
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

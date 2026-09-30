# Хранилище заявок (волна B1/B2): интерфейс RequestsStore + in-memory реализация
# для офлайн-тестов/локали и DbRequestsStore (Postgres, таблицы 0001+0002) для
# стенда. Эндпоинты requests.py получают хранилище зависимостью get_requests_store()
# (как get_settings_store в settings_routes.py); падение БД — RequestsUnavailable
# -> роутер отвечает 503 (не 500); офлайн-тесты переопределяют InMemoryRequestsStore.

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .config import Settings, get_settings

if TYPE_CHECKING:
    from .requests import _Request, _Step


class RequestsUnavailable(Exception):
    """Хранилище заявок (БД) недоступно — роутер отвечает 503, а не 500."""


class RequestsStore(Protocol):
    """Интерфейс хранилища заявок: единый для in-memory и Postgres."""

    def create(self, request: _Request) -> None:
        """Сохранить новую заявку (id уже назначен через next_id)."""
        ...

    def get(self, request_id: str) -> _Request | None:
        """Заявка по id либо None."""
        ...

    def list_all(self) -> list[_Request]:
        """Все заявки (для списков и счетчиков папок)."""
        ...

    def update(self, request: _Request) -> None:
        """Записать изменения заявки и её шагов."""
        ...

    def next_id(self) -> str:
        """Новый номер заявки вида REQ-XXXX."""
        ...


# --- Словари маппинга «модель (русский контракт) <-> код БД».
# Русские ключи — контрактные строки requests.py (README п.1), коды —
# ограничения CHECK миграций 0001/0002. В теле методов значений нет: только
# эти словари и чистые функции ниже. Набор русских ключей сверяется тестами
# с константами requests.py, чтобы маппинг не расходился с контрактом.

REQUEST_STATUS_TO_CODE: dict[str, str] = {
    "Черновик": "draft",
    "На согласовании": "in_approval",
    "На доработке": "rework",
    "Согласовано": "approved",
    "К исполнению": "to_execute",
    "Завершено": "done",
    "Отклонено": "rejected",
    "Отозвано": "withdrawn",
}
REQUEST_STATUS_FROM_CODE: dict[str, str] = {
    code: status for status, code in REQUEST_STATUS_TO_CODE.items()
}

STEP_STATUS_TO_CODE: dict[str, str] = {
    "ожидает": "pending",
    "согласован": "approved",
    "отклонен": "rejected",
    "возвращен": "returned",
    "просрочен": "expired",
}
STEP_STATUS_FROM_CODE: dict[str, str] = {
    code: status for status, code in STEP_STATUS_TO_CODE.items()
}

# route_origin: в модели «custom», в CHECK миграции 0001 — «manual». Это один
# и тот же смысл «ручной маршрут», поэтому синоним 'custom' в CHECK не заводим:
# он дублирует 'manual' без выигрыша и усложняет откат миграции. Маппинг
# сосредоточен здесь (единственная точка), round-trip стабилен.
ROUTE_ORIGIN_TO_CODE: dict[str, str] = {"template": "template", "custom": "manual"}
ROUTE_ORIGIN_FROM_CODE: dict[str, str] = {"template": "template", "manual": "custom"}


def request_status_to_db(status: str) -> str:
    """Русский статус заявки -> код БД (неизвестный — ValueError, не молчать)."""
    try:
        return REQUEST_STATUS_TO_CODE[status]
    except KeyError:
        raise ValueError(f"Неизвестный статус заявки: {status!r}") from None


def request_status_from_db(code: str) -> str:
    """Код БД -> русский статус заявки (неизвестный — ValueError)."""
    try:
        return REQUEST_STATUS_FROM_CODE[code]
    except KeyError:
        raise ValueError(f"Неизвестный код статуса заявки: {code!r}") from None


def step_status_to_db(status: str) -> str:
    """Русский статус шага -> код БД (неизвестный — ValueError)."""
    try:
        return STEP_STATUS_TO_CODE[status]
    except KeyError:
        raise ValueError(f"Неизвестный статус шага: {status!r}") from None


def step_status_from_db(code: str) -> str:
    """Код БД -> русский статус шага (неизвестный — ValueError)."""
    try:
        return STEP_STATUS_FROM_CODE[code]
    except KeyError:
        raise ValueError(f"Неизвестный код статуса шага: {code!r}") from None


def route_origin_to_db(origin: str) -> str:
    """route_origin модели -> код БД (custom -> manual, см. выше)."""
    try:
        return ROUTE_ORIGIN_TO_CODE[origin]
    except KeyError:
        raise ValueError(f"Неизвестный route_origin: {origin!r}") from None


def route_origin_from_db(code: str) -> str:
    """Код БД -> route_origin модели (manual -> custom)."""
    try:
        return ROUTE_ORIGIN_FROM_CODE[code]
    except KeyError:
        raise ValueError(f"Неизвестный код route_origin: {code!r}") from None


# Бизнес-номер заявки REQ-XXXX: в модели — id, в БД — колонка code.
_REQ_ID_PREFIX = "REQ-"


def req_number_to_id(number: int) -> str:
    """Число -> 'REQ-XXXX' (формат контракта, next_id по MAX числовой части)."""
    return f"{_REQ_ID_PREFIX}{number:04d}"


def req_id_to_number(request_id: str) -> int:
    """'REQ-XXXX' -> число (некорректный номер — ValueError)."""
    if not request_id.startswith(_REQ_ID_PREFIX):
        raise ValueError(f"Некорректный номер заявки: {request_id!r}")
    digits = request_id[len(_REQ_ID_PREFIX) :]
    if not digits.isdigit():
        raise ValueError(f"Некорректный номер заявки: {request_id!r}")
    return int(digits)


class InMemoryRequestsStore:
    """Офлайн-хранилище заявок (словарь процесса), интерфейс RequestsStore."""

    def __init__(self) -> None:
        self._requests: dict[str, _Request] = {}
        self._seq = 0

    def reset(self) -> None:
        """Сброс состояния. Только для изоляции pytest/локального запуска."""
        self._requests.clear()
        self._seq = 0

    def create(self, request: _Request) -> None:
        self._requests[request.id] = request

    def get(self, request_id: str) -> _Request | None:
        return self._requests.get(request_id)

    def list_all(self) -> list[_Request]:
        return list(self._requests.values())

    def update(self, request: _Request) -> None:
        self._requests[request.id] = request

    def next_id(self) -> str:
        """Номер по внутреннему счетчику (как в прежней offline-заглушке)."""
        self._seq += 1
        return req_number_to_id(self._seq)


class DbRequestsStore:
    """Хранилище заявок в Postgres (таблицы 0001, дополнены миграцией 0002).

    Схема 0002 добавила под модель заявки: код REQ-XXXX (code), fio,
    department/position/category/escalation_hours; у шагов — resolver/assignee/
    require_comment; base_code nullable без FK (в модели _Request его нет).
    Статусы и route_origin в БД — кодами ('draft'/'manual'), в модели — русскими
    строками контракта ('Черновик'/'custom'); маппинг — словарями выше.
    Ошибки БД оборачиваются в RequestsUnavailable (503), как DbSettingsStore
    в settings_routes.py. Полный round-trip проверяется на стенде (qa-sed).
    """

    _REQUEST_COLUMNS = (
        "id, code, enterprise, tab_num, initiated_by_hr, route_origin, status, "
        "fio, department, position, category, escalation_hours"
    )

    _SELECT_REQUESTS = text(
        f"""
        SELECT {_REQUEST_COLUMNS}
        FROM dismissal_requests
        ORDER BY id
        """
    )
    _SELECT_REQUEST_BY_CODE = text(
        f"""
        SELECT {_REQUEST_COLUMNS}
        FROM dismissal_requests
        WHERE code = :code
        """
    )
    _SELECT_ID_BY_CODE = text(
        "SELECT id FROM dismissal_requests WHERE code = :code"
    )
    _INSERT_REQUEST = text(
        """
        INSERT INTO dismissal_requests (
          code, enterprise, base_code, tab_num, initiated_by_hr, route_origin,
          status, fio, department, position, category, escalation_hours,
          created_at, updated_at
        )
        VALUES (
          :code, :enterprise, :base_code, :tab_num, :created_by, :route_origin,
          :status, :fio, :department, :position, :category, :escalation_hours,
          :created_at, :updated_at
        )
        RETURNING id
        """
    )
    _UPDATE_REQUEST = text(
        """
        UPDATE dismissal_requests
        SET status = :status,
            route_origin = :route_origin,
            fio = :fio,
            department = :department,
            position = :position,
            category = :category,
            escalation_hours = :escalation_hours,
            updated_at = :updated_at
        WHERE code = :code
        """
    )
    _NEXT_CODE_SQL = text(
        """
        SELECT COALESCE(MAX(SUBSTRING(code, 5)::integer), 0)
        FROM dismissal_requests
        WHERE code ~ '^REQ-[0-9]+$'
        """
    )

    _STEP_COLUMNS = (
        "request_id, step_order, owner_group, done_by, status, resolver, "
        "assignee, require_comment, done_at, expires_at, comment"
    )
    _SELECT_STEPS_BY_REQUEST = text(
        f"""
        SELECT {_STEP_COLUMNS}
        FROM request_steps
        WHERE request_id = :request_id
        ORDER BY step_order
        """
    )
    _SELECT_ALL_STEPS = text(
        f"""
        SELECT {_STEP_COLUMNS}
        FROM request_steps
        ORDER BY request_id, step_order
        """
    )
    _INSERT_STEP = text(
        """
        INSERT INTO request_steps (
          request_id, step_order, owner_group, done_by, status, resolver,
          assignee, require_comment, done_at, expires_at, comment
        )
        VALUES (
          :request_id, :step_order, :owner_group, :done_by, :status, :resolver,
          :assignee, :require_comment, :done_at, :expires_at, :comment
        )
        """
    )
    _DELETE_STEPS = text(
        "DELETE FROM request_steps WHERE request_id = :request_id"
    )

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def _step_params(self, request_id: int, step: _Step) -> dict:
        """Параметры вставки шага (статус/resolver — кодами БД)."""
        return {
            "request_id": request_id,
            "step_order": step.order,
            "owner_group": step.owner_group,
            "done_by": step.done_by,
            "status": step_status_to_db(step.status),
            "resolver": step.resolver,
            "assignee": step.assignee,
            "require_comment": step.require_comment,
            "done_at": step.done_at,
            "expires_at": step.expires_at,
            "comment": step.comment,
        }

    def _build_request(self, row, step_rows: list) -> _Request:
        """Строка БД (request + шаги) -> модель _Request (русский контракт).
        Импорт модели — ленивый, чтобы не зациклить requests.py <-> requests_store."""
        from .requests import _Request as _RequestModel
        from .requests import _Step as _StepModel

        return _RequestModel(
            id=row.code,
            status=request_status_from_db(row.status),
            route_origin=route_origin_from_db(row.route_origin),
            enterprise=row.enterprise,
            fio=row.fio or "",
            tab_num=row.tab_num,
            department=row.department,
            position=row.position,
            category=row.category,
            escalation_hours=row.escalation_hours,
            created_by=row.initiated_by_hr,
            steps=[
                _StepModel(
                    order=step_row.step_order,
                    owner_group=step_row.owner_group,
                    resolver=step_row.resolver,
                    assignee=step_row.assignee,
                    status=step_status_from_db(step_row.status),
                    require_comment=step_row.require_comment,
                    expires_at=step_row.expires_at,
                    done_by=step_row.done_by,
                    done_at=step_row.done_at,
                    comment=step_row.comment,
                )
                for step_row in step_rows
            ],
        )

    def create(self, request: _Request) -> None:
        """Новая заявка: dismissal_requests + все шаги одной транзакцией."""
        from .requests import _Request as _RequestModel

        try:
            with self._session_factory() as session:
                now = datetime.now(timezone.utc)
                row = session.execute(
                    self._INSERT_REQUEST,
                    {
                        "code": request.id,
                        "enterprise": request.enterprise,
                        "base_code": None,
                        "tab_num": request.tab_num,
                        "created_by": request.created_by,
                        "route_origin": route_origin_to_db(request.route_origin),
                        "status": request_status_to_db(request.status),
                        "fio": request.fio,
                        "department": request.department,
                        "position": request.position,
                        "category": request.category,
                        "escalation_hours": request.escalation_hours,
                        "created_at": now,
                        "updated_at": now,
                    },
                ).first()
                for step in request.steps:
                    session.execute(
                        self._INSERT_STEP, self._step_params(row.id, step)
                    )
                session.commit()
        except SQLAlchemyError as exc:
            raise RequestsUnavailable(f"Хранилище заявок недоступно: {exc}") from exc

    def get(self, request_id: str) -> _Request | None:
        """Заявка по бизнес-номеру (code) либо None; шаги по порядку."""
        try:
            with self._session_factory() as session:
                row = session.execute(
                    self._SELECT_REQUEST_BY_CODE, {"code": request_id}
                ).first()
                if row is None:
                    return None
                step_rows = session.execute(
                    self._SELECT_STEPS_BY_REQUEST, {"request_id": row.id}
                ).all()
        except SQLAlchemyError as exc:
            raise RequestsUnavailable(f"Хранилище заявок недоступно: {exc}") from exc
        return self._build_request(row, step_rows)

    def list_all(self) -> list[_Request]:
        """Все заявки + шаги (без N+1: два запроса, связка в Python)."""
        try:
            with self._session_factory() as session:
                rows = session.execute(self._SELECT_REQUESTS).all()
                step_rows = session.execute(self._SELECT_ALL_STEPS).all()
        except SQLAlchemyError as exc:
            raise RequestsUnavailable(f"Хранилище заявок недоступно: {exc}") from exc
        steps_by_request: dict[int, list] = {}
        for step_row in step_rows:
            steps_by_request.setdefault(step_row.request_id, []).append(step_row)
        return [
            self._build_request(row, steps_by_request.get(row.id, [])) for row in rows
        ]

    def update(self, request: _Request) -> None:
        """Записать заявку и заменить её шаги (шаги — цельный список модели;
        повторная вставка переживает и отметки владельца, и правку маршрута)."""
        from .requests import _Request as _RequestModel

        try:
            with self._session_factory() as session:
                internal = session.execute(
                    self._SELECT_ID_BY_CODE, {"code": request.id}
                ).first()
                if internal is None:
                    raise RequestsUnavailable(
                        f"Заявка {request.id} не найдена в хранилище"
                    )
                now = datetime.now(timezone.utc)
                session.execute(
                    self._UPDATE_REQUEST,
                    {
                        "code": request.id,
                        "status": request_status_to_db(request.status),
                        "route_origin": route_origin_to_db(request.route_origin),
                        "fio": request.fio,
                        "department": request.department,
                        "position": request.position,
                        "category": request.category,
                        "escalation_hours": request.escalation_hours,
                        "updated_at": now,
                    },
                )
                session.execute(self._DELETE_STEPS, {"request_id": internal.id})
                for step in request.steps:
                    session.execute(
                        self._INSERT_STEP, self._step_params(internal.id, step)
                    )
                session.commit()
        except SQLAlchemyError as exc:
            raise RequestsUnavailable(f"Хранилище заявок недоступно: {exc}") from exc

    def next_id(self) -> str:
        """Номер REQ-XXXX по MAX(числовая часть code) + 1 (контракт B1)."""
        try:
            with self._session_factory() as session:
                max_number = session.execute(self._NEXT_CODE_SQL).scalar() or 0
        except SQLAlchemyError as exc:
            raise RequestsUnavailable(f"Хранилище заявок недоступно: {exc}") from exc
        return req_number_to_id(max_number + 1)


_db_requests_store: DbRequestsStore | None = None


def get_requests_store(settings: Settings = Depends(get_settings)) -> RequestsStore:
    """Боевое хранилище заявок (Postgres): один движок на процесс.

    В офлайн-тестах переопределяется InMemoryRequestsStore через
    dependency_overrides (как get_settings_store в settings_routes.py).
    """
    global _db_requests_store
    if _db_requests_store is None:
        _db_requests_store = DbRequestsStore(settings.DATABASE_URL)
    return _db_requests_store
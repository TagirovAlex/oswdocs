"""Клиент чтения карточек сотрудников из баз 1С ЗУП (мультибаза, только GET).

Железные правила (см. скил onec-multibase):
- Только чтение: клиент выполняет исключительно HTTP GET. Методов записи
  (POST/PUT/PATCH/DELETE) здесь нет и быть не должно.
- Настройки баз — только из env ONEC_BASES_JSON, хардкод URL/предприятий запрещён.
- Таймаут каждого запроса — 5 секунд по умолчанию.
- Падение одной базы не блокирует остальные: circuit-breaker на каждую базу
  (fail-fast при открытой цепи), веерный опрос — через resolver.py.
- Живой OData-контракт (точные пути сущностей 1С) проверяется на стенде;
  построение URL вынесено в один метод _build_employee_url/_build_search_url,
  чтобы заменить без правки логики.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol


# Таймаут одного GET-запроса к базе 1С, секунды (приёмка Фазы 3).
DEFAULT_TIMEOUT = 5.0

# Параметры circuit-breaker по умолчанию.
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_RECOVERY_TIMEOUT = 30.0

# Имя переменной окружения с настройками баз.
ONEC_BASES_ENV = "ONEC_BASES_JSON"


class OneCError(Exception):
    """Базовая ошибка клиента 1С."""


class OneCConfigError(OneCError):
    """Некорректные настройки баз (env)."""


class OneCUnknownBase(OneCError):
    """Запрошен неизвестный код базы."""


class OneCConnectionError(OneCError):
    """Сеть/таймаут/недоступность базы."""


class OneCTimeoutError(OneCConnectionError):
    """Таймаут запроса к базе."""


class OneCBaseDown(OneCError):
    """База ответила серверной ошибкой (5xx) или сеть недоступна."""


class OneCCircuitOpen(OneCError):
    """Цепь базы разомкнута: запросы fail-fast без обращения к сети."""

    def __init__(self, base_code: str):
        self.base_code = base_code
        super().__init__("цепь базы %r разомкнута, запрос пропущен" % base_code)


class OneCNotFound(OneCError):
    """Карточка не найдена в базе (404). На счётчик circuit-breaker не влияет."""


@dataclass(frozen=True)
class OneCBaseConfig:
    """Настройки одной базы 1С. Заполняется только из env, не из кода."""

    code: str  # код базы, напр. "zup_msk"
    enterprise: str  # предприятие-владелец, напр. "Предприятие-МСК"
    url: str  # базовый URL публикации 1С web-сервера
    user: str  # сервисная УЗ чтения (имя)
    secret: str  # сервисная УЗ чтения (пароль, только из env, не логировать)


@dataclass(frozen=True)
class EmployeeCard:
    """Единая карточка сотрудника из 1С (состав ключа: enterprise+base+tab)."""

    enterprise: str
    base_code: str
    tab_num: str
    fio: str
    dept: str = ""
    position: str = ""
    employment_type: str = ""
    hire_date: str = ""
    # Остаток отпуска и дата приёма — только роли ОК, исполнителям не отдавать
    # (обрезка — в B1, здесь поле просто присутствует как nullable).
    vacation_balance: Optional[str] = None
    mol_flag: Optional[bool] = None  # TODO: флаг МОЛ из 1С не подтверждён, nullable

    def key(self) -> str:
        """Составной ключ карточки."""
        return make_key(self.enterprise, self.base_code, self.tab_num)


def make_key(enterprise: str, base_code: str, tab_num: str) -> str:
    """Составной ключ enterprise+base_code+tab_num (таб. номера пересекаются)."""
    return "%s|%s|%s" % (enterprise, base_code, tab_num)


@dataclass
class HttpResult:
    """Ответ HTTP-транспорта: статус и тело-строка."""

    status: int
    body: str


class HttpTransport(Protocol):
    """Интерфейс HTTP-транспорта (граница для mock-HTTP в тестах и на стенде)."""

    def get(self, url: str, headers: Dict[str, str], timeout: float) -> HttpResult:
        """Только GET. Реализации не должны делать записи."""
        ...  # pragma: no cover


class UrllibTransport:
    """Реализация транспорта на стандартной библиотеке (без новых зависимостей)."""

    def get(self, url: str, headers: Dict[str, str], timeout: float) -> HttpResult:
        # Только чтение: используется GET-запрос.
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return HttpResult(status=resp.status, body=raw)
        except TimeoutError as exc:
            raise OneCTimeoutError("таймаут запроса: %s" % url) from exc
        except OSError as exc:
            raise OneCConnectionError("ошибка соединения: %s" % exc) from exc


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: Optional[float] = None

    @property
    def is_open(self) -> bool:
        return self.opened_at is not None


def load_bases_from_env(env_var: str = ONEC_BASES_ENV) -> Dict[str, OneCBaseConfig]:
    """Разобрать ONEC_BASES_JSON формата {enterprise: {base_code: {url,user,secret}}}.

    Возвращает словарь base_code -> OneCBaseConfig. Пустой env — пустой словарь
    (решение о поведении без баз — на вызывающей стороне).
    """
    raw = os.environ.get(env_var, "")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OneCConfigError("некорректный JSON в %s" % env_var) from exc
    if not isinstance(data, dict):
        raise OneCConfigError("%s: ожидается объект {enterprise: {base: {...}}}" % env_var)
    bases: Dict[str, OneCBaseConfig] = {}
    for enterprise, by_base in data.items():
        if not isinstance(by_base, dict):
            raise OneCConfigError("%s: предприятие %r: ожидается объект баз" % (env_var, enterprise))
        for base_code, item in by_base.items():
            if not isinstance(item, dict):
                raise OneCConfigError("%s: база %r: ожидается объект {url,user,secret}" % (env_var, base_code))
            url = item.get("url", "")
            user = item.get("user", "")
            secret = item.get("secret", "")
            if not url or not user or not secret:
                raise OneCConfigError(
                    "%s: база %r: нужны непустые url/user/secret" % (env_var, base_code)
                )
            if base_code in bases:
                raise OneCConfigError("%s: код базы %r дублируется" % (env_var, base_code))
            bases[base_code] = OneCBaseConfig(
                code=base_code, enterprise=enterprise, url=url, user=user, secret=secret
            )
    return bases


def build_enterprise_map(bases: Dict[str, OneCBaseConfig]) -> Dict[str, List[str]]:
    """Индекс предприятие -> список кодов баз (для веерного опроса)."""
    index: Dict[str, List[str]] = {}
    for code, cfg in bases.items():
        index.setdefault(cfg.enterprise, []).append(code)
    return index


class OneCClient:
    """Per-base клиент чтения 1С: только GET, таймаут 5с, circuit-breaker на базу."""

    def __init__(
        self,
        bases: Dict[str, OneCBaseConfig],
        transport: Optional[HttpTransport] = None,
        timeout: float = DEFAULT_TIMEOUT,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_timeout: float = DEFAULT_RECOVERY_TIMEOUT,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bases = dict(bases)
        self._transport: HttpTransport = transport if transport is not None else UrllibTransport()
        self._timeout = timeout
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._time = time_func
        self._circuits: Dict[str, _CircuitState] = {code: _CircuitState() for code in self._bases}

    # -- публичный интерфейс (только чтение) --

    @property
    def base_codes(self) -> List[str]:
        """Коды известных баз."""
        return sorted(self._bases)

    def bases_for_enterprise(self, enterprise: str) -> List[str]:
        """Коды баз, привязанных к предприятию (публичный accessor для resolver)."""
        return sorted(code for code, cfg in self._bases.items() if cfg.enterprise == enterprise)

    def circuit_is_open(self, base_code: str) -> bool:
        """Открыта ли цепь базы (для диагностики и тестов)."""
        self._require_base(base_code)
        return self._circuits[base_code].is_open

    def get_employee(self, base_code: str, tab_num: str) -> EmployeeCard:
        """Прочитать карточку сотрудника по таб. номеру (GET одной базы)."""
        cfg = self._require_base(base_code)
        self._ensure_allowed(base_code)
        url = self._build_employee_url(cfg.url, tab_num)
        try:
            result = self._transport.get(url, self._auth_headers(cfg), self._timeout)
        except OneCTimeoutError:
            self._on_failure(base_code)
            raise
        except OneCConnectionError as exc:
            self._on_failure(base_code)
            raise OneCBaseDown("база %r недоступна: %s" % (base_code, exc)) from exc
        except OneCCircuitOpen:
            raise
        except Exception as exc:  # сеть транспорта-мока: считать падением базы
            self._on_failure(base_code)
            raise OneCBaseDown("база %r недоступна: %s" % (base_code, exc)) from exc
        if result.status == 404:
            # Не найдено — не ошибка базы, счётчик не трогаем.
            raise OneCNotFound("таб. %r не найден в базе %r" % (tab_num, base_code))
        if result.status >= 500:
            self._on_failure(base_code)
            raise OneCBaseDown("база %r ответила %s" % (base_code, result.status))
        if result.status != 200:
            raise OneCError("база %r ответила %s" % (base_code, result.status))
        self._on_success(base_code)
        return self._parse_card(cfg, result.body, tab_num)

    def search(self, base_code: str, query: str) -> List[EmployeeCard]:
        """Поиск сотрудников базы по подстроке ФИО (GET одной базы)."""
        cfg = self._require_base(base_code)
        self._ensure_allowed(base_code)
        url = self._build_search_url(cfg.url, query)
        try:
            result = self._transport.get(url, self._auth_headers(cfg), self._timeout)
        except OneCTimeoutError:
            self._on_failure(base_code)
            raise
        except OneCConnectionError as exc:
            self._on_failure(base_code)
            raise OneCBaseDown("база %r недоступна: %s" % (base_code, exc)) from exc
        except OneCCircuitOpen:
            raise
        except Exception as exc:
            self._on_failure(base_code)
            raise OneCBaseDown("база %r недоступна: %s" % (base_code, exc)) from exc
        if result.status >= 500:
            self._on_failure(base_code)
            raise OneCBaseDown("база %r ответила %s" % (base_code, result.status))
        if result.status != 200:
            raise OneCError("база %r ответила %s" % (base_code, result.status))
        self._on_success(base_code)
        return self._parse_cards(cfg, result.body)

    # -- внутренние методы --

    def _require_base(self, base_code: str) -> OneCBaseConfig:
        try:
            return self._bases[base_code]
        except KeyError:
            raise OneCUnknownBase("неизвестная база %r" % base_code) from None

    def _ensure_allowed(self, base_code: str) -> None:
        state = self._circuits[base_code]
        if state.is_open:
            assert state.opened_at is not None
            if self._time() - state.opened_at >= self._recovery_timeout:
                # Полуоткрытое состояние: разрешаем один пробный запрос.
                state.opened_at = None
                state.failures = 0
                return
            raise OneCCircuitOpen(base_code)

    def _on_success(self, base_code: str) -> None:
        state = self._circuits[base_code]
        state.failures = 0
        state.opened_at = None

    def _on_failure(self, base_code: str) -> None:
        state = self._circuits[base_code]
        state.failures += 1
        if state.failures >= self._failure_threshold:
            state.opened_at = self._time()

    @staticmethod
    def _auth_headers(cfg: OneCBaseConfig) -> Dict[str, str]:
        # Базовая авторизация сервисной УЗ чтения; секрет — только в заголовке.
        token = base64.b64encode(("%s:%s" % (cfg.user, cfg.secret)).encode("utf-8")).decode("ascii")
        return {"Authorization": "Basic " + token, "Accept": "application/json"}

    @staticmethod
    def _build_employee_url(base_url: str, tab_num: str) -> str:
        # Точный путь OData-сущности — пометка «на стенде»; единый контракт для всех баз.
        return base_url.rstrip("/") + "/Employees?tab_num=" + urllib.parse.quote(tab_num, safe="")

    @staticmethod
    def _build_search_url(base_url: str, query: str) -> str:
        # Точный путь OData-сущности — пометка «на стенде»; единый контракт для всех баз.
        return base_url.rstrip("/") + "/Employees?q=" + urllib.parse.quote(query, safe="")

    @staticmethod
    def _parse_card(cfg: OneCBaseConfig, body: str, tab_num: str) -> EmployeeCard:
        # Ожидаемый JSON: {"fio": ..., "dept": ..., "position": ...,
        #   "employment_type": ..., "hire_date": ..., "vacation_balance": ..., "mol_flag": ...}
        # Точные имена полей OData — пометка «на стенде».
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OneCError("база %r вернула не-JSON" % cfg.code) from exc
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            items = data["value"]  # обёртка OData
            if not items:
                raise OneCNotFound("таб. %r не найден в базе %r" % (tab_num, cfg.code))
            data = items[0]
        if not isinstance(data, dict) or not data.get("fio"):
            raise OneCError("база %r вернула карточку без ФИО" % cfg.code)
        return EmployeeCard(
            enterprise=cfg.enterprise,
            base_code=cfg.code,
            tab_num=str(data.get("tab_num", tab_num)),
            fio=str(data["fio"]),
            dept=str(data.get("dept", "")),
            position=str(data.get("position", "")),
            employment_type=str(data.get("employment_type", "")),
            hire_date=str(data.get("hire_date", "")),
            vacation_balance=data.get("vacation_balance"),
            mol_flag=data.get("mol_flag"),
        )

    @staticmethod
    def _parse_cards(cfg: OneCBaseConfig, body: str) -> List[EmployeeCard]:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OneCError("база %r вернула не-JSON" % cfg.code) from exc
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            items = data["value"]
        elif isinstance(data, list):
            items = data
        else:
            raise OneCError("база %r вернула неожиданный формат поиска" % cfg.code)
        cards: List[EmployeeCard] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("fio"):
                continue
            cards.append(
                EmployeeCard(
                    enterprise=cfg.enterprise,
                    base_code=cfg.code,
                    tab_num=str(item.get("tab_num", "")),
                    fio=str(item["fio"]),
                    dept=str(item.get("dept", "")),
                    position=str(item.get("position", "")),
                    employment_type=str(item.get("employment_type", "")),
                    hire_date=str(item.get("hire_date", "")),
                    vacation_balance=item.get("vacation_balance"),
                    mol_flag=item.get("mol_flag"),
                )
            )
        return cards

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
    """Настройки одной базы 1С (OData). Заполняется только из настроек/env.

    Маппинг «предприятие → базы» НЕ из этого конфига (в базе может быть несколько
    предприятий) — его строит синхронизация (onec_sync) и хранит в settings
    (onec_enterprise_bases); enterprise здесь оставлен для обратной совместимости.
    Имена сущностей/полей OData — дефолты под ЗУП 3.х, правит ИТ по факту из базы.
    """

    code: str  # код базы, напр. "zup_msk"
    enterprise: str = ""  # устарело: маппинг предприятий — из синхронизации
    url: str = ""  # OData-URL публикации базы (до /odata/standard.odata/)
    user: str = ""  # сервисная УЗ чтения (имя)
    secret: str = ""  # сервисная УЗ чтения (пароль, только из env/настроек)
    # Справочник сотрудников: в ЗУП это Catalog_Сотрудники (таб.№ = Code,
    # ФИО = Description), а не Catalog_СотрудникиОрганизаций (не публикуется).
    employee_entity: str = "Catalog_Сотрудники"
    organization_entity: str = "Catalog_Организации"
    # Фильтр предприятия в справочнике сотрудников: код предприятия = Ref_Key
    # организации (у «Организаций» кода нет), таб. номера по базам пересекаются.
    employee_org_field: str = "ГоловнаяОрганизация_Key"
    tab_num_field: str = "Code"
    fio_field: str = "Description"
    # Подразделение/должность/дата приёма — из регистра текущих кадровых данных
    # (второй запрос карточки; поля с '/' — через $expand, напр. «ТекущееПодразделение/Description»).
    department_field: str = "ТекущееПодразделение/Description"
    position_field: str = "ТекущаяДолжность/Description"
    hire_date_field: str = "ДатаПриема"
    termination_date_field: str = "ДатаУвольнения"
    # Контактные поля сотрудника (в OData базы пока не опубликованы — пустые
    # дефолты; ИТ заполняет в карточке базы, когда опубликует). Схема — настраиваемая.
    phone_field: str = ""
    email_field: str = ""
    hr_entity: str = "InformationRegister_ТекущиеКадровыеДанныеСотрудников"
    hr_employee_field: str = "Сотрудник_Key"  # поле сотрудника (Ref_Key) в регистре
    organization_code_field: str = "Ref_Key"
    organization_name_field: str = "Description"


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
    # Дата увольнения (из регистра кадровых данных) — только роли ОК,
    # исполнителям не отдавать (обрезка — в B1, здесь поле просто присутствует
    # как nullable-строка). Пустое значение регистра (0001-01-01...) — "".
    dismissal_date: str = ""
    # Контакты 1С (настраиваемые поля схемы; в OData пока не опубликованы — "").
    phone: str = ""
    email: str = ""
    # Ref_Key записи справочника сотрудников: нужен для второго запроса карточки
    # (регистр текущих кадровых данных); наружу как ПДн не отдаётся.
    ref_key: str = ""

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


def normalize_odata_base_url(url: str) -> str:
    """OData-база публикации: добавляет /odata/standard.odata если не указан.

    Админ в карточке базы может ввести URL веб-клиента
    (напр. http://host/zup/ru/) — сервис OData живёт по тому же пути
    + /odata/standard.odata (с локалью тоже отвечает)."""
    url = (url or "").rstrip("/")
    if url and "/odata/standard.odata" not in url:
        url = url + "/odata/standard.odata"
    return url


def build_entity_url(base_url: str, entity: str) -> str:
    """OData-URL коллекции сущности: {base_url}/{entity}?$format=json.

    Сущность кодируется (кириллица в имени, напр. Catalog_СотрудникиОрганизаций)
    — urllib требует ascii-URL, httpx сам не кодирует."""
    return normalize_odata_base_url(base_url) + "/" + urllib.parse.quote(entity) + "?$format=json"


def parse_collection(body: str) -> List[Dict[str, object]]:
    """Список записей из OData-ответа: обёртка {"value": [...]} либо голый список."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OneCError("некорректный JSON в ответе OData") from exc
    if isinstance(data, dict) and isinstance(data.get("value"), list):
        return [item for item in data["value"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise OneCError("неожиданный формат OData-ответа (ожидался список)")


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
        enterprise_index: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self._bases = dict(bases)
        self._transport: HttpTransport = transport if transport is not None else UrllibTransport()
        self._timeout = timeout
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._time = time_func
        self._circuits: Dict[str, _CircuitState] = {code: _CircuitState() for code in self._bases}
        # Маппинг предприятие→базы: из синхронизации (settings.onec_enterprise_bases)
        # либо производный от конфигов (совместимость).
        self._enterprise_index = enterprise_index if enterprise_index is not None else build_enterprise_map(bases)

    # -- публичный интерфейс (только чтение) --

    @property
    def base_codes(self) -> List[str]:
        """Коды известных баз."""
        return sorted(self._bases)

    def bases_for_enterprise(self, enterprise: str) -> List[str]:
        """Коды баз, привязанных к предприятию (маппинг из синхронизации)."""
        return sorted(self._enterprise_index.get(enterprise, []))

    def base_config(self, base_code: str) -> OneCBaseConfig:
        """Конфиг базы (только чтение): код/предприятие/URL для зеркала one_c_bases.

        Неизвестный код — OneCUnknownBase (как _require_base)."""
        return self._require_base(base_code)

    def circuit_is_open(self, base_code: str) -> bool:
        """Открыта ли цепь базы (для диагностики и тестов)."""
        self._require_base(base_code)
        return self._circuits[base_code].is_open

    def get_employee(
        self, base_code: str, tab_num: str, enterprise: Optional[str] = None
    ) -> EmployeeCard:
        """Прочитать карточку сотрудника по таб. номеру (GET одной базы).

        enterprise — фильтр предприятия (код = Ref_Key организации); карточка
        дополняется текущими кадровыми данными (подразделение/должность/дата
        приёма) вторым запросом к регистру кадровых данных."""
        cfg = self._require_base(base_code)
        self._ensure_allowed(base_code)
        url = self._build_employee_url(cfg, tab_num, enterprise)
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
        card = self._parse_card(cfg, result.body, tab_num, enterprise)
        return self._enrich_hr(cfg, card)

    def search(
        self, base_code: str, query: str, enterprise: Optional[str] = None
    ) -> List[EmployeeCard]:
        """Поиск сотрудников базы по подстроке ФИО (GET одной базы).

        enterprise — фильтр предприятия (код = Ref_Key организации); результаты
        справочника лёгкие (без кадровых данных — они в карточке get_employee)."""
        cfg = self._require_base(base_code)
        self._ensure_allowed(base_code)
        url = self._build_search_url(cfg, query, enterprise)
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
        return self._parse_cards(cfg, result.body, enterprise)

    def list_employees(
        self,
        base_code: str,
        enterprise: Optional[str] = None,
        skip: int = 0,
        top: int = 500,
    ) -> List[EmployeeCard]:
        """Выгрузка сотрудников базы по предприятию страницами ($skip/$top, GET).

        Для полной сверки 1С↔AD (автосвязка): обход всех записей предприятия
        без лимита $top=50 поиска. Результаты лёгкие (без кадровых данных —
        они в карточке get_employee). Только чтение."""
        cfg = self._require_base(base_code)
        self._ensure_allowed(base_code)
        url = self._build_list_url(cfg, enterprise, skip, top)
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
        return self._parse_cards(cfg, result.body, enterprise)

    def _enrich_hr(self, cfg: OneCBaseConfig, card: EmployeeCard) -> EmployeeCard:
        """Дополнить карточку текущими кадровыми данными (регистр, $expand).

        Регистр не настроен или записи кадровых данных нет — карточка остаётся
        как есть (не ошибка); падение базы на этом запросе — ошибка (счётчик)."""
        if not cfg.hr_entity or not cfg.hr_employee_field or not card.ref_key:
            return card
        self._ensure_allowed(cfg.code)
        url = self._build_hr_url(cfg, card.ref_key)
        try:
            result = self._transport.get(url, self._auth_headers(cfg), self._timeout)
        except OneCTimeoutError:
            self._on_failure(cfg.code)
            raise
        except OneCConnectionError as exc:
            self._on_failure(cfg.code)
            raise OneCBaseDown("база %r недоступна: %s" % (cfg.code, exc)) from exc
        except OneCCircuitOpen:
            raise
        except Exception as exc:
            self._on_failure(cfg.code)
            raise OneCBaseDown("база %r недоступна: %s" % (cfg.code, exc)) from exc
        if result.status == 404:
            return card  # кадровых данных нет (напр. уволен) — карточка как есть
        if result.status >= 500:
            self._on_failure(cfg.code)
            raise OneCBaseDown("база %r ответила %s" % (cfg.code, result.status))
        if result.status != 200:
            raise OneCError("база %r ответила %s" % (cfg.code, result.status))
        self._on_success(cfg.code)
        return self._parse_hr(cfg, result.body, card)

    @classmethod
    def _parse_hr(cls, cfg: OneCBaseConfig, body: str, card: EmployeeCard) -> EmployeeCard:
        """Наложить запись регистра кадровых данных на карточку (поля схемы)."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OneCError("база %r вернула не-JSON" % cfg.code) from exc
        items = data.get("value") if isinstance(data, dict) else data
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            return card  # запись не найдена — карточка без кадровых данных
        item = items[0]
        return EmployeeCard(
            enterprise=card.enterprise,
            base_code=card.base_code,
            tab_num=card.tab_num,
            fio=card.fio,
            dept=cls._field(item, cfg.department_field) or card.dept,
            position=cls._field(item, cfg.position_field) or card.position,
            employment_type=card.employment_type,
            hire_date=cls._field(item, cfg.hire_date_field) or card.hire_date,
            dismissal_date=cls._normalize_date(cls._field(item, cfg.termination_date_field)),
            phone=card.phone,
            email=card.email,
            ref_key=card.ref_key,
        )

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
    def _expand_part(fio_field: str) -> str:
        """Часть до '/' в поле ФИО для $expand (напр. 'Сотрудник/Description' → 'Сотрудник')."""
        part = fio_field.split("/", 1)[0].strip()
        return part if part and "/" in fio_field else ""

    @staticmethod
    def _unique_fields(fields: List[str]) -> List[str]:
        """Поля без пустых и дублей (порядок сохраняется)."""
        seen: List[str] = []
        for f in fields:
            if f and f not in seen:
                seen.append(f)
        return seen

    @staticmethod
    def _employee_select_fields(cfg: OneCBaseConfig) -> str:
        """Поля $select справочника сотрудников: ключ + таб.№ + ФИО + фильтр предприятия.

        Ref_Key — ключ записи для второго запроса карточки (кадровые данные)."""
        return ",".join(
            OneCClient._unique_fields(
                ["Ref_Key", cfg.tab_num_field, cfg.fio_field, cfg.employee_org_field]
            )
        )

    @classmethod
    def _org_filter(cls, cfg: OneCBaseConfig, enterprise: Optional[str]) -> str:
        """Условие фильтра по предприятию (код = Ref_Key организации), если задано."""
        if enterprise and cfg.employee_org_field:
            return " and %s eq guid'%s'" % (cfg.employee_org_field, enterprise)
        return ""

    @classmethod
    def _build_employee_url(
        cls, cfg: OneCBaseConfig, tab_num: str, enterprise: Optional[str] = None
    ) -> str:
        """OData-URL карточки: сущность + $filter по таб.№ (+предприятие) + $select."""
        select = urllib.parse.quote(cls._employee_select_fields(cfg), safe=",;/")
        flt = urllib.parse.quote(
            "%s eq '%s'%s" % (cfg.tab_num_field, tab_num, cls._org_filter(cfg, enterprise)),
            safe="",
        )
        url = "%s/%s?$format=json&$top=1&$select=%s&$filter=%s" % (
            normalize_odata_base_url(cfg.url),
            urllib.parse.quote(cfg.employee_entity),  # кириллица в имени сущности
            select,
            flt,
        )
        return url

    @classmethod
    def _build_search_url(
        cls, cfg: OneCBaseConfig, query: str, enterprise: Optional[str] = None
    ) -> str:
        """OData-URL поиска: подстрока ФИО (substringof) по полю схемы (+предприятие)."""
        select = urllib.parse.quote(cls._employee_select_fields(cfg), safe=",;/")
        flt = urllib.parse.quote(
            "substringof('%s', %s) eq true%s"
            % (query, cfg.fio_field, cls._org_filter(cfg, enterprise)),
            safe="",
        )
        url = "%s/%s?$format=json&$top=50&$select=%s&$filter=%s" % (
            normalize_odata_base_url(cfg.url),
            urllib.parse.quote(cfg.employee_entity),  # кириллица в имени сущности
            select,
            flt,
        )
        return url

    @classmethod
    def _build_list_url(
        cls,
        cfg: OneCBaseConfig,
        enterprise: Optional[str] = None,
        skip: int = 0,
        top: int = 500,
    ) -> str:
        """OData-URL выгрузки: страница $top/$skip по предприятию (без подстроки)."""
        select = urllib.parse.quote(cls._employee_select_fields(cfg), safe=",;/")
        url = "%s/%s?$format=json&$top=%d&$skip=%d&$select=%s" % (
            normalize_odata_base_url(cfg.url),
            urllib.parse.quote(cfg.employee_entity),  # кириллица в имени сущности
            int(top),
            int(skip),
            select,
        )
        if enterprise and cfg.employee_org_field:
            url += "&$filter=" + urllib.parse.quote(
                "%s eq guid'%s'" % (cfg.employee_org_field, enterprise), safe=""
            )
        return url

    @classmethod
    def _build_hr_url(cls, cfg: OneCBaseConfig, ref_key: str) -> str:
        """OData-URL регистра кадровых данных: по сотруднику (Ref_Key) + $expand.

        $expand берётся из полей подразделения/должности (часть до '/' в имени),
        чтобы получить их Description без отдельного запроса к справочникам."""
        flt = urllib.parse.quote(
            "%s eq guid'%s'" % (cfg.hr_employee_field, ref_key), safe=""
        )
        url = "%s/%s?$format=json&$top=1&$filter=%s" % (
            normalize_odata_base_url(cfg.url),
            urllib.parse.quote(cfg.hr_entity),  # кириллица в имени сущности
            flt,
        )
        expand = ",".join(
            part
            for part in (
                cls._expand_part(cfg.department_field),
                cls._expand_part(cfg.position_field),
            )
            if part
        )
        if expand:
            url += "&$expand=" + urllib.parse.quote(expand)
        return url

    @staticmethod
    def _field(item: Dict[str, object], name: str) -> str:
        """Значение поля OData: поддерживает вложенные 'Сотрудник/Description'."""
        value: object = item
        for part in name.split("/"):
            if not isinstance(value, dict):
                return ""
            value = value.get(part)
        return str(value) if value is not None else ""

    @staticmethod
    def _normalize_date(raw: str) -> str:
        """Дата из 1С: пустое значение регистра = 0001-01-01T00:00:00 — вернуть ''."""
        value = (raw or "").strip()
        if not value or value.startswith("0001-01-01"):
            return ""
        return value

    @staticmethod
    def _parse_card(
        cfg: OneCBaseConfig, body: str, tab_num: str, enterprise: Optional[str] = None
    ) -> EmployeeCard:
        # OData-ответ — обёртка {"value": [...]}; поля — по схеме базы.
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OneCError("база %r вернула не-JSON" % cfg.code) from exc
        if isinstance(data, dict) and isinstance(data.get("value"), list):
            items = data["value"]  # обёртка OData
            if not items:
                raise OneCNotFound("таб. %r не найден в базе %r" % (tab_num, cfg.code))
            data = items[0]
        if not isinstance(data, dict):
            raise OneCError("база %r вернула неожиданный формат карточки" % cfg.code)
        fio = OneCClient._field(data, cfg.fio_field)
        if not fio:
            raise OneCError("база %r вернула карточку без ФИО" % cfg.code)
        # Подразделение/должность/дата приёма — из регистра кадровых данных
        # (см. _enrich_hr); здесь — пустые, чтобы справочник оставался лёгким.
        return EmployeeCard(
            enterprise=enterprise or cfg.enterprise,
            base_code=cfg.code,
            tab_num=OneCClient._field(data, cfg.tab_num_field) or tab_num,
            fio=fio,
            dept="",
            position="",
            employment_type="",
            hire_date="",
            phone=OneCClient._field(data, cfg.phone_field) if cfg.phone_field else "",
            email=OneCClient._field(data, cfg.email_field) if cfg.email_field else "",
            ref_key=str(data.get("Ref_Key") or ""),
        )

    @staticmethod
    def _parse_cards(
        cfg: OneCBaseConfig, body: str, enterprise: Optional[str] = None
    ) -> List[EmployeeCard]:
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
            if not isinstance(item, dict):
                continue
            fio = OneCClient._field(item, cfg.fio_field)
            if not fio:
                continue
            cards.append(
                EmployeeCard(
                    enterprise=enterprise or cfg.enterprise,
                    base_code=cfg.code,
                    tab_num=OneCClient._field(item, cfg.tab_num_field),
                    fio=fio,
                    dept="",
                    position="",
                    employment_type="",
                    hire_date="",
                    phone=OneCClient._field(item, cfg.phone_field) if cfg.phone_field else "",
                    email=OneCClient._field(item, cfg.email_field) if cfg.email_field else "",
                    ref_key=str(item.get("Ref_Key") or ""),
                )
            )
        return cards

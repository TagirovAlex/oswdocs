# Кэш карточек 1С в Redis (Фаза 3, backend-1c). Только чтение 1С (GET).
#
# Железные правила (см. контракт TASKS_PHASE3.md и скил onec-multibase):
# - Кэшируется ТОЛЬКО успешный get_employee; search не кэшируется, ошибки
#   (сеть/таймаут/circuit/404) не кэшируются — повторный вызов идёт в сеть.
# - Кэш страхуется: любое исключение Redis — промах (get -> None) или no-op
#   (set). Кэш никогда не должен валить чтение карточки из 1С.
# - Ключ записи — `sed:onec:card:{base_code}|{enterprise}|{tab_num}` (enterprise
#   отсутствует у вызовов без него); составной ключ enterprise|base|tab хранится
#   в значении и возвращается как card.key().
# - Настройки TTL — только из env (ONEC_CACHE_TTL), хардкод запрещён.

from __future__ import annotations

import dataclasses
import json
from typing import List, Optional, Protocol

import redis

from .onec_client import EmployeeCard, OneCClient

# Префикс ключей кэша карточек 1С (единый namespace в Redis).
CARD_CACHE_PREFIX = "sed:onec:card:"


class CardCache(Protocol):
    """Граница кэша карточек 1С. Обе операции страхуются: исключение — промах/no-op."""

    def get(self, key: str) -> Optional[EmployeeCard]:
        """Карточка по ключу либо None (нет записи/ошибка кэша)."""
        ...  # pragma: no cover

    def set(self, key: str, card: EmployeeCard, ttl_seconds: int) -> None:
        """Сохранить карточку с TTL. Ошибка кэша — no-op (кэш не валит чтение 1С)."""
        ...  # pragma: no cover


class RedisCardCache:
    """Карточки 1С в Redis: ключ `sed:onec:card:{key}`, значение — JSON EmployeeCard.

    Соединение — redis.from_url(REDIS_URL из env) либо инъекция redis_client
    (для тестов). Падение Redis не роняет чтение 1С: get -> None, set -> no-op.
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        redis_client=None,
        prefix: str = CARD_CACHE_PREFIX,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._prefix = prefix
        # Инъекция клиента — для тестов (без сети); по умолчанию — боевое соединение.
        self._redis = (
            redis_client
            if redis_client is not None
            else redis.from_url(redis_url, decode_responses=True)
        )

    def _key(self, key: str) -> str:
        return self._prefix + key

    def get(self, key: str) -> Optional[EmployeeCard]:
        """Прочитать карточку: JSON -> EmployeeCard; ошибки/нет записи -> None."""
        try:
            raw = self._redis.get(self._key(key))
        except Exception:
            # Redis недоступен/ошибка — считаем промахом, 1С читается напрямую.
            return None
        if not raw:
            return None
        try:
            return EmployeeCard(**json.loads(raw))
        except Exception:
            # Битый JSON — промах, а не падение (данные из 1С остаются истиной).
            return None

    def set(
        self, key: str, card: EmployeeCard, ttl_seconds: Optional[int] = None
    ) -> None:
        """Сохранить карточку (asdict -> json); TTL — аргумент либо self._ttl_seconds."""
        try:
            payload = json.dumps(dataclasses.asdict(card), ensure_ascii=False)
            self._redis.set(
                self._key(key),
                payload,
                ex=ttl_seconds if ttl_seconds is not None else self._ttl_seconds,
            )
        except Exception:
            # Redis недоступен — no-op: запись в кэш не критична для чтения 1С.
            pass


class CachingOneCClient:
    """Обёртка над OneCClient с кэшем успешных get_employee (интерфейс как у клиента).

    Кэшируется только успешный get_employee (ключ `base_code|enterprise|tab_num`);
    search и ошибки (сеть/таймаут/circuit/404) не кэшируются — повторный
    вызов снова идёт в сеть. Методов записи в 1С здесь нет (только чтение).
    """

    def __init__(
        self,
        client: OneCClient,
        cache: CardCache,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._ttl_seconds = ttl_seconds

    @property
    def base_codes(self) -> List[str]:
        """Коды известных баз (проброс к клиенту)."""
        return self._client.base_codes

    def bases_for_enterprise(self, enterprise: str) -> List[str]:
        """Коды баз предприятия (проброс к клиенту)."""
        return self._client.bases_for_enterprise(enterprise)

    def base_config(self, base_code: str):
        """Конфиг базы (проброс к клиенту, только чтение)."""
        return self._client.base_config(base_code)

    def circuit_is_open(self, base_code: str) -> bool:
        """Открыта ли цепь базы (проброс к клиенту)."""
        return self._client.circuit_is_open(base_code)

    def get_employee(
        self, base_code: str, tab_num: str, enterprise: Optional[str] = None
    ) -> EmployeeCard:
        """Карточка сотрудника: попадание в кэш — без сети, промах — 1С + запись.

        Ключ кэша включает предприятие (таб. номера в базе пересекаются между
        предприятиями, а фильтр по предприятию — часть запроса к 1С). В кэш
        пишется только успешный ответ; ошибки (сеть/таймаут/circuit/404)
        пробрасываются как есть и не кэшируются.
        """
        key = (
            "%s|%s|%s" % (base_code, enterprise, tab_num)
            if enterprise
            else "%s|%s" % (base_code, tab_num)
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        card = self._client.get_employee(base_code, tab_num, enterprise)
        self._cache.set(key, card, ttl_seconds=self._ttl_seconds)
        return card

    def search(
        self, base_code: str, query: str, enterprise: Optional[str] = None
    ) -> List[EmployeeCard]:
        """Поиск сотрудников базы: не кэшируется (живые данные)."""
        return self._client.search(base_code, query, enterprise)

    def list_employees(
        self,
        base_code: str,
        enterprise: Optional[str] = None,
        skip: int = 0,
        top: int = 500,
    ) -> List[EmployeeCard]:
        """Выгрузка сотрудников базы страницами (для автосвязки; не кэшируется)."""
        return self._client.list_employees(base_code, enterprise, skip=skip, top=top)
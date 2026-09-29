"""Тесты кэша Redis карточек 1С (Фаза 3, backend-1c).

Покрывают по контракту TASKS_PHASE3.md:
- CachingOneCClient: попадание/промах в кэш, не-кэширование 404 и ошибок
  сети, не-кэширование search, проброс служебных методов в обёрнутый
  клиент, состав кэш-ключа base|tab.
- RedisCardCache: round-trip сериализации EmployeeCard (фейковый redis,
  без сети), передача TTL в set, страховка при исключениях redis, префикс.
- get_onec_client из employees.py: пустой env -> 503, валидный env ->
  сборка CachingOneCClient без обращения к сети.

Все ФИО/предприятия вымышленные, ПДн не реальные. Общий conftest не
используется — фикстуры локально здесь (как в test_onec_resolver.py).
Модуль app.onec_cache создаётся параллельным агентом: тесты написаны по
контракту и выполняются после его появления.
"""
import json
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.employees as employees  # noqa: E402
from app.config import Settings  # noqa: E402
from app.onec_cache import CachingOneCClient, RedisCardCache  # noqa: E402
from app.onec_client import (  # noqa: E402
    EmployeeCard,
    HttpResult,
    OneCBaseConfig,
    OneCBaseDown,
    OneCClient,
    OneCConnectionError,
    OneCNotFound,
)

ENT = "Предприятие-Север-Тест"  # вымышленное
FIO_A = "Сказочников Тест Тестович"  # вымышленное
FIO_B = "Выдуманова Проверка Примеровна"  # вымышленное


def _bases():
    # Mock-адреса только для FakeTransport (боевые — из env ONEC_BASES_JSON).
    return {
        "zup_a": OneCBaseConfig(
            code="zup_a", enterprise=ENT, url="https://1c-mock.local/a", user="r", secret="s1"
        ),
        "zup_b": OneCBaseConfig(
            code="zup_b", enterprise=ENT, url="https://1c-mock.local/b", user="r", secret="s2"
        ),
    }


def _card_json(tab, fio):
    return json.dumps({"tab_num": tab, "fio": fio}, ensure_ascii=False)


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append(url)
        return self.handler(url, headers, timeout)


class FakeCache:
    """Фейковый CardCache: хранилище карточек + история вызовов."""

    def __init__(self):
        self.store = {}
        self.sets = []
        self.gets = []

    def get(self, key):
        self.gets.append(key)
        return self.store.get(key)

    def set(self, key, card, ttl_seconds=None):
        self.sets.append((key, card, ttl_seconds))
        self.store[key] = card


class FakeRedisClient:
    """Фейковый redis-клиент на dict-хранилище, без сети."""

    def __init__(self):
        self.store = {}
        self.last_ex = None

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.last_ex = ex
        self.store[key] = value


class ExplodingRedis:
    """Redis, падающий на любой операции (проверка страховки кэша)."""

    def get(self, key):
        raise RuntimeError("redis недоступен")

    def set(self, key, value, ex=None):
        raise RuntimeError("redis недоступен")


# --- CachingOneCClient ---

def test_caching_get_employee_first_network_second_hit():
    transport = FakeTransport(lambda u, h, t: HttpResult(200, _card_json("100", FIO_A)))
    client = OneCClient(_bases(), transport=transport)
    cache = FakeCache()
    wrapped = CachingOneCClient(client, cache, ttl_seconds=60)

    card1 = wrapped.get_employee("zup_a", "100")
    assert len(transport.calls) == 1  # промах: сеть вызвана один раз
    card2 = wrapped.get_employee("zup_a", "100")
    assert len(transport.calls) == 1  # попадание: сеть НЕ вызвана
    assert card1 == card2
    # Запись в кэш — ровно одна, ключ base|tab, TTL из конструктора.
    assert len(cache.sets) == 1
    assert cache.sets[0][0] == "zup_a|100"
    assert cache.sets[0][2] == 60
    assert cache.store["zup_a|100"] == card1


def test_caching_get_employee_not_found_not_cached():
    transport = FakeTransport(lambda u, h, t: HttpResult(404, "{}"))
    client = OneCClient(_bases(), transport=transport)
    cache = FakeCache()
    wrapped = CachingOneCClient(client, cache, ttl_seconds=60)

    with pytest.raises(OneCNotFound):
        wrapped.get_employee("zup_a", "100")
    with pytest.raises(OneCNotFound):
        wrapped.get_employee("zup_a", "100")
    assert len(transport.calls) == 2  # 404 не кэшируется: сеть дважды
    assert cache.store == {}


def test_caching_get_employee_network_error_not_cached_and_raised():
    def router(url, headers, timeout):
        raise OneCConnectionError("сеть базы недоступна")

    transport = FakeTransport(router)
    client = OneCClient(_bases(), transport=transport, failure_threshold=10)
    cache = FakeCache()
    wrapped = CachingOneCClient(client, cache, ttl_seconds=60)

    # OneCClient оборачивает ошибку соединения в OneCBaseDown — она обязана
    # проброситься наружу (кэш ошибки не пишет).
    with pytest.raises(OneCBaseDown):
        wrapped.get_employee("zup_a", "100")
    with pytest.raises(OneCBaseDown):
        wrapped.get_employee("zup_a", "100")
    assert len(transport.calls) == 2  # ошибка сети не кэшируется
    assert cache.store == {}


def test_caching_search_not_cached():
    body = json.dumps([{"tab_num": "100", "fio": FIO_B}], ensure_ascii=False)
    transport = FakeTransport(lambda u, h, t: HttpResult(200, body))
    client = OneCClient(_bases(), transport=transport)
    cache = FakeCache()
    wrapped = CachingOneCClient(client, cache, ttl_seconds=60)

    first = wrapped.search("zup_a", "Выдуманова")
    second = wrapped.search("zup_a", "Выдуманова")
    assert len(transport.calls) == 2  # поиск не кэшируется
    assert [c.fio for c in first] == [FIO_B]
    assert [c.fio for c in second] == [FIO_B]
    assert cache.store == {}


def test_caching_forwards_service_methods():
    transport = FakeTransport(lambda u, h, t: HttpResult(404, "{}"))
    client = OneCClient(_bases(), transport=transport)
    wrapped = CachingOneCClient(client, FakeCache(), ttl_seconds=60)

    assert wrapped.base_codes == ["zup_a", "zup_b"]
    assert wrapped.bases_for_enterprise(ENT) == ["zup_a", "zup_b"]
    assert wrapped.bases_for_enterprise("Другое-Предприятие") == []
    assert wrapped.circuit_is_open("zup_a") is False


# --- RedisCardCache (фейковый redis, без сети) ---

def _full_card():
    return EmployeeCard(
        enterprise=ENT,
        base_code="zup_a",
        tab_num="100",
        fio=FIO_A,
        dept="Отдел разработки",
        position="Разработчик",
        employment_type="Основное место работы",
        hire_date="2019-03-01",
        vacation_balance=None,
        mol_flag=None,
    )


def test_redis_cache_roundtrip():
    redis = FakeRedisClient()
    cache = RedisCardCache(
        redis_url="redis://mock.local:6379/0", ttl_seconds=120, redis_client=redis
    )
    card = _full_card()

    cache.set("zup_a|100", card)
    got = cache.get("zup_a|100")

    assert got == card
    assert got.fio == FIO_A
    assert got.dept == card.dept
    assert got.position == card.position
    assert got.employment_type == card.employment_type
    assert got.hire_date == card.hire_date
    assert got.vacation_balance is None
    assert got.mol_flag is None
    # Ключ в redis — с префиксом; TTL по умолчанию передан в set.
    assert "sed:onec:card:zup_a|100" in redis.store
    assert redis.last_ex == 120


def test_redis_cache_roundtrip_non_null_nullables():
    redis = FakeRedisClient()
    cache = RedisCardCache(
        redis_url="redis://mock.local:6379/0", ttl_seconds=120, redis_client=redis
    )
    card = EmployeeCard(
        enterprise=ENT,
        base_code="zup_a",
        tab_num="100",
        fio=FIO_A,
        vacation_balance="7",
        mol_flag=True,
    )
    cache.set("zup_a|100", card)
    got = cache.get("zup_a|100")
    assert got == card
    assert got.vacation_balance == "7"
    assert got.mol_flag is True


def test_redis_cache_set_ttl_override():
    redis = FakeRedisClient()
    cache = RedisCardCache(
        redis_url="redis://mock.local:6379/0", ttl_seconds=120, redis_client=redis
    )
    cache.set("zup_a|100", _full_card(), ttl_seconds=45)
    assert redis.last_ex == 45  # явный TTL побеждает дефолт конструктора


def test_redis_cache_miss_returns_none():
    redis = FakeRedisClient()
    cache = RedisCardCache(
        redis_url="redis://mock.local:6379/0", ttl_seconds=120, redis_client=redis
    )
    assert cache.get("absent|key") is None


def test_redis_cache_bad_json_returns_none():
    redis = FakeRedisClient()
    redis.store["sed:onec:card:zup_a|100"] = "{broken json"
    cache = RedisCardCache(
        redis_url="redis://mock.local:6379/0", ttl_seconds=120, redis_client=redis
    )
    assert cache.get("zup_a|100") is None


def test_redis_cache_redis_exception_is_safe():
    cache = RedisCardCache(
        redis_url="redis://mock.local:6379/0",
        ttl_seconds=120,
        redis_client=ExplodingRedis(),
    )
    card = _full_card()
    # get — промах (None), set — no-op: падение redis не валит чтение 1С.
    assert cache.get("zup_a|100") is None
    cache.set("zup_a|100", card)  # не должно бросить


# --- get_onec_client (сборка боевого клиента) ---

def test_get_onec_client_empty_env_503(monkeypatch):
    monkeypatch.delenv("ONEC_BASES_JSON", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        employees.get_onec_client(Settings())
    assert exc_info.value.status_code == 503


def test_get_onec_client_blank_env_503(monkeypatch):
    monkeypatch.setenv("ONEC_BASES_JSON", "")
    with pytest.raises(HTTPException) as exc_info:
        employees.get_onec_client(Settings())
    assert exc_info.value.status_code == 503


def test_get_onec_client_builds_caching_client(monkeypatch):
    payload = json.dumps(
        {
            "Предприятие-Юг-Тест": {
                "zup_c": {"url": "https://1c-mock.local/c", "user": "r", "secret": "s3"}
            }
        },
        ensure_ascii=False,
    )
    monkeypatch.setenv("ONEC_BASES_JSON", payload)
    client = employees.get_onec_client(Settings())
    assert isinstance(client, CachingOneCClient)
    assert client.base_codes == ["zup_c"]
"""Тесты OneCClient на mock-HTTP (изоляция, таймаут, circuit-breaker, только GET).

Все ФИО вымышлены и используются только как фикстуры. URL вида
https://1c-mock.local/... — mock-адреса только для FakeTransport, боевые
настройки — исключительно из env ONEC_BASES_JSON (см. тест env-парсинга).
Общий conftest.py не используется (чужой файл A2); фикстуры — локально здесь.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.onec_client import (  # noqa: E402
    DEFAULT_TIMEOUT,
    EmployeeCard,
    HttpResult,
    OneCBaseConfig,
    OneCBaseDown,
    OneCCircuitOpen,
    OneCClient,
    OneCConfigError,
    OneCNotFound,
    OneCTimeoutError,
    load_bases_from_env,
    make_key,
)

# Вымышленные данные фикстур (не ПДн реальных лиц).
FIOS = {
    "a100": "Сказочников Тест Тестович",
    "b100": "Выдуманова Проверка Примеровна",
    "b200": "Несуществов Демо Демонович",
}

ENT = "Предприятие-Север-Тест"  # вымышленное предприятие для тестов


def _bases():
    # Mock-адреса только для FakeTransport (боевые — из env, см. тест ниже).
    return {
        "zup_a": OneCBaseConfig(
            code="zup_a", enterprise=ENT, url="https://1c-mock.local/a", user="reader", secret="s1"
        ),
        "zup_b": OneCBaseConfig(
            code="zup_b", enterprise=ENT, url="https://1c-mock.local/b", user="reader", secret="s2"
        ),
    }


def _card_json(tab, fio, **kw):
    payload = {
        "tab_num": tab,
        "fio": fio,
        "dept": kw.get("dept", "Цех тестовый"),
        "position": kw.get("position", "Тестировщик"),
        "employment_type": kw.get("employment_type", "Основная"),
        "hire_date": kw.get("hire_date", "2020-01-15"),
        "vacation_balance": kw.get("vacation_balance", "14"),
        "mol_flag": None,
    }
    return json.dumps(payload, ensure_ascii=False)


class FakeTransport:
    """Mock-HTTP: только GET; поведение задаётся колбэком, вызовы записываются."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []  # (url, timeout)

    def get(self, url, headers, timeout):
        self.calls.append((url, timeout))
        assert "Authorization" in headers  # сервисная УЗ прикладывается
        return self.handler(url, headers, timeout)


def _ok_a(url, headers, timeout):
    if "/a/Employees" in url and "tab_num=" in url:
        return HttpResult(200, _card_json("100", FIOS["a100"]))
    if "/a/Employees" in url:
        return HttpResult(200, json.dumps([{"tab_num": "100", "fio": FIOS["a100"]}]))
    return HttpResult(404, "{}")


def test_get_employee_success():
    t = FakeTransport(_ok_a)
    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=t)
    card = c.get_employee("zup_a", "100")
    assert card.fio == FIOS["a100"]
    assert card.key() == make_key(ENT, "zup_a", "100")


def test_timeout_is_5_seconds():
    t = FakeTransport(_ok_a)
    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=t)
    assert c._timeout == DEFAULT_TIMEOUT == 5.0
    c.get_employee("zup_a", "100")
    assert t.calls and t.calls[0][1] == 5.0  # таймаут реально передан транспорту


def test_client_is_read_only_no_write_methods():
    c = OneCClient(_bases(), transport=FakeTransport(_ok_a))
    for name in ("post", "put", "patch", "delete", "create", "update", "write"):
        assert not hasattr(c, name), "у клиента не должно быть метода записи %s" % name
    c.get_employee("zup_a", "100")
    # Транспорт-мок умеет только get — записей на уровне интерфейса нет.


def test_404_does_not_trip_circuit():
    def h404(url, headers, timeout):
        return HttpResult(404, "{}")

    c = OneCClient(
        {"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h404), failure_threshold=2
    )
    with pytest.raises(OneCNotFound):
        c.get_employee("zup_a", "999")
    with pytest.raises(OneCNotFound):
        c.get_employee("zup_a", "999")
    assert c.circuit_is_open("zup_a") is False  # отсутствие карточки — не падение базы


def test_5xx_opens_circuit_and_fail_fast():
    calls = {"n": 0}

    def h500(url, headers, timeout):
        calls["n"] += 1
        return HttpResult(500, "err")

    c = OneCClient(
        {"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h500), failure_threshold=2
    )
    with pytest.raises(OneCBaseDown):
        c.get_employee("zup_a", "100")
    with pytest.raises(OneCBaseDown):
        c.get_employee("zup_a", "100")
    assert c.circuit_is_open("zup_a") is True
    with pytest.raises(OneCCircuitOpen):
        c.get_employee("zup_a", "100")
    assert calls["n"] == 2  # третий запрос не пошёл в сеть (fail-fast)


def test_circuit_recovers_after_timeout():
    now = {"t": 1000.0}

    def h500(url, headers, timeout):
        return HttpResult(500, "err")

    c = OneCClient(
        {"zup_a": _bases()["zup_a"]},
        transport=FakeTransport(h500),
        failure_threshold=1,
        recovery_timeout=30.0,
        time_func=lambda: now["t"],
    )
    with pytest.raises(OneCBaseDown):
        c.get_employee("zup_a", "100")
    assert c.circuit_is_open("zup_a") is True
    now["t"] += 31.0  # окно восстановления прошло
    with pytest.raises(OneCBaseDown):  # пробный запрос снова идёт в сеть
        c.get_employee("zup_a", "100")


def test_timeout_counts_as_failure_and_opens_circuit():
    class TimeoutTransport:
        def get(self, url, headers, timeout):
            raise OneCTimeoutError("таймаут")

    c = OneCClient(
        {"zup_a": _bases()["zup_a"]},
        transport=TimeoutTransport(),
        failure_threshold=2,
    )
    with pytest.raises(OneCTimeoutError):
        c.get_employee("zup_a", "100")
    with pytest.raises(OneCTimeoutError):
        c.get_employee("zup_a", "100")
    assert c.circuit_is_open("zup_a") is True


def test_one_base_failure_does_not_affect_other_base():
    # Изоляция на уровне клиента: цепь базы A разомкнута, база B отвечает.
    def router(url, headers, timeout):
        if "/a/Employees" in url:
            return HttpResult(500, "down")
        return HttpResult(200, _card_json("100", FIOS["b100"]))

    c = OneCClient(_bases(), transport=FakeTransport(router), failure_threshold=1)
    with pytest.raises(OneCBaseDown):
        c.get_employee("zup_a", "100")
    assert c.circuit_is_open("zup_a") is True
    assert c.circuit_is_open("zup_b") is False
    card = c.get_employee("zup_b", "100")
    assert card.fio == FIOS["b100"]


def test_composite_key_unique_across_bases():
    assert make_key(ENT, "zup_a", "100") != make_key(ENT, "zup_b", "100")
    assert make_key(ENT, "zup_a", "100") != make_key("Другое-Предприятие", "zup_a", "100")


def test_search_returns_cards():
    def h(url, headers, timeout):
        return HttpResult(200, json.dumps([{"tab_num": "200", "fio": FIOS["b200"]}]))

    c = OneCClient({"zup_b": _bases()["zup_b"]}, transport=FakeTransport(h))
    cards = c.search("zup_b", "Несуществов")
    assert len(cards) == 1 and cards[0].fio == FIOS["b200"]


def test_load_bases_from_env_parses_documented_format(monkeypatch):
    # Формат из .env.example: {enterprise: {base: {url, user, secret}}}.
    monkeypatch.setenv(
        "ONEC_BASES_JSON",
        json.dumps(
            {ENT: {"zup_a": {"url": "https://1c-mock.local/a", "user": "r", "secret": "s"}}}
        ),
    )
    bases = load_bases_from_env()
    assert bases["zup_a"].enterprise == ENT
    assert bases["zup_a"].url.startswith("https://")


def test_load_bases_from_env_rejects_bad_json(monkeypatch):
    monkeypatch.setenv("ONEC_BASES_JSON", "{не json")
    with pytest.raises(OneCConfigError):
        load_bases_from_env()
    monkeypatch.setenv("ONEC_BASES_JSON", json.dumps({ENT: {"zup_a": {"url": "", "user": "", "secret": ""}}}))
    with pytest.raises(OneCConfigError):
        load_bases_from_env()


def test_employee_card_is_plain_data():
    card = EmployeeCard(enterprise=ENT, base_code="zup_a", tab_num="1", fio=FIOS["a100"])
    assert card.mol_flag is None  # TODO флаг МОЛ: nullable до выяснения на стенде

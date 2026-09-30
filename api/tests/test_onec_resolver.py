"""Тесты resolver.py: предприятие→база→сотрудник, изоляция падения базы.

Все ФИО вымышлены. Общий conftest.py не используется (чужой файл A2);
фикстуры — локально здесь. Mock-HTTP — через FakeTransport из клиента.
"""
import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.onec_client import (  # noqa: E402
    HttpResult,
    OneCBaseConfig,
    OneCClient,
    OneCConnectionError,
)
from app.resolver import (  # noqa: E402
    EmployeeNotFoundError,
    UnknownEnterpriseError,
    enterprise_bases,
    is_snapshot_stale,
    make_snapshot_1c,
    resolve_employee,
    search_enterprise,
)

ENT = "Предприятие-Север-Тест"  # вымышленное
FIO_A = "Сказочников Тест Тестович"  # вымышленное
FIO_B = "Выдуманова Проверка Примеровна"  # вымышленное


def _bases():
    return {
        "zup_a": OneCBaseConfig(
            code="zup_a", enterprise=ENT, url="https://1c-mock.local/a", user="r", secret="s1"
        ),
        "zup_b": OneCBaseConfig(
            code="zup_b", enterprise=ENT, url="https://1c-mock.local/b", user="r", secret="s2"
        ),
    }


def _card_json(tab, fio):
    # OData-ответ справочника Catalog_Сотрудники (таб.№ = Code, ФИО = Description).
    return json.dumps(
        {"value": [{"Ref_Key": "ref-" + tab, "Code": tab, "Description": fio}]},
        ensure_ascii=False,
    )


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append(url)
        return self.handler(url, headers, timeout)


def test_resolve_finds_in_second_base_when_first_has_no_card():
    def router(url, headers, timeout):
        if "/a/" in url:
            return HttpResult(404, "{}")
        return HttpResult(200, _card_json("100", FIO_B))

    client = OneCClient(_bases(), transport=FakeTransport(router))
    result = resolve_employee(ENT, "100", client)
    assert result.found and result.card.fio == FIO_B
    assert result.card.base_code == "zup_b"
    assert result.errors == []


def test_failing_base_does_not_fail_others_mandatory():
    """Обязательный тест приёмки: падение одной базы не валит остальные."""

    def router(url, headers, timeout):
        if "/a/" in url:
            raise OneCConnectionError("сеть базы A недоступна")  # база A упала
        return HttpResult(200, _card_json("100", FIO_B))  # база B жива

    client = OneCClient(_bases(), transport=FakeTransport(router))
    result = resolve_employee(ENT, "100", client)
    assert result.found is True
    assert result.card.fio == FIO_B
    assert len(result.errors) == 1 and "zup_a" in result.errors[0]
    # Упавшая база зафиксирована в errors, здоровая — вернула карточку.


def test_failing_base_also_isolated_on_search():
    def router(url, headers, timeout):
        if "/a/" in url:
            return HttpResult(500, "down")
        return HttpResult(
            200,
            json.dumps([{"Ref_Key": "ref-100", "Code": "100", "Description": FIO_B}]),
        )

    client = OneCClient(_bases(), transport=FakeTransport(router), failure_threshold=10)
    result = search_enterprise(ENT, "Выдуманова", client)
    assert result.card is not None and result.card.fio == FIO_B
    assert any("zup_a" in e for e in result.errors)


def test_all_bases_down_raises_not_found_with_errors():
    def router(url, headers, timeout):
        raise OneCConnectionError("все базы недоступны")

    client = OneCClient(_bases(), transport=FakeTransport(router))
    with pytest.raises(EmployeeNotFoundError) as exc_info:
        resolve_employee(ENT, "100", client)
    assert len(exc_info.value.errors) == 2  # обе базы зафиксированы


def test_unknown_enterprise():
    client = OneCClient(_bases(), transport=FakeTransport(lambda u, h, t: HttpResult(404, "{}")))
    with pytest.raises(UnknownEnterpriseError):
        resolve_employee("Несуществующее-Предприятие", "100", client)


def test_enterprise_bases_lists_bound_bases():
    client = OneCClient(_bases(), transport=FakeTransport(lambda u, h, t: HttpResult(404, "{}")))
    assert sorted(enterprise_bases(ENT, client)) == ["zup_a", "zup_b"]


def test_snapshot_marks_stale_after_24h():
    client = OneCClient(_bases(), transport=FakeTransport(lambda u, h, t: HttpResult(404, "{}")))
    now = dt.datetime.now(dt.timezone.utc)
    from app.onec_client import EmployeeCard as Card

    card = Card(enterprise=ENT, base_code="zup_a", tab_num="100", fio=FIO_A)
    fresh = make_snapshot_1c(card, now=now)
    assert fresh["key"] == "%s|%s|%s" % (ENT, "zup_a", "100")
    assert is_snapshot_stale(fresh, now=now) is False
    old = make_snapshot_1c(card, now=now - dt.timedelta(hours=25))
    assert is_snapshot_stale(old, now=now) is True

"""Тесты OneCClient на mock-HTTP (изоляция, таймаут, circuit-breaker, только GET).

Все ФИО вымышлены и используются только как фикстуры. URL вида
https://1c-mock.local/... — mock-адреса только для FakeTransport, боевые
настройки — исключительно из env ONEC_BASES_JSON (см. тест env-парсинга).
Общий conftest.py не используется (чужой файл A2); фикстуры — локально здесь.
"""
import dataclasses
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


def _card_json(tab, fio, ref=None, org=ENT):
    # OData-ответ справочника Catalog_Сотрудники (дефолты схемы): таб.№ = Code,
    # ФИО = Description, предприятие = ГоловнаяОрганизация_Key (код = Ref_Key).
    item = {
        "Ref_Key": ref or ("ref-" + tab),
        "Code": tab,
        "Description": fio,
        "ГоловнаяОрганизация_Key": org,
    }
    return json.dumps({"value": [item]}, ensure_ascii=False)


def _hr_json(ref, dept="Цех тестовый", position="Тестировщик", hire_date="2020-01-15", dismissal_date=None):
    # OData-ответ регистра текущих кадровых данных с $expand подразделения/должности.
    item = {
        "Сотрудник_Key": ref,
        "ТекущееПодразделение": {"Description": dept},
        "ТекущаяДолжность": {"Description": position},
        "ДатаПриема": hire_date,
    }
    if dismissal_date is not None:
        item["ДатаУвольнения"] = dismissal_date
    return json.dumps({"value": [item]}, ensure_ascii=False)


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
    # URL карточки/поиска по схеме: сущность Catalog_Сотрудники + $filter по Code.
    if "/a/" in url:
        return HttpResult(200, _card_json("100", FIOS["a100"]))
    return HttpResult(404, "{}")


def test_get_employee_success():
    t = FakeTransport(_ok_a)
    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=t)
    card = c.get_employee("zup_a", "100")
    assert card.fio == FIOS["a100"]
    assert card.key() == make_key(ENT, "zup_a", "100")


def test_list_hr_dismissals_pages_and_normalizes():
    """Постраничное чтение регистра кадровых данных: (Ref_Key, дата увольнения).

    Пустая дата регистра (0001-01-01) нормализуется в "" — увольнения не было;
    записи без Сотрудник_Key пропускаются; пагинация идёт через $skip/$top."""
    pages = [
        [("ref-1", "2026-04-14T00:00:00"), ("ref-2", "")],
        [("ref-3", "0001-01-01T00:00:00")],
    ]
    seen_skips = []

    def handler(url, headers, timeout):
        if "InformationRegister_" not in url:
            return HttpResult(404, "{}")
        seen_skips.append(url)
        index = len(seen_skips) - 1
        if index >= len(pages):
            return HttpResult(200, json.dumps({"value": []}))
        return HttpResult(
            200,
            json.dumps(
                {
                    "value": [
                        {
                            "Сотрудник_Key": ref,
                            "ДатаУвольнения": value,
                        }
                        for ref, value in pages[index]
                    ]
                },
                ensure_ascii=False,
            ),
        )

    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=FakeTransport(handler))
    first = c.list_hr_dismissals("zup_a", skip=0, top=2)
    second = c.list_hr_dismissals("zup_a", skip=2, top=2)
    assert first == [("ref-1", "2026-04-14T00:00:00"), ("ref-2", "")]
    assert second == [("ref-3", "")]  # 0001-01-01 — увольнения не было
    assert "$skip=2" in seen_skips[1]
    assert "$select" in seen_skips[0]  # лёгкая выборка двух полей


def test_list_hr_dismissals_without_register_configured():
    """Регистр не настроен (hr_entity пуст) — пустой список, без запроса."""
    cfg = dataclasses.replace(_bases()["zup_a"], hr_entity="")
    t = FakeTransport(_ok_a)
    c = OneCClient({"zup_a": cfg}, transport=t)
    assert c.list_hr_dismissals("zup_a") == []
    assert t.calls == []


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
        if "/a/" in url:
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
        return HttpResult(
            200,
            json.dumps(
                {"value": [{"Ref_Key": "ref-200", "Code": "200", "Description": FIOS["b200"]}]},
                ensure_ascii=False,
            ),
        )

    c = OneCClient({"zup_b": _bases()["zup_b"]}, transport=FakeTransport(h))
    cards = c.search("zup_b", "Несуществов")
    assert len(cards) == 1 and cards[0].fio == FIOS["b200"]
    assert cards[0].ref_key == "ref-200"


def test_list_employees_paginated_url_and_org_filter():
    """Выгрузка страницами: $top/$skip + фильтр предприятия (для автосвязки)."""
    from urllib.parse import parse_qs, unquote, urlparse

    seen = []

    def h(url, headers, timeout):
        seen.append(url)
        return HttpResult(
            200,
            json.dumps(
                {"value": [{"Ref_Key": "ref-1", "Code": "1", "Description": FIOS["a100"]}]},
                ensure_ascii=False,
            ),
        )

    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h))
    cards = c.list_employees("zup_a", ENT, skip=100, top=500)
    assert [card.fio for card in cards] == [FIOS["a100"]]
    q = parse_qs(urlparse(seen[0]).query)
    assert q["$top"] == ["500"]
    assert q["$skip"] == ["100"]
    assert "ГоловнаяОрганизация_Key eq guid'%s'" % ENT in unquote(q["$filter"][0])
    # Без предприятия — фильтра по организации нет.
    c.list_employees("zup_a", skip=0, top=50)
    q = parse_qs(urlparse(seen[1]).query)
    assert "$filter" not in q


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
    assert card.dismissal_date == ""
    # Отпуск/МОЛ убраны из карточки (Задача 1) — атрибутов нет.
    assert not hasattr(card, "vacation_balance")
    assert not hasattr(card, "mol_flag")


def test_normalize_odata_base_url():
    """URL веб-клиента дополняется /odata/standard.odata; полный OData-URL не меняется."""
    from app.onec_client import build_entity_url, normalize_odata_base_url

    assert normalize_odata_base_url("http://h/zup/ru/") == "http://h/zup/ru/odata/standard.odata"
    assert normalize_odata_base_url("http://h/zup") == "http://h/zup/odata/standard.odata"
    assert (
        normalize_odata_base_url("http://h/zup/odata/standard.odata/")
        == "http://h/zup/odata/standard.odata"
    )
    assert build_entity_url("http://h/zup/ru/", "Catalog_Организации") == (
        "http://h/zup/ru/odata/standard.odata/Catalog_%D0%9E%D1%80%D0%B3%D0%B0%D0%BD%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D0%B8?$format=json"
    )


def test_built_urls_are_ascii():
    """Кириллица в сущности/полях кодируется — URL пригоден для urllib (ascii)."""
    from urllib.parse import quote

    from app.onec_client import OneCClient

    cfg = _bases()["zup_a"]
    emp = OneCClient._build_employee_url(cfg, "100")
    search = OneCClient._build_search_url(cfg, "Сказочников")
    for url in (emp, search):
        url.encode("ascii")  # не бросает UnicodeEncodeError
    assert quote("Catalog_Сотрудники") in emp
    assert "СотрудникиОрганизаций" not in emp  # сырой кириллицы в пути нет


def test_employee_url_has_org_filter():
    """Карточка с предприятием: в $filter условие ГоловнаяОрганизация_Key eq guid'...'."""
    from urllib.parse import parse_qs, unquote, urlparse

    from app.onec_client import OneCClient

    cfg = _bases()["zup_a"]
    url = OneCClient._build_employee_url(cfg, "100", ENT)
    flt = unquote(parse_qs(urlparse(url).query)["$filter"][0])
    assert "Code eq '100'" in flt
    assert "ГоловнаяОрганизация_Key eq guid'%s'" % ENT in flt
    # Без предприятия — фильтра по организации нет.
    plain = OneCClient._build_employee_url(cfg, "100")
    assert "ГоловнаяОрганизация_Key" not in unquote(parse_qs(urlparse(plain).query)["$filter"][0])


def test_search_url_has_org_filter():
    """Поиск с предприятием: substringof по ФИО + фильтр организации в одном $filter."""
    from urllib.parse import parse_qs, unquote, urlparse

    from app.onec_client import OneCClient

    cfg = _bases()["zup_b"]
    url = OneCClient._build_search_url(cfg, "Выдуманова", ENT)
    flt = unquote(parse_qs(urlparse(url).query)["$filter"][0])
    assert "substringof('Выдуманова', Description) eq true" in flt
    assert "ГоловнаяОрганизация_Key eq guid'%s'" % ENT in flt


def test_search_paginated_url_and_total():
    """Поиск с пагинацией: $skip/$top + total из $inlinecount (odata.count)."""
    from urllib.parse import parse_qs, urlparse

    from app.onec_client import OneCClient, OneCSearchPage

    seen = []

    def h(url, headers, timeout):
        seen.append(url)
        return HttpResult(
            200,
            json.dumps(
                {
                    "odata.count": "7",
                    "value": [{"Ref_Key": "ref-1", "Code": "1", "Description": FIOS["b200"]}],
                },
                ensure_ascii=False,
            ),
        )

    c = OneCClient({"zup_b": _bases()["zup_b"]}, transport=FakeTransport(h))
    page = c.search_page("zup_b", "Выдуманова", ENT, skip=50, top=50)
    assert isinstance(page, OneCSearchPage)
    assert [card.fio for card in page.cards] == [FIOS["b200"]]
    assert page.total == 7
    q = parse_qs(urlparse(seen[0]).query)
    assert q["$top"] == ["50"]
    assert q["$skip"] == ["50"]
    assert q["$inlinecount"] == ["allpages"]
    # search без запроса счётчика: $inlinecount/$skip в URL не добавляются.
    c.search("zup_b", "Выдуманова")
    q = parse_qs(urlparse(seen[1]).query)
    assert "$inlinecount" not in q
    assert "$skip" not in q
    assert q["$top"] == ["50"]


def test_get_employee_enriches_from_hr_register():
    """Карточка: после справочника второй запрос к регистру кадровых данных
    ($expand подразделения/должности) заполняет депт/должность/дату приёма."""
    ref = "ref-100"
    seen = []

    def h(url, headers, timeout):
        seen.append(url)
        # ASCII-префикс «InformationRegister» в URL не кодируется (кириллица — да).
        if "InformationRegister" in url:
            return HttpResult(200, _hr_json(ref, dept="Цех тестовый", position="Тестировщик"))
        return HttpResult(200, _card_json("100", FIOS["a100"], ref=ref))

    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h))
    card = c.get_employee("zup_a", "100", ENT)
    assert card.fio == FIOS["a100"]
    assert card.dept == "Цех тестовый"
    assert card.position == "Тестировщик"
    assert card.hire_date == "2020-01-15"
    assert card.ref_key == ref
    assert len(seen) == 2  # справочник + регистр
    # Второй запрос: фильтр по Сотрудник_Key (guid) + $expand подразделения/должности.
    from urllib.parse import parse_qs, unquote, urlparse

    q = parse_qs(urlparse(seen[1]).query)
    assert "Сотрудник_Key eq guid'%s'" % ref in unquote(q["$filter"][0])
    assert "ТекущееПодразделение" in unquote(q["$expand"][0])
    assert "ТекущаяДолжность" in unquote(q["$expand"][0])


def test_hr_register_not_found_keeps_brief_card():
    """Записи кадровых данных нет (404) — карточка остаётся без депт/должности/приёма."""
    def h(url, headers, timeout):
        if "InformationRegister_ТекущиеКадровыеДанныеСотрудников" in url:
            return HttpResult(404, "{}")
        return HttpResult(200, _card_json("100", FIOS["a100"]))

    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h))
    card = c.get_employee("zup_a", "100")
    assert card.fio == FIOS["a100"]
    assert card.dept == "" and card.position == "" and card.hire_date == ""
    assert card.dismissal_date == ""


def test_get_employee_parses_dismissal_date():
    """Дата увольнения уволенного сотрудника — из регистра кадровых данных."""
    ref = "ref-100"

    def h(url, headers, timeout):
        if "InformationRegister" in url:
            return HttpResult(200, _hr_json(ref, dismissal_date="2026-09-30T00:00:00"))
        return HttpResult(200, _card_json("100", FIOS["a100"], ref=ref))

    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h))
    card = c.get_employee("zup_a", "100", ENT)
    assert card.dismissal_date == "2026-09-30T00:00:00"


def test_hr_empty_dismissal_date_normalized_to_empty():
    """Пустая дата увольнения в регистре (0001-01-01T00:00:00) — нормализуется в ''."""
    ref = "ref-100"

    def h(url, headers, timeout):
        if "InformationRegister" in url:
            return HttpResult(200, _hr_json(ref, dismissal_date="0001-01-01T00:00:00"))
        return HttpResult(200, _card_json("100", FIOS["a100"], ref=ref))

    c = OneCClient({"zup_a": _bases()["zup_a"]}, transport=FakeTransport(h))
    card = c.get_employee("zup_a", "100", ENT)
    assert card.dismissal_date == ""


def test_hr_register_not_configured_skips_second_request():
    """Регистр кадровых данных не настроен (hr_entity='') — только один запрос."""
    from dataclasses import replace

    seen = []

    def h(url, headers, timeout):
        seen.append(url)
        return HttpResult(200, _card_json("100", FIOS["a100"]))

    cfg = replace(_bases()["zup_a"], hr_entity="", hr_employee_field="")
    c = OneCClient({"zup_a": cfg}, transport=FakeTransport(h))
    card = c.get_employee("zup_a", "100")
    assert card.fio == FIOS["a100"]
    assert len(seen) == 1

# Подбор заместителя: GET /substitutes/candidates — узкая ручка, которой не
# хватало сотруднику (/api/ad/search закрыт блоком employee_directory,
# решение человека 2026-10-15). Доступна любому вошедшему, блока прав нет;
# источник — ЛОКАЛЬНАЯ таблица employees (только чтение, AD/1С не опрашиваются).
# В выдаче только сотрудники со связанной учёткой AD (ad_sam) — заместителем
# может быть лишь тот, у кого есть логин; текущий пользователь исключён.
#
# Офлайн-прогон без Postgres: локальный справочник подменён
# InMemoryEmployeeSyncStore (conftest, OFFLINE_BOUNDARIES + фикстура ниже).
# Все ФИО, логины и подразделения вымышленные.

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app import employee_sync  # noqa: E402
from app.employee_sync import (  # noqa: E402
    EmployeeSyncUnavailable,
    InMemoryEmployeeSyncStore,
    get_employee_sync_store,
)
from app.main import app  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_STEP_PREFIX = "SED_STEP_"

ENTERPRISE = "zavod.vymyshlennyy"
BASE_CODE = "ZUP_VYMYSH"
# Вымышленный сотрудник ОК из conftest: его логином подписан тест «текущего
# пользователя» — так проверяется, что себя в выдаче он не находит.
CURRENT_SAM = "ok.ivnova"
# Подстрока, общая для всех четырёх строк справочника в фикстуре.
NEEDLE = "Ефим"
# Подстрока второго семейства строк фикстуры: там проверяется отсечение
# уволенных и дедуп по логину (одна база/приём на человека — две строки).
STATUS_NEEDLE = "Статус"


def _row(tab_num: str, fio: str, ad_sam: str, dismissal: date | None = None) -> dict:
    """Строка локального справочника той же формы, что даёт синк из 1С.

    dismissal — дата увольнения из регистра кадровых данных (None = работает)."""
    return {
        "enterprise": ENTERPRISE,
        "base_code": BASE_CODE,
        "tab_num": tab_num,
        "fio": fio,
        "department": "Вымышленный отдел",
        "position": "Вымышленная должность",
        "ref_key": None,
        "ad_sam": ad_sam,
        "ad_status": "linked" if ad_sam else None,
        "dismissal_date": dismissal,
    }


@pytest.fixture(autouse=True)
def settings_override():
    """Группы ролей тестовыми (дефолты кода нейтральные) — как в наборе
    справочника заместителей: владелец шага входит по префиксу групп."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        HR_ADMIN_GROUPS=TEST_HR_ADMIN,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def directory():
    """Локальный справочник сотрудников с вымышленными данными.

    Первое семейство (подстрока NEEDLE): три сотрудника со связанной учёткой AD
    (один из них — текущий пользователь теста) и один без учётки: заместителем
    без логина быть не может.

    Второе семейство (подстрока STATUS_NEEDLE) — состояния по дате увольнения и
    дедуп одного человека: уволен сегодня (не выдаётся), увольняется позже
    (выдаётся), работает без даты (выдаётся) и ВТОРАЯ строка того же работника с
    тем же логином (в подборе человек один)."""
    store = InMemoryEmployeeSyncStore()
    today = employee_sync._today()  # «сегодня» той же зоной, что и у хранилища
    store.upsert_many(
        [
            _row("001", "Абриков Ефим Захарович", "efim.zahar"),
            _row("002", "Борисова Ефимия Ольговна", "efim.olga"),
            _row("003", "Гусев Ефим Без Учетки", ""),
            _row("004", "Дневой Ефим Себе", CURRENT_SAM),
            _row("005", "Козлов Статус Уволен", "kozlov.status", today),
            _row("006", "Крылов Статус Увольняется", "krylov.status", today + timedelta(days=30)),
            _row("007", "Ким Статус Работает", "kim.status"),
            _row("008", "Колотов Статус Работает Два", "kim.status"),
        ]
    )
    app.dependency_overrides[get_employee_sync_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_employee_sync_store, None)


class UnavailableDirectory(InMemoryEmployeeSyncStore):
    """Справочник сотрудников, который недоступен (ошибка БД) — 503, не 500."""

    def search_linked(self, q, limit, offset):
        raise EmployeeSyncUnavailable("Справочник сотрудников недоступен (тест)")


class BrokenDirectory(InMemoryEmployeeSyncStore):
    """Справочник, который падает не штатным исключением — тоже 503, не 500."""

    def search_linked(self, q, limit, offset):
        raise RuntimeError("обрыв связи с хранилищем (тест)")


def _items(response) -> list[dict]:
    assert response.status_code == 200, response.text
    return response.json()["items"]


# ---------------------------------------------------------------------------
# Подбор: по ФИО и по логину, состав выдачи
# ---------------------------------------------------------------------------


def test_candidates_available_to_any_authenticated(client, hr_headers, owner_headers, directory):
    """Подбор открыт любому вошедшему: блок employee_directory на нём не действует
    (в отличие от /ad/search), поэтому и сотрудник ОК, и владельцу шага кандидаты
    доступны одинаково."""
    for headers in (hr_headers, owner_headers):
        response = client.get("/substitutes/candidates", params={"q": NEEDLE}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert {"efim.zahar", "efim.olga"} <= {item["sam"] for item in body["items"]}
        assert body["page"] == 1 and body["page_size"] == 50


def test_candidates_find_by_fio_and_by_login(client, hr_headers, directory):
    """Сотрудника находится и по части ФИО, и по части логина (без учёта регистра).

    Состав элемента — sam (это и есть логин для записи), ФИО, подразделение и
    должность; больше ничего в выдаче нет."""
    by_fio = client.get("/substitutes/candidates", params={"q": "Борисова"}, headers=hr_headers)
    assert _items(by_fio) == [
        {
            "sam": "efim.olga",
            "fio": "Борисова Ефимия Ольговна",
            "department": "Вымышленный отдел",
            "position": "Вымышленная должность",
        }
    ]
    by_login = client.get("/substitutes/candidates", params={"q": "ZAHAR"}, headers=hr_headers)
    assert [item["sam"] for item in _items(by_login)] == ["efim.zahar"]


def test_candidates_hide_unlinked_and_current_user(client, hr_headers, directory):
    """В выдаче нет ни сотрудника без учётки AD, ни самого пользователя.

    Без учётки заместителем быть нельзя (логин нужен для резолвера), а себя
    заместителем не назначают: строка текущего пользователя отсекается на
    границе, поэтому в items её нет, а total её ещё считает."""
    response = client.get("/substitutes/candidates", params={"q": NEEDLE}, headers=hr_headers)
    body = response.json()
    assert [item["sam"] for item in body["items"]] == ["efim.zahar", "efim.olga"]
    assert body["total"] == 3  # сотрудник без учётки в фильтр не попал
    # Отдельно: поимённый поиск несвязанного — пусто (total тоже 0).
    unlinked = client.get("/substitutes/candidates", params={"q": "Гусев"}, headers=hr_headers)
    assert _items(unlinked) == [] and unlinked.json()["total"] == 0
    # Текущий пользователь: запись в справочнике есть, а выбрать себя нельзя.
    myself = client.get("/substitutes/candidates", params={"q": "Дневой"}, headers=hr_headers)
    assert _items(myself) == [] and myself.json()["total"] == 1


def test_candidates_hide_dismissed_and_dedupe_by_login(client, hr_headers, directory):
    """Уволенные в подборе скрыты, а человек с несколькими строками справочника
    выдаётся один раз.

    Первое — правило проекта «увольнение определяет 1С, уволенных скрыто»: заместителем
    назначают действующего сотрудника. Второе — ключ справочника составной
    (enterprise+base_code+tab_num), одна учётка может быть связана с несколькими
    карточками 1С; в модалке это дало бы дубликаты одного человека."""
    body = client.get(
        "/substitutes/candidates", params={"q": STATUS_NEEDLE}, headers=hr_headers
    ).json()
    # Козлов уволен сегодня — нет; Ким работает и Крылов увольняется позже — есть
    # (порядок выдачи — по ФИО).
    assert [item["sam"] for item in body["items"]] == ["kim.status", "krylov.status"]
    # Ким в справочнике двумя строками (007 и 008) — в выдаче один.
    assert sum(1 for item in body["items"] if item["sam"] == "kim.status") == 1
    assert body["total"] == 2  # два работника с одним логином считаются одним


def test_sql_of_candidates_filters_dismissed_and_dedupes():
    """Боевой SQL подбора отсекает уволенных и схлопывает строки по логину.

    Офлайн-прогоны идут через хранилище в памяти, поэтому текст запросов
    проверяется отдельно — по образцу проверок SQL в test_employee_hr_sync.py."""
    from app.employee_sync import DbEmployeeSyncStore

    where = DbEmployeeSyncStore._LINKED_FILTER_SQL
    assert "dismissal_date IS NULL OR dismissal_date > CURRENT_DATE" in where
    assert "btrim(COALESCE(ad_sam, '')) <> ''" in where
    select_sql = str(DbEmployeeSyncStore._SEARCH_LINKED_SQL)
    assert "DISTINCT ON (ad_sam)" in select_sql
    assert DbEmployeeSyncStore._LINKED_FILTER_SQL in select_sql
    count_sql = str(DbEmployeeSyncStore._COUNT_LINKED_SQL)
    assert "count(DISTINCT ad_sam)" in count_sql
    assert DbEmployeeSyncStore._LINKED_FILTER_SQL in count_sql


def test_candidates_paginated_by_fio(client, hr_headers, directory):
    """Страницы серверные и упорядочены по ФИО; total отражает фильтр хранилища."""
    first = client.get(
        "/substitutes/candidates", params={"q": NEEDLE, "page_size": 1}, headers=hr_headers
    )
    assert first.json()["page_size"] == 1 and first.json()["total"] == 3
    assert [item["fio"] for item in _items(first)] == ["Абриков Ефим Захарович"]
    second = client.get(
        "/substitutes/candidates",
        params={"q": NEEDLE, "page": 2, "page_size": 1},
        headers=hr_headers,
    )
    assert [item["fio"] for item in _items(second)] == ["Борисова Ефимия Ольговна"]


def test_candidates_no_match_returns_empty(client, hr_headers, directory):
    """Совпадений нет — пустая выдача и total = 0 (не ошибка и не весь справочник)."""
    response = client.get("/substitutes/candidates", params={"q": "Нетакого"}, headers=hr_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == [] and body["total"] == 0


def test_candidates_writes_no_audit_with_personal_data(client, hr_headers, directory):
    """Подбор не аудитируется: выдача — это ФИО сотрудников, а право её видеть
    решение человека дало всем вошедшим; в журнале остаются сами записи
    (substitutes.create/update/delete)."""
    client.get("/substitutes/candidates", params={"q": NEEDLE}, headers=hr_headers)
    assert not [event for event in audit_log.all() if event.action.startswith("employees.")]


# ---------------------------------------------------------------------------
# Ошибки границы: 422 по подстроке, 503 по хранилищу, 401 без сессии
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "  "},
        {"q": "Е"},
        {"q": "Еф"},
    ],
    ids=["без-q", "пусто", "пробелы", "один-символ", "два-символа"],
)
def test_candidates_short_query_422(client, hr_headers, directory, params):
    """Подстрока короче 3 символов (в том числе пустая) — 422 с понятным текстом.

    Три, а не два: на employees совпадений слишком много, а ручка доступна
    любому вошедшему (решение человека 2026-10-15 про подбор через модалку)."""
    response = client.get("/substitutes/candidates", params=params, headers=hr_headers)
    assert response.status_code == 422, response.text
    assert "не менее 3 символов" in response.json()["detail"]


def test_candidates_query_is_trimmed(client, hr_headers, directory):
    """Пробелы вокруг подстроки не считаются символами: « Ефим » ищет так же,
    как «Ефим»."""
    padded = client.get("/substitutes/candidates", params={"q": "  %s  " % NEEDLE}, headers=hr_headers)
    plain = client.get("/substitutes/candidates", params={"q": NEEDLE}, headers=hr_headers)
    assert padded.json() == plain.json()


@pytest.mark.parametrize(
    "broken",
    [UnavailableDirectory(), BrokenDirectory()],
    ids=["справочник-недоступен", "прочие-сбои"],
)
def test_candidates_store_down_503(client, hr_headers, directory, broken):
    """Хранилище сотрудников недоступно (или падает не штатно) — 503, не 500."""
    app.dependency_overrides[get_employee_sync_store] = lambda: broken
    response = client.get("/substitutes/candidates", params={"q": NEEDLE}, headers=hr_headers)
    assert response.status_code == 503, response.text
    assert "недоступен" in response.json()["detail"]


def test_candidates_no_auth_401(client, noauth_headers, directory):
    """Без сессии подбор закрыт (401) — как и любые другие входы в API."""
    assert (
        client.get("/substitutes/candidates", params={"q": NEEDLE}, headers=noauth_headers).status_code
        == 401
    )
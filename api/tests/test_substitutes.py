# Справочник заместителей (миграция 0023): CRUD /settings/substitutes.
# Гейт на всех ручках — блок прав settings_substitutes (выдан шаблонам
# sed_admin/admin сидом 0023), разбор состояния — 422 (ошибки тела запроса),
# 409 (пара «сотрудник + заместитель» уже есть), 404 (нет записи по id) и 503
# (хранилище недоступно). Аудит substitutes.create/update/delete пишет в details
# только срок и признак писем — логинов и ФИО там быть не должно (ПДн).
#
# Офлайн-прогон без Postgres: справочник заместителей подменён
# InMemorySubstituteStore (conftest, OFFLINE_BOUNDARIES), справочник прав —
# InMemoryPermissionsStore с зеркалом миграции 0023. Все логины вымышленные.
#
# Вторая половина набора — личный кабинет /substitutes/my (заместитель
# назначает себе сам сотрудник, решение человека 2026-10-15): блока прав там
# нет, гейт один — принадлежность записи (principal_sam == логин сессии), а
# чужая запись отвечает 404 наравне с отсутствующей, чтобы по коду ответа не
# было видно, что она существует.
#
# Третья часть — лимит числа заместителей (настройка substitute_deputies_limit,
# сид миграции 0025): третьего заместителя завести можно, четвёртого — нет.

from __future__ import annotations

import base64
import os
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.employee_sync import (  # noqa: E402
    EmployeeSyncUnavailable,
    InMemoryEmployeeSyncStore,
    get_employee_sync_store,
)
from app.main import app  # noqa: E402
from app.settings_routes import (  # noqa: E402
    SettingsUnavailable,
    get_settings_store,
)
from app.substitute_store import (  # noqa: E402
    InMemorySubstituteStore,
    SubstituteNotFound,
    SubstitutesUnavailable,
    get_substitute_store,
)
from app.timezone import current_timezone  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные логины (не реальные sAMAccountName) и даты записи справочника.
PRINCIPAL = "sotrudnik.osnovnoy"
DEPUTY = "zameshchitel.vymyshlennyy"
VALID_FROM = "2026-10-01"
VALID_TO = "2026-12-31"
ADMIN_SAM = "adm.petrov"
ADMIN_FIO = "Петров Сергей Андреевич"
# Владелец шага и сотрудник ОК из conftest: у обоих нет блока
# settings_substitutes, но личные ручки им доступны.
OWNER_SAM = "step.sidorov"
HR_SAM = "ok.ivnova"
SECOND_DEPUTY = "zameshchitel.vtoroj"
# Ещё четыре вымышленных заместителя — для проверки лимита нужно больше записей,
# чем дефолт 3 (все они в справочнике сотрудников фикстуры directory).
THIRD_DEPUTY = "zameshchitel.tretij"
FOURTH_DEPUTY = "zameshchitel.chetvertyj"
FIFTH_DEPUTY = "zameshchitel.pytyj"
SIXTH_DEPUTY = "zameshchitel.shestoy"
# Ключ настройки с лимитом заместителей (инфра-ключ, сид миграции 0025).
LIMIT_KEY = "substitute_deputies_limit"


def _b64(value: str) -> str:
    """Кодирование мок-значения в base64: HTTP-заголовки обязаны быть ASCII."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


class UnavailableDirectory(InMemoryEmployeeSyncStore):
    """Справочник сотрудников, недоступный по БД, — 503 при назначении заместителя."""

    def search_linked(self, q, limit, offset):
        raise EmployeeSyncUnavailable("Справочник сотрудников недоступен (тест)")


class UnavailableSubstituteStore:
    """Справочник заместителей, который недоступен (ошибка БД) — 503, не 500."""
    def list_all(self):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")

    def list_active(self, on):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")

    def by_id(self, sub_id):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")

    def list_by_principal(self, principal_sam):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")

    def create(self, **kwargs):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")

    def update(self, sub_id, **kwargs):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")

    def delete(self, sub_id):
        raise SubstitutesUnavailable("Хранилище справочника заместителей недоступно (тест)")


@pytest.fixture
def directory():
    """Справочник сотрудников: в нём есть вымышленный заместитель (учётка связана).

    Личные ручки проверяют заместителя по локальной таблице `employees` (иначе
    запись завёлась бы на несуществующий логин и молча никогда не сработала бы),
    поэтому в фикстуре нужен сотрудник с `ad_sam`. Админские ручки справочник не
    смотрят и работают с этой фикстурой так же."""
    store = InMemoryEmployeeSyncStore()
    store.upsert_many(
        [
            {
                "enterprise": "zavod.vymyshlennyy",
                "base_code": "ZUP_VYMYSH",
                "tab_num": tab,
                "fio": fio,
                "department": "Служба вымышленного учета",
                "position": "Вымышленная должность",
                "ref_key": None,
                "ad_sam": sam,
                "ad_status": "linked",
                "dismissal_date": None,
            }
            for tab, sam, fio in (
                ("В-0001", DEPUTY, "Заместителев Вымышленный Заместителевич"),
                ("В-0002", SECOND_DEPUTY, "Второй Вымышленный Заместителевич"),
                # Владелец шага из conftest — тоже сотрудник: его логином
                # проверяется обратная пара (кто-то назначил заместителем меня).
                ("В-0003", OWNER_SAM, "Сидоров Вымышленный Владелевич"),
                # Четыре заместителя сверх первых двух — под лимит заместителей.
                ("В-0004", THIRD_DEPUTY, "Третий Вымышленный Заместителевич"),
                ("В-0005", FOURTH_DEPUTY, "Четвёртый Вымышленный Заместителевич"),
                ("В-0006", FIFTH_DEPUTY, "Пятый Вымышленный Заместителевич"),
                ("В-0007", SIXTH_DEPUTY, "Шестой Вымышленный Заместителевич"),
            )
        ]
    )
    app.dependency_overrides[get_employee_sync_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_employee_sync_store, None)


@pytest.fixture(autouse=True)
def settings_override():
    """Группы ролей тестовыми (дефолты кода нейтральные).

    От них зависят и вход по мок-заголовкам, и группы шаблонов офлайн-справочника
    прав: без ADMIN_GROUPS шаблон admin никому не совпал бы и гейт отдал бы 403."""
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
def substitutes():
    """Справочник заместителей через зависимость (чистое состояние на тест)."""
    store = InMemorySubstituteStore()
    app.dependency_overrides[get_substitute_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_substitute_store, None)


def _body(**kw) -> dict:
    """Тело записи справочника с вымышленными значениями по умолчанию."""
    body = {
        "principal_sam": PRINCIPAL,
        "deputy_sam": DEPUTY,
        "valid_from": VALID_FROM,
        "valid_to": VALID_TO,
        "notify": True,
        "note": "Вымышленная причина замещения",
    }
    body.update(kw)
    return body


def _create(client, headers, **kw):
    """POST записи справочника (по умолчанию — корректное тело)."""
    return client.post("/settings/substitutes", json=_body(**kw), headers=headers)


def _seed(client, headers, **kw) -> int:
    """Создать запись и вернуть её id (тест сам проверяет статус)."""
    response = _create(client, headers, **kw)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _audit(action: str):
    """Последнее событие журнала аудита с таким действием."""
    events = [event for event in audit_log.all() if event.action == action]
    assert events, "событие аудита %s не записано" % action
    return events[-1]


def _admin_headers() -> dict:
    """Заголовки администратора (тот же состав, что фикстура admin_headers)."""
    return {
        "X-Mock-Sam": ADMIN_SAM,
        "X-Mock-Fio": _b64(ADMIN_FIO),
        "X-Mock-Mail": _b64("adm.petrov@example.com"),
        "X-Mock-Department": _b64("ИТ-департамент"),
        "X-Mock-Title": _b64("Администратор СЭД"),
        "X-Mock-Groups": TEST_ADMINS,
    }


def _my_body(**kw) -> dict:
    """Тело личной ручки /substitutes/my: логина оригинала в нём нет — он
    берётся из сессии."""
    body = {
        "deputy_sam": DEPUTY,
        "valid_from": VALID_FROM,
        "valid_to": VALID_TO,
        "notify": True,
        "note": "Вымышленная причина замещения",
    }
    body.update(kw)
    return body


def _my_create(client, headers, **kw) -> dict:
    """POST личной ручки и вернуть тело ответа (статус проверяет сам тест)."""
    response = client.post("/substitutes/my", json=_my_body(**kw), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Чтение справочника: гейт по блоку settings_substitutes
# ---------------------------------------------------------------------------


def test_list_returns_whole_reference(client, admin_headers, substitutes):
    """Список отдаёт весь справочник, включая записи с истёкшим сроком.

    Админка правит и удаляет истёкшие записи, поэтому фильтра по «сегодня» нет."""
    assert client.get("/settings/substitutes", headers=admin_headers).json() == []
    _seed(client, admin_headers)
    _seed(client, admin_headers, deputy_sam="zameshchitel.vtoroj", valid_from="2020-01-01", valid_to="2020-06-01")
    response = client.get("/settings/substitutes", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    # Контракт формы ответа зафиксирован намеренно: список — МАССИВ (как все
    # прочие списки API), не объект с полем items. На этом стыке клиент админки
    # уже разъезжался с сервером.
    assert isinstance(body, list)
    assert [(item["principal_sam"], item["deputy_sam"]) for item in body] == [
        (PRINCIPAL, "zameshchitel.vtoroj"),
        (PRINCIPAL, DEPUTY),
    ]
    assert {item["notify"] for item in body} == {True}


def test_list_forbidden_without_block(
    client, hr_headers, hr_admin_headers, owner_headers, nogroup_headers, substitutes
):
    """Без блока settings_substitutes справочник закрыт (403) — замещение не
    выдаёт права на его правку, а блок есть только у админов (сид 0023)."""
    for headers in (hr_headers, hr_admin_headers, owner_headers, nogroup_headers):
        assert client.get("/settings/substitutes", headers=headers).status_code == 403, headers


def test_create_forbidden_without_block(client, hr_headers, substitutes):
    """Запись в справочник — тоже админская функция (403 у сотрудника ОК)."""
    assert _create(client, hr_headers).status_code == 403
    assert substitutes.list_all() == []


# ---------------------------------------------------------------------------
# Создание: контракт записи, аудит, ошибки данных
# ---------------------------------------------------------------------------


def test_create_substitute_saves_record_with_audit(client, admin_headers, substitutes):
    """POST: 201, запись сохранена целиком, аудит substitutes.create без ПДн."""
    response = _create(client, admin_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["principal_sam"] == PRINCIPAL
    assert body["deputy_sam"] == DEPUTY
    assert body["valid_from"] == VALID_FROM
    assert body["valid_to"] == VALID_TO
    assert body["notify"] is True
    assert body["note"] == "Вымышленная причина замещения"
    assert body["created_by"] == "adm.petrov"
    assert body["created_at"] and body["updated_at"] is None
    stored = substitutes.by_id(body["id"])
    assert stored is not None and stored.principal_sam == PRINCIPAL
    event = _audit("substitutes.create")
    assert event.actor == "adm.petrov"
    assert event.entity == "substitute"
    assert event.entity_id == str(body["id"])


def test_create_defaults_notify_and_open_ended_period(client, admin_headers, substitutes):
    """notify по умолчанию включён, valid_to = None — «действует, пока не отменят»."""
    response = client.post(
        "/settings/substitutes",
        json={"principal_sam": PRINCIPAL, "deputy_sam": DEPUTY, "valid_from": VALID_FROM},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["notify"] is True
    assert response.json()["valid_to"] is None


def test_create_strips_logins_and_note(client, admin_headers, substitutes):
    """Пробелы вокруг логинов и пояснения срезаются — иначе пара не совпадёт."""
    response = _create(
        client, admin_headers, principal_sam="  %s  " % PRINCIPAL, note="  отпуск  "
    )
    assert response.status_code == 201, response.text
    assert response.json()["principal_sam"] == PRINCIPAL
    assert response.json()["note"] == "отпуск"


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"deputy_sam": PRINCIPAL}, "тем же сотрудником"),
        ({"principal_sam": DEPUTY, "deputy_sam": DEPUTY}, "тем же сотрудником"),
        ({"principal_sam": "   "}, "не может быть пустым"),
        ({"deputy_sam": "  "}, "не может быть пустым"),
        ({"valid_from": VALID_TO, "valid_to": VALID_FROM}, "раньше начала"),
    ],
)
def test_create_bad_data_422(client, admin_headers, substitutes, overrides, needle):
    """Ошибки данных записи — 422 до похода в хранилище (справочник пуст)."""
    response = _create(client, admin_headers, **overrides)
    assert response.status_code == 422, response.text
    assert needle in response.text
    assert substitutes.list_all() == []


def test_create_duplicate_pair_409(client, admin_headers, substitutes):
    """Пара «сотрудник + заместитель» уникальна: повтор — 409 (не 422 и не 503)."""
    _seed(client, admin_headers)
    duplicate = _create(client, admin_headers)
    assert duplicate.status_code == 409, duplicate.text
    assert "уже есть" in duplicate.text
    assert len(substitutes.list_all()) == 1
    # Обратная пара (и другой заместитель той же пары) — не конфликт.
    assert _create(
        client, admin_headers, principal_sam=DEPUTY, deputy_sam=PRINCIPAL
    ).status_code == 201
    assert len(substitutes.list_all()) == 2


# ---------------------------------------------------------------------------
# Правка: 200 с новым составом, 404, 409
# ---------------------------------------------------------------------------


def test_update_substitute_replaces_fields_with_audit(client, admin_headers, substitutes):
    """PUT: запись меняется целиком, updated_at проставляется, аудит — update."""
    sub_id = _seed(client, admin_headers)
    response = client.put(
        f"/settings/substitutes/{sub_id}",
        json={
            "principal_sam": PRINCIPAL,
            "deputy_sam": "zameshchitel.tretij",
            "valid_from": "2026-11-01",
            "valid_to": None,
            "notify": False,
            "note": "Вымышленная новая причина",
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deputy_sam"] == "zameshchitel.tretij"
    assert body["valid_from"] == "2026-11-01"
    assert body["valid_to"] is None
    assert body["notify"] is False
    assert body["updated_at"]
    stored = substitutes.by_id(sub_id)
    assert stored.deputy_sam == "zameshchitel.tretij" and stored.notify is False
    event = _audit("substitutes.update")
    assert event.entity_id == str(sub_id)


def test_update_same_record_is_not_a_duplicate(client, admin_headers, substitutes):
    """PUT с теми же полами — 200: пара с собственной записью не конфликтует."""
    sub_id = _seed(client, admin_headers)
    response = client.put(
        f"/settings/substitutes/{sub_id}", json=_body(), headers=admin_headers
    )
    assert response.status_code == 200, response.text


def test_update_pair_taken_by_another_record_409(client, admin_headers, substitutes):
    """PUT на чужую пару — 409, обе записи остаются прежними."""
    first = _seed(client, admin_headers)
    second = _seed(client, admin_headers, deputy_sam="zameshchitel.vtoroj")
    response = client.put(
        f"/settings/substitutes/{second}",
        json=_body(deputy_sam="zameshchitel.vtoroj_2"),
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    # Теперь первая запись хочет пару, которую занял второй: 409.
    conflict = client.put(
        f"/settings/substitutes/{first}",
        json=_body(deputy_sam="zameshchitel.vtoroj_2"),
        headers=admin_headers,
    )
    assert conflict.status_code == 409, conflict.text
    assert substitutes.by_id(first).deputy_sam == DEPUTY


def test_update_unknown_id_404(client, admin_headers, substitutes):
    """Записи с таким id нет — 404 (справочник не меняется)."""
    response = client.put("/settings/substitutes/424242", json=_body(), headers=admin_headers)
    assert response.status_code == 404, response.text
    assert "не найдена" in response.text
    assert substitutes.list_all() == []


def test_update_bad_data_422(client, admin_headers, substitutes):
    """Правка проверяется теми же правилами, что и создание (422)."""
    sub_id = _seed(client, admin_headers)
    response = client.put(
        f"/settings/substitutes/{sub_id}",
        json=_body(deputy_sam=PRINCIPAL),
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert substitutes.by_id(sub_id).deputy_sam == DEPUTY


def test_update_forbidden_without_block(client, hr_headers, substitutes):
    """Правка справочника — админская функция (403), запись остаётся прежней."""
    sub_id = _seed(client, _admin_headers())
    response = client.put(
        f"/settings/substitutes/{sub_id}", json=_body(), headers=hr_headers
    )
    assert response.status_code == 403
    assert substitutes.by_id(sub_id).deputy_sam == DEPUTY


# ---------------------------------------------------------------------------
# Удаление: 200 с {id, deleted}, 404
# ---------------------------------------------------------------------------


def test_delete_substitute_and_audit(client, admin_headers, substitutes):
    """DELETE: запись исчезает из справочника, аудит substitutes.delete."""
    sub_id = _seed(client, admin_headers)
    response = client.delete(f"/settings/substitutes/{sub_id}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"id": sub_id, "deleted": True}
    assert substitutes.by_id(sub_id) is None
    assert substitutes.list_all() == []
    assert _audit("substitutes.delete").entity_id == str(sub_id)


def test_delete_unknown_id_404(client, admin_headers, substitutes):
    """Удаление несуществующей записи — 404."""
    response = client.delete("/settings/substitutes/424242", headers=admin_headers)
    assert response.status_code == 404, response.text
    assert "не найдена" in response.text


def test_delete_forbidden_without_block(client, hr_headers, substitutes):
    """Удаление записи — админская функция (403), запись остаётся."""
    sub_id = _seed(client, _admin_headers())
    assert client.delete(f"/settings/substitutes/{sub_id}", headers=hr_headers).status_code == 403
    assert substitutes.by_id(sub_id) is not None


# ---------------------------------------------------------------------------
# Хранилище недоступно: 503 на всех ручках, не 500
# ---------------------------------------------------------------------------


def _unavailable():
    """Подменить справочник на «упавший» (503 на границе)."""
    app.dependency_overrides[get_substitute_store] = lambda: UnavailableSubstituteStore()


def test_list_store_down_503(client, admin_headers, substitutes):
    """Чтение при недоступном хранилище — 503 с понятным текстом."""
    _unavailable()
    response = client.get("/settings/substitutes", headers=admin_headers)
    assert response.status_code == 503
    assert "недоступно" in response.json()["detail"]


def test_create_store_down_503(client, admin_headers, substitutes):
    """Создание при недоступном хранилище — 503 (а не 500 и не 201)."""
    _unavailable()
    response = _create(client, admin_headers)
    assert response.status_code == 503
    assert not [e for e in audit_log.all() if e.action == "substitutes.create"]


def test_update_store_down_503(client, admin_headers, substitutes):
    """Правка при недоступном хранилище — 503."""
    sub_id = _seed(client, admin_headers)
    _unavailable()
    response = client.put(
        f"/settings/substitutes/{sub_id}", json=_body(), headers=admin_headers
    )
    assert response.status_code == 503


def test_delete_store_down_503(client, admin_headers, substitutes):
    """Удаление при недоступном хранилище — 503."""
    sub_id = _seed(client, admin_headers)
    _unavailable()
    assert client.delete(f"/settings/substitutes/{sub_id}", headers=admin_headers).status_code == 503


def test_forbidden_wins_over_unavailable_store(client, hr_headers, substitutes):
    """Нет блока прав — 403 даже при упавшем хранилище: гейт проверяется первым."""
    _unavailable()
    assert client.get("/settings/substitutes", headers=hr_headers).status_code == 403


# ---------------------------------------------------------------------------
# Аудит без ПДн: логинов и ФИО сотрудников в details нет
# ---------------------------------------------------------------------------


def test_audit_details_carry_no_logins_or_fio(client, admin_headers, substitutes):
    """В пояснении события — только срок и признак писем, без логинов и ФИО.

    Состав пары «кто кого замещает» — ПДн, поэтому в журнал он не попадает."""
    sub_id = _seed(client, admin_headers, note="Вымышленная причина замещения")
    client.put(
        f"/settings/substitutes/{sub_id}",
        json=_body(deputy_sam="zameshchitel.vtoroj"),
        headers=admin_headers,
    )
    client.delete(f"/settings/substitutes/{sub_id}", headers=admin_headers)
    events = [e for e in audit_log.all() if e.action.startswith("substitutes.")]
    assert [e.action for e in events] == [
        "substitutes.create",
        "substitutes.update",
        "substitutes.delete",
    ]
    for event in events:
        blob = " ".join([event.actor, str(event.entity_id), event.detail])
        assert PRINCIPAL not in blob, event
        assert "zameshchitel.vtoroj" not in blob, event
        assert "Вымышленная" not in event.detail, event
    # Срок и признак писем в пояснении остаются (по ним видно, что меняли).
    assert VALID_FROM in events[0].detail
    assert "письма заместителю: да" in events[0].detail
    assert "запись удалена" == events[-1].detail


# ---------------------------------------------------------------------------
# Личный кабинет /substitutes/my: без блока прав, только свои записи
# ---------------------------------------------------------------------------


def test_my_create_available_to_any_authenticated(client, owner_headers, hr_headers, substitutes, directory):
    """Заместителя назначает себе любой вошедший: блок прав не нужен, в том числе
    рядовому владельцу шага (owner). principal_sam в ответе — логин сессии."""
    for headers, sam in ((owner_headers, OWNER_SAM), (hr_headers, HR_SAM)):
        response = client.post("/substitutes/my", json=_my_body(), headers=headers)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["principal_sam"] == sam, body
        assert body["deputy_sam"] == DEPUTY
        assert body["valid_from"] == VALID_FROM
        assert body["valid_to"] == VALID_TO
        assert body["created_by"] == sam
        stored = substitutes.by_id(body["id"])
        assert stored is not None and stored.principal_sam == sam


def test_my_create_rejects_principal_in_body(client, owner_headers, substitutes):
    """Логин оригинала в теле личной ручки НЕ принимается (422).

    Иначе сотрудник назначил бы заместителя другому человеку; запись при этом
    не создаётся."""
    response = client.post(
        "/substitutes/my", json=_my_body(principal_sam=PRINCIPAL), headers=owner_headers
    )
    assert response.status_code == 422, response.text
    assert "указывать нельзя" in response.text
    assert substitutes.list_all() == []


def test_my_list_returns_only_own_records(client, owner_headers, hr_headers, admin_headers, substitutes):
    """В выдаче только свои записи, в том числе с истёкшим сроком (их надо видеть
    и удалять); чужие, заведённые админом, не отдаются никому."""
    _seed(client, admin_headers, principal_sam=OWNER_SAM)
    _seed(
        client,
        admin_headers,
        principal_sam=OWNER_SAM,
        deputy_sam=SECOND_DEPUTY,
        valid_from="2020-01-01",
        valid_to="2020-06-01",
    )
    _seed(client, admin_headers, principal_sam=PRINCIPAL)
    _seed(client, admin_headers, principal_sam=HR_SAM, deputy_sam="zameshchitel.tretij")
    response = client.get("/substitutes/my", headers=owner_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {item["principal_sam"] for item in body} == {OWNER_SAM}
    assert sorted(item["deputy_sam"] for item in body) == sorted([DEPUTY, SECOND_DEPUTY])
    # У сотрудника ОК в своём кабинете только своя запись: чужие не «протекают».
    hr_body = client.get("/substitutes/my", headers=hr_headers).json()
    assert [(item["principal_sam"], item["deputy_sam"]) for item in hr_body] == [
        (HR_SAM, "zameshchitel.tretij")
    ]


def test_my_update_and_delete_own_record(client, owner_headers, substitutes, directory):
    """Свою запись сотрудник правит и удаляет сам (без блока прав)."""
    sub_id = _my_create(client, owner_headers)["id"]
    updated = client.put(
        f"/substitutes/my/{sub_id}",
        json=_my_body(deputy_sam=SECOND_DEPUTY, valid_to=None, notify=False),
        headers=owner_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["deputy_sam"] == SECOND_DEPUTY
    assert updated.json()["notify"] is False
    assert substitutes.by_id(sub_id).deputy_sam == SECOND_DEPUTY
    deleted = client.delete(f"/substitutes/my/{sub_id}", headers=owner_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": sub_id, "deleted": True}
    assert substitutes.by_id(sub_id) is None


def test_my_update_foreign_record_404(client, owner_headers, admin_headers, substitutes):
    """PUT чужой записи — 404, а не 403 и не 409: по коду ответа не должно быть
    видно, что запись существует. У другого сотрудника она не меняется."""
    sub_id = _seed(client, admin_headers, principal_sam=PRINCIPAL)
    response = client.put(f"/substitutes/my/{sub_id}", json=_my_body(), headers=owner_headers)
    assert response.status_code == 404, response.text
    assert "не найдена" in response.text
    stored = substitutes.by_id(sub_id)
    assert stored.principal_sam == PRINCIPAL and stored.deputy_sam == DEPUTY
    assert stored.updated_at is None
    # Отказ не пишется в аудит: чужую запись пользователь даже не тронул.
    assert not [
        e for e in audit_log.all() if e.action in ("substitutes.update", "substitutes.delete")
    ]


def test_my_delete_foreign_record_404(client, owner_headers, admin_headers, substitutes):
    """DELETE чужой записи — 404 (как и отсутствующей), запись у другого
    сотрудника остаётся, аудита удаления нет."""
    sub_id = _seed(client, admin_headers, principal_sam=PRINCIPAL)
    response = client.delete(f"/substitutes/my/{sub_id}", headers=owner_headers)
    assert response.status_code == 404, response.text
    assert "не найдена" in response.text
    assert substitutes.by_id(sub_id) is not None
    assert not [
        e for e in audit_log.all() if e.action in ("substitutes.update", "substitutes.delete")
    ]


def test_my_unknown_id_404(client, owner_headers, substitutes):
    """Несуществующий id — 404 у PUT и DELETE (как у админских ручек)."""
    assert (
        client.put("/substitutes/my/424242", json=_my_body(), headers=owner_headers).status_code
        == 404
    )
    assert client.delete("/substitutes/my/424242", headers=owner_headers).status_code == 404


@pytest.mark.parametrize(
    "overrides, needle",
    [
        ({"deputy_sam": OWNER_SAM}, "тем же сотрудником"),
        ({"deputy_sam": OWNER_SAM.upper()}, "тем же сотрудником"),
        ({"deputy_sam": "  "}, "не может быть пустым"),
        ({"valid_from": VALID_TO, "valid_to": VALID_FROM}, "раньше начала"),
        ({"principal_sam": PRINCIPAL}, "указывать нельзя"),
    ],
)
def test_my_create_bad_data_422(client, owner_headers, substitutes, directory, overrides, needle):
    """Ошибки данных записи — 422 до похода в хранилище (правила те же, что у
    админской схемы; регистр логина не делает заместителя «другим сотрудником»)."""
    response = client.post("/substitutes/my", json=_my_body(**overrides), headers=owner_headers)
    assert response.status_code == 422, response.text
    assert needle in response.text
    assert substitutes.list_all() == []


def test_my_create_duplicate_pair_409(client, owner_headers, hr_headers, substitutes, directory):
    """Дубль пары «я + этот заместитель» — 409 с тем же текстом, что у админской
    ручки. Обратная пара (меня заместителем назначил кто-то ещё) не конфликт."""
    _my_create(client, owner_headers)
    duplicate = client.post("/substitutes/my", json=_my_body(), headers=owner_headers)
    assert duplicate.status_code == 409, duplicate.text
    assert "уже есть" in duplicate.json()["detail"]
    assert len(substitutes.list_all()) == 1
    reverse = client.post(
        "/substitutes/my", json=_my_body(deputy_sam=OWNER_SAM), headers=hr_headers
    )
    assert reverse.status_code == 201, reverse.text


def test_my_create_unknown_deputy_422(client, owner_headers, substitutes, directory):
    """Заместитель проверяется по локальному справочнику: неизвестный логин — 422.

    Иначе запись завёлась бы на несуществующую учётку и молча никогда не
    сработала бы: резолвер ищет по логину. В подсказках модалки выбор идёт из
    того же справочника, но через API можно прислать что угодно."""
    response = client.post(
        "/substitutes/my", json=_my_body(deputy_sam="net.v.spravke"), headers=owner_headers
    )
    assert response.status_code == 422, response.text
    assert "не найден в справочнике" in response.text
    assert substitutes.list_all() == []


def test_my_create_dismissed_deputy_422(client, owner_headers, substitutes, directory):
    """Уволенного заместителем не назначить: он скрыт в подборе и здесь — 422."""
    from datetime import date as _date

    directory.upsert_many(
        [
            {
                "enterprise": "zavod.vymyshlennyy",
                "base_code": "ZUP_VYMYSH",
                "tab_num": "В-0009",
                "fio": "Уволенный Вымышленный",
                "department": "Служба вымышленного учета",
                "position": "Вымышленная должность",
                "ref_key": None,
                "ad_sam": "uvolen.vymyshlennyy",
                "ad_status": "linked",
                "dismissal_date": _date.today(),
            }
        ]
    )
    response = client.post(
        "/substitutes/my",
        json=_my_body(deputy_sam="uvolen.vymyshlennyy"),
        headers=owner_headers,
    )
    assert response.status_code == 422, response.text
    assert substitutes.list_all() == []


def test_my_create_directory_down_503(client, owner_headers, substitutes, directory):
    """Справочник сотрудников недоступен — 503, запись не создаётся (fail-closed)."""
    app.dependency_overrides[get_employee_sync_store] = lambda: UnavailableDirectory()
    try:
        response = client.post("/substitutes/my", json=_my_body(), headers=owner_headers)
        assert response.status_code == 503, response.text
    finally:
        app.dependency_overrides.pop(get_employee_sync_store, None)
    assert substitutes.list_all() == []


def test_my_update_bad_data_and_taken_pair(client, owner_headers, substitutes, directory):
    """PUT своей записи проверяется теми же правилами: 422 по данным записи,
    409 если пару заняла другая его же запись; обе записи остаются прежними."""
    first = _my_create(client, owner_headers)["id"]
    second = _my_create(client, owner_headers, deputy_sam=SECOND_DEPUTY)["id"]
    bad = client.put(
        f"/substitutes/my/{first}", json=_my_body(deputy_sam=OWNER_SAM), headers=owner_headers
    )
    assert bad.status_code == 422, bad.text
    assert substitutes.by_id(first).deputy_sam == DEPUTY
    conflict = client.put(
        f"/substitutes/my/{first}", json=_my_body(deputy_sam=SECOND_DEPUTY), headers=owner_headers
    )
    assert conflict.status_code == 409, conflict.text
    assert substitutes.by_id(first).deputy_sam == DEPUTY
    assert substitutes.by_id(second).deputy_sam == SECOND_DEPUTY


def test_my_audit_details_carry_no_logins_or_fio(client, owner_headers, substitutes, directory):
    """Личная ручка аудитируется теми же событиями (actor — логин пользователя),
    но без логина заместителя и пояснения: состав пары — ПДн."""
    sub_id = _my_create(client, owner_headers, note="Вымышленная причина замещения")["id"]
    client.put(
        f"/substitutes/my/{sub_id}",
        json=_my_body(deputy_sam=SECOND_DEPUTY, note="Вымышленная причина замещения"),
        headers=owner_headers,
    )
    client.delete(f"/substitutes/my/{sub_id}", headers=owner_headers)
    events = [e for e in audit_log.all() if e.action.startswith("substitutes.")]
    assert [e.action for e in events] == [
        "substitutes.create",
        "substitutes.update",
        "substitutes.delete",
    ]
    for event in events:
        assert event.actor == OWNER_SAM, event
        assert event.entity == "substitute"
        assert event.entity_id == str(sub_id)
        assert DEPUTY not in event.detail, event
        assert SECOND_DEPUTY not in event.detail, event
        assert "Вымышленная" not in event.detail, event
    assert VALID_FROM in events[0].detail
    assert "письма заместителю: да" in events[0].detail
    assert events[-1].detail == "запись удалена"


def test_my_list_store_down_503(client, owner_headers, substitutes):
    """Чтение своих записей при недоступном хранилище — 503 с понятным текстом."""
    _unavailable()
    response = client.get("/substitutes/my", headers=owner_headers)
    assert response.status_code == 503
    assert "недоступно" in response.json()["detail"]


def test_my_create_store_down_503(client, owner_headers, substitutes, directory):
    """Создание при недоступном хранилище — 503 (аудит при этом не пишется)."""
    _unavailable()
    assert client.post("/substitutes/my", json=_my_body(), headers=owner_headers).status_code == 503
    assert not [e for e in audit_log.all() if e.action.startswith("substitutes.")]


def test_my_update_store_down_503(client, owner_headers, substitutes, directory):
    """Правка при недоступном хранилище — 503 (проверка принадлежности тоже)."""
    sub_id = _my_create(client, owner_headers)["id"]
    _unavailable()
    response = client.put(
        f"/substitutes/my/{sub_id}", json=_my_body(), headers=owner_headers
    )
    assert response.status_code == 503


def test_my_delete_store_down_503(client, owner_headers, substitutes, directory):
    """Удаление при недоступном хранилище — 503."""
    sub_id = _my_create(client, owner_headers)["id"]
    _unavailable()
    assert client.delete(f"/substitutes/my/{sub_id}", headers=owner_headers).status_code == 503


def test_my_no_auth_401(client, noauth_headers, substitutes):
    """Без сессии личные ручки закрыты (401) — как и любые другие входы в API."""
    assert client.get("/substitutes/my", headers=noauth_headers).status_code == 401
    assert (
        client.post("/substitutes/my", json=_my_body(), headers=noauth_headers).status_code == 401
    )
    assert (
        client.put(
            "/substitutes/my/1", json=_my_body(), headers=noauth_headers
        ).status_code
        == 401
    )
    assert client.delete("/substitutes/my/1", headers=noauth_headers).status_code == 401


# ---------------------------------------------------------------------------
# Хранилище: гонка в личном контуре (expect_principal) и чтение по principal
# ---------------------------------------------------------------------------


def _own_record(store, deputy_sam: str = DEPUTY):
    """Запись личного кабинета владельца шага — для проверок хранилища."""
    return store.create(
        principal_sam=OWNER_SAM,
        deputy_sam=deputy_sam,
        valid_from=date(2026, 10, 1),
        valid_to=None,
        notify=True,
        note="Вымышленная причина замещения",
        actor=OWNER_SAM,
    )


def test_store_update_and_delete_reject_foreign_principal(substitutes):
    """Гонка: между проверкой принадлежности и UPDATE/DELETE запись перестала быть
    своей — хранилище отвечает SubstituteNotFound (404) и ничего не меняет.

    Тот же контракт отдаёт БД (условие по principal_sam в самом UPDATE/DELETE)."""
    record = _own_record(substitutes)
    fields = {
        "principal_sam": OWNER_SAM,
        "deputy_sam": SECOND_DEPUTY,
        "valid_from": date(2026, 11, 1),
        "valid_to": None,
        "notify": False,
        "note": "Вымышленная новая причина",
    }
    with pytest.raises(SubstituteNotFound):
        substitutes.update(record.id, expect_principal=HR_SAM, **fields)
    with pytest.raises(SubstituteNotFound):
        substitutes.delete(record.id, expect_principal=HR_SAM)
    stored = substitutes.by_id(record.id)
    assert stored.deputy_sam == DEPUTY and stored.updated_at is None
    # Свой principal_sam — те же поля применяются.
    assert substitutes.update(record.id, expect_principal=OWNER_SAM, **fields).deputy_sam == (
        SECOND_DEPUTY
    )
    substitutes.delete(record.id, expect_principal=OWNER_SAM)
    assert substitutes.by_id(record.id) is None


def test_store_list_by_principal_reads_only_own(substitutes):
    """list_by_principal отдаёт записи одного логина (регистр не различает),
    чужих нет; порядок — тот же, что у всего справочника (по логинам)."""
    _own_record(substitutes)
    _own_record(substitutes, deputy_sam=SECOND_DEPUTY)
    assert [record.deputy_sam for record in substitutes.list_by_principal(OWNER_SAM)] == sorted(
        [DEPUTY, SECOND_DEPUTY]
    )
    assert substitutes.list_by_principal(OWNER_SAM.upper()) == substitutes.list_by_principal(
        OWNER_SAM
    )
    assert substitutes.list_by_principal(HR_SAM) == []


# ---------------------------------------------------------------------------
# Лимит числа заместителей: настройка substitute_deputies_limit (миграция 0025)
# ---------------------------------------------------------------------------


class SeedSettingsStore:
    """Хранилище настроек с заданными значениями (сид-формат строками)."""

    def __init__(self, values=None, broken=False):
        self.values = dict(values or {})
        self.broken = broken

    def _check(self):
        if self.broken:
            raise SettingsUnavailable("Хранилище настроек недоступно (тест)")

    def get(self, key):
        self._check()
        return self.values.get(key)

    def set(self, key, value):
        self._check()
        self.values[key] = value

    def get_many(self, keys):
        self._check()
        return {key: self.values.get(key) for key in keys}

    def set_many(self, values):
        self._check()
        self.values.update(values)


@pytest.fixture
def deputies_limit():
    """Хранилище настроек, значение лимита задаёт сам тест (пусто — «ключа нет»)."""
    store = SeedSettingsStore()
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


def _today() -> date:
    """Сегодня по часовой зоне системы — так же, как считает лимит."""
    return datetime.now(current_timezone()).date()


def _active_period() -> dict:
    """Срок записи, действующей сегодня (бессрочно с сегодняшнего числа)."""
    return {"valid_from": _today().isoformat(), "valid_to": None}


def _active_body(deputy_sam: str) -> dict:
    """Тело личной записи, действующей сегодня (заместитель + открытый срок)."""
    return _my_body(deputy_sam=deputy_sam, **_active_period())


def _admin_active_body(deputy_sam: str, principal_sam: str = PRINCIPAL) -> dict:
    """Тело админской записи, действующей сегодня."""
    return _body(principal_sam=principal_sam, deputy_sam=deputy_sam, **_active_period())


def test_limit_default_three_without_key(client, owner_headers, substitutes, directory):
    """Без ключа в настройках (офлайн-хранилище пусто) — дефолт 3: третьего
    заместителя завести можно, четвёртый — 422 с понятным текстом.

    Текст ответа содержит само значение лимита и имя настройки: сотрудник должен
    понять, что лимит настраивается, а не зашит."""
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())
    fourth = client.post(
        "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
    )
    assert fourth.status_code == 422, fourth.text
    assert "Лимит заместителей — 3" in fourth.json()["detail"]
    assert LIMIT_KEY in fourth.json()["detail"]
    assert len(substitutes.list_all()) == 3


def test_limit_taken_from_setting(client, owner_headers, substitutes, directory, deputies_limit):
    """Лимит — значение НАСТРОЙКИ, а не константа: при 1 второй заместитель 422."""
    deputies_limit.values[LIMIT_KEY] = "1"
    _my_create(client, owner_headers, **_active_period())
    second = client.post(
        "/substitutes/my", json=_active_body(SECOND_DEPUTY), headers=owner_headers
    )
    assert second.status_code == 422, second.text
    assert "Лимит заместителей — 1" in second.json()["detail"]
    assert LIMIT_KEY in second.json()["detail"]
    assert len(substitutes.list_all()) == 1


def test_limit_raised_by_setting_allows_fourth(
    client, owner_headers, substitutes, directory, deputies_limit
):
    """Подняли настройку до 4 — четвёртый заместитель заводится (значение из БД)."""
    deputies_limit.values[LIMIT_KEY] = "4"
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY, SIXTH_DEPUTY):
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())
    assert len(substitutes.list_all()) == 4


def test_limit_fallback_when_settings_store_down(
    client, owner_headers, substitutes, directory, deputies_limit
):
    """Хранилище настроек недоступно — запасной дефолт: третий создаётся, четвёртый 422.

    Проверка ограничения не должна ронять сохранение записи."""
    deputies_limit.broken = True
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())
    fourth = client.post(
        "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
    )
    assert fourth.status_code == 422, fourth.text
    assert "Лимит заместителей — 3" in fourth.json()["detail"]


def test_limit_fallback_on_dirty_setting(
    client, owner_headers, substitutes, directory, deputies_limit
):
    """Битое или неположительное значение настройки — запасной дефолт 3.

    Через API такое значение в БД не попадёт (валидатор настроек отвергает 0 и
    мусор), но при ручной правке БД ограничение продолжает работать: третий
    создаётся, четвёртый 422."""
    for value in ("не число", "0", "-1"):
        deputies_limit.values[LIMIT_KEY] = value
        for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
            response = client.post(
                "/substitutes/my",
                json=_active_body(deputy),
                headers=owner_headers,
            )
            assert response.status_code == 201, (value, response.text)
        fourth = client.post(
            "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
        )
        assert fourth.status_code == 422, (value, fourth.text)
        assert "Лимит заместителей — 3" in fourth.json()["detail"]
        for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
            stored = next(
                item
                for item in substitutes.list_all()
                if item.deputy_sam == deputy
            )
            client.delete(f"/substitutes/my/{stored.id}", headers=owner_headers)


def test_limit_counts_record_ending_today(client, owner_headers, substitutes, directory):
    """Запись, заканчивающаяся СЕГОДНЯ, место в лимите занимает.

    Граница интервала: `valid_to` — последний день действия, а не день после
    него. Ошибка на символе тут сделала бы «вечные» записи (valid_to = сегодня),
    которые никогда не освобождали бы место."""
    today = _today()
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY):
        response = client.post(
            "/substitutes/my",
            json=_my_body(
                deputy_sam=deputy,
                valid_from=today.isoformat(),
                valid_to=today.isoformat(),
            ),
            headers=owner_headers,
        )
        assert response.status_code == 201, response.text
    started_today = client.post(
        "/substitutes/my",
        json=_my_body(deputy_sam=FIFTH_DEPUTY, valid_from=today.isoformat()),
        headers=owner_headers,
    )
    assert started_today.status_code == 201, started_today.text
    fourth = client.post(
        "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
    )
    assert fourth.status_code == 422, fourth.text


def test_limit_counts_only_records_active_today(client, owner_headers, substitutes, directory):
    """Законченная и ещё не начатая записи место не занимают: при трёх действующих
    они заводятся, а четвёртая действующая — уже 422.

    Иначе истёкшие записи (их нельзя удалить — они нужны для истории) заблокировали
    бы новые навсегда."""
    today = _today()
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())
    past = client.post(
        "/substitutes/my",
        json=_my_body(
            deputy_sam=DEPUTY,
            valid_from=(today - timedelta(days=30)).isoformat(),
            valid_to=(today - timedelta(days=1)).isoformat(),
        ),
        headers=owner_headers,
    )
    assert past.status_code == 201, past.text
    future = client.post(
        "/substitutes/my",
        json=_my_body(
            deputy_sam=SECOND_DEPUTY,
            valid_from=(today + timedelta(days=30)).isoformat(),
            valid_to=None,
        ),
        headers=owner_headers,
    )
    assert future.status_code == 201, future.text
    fourth_active = client.post(
        "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
    )
    assert fourth_active.status_code == 422, fourth_active.text
    assert len(substitutes.list_all()) == 5


def test_limit_not_applied_to_other_employee(
    client, owner_headers, hr_headers, substitutes, directory
):
    """Лимит свой у каждого сотрудника: чужой счётчик не мешает (справочник общий)."""
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())
    blocked = client.post(
        "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
    )
    assert blocked.status_code == 422, blocked.text
    # У сотрудника ОК свой счётчик: его первая запись проходит.
    first = client.post(
        "/substitutes/my", json=_active_body(FOURTH_DEPUTY), headers=hr_headers
    )
    assert first.status_code == 201, first.text
    assert first.json()["principal_sam"] == HR_SAM


def test_update_does_not_count_edited_record(client, owner_headers, substitutes, directory):
    """Правка записи не вылетает за лимит: правимая запись в счёт не входит.

    Иначе при лимите 3 сотрудник не смог бы ни продлить срок, ни сменить
    заместителя — а запись всё равно одна и та же."""
    ids = [
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())["id"]
        for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY)
    ]
    edited = client.put(
        f"/substitutes/my/{ids[-1]}",
        json=_active_body(SIXTH_DEPUTY),
        headers=owner_headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["deputy_sam"] == SIXTH_DEPUTY
    assert len(substitutes.list_all()) == 3
    # Лимит при этом не сдвинулся: лишняя запись по-прежнему не заводится.
    blocked = client.post(
        "/substitutes/my", json=_active_body(DEPUTY), headers=owner_headers
    )
    assert blocked.status_code == 422, blocked.text


def test_limit_checked_before_write(client, owner_headers, substitutes, directory):
    """Превышение лимита — 422 до похода в хранилище: запись не появляется,
    аудита substitutes.create не пишется."""
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
        _my_create(client, owner_headers, deputy_sam=deputy, **_active_period())
    creates_before = len([e for e in audit_log.all() if e.action == "substitutes.create"])
    response = client.post(
        "/substitutes/my", json=_active_body(SIXTH_DEPUTY), headers=owner_headers
    )
    assert response.status_code == 422, response.text
    assert SIXTH_DEPUTY not in [record.deputy_sam for record in substitutes.list_all()]
    assert len([e for e in audit_log.all() if e.action == "substitutes.create"]) == (
        creates_before
    )


def test_admin_routes_respect_the_same_limit(client, admin_headers, substitutes):
    """Справочник один: админские ручки /settings/substitutes ограничение соблюдают
    те же, а правимая запись в счёт не входит (её можно поменять при полном лимите)."""
    ids = [
        _seed(client, admin_headers, deputy_sam=deputy, **_active_period())
        for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY)
    ]
    blocked = _create(
        client, admin_headers, deputy_sam=SIXTH_DEPUTY, **_active_period()
    )
    assert blocked.status_code == 422, blocked.text
    assert "Лимит заместителей — 3" in blocked.json()["detail"]
    assert LIMIT_KEY in blocked.json()["detail"]
    edited = client.put(
        f"/settings/substitutes/{ids[-1]}",
        json=_admin_active_body(SIXTH_DEPUTY),
        headers=admin_headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["deputy_sam"] == SIXTH_DEPUTY
    assert len(substitutes.list_all()) == 3


def test_admin_update_checks_limit_of_target_employee(client, admin_headers, substitutes):
    """Перенос записи на сотрудника, у которого лимит исчерпан, — 422 и запись
    остаётся у прежнего сотрудника."""
    for deputy in (THIRD_DEPUTY, FOURTH_DEPUTY, FIFTH_DEPUTY):
        _seed(client, admin_headers, deputy_sam=deputy, **_active_period())
    moving = _seed(client, admin_headers, principal_sam=HR_SAM, **_active_period())
    response = client.put(
        f"/settings/substitutes/{moving}",
        json=_admin_active_body(SIXTH_DEPUTY, principal_sam=PRINCIPAL),
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert substitutes.by_id(moving).principal_sam == HR_SAM

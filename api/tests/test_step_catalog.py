# Справочник шагов step_catalog (миграция 0018): переиспользуемые заготовки,
# из которых бланк набирается копией в blank_steps. Хранилище
# (DbRoutingStore.list_catalog_steps/create_catalog_step/update_catalog_step/
# delete_catalog_step) и админ-эндпоинты GET/POST/PUT/DELETE
# /settings/routing/step-catalog. Живой Postgres не нужен: сессия хранилища
# подменяется моком (как в test_routing_store.py), эндпоинты — in-memory
# заглушкой справочника (как в test_blanks_catalog.py). Все коды, названия и
# логины — вымышленные.
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.routing_store import DbRoutingStore, get_routing_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402

# --- Вымышленные данные справочника ---
STEP_ID = 50
STEP_CODE = "soglasovatel_buh"
STEP_TITLE = "Согласовать бухгалтерией"
STEP_GROUP = "SED_STEP_BUH"
PERSON_1 = "soglasovatel.pervyy"
PERSON_2 = "soglasovatel.vtoroy"

CATALOG_STEP_ROW = {
    "id": STEP_ID,
    "code": STEP_CODE,
    "title": STEP_TITLE,
    "stage_lines": ["проверить расчёт"],
    "executor_kind": "people",
    "assignees": [PERSON_1],
    "owner_group": None,
    "approval_mode": "sequential",
    "optional": False,
    "require_comment": False,
    "active": True,
    "updated_by": "adm.petrov",
    "updated_at": None,
}


# ================= Хранилище: сессия-мок =================


class FakeRow:
    """Строка ответа сессии: доступ по имени, по номеру и через _mapping."""

    def __init__(self, values: dict) -> None:
        self._values = dict(values)
        self._mapping = dict(self._values)
        for key, value in self._values.items():
            setattr(self, key, value)

    def __getitem__(self, index):
        if isinstance(index, int):
            return list(self._values.values())[index]
        return self._values[index]


class FakeResult:
    """Ответ сессии: заранее заданные строки (dict -> FakeRow)."""

    def __init__(self, rows=None) -> None:
        self._rows = rows
        self.rowcount = 0

    def _as_rows(self) -> list:
        if self._rows is None:
            return []
        if isinstance(self._rows, list):
            return self._rows
        return [self._rows]

    def all(self) -> list:
        return [FakeRow(row) if isinstance(row, dict) else row for row in self._as_rows()]

    def first(self):
        rows = self.all()
        return rows[0] if rows else None


class FakeSession:
    """Сессия-мок: ответы выдаются по порядку, SQL/параметры/commit запоминаются."""

    def __init__(self, results=None) -> None:
        self._results = list(results or [])
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        if not self._results:
            result = FakeResult(None)
            result.rowcount = 0
            return result
        result = FakeResult(self._results.pop(0))
        result.rowcount = 1
        return result

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


def _store(session: FakeSession) -> DbRoutingStore:
    """DbRoutingStore с подменённой сессией (движок не создаётся)."""
    store = object.__new__(DbRoutingStore)
    store._engine = None
    store._session_factory = lambda: session
    return store


def _sqls(session: FakeSession) -> list[str]:
    return [sql for sql, _ in session.calls]


def _params_with(session: FakeSession, needle: str) -> list[dict]:
    return [params for sql, params in session.calls if needle in sql]


def _audit_call(session: FakeSession) -> dict:
    """Параметры INSERT в audit_log (последний такой вызов)."""
    audits = _params_with(session, "INSERT INTO audit_log")
    assert audits, "аудит не записан"
    return audits[-1]


# ================= Хранилище: шаги справочника =================


def test_list_catalog_steps_returns_dicts_with_active_only():
    """Список шагов — словари, jsonb-колонки списками, флаг active_only уходит в БД."""
    session = FakeSession([[CATALOG_STEP_ROW]])
    items = _store(session).list_catalog_steps(active_only=True)
    assert items[0]["code"] == STEP_CODE
    assert items[0]["stage_lines"] == ["проверить расчёт"]
    assert items[0]["assignees"] == [PERSON_1]
    assert items[0]["active"] is True
    sql = _sqls(session)[0]
    assert "FROM step_catalog" in sql
    assert session.calls[0][1] == {"active_only": True}
    assert session.commits == 0


def test_create_catalog_step_returns_id_and_audits():
    """Создание шага: RETURNING id + аудит step_catalog.create без ПДн."""
    session = FakeSession([FakeRow({"id": 51})])
    step_id = _store(session).create_catalog_step(
        {
            "code": "soglasovatel_kadry",
            "title": "Согласовать кадрами",
            "stage_lines": ["оформить прекращение"],
            "executor_kind": "people",
            "assignees": [PERSON_2],
            "owner_group": None,
            "approval_mode": "parallel",
            "optional": True,
            "require_comment": False,
            "active": True,
            "actor": "adm.petrov",
        }
    )
    assert step_id == 51
    sql, params = session.calls[0]
    assert "INSERT INTO step_catalog" in sql
    assert params["code"] == "soglasovatel_kadry"
    assert params["title"] == "Согласовать кадрами"
    assert params["executor_kind"] == "people"
    assert params["approval_mode"] == "parallel"
    assert json.loads(params["assignees"]) == [PERSON_2]
    assert params["optional"] is True and params["active"] is True
    assert params["actor"] == "adm.petrov"
    audit = _audit_call(session)
    assert audit["action"] == "step_catalog.create" and audit["entity"] == "step_catalog"
    assert audit["entity_id"] == "51" and audit["actor"] == "adm.petrov"
    assert PERSON_2 not in audit["details"]
    assert session.commits == 1


def test_update_catalog_step_whitelist_and_audit():
    """Правка шага: в SET только поля из белого списка, неизвестные игнорируются."""
    session = FakeSession([])
    _store(session).update_catalog_step(
        STEP_ID,
        {"title": "Уточнённое название", "drop_table": "x", "actor": "adm.petrov"},
    )
    sql, params = session.calls[0]
    assert sql.startswith("UPDATE step_catalog SET")
    assert "title = :title" in sql
    assert "drop_table" not in sql and "drop_table" not in params
    assert params["id"] == STEP_ID and params["actor"] == "adm.petrov"
    assert _audit_call(session)["action"] == "step_catalog.update"
    assert session.commits == 1


def test_update_catalog_step_empty_payload_is_noop():
    """Пустой payload — ни запроса, ни commit (нечего менять)."""
    session = FakeSession([])
    _store(session).update_catalog_step(STEP_ID, {"actor": "adm.petrov"})
    assert session.calls == [] and session.commits == 0


def test_delete_catalog_step_returns_rowcount_and_audits():
    """Удаление: True с аудитом, False без аудита, если строки не было."""
    session = FakeSession([FakeRow({"id": STEP_ID})])
    assert _store(session).delete_catalog_step(STEP_ID, "adm.petrov") is True
    assert _params_with(session, "DELETE FROM step_catalog") == [{"id": STEP_ID}]
    assert _audit_call(session)["action"] == "step_catalog.delete"
    assert session.commits == 1

    session = FakeSession([])
    assert _store(session).delete_catalog_step(999, "adm.petrov") is False
    assert _params_with(session, "INSERT INTO audit_log") == []
    assert session.commits == 1


# ================= Эндпоинты: in-memory заглушка справочника =================


class FakeStepCatalogStore:
    """Справочник шагов в памяти: контракт DbRoutingStore на чтение и записи."""

    def __init__(self, steps=None) -> None:
        self.steps: list[dict] = [dict(CATALOG_STEP_ROW)] if steps is None else steps
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def list_catalog_steps(self, active_only: bool = False) -> list[dict]:
        self._count("list_catalog_steps")
        return [dict(item) for item in self.steps]

    def create_catalog_step(self, data: dict) -> int:
        self._count("create_catalog_step")
        step_id = max([item["id"] for item in self.steps] or [0]) + 1
        self.steps.append(
            {
                **data,
                "id": step_id,
                "updated_by": data.get("actor"),
                "updated_at": None,
            }
        )
        return step_id

    def update_catalog_step(self, step_id: int, data: dict) -> None:
        self._count("update_catalog_step")
        for item in self.steps:
            if item["id"] == step_id:
                item.update({key: value for key, value in data.items() if key != "actor"})
                return

    def delete_catalog_step(self, step_id: int, actor: str) -> bool:
        self._count("delete_catalog_step")
        for index, item in enumerate(self.steps):
            if item["id"] == step_id:
                self.steps.pop(index)
                return True
        return False


@pytest.fixture
def settings_override():
    """Группы ролей тестовыми (дефолты кода нейтральные): SED_ADMINS — админ."""
    settings = Settings(ALLOWED_AD_GROUPS="SED_HR,SED_ADMINS", ADMIN_GROUPS="SED_ADMINS")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def step_catalog_store():
    """Справочник шагов через зависимость (тест может заменить содержимое)."""
    store = FakeStepCatalogStore()
    app.dependency_overrides[get_routing_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_routing_store, None)


class SeededSettingsStore:
    """Настройки в памяти; значения — строками в сид-формате (JSONB)."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_many(self, keys) -> dict[str, str | None]:
        return {key: self.values.get(key) for key in keys}

    def set_many(self, values) -> None:
        self.values.update(values)


@pytest.fixture
def groups_store():
    """Справочник групп шагов: группа шага ad_group обязана быть в нём."""
    store = SeededSettingsStore(
        {"allowed_ad_groups": json.dumps([STEP_GROUP, "SED_STEP_OTHER"])}
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture(autouse=True)
def clean_state():
    """Чистый журнал аудита на каждый тест."""
    audit_log.clear_for_tests()
    yield
    audit_log.clear_for_tests()


def _step_body(**kw) -> dict:
    body = {"code": "soglasovatel_kadry", "title": "Согласовать кадрами"}
    body.update(kw)
    return body


def _ad_group_step(**kw) -> dict:
    """Тело шага с группой AD (исполнитель ad_group)."""
    step = {
        "code": "soglasovatel_buh_gruppoy",
        "title": "Согласовать бухгалтерией",
        "executor_kind": "ad_group",
        "owner_group": STEP_GROUP,
        "stage_lines": ["проверить расчёт"],
    }
    step.update(kw)
    return step


# --- Эндпоинты шагов справочника ---


def test_catalog_step_crud_cycle(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """CRUD шага справочника: создание (201), чтение, правка, удаление (200)."""
    created = client.post(
        "/settings/routing/step-catalog",
        json=_step_body(stage_lines=["проверить расчёт"], assignees=[PERSON_1]),
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    step_id = created.json()["id"]

    listed = client.get("/settings/routing/step-catalog", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert step_id in [item["id"] for item in listed.json()]
    assert [item["code"] for item in listed.json()] == [STEP_CODE, "soglasovatel_kadry"]

    updated = client.put(
        f"/settings/routing/step-catalog/{step_id}",
        json={"title": "Уточнённое название"},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated"] == "title"
    stored = next(item for item in step_catalog_store.steps if item["id"] == step_id)
    assert stored["title"] == "Уточнённое название"

    deleted = client.delete(
        f"/settings/routing/step-catalog/{step_id}", headers=admin_headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": step_id, "deleted": True}
    assert step_id not in [item["id"] for item in step_catalog_store.steps]


def test_catalog_step_duplicate_code_409(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """Занятый код шага — 409 (проверка до INSERT, как у бланков и этапов)."""
    duplicate = client.post(
        "/settings/routing/step-catalog",
        json=_step_body(code=STEP_CODE, assignees=[PERSON_1]),
        headers=admin_headers,
    )
    assert duplicate.status_code == 409, duplicate.text
    assert "уже существует" in duplicate.text
    assert "create_catalog_step" not in step_catalog_store.calls


def test_catalog_step_people_without_assignees_422(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """Шаг people без согласующих — 422 на границе, справочник не меняется."""
    response = client.post(
        "/settings/routing/step-catalog",
        json=_step_body(executor_kind="people", assignees=[]),
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "assignees" in response.text
    assert "create_catalog_step" not in step_catalog_store.calls


def test_catalog_step_ad_group_without_group_422(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """Шаг ad_group без группы — 422 на границе, справочник не меняется."""
    response = client.post(
        "/settings/routing/step-catalog",
        json=_step_body(executor_kind="ad_group", owner_group=None),
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "owner_group" in response.text
    assert "create_catalog_step" not in step_catalog_store.calls


def test_catalog_step_unknown_ad_group_422(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """Группа шага вне справочника групп шагов — 422, справочник не меняется."""
    response = client.post(
        "/settings/routing/step-catalog",
        json=_ad_group_step(owner_group="SED_STEP_NET"),
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "SED_STEP_NET" in response.text
    assert "create_catalog_step" not in step_catalog_store.calls


def test_catalog_step_empty_title_422(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """Пустое название шага — 422 на границе, справочник не меняется."""
    response = client.post(
        "/settings/routing/step-catalog",
        json=_step_body(title="   "),
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "title" in response.text
    assert "create_catalog_step" not in step_catalog_store.calls


def test_catalog_step_delete_missing_404(
    client, admin_headers, settings_override, step_catalog_store, groups_store
):
    """Удаление несуществующего шага — 404."""
    response = client.delete(
        "/settings/routing/step-catalog/999999", headers=admin_headers
    )
    assert response.status_code == 404, response.text
    assert "не найден" in response.text


def test_catalog_step_requires_admin(
    client, hr_headers, settings_override, step_catalog_store, groups_store
):
    """Ручки справочника — только admin: ОК получает 403 на чтение и запись."""
    assert client.get("/settings/routing/step-catalog", headers=hr_headers).status_code == 403
    assert client.post(
        "/settings/routing/step-catalog", json=_step_body(assignees=[PERSON_1]), headers=hr_headers
    ).status_code == 403
    assert client.put(
        f"/settings/routing/step-catalog/{STEP_ID}",
        json={"title": "Другое"},
        headers=hr_headers,
    ).status_code == 403
    assert client.delete(
        f"/settings/routing/step-catalog/{STEP_ID}", headers=hr_headers
    ).status_code == 403
    assert "create_catalog_step" not in step_catalog_store.calls
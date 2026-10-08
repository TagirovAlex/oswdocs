# Справочник бланков (миграция 0012: blanks/blank_steps): хранилище
# (DbRoutingStore.list_blanks/create_blank/update_blank/list_blank_steps/
# set_blank_steps) и админ-эндпоинты GET/POST /settings/routing/blanks,
# PUT /settings/routing/blanks/{id}[/steps], секция blanks в
# GET /settings/routing/catalogs. Живой Postgres не нужен: сессия хранилища
# подменяется моком (как в test_routing_store.py), эндпоинты — in-memory
# заглушкой справочника (как в test_routing_preview.py). Все коды, названия
# и логины — вымышленные.
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.routing_store import DbRoutingStore, get_routing_store  # noqa: E402

# --- Вымышленные данные справочника ---
BLANK_ID = 30
BLANK_CODE = "uvolnenie"
BLANK_NAME = "Вымышленный бланк увольнения"
STAGE_BUH = 21
STAGE_BOSS = 22
STAGE_HR = 23

BLANK_ROW = {
    "id": BLANK_ID,
    "code": BLANK_CODE,
    "name": BLANK_NAME,
    "doc_type_code": "uvolnenie_doc",
    "description": "Вымышленное описание бланка",
    "layout": "office",
    "active": True,
    "version": 1,
    "updated_at": None,
    "updated_by": None,
    "step_count": 2,
}
BLANK_STEP_ROW = {
    "blank_id": BLANK_ID,
    "stage_id": STAGE_BUH,
    "step_order": 1,
    "optional_override": None,
    "require_comment_override": None,
    "stage_code": "buh",
    "title": "Вымышленная бухгалтерия",
    "stage_lines": '["согласовать выплаты", "проверить расчёт"]',
    "owner_kind": "ad_group",
    "owner_group": "SED_STEP_BUH",
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "stage_active": True,
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

    def scalar(self):
        row = self.first()
        return None if row is None else row[0]


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
            return FakeResult(None)
        return FakeResult(self._results.pop(0))

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


# ================= Хранилище: бланки =================


def test_list_blanks_returns_dicts_with_step_count():
    """Список бланков — словари со счётчиком шагов, флаг active_only уходит в БД."""
    session = FakeSession([[BLANK_ROW]])
    items = _store(session).list_blanks(active_only=True)
    assert items[0]["code"] == BLANK_CODE
    assert items[0]["step_count"] == 2
    sql = _sqls(session)[0]
    assert "FROM blanks" in sql and "step_count" in sql
    assert "JOIN blank_steps" not in sql
    assert session.calls[0][1] == {"active_only": True}
    assert session.commits == 0


def test_blank_by_id_and_missing_blank():
    """Бланк по id; отсутствующий — None (без 500)."""
    session = FakeSession([BLANK_ROW])
    assert _store(session).blank_by_id(BLANK_ID)["layout"] == "office"
    assert "WHERE id = :blank_id" in _sqls(session)[0]
    assert session.calls[0][1] == {"blank_id": BLANK_ID}

    session = FakeSession([])
    assert _store(session).blank_by_id(999) is None


def test_create_blank_returns_id_and_audits():
    """Создание бланка: RETURNING id + аудит blank.create без ПДн."""
    session = FakeSession([FakeRow({"id": 31})])
    blank_id = _store(session).create_blank(
        {
            "code": BLANK_CODE,
            "name": BLANK_NAME,
            "doc_type_code": "uvolnenie_doc",
            "description": None,
            "layout": "line",
            "active": False,
            "actor": "adm.petrov",
        }
    )
    assert blank_id == 31
    sql, params = session.calls[0]
    assert "INSERT INTO blanks" in sql
    assert params["code"] == BLANK_CODE and params["doc_type_code"] == "uvolnenie_doc"
    assert params["layout"] == "line" and params["active"] is False
    assert params["actor"] == "adm.petrov"
    audit = _audit_call(session)
    assert audit["action"] == "blank.create" and audit["entity"] == "blank"
    assert audit["entity_id"] == "31" and audit["actor"] == "adm.petrov"
    assert "adm.petrov" not in audit["details"]
    assert session.commits == 1


def test_create_blank_layout_default_in_sql():
    """Пустой макет — пресет office из SQL (COALESCE), не из кода роутера."""
    session = FakeSession([FakeRow({"id": 31})])
    _store(session).create_blank({"code": BLANK_CODE, "name": BLANK_NAME, "actor": ""})
    sql, params = session.calls[0]
    assert "COALESCE(:layout, 'office')" in sql
    assert params["layout"] is None


def test_update_blank_whitelist_and_audit():
    """Правка бланка: в SET только поля из белого списка, неизвестные игнорируются."""
    session = FakeSession([])
    _store(session).update_blank(
        BLANK_ID,
        {"name": "Уточнённое название", "drop_table": "x", "actor": "adm.petrov"},
    )
    sql, params = session.calls[0]
    assert sql.startswith("UPDATE blanks SET")
    assert "name = :name" in sql
    assert "drop_table" not in sql and "drop_table" not in params
    assert params["id"] == BLANK_ID and params["actor"] == "adm.petrov"
    assert _audit_call(session)["action"] == "blank.update"
    assert session.commits == 1


def test_update_blank_empty_payload_is_noop():
    """Пустой payload — ни запроса, ни commit (нечего менять)."""
    session = FakeSession([])
    _store(session).update_blank(BLANK_ID, {"actor": "adm.petrov"})
    assert session.calls == [] and session.commits == 0


def test_list_blank_steps_joins_stage_and_parses_lines():
    """Шаги бланка идут с join этапа; stage_lines из jsonb разбирается в список."""
    session = FakeSession([[BLANK_STEP_ROW]])
    items = _store(session).list_blank_steps(BLANK_ID)
    assert items[0]["stage_id"] == STAGE_BUH
    assert items[0]["title"] == "Вымышленная бухгалтерия"
    assert items[0]["stage_lines"] == ["согласовать выплаты", "проверить расчёт"]
    assert items[0]["optional_override"] is None
    assert "JOIN approval_stages" in _sqls(session)[0]
    assert session.calls[0][1] == {"blank_id": BLANK_ID}


def test_set_blank_steps_replaces_composition_and_bumps_version():
    """Шаги бланка заменяются целиком, версия растёт: DELETE + INSERT + UPDATE."""
    session = FakeSession([[FakeRow({"id": STAGE_BOSS}), FakeRow({"id": STAGE_BUH})]])
    _store(session).set_blank_steps(
        BLANK_ID,
        [
            {"stage_id": STAGE_BUH, "step_order": 2, "optional_override": False},
            {"stage_id": STAGE_BOSS, "step_order": 1, "require_comment_override": True},
        ],
        "adm.petrov",
    )
    assert _params_with(session, "DELETE FROM blank_steps") == [{"blank_id": BLANK_ID}]
    inserts = _params_with(session, "INSERT INTO blank_steps")
    assert [(item["stage_id"], item["step_order"]) for item in inserts] == [
        (STAGE_BOSS, 1),
        (STAGE_BUH, 2),
    ]
    assert all(item["blank_id"] == BLANK_ID for item in inserts)
    assert inserts[0]["require_comment_override"] is True
    assert inserts[1]["optional_override"] is False
    bumps = _params_with(session, "UPDATE blanks SET version = version + 1")
    assert bumps == [{"blank_id": BLANK_ID, "actor": "adm.petrov"}]
    audit = _audit_call(session)
    assert audit["action"] == "blank.steps.update" and audit["entity"] == "blank"
    assert audit["entity_id"] == str(BLANK_ID) and audit["actor"] == "adm.petrov"
    details = json.loads(audit["details"])
    assert details["steps"] == 2 and details["stage_ids"] == [STAGE_BUH, STAGE_BOSS]
    assert session.commits == 1


def test_set_blank_steps_unknown_stage_422():
    """Неизвестный/отключённый этап — 422 до DELETE (состав и версия целы)."""
    session = FakeSession([[FakeRow({"id": STAGE_BUH})]])
    with pytest.raises(HTTPException) as err:
        _store(session).set_blank_steps(
            BLANK_ID,
            [{"stage_id": STAGE_BUH, "step_order": 1}, {"stage_id": 99, "step_order": 2}],
            "adm.petrov",
        )
    assert err.value.status_code == 422
    assert "99" in str(err.value.detail)
    joined = " ".join(_sqls(session))
    assert "DELETE FROM blank_steps" not in joined
    assert "UPDATE blanks SET version" not in joined
    assert session.commits == 0


def test_set_blank_steps_skips_broken_items_and_bumps_version():
    """Битые элементы пропускаются; пустой состав — замена на пустой + версия."""
    session = FakeSession([[]])
    _store(session).set_blank_steps(
        BLANK_ID, [None, {}, {"stage_id": None, "step_order": 1}], ""
    )
    assert _params_with(session, "INSERT INTO blank_steps") == []
    assert _params_with(session, "DELETE FROM blank_steps")
    assert _params_with(session, "UPDATE blanks SET version = version + 1")
    details = json.loads(_audit_call(session)["details"])
    assert details["steps"] == 0 and details["stage_ids"] == []
    assert session.commits == 1


# ================= Эндпоинты: in-memory заглушка справочника =================


class FakeBlankStore:
    """Справочник бланков в памяти: контракт DbRoutingStore на чтение и записи."""

    def __init__(self, blanks=None, steps=None) -> None:
        self.blanks: list[dict] = [dict(BLANK_ROW)] if blanks is None else blanks
        self.steps: list[dict] = (
            [
                {
                    "blank_id": BLANK_ID,
                    "stage_id": STAGE_BUH,
                    "step_order": 1,
                    "optional_override": None,
                    "require_comment_override": None,
                    "title": "Вымышленная бухгалтерия",
                    "stage_lines": ["согласовать выплаты"],
                    "owner_kind": "ad_group",
                    "owner_group": "SED_STEP_BUH",
                    "optional": False,
                    "print_assignee": True,
                    "require_comment": False,
                    "stage_code": "buh",
                    "stage_active": True,
                }
            ]
            if steps is None
            else steps
        )
        self.calls: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    # --- чтение ---
    def list_services(self, active_only: bool = False) -> list[dict]:
        return []

    def list_profiles(self, active_only: bool = False) -> list[dict]:
        return []

    def list_stages(self, active_only: bool = False) -> list[dict]:
        return []

    def list_profile_steps(self, profile_id: int) -> list[dict]:
        return []

    def list_all_profile_steps(self) -> list[dict]:
        return []

    def list_stage_assignees(self, stage_id: int, active_only: bool = True) -> list[dict]:
        return []

    def list_blanks(self, active_only: bool = False) -> list[dict]:
        self._count("list_blanks")
        return [dict(item) for item in self.blanks]

    def list_blank_steps(self, blank_id: int) -> list[dict]:
        self._count("list_blank_steps")
        return [dict(item) for item in self.steps if item["blank_id"] == blank_id]

    # --- запись (аудит в боевом хранилище) ---
    def create_blank(self, data: dict) -> int:
        self._count("create_blank")
        blank_id = max([item["id"] for item in self.blanks] or [0]) + 1
        self.blanks.append(
            {
                **data,
                "id": blank_id,
                "version": 1,
                "updated_at": None,
                "updated_by": data.get("actor"),
                "step_count": 0,
            }
        )
        return blank_id

    def update_blank(self, blank_id: int, data: dict) -> None:
        self._count("update_blank")
        for item in self.blanks:
            if item["id"] == blank_id:
                item.update({key: value for key, value in data.items() if key != "actor"})
                return

    def set_blank_steps(self, blank_id: int, items: list[dict], actor: str) -> None:
        self._count("set_blank_steps")
        self.steps = [
            {"blank_id": blank_id, "optional_override": None,
             "require_comment_override": None, **item}
            for item in items
        ]


@pytest.fixture
def settings_override():
    """Группы ролей тестовыми (дефолты кода нейтральные): SED_ADMINS — админ."""
    settings = Settings(ALLOWED_AD_GROUPS="SED_HR,SED_ADMINS", ADMIN_GROUPS="SED_ADMINS")
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def blanks_store():
    """Справочник бланков через зависимость (тест может заменить содержимое)."""
    store = FakeBlankStore()
    app.dependency_overrides[get_routing_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_routing_store, None)


@pytest.fixture(autouse=True)
def clean_state():
    """Чистый журнал аудита на каждый тест."""
    audit_log.clear_for_tests()
    yield
    audit_log.clear_for_tests()


def _blank_body(**kw) -> dict:
    body = {"code": "predpravka", "name": "Вымышленный бланк удержания"}
    body.update(kw)
    return body


# --- Эндпоинты бланков ---


def test_admin_blanks_crud_cycle(client, admin_headers, settings_override, blanks_store):
    """CRUD бланка: создание (201), правка (частичная), чтение состава шагов."""
    created = client.post("/settings/routing/blanks", json=_blank_body(), headers=admin_headers)
    assert created.status_code == 201, created.text
    blank_id = created.json()["id"]

    listed = client.get("/settings/routing/blanks", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert blank_id in [item["id"] for item in listed.json()]

    updated = client.put(
        f"/settings/routing/blanks/{blank_id}",
        json={"layout": "line", "description": "Вымышленное уточнение"},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated"] == "description,layout"
    stored = next(item for item in blanks_store.blanks if item["id"] == blank_id)
    assert stored["layout"] == "line" and stored["description"] == "Вымышленное уточнение"

    replaced = client.put(
        f"/settings/routing/blanks/{blank_id}/steps",
        json={"steps": [{"stage_id": STAGE_BOSS, "step_order": 1,
                         "optional_override": True}]},
        headers=admin_headers,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json() == {"blank_id": blank_id, "count": 1}

    steps = client.get(f"/settings/routing/blanks/{blank_id}/steps", headers=admin_headers)
    assert steps.status_code == 200, steps.text
    item = steps.json()[0]
    assert item["stage_id"] == STAGE_BOSS and item["optional_override"] is True


def test_blank_steps_read_joins_stage(client, admin_headers, settings_override, blanks_store):
    """Состав бланка отдаётся с этапом: текст этапа и вид исполнителя на месте."""
    response = client.get(f"/settings/routing/blanks/{BLANK_ID}/steps", headers=admin_headers)
    assert response.status_code == 200, response.text
    item = response.json()[0]
    assert item["title"] == "Вымышленная бухгалтерия"
    assert item["stage_lines"] == ["согласовать выплаты"]
    assert item["owner_kind"] == "ad_group" and item["print_assignee"] is True


def test_create_blank_duplicate_code_409(
    client, admin_headers, settings_override, blanks_store
):
    """Занятый код бланка — 409 (проверка до INSERT, как у профилей и этапов)."""
    duplicate = client.post(
        "/settings/routing/blanks", json=_blank_body(code=BLANK_CODE), headers=admin_headers
    )
    assert duplicate.status_code == 409, duplicate.text
    assert "уже существует" in duplicate.text
    assert "create_blank" not in blanks_store.calls


def test_replace_blank_steps_unknown_stage_422(
    client, admin_headers, settings_override, blanks_store
):
    """Неизвестный этап в составе — 422 от хранилища (текст с перечнем этапов)."""

    def _reject(blank_id, items, actor):
        raise HTTPException(status_code=422, detail="Шаги бланка: этапы не найдены или отключены: 99")

    blanks_store.set_blank_steps = _reject
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [{"stage_id": 99, "step_order": 1}]},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "99" in response.text


def test_replace_blank_steps_duplicate_order_422(
    client, admin_headers, settings_override, blanks_store
):
    """Дубль порядка шага — 422 на границе (в БД PK (blank_id, step_order))."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [{"stage_id": STAGE_BUH, "step_order": 1},
                        {"stage_id": STAGE_BOSS, "step_order": 1}]},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "уникален" in response.text


def test_blanks_require_admin(client, hr_headers, settings_override, blanks_store):
    """Ручки бланков — только admin: ОК получает 403 на чтение и запись."""
    assert client.get("/settings/routing/blanks", headers=hr_headers).status_code == 403
    assert client.post(
        "/settings/routing/blanks", json=_blank_body(), headers=hr_headers
    ).status_code == 403
    assert client.put(
        f"/settings/routing/blanks/{BLANK_ID}", json={"active": False}, headers=hr_headers
    ).status_code == 403
    assert client.get(
        f"/settings/routing/blanks/{BLANK_ID}/steps", headers=hr_headers
    ).status_code == 403
    assert client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [{"stage_id": STAGE_BUH, "step_order": 1}]},
        headers=hr_headers,
    ).status_code == 403
    assert "create_blank" not in blanks_store.calls


def test_blank_code_and_layout_validation(
    client, admin_headers, settings_override, blanks_store
):
    """Код бланка — snake_case, макет — из встроенных пресетов (office|line)."""
    assert client.post(
        "/settings/routing/blanks", json=_blank_body(code="Плохой Код"), headers=admin_headers
    ).status_code == 422
    assert client.post(
        "/settings/routing/blanks", json=_blank_body(layout="docx"), headers=admin_headers
    ).status_code == 422
    assert "create_blank" not in blanks_store.calls


def test_catalogs_contains_blanks_and_keeps_existing_keys(
    client, admin_headers, settings_override, blanks_store
):
    """В справочниках админки появилась секция blanks; прежние ключи на месте."""
    response = client.get("/settings/routing/catalogs", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["code"] for item in body["blanks"]] == [BLANK_CODE]
    for key in ("services", "profiles", "stages", "profile_steps", "rosters"):
        assert key in body
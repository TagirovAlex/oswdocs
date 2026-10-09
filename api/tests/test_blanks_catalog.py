# Справочник бланков (миграции 0012/0014): хранилище
# (DbRoutingStore.list_blanks/create_blank/update_blank/list_blank_steps/
# set_blank_steps) и админ-эндпоинты GET/POST /settings/routing/blanks,
# PUT /settings/routing/blanks/{id}[/steps], секция blanks в
# GET /settings/routing/catalogs. Шаг бланка самостоятельный: свой текст
# (title/stage_lines) и свой исполнитель (executor_kind + assignees/owner_group),
# этапа у него нет. Живой Postgres не нужен: сессия хранилища подменяется моком
# (как в test_routing_store.py), эндпоинты — in-memory заглушкой справочника
# (как в test_routing_preview.py). Все коды, названия и логины — вымышленные.
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
BLANK_ID = 30
BLANK_CODE = "uvolnenie"
BLANK_NAME = "Вымышленный бланк увольнения"
STEP_GROUP = "SED_STEP_BUH"
STEP_GROUP_NAME = "Согласующие вымышленной службы"
PERSON_1 = "soglasovatel.pervyy"
PERSON_2 = "soglasovatel.vtoroy"
BLANK_HEADER = "<p>Увольнение {fio}</p>"
BLANK_FOOTER = ["Подпись {fio}", "Дата {date}"]

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
    "header_html": BLANK_HEADER,
    "footer_lines": list(BLANK_FOOTER),
    "step_count": 2,
}
# Строка шага бланка из БД (list_blank_steps): своих полей, без join с этапом.
BLANK_STEP_ROW = {
    "blank_id": BLANK_ID,
    "step_order": 1,
    "title": "Вымышленная бухгалтерия",
    "stage_lines": '["согласовать выплаты", "проверить расчёт"]',
    "executor_kind": "ad_group",
    "assignees": '["%s", "%s"]' % (PERSON_1, PERSON_2),
    "owner_group": STEP_GROUP,
    "optional": False,
    "require_comment": True,
    "approval_mode": "parallel",
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
    """Бланк по id (шапка/подвал на месте, подвал — список); отсутствует — None."""
    session = FakeSession([dict(BLANK_ROW, footer_lines=json.dumps(BLANK_FOOTER))])
    blank = _store(session).blank_by_id(BLANK_ID)
    assert blank["layout"] == "office"
    assert blank["header_html"] == BLANK_HEADER
    assert blank["footer_lines"] == BLANK_FOOTER
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
            "header_html": BLANK_HEADER,
            "footer_lines": list(BLANK_FOOTER),
            "actor": "adm.petrov",
        }
    )
    assert blank_id == 31
    sql, params = session.calls[0]
    assert "INSERT INTO blanks" in sql
    assert params["code"] == BLANK_CODE and params["doc_type_code"] == "uvolnenie_doc"
    assert params["layout"] == "line" and params["active"] is False
    assert params["header_html"] == BLANK_HEADER
    assert json.loads(params["footer_lines"]) == BLANK_FOOTER
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


def test_list_blank_steps_own_fields_without_stage_join():
    """Шаги бланка — собственные поля шага, join с этапом больше не нужен.

    jsonb-колонки разбираются в списки, логины согласующих — без пустых и
    повторов."""
    session = FakeSession([[BLANK_STEP_ROW]])
    items = _store(session).list_blank_steps(BLANK_ID)
    item = items[0]
    assert item["step_order"] == 1
    assert item["title"] == "Вымышленная бухгалтерия"
    assert item["stage_lines"] == ["согласовать выплаты", "проверить расчёт"]
    assert item["executor_kind"] == "ad_group"
    assert item["assignees"] == [PERSON_1, PERSON_2]
    assert item["owner_group"] == STEP_GROUP
    assert item["optional"] is False
    assert item["require_comment"] is True
    assert item["approval_mode"] == "parallel"
    sql = _sqls(session)[0]
    assert "FROM blank_steps" in sql
    assert "JOIN approval_stages" not in sql
    assert session.calls[0][1] == {"blank_id": BLANK_ID}


def test_set_blank_steps_replaces_composition_and_bumps_version():
    """Шаги бланка заменяются целиком, версия растёт: DELETE + INSERT + UPDATE."""
    session = FakeSession([])
    _store(session).set_blank_steps(
        BLANK_ID,
        [
            {
                "step_order": 2,
                "title": "Отдел кадров",
                "executor_kind": "people",
                "assignees": [PERSON_2, " ", PERSON_1],
                "stage_lines": ["оформить"],
            },
            {
                "step_order": 1,
                "title": "Бухгалтерия",
                "executor_kind": "ad_group",
                "owner_group": STEP_GROUP,
                "require_comment": True,
            },
        ],
        "adm.petrov",
    )
    assert _params_with(session, "DELETE FROM blank_steps") == [{"blank_id": BLANK_ID}]
    inserts = _params_with(session, "INSERT INTO blank_steps")
    # Порядок вставки — по step_order; этап у шага нет (в INSERT его нет вовсе).
    assert [item["step_order"] for item in inserts] == [1, 2]
    assert "stage_id" not in _sqls(session)[1]
    assert inserts[0]["title"] == "Бухгалтерия"
    assert inserts[0]["owner_group"] == STEP_GROUP
    assert inserts[0]["require_comment"] is True
    assert inserts[0]["optional"] is False
    assert inserts[1]["executor_kind"] == "people"
    assert inserts[1]["assignees"] == '["%s", "%s"]' % (PERSON_2, PERSON_1)
    assert all(item["blank_id"] == BLANK_ID for item in inserts)
    bumps = _params_with(session, "UPDATE blanks SET version = version + 1")
    assert bumps == [{"blank_id": BLANK_ID, "actor": "adm.petrov"}]
    audit = _audit_call(session)
    assert audit["action"] == "blank.steps.update" and audit["entity"] == "blank"
    assert audit["entity_id"] == str(BLANK_ID) and audit["actor"] == "adm.petrov"
    details = json.loads(audit["details"])
    assert details["steps"] == 2
    # Логины согласующих — данные сотрудников, в аудит не пишемся.
    assert "assignees" not in details and PERSON_1 not in audit["details"]
    assert session.commits == 1


def test_set_blank_steps_skips_broken_items_and_bumps_version():
    """Битые элементы пропускаются; пустой состав — замена на пустой + версия."""
    session = FakeSession([])
    _store(session).set_blank_steps(
        BLANK_ID,
        [None, {}, {"step_order": 1}, {"step_order": 2, "title": "  "}],
        "",
    )
    assert _params_with(session, "INSERT INTO blank_steps") == []
    assert _params_with(session, "DELETE FROM blank_steps")
    assert _params_with(session, "UPDATE blanks SET version = version + 1")
    details = json.loads(_audit_call(session)["details"])
    assert details["steps"] == 0
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
                    "step_order": 1,
                    "title": "Вымышленная бухгалтерия",
                    "stage_lines": ["согласовать выплаты"],
                    "executor_kind": "ad_group",
                    "assignees": [],
                    "owner_group": STEP_GROUP,
                    "optional": False,
                    "require_comment": False,
                    "approval_mode": "sequential",
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

    def blank_by_id(self, blank_id: int) -> dict | None:
        self._count("blank_by_id")
        found = next((item for item in self.blanks if item["id"] == blank_id), None)
        if found is None:
            return None
        row = dict(found)
        row["step_count"] = len(
            [item for item in self.steps if item["blank_id"] == blank_id]
        )
        return row

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
        # Режим шага нормализуется боевым хранилищем (миграция 0013): parallel или
        # sequential, всё прочее — «все ответственные».
        self.steps = [
            {
                "blank_id": blank_id,
                **item,
                "approval_mode": (
                    "parallel"
                    if str(item.get("approval_mode") or "").strip().casefold() == "parallel"
                    else "sequential"
                ),
            }
            for item in items
        ]
        for item in self.blanks:
            if item["id"] == blank_id:
                item["version"] = int(item.get("version") or 0) + 1

    def steps_for(self, blank_id: int) -> list[dict]:
        """Состав бланка для проверок теста (тот же срез, что list_blank_steps)."""
        return [dict(item) for item in self.steps if item["blank_id"] == blank_id]


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


def _blank_body(**kw) -> dict:
    body = {"code": "predpravka", "name": "Вымышленный бланк удержания"}
    body.update(kw)
    return body


def _people_step(step_order: int, **kw) -> dict:
    """Тело шага бланка с персональными согласующими (исполнитель people)."""
    step = {
        "step_order": step_order,
        "title": "Отдел кадров",
        "executor_kind": "people",
        "assignees": [PERSON_1],
        "stage_lines": ["оформить прекращение"],
    }
    step.update(kw)
    return step


# --- Эндпоинты бланков ---


def test_admin_blanks_crud_cycle(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """CRUD бланка: создание (201) с шапкой/подвалом, правка, чтение состава шагов."""
    created = client.post(
        "/settings/routing/blanks",
        json=_blank_body(header_html=BLANK_HEADER, footer_lines=[" Подпись {fio} "]),
        headers=admin_headers,
    )
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
    # Шапка/подвал — часть бланка: сохранились, края строк подвала обрезаны.
    assert stored["header_html"] == BLANK_HEADER
    assert stored["footer_lines"] == ["Подпись {fio}"]

    replaced = client.put(
        f"/settings/routing/blanks/{blank_id}/steps",
        json={"steps": [_people_step(1, optional=True)]},
        headers=admin_headers,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json() == {"blank_id": blank_id, "count": 1}

    steps = client.get(f"/settings/routing/blanks/{blank_id}/steps", headers=admin_headers)
    assert steps.status_code == 200, steps.text
    item = steps.json()[0]
    assert item["executor_kind"] == "people"
    assert item["assignees"] == [PERSON_1]
    assert item["optional"] is True


def test_update_blank_header_and_footer(
    client, admin_headers, settings_override, blanks_store
):
    """Правка шапки/подвала бланка — частичное обновление, поля в белом списке."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}",
        json={"header_html": "<p>Другая шапка</p>", "footer_lines": ["Подвал"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == "footer_lines,header_html"
    stored = next(item for item in blanks_store.blanks if item["id"] == BLANK_ID)
    assert stored["header_html"] == "<p>Другая шапка</p>"
    assert stored["footer_lines"] == ["Подвал"]


def test_blank_steps_read_returns_own_step(
    client, admin_headers, settings_override, blanks_store
):
    """Состав бланка отдаётся собственными полями шага (этапа у шага нет)."""
    response = client.get(f"/settings/routing/blanks/{BLANK_ID}/steps", headers=admin_headers)
    assert response.status_code == 200, response.text
    item = response.json()[0]
    assert item["title"] == "Вымышленная бухгалтерия"
    assert item["stage_lines"] == ["согласовать выплаты"]
    assert item["executor_kind"] == "ad_group"
    assert item["owner_group"] == STEP_GROUP
    assert item["approval_mode"] == "sequential"
    assert "stage_id" not in item and "print_assignee" not in item


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


def test_replace_blank_steps_unknown_ad_group_422(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Группа шага вне справочника групп шагов — 422, состав не меняется."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [{"step_order": 1, "title": "Вымышленная бухгалтерия",
                         "executor_kind": "ad_group", "owner_group": "SED_STEP_NET"}]},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "SED_STEP_NET" in response.text
    assert "set_blank_steps" not in blanks_store.calls


def test_replace_blank_steps_people_without_assignees_422(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Шаг people без согласующих — 422 на границе, состав не меняется."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [{"step_order": 1, "title": "Отдел кадров",
                         "executor_kind": "people", "assignees": ["  "]}]},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "assignees" in response.text
    assert "set_blank_steps" not in blanks_store.calls


def test_replace_blank_steps_ad_group_without_group_422(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Шаг ad_group без группы — 422 на границе, состав не меняется."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [{"step_order": 1, "title": "Бухгалтерия",
                         "executor_kind": "ad_group"}]},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "owner_group" in response.text
    assert "set_blank_steps" not in blanks_store.calls


def test_replace_blank_steps_empty_title_422(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Пустое название шага — 422 на границе, состав не меняется."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [_people_step(1, title="   ")]},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "title" in response.text
    assert "set_blank_steps" not in blanks_store.calls


def test_replace_blank_steps_duplicate_order_422(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Дубль порядка шага — 422 на границе (в БД PK (blank_id, step_order))."""
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [_people_step(1), _people_step(1, title="Второй")]},
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
        json={"steps": [_people_step(1)]},
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


def test_set_blank_steps_keeps_approval_mode_and_defaults_sequential():
    """Режим шага пишется в blank_steps; без режима — sequential («все»)."""
    session = FakeSession([])
    _store(session).set_blank_steps(
        BLANK_ID,
        [
            {"step_order": 1, "title": "Отдел кадров", "executor_kind": "people",
             "assignees": [PERSON_1], "approval_mode": "parallel"},
            {"step_order": 2, "title": "Бухгалтерия", "executor_kind": "ad_group",
             "owner_group": STEP_GROUP, "approval_mode": "что-то"},
        ],
        "adm.petrov",
    )
    inserts = _params_with(session, "INSERT INTO blank_steps")
    assert [item["approval_mode"] for item in inserts] == ["parallel", "sequential"]
    joined = " ".join(_sqls(session))
    assert "approval_mode" in joined


def test_blank_steps_422_on_unknown_approval_mode(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Режим вне parallel/sequential — 422 на границе, состав не меняется."""
    created = client.post("/settings/routing/blanks", json=_blank_body(), headers=admin_headers)
    blank_id = created.json()["id"]
    status = client.put(
        f"/settings/routing/blanks/{blank_id}/steps",
        json={"steps": [_people_step(1, approval_mode="как-нибудь")]},
        headers=admin_headers,
    )
    assert status.status_code == 422, status.text
    assert blanks_store.steps_for(blank_id) == []


def test_blank_steps_mode_round_trip(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Режим шага сохраняется в составе бланка и возвращается при чтении."""
    created = client.post("/settings/routing/blanks", json=_blank_body(), headers=admin_headers)
    blank_id = created.json()["id"]
    replaced = client.put(
        f"/settings/routing/blanks/{blank_id}/steps",
        json={"steps": [
            _people_step(1, approval_mode="parallel"),
            {"step_order": 2, "title": "Бухгалтерия", "executor_kind": "ad_group",
             "owner_group": STEP_GROUP},
        ]},
        headers=admin_headers,
    )
    assert replaced.status_code == 200, replaced.text
    rows = client.get(f"/settings/routing/blanks/{blank_id}/steps", headers=admin_headers)
    assert rows.status_code == 200, rows.text
    modes = [(row["title"], row.get("approval_mode")) for row in rows.json()]
    assert modes == [("Отдел кадров", "parallel"), ("Бухгалтерия", "sequential")]


def test_replace_blank_steps_bumps_blank_version(
    client, admin_headers, settings_override, blanks_store, groups_store
):
    """Замена состава увеличивает версию бланка (снимок blank_version заявки)."""
    before = next(
        item for item in blanks_store.blanks if item["id"] == BLANK_ID
    )["version"]
    response = client.put(
        f"/settings/routing/blanks/{BLANK_ID}/steps",
        json={"steps": [_people_step(1)]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    after = next(item for item in blanks_store.blanks if item["id"] == BLANK_ID)
    assert after["version"] == before + 1

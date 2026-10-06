# Хранилище справочников маршрута (app.routing_store): разбор ответов БД
# (list[dict], stage_lines из jsonb), upsert службы, замена состава этапа
# одной транзакцией, аудит правок без ПДн, обёртка ошибок БД в
# RoutingUnavailable. Живой Postgres не нужен: сессия подменяется моком,
# который отдаёт заранее заданные строки и помнит SQL/параметры/commit.
# Все логины и названия вымышленные.
from __future__ import annotations

import json
import os
import sys

import pytest
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Settings  # noqa: E402
from app.routing_store import (  # noqa: E402
    DbRoutingStore,
    RoutingUnavailable,
    get_routing_store,
)
from fastapi import HTTPException  # noqa: E402

# Вымышленные строки справочников (как их отдаёт Postgres).
SERVICE_ROW = {
    "id": 1,
    "dept_name": "Бухгалтерия",
    "status": "active",
    "active_count": 3,
    "last_seen_at": None,
    "blank_kind": "office",
    "route_profile_id": 10,
}
STAGE_ROW = {
    "id": 21,
    "code": "buh",
    "title": "Бухгалтерия",
    "stage_lines": '["согласовать выплаты", "проверить расчёт"]',
    "owner_kind": "ad_group",
    "owner_group": "SED_STEP_BUH",
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "active": True,
    "version": 1,
}
STEP_ROW = {
    "profile_step_id": 31,
    "profile_id": 10,
    "step_order": 1,
    "optional_override": None,
    "require_comment_override": None,
    "stage_id": 21,
    "stage_code": "buh",
    "stage_title": "Бухгалтерия",
    "stage_lines": ["согласовать выплаты"],
    "owner_kind": "ad_group",
    "owner_group": "SED_STEP_BUH",
    "optional": False,
    "print_assignee": True,
    "require_comment": False,
    "stage_active": True,
}
USER_ROW = {
    "sam": "step.buhgalter",
    "fio_full": "Вымышленный Бухгалтер Полный",
    "dept_ad": "Бухгалтерия",
    "title_ad": "Главный бухгалтер",
    "manager_dn": "CN=Вымышленный Начальник,OU=SED,DC=example,DC=local",
}


class FakeRow:
    """Строка ответа сессии: доступ по имени (row.code), по номеру (row[0])
    и через _mapping (dict) — как у настоящей строки SQLAlchemy."""

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

    def __init__(self, results=None, error: Exception | None = None) -> None:
        self._results = list(results or [])
        self.error = error
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, statement, params=None):
        if self.error is not None:
            raise self.error
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


def _audit_call(session: FakeSession) -> dict:
    """Параметры INSERT в audit_log (последний такой вызов)."""
    audits = [params for sql, params in session.calls if "INSERT INTO audit_log" in sql]
    assert audits, "аудит не записан"
    return audits[-1]


# --- Чтение справочников ---


def test_list_services_active_only_flag():
    """Список служб отдаётся словарями, флаг active_only уходит в БД."""
    session = FakeSession([[SERVICE_ROW]])
    items = _store(session).list_services(active_only=True)
    assert items == [SERVICE_ROW]
    assert "ad_services" in _sqls(session)[0]
    assert session.calls[0][1] == {"active_only": True}
    assert session.commits == 0


def test_list_profiles_and_stages_parse_json_lines():
    """Профили — словари; stage_lines из jsonb приходит списком."""
    session = FakeSession([[{"id": 12, "code": "default", "name": "По умолчанию",
                             "service_id": None, "active": True}]])
    assert _store(session).list_profiles()[0]["service_id"] is None

    session = FakeSession([[STAGE_ROW]])
    assert _store(session).list_stages()[0]["stage_lines"] == [
        "согласовать выплаты",
        "проверить расчёт",
    ]

    session = FakeSession([[dict(STAGE_ROW, stage_lines=None),
                            dict(STAGE_ROW, stage_lines="{битый")]])
    stages = _store(session).list_stages()
    assert stages[0]["stage_lines"] == [] and stages[1]["stage_lines"] == []
    assert session.calls[0][1] == {"active_only": False}


def test_list_profile_steps_returns_joined_stage_columns():
    """Шаги профиля приходят с колонками этапа (нужны pick_profile)."""
    session = FakeSession([[STEP_ROW]])
    items = _store(session).list_profile_steps(10)
    assert items[0]["stage_code"] == "buh"
    assert items[0]["stage_lines"] == ["согласовать выплаты"]
    assert items[0]["optional_override"] is None
    assert "JOIN approval_stages" in _sqls(session)[0]
    assert session.calls[0][1] == {"profile_id": 10}


def test_list_all_profile_steps_single_query():
    """Шаги ВСЕХ профилей одним запросом (без N+1 по профилям)."""
    session = FakeSession([[dict(STEP_ROW, id=None)]])
    items = _store(session).list_all_profile_steps()
    assert items[0]["profile_id"] == 10 and items[0]["stage_id"] == 21
    sql = _sqls(session)[0]
    assert "FROM route_profile_steps" in sql and "WHERE ps.profile_id" not in sql
    assert session.calls[0][1] == {}


def test_set_profile_steps_replaces_composition_in_one_transaction():
    """Состав профиля заменяется целиком: DELETE прежних + INSERT новых + аудит."""
    session = FakeSession([[FakeRow({"id": 21}), FakeRow({"id": 22})]])
    _store(session).set_profile_steps(
        10,
        [
            {"stage_id": 21, "step_order": 2, "optional_override": False,
             "require_comment_override": None},
            {"stage_id": 22, "step_order": 1, "optional_override": None,
             "require_comment_override": True},
        ],
        "ok.ivnova",
    )
    deletes = [params for sql, params in session.calls if "DELETE FROM route_profile_steps" in sql]
    inserts = [params for sql, params in session.calls if "INSERT INTO route_profile_steps" in sql]
    assert deletes == [{"profile_id": 10}]
    assert [(item["stage_id"], item["step_order"]) for item in inserts] == [(22, 1), (21, 2)]
    assert all(item["profile_id"] == 10 and item["actor"] == "ok.ivnova" for item in inserts)
    assert inserts[0]["optional_override"] is None and inserts[0]["require_comment_override"] is True
    assert inserts[1]["optional_override"] is False and inserts[1]["require_comment_override"] is None
    audit = _audit_call(session)
    assert audit["action"] == "profile.steps.update" and audit["entity"] == "profile"
    assert audit["entity_id"] == "10" and audit["actor"] == "ok.ivnova"
    details = json.loads(audit["details"])
    assert details["steps"] == 2 and details["stage_ids"] == [21, 22]
    assert session.commits == 1


def test_set_profile_steps_unknown_or_inactive_stage_422():
    """Неизвестный/отключённый этап — 422 до удаления прежних шагов (состав цел)."""
    session = FakeSession([[FakeRow({"id": 21})]])
    with pytest.raises(HTTPException) as err:
        _store(session).set_profile_steps(
            10,
            [{"stage_id": 21, "step_order": 1}, {"stage_id": 99, "step_order": 2}],
            "adm.petrov",
        )
    assert err.value.status_code == 422
    assert "99" in str(err.value.detail)
    assert "DELETE FROM route_profile_steps" not in " ".join(_sqls(session))
    assert session.commits == 0


def test_set_profile_steps_skips_broken_items_and_clears_composition():
    """Битые элементы пропускаются; пустой состав — очистка профиля с аудитом."""
    session = FakeSession([[]])
    _store(session).set_profile_steps(
        10,
        [None, {}, {"stage_id": None, "step_order": 1}, {"stage_id": 21, "step_order": 0}],
        "",
    )
    assert [sql for sql, _ in session.calls if "INSERT INTO route_profile_steps" in sql] == []
    assert [sql for sql, _ in session.calls if "DELETE FROM route_profile_steps" in sql]
    details = json.loads(_audit_call(session)["details"])
    assert details["steps"] == 0 and details["stage_ids"] == []
    assert session.commits == 1


def test_list_stage_assignees_active_only_by_default():
    """Состав этапа по умолчанию — только активные."""
    session = FakeSession([[{"sam": "ok.ivnova", "position_title": "Специалист", "active": True}]])
    items = _store(session).list_stage_assignees(22)
    assert items[0]["sam"] == "ok.ivnova"
    assert session.calls[0][1] == {"stage_id": 22, "active_only": True}


# --- Службы ---


def test_upsert_service_conflict_and_returns_id():
    """upsert службы идёт по имени (ON CONFLICT) и возвращает id."""
    session = FakeSession([FakeRow({"id": 7})])
    assert _store(session).upsert_service("Бухгалтерия", 3, None) == 7
    sql, params = session.calls[0]
    assert "ON CONFLICT (dept_name)" in sql
    assert params == {"dept_name": "Бухгалтерия", "active_count": 3, "last_seen_at": None}
    assert session.commits == 1


def test_service_by_dept_case_insensitive_lookup():
    """Поиск службы идёт через lower(btrim(...)) — регистр/пробелы не важны."""
    session = FakeSession([SERVICE_ROW])
    assert _store(session).service_by_dept("  бухгалтерия ")["id"] == 1
    assert "lower(btrim(dept_name))" in _sqls(session)[0]

    session = FakeSession([None])
    assert _store(session).service_by_dept("Нет такой") is None


def test_create_service_returns_id_and_audits():
    """Создание службы: RETURNING id + запись в audit_log (без ПДн)."""
    session = FakeSession([FakeRow({"id": 4})])
    assert _store(session).create_service({
        "dept_name": "Цех №1",
        "blank_kind": "line",
        "actor": "adm.petrov",
    }) == 4
    audit = _audit_call(session)
    assert audit["action"] == "service.create" and audit["entity"] == "service"
    assert audit["entity_id"] == "4" and audit["actor"] == "adm.petrov"
    assert "adm.petrov" not in audit["details"]
    assert session.commits == 1


def test_update_service_whitelist_and_audit():
    """В SET только поля из белого списка, неизвестные игнорируются."""
    session = FakeSession([])
    _store(session).update_service(4, {
        "blank_kind": "office",
        "route_profile_id": 12,
        "drop_table": "x",
        "actor": "ok.ivnova",
    })
    sql, params = session.calls[0]
    assert sql.startswith("UPDATE ad_services SET")
    assert "blank_kind = :blank_kind" in sql and "route_profile_id = :route_profile_id" in sql
    assert "drop_table" not in sql and "drop_table" not in params
    assert params["id"] == 4 and params["actor"] == "ok.ivnova"
    assert _audit_call(session)["action"] == "service.update"
    assert session.commits == 1


# --- Профили и этапы ---


def test_create_profile_returns_id():
    """Профиль создаётся с кодом и именем (service_id может быть пустым)."""
    session = FakeSession([FakeRow({"id": 12})])
    assert _store(session).create_profile({
        "code": "default",
        "name": "Профиль по умолчанию",
        "service_id": None,
        "actor": "adm.petrov",
    }) == 12
    sql, params = session.calls[0]
    assert "INSERT INTO route_profiles" in sql
    assert params["code"] == "default" and params["service_id"] is None
    assert _audit_call(session)["action"] == "profile.create"


def test_update_profile_whitelist_and_audit():
    """Правка профиля: активность/служба, аудит profile.update."""
    session = FakeSession([])
    _store(session).update_profile(12, {"active": False, "actor": "ok.ivnova"})
    sql, params = session.calls[0]
    assert "UPDATE route_profiles SET" in sql and "active = :active" in sql
    assert params["active"] is False and params["id"] == 12
    assert _audit_call(session)["action"] == "profile.update"


def test_create_stage_json_lines_and_audit():
    """Этап создаётся: stage_lines уходит jsonb-строкой, аудит без логинов."""
    session = FakeSession([FakeRow({"id": 21})])
    assert _store(session).create_stage({
        "code": "buh",
        "title": "Бухгалтерия",
        "stage_lines": ["согласовать выплаты", "проверить расчёт"],
        "owner_group": "SED_STEP_BUH",
        "actor": "ok.ivnova",
    }) == 21
    sql, params = session.calls[0]
    assert "CAST(:stage_lines AS jsonb)" in sql
    assert json.loads(params["stage_lines"]) == ["согласовать выплаты", "проверить расчёт"]
    audit = _audit_call(session)
    assert audit["action"] == "stage.create" and audit["entity"] == "stage"
    assert "SED_STEP_BUH" not in audit["details"]


def test_update_stage_bumps_version_and_audits():
    """Правка этапа поднимает версию (version = version + 1) и пишется в аудит."""
    session = FakeSession([])
    _store(session).update_stage(21, {
        "title": "Бухгалтерия (уточнено)",
        "stage_lines": ["новый пункт"],
        "actor": "ok.ivnova",
    })
    sql, params = session.calls[0]
    assert "version = version + 1" in sql
    assert json.loads(params["stage_lines"]) == ["новый пункт"]
    assert "owner_kind" not in params
    assert _audit_call(session)["action"] == "stage.update"
    assert session.commits == 1


def test_update_stage_empty_payload_is_noop():
    """Пустой payload — ни запроса, ни commit (нет что менять)."""
    session = FakeSession([])
    _store(session).update_stage(21, {"actor": "ok.ivnova"})
    assert session.calls == [] and session.commits == 0


# --- Состав этапа ---


def test_set_stage_assignees_replaces_composition_in_one_transaction():
    """Состав заменяется целиком: отсутствующие деактивируются, новые добавляются."""
    session = FakeSession([
        [{"sam": "a.b"}, {"sam": "c.d"}, {"sam": "e.f"}],  # текущий состав этапа
    ])
    _store(session).set_stage_assignees(22, [
        {"sam": "a.b", "position_title": "Специалист"},
        {"sam": " g.h ", "position_title": "  "},
    ], "ok.ivnova")
    updates = [params for sql, params in session.calls
               if "UPDATE stage_assignees" in sql]
    upserts = [params for sql, params in session.calls
               if "INSERT INTO stage_assignees" in sql]
    assert sorted(p["sam"] for p in updates) == ["c.d", "e.f"]
    assert all(p["actor"] == "ok.ivnova" for p in updates)
    assert [p["sam"] for p in upserts] == ["a.b", "g.h"]
    assert upserts[1]["position_title"] is None
    audit = _audit_call(session)
    assert audit["action"] == "stage.assignees.update" and audit["entity_id"] == "22"
    details = json.loads(audit["details"])
    assert details["activated"] == 2 and details["deactivated"] == 2
    assert "a.b" not in audit["details"] and "g.h" not in audit["details"]
    assert session.commits == 1


def test_set_stage_assignees_skips_broken_items():
    """Пустые/не-словарные элементы состава пропускаются."""
    session = FakeSession([[]])
    _store(session).set_stage_assignees(22, [None, {}, {"sam": "  "}], "")
    assert session.commits == 1
    upserts = [sql for sql, _ in session.calls if "INSERT INTO stage_assignees" in sql]
    assert upserts == []
    assert _audit_call(session)["actor"] == ""


# --- Карточка сотрудника и руководитель (только чтение) ---


def test_user_card_and_missing_card():
    """Карточка сотрудника — из зеркала users; отсутствие — None."""
    session = FakeSession([USER_ROW])
    card = _store(session).user_card(" Step.Buhgalter ")
    assert card["manager_dn"].startswith("CN=")
    assert "lower(sam) = lower(:sam)" in _sqls(session)[0]

    session = FakeSession([])
    assert _store(session).user_card("нет.такого") is None


def test_is_manager_true_and_false():
    """Руководитель зафиксирован, если у сотрудника непустой manager_dn."""
    session = FakeSession([FakeRow({"exists": True})])
    assert _store(session).is_manager("step.buhgalter") is True
    assert "manager_dn" in _sqls(session)[0]

    session = FakeSession([FakeRow({"exists": False})])
    assert _store(session).is_manager("step.buhgalter") is False


def test_manager_sam_by_dn():
    """sam по DN ищется по manager_dn; пустой DN — без обращения к БД."""
    session = FakeSession([FakeRow({"sam": "boss.petrov"})])
    assert _store(session).manager_sam_by_dn(
        "CN=Вымышленный Начальник,OU=SED,DC=example,DC=local"
    ) == "boss.petrov"

    session = FakeSession([FakeRow({"sam": None})])
    assert _store(session).manager_sam_by_dn("  ") is None
    assert session.calls == []


# --- Ошибки БД и боевая зависимость ---


def test_db_error_wrapped_into_routing_unavailable():
    """Сбой БД — RoutingUnavailable (роутер отвечает 503, не 500)."""
    session = FakeSession(error=OperationalError("SELECT 1", {}, Exception("соединение закрыто")))
    store = _store(session)
    with pytest.raises(RoutingUnavailable):
        store.list_services()
    with pytest.raises(RoutingUnavailable):
        store.create_profile({"code": "default", "name": "По умолчанию", "actor": "adm.petrov"})


def test_get_routing_store_is_lazy_singleton():
    """Боевая зависимость: один движок на процесс, подключения при создании нет."""
    settings = Settings(DATABASE_URL="postgresql://localhost:1/sed")
    store = get_routing_store(settings)
    assert isinstance(store, DbRoutingStore)
    assert get_routing_store(settings) is store
    store._engine.dispose()
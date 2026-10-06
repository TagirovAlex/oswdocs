# Подбор маршрута из справочников (app.routing): нормализация имён служб,
# четыре ветви pick_profile, порядок/пропуск этапов, снятия и добавления,
# право действия по шагу и резолвер исполнителя. БД/AD/1С не трогаются:
# справочники — фикстуры-словари, все данные вымышленные.
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routing import (  # noqa: E402
    NOTE_DEFAULT_DUPLICATES,
    NOTE_SERVICE_DUPLICATES,
    NOTE_SERVICE_PROFILE_UNAVAILABLE,
    REASON_DEFAULT_PROFILE,
    REASON_PROFILE_NOT_FOUND,
    REASON_SERVICE_NOT_REGISTERED,
    REASON_SERVICE_PROFILE,
    RoutePick,
    apply_dismissals_and_additions,
    can_user_act,
    normalize_dept,
    pick_profile,
    stage_executor,
)

# Справочники теста: службы (route_profile_id — профиль службы или пусто).
SERVICES = [
    {"id": 1, "dept_name": "Бухгалтерия", "route_profile_id": 10, "status": "active"},
    {"id": 2, "dept_name": "Цех №1", "route_profile_id": None, "status": "active"},
]

# Второе подразделение с тем же именем (после нормализации) — для проверки
# выбора первой по id и пометки причины.
SERVICES_WITH_DUPLICATE = SERVICES + [
    {"id": 3, "dept_name": "  бухгалтерия ", "route_profile_id": 11, "status": "active"},
]

PROFILES = [
    {"id": 9, "code": "buh", "name": "Бухгалтерия (старый)", "service_id": 1, "active": False},
    {"id": 10, "code": "buh-main", "name": "Бухгалтерия основной", "service_id": 1, "active": True},
    {"id": 11, "code": "buh-second", "name": "Бухгалтерия запасной", "service_id": 3, "active": True},
    {"id": 12, "code": "default", "name": "Профиль по умолчанию", "service_id": None, "active": True},
    {"id": 13, "code": "default-off", "name": "Выключенный по умолчанию", "service_id": None, "active": False},
]


def _step(step_order: int, stage_id: int, code: str, **kwargs) -> dict:
    """Строка route_profile_steps вместе с этапом (как отдаёт store)."""
    row = {
        "profile_step_id": 100 + stage_id,
        "step_order": step_order,
        "stage_id": stage_id,
        "stage_code": code,
        "stage_title": kwargs.get("title", code.upper()),
        "stage_lines": kwargs.get("lines", ["строка 1"]),
        "owner_kind": kwargs.get("owner_kind", "ad_group"),
        "owner_group": kwargs.get("owner_group", "SED_STEP_" + code.upper()),
        "optional": kwargs.get("optional", True),
        "print_assignee": kwargs.get("print_assignee", False),
        "require_comment": kwargs.get("require_comment", False),
        "optional_override": kwargs.get("optional_override"),
        "require_comment_override": kwargs.get("require_comment_override"),
        "stage_active": kwargs.get("stage_active", True),
    }
    for key in ("profile_id",):
        if key in kwargs:
            row[key] = kwargs[key]
    return row


# Шаги всех профилей (pick_profile получает список целиком и фильтрует сам).
PROFILE_STEPS = [
    _step(1, 20, "lead", profile_id=9, optional=False),
    _step(1, 21, "buh", profile_id=10, optional=False),
    _step(2, 22, "ok", profile_id=10, owner_kind="stage_roster", optional=True),
    _step(3, 23, "office", profile_id=10, owner_kind="ad_group", optional=True,
          owner_group="SED_STEP_OFFICE"),
    _step(1, 24, "ceo", profile_id=11, owner_kind="manager_ad", optional=False),
    _step(1, 25, "default-lead", profile_id=12, optional=False),
    _step(2, 26, "default-ok", profile_id=12, owner_kind="stage_roster"),
]

STAGES_CATALOG = [
    {"id": 20, "code": "lead", "title": "Руководитель", "optional": False, "active": True},
    {"id": 21, "code": "buh", "title": "Бухгалтерия", "optional": False, "active": True},
    {"id": 22, "code": "ok", "title": "Отдел кадров", "optional": True, "active": True},
    {"id": 23, "code": "office", "title": "Канцелярия", "optional": True, "active": True},
    {"id": 24, "code": "ceo", "title": "Директор", "optional": False, "active": True},
    {"id": 25, "code": "default-lead", "title": "Руководитель по умолчанию", "optional": False, "active": True},
    {"id": 26, "code": "default-ok", "title": "ОК по умолчанию", "optional": True, "active": True},
    {"id": 27, "code": "hr-council", "title": "Совет ОК", "optional": True, "active": True},
    {"id": 28, "code": "archive", "title": "Архив", "optional": True, "active": False},
]


# --- normalize_dept ---


def test_normalize_dept_trims_collapses_and_casefolds():
    """Пробелы (в том числе внутренние) и регистр не различаются."""
    assert normalize_dept("  Бухгалтерия  ") == "бухгалтерия"
    assert normalize_dept("БУХГАЛТеРИЯ") == "бухгалтерия"
    assert normalize_dept("Бухгалтерия   основного   фонда") == "бухгалтерия основного фонда"
    assert normalize_dept("Бухгалтерия\tосновного") == "бухгалтерия основного"


def test_normalize_dept_empty_values():
    """Пусто/None/пробелы — пустая строка (служба не найдена)."""
    assert normalize_dept(None) == ""
    assert normalize_dept("") == ""
    assert normalize_dept("   ") == ""


# --- pick_profile: четыре ветви ---


def test_pick_profile_service_profile():
    """Служба с route_profile_id — профиль службы, признак service_profile."""
    pick = pick_profile("Бухгалтерия", SERVICES, PROFILES, PROFILE_STEPS)
    assert pick.reason == REASON_SERVICE_PROFILE
    assert pick.profile["id"] == 10
    assert pick.service["id"] == 1


def test_pick_profile_service_match_ignores_case_and_spaces():
    """Служба ищется по нормализованному имени (регистр/пробелы не важны)."""
    pick = pick_profile("  бухгалтерия  ", SERVICES, PROFILES, PROFILE_STEPS)
    assert pick.reason == REASON_SERVICE_PROFILE
    assert pick.profile["id"] == 10


def test_pick_profile_duplicate_service_takes_lowest_id():
    """Несколько служб под одним именем — первая по id, помечаем причину."""
    pick = pick_profile("бухгалтерия", SERVICES_WITH_DUPLICATE, PROFILES, PROFILE_STEPS)
    assert pick.profile["id"] == 10
    assert pick.service["id"] == 1
    assert pick.reason.startswith(REASON_SERVICE_PROFILE)
    assert NOTE_SERVICE_DUPLICATES in pick.reason


def test_pick_profile_default_when_service_without_profile():
    """Служба есть, профиля у неё нет — профиль по умолчанию (service_id пуст)."""
    pick = pick_profile("Цех №1", SERVICES, PROFILES, PROFILE_STEPS)
    assert pick.reason == REASON_DEFAULT_PROFILE
    assert pick.profile["id"] == 12
    assert pick.service["id"] == 2


def test_pick_profile_default_when_service_profile_inactive():
    """Профиль службы выключен — берём профиль по умолчанию."""
    profiles = [dict(item) for item in PROFILES]
    for item in profiles:
        if item["id"] == 10:
            item["active"] = False
    pick = pick_profile("Бухгалтерия", SERVICES, profiles, PROFILE_STEPS)
    assert pick.reason.startswith(REASON_DEFAULT_PROFILE)
    assert NOTE_SERVICE_PROFILE_UNAVAILABLE in pick.reason
    assert pick.profile["id"] == 12


def test_pick_profile_default_skips_inactive_defaults():
    """Выключенный профиль по умолчанию не подходит."""
    profiles = [item for item in PROFILES if item["id"] != 12]
    pick = pick_profile("Цех №1", SERVICES, profiles, PROFILE_STEPS)
    assert pick.reason == REASON_PROFILE_NOT_FOUND
    assert pick.profile is None
    assert pick.stages == []


def test_pick_profile_duplicate_defaults_picks_lowest_id():
    """Несколько профилей по умолчанию — первый по id, помечаем причину."""
    profiles = list(PROFILES) + [
        {"id": 14, "code": "default-2", "name": "Второй по умолчанию",
         "service_id": None, "active": True},
    ]
    pick = pick_profile("Цех №1", SERVICES, profiles, PROFILE_STEPS)
    assert pick.profile["id"] == 12
    assert pick.reason.startswith(REASON_DEFAULT_PROFILE)
    assert NOTE_DEFAULT_DUPLICATES in pick.reason


def test_pick_profile_unknown_service_without_default():
    """Служба не заведена и профиля по умолчанию нет — profile_not_found."""
    pick = pick_profile("Неизвестная служба", SERVICES, [PROFILES[0]], PROFILE_STEPS)
    assert pick.reason == REASON_PROFILE_NOT_FOUND
    assert pick.profile is None
    assert pick.service is None
    assert pick.stages == []


def test_pick_profile_empty_dept_without_default():
    """Пустое имя службы и нет профиля по умолчанию — service_not_registered."""
    pick = pick_profile("   ", SERVICES, [PROFILES[0]], PROFILE_STEPS)
    assert pick.reason == REASON_SERVICE_NOT_REGISTERED
    assert pick.profile is None


def test_pick_profile_empty_dept_with_default():
    """Пустое имя службы, но есть профиль по умолчанию — маршрут строится."""
    pick = pick_profile(None, SERVICES, PROFILES, PROFILE_STEPS)
    assert pick.reason == REASON_DEFAULT_PROFILE
    assert pick.profile["id"] == 12


# --- pick_profile: этапы ---


def test_pick_profile_stages_order_and_optional_flag():
    """Этапы профиля по step_order; флаг «опционален» — override, иначе этапа."""
    pick = pick_profile("Бухгалтерия", SERVICES, PROFILES, PROFILE_STEPS)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "ok", "office"]
    assert [optional for _, optional in pick.stages] == [False, True, True]


def test_pick_profile_stages_optional_override_used():
    """optional_override шага профиля важнее optional этапа."""
    steps = [
        _step(1, 21, "buh", profile_id=12, optional=False, optional_override=True),
        _step(2, 26, "default-ok", profile_id=12, optional=True, optional_override=False),
    ]
    pick = pick_profile("Цех №1", SERVICES, PROFILES, steps)
    assert [optional for _, optional in pick.stages] == [True, False]


def test_pick_profile_skips_inactive_stage():
    """Выключенный этап в маршрут не попадает."""
    steps = [
        _step(1, 21, "buh", profile_id=12),
        _step(2, 28, "archive", profile_id=12, stage_active=False),
        _step(3, 26, "default-ok", profile_id=12),
    ]
    pick = pick_profile("Цех №1", SERVICES, PROFILES, steps)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "default-ok"]


def test_pick_profile_stages_stable_on_equal_step_order():
    """Одинаковый step_order — порядок по id этапа (стабильно)."""
    steps = [
        _step(1, 26, "default-ok", profile_id=12),
        _step(1, 21, "buh", profile_id=12),
    ]
    first = pick_profile("Цех №1", SERVICES, PROFILES, steps)
    second = pick_profile("Цех №1", SERVICES, PROFILES, list(reversed(steps)))
    codes = [stage["stage_code"] for stage, _ in first.stages]
    assert codes == ["buh", "default-ok"]
    assert [stage["stage_code"] for stage, _ in second.stages] == codes


def test_pick_profile_stages_only_of_chosen_profile():
    """Шаги других профилей не подтягиваются."""
    pick = pick_profile("Бухгалтерия", SERVICES, PROFILES, PROFILE_STEPS)
    assert "default-ok" not in [stage["stage_code"] for stage, _ in pick.stages]
    assert "ceo" not in [stage["stage_code"] for stage, _ in pick.stages]


def test_pick_profile_stages_without_profile_id_column():
    """Список шагов без profile_id (уже отобран по профилю) — берём целиком."""
    steps = [
        {"step_order": 1, "stage_id": 21, "stage_code": "buh", "optional": False},
        {"step_order": 2, "stage_id": 26, "stage_code": "default-ok", "optional": True},
    ]
    pick = pick_profile("Цех №1", SERVICES, PROFILES, steps)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "default-ok"]


# --- apply_dismissals_and_additions ---


def _base_pick() -> RoutePick:
    return pick_profile("Бухгалтерия", SERVICES, PROFILES, PROFILE_STEPS)


def test_apply_removes_dismissed_stage():
    """Снятый этап выпадает, остальные сохраняют порядок и флаги."""
    pick = apply_dismissals_and_additions(_base_pick(), ["OK"], [], STAGES_CATALOG)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "office"]
    assert "Dismissed=ok" in pick.reason
    assert pick.reason.startswith(REASON_SERVICE_PROFILE)


def test_apply_adds_stage_from_catalog():
    """Добавленный этап дописывается в конец из справочника (active=true)."""
    pick = apply_dismissals_and_additions(_base_pick(), [], ["hr-council"], STAGES_CATALOG)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "ok", "office", "hr-council"]
    assert pick.stages[-1][1] is True
    assert "Added=hr-council" in pick.reason


def test_apply_adds_inactive_catalog_stage_skipped():
    """Выключенный этап справочника не добавляется."""
    pick = apply_dismissals_and_additions(_base_pick(), [], ["archive"], STAGES_CATALOG)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "ok", "office"]
    assert "Added" not in pick.reason


def test_apply_unknown_code_ignored():
    """Неизвестный код снятия/добавления не ломает маршрут."""
    pick = apply_dismissals_and_additions(
        _base_pick(), ["нет-такого"], ["тоже-нет"], STAGES_CATALOG
    )
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "ok", "office"]
    assert pick.reason == REASON_SERVICE_PROFILE


def test_apply_duplicate_addition_once():
    """Повтор кода в добавлениях и уже присутствующий этап — без дублей."""
    pick = apply_dismissals_and_additions(
        _base_pick(), [], ["hr-council", "hr-council", "ok"], STAGES_CATALOG
    )
    codes = [stage["stage_code"] for stage, _ in pick.stages]
    assert codes == ["buh", "ok", "office", "hr-council"]
    assert "Added=hr-council" in pick.reason
    assert "ok" not in pick.reason.split("Added=")[1]


def test_apply_dismissed_then_added_returns_stage():
    """Снятый и тут же добавленный этап возвращается в конец."""
    pick = apply_dismissals_and_additions(_base_pick(), ["ok"], ["ok"], STAGES_CATALOG)
    assert [stage["stage_code"] for stage, _ in pick.stages] == ["buh", "office", "ok"]
    assert "Dismissed=ok" in pick.reason and "Added=ok" in pick.reason


def test_apply_keeps_profile_and_service():
    """Профиль и служба переносятся в новое решение без изменений."""
    base = _base_pick()
    pick = apply_dismissals_and_additions(base, [], ["hr-council"], STAGES_CATALOG)
    assert pick.profile == base.profile
    assert pick.service == base.service


def test_apply_does_not_mutate_source_pick():
    """Исходное решение не меняется (frozen + новые списки)."""
    base = _base_pick()
    apply_dismissals_and_additions(base, ["ok"], ["hr-council"], STAGES_CATALOG)
    assert [stage["stage_code"] for stage, _ in base.stages] == ["buh", "ok", "office"]


# --- can_user_act ---


def test_can_user_act_by_assignee():
    """Персональный исполнитель шага (sam)."""
    step = {"assignee": "Step.Buhgalter", "owner_group": "SED_STEP_OK"}
    assert can_user_act(step, "step.buhgalter", [], []) is True
    assert can_user_act(step, "  STEP.BUHGALTER ", [], []) is True
    assert can_user_act(step, "step.other", [], []) is False


def test_can_user_act_by_owner_group():
    """Член группы-владельца."""
    step = {"owner_group": "SED_STEP_BUH", "owner_kind": "ad_group"}
    assert can_user_act(step, "step.other", ["SED_STEP_BUH"], []) is True
    assert can_user_act(step, "step.other", ["sed_step_buh"], []) is True
    assert can_user_act(step, "step.other", ["SED_STEP_OK"], []) is False


def test_can_user_act_by_stage_roster():
    """Состав этапа (stage_roster) — по списку логинов этапа."""
    step = {"owner_kind": "stage_roster", "owner_group": None, "assignee": None}
    assert can_user_act(step, "ok.ivnova", [], ["ok.ivnova", "ok.head"]) is True
    assert can_user_act(step, "step.buhgalter", [], ["ok.ivnova"]) is False


def test_can_user_act_roster_ignored_for_other_kinds():
    """Список состава не даёт прав на этапы других видов."""
    step = {"owner_kind": "ad_group", "owner_group": "SED_STEP_OK"}
    assert can_user_act(step, "ok.ivnova", [], ["ok.ivnova"]) is False


def test_can_user_act_without_login_and_broken_step():
    """Без логина и для не-словаря — False."""
    step = {"owner_group": "SED_STEP_BUH"}
    assert can_user_act(step, "", ["SED_STEP_BUH"], []) is False
    assert can_user_act(None, "ok.ivnova", ["SED_STEP_BUH"], []) is False


# --- stage_executor ---


def test_stage_executor_manager_ad():
    """manager_ad — руководитель заявки резолвится в assignee."""
    step = {"owner_kind": "manager_ad"}
    assert stage_executor(step, "boss.petrov") == ("ad_direct_manager", "boss.petrov")
    assert stage_executor(step, None) == ("ad_direct_manager", None)


def test_stage_executor_groups_and_roster_without_assignee():
    """ad_group и stage_roster — персонального исполнителя нет."""
    assert stage_executor({"owner_kind": "ad_group", "owner_group": "SED_STEP_BUH"},
                          "boss.petrov") == ("by_group", None)
    assert stage_executor({"owner_kind": "stage_roster"}, "boss.petrov") == ("by_group", None)
    assert stage_executor({}, None) == ("by_group", None)


def test_stage_executor_by_user_takes_step_sam():
    """by_user — персональный шаг, исполнитель из sam шага."""
    step = {"owner_kind": "by_user", "sam": " Step.Buhgalter "}
    assert stage_executor(step, None) == ("by_user", "Step.Buhgalter")
    assert stage_executor({"owner_kind": "by_user"}, None) == ("by_user", None)
    assert stage_executor(None, None) == (None, None)
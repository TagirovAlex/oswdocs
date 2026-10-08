# Подбор маршрута согласования из справочников (без БД): по службе увольняемого
# выбирается профиль маршрута и его этапы, затем применяются снятия/добавления
# этапов, которые сделал ОК. Модуль чистый: на входе — строки справочников
# (list[dict] из app.routing_store), на выходе — решение RoutePick; ни одного
# обращения к БД, AD или 1С. Хранилище справочников — app/routing_store.py.
#
# Бланк (миграция 0012) — запасной/основной источник шагов вместо профиля:
# его состав приходит из справочника blank_steps, профиль при этом остаётся
# справочной подсказкой (см. pick_blank_steps).
#
# ПРИЗНАК ПОДБОРА reason — машинно читаемый токен: базовый (service_profile /
# default_profile / service_not_registered / profile_not_found), к нему через
# «+» дописываются пометки (service_duplicates, Dismissed=..., Added=...).

from __future__ import annotations

from dataclasses import dataclass, field

# Базовые признаки подбора профиля (reason RoutePick).
REASON_SERVICE_PROFILE = "service_profile"
REASON_DEFAULT_PROFILE = "default_profile"
REASON_SERVICE_NOT_REGISTERED = "service_not_registered"
REASON_PROFILE_NOT_FOUND = "profile_not_found"

# Пометки к базовому признаку (через «+»).
NOTE_SERVICE_DUPLICATES = "service_duplicates"
NOTE_DEFAULT_DUPLICATES = "default_profile_duplicates"
NOTE_SERVICE_PROFILE_UNAVAILABLE = "service_profile_unavailable"


@dataclass(frozen=True)
class RoutePick:
    """Решение по маршруту: профиль, служба, этапы и признак подбора.

    stages — список пар (этап, флаг «опционален») в порядке шагов профиля;
    этап здесь — строка справочника (из route_profile_steps вместе с этапом
    или из approval_stages при добавлении). Пустой профиль/этапы допустимы:
    маршрут собирает вызывающий код.
    """

    profile: dict | None = None
    service: dict | None = None
    reason: str = REASON_PROFILE_NOT_FOUND
    stages: list[tuple[dict, bool]] = field(default_factory=list)


def _normalize(value: str | None) -> str:
    """Строка к сравнению: обрезка краёв, схлопывание внутренних пробелов,
    casefold (регистр не различается)."""
    return " ".join(str(value or "").strip().casefold().split())


def normalize_dept(value: str | None) -> str:
    """Имя службы/подразделения к сравнению (как в app.docs _norm_position)."""
    return _normalize(value)


def _by_id(item: dict) -> int:
    """Порядок обхода справочника — по id (строки без id идут в конец)."""
    try:
        return int(item.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _stage_code(stage: dict) -> str:
    """Код этапа: у строки справочника code, у шага профиля stage_code."""
    return str(stage.get("code") or stage.get("stage_code") or "")


def _stage_view(row: dict) -> dict:
    """Строка этапа в едином виде: заполнены и code, и stage_code (в справочнике
    этапа это code, в шаге профиля — stage_code из join'а со справочником)."""
    view = dict(row)
    code = _stage_code(row)
    if code:
        view.setdefault("code", code)
        view.setdefault("stage_code", code)
    return view


def _join_reason(reason: str, *tokens: str) -> str:
    """Базовый признак + пометки через «+» (пустые пометки не добавляются)."""
    parts = [reason] if reason else []
    parts.extend(token for token in tokens if token)
    return "+".join(parts)


def pick_profile(
    dept_name: str | None,
    services: list[dict],
    profiles: list[dict],
    profile_steps: list[dict],
) -> RoutePick:
    """Профиль маршрута и его этапы по службе увольняемого.

    Порядок: профиль службы (services.dept_name к нормализованному dept_name,
    ad_services.route_profile_id; профиль должен быть active) -> профиль по
    умолчанию (service_id пуст и active, при нескольких — первый по id) ->
    ничего (service_not_registered, если имя службы не задано, иначе
    profile_not_found). Совпадений служб несколько — берём первую по id и
    помечаем причину service_duplicates.

    Этапы профиля — по step_order (при равенстве по id этапа); этап
    active=false в маршрут не попадает. Флаг «опционален»:
    optional_override шага профиля, а если он NULL — optional этапа.
    """
    wanted = normalize_dept(dept_name)
    notes: list[str] = []

    service: dict | None = None
    if wanted:
        matched = sorted(
            (
                item
                for item in (services or [])
                if isinstance(item, dict) and normalize_dept(item.get("dept_name")) == wanted
            ),
            key=_by_id,
        )
        if matched:
            service = matched[0]
            if len(matched) > 1:
                notes.append(NOTE_SERVICE_DUPLICATES)

    profile: dict | None = None
    reason = ""
    if service is not None and service.get("route_profile_id"):
        try:
            wanted_id = int(service.get("route_profile_id"))
        except (TypeError, ValueError):
            wanted_id = 0
        if wanted_id:
            found = sorted(
                (
                    item
                    for item in (profiles or [])
                    if isinstance(item, dict)
                    and _by_id(item) == wanted_id
                    and item.get("active")
                ),
                key=_by_id,
            )
            if found:
                profile = found[0]
                reason = REASON_SERVICE_PROFILE
            else:
                notes.append(NOTE_SERVICE_PROFILE_UNAVAILABLE)

    if profile is None:
        defaults = sorted(
            (
                item
                for item in (profiles or [])
                if isinstance(item, dict) and not item.get("service_id") and item.get("active")
            ),
            key=_by_id,
        )
        if defaults:
            profile = defaults[0]
            reason = REASON_DEFAULT_PROFILE
            if len(defaults) > 1:
                notes.append(NOTE_DEFAULT_DUPLICATES)

    if profile is None:
        reason = REASON_SERVICE_NOT_REGISTERED if not wanted else REASON_PROFILE_NOT_FOUND

    stages: list[tuple[dict, bool]] = []
    if profile is not None:
        profile_id = _by_id(profile)
        rows = [
            item
            for item in (profile_steps or [])
            if isinstance(item, dict)
            and _step_of_profile(item, profile_id)
            and item.get("stage_active", True)
        ]
        rows.sort(key=lambda item: (_step_order(item), _stage_id(item)))
        for item in rows:
            override = item.get("optional_override")
            optional = bool(item.get("optional")) if override is None else bool(override)
            stages.append((_stage_view(item), optional))

    return RoutePick(
        profile=profile,
        service=service,
        reason=_join_reason(reason, *notes),
        stages=stages,
    )


def _step_of_profile(item: dict, profile_id: int) -> bool:
    """Шаг этого профиля ли строка: profile_id совпал; в строке без profile_id
    считаем, что список шагов уже отобран по профилю (store отдаёт так)."""
    own = item.get("profile_id")
    if own is None:
        return True
    try:
        return int(own) == profile_id
    except (TypeError, ValueError):
        return False


def _step_order(item: dict) -> int:
    """Порядок шага профиля (строки без номера — в конец)."""
    try:
        return int(item.get("step_order") or 0)
    except (TypeError, ValueError):
        return 0


def _stage_id(item: dict) -> int:
    """id этапа: у шага профиля stage_id, у строки справочника — id."""
    value = item.get("stage_id")
    if value is None:
        value = item.get("id")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def apply_dismissals_and_additions(
    picked: RoutePick,
    dismissed_codes: list[str],
    addition_codes: list[str],
    stages_catalog: list[dict],
) -> RoutePick:
    """Снятия и добавления этапов поверх подбора (правка маршрута ОК).

    Шаги в порядке: снимаем этапы из dismissed_codes, затем дописываем в конец
    этапы из addition_codes (только active=true из справочника, каждый не более
    одного раза — повтор и уже присутствующий код игнорируются). Неизвестные коды
    молча пропускаются: маршрут не должен падать из-за устаревшего запроса.
    Новая reason — базовая с пометками Dismissed=коды / Added=коды.
    """
    dismissed = {
        code for code in (_normalize(item) for item in (dismissed_codes or [])) if code
    }
    kept: list[tuple[dict, bool]] = []
    dropped: list[str] = []
    for stage, optional in picked.stages:
        code = _normalize(_stage_code(stage))
        if code and code in dismissed:
            dropped.append(_stage_code(stage))
        else:
            kept.append((stage, optional))

    present = {_normalize(_stage_code(stage)) for stage, _ in kept}
    added: list[str] = []
    for raw in addition_codes or []:
        code = _normalize(raw)
        if not code or code in present:
            continue
        found = next(
            (
                item
                for item in (stages_catalog or [])
                if isinstance(item, dict)
                and _normalize(item.get("code")) == code
                and item.get("active")
            ),
            None,
        )
        if found is None:
            continue
        kept.append((_stage_view(found), bool(found.get("optional", True))))
        present.add(code)
        added.append(str(found.get("code") or ""))

    tokens: list[str] = []
    if dropped:
        tokens.append("Dismissed=%s" % ",".join(dropped))
    if added:
        tokens.append("Added=%s" % ",".join(added))
    return RoutePick(
        profile=picked.profile,
        service=picked.service,
        reason=_join_reason(picked.reason, *tokens),
        stages=kept,
    )


def pick_blank_steps(picked: RoutePick, blank_steps: list[dict]) -> RoutePick:
    """Шаги выбранного бланка вместо шагов профиля (бланк выбирает человек).

    blank_steps — строки DbRoutingStore.list_blank_steps: этап присоединён join'ом,
    поэтому строка этапа доступна целиком. Порядок — step_order; этап, отключённый
    позже (stage_active = FALSE), в маршрут не попадает. Флаг «опционален»:
    optional_override шага бланка, а если он NULL — optional этапа (как у шага
    профиля).

    Профиль, служба и признак подбора остаются от базового подбора (picked):
    для выбранного бланка они справочная подсказка, а этапы задаёт бланк. Так же
    работают снятия/добавления этапов — их применяет вызывающий через
    apply_dismissals_and_additions."""
    stages: list[tuple[dict, bool]] = []
    for item in blank_steps or []:
        if not isinstance(item, dict) or not item.get("stage_active", True):
            continue
        override = item.get("optional_override")
        optional = bool(item.get("optional")) if override is None else bool(override)
        stages.append((_stage_view(item), optional))
    stages.sort(key=lambda pair: (_step_order(pair[0]), _stage_id(pair[0])))
    return RoutePick(
        profile=picked.profile,
        service=picked.service,
        reason=picked.reason,
        stages=stages,
    )


def can_user_act(
    step: dict,
    user_sam: str,
    user_groups: list[str],
    roster_sams: list[str],
) -> bool:
    """Может ли сотрудник действовать по шагу: персональный исполнитель,
    группа-владелец или состав этапа (owner_kind = stage_roster)."""
    if not isinstance(step, dict):
        return False
    sam = _normalize(user_sam)
    if not sam:
        return False
    assignee = _normalize(step.get("assignee"))
    if assignee and assignee == sam:
        return True
    owner_group = _normalize(step.get("owner_group"))
    if owner_group and owner_group in {_normalize(g) for g in (user_groups or [])}:
        return True
    if step.get("owner_kind") == "stage_roster" and sam in {
        _normalize(item) for item in (roster_sams or [])
    }:
        return True
    return False


def stage_executor(step: dict, manager_sam: str | None) -> tuple[str | None, str | None]:
    """Резолвер и исполнитель шага этапа: (resolver, assignee).

    manager_ad — руководитель заявки (резолвер ad_direct_manager, как в
    requests.py _build_steps), stage_roster/ad_group — исполнитель из состава
    этапа или группы (резолвер by_group, персонального исполнителя нет),
    by_user — персональный шаг, assignee = sam из шага.
    """
    if not isinstance(step, dict):
        return None, None
    kind = str(step.get("owner_kind") or "ad_group").strip()
    if kind == "manager_ad":
        return "ad_direct_manager", (manager_sam or None)
    if kind == "by_user":
        sam = str(step.get("sam") or "").strip()
        return "by_user", (sam or None)
    return "by_group", None
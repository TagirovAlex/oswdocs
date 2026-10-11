# Справочник прав доступа: резолв блоков по группам AD, fail-closed при
# недоступном справочнике и CRUD раздела «Доступ и роли» (/settings/permissions).
#
# Офлайн-прогон без Postgres: справочник прав подменён InMemoryPermissionsStore,
# состав которого повторяет состояние после миграции 0020 (см. conftest).
# Группы шаблонов в тестах заданы явно и вымышлены. ПДн вымышленные.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.ad_groups_cache import (  # noqa: E402
    CachedMember,
    GroupsCacheUnavailable,
    InMemoryGroupsCacheStore,
    get_groups_cache_store,
)
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.permissions import (  # noqa: E402
    BASE_TEMPLATE_CODE,
    BLOCK_ARCHIVE_BACKUPS,
    BLOCK_EMPLOYEE_DIRECTORY,
    BLOCK_PRINT_BLANK,
    BLOCK_REQUEST_CREATE,
    BLOCK_SETTINGS_CONTENT,
    BLOCK_SETTINGS_INFRA,
    InMemoryPermissionsStore,
    PermissionsUnavailable,
    get_permissions_store,
    resolve_permissions,
)
from app.requests import (  # noqa: E402
    get_memory_requests_store,
    get_requests_store,
    reset_state_for_tests,
)
from app.settings_routes import get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_HR_ADMIN = "SED_HR_ADMIN"
TEST_SED_ADMIN = "SED_SEDADM"
TEST_STEP_PREFIX = "SED_STEP_"

# Вымышленные блоки для тестов (реальные коды — справочник perm_blocks).
BLOCK_LIST = "requests_list"
BLOCK_CARD = "request_card"
BLOCK_DECIDE = "step_decide"


class BrokenPermissionsStore:
    """Хранилище справочника прав, которое недоступно (ошибка БД)."""

    def load_catalog(self):
        raise PermissionsUnavailable("Справочник прав доступа недоступен (тест)")

    def replace_template(self, code, block_codes, group_names):
        raise PermissionsUnavailable("Справочник прав доступа недоступен (тест)")


@pytest.fixture(autouse=True)
def settings_override():
    """Тестовые группы ролей в env + возврат подмены после теста."""
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


@pytest.fixture(autouse=True)
def requests_store():
    """Офлайн-хранилище заявок (без Postgres) + чистый старт каждого теста."""
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    reset_state_for_tests()
    yield store
    app.dependency_overrides.pop(get_requests_store, None)
    reset_state_for_tests()


def _catalog(**template_groups):
    """Справочник прав для теста: блоки и шаблоны с группами по кодам.

    Коды блоков тестовые (как в тестовых группах) — резолв работает с любыми
    кодами справочника, важно только совпадение групп."""
    from app.permissions import PermissionBlock, PermissionCatalog, PermissionTemplate

    blocks = tuple(
        PermissionBlock(code=code, title=code, sort_order=10 * (index + 1))
        for index, code in enumerate(
            (
                BLOCK_LIST,
                BLOCK_CARD,
                BLOCK_REQUEST_CREATE,
                BLOCK_DECIDE,
                BLOCK_PRINT_BLANK,
                BLOCK_EMPLOYEE_DIRECTORY,
                BLOCK_SETTINGS_CONTENT,
                BLOCK_SETTINGS_INFRA,
                BLOCK_ARCHIVE_BACKUPS,
            )
        )
    )
    templates = (
        PermissionTemplate(
            code="admin",
            title="Администратор",
            protected=True,
            sort_order=20,
            blocks=(
                BLOCK_LIST,
                BLOCK_CARD,
                BLOCK_REQUEST_CREATE,
                BLOCK_DECIDE,
                BLOCK_PRINT_BLANK,
                BLOCK_EMPLOYEE_DIRECTORY,
                BLOCK_SETTINGS_CONTENT,
                BLOCK_SETTINGS_INFRA,
                BLOCK_ARCHIVE_BACKUPS,
            ),
            groups=tuple(sorted(template_groups.get("admin", set()))),
        ),
        PermissionTemplate(
            code="hr",
            title="Сотрудник ОК",
            sort_order=40,
            blocks=(
                BLOCK_LIST,
                BLOCK_CARD,
                BLOCK_REQUEST_CREATE,
                BLOCK_DECIDE,
                BLOCK_PRINT_BLANK,
                BLOCK_EMPLOYEE_DIRECTORY,
            ),
            groups=tuple(sorted(template_groups.get("hr", set()))),
        ),
        PermissionTemplate(
            code=BASE_TEMPLATE_CODE,
            title="Владелец шага",
            sort_order=50,
            blocks=(BLOCK_LIST, BLOCK_CARD, BLOCK_DECIDE),
        ),
    )
    return PermissionCatalog(blocks=blocks, templates=templates)


@pytest.fixture
def perms(admin=True, hr=True):
    """Хранилище справочника прав с группами admin/hr (вымышленные)."""
    groups = {}
    if admin:
        groups["admin"] = {TEST_ADMINS}
    if hr:
        groups["hr"] = {TEST_HR}
    store = InMemoryPermissionsStore(_catalog(**groups))
    app.dependency_overrides[get_permissions_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_permissions_store, None)


def _user(groups: list[str]):
    """Пользователь для юнит-проверок резолва (группы memberOf)."""
    from app.deps import CurrentUser

    return CurrentUser(sam="test.user", groups=list(groups))


# --- Резолв по группам ---

def test_resolve_blocks_by_template_groups(perms):
    """Блоки сотрудника ОК — из шаблона hr; админские блоки не выдаются."""
    resolved = resolve_permissions([TEST_HR], perms.load_catalog())
    assert BLOCK_REQUEST_CREATE in resolved.blocks
    assert BLOCK_EMPLOYEE_DIRECTORY in resolved.blocks
    assert BLOCK_SETTINGS_INFRA not in resolved.blocks
    assert resolved.role == "hr"


def test_resolve_unions_blocks_of_several_templates(perms):
    """Несколько шаблонов — объединение прав, а не «самый младший»."""
    resolved = resolve_permissions([TEST_HR, TEST_ADMINS], perms.load_catalog())
    assert BLOCK_REQUEST_CREATE in resolved.blocks
    assert BLOCK_SETTINGS_INFRA in resolved.blocks
    assert resolved.role == "admin"


def test_resolve_gives_owner_blocks_to_any_authenticated(perms):
    """Владелец шага: блоки «свои заявки/свой шаг» есть, служебных — нет."""
    resolved = resolve_permissions(["SED_STEP_BUH"], perms.load_catalog())
    assert {BLOCK_LIST, BLOCK_CARD, BLOCK_DECIDE} <= resolved.blocks
    assert BLOCK_REQUEST_CREATE not in resolved.blocks
    assert resolved.role == "owner"


def test_template_without_groups_matches_nobody(perms):
    """Пустой состав групп у шаблона — не «доступ всем», а никому."""
    perms.replace_template("hr", [BLOCK_LIST], [])
    resolved = resolve_permissions([TEST_HR], perms.load_catalog())
    assert resolved.blocks == frozenset({BLOCK_LIST, BLOCK_CARD, BLOCK_DECIDE})


def test_inactive_block_is_not_granted(perms):
    """Отключённый блок справочника не выдается даже своему шаблону."""
    catalog = perms.load_catalog()
    blocks = tuple(
        block.__class__(**{**block.__dict__, "active": block.code != BLOCK_PRINT_BLANK})
        for block in catalog.blocks
    )
    perms.set_catalog(catalog.__class__(blocks=blocks, templates=catalog.templates))
    resolved = resolve_permissions([TEST_HR], perms.load_catalog())
    assert BLOCK_PRINT_BLANK not in resolved.blocks
    assert BLOCK_EMPLOYEE_DIRECTORY in resolved.blocks


# --- Гейты: 403 на закрытом блоке и fail-closed ---

def _create_body(owner_group: str = "SED_STEP_BUH") -> dict:
    """Минимальное тело создания заявки (ручной маршрут, как в тестах заявок)."""
    return {
        "enterprise": "Вымышленное предприятие",
        "fio": "Вымышлен Сотрудник Полный",
        "tab_num": "В-0001",
        "department": "Вымышленная служба",
        "position": "Вымышленная должность",
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
        "steps": [{"owner_group": owner_group}],
    }


def test_closed_block_gives_403(client, hr_headers, perms):
    """Блок снят с шаблона — сотрудник ОК теряет создание заявки (403)."""
    before = client.post("/requests", json=_create_body(), headers=hr_headers)
    assert before.status_code == 201, before.text
    perms.replace_template("hr", [BLOCK_LIST, BLOCK_CARD], [TEST_HR])
    response = client.post("/requests", json=_create_body(), headers=hr_headers)
    assert response.status_code == 403
    assert response.json()["detail"]


def test_owner_cannot_create_request_by_block(client, owner_headers, perms):
    """У владельца шага блока «Создание заявки» нет — 403 (как и раньше)."""
    response = client.post("/requests", json=_create_body(), headers=owner_headers)
    assert response.status_code == 403


def test_fail_closed_when_catalog_unavailable(client, admin_headers):
    """Справочник прав недоступен — доступ закрыт (503), вход при этом работает."""
    app.dependency_overrides[get_permissions_store] = lambda: BrokenPermissionsStore()
    response = client.get("/settings", headers=admin_headers)
    assert response.status_code == 503
    assert "недоступен" in response.json()["detail"]
    # Вход и контракт /auth/me не ломаются: роль минимальная (owner).
    me = client.get("/auth/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "owner"


def test_empty_catalog_denies_everything(client, admin_headers, owner_headers):
    """Пустой справочник = прав нет: молчаливого возврата к ролям нет.

    Закрыты и админские функции, и базовые блоки владельца шага (fail-closed)."""
    app.dependency_overrides[get_permissions_store] = lambda: InMemoryPermissionsStore()
    assert client.get("/settings", headers=admin_headers).status_code == 403
    assert client.get("/requests", headers=owner_headers).status_code == 403
    assert client.get("/auth/me", headers=owner_headers).status_code == 200


def test_owner_sees_own_requests_by_default(client, owner_headers, perms, hr_headers):
    """Базовые блоки владельца шага (список/карточка/отметки) выданы всем."""
    created = client.post(
        "/requests", json=_create_body("SED_STEP_OTHER"), headers=hr_headers
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert client.get("/requests", headers=owner_headers).status_code == 200
    # Карточку чужой заявки закрывает область видимости (участники заявки), а не
    # блок: блок request_card у владельца есть (шаблон owner).
    assert client.get(f"/requests/{request_id}", headers=owner_headers).status_code == 403
    assert client.get(f"/requests/{request_id}", headers=hr_headers).status_code == 200


# --- CRUD раздела «Доступ и роли» ---

def test_read_permissions_contract(client, admin_headers, perms):
    """GET /settings/permissions: блоки, шаблоны, состав и группы одним ответом."""
    response = client.get("/settings/permissions", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["code"] for item in body["blocks"]][:2] == [BLOCK_LIST, BLOCK_CARD]
    templates = {item["code"]: item for item in body["templates"]}
    assert templates["admin"]["protected"] is True
    assert templates["admin"]["groups"] == [TEST_ADMINS]
    assert BLOCK_SETTINGS_INFRA in templates["admin"]["blocks"]
    assert templates[BASE_TEMPLATE_CODE]["blocks"] == [BLOCK_LIST, BLOCK_CARD, BLOCK_DECIDE]
    read = [e for e in audit_log.all() if e.action == "permissions.read"]
    assert read and read[-1].actor == "adm.petrov"


def test_permissions_section_forbidden_for_hr(client, hr_headers, perms):
    """Править права может не сотрудник ОК — 403."""
    assert client.get("/settings/permissions", headers=hr_headers).status_code == 403
    assert client.get("/settings/permissions/groups", headers=hr_headers).status_code == 403


def test_update_template_composition_with_audit(client, admin_headers, perms):
    """PUT /settings/permissions/templates/{code}: состав и группы целиком + аудит."""
    response = client.put(
        "/settings/permissions/templates/hr",
        json={"blocks": [BLOCK_LIST, BLOCK_REQUEST_CREATE], "groups": [TEST_HR, "SED_HR_2"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "code": "hr",
        "blocks": [BLOCK_LIST, BLOCK_REQUEST_CREATE],
        "groups": [TEST_HR, "SED_HR_2"],
    }
    events = [e for e in audit_log.all() if e.action == "permissions.update"]
    assert events and events[-1].entity_id == "hr"
    assert BLOCK_REQUEST_CREATE in events[-1].detail


def test_update_template_unknown_block_422(client, admin_headers, perms):
    """Код блока вне справочника — 422, состав не меняется."""
    response = client.put(
        "/settings/permissions/templates/hr",
        json={"blocks": [BLOCK_LIST, "no_such_block"], "groups": [TEST_HR]},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "no_such_block" in response.json()["detail"]
    assert BLOCK_REQUEST_CREATE in perms.load_catalog().template("hr").blocks


def test_update_unknown_template_404(client, admin_headers, perms):
    """Шаблона с таким кодом нет — 404."""
    response = client.put(
        "/settings/permissions/templates/nope",
        json={"blocks": [BLOCK_LIST], "groups": []},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_cannot_remove_own_infra_block_409(client, admin_headers, perms):
    """Снять у себя блок settings_infra нельзя: 409 и состав не меняется."""
    response = client.put(
        "/settings/permissions/templates/admin",
        json={
            "blocks": [BLOCK_LIST, BLOCK_SETTINGS_CONTENT],
            "groups": [TEST_ADMINS],
        },
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "Нельзя снять права у себя" in response.json()["detail"]
    assert BLOCK_SETTINGS_INFRA in perms.load_catalog().template("admin").blocks


def test_cannot_remove_own_group_409(client, admin_headers, perms):
    """Свою группу из своего шаблона убрать тоже нельзя — доступ к разделу падёт."""
    response = client.put(
        "/settings/permissions/templates/admin",
        json={
            "blocks": list(perms.load_catalog().template("admin").blocks),
            "groups": [],
        },
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert perms.load_catalog().template("admin").groups == (TEST_ADMINS,)


def test_settings_infra_can_be_moved_to_another_template(client, admin_headers, perms):
    """Блок можно убрать из шаблона, если право сохраняется другим шаблоном."""
    # Админ состоит и в шаблоне hr — блок переносится туда, доступ не теряется.
    perms.replace_template(
        "hr",
        list(perms.load_catalog().template("hr").blocks) + [BLOCK_SETTINGS_INFRA],
        [TEST_HR, TEST_ADMINS],
    )
    response = client.put(
        "/settings/permissions/templates/admin",
        json={
            "blocks": [
                code
                for code in perms.load_catalog().template("admin").blocks
                if code != BLOCK_SETTINGS_INFRA
            ],
            "groups": [TEST_ADMINS],
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert BLOCK_SETTINGS_INFRA not in response.json()["blocks"]


def test_permission_groups_from_ad_cache(client, admin_headers, perms):
    """GET /settings/permissions/groups: группы из кэша AD + дата синхронизации."""
    cache = InMemoryGroupsCacheStore()
    cache.save("SED_STEP_BUH", [CachedMember(group_name="SED_STEP_BUH", sam="t.ivan")])
    app.dependency_overrides[get_groups_cache_store] = lambda: cache
    response = client.get("/settings/permissions/groups", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["groups"]] == ["SED_STEP_BUH"]
    assert body["groups"][0]["members"] == 1
    # Дата синхронизации в офлайн-кэше не хранится — null, а не 500.
    assert body["synced_at"] is None


def test_permission_groups_store_down_503(client, admin_headers, perms):
    """Кэш состава групп недоступен — 503, не 500."""

    class BrokenGroups:
        def list_groups(self):
            raise GroupsCacheUnavailable("Кэш состава групп недоступен (тест)")

    app.dependency_overrides[get_groups_cache_store] = lambda: BrokenGroups()
    response = client.get("/settings/permissions/groups", headers=admin_headers)
    assert response.status_code == 503


def test_sed_admin_may_edit_permissions(client, perms):
    """Править права может и администратор СЭД (решение человека)."""
    from app.permissions import PermissionCatalog, PermissionTemplate

    catalog = perms.load_catalog()
    perms.set_catalog(
        PermissionCatalog(
            blocks=catalog.blocks,
            templates=catalog.templates
            + (
                PermissionTemplate(
                    code="sed_admin",
                    title="Администратор СЭД",
                    protected=True,
                    sort_order=10,
                    blocks=(BLOCK_SETTINGS_INFRA,),
                    groups=(TEST_SED_ADMIN,),
                ),
            ),
        )
    )
    settings = Settings(
        ALLOWED_AD_GROUPS="%s,%s" % (TEST_ALLOWED, TEST_SED_ADMIN),
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {
        "X-Mock-Sam": "sed.admin",
        "X-Mock-Groups": TEST_SED_ADMIN,
    }
    assert client.get("/settings/permissions", headers=headers).status_code == 200


def test_gate_uses_block_not_role_after_group_removed(client, admin_headers, perms):
    """Роль в /auth/me остаётся, но блок снят — функция закрыта (fail-closed)."""
    perms.replace_template(
        "admin",
        [
            code
            for code in perms.load_catalog().template("admin").blocks
            if code != BLOCK_ARCHIVE_BACKUPS
        ],
        [TEST_ADMINS],
    )
    assert client.get("/archive", headers=admin_headers).status_code == 403


def test_settings_store_unused_by_permissions(client, admin_headers, perms):
    """Правка справочника не трогает ключи ролевых групп в settings."""
    response = client.put(
        "/settings/permissions/templates/hr",
        json={"blocks": [BLOCK_LIST], "groups": [TEST_HR, "SED_HR_3"]},
        headers=admin_headers,
    )
    assert response.status_code == 200
    store = app.dependency_overrides.get(get_settings_store)
    values = store().get_many(("hr_groups", "admin_groups")) if store else {}
    assert "SED_HR_3" not in (values.get("hr_groups") or "")
# --- Раунд 3: гварды админки прав (решение человека 2026-10-11) ---


def test_owner_template_rejects_blocks_given_to_everyone(client, admin_headers, perms):
    """Шаблон owner совпадает ВСЕГДА: лишний блок раздался бы всем вошедшим.

    В UI это одна ошибка администратора, поэтому запрет серверный."""
    before = perms.load_catalog().template(BASE_TEMPLATE_CODE).blocks
    response = client.put(
        "/settings/permissions/templates/%s" % BASE_TEMPLATE_CODE,
        json={"blocks": before + [BLOCK_SETTINGS_INFRA], "groups": []},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "Настройки: инфраструктура" in response.json()["detail"]
    assert perms.load_catalog().template(BASE_TEMPLATE_CODE).blocks == before


def test_protected_template_cannot_be_left_without_blocks(client, admin_headers, perms):
    """protected = шаблон администратора нельзя очистить (миграция 0020).

    Через API этот случай перехватывает более точное правило «нельзя снять права
    у себя» (409) — сам запрет protected проверяется в хранилище, см. тест ниже."""
    response = client.put(
        "/settings/permissions/templates/admin",
        json={"blocks": [], "groups": ["SED_ADMINS"]},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert perms.load_catalog().template("admin").blocks

# --- Раунд 3: гварды админки прав (решение человека 2026-10-11) ---


def test_owner_template_rejects_blocks_given_to_everyone(client, admin_headers, perms):
    """Шаблон owner совпадает ВСЕГДА: лишний блок раздался бы всем вошедшим.

В UI это одна ошибка администратора, поэтому запрет серверный."""
    before = tuple(perms.load_catalog().template(BASE_TEMPLATE_CODE).blocks)
    response = client.put(
        "/settings/permissions/templates/%s" % BASE_TEMPLATE_CODE,
        json={"blocks": list(before) + [BLOCK_SETTINGS_INFRA], "groups": []},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "владелец шага" in response.json()["detail"]
    assert tuple(perms.load_catalog().template(BASE_TEMPLATE_CODE).blocks) == before


def test_protected_template_rejects_empty_blocks(perms):
    """protected = шаблон администратора нельзя очистить (миграция 0020).

    Проверяем хранилище напрямую: через API этот случай перехватывает более
    точное правило «нельзя снять права у себя» (409), а 422 — второй рубеж."""
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        perms.replace_template("admin", [], ["SED_ADMINS"])
    assert raised.value.status_code == 422
    assert "Защищённый шаблон" in raised.value.detail
    assert perms.load_catalog().template("admin").blocks

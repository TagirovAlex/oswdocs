# Фикстуры pytest для скелета API (волна A2): TestClient + моки групп.
# Границы LDAP/1C/SMTP/Postgres-live здесь не вызываются: пользователь
# подставляется заголовками X-Mock-*, которые на стенде заменит LDAPS bind.
# Все ПДн ниже — вымышленные.

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Корень api/ в sys.path, чтобы работал `from app.main import app` при запуске
# `pytest api/tests` из корня репозитория.
API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import app.audit as audit_module  # noqa: E402
from app.ad_groups_cache import InMemoryGroupsCacheStore, get_groups_cache_store  # noqa: E402
from app.audit import audit_log  # noqa: E402
from app.employee_sync import InMemoryEmployeeSyncStore, get_employee_sync_store  # noqa: E402
from app.employees import get_ad_reader  # noqa: E402
from app.main import app  # noqa: E402
from app.routing_store import get_routing_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402

# --- Вымышленные пользователи (не реальные ФИО/логины) ---
FAKE_HR = {
    "sam": "ok.ivnova",
    "fio": "Иванова Ольга Петровна",
    "mail": "ok.ivnova@example.com",
    "department": "Отдел кадров",
    "title": "Специалист по кадрам",
    "groups": ["SED_HR"],
}

FAKE_ADMIN = {
    "sam": "adm.petrov",
    "fio": "Петров Сергей Андреевич",
    "mail": "adm.petrov@example.com",
    "department": "ИТ-департамент",
    "title": "Администратор СЭД",
    "groups": ["SED_ADMINS"],
}

FAKE_HR_ADMIN = {
    "sam": "ok.head",
    "fio": "Королева Мария Викторовна",
    "mail": "ok.head@example.com",
    "department": "Отдел кадров",
    "title": "Руководитель отдела кадров",
    "groups": ["SED_HR_ADMIN"],
}

FAKE_OWNER = {
    "sam": "step.sidorov",
    "fio": "Сидоров Алексей Викторович",
    "mail": "step.sidorov@example.com",
    "department": "Бухгалтерия",
    "title": "Главный бухгалтер",
    "groups": ["SED_STEP_BUH"],
}

FAKE_NOBODY = {
    "sam": "user.guest",
    "fio": "Гостев Иван Иванович",
    "mail": "user.guest@example.com",
    "department": "Канцелярия",
    "title": "Делопроизводитель",
    "groups": ["SED_GUESTS"],
}


def _b64(value: str) -> str:
    """Кодирование мок-значения в base64: HTTP-заголовки обязаны быть ASCII."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def mock_headers(user: dict) -> dict:
    """Заголовки мока пользователя для TestClient (замена будущего LDAPS bind)."""
    return {
        "X-Mock-Sam": user["sam"],
        "X-Mock-Fio": _b64(user["fio"]),
        "X-Mock-Mail": _b64(user["mail"]),
        "X-Mock-Department": _b64(user["department"]),
        "X-Mock-Title": _b64(user["title"]),
        "X-Mock-Groups": ",".join(user["groups"]),
    }


class OfflineSettingsStore:
    """Пустое in-memory хранилище настроек для офлайн-прогонов (без Postgres).

    Контракт DbSettingsStore: значения в сид-формате строками, ключа нет — None.
    Пустое хранилище = «ключа enterprises нет», поэтому enterprise_name в ответах
    None (без 500) и ни одного обращения к живой БД."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get_many(self, keys) -> dict[str, str | None]:
        return {key: self.values.get(key) for key in keys}

    def set_many(self, values) -> None:
        self.values.update(values)


class OfflineRoutingStore:
    """Пустой справочник маршрута для офлайн-прогонов (без Postgres).

    Контракт DbRoutingStore на чтении: пустые списки справочников, отсутствующая
    карточка сотрудника/руководителя. Офлайн это значит «профилей нет» — подбор
    маршрута route_mode=auto отвечает 422 с причиной, а живая БД не запрашивается."""

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

    def user_card(self, sam: str) -> dict | None:
        return None

    def is_manager(self, sam: str) -> bool:
        return False

    def manager_sam_by_dn(self, manager_dn: str) -> str | None:
        return None


# Границы, которые по умолчанию не должны ходить в живую БД/AD (заметка B2 ревью):
# привилегированные эндпоинты заявок резолвят карту предприятий
# (_enterprise_names_map -> get_settings_store) и ридер AD, поэтому без подмен
# весь набор pytest зависал на несуществующем Postgres. Справочники маршрута
# (get_routing_store) — по той же причине: подбор маршрута auto и состав этапа
# читаются при создании заявки и выдаче карточки.
OFFLINE_BOUNDARIES = (
    get_settings_store,
    get_ad_reader,
    get_employee_sync_store,
    get_groups_cache_store,
    get_routing_store,
)


@pytest.fixture(autouse=True)
def offline_boundaries(monkeypatch):
    """Подменить живые границы офлайн-прогонов: настройки — пусто, AD — None,
    локальный справочник — пустой (фолбэк поиска на живой 1С), справочники
    маршрута — пустые.

    Тесты со своими override'ами перебивают эти значения своими (override ставится
    после autouse-фикстуры); для юнит-тестов ветки боевого кода без подмен —
    фикстура real_boundaries. Только чтение: AD/1С/Postgres не трогаются."""
    app.dependency_overrides[get_settings_store] = lambda: OfflineSettingsStore()
    app.dependency_overrides[get_ad_reader] = lambda: None
    app.dependency_overrides[get_employee_sync_store] = lambda: InMemoryEmployeeSyncStore()
    app.dependency_overrides[get_groups_cache_store] = lambda: InMemoryGroupsCacheStore()
    app.dependency_overrides[get_routing_store] = lambda: OfflineRoutingStore()
    # Персистентный аудит (INSERT в audit_log) — best-effort, но офлайн Postgres
    # нет: отключаем БД-хранилище, журнал остаётся in-memory (контракт прежний).
    monkeypatch.setattr(audit_module, "_get_db_store", lambda: None)
    yield
    for factory in OFFLINE_BOUNDARIES:
        app.dependency_overrides.pop(factory, None)


@pytest.fixture
def real_boundaries():
    """Снять офлайн-подмены границ (тест проверяет боевое ветвление без них)."""
    saved = {
        factory: app.dependency_overrides.pop(factory)
        for factory in OFFLINE_BOUNDARIES
        if factory in app.dependency_overrides
    }
    yield
    app.dependency_overrides.update(saved)


@pytest.fixture
def client():
    """TestClient приложения с чистым журналом аудита на каждый тест."""
    audit_log.clear_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    audit_log.clear_for_tests()


@pytest.fixture
def hr_headers() -> dict:
    """Заголовки сотрудника ОК (положена полная заглушка)."""
    return mock_headers(FAKE_HR)


@pytest.fixture
def admin_headers() -> dict:
    """Заголовки администратора (положена полная заглушка)."""
    return mock_headers(FAKE_ADMIN)


@pytest.fixture
def hr_admin_headers() -> dict:
    """Заголовки руководителя ОК (положена полная заглушка, как ОК)."""
    return mock_headers(FAKE_HR_ADMIN)


@pytest.fixture
def owner_headers() -> dict:
    """Заголовки владельца шага (положена только урезанная без ПДн)."""
    return mock_headers(FAKE_OWNER)


@pytest.fixture
def nogroup_headers() -> dict:
    """Заголовки пользователя без разрешающих групп (ожидается 403)."""
    return mock_headers(FAKE_NOBODY)


@pytest.fixture
def noauth_headers() -> dict:
    """Пустые заголовки без логина (ожидается 401)."""
    return {}

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

from app.audit import audit_log  # noqa: E402
from app.main import app  # noqa: E402

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

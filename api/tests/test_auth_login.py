# Тесты A2 auth-сервиса: POST /auth/login (мок AuthService in-memory + мок
# лимитера входа), GET /auth/me (обрезка владельца). Живых LDAP/Redis нет:
# сервис и лимитер подменяются через dependency_overrides, /auth/me в мок-пути —
# заголовки X-Mock-* из conftest. Логику самого лимитера проверяет test_w5a_rate_limit.py.
# ПДн вымышленные. Тестовые группы повторяют conftest (продовые — через env/БД).

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdUnavailable  # noqa: E402
from app.auth import AuthFailed, AuthResult, get_auth_service, get_login_limiter  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.main import app  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"


class InMemoryAuthService:
    """Мок AuthService (in-memory): сессии в словаре, без сети/LDAP/Redis."""

    def __init__(self, users=None, ad_down=False):
        self._users = {u["sam"].lower(): dict(u) for u in (users or [])}
        self._sessions = {}
        self.ad_down = ad_down
        self.logged_out = []

    def login(self, login, password):
        if self.ad_down:
            raise AdUnavailable("AD недоступен (тест)")
        user = self._users.get((login or "").strip().lower())
        if user is None or user.get("password") != password:
            raise AuthFailed("Неверные данные")
        if not user.get("allowed", True):
            raise AuthFailed("Нет доступа: пользователь не входит в разрешенные группы")
        current = CurrentUser(
            sam=user["sam"],
            fio=user.get("fio"),
            mail=user.get("mail"),
            department=user.get("department"),
            title=user.get("title"),
            groups=user.get("groups", []),
            role=user.get("role", "owner"),
        )
        token = "tok_" + user["sam"]
        self._sessions[token] = current
        return AuthResult(token=token, user=current)

    def me(self, token):
        return self._sessions.get(token)

    def logout(self, token):
        self._sessions.pop(token, None)
        self.logged_out.append(token)


class AlwaysAllowLoginRateLimiter:
    """Мок LoginRateLimiter: всегда пропускает вход, без Redis и сетевых таймаутов.

    Проверку порога лимита и 429 делает test_w5a_rate_limit.py на своем фейке;
    здесь нужен лишь заход без задержки на connect к живому Redis.
    """

    def __init__(self):
        self.reset_calls = []

    def allowed(self, ip, login):
        return True

    def reset(self, ip, login):
        self.reset_calls.append((ip, login))


# --- Вымышленные доменные учетки ---
HR_USER = {
    "sam": "ok.ivnova",
    "password": "pw-hr-1",
    "fio": "Иванова Ольга Петровна",
    "mail": "ok.ivnova@example.com",
    "department": "Отдел кадров",
    "title": "Специалист по кадрам",
    "groups": ["SED_HR"],
    "role": "hr",
}
NOGROUP_USER = {
    "sam": "user.guest",
    "password": "pw-guest-1",
    "fio": "Гостев Иван Иванович",
    "groups": ["SED_GUESTS"],
    "role": "owner",
    "allowed": False,
}


@pytest.fixture
def test_settings_override():
    """Тестовые группы (дефолты кода остаются нейтральными) + возврат после теста."""
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def mock_auth_service():
    """Мок AuthService вместо LdapAuthService (никакой сети/Redis)."""
    service = InMemoryAuthService(users=[HR_USER, NOGROUP_USER])
    limiter = AlwaysAllowLoginRateLimiter()
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    yield service
    app.dependency_overrides.pop(get_auth_service, None)
    app.dependency_overrides.pop(get_login_limiter, None)


# --- POST /auth/login ---

def test_login_ok_200(client, mock_auth_service):
    """Верные данные ОК: 200, токен + полный профиль пользователя."""
    response = client.post(
        "/auth/login", json={"login": "ok.ivnova", "password": "pw-hr-1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token"] == "tok_ok.ivnova"
    assert body["user"]["sam"] == "ok.ivnova"
    assert body["user"]["role"] == "hr"
    assert body["user"]["fio"] == HR_USER["fio"]


def test_login_wrong_password_401(client, mock_auth_service):
    """Неверный пароль — 401 (не раскрываем, какое поле неверно)."""
    response = client.post(
        "/auth/login", json={"login": "ok.ivnova", "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_unknown_user_401(client, mock_auth_service):
    """Неизвестный логин — 401 (то же сообщение, что при неверном пароле)."""
    response = client.post(
        "/auth/login", json={"login": "no.such", "password": "x"}
    )
    assert response.status_code == 401


def test_login_no_allowed_groups_401(client, mock_auth_service):
    """Верный пароль, но нет разрешенных групп — 401."""
    response = client.post(
        "/auth/login", json={"login": "user.guest", "password": "pw-guest-1"}
    )
    assert response.status_code == 401


def test_login_ad_down_503(client, mock_auth_service):
    """AD недоступен — 503 (инфраструктурная ошибка, а не 401/500)."""
    mock_auth_service.ad_down = True
    response = client.post(
        "/auth/login", json={"login": "ok.ivnova", "password": "pw-hr-1"}
    )
    assert response.status_code == 503


def test_login_empty_fields_422(client, mock_auth_service):
    """Пустые login/password не проходят валидацию — 422."""
    response = client.post("/auth/login", json={"login": "", "password": ""})
    assert response.status_code == 422


def test_login_service_me_logout(client, mock_auth_service):
    """me() по токену возвращает профиль, logout() — завершает сессию."""
    result = mock_auth_service.login("ok.ivnova", "pw-hr-1")
    assert mock_auth_service.me(result.token).sam == "ok.ivnova"
    mock_auth_service.logout(result.token)
    assert mock_auth_service.me(result.token) is None


def test_get_auth_service_forwards_tls_settings(monkeypatch):
    """Боевой сервис пробрасывает AD_TLS_VALIDATE/AD_CA_CERT в настройки шлюза
    (фикс TLS: вход проверяет цепочку корневым CA, а не CERT_NONE)."""
    captured = {}

    class FakeGateway:
        def __init__(self, ad_settings):
            captured["tls_validate"] = ad_settings.tls_validate
            captured["ca_certs_file"] = ad_settings.ca_certs_file

        def bind_user(self, dn, password):
            return True

    class FakeSessions:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("app.ad_reader.Ldap3Gateway", FakeGateway)
    monkeypatch.setattr("app.auth.RedisSessionStore", FakeSessions)
    settings = Settings(
        AD_TLS_VALIDATE=True,
        AD_CA_CERT="/etc/ssl/certs/sed-ca.pem",
        AD_BASE_DN="DC=example,DC=com",
        AD_READER_DN="CN=changeme,CN=Users,DC=example,DC=com",
    )
    service = get_auth_service(settings)
    assert service is not None
    assert captured["tls_validate"] is True
    assert captured["ca_certs_file"] == "/etc/ssl/certs/sed-ca.pem"


# --- GET /auth/me (обрезка как /me) ---

def test_auth_me_hr_full(client, hr_headers, test_settings_override):
    """ОК: /auth/me отдает полную карточку с ПДн и ролью hr."""
    response = client.get("/auth/me", headers=hr_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "hr"
    assert body["sam"] == hr_headers["X-Mock-Sam"]
    assert body["fio"] == "Иванова Ольга Петровна"


def test_auth_me_owner_trimmed_no_pdn(client, owner_headers, test_settings_override):
    """Владелец: /auth/me урезанная — только sam/groups/role, без ПДн."""
    response = client.get("/auth/me", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    assert set(body) == {"sam", "groups", "role"}
    for forbidden in ("fio", "mail", "department", "title"):
        assert forbidden not in body


def test_auth_me_no_auth_401(client, noauth_headers):
    """/auth/me без учетных данных — 401."""
    response = client.get("/auth/me", headers=noauth_headers)
    assert response.status_code == 401


def test_auth_me_no_group_403(client, nogroup_headers, test_settings_override):
    """/auth/me пользователя без разрешающих групп — 403 (мок-путь)."""
    response = client.get("/auth/me", headers=nogroup_headers)
    assert response.status_code == 403
# Тесты W5a rate-limit входа: Redis-счётчик sed:login:{ip}:{login} перед
# проверкой пароля. Юнит — RedisLoginRateLimiter на фейковом Redis (ключ,
# блок после N, сброс, Redis-down); эндпоинт — на фейковом лимитере через
# dependency_overrides (429 до проверки пароля, сброс при успехе, Redis-down —
# вход работает). ПДн вымышленные. Продовые лимиты — только из env.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import redis  # noqa: E402
from app.ad_reader import AdUnavailable  # noqa: E402
from app.auth import (  # noqa: E402
    AuthFailed,
    AuthResult,
    RedisLoginRateLimiter,
    get_auth_service,
    get_login_limiter,
)
from app.config import Settings, get_settings  # noqa: E402
from app.deps import CurrentUser  # noqa: E402
from app.main import app  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

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


class InMemoryAuthService:
    """Мок AuthService (in-memory): без сети/LDAP/Redis (как в test_auth_login)."""

    def __init__(self, users=None, ad_down=False):
        self._users = {u["sam"].lower(): dict(u) for u in (users or [])}
        self._sessions = {}
        self.ad_down = ad_down

    def login(self, login, password):
        if self.ad_down:
            raise AdUnavailable("AD недоступен (тест)")
        user = self._users.get((login or "").strip().lower())
        if user is None or user.get("password") != password:
            raise AuthFailed("Неверные данные")
        current = CurrentUser(
            sam=user["sam"], fio=user.get("fio"), mail=user.get("mail"),
            department=user.get("department"), title=user.get("title"),
            groups=user.get("groups", []), role=user.get("role", "owner"),
        )
        token = "tok_" + user["sam"]
        self._sessions[token] = current
        return AuthResult(token=token, user=current)

    def me(self, token):
        return self._sessions.get(token)

    def logout(self, token):
        self._sessions.pop(token, None)


class FakeLoginRateLimiter:
    """Фейк лимитера для эндпоинта: счетчики в памяти, down — Redis недоступен."""

    def __init__(self, limit=5):
        self.limit = limit
        self._counts = {}
        self.reset_calls = []
        self.down = False

    def allowed(self, ip, login):
        if self.down:
            return True
        key = (ip, login)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key] <= self.limit

    def reset(self, ip, login):
        self.reset_calls.append((ip, login))
        self._counts.pop((ip, login), None)


@pytest.fixture
def test_settings_override():
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
    service = InMemoryAuthService(users=[HR_USER])
    app.dependency_overrides[get_auth_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_auth_service, None)


@pytest.fixture
def fake_limiter():
    limiter = FakeLoginRateLimiter(limit=5)
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    yield limiter
    app.dependency_overrides.pop(get_login_limiter, None)


def _login(client, login="ok.ivnova", password="pw-hr-1"):
    return client.post("/auth/login", json={"login": login, "password": password})


# --- юнит RedisLoginRateLimiter (фейковый Redis) ---

class FakePipeline:
    def __init__(self, store):
        self._store = store
        self._cmds = []

    def incr(self, key):
        self._cmds.append(("incr", key))
        return self

    def expire(self, key, seconds, nx=False):
        self._cmds.append(("expire", key, seconds, nx))
        return self

    def execute(self):
        out = []
        for cmd in self._cmds:
            if cmd[0] == "incr":
                value = self._store.get(cmd[1], 0) + 1
                self._store[cmd[1]] = value
                out.append(value)
            elif cmd[0] == "expire":
                out.append(True)
        return out


class FakeRedis:
    def __init__(self, down=False):
        self._data = {}
        self.down = down
        self._pipelines = []

    def pipeline(self):
        if self.down:
            raise redis.RedisError("Redis недоступен (тест)")
        pipe = FakePipeline(self._data)
        self._pipelines.append(pipe)
        return pipe

    def delete(self, key):
        if self.down:
            raise redis.RedisError("Redis недоступен (тест)")
        self._data.pop(key, None)


def _make_limiter(monkeypatch, fake_redis, limit=5, window=60):
    monkeypatch.setattr(
        "app.auth.redis.from_url", lambda url, **kw: fake_redis
    )
    return RedisLoginRateLimiter("redis://fake", limit, window)


def test_rate_limit_key_format_and_blocks(monkeypatch):
    """Ключ sed:login:{ip}:{login}; блок после N попыток, EXPIRE с NX — только первой."""
    fake = FakeRedis()
    limiter = _make_limiter(monkeypatch, fake, limit=3, window=60)
    for _ in range(3):
        assert limiter.allowed("10.0.0.1", "ok.ivnova") is True
    assert limiter.allowed("10.0.0.1", "ok.ivnova") is False
    assert "sed:login:10.0.0.1:ok.ivnova" in fake._data
    # Окно: EXPIRE(NX) выполняется (TTL не продлевается на каждой попытке).
    expire_calls = [
        c for p in fake._pipelines for c in p._cmds if c[0] == "expire"
    ]
    assert expire_calls and all(c[3] is True for c in expire_calls)


def test_rate_limit_isolated_per_login_and_ip(monkeypatch):
    """Блок одного ключа не блокирует другой логин/адрес."""
    fake = FakeRedis()
    limiter = _make_limiter(monkeypatch, fake, limit=2, window=60)
    limiter.allowed("10.0.0.1", "user.a")
    limiter.allowed("10.0.0.1", "user.a")
    assert limiter.allowed("10.0.0.1", "user.a") is False
    assert limiter.allowed("10.0.0.1", "user.b") is True
    assert limiter.allowed("10.0.0.2", "user.a") is True


def test_rate_limit_reset_clears(monkeypatch):
    """reset() удаляет ключ: после сброса попытки снова разрешены."""
    fake = FakeRedis()
    limiter = _make_limiter(monkeypatch, fake, limit=2, window=60)
    limiter.allowed("10.0.0.1", "ok.ivnova")
    limiter.reset("10.0.0.1", "ok.ivnova")
    assert limiter.allowed("10.0.0.1", "ok.ivnova") is True


def test_rate_limit_redis_down_allows(monkeypatch):
    """Redis недоступен — лимит пропускается (вход работает, не 503)."""
    fake = FakeRedis(down=True)
    limiter = _make_limiter(monkeypatch, fake, limit=3, window=60)
    assert limiter.allowed("10.0.0.1", "ok.ivnova") is True
    limiter.reset("10.0.0.1", "ok.ivnova")  # не падает


# --- эндпоинт POST /auth/login с лимитером ---

def test_login_429_after_threshold(client, mock_auth_service, fake_limiter):
    """После N неудач — 429 (до проверки пароля), с нужным detail."""
    for _ in range(5):
        assert _login(client, password="wrong").status_code == 401
    response = _login(client, password="pw-hr-1")
    assert response.status_code == 429
    assert response.json()["detail"] == "Слишком много попыток входа"


def test_login_rate_limit_reset_on_success(client, mock_auth_service, fake_limiter):
    """Успешный вход сбрасывает счетчик (подбор не копится), сброс по ip:login."""
    fake_limiter.limit = 3
    _login(client, password="wrong")
    _login(client, password="wrong")
    ok = _login(client)
    assert ok.status_code == 200
    assert fake_limiter.reset_calls and fake_limiter.reset_calls[-1][1] == "ok.ivnova"
    assert fake_limiter.reset_calls[-1][0] == "testclient"  # request.client.host
    # Снова первая неудача — 401, а не 429.
    assert _login(client, password="wrong").status_code == 401


def test_login_rate_limit_redis_down_login_works(client, mock_auth_service, fake_limiter):
    """Redis down — лимит пропускается: неверный пароль 401, верный — 200."""
    fake_limiter.down = True
    assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200


def test_login_rate_limit_ignores_ad_503(client, mock_auth_service, fake_limiter):
    """AD недоступен — 503, лимитер при этом не сработал (вход не блокируется)."""
    mock_auth_service.ad_down = True
    response = _login(client)
    assert response.status_code == 503
    assert fake_limiter.reset_calls == []
# Аутентификация СЭД: LDAPS bind (пароль пользователя) + сессии в Redis (волна A2).
#
# Контракт (TASKS_AUTH.md):
#   POST /auth/login  {login, password} -> 200 {token, user} | 401 | 503 (AD недоступен)
#   GET  /auth/me     Bearer -> CurrentUser (полный для admin/hr_admin/hr, урезанный для owner)
#   Токен — secrets.token_urlsafe; ключ Redis `sed:session:{token}`,
#   TTL — SESSION_TTL_MINUTES из настроек (дефолт 20, README п.5: 15–20 мин).
#   Пароль проверяется bind'ом пользователя по DN (шлюз Ldap3Gateway с bind_user
#   добавляет агент A1 в ad_reader.py), сам пароль нигде не хранится.
#   Роль — существующий _detect_role из deps (admin > hr_admin > hr > owner); разрешенные
#   группы — из настроек (is_group_allowed). AD недоступен -> 503, неверные
#   данные/нет разрешенных групп -> 401.

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable, Protocol

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .ad_reader import (
    AdNotFound,
    AdReader,
    AdReaderError,
    AdReaderSettings,
    AdUnavailable,
    AdUser,
    InMemoryCache,
    group_cn,
    reader_secret_from_env,
)
from .audit import AuditEvent, audit_log
from .config import Settings, get_settings
from .deps import CurrentUser, _detect_role, get_current_user, is_privileged

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Ошибки (маппинг на HTTP-коды — в роутере ниже)
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Базовая ошибка аутентификации."""


class AuthFailed(AuthError):
    """Неверные данные либо нет разрешенных групп (401)."""


class SessionUnavailable(AuthError):
    """Хранилище сессий (Redis) недоступно (503)."""


# ---------------------------------------------------------------------------
# Результат входа
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthResult:
    """Результат успешного входа: случайный токен + профиль пользователя."""

    token: str
    user: CurrentUser


# ---------------------------------------------------------------------------
# Хранилище сессий: Redis в бою, in-memory мок — в тестах
# ---------------------------------------------------------------------------

_SESSION_KEY_PREFIX = "sed:session:"


class SessionStore(Protocol):
    """Граница хранилища сессий (ключи только `sed:session:*`)."""

    def get(self, token: str) -> CurrentUser | None:
        """Профиль по токену либо None (нет/истекла)."""
        ...  # pragma: no cover

    def set(self, token: str, user: CurrentUser, ttl_seconds: int) -> None:
        """Сохранить профиль с TTL в секундах."""
        ...  # pragma: no cover

    def delete(self, token: str) -> None:
        """Удалить сессию (выход)."""
        ...  # pragma: no cover


class RedisSessionStore:
    """Сессии в Redis: ключ `sed:session:{token}`, значение — JSON CurrentUser.

    Соединение — redis.from_url(REDIS_URL из env), TTL — из настроек.
    Падение Redis превращается в SessionUnavailable (503), а не в 500.
    """

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds
        self._redis = redis.from_url(redis_url, decode_responses=True)

    @staticmethod
    def _key(token: str) -> str:
        return f"{_SESSION_KEY_PREFIX}{token}"

    def get(self, token: str) -> CurrentUser | None:
        if not token:
            return None
        try:
            raw = self._redis.get(self._key(token))
        except redis.RedisError as exc:
            raise SessionUnavailable(f"Хранилище сессий недоступно: {exc}") from exc
        if not raw:
            return None
        try:
            return CurrentUser.model_validate_json(raw)
        except Exception:
            # Битый JSON — считаем сессию недействительной, а не падаем 500.
            return None

    def set(self, token: str, user: CurrentUser, ttl_seconds: int | None = None) -> None:
        try:
            self._redis.set(
                self._key(token),
                user.model_dump_json(),
                ex=ttl_seconds if ttl_seconds is not None else self._ttl_seconds,
            )
        except redis.RedisError as exc:
            raise SessionUnavailable(f"Хранилище сессий недоступно: {exc}") from exc

    def delete(self, token: str) -> None:
        try:
            self._redis.delete(self._key(token))
        except redis.RedisError as exc:
            raise SessionUnavailable(f"Хранилище сессий недоступно: {exc}") from exc


# ---------------------------------------------------------------------------
# Rate-limit входа (W5a): Redis-счётчик sed:login:{ip}:{login} перед проверкой
# пароля. Лимит/окно — из env (LOGIN_RATE_LIMIT/LOGIN_RATE_WINDOW_SECONDS);
# падение Redis — лимит пропускается (вход работает, а не 503, как
# SessionUnavailable-паттерн). Успешный вход сбрасывает счётчик.
# ---------------------------------------------------------------------------

_LOGIN_RATE_KEY_PREFIX = "sed:login:"


class LoginRateLimiter(Protocol):
    """Граница лимитера входа (в бою — Redis, в тестах — in-memory фейк)."""

    def allowed(self, ip: str, login: str) -> bool:
        """Разрешить ли попытку: False — лимит исчерпан (429), до проверки пароля."""
        ...  # pragma: no cover

    def reset(self, ip: str, login: str) -> None:
        """Сбросить счётчик (успешный вход)."""
        ...  # pragma: no cover


class RedisLoginRateLimiter:
    """Счётчик попыток входа в Redis: ключ `sed:login:{ip}:{login}`.

    INCR + EXPIRE(NX) атомарно в pipeline: окно отсчитывается от первой
    попытки. Падение Redis — allowed() возвращает True (лимит пропускаем,
    вход работает), reset() — молча пропускаем.
    """

    def __init__(self, redis_url: str, limit: int, window_seconds: int) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._limit = limit
        self._window_seconds = window_seconds

    @staticmethod
    def _key(ip: str, login: str) -> str:
        return f"{_LOGIN_RATE_KEY_PREFIX}{ip}:{login}"

    def allowed(self, ip: str, login: str) -> bool:
        key = self._key(ip, login)
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            # EXPIRE с NX: TTL ставится только на первой попытке, окно не продлевается.
            pipe.expire(key, self._window_seconds, nx=True)
            current, _ = pipe.execute()
        except redis.RedisError:
            return True
        return current <= self._limit

    def reset(self, ip: str, login: str) -> None:
        try:
            self._redis.delete(self._key(ip, login))
        except redis.RedisError:
            return


def get_login_limiter(settings: Settings = Depends(get_settings)) -> LoginRateLimiter:
    """Боевой лимитер: тот же Redis, что и сессии (REDIS_URL из env).

    В офлайн-тестах переопределяется in-memory фейком через
    dependency_overrides (как get_auth_service/get_settings_store).
    """
    return RedisLoginRateLimiter(
        settings.REDIS_URL,
        settings.LOGIN_RATE_LIMIT,
        settings.LOGIN_RATE_WINDOW_SECONDS,
    )


# ---------------------------------------------------------------------------
# Auth-сервис: Protocol (граница) + живая реализация на AdReader
# ---------------------------------------------------------------------------

class AuthService(Protocol):
    """Граница auth-сервиса: в бою — LdapAuthService, в тестах — in-memory мок."""

    def login(self, login: str, password: str) -> AuthResult:
        """Вход по доменным логину+паролю.

        Ошибки: AuthFailed (401), AdUnavailable (503, AD недоступен).
        """
        ...  # pragma: no cover

    def me(self, token: str) -> CurrentUser | None:
        """Профиль по токену сессии либо None (нет/истекла)."""
        ...  # pragma: no cover

    def logout(self, token: str) -> None:
        """Завершить сессию."""
        ...  # pragma: no cover


class LdapAuthService:
    """Реальная доменная аутентификация: AdReader (LDAPS) + SessionStore (Redis).

    Пароль проверяется bind'ом пользователя по DN (шлюз с bind_user добавит A1),
    пароль нигде не хранится. Роль — существующий _detect_role (admin > hr_admin > hr
    > owner), разрешенные группы — из настроек. AD недоступен -> 503, неверные
    данные/нет групп -> 401.
    """

    def __init__(
        self,
        reader: AdReader,
        sessions: SessionStore,
        settings: Settings,
        verify_password: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._reader = reader
        self._sessions = sessions
        self._settings = settings
        # Проверка пароля — bind_user шлюза (волна A1). Если не передана —
        # это недостроенный стенд (503), а не молчаливый пропуск проверки.
        self._verify_password = verify_password

    def login(self, login: str, password: str) -> AuthResult:
        login = (login or "").strip()
        if not login or not password:
            raise AuthFailed("Неверные данные")
        try:
            # Сначала bind RO-учеткой: поиск без связки шлюз не выполняет (A1).
            self._reader.bind_reader()
            ad_user = self._reader.get_user(login)
        except AdNotFound as exc:
            # Пользователя нет — то же «Неверные данные» (не раскрываем наличие).
            raise AuthFailed("Неверные данные") from exc
        # AdUnavailable из reader.get_user пробрасываем как есть: роутер -> 503.
        if not ad_user.enabled:
            raise AuthFailed("Учетная запись отключена")
        if self._verify_password is None:
            raise AdUnavailable("Проверка пароля не настроена: шлюз AD (волна A1)")
        try:
            ok = self._verify_password(ad_user.dn, password)
        except AdReaderError:
            raise
        except Exception as exc:  # таймаут/сеть на bind — AD недоступен, не 500
            raise AdUnavailable(f"AD недоступен (bind пароля): {exc}") from exc
        if not ok:
            raise AuthFailed("Неверные данные")
        if not self._has_allowed_group(ad_user):
            raise AuthFailed("Нет доступа: пользователь не входит в разрешенные группы")
        groups = [group_cn(dn) for dn in ad_user.member_of]
        user = CurrentUser(
            sam=ad_user.sam,
            fio=ad_user.display_name,
            mail=ad_user.mail,
            department=ad_user.department,
            title=ad_user.title,
            groups=groups,
            role=_detect_role(groups, self._settings),
        )
        token = secrets.token_urlsafe(32)
        self._sessions.set(token, user, self._session_ttl_seconds())
        return AuthResult(token=token, user=user)

    def me(self, token: str) -> CurrentUser | None:
        return self._sessions.get(token)

    def logout(self, token: str) -> None:
        self._sessions.delete(token)

    def _has_allowed_group(self, ad_user: AdUser) -> bool:
        """Есть ли хоть одна разрешенная группа (явные + админы + ОК + префикс шагов)."""
        return any(
            self._settings.is_group_allowed(group_cn(dn)) for dn in ad_user.member_of
        )

    def _session_ttl_seconds(self) -> int:
        """TTL новой сессии: из settings БД (session_ttl_minutes, правит админ),
        иначе env SESSION_TTL_MINUTES. БД недоступна — env-дефолт (вход не валим)."""
        ttl_minutes = self._settings.SESSION_TTL_MINUTES
        try:
            from .settings_routes import DbSettingsStore, read_setting_value

            store = DbSettingsStore(self._settings.DATABASE_URL)
            raw = read_setting_value(store, "session_ttl_minutes")
            if raw:
                ttl_minutes = int(raw)
        except Exception:
            pass  # БД/настройки недоступны — берем значение из env
        return max(60, ttl_minutes * 60)


# ---------------------------------------------------------------------------
# Зависимость FastAPI: боевой сервис (на стенде); в тестах — dependency_overrides
# ---------------------------------------------------------------------------

def get_auth_service(settings: Settings = Depends(get_settings)) -> AuthService:
    """Боевой auth-сервис: AdReader + Redis-сессии + bind пароля через AD.

    Шлюз Ldap3Gateway (bind_user) реализует агент A1 в ad_reader.py — импорт
    ленивый, чтобы модуль работал до сборки волны A1 (офлайн-тесты подменяют
    сервис через dependency_overrides). Сигнатуру конструктора шлюза сверить
    с A1 при мерже волны A.
    """
    try:
        from .ad_reader import Ldap3Gateway  # волна A1: реализация шлюза LDAP

        ad_settings = AdReaderSettings(
            ad_url=settings.AD_URL,
            base_dn=settings.AD_BASE_DN,
            reader_dn=settings.AD_READER_DN,
            reader_secret=reader_secret_from_env(),
            cache_ttl_seconds=settings.LDAP_CACHE_TTL,
            tls_validate=settings.AD_TLS_VALIDATE,
            ca_certs_file=settings.AD_CA_CERT,
        )
        gateway = Ldap3Gateway(ad_settings)
        reader = AdReader(ad_settings, gateway=gateway, cache=InMemoryCache())
        verify_password = gateway.bind_user
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AD-шлюз не собран (волна A1): {exc}",
        ) from exc
    sessions = RedisSessionStore(settings.REDIS_URL, settings.session_ttl_seconds)
    return LdapAuthService(reader, sessions, settings, verify_password=verify_password)


# ---------------------------------------------------------------------------
# Роутер: POST /auth/login, GET /auth/me (обрезка как /me)
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """Тело POST /auth/login (пароль в открытом виде — только по HTTPS)."""

    login: str = Field(..., min_length=1, description="Доменный логин (sAMAccountName)")
    password: str = Field(..., min_length=1, description="Пароль доменной учетки")


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    limiter: LoginRateLimiter = Depends(get_login_limiter),
) -> dict:
    """Вход по доменной учетке: 200 {token, user} | 401 | 429 (rate-limit) | 503."""
    ip = request.client.host if request.client is not None else ""
    if not limiter.allowed(ip, payload.login):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много попыток входа",
        )
    try:
        result = service.login(payload.login, payload.password)
    except AuthFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    except (AdUnavailable, SessionUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    # Успешный вход — сброс счётчика (подбор не копится).
    limiter.reset(ip, payload.login)
    return {"token": result.token, "user": result.user.model_dump()}


@router.get("/auth/me")
def auth_me(
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Текущий пользователь: ОК/админам — полная карточка, владельцам — без ПДн (как /me)."""
    settings.ensure_read_only()
    audit_log.append(
        AuditEvent(actor=user.sam, action="me.read", entity="user", entity_id=user.sam)
    )
    if is_privileged(user):
        return {
            "sam": user.sam,
            "fio": user.fio,
            "department": user.department,
            "title": user.title,
            "mail": user.mail,
            "groups": user.groups,
            "role": user.role,
        }
    # Урезанная: без ФИО/подразделения/должности/почты (ПДн не светят владельцам).
    return {"sam": user.sam, "groups": user.groups, "role": user.role}
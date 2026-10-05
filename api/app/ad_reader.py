# СЭД, Волна A4: чтение AD только через LDAPS (только чтение).
#
# Живой bind по LDAPS:636 сервисной RO-учеткой — пометка «на стенде»
# (проверяется на тестовой ВМ по чек-листу ИТ; здесь — фейковый шлюз в тестах).
# Запись в AD запрещена: методов записи/удаления/отключения УЗ здесь нет
# и быть не должно (см. ensure_read_only + test_no_write_methods).

"""Интерфейс чтения AD (только чтение).

Читаемые атрибуты: sAMAccountName, displayName, manager,
department/title, memberOf, mail, userAccountControl.

Правила (см. README п.1, скил ad-reader):
- Группы/OU/BASE_DN — только из настроек/env, хардкод DN запрещен.
- Стыковка 1С↔AD — только по полному ФИО + ручное подтверждение ОК.
- При расхождении истина — 1С; оба значения показываем в снапшоте.
- Кэш manager/memberOf — TTL из настроек; mail — только для уведомлений.
- Падения AD не кладут API: таймаут + понятная ошибка (AdUnavailable).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Tuple


# ---------------------------------------------------------------------------
# Ошибки
# ---------------------------------------------------------------------------

class AdReaderError(Exception):
    """Базовая ошибка чтения AD."""


class AdNotFound(AdReaderError):
    """Запись не найдена в каталоге."""


class AdUnavailable(AdReaderError):
    """Каталог недоступен (таймаут/сеть/bind); API падать не должен."""


class AdWriteBlocked(AdReaderError):
    """Попытка записи в AD либо включен флаг записи (запрещено)."""


def ensure_read_only() -> None:
    """Проверка запрета записи: флаг AD_WRITE_ENABLED обязан быть false.

    Сам флаг не меняем (чужой, A2/env), только читаем.
    """
    flag = os.getenv("AD_WRITE_ENABLED", "false").strip().lower()
    if flag not in ("", "false", "0", "no", "off"):
        raise AdWriteBlocked(
            "Запись в AD запрещена: AD_WRITE_ENABLED должен быть false."
        )


# ---------------------------------------------------------------------------
# Настройки (только env/settings, хардкода DN нет)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdReaderSettings:
    """Параметры подключения и кэша; значения — из env/settings."""

    ad_url: str  # ожидаем ldaps://...:636
    base_dn: str  # из настроек (BASE_DN)
    reader_dn: str  # DN сервисной RO-учетки из настроек
    reader_secret: str = ""  # AD_READER_SECRET — секрет, только из env
    cache_ttl_seconds: int = 21600  # LDAP_CACHE_TTL, по умолчанию 6 ч (в окне 4–8 ч)
    timeout_seconds: float = 5.0  # таймаут LDAP-операций
    tls_validate: bool = False  # AD_TLS_VALIDATE; внутренний ЦС — по умолчанию false
    ca_certs_file: str = ""  # AD_CA_CERT — файл корневого CA для проверки LDAPS

    @classmethod
    def from_env(cls) -> "AdReaderSettings":
        """Собрать настройки из окружения (секреты — только env)."""
        ttl_raw = os.getenv("LDAP_CACHE_TTL", "21600").strip()
        timeout_raw = os.getenv("AD_TIMEOUT_SECONDS", "5").strip()
        validate_raw = os.getenv("AD_TLS_VALIDATE", "false").strip().lower()
        return cls(
            ad_url=os.getenv("AD_URL", ""),
            base_dn=os.getenv("AD_BASE_DN", ""),
            reader_dn=os.getenv("AD_READER_DN", ""),
            reader_secret=os.getenv("AD_READER_SECRET", ""),
            cache_ttl_seconds=int(ttl_raw or 21600),
            timeout_seconds=float(timeout_raw or 5),
            tls_validate=validate_raw in ("1", "true", "yes", "on"),
            ca_certs_file=os.getenv("AD_CA_CERT", ""),
        )


def reader_secret_from_env() -> str:
    """Секрет RO-учетки — только из env (в код/настройки БД не класть)."""
    return os.getenv("AD_READER_SECRET", "")


# ---------------------------------------------------------------------------
# Шлюз LDAP (граница для моков): живую реализацию подменить на стенде
# ---------------------------------------------------------------------------

class LdapGateway(Protocol):
    """Граница LDAP: фейк в тестах, живой ldap3-клиент — на стенде."""

    def bind(self) -> None:
        """Bind сервисной RO-учеткой. Живой bind — на стенде."""
        ...  # pragma: no cover

    def search_user_by_sam(self, sam: str) -> Optional[Dict]:
        """Сырая запись по sAMAccountName либо None. Живой поиск — на стенде."""
        ...  # pragma: no cover

    def search_user_by_dn(self, dn: str) -> Optional[Dict]:
        """Сырая запись по DN либо None. Живой поиск — на стенде."""
        ...  # pragma: no cover

    def search_users(self, query: str) -> List[Dict]:
        """Сырые записи по подстроке displayName либо []. Живой поиск — на стенде."""
        ...  # pragma: no cover

    def list_user_titles(self) -> List[Dict]:
        """Сырые записи всех пользователей (title + userAccountControl) либо [].

        Лёгкий запрос для справочника должностей (полные карточки не нужны).
        Живое перечисление — на стенде (постранично)."""
        ...  # pragma: no cover

    def search_group_by_cn(self, cn: str) -> Optional[Dict]:
        """Сырая запись группы по CN (cn + member) либо None. Только чтение."""
        ...  # pragma: no cover


class Ldap3Gateway:
    """Живой LDAP-шлюз на ldap3 (LDAPS, только чтение).

    Реализует контракт LdapGateway: bind RO-учеткой, поиск по sAMAccountName
    (SUBTREE по BASE_DN) и по DN (scope BASE). Дополнительно bind_user(dn,
    password) — проверка пароля пользователя его собственной учеткой,
    без записи.

    ldap3 импортируется лениво и в тестах подменяется фейковым модулем через
    инъекцию (ldap3_module) — никакой сети в CI/локальных тестах.
    """

    SEARCH_ATTRS = (
        "sAMAccountName",
        "displayName",
        "manager",
        "department",
        "title",
        "memberOf",
        "mail",
        "userAccountControl",
    )
    #: Атрибуты группы: состав (member) — только чтение, для уведомлений.
    GROUP_SEARCH_ATTRS = ("cn", "member")

    def __init__(
        self,
        settings: AdReaderSettings,
        ldap3_module: object | None = None,
    ) -> None:
        ensure_read_only()
        self._settings = settings
        if ldap3_module is None:
            import ldap3  # лениво: пакет нужен только для живого LDAPS

            ldap3_module = ldap3
        self._ldap3 = ldap3_module
        self._server = None
        self._conn = None

    # -- подключение -----------------------------------------------------------
    def _make_tls(self) -> object:
        # Внутренний корпоративный ЦС (FIDELIO-DC2-CA): при tls_validate=true
        # проверяем цепочку корневым CA из AD_CA_CERT (read-only mount в api).
        # ldap3.Tls принимает ssl-режим проверки (VerifyMode): CERT_NONE без
        # проверки цепочки, CERT_REQUIRED — с проверкой.
        import ssl  # локальный импорт: ssl нужен только живому шлюзу

        if self._settings.tls_validate:
            ca = self._settings.ca_certs_file
            if not ca:
                raise AdReaderError("AD_TLS_VALIDATE=true требует AD_CA_CERT (корневой CA).")
            return self._ldap3.Tls(
                validate=ssl.CERT_REQUIRED,
                ca_certs_file=ca,
            )
        return self._ldap3.Tls(validate=ssl.CERT_NONE)

    def _connect(self, user_dn: str, password: str) -> Tuple[object, object]:
        """Создать Server/Connection и сразу сделать bind указанной учеткой."""
        ldap3 = self._ldap3
        # ldap3 (2.9.1) требует целочисленные таймауты: float ломает socket
        # ("required argument is not an integer") — переводим в int.
        timeout = int(self._settings.timeout_seconds)
        server = ldap3.Server(
            self._settings.ad_url,
            use_ssl=True,
            connect_timeout=timeout,
            tls=self._make_tls(),
        )
        conn = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            raise_exceptions=True,
            receive_timeout=timeout,
        )
        return server, conn

    def bind(self) -> None:
        """Bind сервисной RO-учеткой (только чтение)."""
        self._server, self._conn = self._connect(
            self._settings.reader_dn, self._settings.reader_secret
        )

    # -- поиск -----------------------------------------------------------------
    def search_user_by_sam(self, sam: str) -> Optional[Dict]:
        """Сырая запись по sAMAccountName (SUBTREE по BASE_DN) либо None."""
        return self._search(
            self._settings.base_dn,
            "(sAMAccountName={})".format(self._escape_filter(sam)),
            self._ldap3.SUBTREE,
        )

    def search_user_by_dn(self, dn: str) -> Optional[Dict]:
        """Сырая запись по DN (scope BASE) либо None."""
        return self._search(dn, "(objectClass=user)", self._ldap3.BASE)

    def search_users(self, query: str) -> List[Dict]:
        """Поиск по подстроке displayName (SUBTREE по BASE_DN) — сырые записи.

        Фильтр displayName=*<q>* — как требует план автосвязки; спецсимволы
        запроса экранируются (_escape_filter), чтобы не сломать фильтр."""
        return self._search_many(
            self._settings.base_dn,
            "(displayName=*{}*)".format(self._escape_filter(query)),
            self._ldap3.SUBTREE,
        )

    def search_group_by_cn(self, cn: str) -> Optional[Dict]:
        """Сырая запись группы по CN (SUBTREE по BASE_DN) либо None.

        Возвращает {"dn": ..., "members": [DN, ...]}; группа без участников —
        тоже запись (пустой список member), это не «не найдена»."""
        if self._conn is None:
            self.bind()
        self._conn.search(
            search_base=self._settings.base_dn,
            search_filter="(&(objectClass=group)(cn={}))".format(self._escape_filter(cn)),
            search_scope=self._ldap3.SUBTREE,
            attributes=list(self.GROUP_SEARCH_ATTRS),
        )
        for entry in self._conn.entries:
            return self._group_to_raw(entry)
        return None

    #: Атрибуты лёгкого перечисления для справочника должностей.
    TITLE_ATTRS = ("title", "userAccountControl")

    def list_user_titles(self) -> List[Dict]:
        """Все пользователи каталога (title + userAccountControl), постранично.

        Только чтение; фильтр — люди с заполненным title (компьютеры и записи
        без должности не нужны справочнику). Сбой каталога — AdUnavailable.
        Постраничность — cookie ldap3 (лимит сервера обычно 1000): без неё
        выгрузка обрезалась бы первой тысячей.
        """
        if self._conn is None:
            self.bind()
        rows: List[Dict] = []
        cookie = None
        try:
            while True:
                self._conn.search(
                    search_base=self._settings.base_dn,
                    search_filter="(&(objectClass=user)(!(objectClass=computer))(title=*))",
                    search_scope=self._ldap3.SUBTREE,
                    attributes=list(self.TITLE_ATTRS),
                    paged_size=1000,
                    paged_cookie=cookie,
                )
                rows.extend(self._to_raw_title(entry) for entry in self._conn.entries)
                result = getattr(self._conn, "result", None) or {}
                controls = result.get("controls", {}) or {}
                paged = controls.get("1.2.840.113556.1.4.319", {}) or {}
                cookie = (paged.get("value", {}) or {}).get("cookie")
                if not cookie:
                    break
        except Exception as exc:
            raise AdUnavailable(f"AD недоступен (перечисление должностей): {exc}") from exc
        return rows

    @staticmethod
    def _to_raw_title(entry: object) -> Dict:
        """Перевод ldap3-Entry в {title, userAccountControl} (как _to_raw)."""
        attrs = getattr(entry, "entry_attributes_as_dict", None)
        if not isinstance(attrs, dict):
            attrs = getattr(entry, "entry_attributes", {}) or {}
        if not isinstance(attrs, dict):
            attrs = {}
        title = attrs.get("title")
        if isinstance(title, (list, tuple)):
            title = title[0] if title else ""
        uac = attrs.get("userAccountControl")
        if isinstance(uac, (list, tuple)):
            uac = uac[0] if uac else ""
        return {"title": str(title) if title else "", "userAccountControl": uac}

    def _search(self, base_dn: str, filter_str: str, scope: object) -> Optional[Dict]:
        rows = self._search_many(base_dn, filter_str, scope)
        return rows[0] if rows else None

    def _search_many(self, base_dn: str, filter_str: str, scope: object) -> List[Dict]:
        if self._conn is None:
            # Ленивый bind RO-учеткой: шлюз читается самодостаточно (AD-блок
            # карточки, ручная привязка, автосвязка, worker), а не только после
            # явного bind_reader() в auth. Повторный bind не нужен — conn уже есть.
            self.bind()
        self._conn.search(
            search_base=base_dn,
            search_filter=filter_str,
            search_scope=scope,
            attributes=self.SEARCH_ATTRS,
        )
        return [self._to_raw(entry) for entry in self._conn.entries]

    @staticmethod
    def _to_raw(entry: object) -> Dict:
        """Перевод ldap3-Entry в словарь, ожидаемый parse_ldap_entry.

        ldap3 отдает атрибуты списками — скалярные поля схлопываем до строки,
        memberOf оставляем списком. Реальный ldap3: `entry_attributes` — список
        имен, значения — в `entry_attributes_as_dict`; фейк тестов кладет dict
        прямо в `entry_attributes` — поддерживаем оба варианта.
        """
        attrs = getattr(entry, "entry_attributes_as_dict", None)
        if not isinstance(attrs, dict):
            attrs = getattr(entry, "entry_attributes", {}) or {}
        if not isinstance(attrs, dict):
            attrs = {}
        raw: Dict = {"dn": str(getattr(entry, "entry_dn", ""))}
        for name in (
            "sAMAccountName",
            "displayName",
            "manager",
            "department",
            "title",
            "mail",
            "userAccountControl",
        ):
            value = attrs.get(name)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            raw[name] = str(value) if value else ""
        members = attrs.get("memberOf", []) or []
        if isinstance(members, str):
            members = [members]
        raw["memberOf"] = list(members)
        return raw

    @staticmethod
    def _group_to_raw(entry: object) -> Dict:
        """Перевод ldap3-Entry группы в {"dn", "members"} (список DN участников).

        Аналог _to_raw для групп: member — список (не схлопываем), варианты
        атрибутов фейка/настоящего ldap3 поддержаны так же, как в _to_raw.
        """
        attrs = getattr(entry, "entry_attributes_as_dict", None)
        if not isinstance(attrs, dict):
            attrs = getattr(entry, "entry_attributes", {}) or {}
        if not isinstance(attrs, dict):
            attrs = {}
        members = attrs.get("member", []) or []
        if isinstance(members, str):
            members = [members]
        return {
            "dn": str(getattr(entry, "entry_dn", "")),
            "members": [str(dn) for dn in members],
        }

    @staticmethod
    def _escape_filter(value: str) -> str:
        """Экранирование спецсимволов LDAP-фильтра (защита от инъекций)."""
        out = []
        for ch in str(value):
            if ch in "()*\\\x00":
                out.append("\\" + "".join(f"{ord(c):02x}" for c in ch))
            else:
                out.append(ch)
        return "".join(out)

    # -- проверка пароля пользователя -------------------------------------------
    def bind_user(self, dn: str, password: str) -> bool:
        """Проверка пароля bind'ом его же учеткой (только чтение, без записи).

        True — пара логин/пароль верна; False — неверная пара;
        AdUnavailable — каталог недоступен (для ответа 503).
        """
        try:
            _, conn = self._connect(dn, password)
        except Exception as exc:
            if self._is_bind_refused(exc):
                return False
            raise AdUnavailable(f"AD недоступен (bind пользователя): {exc}") from exc
        try:
            with conn:
                return bool(conn.bound)
        except Exception as exc:
            if self._is_bind_refused(exc):
                return False
            raise AdUnavailable(f"AD недоступен (bind пользователя): {exc}") from exc

    def _is_bind_refused(self, exc: Exception) -> bool:
        """Неверная пара логин/пароль — отказ каталога, а не его недоступность.

        Ловим классы ldap3, отвечающие за неверные учетные данные, плюс
        запасной признак — код результата 49 (invalidCredentials).
        """
        for name in (
            "LDAPInvalidCredentialsError",
            "LDAPInvalidCredentialsResult",
            "LDAPBindError",
        ):
            cls = getattr(self._ldap3, name, None)
            if cls is not None and isinstance(exc, cls):
                return True
        return getattr(exc, "result", None) == 49


# ---------------------------------------------------------------------------
# Кэш-интерфейс (TTL — из настроек)
# ---------------------------------------------------------------------------

class CacheBackend(Protocol):
    """Интерфейс кэша: боевая реализация — Redis, локальная — InMemoryCache."""

    def get(self, key: str) -> object:
        """Вернуть значение либо None при отсутствии/просрочке."""
        ...  # pragma: no cover

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        """Положить значение с TTL в секундах."""
        ...  # pragma: no cover

    def invalidate(self, key: str) -> None:
        """Сбросить ключ (кнопка «обновить»)."""
        ...  # pragma: no cover


class InMemoryCache:
    """Простой кэш в памяти с TTL (для тестов/одиночки; в бою — Redis)."""

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        # Часы инжектируются ради детерминированных тестов TTL.
        self._now = now or time.monotonic
        self._store: Dict[str, Tuple[object, float]] = {}

    def get(self, key: str) -> object:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if self._now() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        self._store[key] = (value, self._now() + max(0, ttl_seconds))

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Модель пользователя AD
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AdUser:
    """Карточка пользователя AD (только чтение)."""

    dn: str
    sam: str
    display_name: str
    manager_dn: str = ""
    department: str = ""
    title: str = ""
    member_of: Tuple[str, ...] = ()
    mail: str = ""
    enabled: bool = True


def _parse_enabled(raw: Dict) -> bool:
    """Разобрать userAccountControl: бит 2 (ACCOUNTDISABLE) означает отключена."""
    try:
        uac = int(raw.get("userAccountControl", 0))
    except (TypeError, ValueError):
        return True
    return not bool(uac & 0x2)


def parse_ldap_entry(raw: Dict) -> AdUser:
    """Перевести сырую LDAP-запись в AdUser (имена атрибутов — по схеме AD)."""
    member_of = raw.get("memberOf", []) or []
    if isinstance(member_of, str):
        member_of = [member_of]
    return AdUser(
        dn=str(raw.get("dn", "")),
        sam=str(raw.get("sAMAccountName", "")),
        display_name=str(raw.get("displayName", "")),
        manager_dn=str(raw.get("manager", "") or ""),
        department=str(raw.get("department", "") or ""),
        title=str(raw.get("title", "") or ""),
        member_of=tuple(member_of),
        mail=str(raw.get("mail", "") or ""),
        enabled=_parse_enabled(raw),
    )


def group_cn(group_dn: str) -> str:
    """Короткое имя группы из DN (CN=... — первый RDN)."""
    first = group_dn.split(",", 1)[0].strip()
    if first.lower().startswith("cn="):
        return first[3:]
    return group_dn


# ---------------------------------------------------------------------------
# Ридер (только чтение; методов записи нет намеренно)
# ---------------------------------------------------------------------------

_CACHE_PREFIX = "ad:user:"
# Карточки по DN лежат под отдельным префиксом, чтобы ключ sam никогда не
# совпал с ключом DN (в group_members резолвится много DN за один проход).
_DN_CACHE_PREFIX = "ad:user-dn:"
_MAX_MANAGER_DEPTH = 10


class AdReader:
    """Чтение AD через LDAPS с кэшем. Только чтение."""

    def __init__(
        self,
        settings: AdReaderSettings,
        gateway: LdapGateway,
        cache: CacheBackend,
    ) -> None:
        ensure_read_only()
        self._require_ldaps(settings.ad_url)
        if not settings.base_dn or not settings.reader_dn:
            raise AdReaderError(
                "BASE_DN и DN RO-учетки обязаны прийти из настроек/env."
            )
        self._settings = settings
        self._gateway = gateway
        self._cache = cache

    @staticmethod
    def _require_ldaps(ad_url: str) -> None:
        # Доступ — только LDAPS; обычный LDAP запрещен.
        if not ad_url.lower().startswith("ldaps://"):
            raise AdReaderError("AD доступен только по LDAPS (ldaps://...:636).")

    # -- bind ---------------------------------------------------------------
    def bind_reader(self) -> None:
        """Bind сервисной RO-учеткой. Живой bind — пометка «на стенде»."""
        try:
            self._gateway.bind()
        except AdReaderError:
            raise
        except Exception as exc:  # таймаут/сеть — понятная ошибка, не падение API
            raise AdUnavailable(f"AD недоступен (bind): {exc}") from exc

    # -- чтение --------------------------------------------------------------
    def _cache_key(self, sam: str) -> str:
        return f"{_CACHE_PREFIX}{sam.strip().lower()}"

    def get_user(self, sam: str) -> AdUser:
        """Карточка по sAMAccountName (сначала кэш с TTL из настроек)."""
        key = self._cache_key(sam)
        cached = self._cache.get(key)
        if isinstance(cached, AdUser):
            return cached
        try:
            raw = self._gateway.search_user_by_sam(sam)
        except Exception as exc:
            raise AdUnavailable(f"AD недоступен (поиск {sam!r}): {exc}") from exc
        if not raw:
            raise AdNotFound(f"Пользователь {sam!r} не найден (база: настройки BASE_DN).")
        user = parse_ldap_entry(raw)
        self._cache.set(key, user, self._settings.cache_ttl_seconds)
        return user

    def get_user_by_dn(self, dn: str) -> AdUser:
        """Карточка по DN (резолв manager-цепочки и участников группы).

        Успешный ответ кэшируется с тем же TTL, что и поиск по sAMAccountName:
        group_members зовёт метод на каждого участника, без кэша это
        O(участников) живых LDAPS-запросов. Не найденный DN не кэшируется."""
        key = f"{_DN_CACHE_PREFIX}{dn.strip().lower()}"
        cached = self._cache.get(key)
        if isinstance(cached, AdUser):
            return cached
        try:
            raw = self._gateway.search_user_by_dn(dn)
        except Exception as exc:
            raise AdUnavailable(f"AD недоступен (поиск по DN): {exc}") from exc
        if not raw:
            raise AdNotFound("Запись DN не найдена.")
        user = parse_ldap_entry(raw)
        self._cache.set(key, user, self._settings.cache_ttl_seconds)
        return user

    def search_users(self, query: str) -> List[AdUser]:
        """Кандидаты AD по подстроке ФИО (displayName) для стыковки 1С↔AD.

        Только чтение; пустой запрос — пустой список; сбой каталога — AdUnavailable
        (API падать не должен). Возвращаются только активные учётные записи —
        отключённые (userAccountControl) исключаются во всех местах поиска.
        Список сортируется по sAMAccountName."""
        q = (query or "").strip()
        if not q:
            return []
        try:
            rows = self._gateway.search_users(q)
        except Exception as exc:
            raise AdUnavailable(f"AD недоступен (поиск по имени): {exc}") from exc
        users = [parse_ldap_entry(row) for row in rows]
        users = [u for u in users if u.enabled]  # только активные (userAccountControl)
        users.sort(key=lambda u: u.sam.strip().lower())
        return users

    def refresh(self, sam: str) -> AdUser:
        """Кнопка «обновить»: сбросить кэш и перечитать."""
        self._cache.invalidate(self._cache_key(sam))
        return self.get_user(sam)

    def list_all_titles(self) -> List[str]:
        """Все должности каталога (title активных учёток) для справочника.

        Только чтение; приоритет справочника — AD (см. ad_groups_cache).
        Отключённые учётки исключаются, как в search_users; пустые титулы —
        пропускаются; точное написание схлопывается без учёта регистра
        (первый вариант); итог — сортированный. Сбой каталога — AdUnavailable.
        """
        try:
            rows = self._gateway.list_user_titles()
        except Exception as exc:
            raise AdUnavailable(f"AD недоступен (перечисление должностей): {exc}") from exc
        seen: set[str] = set()
        titles: List[str] = []
        for row in rows:
            title = str((row or {}).get("title") or "").strip()
            if not title:
                continue
            if not _parse_enabled(row):
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            titles.append(title)
        return sorted(titles)

    # -- manager-цепочка -------------------------------------------------------
    def resolve_manager_chain(self, sam: str) -> List[AdUser]:
        """Рекурсивный подъем по manager (с защитой от циклов и глубины)."""
        chain: List[AdUser] = []
        seen = set()
        current = self.get_user(sam)
        for _ in range(_MAX_MANAGER_DEPTH):
            manager_dn = current.manager_dn.strip()
            if not manager_dn or manager_dn.lower() in seen:
                break
            seen.add(manager_dn.lower())
            try:
                manager = self.get_user_by_dn(manager_dn)
            except AdNotFound:
                break
            chain.append(manager)
            current = manager
        return chain

    # -- группы -----------------------------------------------------------------
    def group_members(self, group: str) -> List[AdUser]:
        """Активные участники группы (только чтение) — адресаты уведомлений.

        Группа ищется по CN под BASE_DN (принимается и полный DN группы);
        пустое имя — [], группа не найдена — AdNotFound, сбой каталога —
        AdUnavailable. Отключённые учётные записи (userAccountControl)
        исключаются, как в search_users; порядок — по sAMAccountName."""
        name = (group or "").strip()
        if not name:
            return []
        if "," in name:
            name = group_cn(name).strip()
        try:
            raw = self._gateway.search_group_by_cn(name)
        except Exception as exc:
            raise AdUnavailable(
                f"AD недоступен (поиск группы {name!r}): {exc}"
            ) from exc
        if not raw:
            raise AdNotFound(f"Группа {name!r} не найдена (база: настройки BASE_DN).")
        members = raw.get("members") or []
        if isinstance(members, str):
            members = [members]
        users: List[AdUser] = []
        for dn in members:
            try:
                user = self.get_user_by_dn(str(dn))
            except AdNotFound:
                continue  # участник удалён из каталога — молча пропускаем
            if not user.enabled:
                continue  # только активные (userAccountControl)
            users.append(user)
        users.sort(key=lambda u: u.sam.strip().lower())
        return users

    def member_of(self, sam: str) -> Tuple[str, ...]:
        """Полные DN групп пользователя (фильтр групп — снаружи, из настроек)."""
        return self.get_user(sam).member_of

    def is_member_of(self, sam: str, group: str) -> bool:
        """Проверка членства: принимает полный DN либо короткое CN-имя."""
        wanted = group.strip()
        for dn in self.member_of(sam):
            if dn == wanted or group_cn(dn) == wanted:
                return True
        return False

    def has_any_group(self, sam: str, groups: List[str]) -> bool:
        """Есть ли у пользователя хоть одна группа из списка настроек."""
        return any(self.is_member_of(sam, g) for g in groups)


# ---------------------------------------------------------------------------
# Снапшот: при расхождении истина — 1С
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComparedField:
    """Одно поле снапшота: оба значения + признанная истина (всегда 1С)."""

    value_1c: str
    value_ad: str
    truth: str  # всегда значение 1С
    diverged: bool


@dataclass(frozen=True)
class EmployeeSnapshot:
    """Снапшот карточки: блок 1С + блок AD; истина при расхождении — 1С."""

    fio: ComparedField
    department: ComparedField
    title: ComparedField
    sam: str = ""
    manager_dn: str = ""
    mail: str = ""  # mail из AD — только для уведомлений, в бланк не печатать
    truth_source: str = "1c"
    verified_manually: bool = False  # решает ОК вручную (link_1c_ad)

    def divergences(self) -> List[str]:
        """Имена полей, где 1С и AD разошлись (на ручную сверку ОК)."""
        out = []
        for name in ("fio", "department", "title"):
            if getattr(self, name).diverged:
                out.append(name)
        return out


def _compare(one_c: str, ad: str) -> ComparedField:
    return ComparedField(
        value_1c=one_c,
        value_ad=ad,
        truth=one_c,  # истина — всегда 1С
        diverged=one_c.strip() != ad.strip(),
    )


def build_snapshot(snapshot_1c: Dict[str, str], ad_user: AdUser) -> EmployeeSnapshot:
    """Собрать снапшот: истина при любом расхождении — значения 1С.

    snapshot_1c — карточка из 1С (ключи fio/department/title);
    резолвер 1С↔AD — чужой (A3), сюда приходит готовый dict.
    """
    return EmployeeSnapshot(
        fio=_compare(str(snapshot_1c.get("fio", "")), ad_user.display_name),
        department=_compare(str(snapshot_1c.get("department", "")), ad_user.department),
        title=_compare(str(snapshot_1c.get("title", "")), ad_user.title),
        sam=ad_user.sam,
        manager_dn=ad_user.manager_dn,
        mail=ad_user.mail,
    )


#: Пустой список как напоминание: методы записи запрещены и отсутствуют.
#: Любая будущая AdLifecycle.disable — только за флагом и не в этом модуле.
READ_ONLY_NOTE = "AD_WRITE_ENABLED=false: модуль только читает AD."

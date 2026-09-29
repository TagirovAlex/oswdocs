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
    cache_ttl_seconds: int = 21600  # LDAP_CACHE_TTL, по умолчанию 6 ч (в окне 4–8 ч)
    timeout_seconds: float = 5.0  # таймаут LDAP-операций

    @classmethod
    def from_env(cls) -> "AdReaderSettings":
        """Собрать настройки из окружения (секреты — только env)."""
        ttl_raw = os.getenv("LDAP_CACHE_TTL", "21600").strip()
        timeout_raw = os.getenv("AD_TIMEOUT_SECONDS", "5").strip()
        return cls(
            ad_url=os.getenv("AD_URL", ""),
            base_dn=os.getenv("AD_BASE_DN", ""),
            reader_dn=os.getenv("AD_READER_DN", ""),
            cache_ttl_seconds=int(ttl_raw or 21600),
            timeout_seconds=float(timeout_raw or 5),
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
        """Карточка по DN (для резолва manager-цепочки)."""
        try:
            raw = self._gateway.search_user_by_dn(dn)
        except Exception as exc:
            raise AdUnavailable(f"AD недоступен (поиск по DN): {exc}") from exc
        if not raw:
            raise AdNotFound("Запись DN не найдена.")
        return parse_ldap_entry(raw)

    def refresh(self, sam: str) -> AdUser:
        """Кнопка «обновить»: сбросить кэш и перечитать."""
        self._cache.invalidate(self._cache_key(sam))
        return self.get_user(sam)

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

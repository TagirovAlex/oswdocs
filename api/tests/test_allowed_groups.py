# Сведение источников групп доступа: env-набор (bootstrap) и инфра-ключ
# access_groups из настроек БД дают ВХОД; контент-ключ allowed_ad_groups даёт
# только группы ручного конструктора шагов. БД недоступна — фолбэк на env.
# Группы вымышленные; продовые значения — только через env/settings.

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdUser  # noqa: E402
from app.auth import LdapAuthService  # noqa: E402
from app.config import Settings  # noqa: E402
from app.deps import _detect_role  # noqa: E402
from app.settings_routes import (  # noqa: E402
    SettingsUnavailable,
    is_group_allowed_with_settings,
    resolve_allowed_groups,
    resolve_role_groups,
    resolve_step_groups,
)


class _Store:
    """Мок хранилища настроек: сид-значения в памяти; broken — падение БД."""

    def __init__(self, data=None, broken=False):
        self._data = dict(data or {})
        self.broken = broken

    def get(self, key):
        if self.broken:
            raise SettingsUnavailable("хранилище недоступно (тест)")
        return self._data.get(key)

    def get_many(self, keys):
        if self.broken:
            raise SettingsUnavailable("хранилище недоступно (тест)")
        return {key: self._data.get(key) for key in keys}


def _settings(**over):
    base = {
        "ALLOWED_AD_GROUPS": "SED_HR",
        "STEP_GROUP_PREFIX": "SED_STEP_",
    }
    base.update(over)
    return Settings(**base)


def _role_settings(**over):
    """env-группы ролей заданы (bootstrap) — для проверок фолбэка ролей."""
    base = {
        "ADMIN_GROUPS": "SED_ADMINS",
        "HR_GROUPS": "SED_HR",
        "HR_ADMIN_GROUPS": "SED_HR_ADMIN",
    }
    base.update(over)
    return _settings(**base)


def _ad_user(*groups):
    return AdUser(
        dn="CN=user,OU=SED,DC=example,DC=local",
        sam="user",
        display_name="Пользователь Пример",
        member_of=tuple(groups),
    )


# --- Вход: env ∪ access_groups ---

def test_resolve_unions_env_and_access_groups_db():
    store = _Store({"access_groups": '["SED_DB_ONLY", "SED_HR"]'})
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR", "SED_DB_ONLY"}


def test_resolve_store_down_falls_back_to_env():
    store = _Store(broken=True)
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR"}


def test_resolve_without_store_only_env():
    assert resolve_allowed_groups(_settings(), None) == {"SED_HR"}


def test_resolve_ignores_malformed_list_items():
    store = _Store({"access_groups": '[" SED_A ", 7, "", "SED_B"]'})
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR", "SED_A", "SED_B"}


def test_resolve_ignores_allowed_ad_groups_for_entry():
    """Контент-ключ allowed_ad_groups входа НЕ расширяет (только конструктор)."""
    store = _Store({"allowed_ad_groups": '["SED_DB_ONLY"]'})
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR"}


def test_resolve_env_role_groups_still_allow_entry():
    """Bootstrap-группы ролей из env по-прежнему пускают (env = bootstrap)."""
    assert resolve_allowed_groups(_role_settings(), None) == {
        "SED_HR",
        "SED_ADMINS",
        "SED_HR_ADMIN",
    }


# --- Группы ручного конструктора шагов: allowed_ad_groups ---

def test_is_group_allowed_settings_and_prefix():
    store = _Store({"allowed_ad_groups": '["SED_DB_ONLY"]'})
    settings = _settings()
    assert is_group_allowed_with_settings(settings, store, "SED_DB_ONLY") is True
    assert is_group_allowed_with_settings(settings, store, "SED_STEP_ANY") is True
    assert is_group_allowed_with_settings(settings, store, "SED_UNKNOWN") is False
    assert is_group_allowed_with_settings(settings, store, "   ") is False


def test_is_group_allowed_ignores_access_groups_only():
    """access_groups (вход в систему) состав шага не открывает."""
    store = _Store({"access_groups": '["SED_DB_ONLY"]'})
    assert is_group_allowed_with_settings(_settings(), store, "SED_DB_ONLY") is False


def test_resolve_step_groups_from_content_key():
    store = _Store({"allowed_ad_groups": '["SED_DB_ONLY"]'})
    assert "SED_DB_ONLY" in resolve_step_groups(_settings(), store)


def test_resolve_step_groups_store_down_falls_back_to_env():
    store = _Store(broken=True)
    assert resolve_step_groups(_settings(), store) == resolve_step_groups(_settings(), None)


# --- Боевой вход (auth) ---

def test_auth_login_allows_group_from_access_groups(monkeypatch):
    """Боевой вход: группа только из access_groups настроек БД пускает."""
    monkeypatch.setattr(
        "app.settings_routes.DbSettingsStore",
        lambda url: _Store({"access_groups": '["SED_DB_ONLY"]'}),
    )
    service = LdapAuthService(reader=None, sessions=None, settings=_settings())
    assert service._has_allowed_group(_ad_user("SED_DB_ONLY")) is True
    assert service._has_allowed_group(_ad_user("SED_UNKNOWN")) is False
    # Разрешённая env-группа по-прежнему проходит.
    assert service._has_allowed_group(_ad_user("SED_HR")) is True


def test_auth_login_rejects_group_only_from_allowed_ad_groups(monkeypatch):
    """allowed_ad_groups входа не даёт: группа только конструктора — 401."""
    monkeypatch.setattr(
        "app.settings_routes.DbSettingsStore",
        lambda url: _Store({"allowed_ad_groups": '["SED_MANUAL_ONLY"]'}),
    )
    service = LdapAuthService(reader=None, sessions=None, settings=_settings())
    assert service._has_allowed_group(_ad_user("SED_MANUAL_ONLY")) is False


def test_auth_login_store_down_uses_env_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.settings_routes.DbSettingsStore", lambda url: _Store(broken=True)
    )
    service = LdapAuthService(reader=None, sessions=None, settings=_settings())
    assert service._has_allowed_group(_ad_user("SED_HR")) is True
    assert service._has_allowed_group(_ad_user("SED_DB_ONLY")) is False


# --- Группы ролей: БД → env-фолбэк ---

def test_resolve_role_groups_from_db():
    store = _Store({"admin_groups": '["SED_ADMINS_DB"]', "hr_groups": '["SED_HR_DB"]'})
    resolved = resolve_role_groups(_role_settings(), store)
    assert resolved["admin_groups"] == {"SED_ADMINS_DB"}
    assert resolved["hr_groups"] == {"SED_HR_DB"}
    # Ключа нет в БД — env (bootstrap).
    assert resolved["hr_admin_groups"] == {"SED_HR_ADMIN"}


def test_resolve_role_groups_db_value_does_not_extend_env():
    """Значение из БД — единственный источник: env не дополняет (иначе отзыв не работал бы)."""
    store = _Store({"admin_groups": '["SED_ADMINS_DB"]'})
    assert resolve_role_groups(_role_settings(), store)["admin_groups"] == {"SED_ADMINS_DB"}


def test_resolve_role_groups_empty_list_in_db_revokes_env():
    """Пустой список в БД = админ снял все группы (не откат на env)."""
    store = _Store({"admin_groups": "[]"})
    assert resolve_role_groups(_role_settings(), store)["admin_groups"] == set()


def test_resolve_role_groups_store_down_falls_back_to_env():
    store = _Store(broken=True)
    resolved = resolve_role_groups(_role_settings(), store)
    assert resolved["admin_groups"] == {"SED_ADMINS"}
    assert resolved["hr_groups"] == {"SED_HR"}


def test_resolve_role_groups_without_store_only_env():
    assert resolve_role_groups(_role_settings(), None) == {
        "admin_groups": {"SED_ADMINS"},
        "hr_groups": {"SED_HR"},
        "hr_admin_groups": {"SED_HR_ADMIN"},
    }


def test_detect_role_uses_db_role_groups():
    store = _Store({"admin_groups": '["SED_ADMINS_DB"]'})
    settings = _role_settings()
    assert _detect_role(["SED_ADMINS_DB"], settings, store) == "admin"
    # Ключ admin_groups есть в БД, поэтому env-группа админа роли не даёт.
    assert _detect_role(["SED_ADMINS"], settings, store) == "owner"


def test_detect_role_env_fallback_when_key_absent():
    store = _Store({"access_groups": '["SED_ENTRY"]'})
    settings = _role_settings()
    assert _detect_role(["SED_ADMINS"], settings, store) == "admin"
    assert _detect_role(["SED_HR_ADMIN"], settings, store) == "hr_admin"
    assert _detect_role(["SED_HR"], settings, store) == "hr"
    assert _detect_role(["SED_STEP_BUH"], settings, store) == "owner"


def test_detect_role_priority_preserved_with_db_groups():
    store = _Store({"admin_groups": '["A"]', "hr_admin_groups": '["H"]', "hr_groups": '["R"]'})
    settings = _role_settings()
    assert _detect_role(["H", "R"], settings, store) == "hr_admin"
    assert _detect_role(["A", "H", "R"], settings, store) == "admin"

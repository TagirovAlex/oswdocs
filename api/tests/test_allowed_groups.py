# Сведение источников разрешённых AD-групп (#9): env-набор и список
# allowed_ad_groups из настроек БД. БД недоступна — фолбэк на env.
# Группы вымышленные; продовые значения — только через env/settings.

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdUser  # noqa: E402
from app.auth import LdapAuthService  # noqa: E402
from app.config import Settings  # noqa: E402
from app.settings_routes import (  # noqa: E402
    SettingsUnavailable,
    is_group_allowed_with_settings,
    resolve_allowed_groups,
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


def _settings(**over):
    base = {
        "ALLOWED_AD_GROUPS": "SED_HR",
        "STEP_GROUP_PREFIX": "SED_STEP_",
    }
    base.update(over)
    return Settings(**base)


def _ad_user(*groups):
    return AdUser(
        dn="CN=user,OU=SED,DC=example,DC=local",
        sam="user",
        display_name="Пользователь Пример",
        member_of=tuple(groups),
    )


def test_resolve_unions_env_and_settings_db():
    store = _Store({"allowed_ad_groups": '["SED_DB_ONLY", "SED_HR"]'})
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR", "SED_DB_ONLY"}


def test_resolve_store_down_falls_back_to_env():
    store = _Store(broken=True)
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR"}


def test_resolve_without_store_only_env():
    assert resolve_allowed_groups(_settings(), None) == {"SED_HR"}


def test_resolve_ignores_malformed_list_items():
    store = _Store({"allowed_ad_groups": '[" SED_A ", 7, "", "SED_B"]'})
    assert resolve_allowed_groups(_settings(), store) == {"SED_HR", "SED_A", "SED_B"}


def test_is_group_allowed_settings_and_prefix():
    store = _Store({"allowed_ad_groups": '["SED_DB_ONLY"]'})
    settings = _settings()
    assert is_group_allowed_with_settings(settings, store, "SED_DB_ONLY") is True
    assert is_group_allowed_with_settings(settings, store, "SED_STEP_ANY") is True
    assert is_group_allowed_with_settings(settings, store, "SED_UNKNOWN") is False
    assert is_group_allowed_with_settings(settings, store, "   ") is False


def test_auth_login_allows_group_from_settings_db(monkeypatch):
    """Боевой вход: группа только из настроек БД (нет в env, без префикса) пускает."""
    monkeypatch.setattr(
        "app.settings_routes.DbSettingsStore",
        lambda url: _Store({"allowed_ad_groups": '["SED_DB_ONLY"]'}),
    )
    service = LdapAuthService(reader=None, sessions=None, settings=_settings())
    assert service._has_allowed_group(_ad_user("SED_DB_ONLY")) is True
    assert service._has_allowed_group(_ad_user("SED_UNKNOWN")) is False
    # Разрешённая env-группа по-прежнему проходит.
    assert service._has_allowed_group(_ad_user("SED_HR")) is True


def test_auth_login_store_down_uses_env_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.settings_routes.DbSettingsStore", lambda url: _Store(broken=True)
    )
    service = LdapAuthService(reader=None, sessions=None, settings=_settings())
    assert service._has_allowed_group(_ad_user("SED_HR")) is True
    assert service._has_allowed_group(_ad_user("SED_DB_ONLY")) is False

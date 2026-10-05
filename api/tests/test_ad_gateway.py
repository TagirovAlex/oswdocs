# СЭД, Волна A1: тесты живого шлюза Ldap3Gateway на фейковом ldap3-модуле.
#
# Никакой сети: ldap3 подменяется фейком через инъекцию (ldap3_module).
# Все ПДн — вымышленные. Запись в AD запрещена (AD_WRITE_ENABLED=false).

"""Тесты Ldap3Gateway: bind, поиск, разбор записей, bind_user, AD_TLS_VALIDATE."""

import os
import sys

import pytest

# Независимость от чужого conftest.py: кладем api/ в sys.path локально.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import (  # noqa: E402
    AdReaderError,
    AdReaderSettings,
    AdUnavailable,
    AdUser,
    AdWriteBlocked,
    Ldap3Gateway,
    parse_ldap_entry,
)

# ---------------------------------------------------------------------------
# Фейковый модуль ldap3 (без сети): каталог в памяти + проверка bind по паролям
# ---------------------------------------------------------------------------

BASE_DN = "DC=FIDELIO,DC=LOCAL"
READER_DN = "CN=oswdocs,CN=Users,DC=FIDELIO,DC=LOCAL"
USER_DN = "CN=Тестов Тест Тестович,OU=OSWDOCS,DC=FIDELIO,DC=LOCAL"
SED_HR = "CN=SED_HR,OU=OSWDOCS,DC=FIDELIO,DC=LOCAL"


class _FakeEntry:
    """ldap3-Entry: entry_dn + атрибуты списками (как их отдает настоящий ldap3)."""

    def __init__(self, dn, entry_attributes):
        self.entry_dn = dn
        self.entry_attributes = entry_attributes


class _FakeLdap3Module:
    """Фейк модуля ldap3: хранит каталог, проверяет bind по словарю паролей."""

    SUBTREE = "SUBTREE"
    BASE = "BASE"
    TLS_VALIDATE_NONE = 0
    TLS_VALIDATE_CERT = 1

    class LDAPInvalidCredentialsError(Exception):
        """Неверная пара логин/пароль (класс-тезка ldap3)."""

    class LDAPInvalidCredentialsResult(LDAPInvalidCredentialsError):
        """Реальный ldap3 с raise_exceptions поднимает именно этот класс."""

    class LDAPBindError(LDAPInvalidCredentialsError):
        """Базовый класс ошибок bind."""

    _current = None  # активный экземпляр: Server/Connection ходят через него

    def __init__(self, entries, credentials=None, fail_connect=False):
        _FakeLdap3Module._current = self
        self._entries = [dict(e) for e in entries]
        self.credentials = dict(credentials or {})
        self.fail_connect = fail_connect
        self.servers = []
        self.tls_list = []
        self.bind_attempts = []
        self.searches = []

    # -- классы ldap3, которые дергает шлюз ----------------------------------
    class Tls:
        def __init__(self, validate=0, **kwargs):
            self.validate = validate
            self.kwargs = kwargs

    class Server:
        def __init__(self, host, use_ssl=False, connect_timeout=None, tls=None):
            self.host = host
            self.use_ssl = use_ssl
            self.connect_timeout = connect_timeout
            self.tls = tls
            _FakeLdap3Module._current.servers.append(self)

    class Connection:
        def __init__(self, server, user="", password="", auto_bind=False,
                     raise_exceptions=False, receive_timeout=None):
            self.server = server
            self.user = user
            self.password = password
            self.raise_exceptions = raise_exceptions
            self.bound = False
            self.entries = []
            if auto_bind:
                self.bind()

        def bind(self):
            fake = _FakeLdap3Module._current
            fake.bind_attempts.append((self.user, self.password))
            if fake.fail_connect:
                raise TimeoutError("ad недоступен")
            if self.user in fake.credentials and fake.credentials[self.user] == self.password:
                self.bound = True
            elif self.raise_exceptions:
                raise _FakeLdap3Module.LDAPInvalidCredentialsResult("неверная пара")
            return self.bound

        def search(self, search_base, search_filter, search_scope, attributes):
            fake = _FakeLdap3Module._current
            fake.searches.append({
                "base": search_base,
                "filter": search_filter,
                "scope": search_scope,
                "attributes": list(attributes),
            })
            self.entries = fake._run_search(search_base, search_filter, search_scope)

        def unbind(self):
            self.bound = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.unbind()

    # -- каталог -------------------------------------------------------------
    def _run_search(self, search_base, search_filter, search_scope):
        wanted_sam = None
        if "(sAMAccountName=" in search_filter:
            wanted_sam = search_filter.split("(sAMAccountName=", 1)[1].rsplit(")", 1)[0].lower()
        wanted_name = None
        if "(displayName=*" in search_filter:
            # Фильтр (displayName=*<подстрока>*) — подстрока без звездочек.
            wanted_name = search_filter.split("(displayName=*", 1)[1].rsplit("*)", 1)[0].lower()
        wanted_cn = None
        if "(cn=" in search_filter:
            # Фильтр (&(objectClass=group)(cn=<CN>)) — имя группы до первой скобки.
            wanted_cn = search_filter.split("(cn=", 1)[1].split(")", 1)[0].lower()
        results = []
        for raw in self._entries:
            if search_scope == self.BASE:
                ok = raw["dn"].lower() == search_base.lower()
            else:
                ok = raw["dn"].lower().endswith(search_base.lower())
                if ok and wanted_sam is not None:
                    ok = raw["sAMAccountName"].lower() == wanted_sam
                if ok and wanted_name is not None:
                    ok = wanted_name in raw["displayName"].lower()
                if ok and wanted_cn is not None:
                    ok = raw["dn"].lower().startswith("cn=%s," % wanted_cn)
            if ok:
                results.append(self._to_ldap3_entry(raw))
        return results

    @staticmethod
    def _to_ldap3_entry(raw):
        attrs = {}
        for key, value in raw.items():
            if key == "dn":
                continue
            if isinstance(value, (list, tuple)):
                attrs[key] = list(value)
            else:
                attrs[key] = [str(value)] if value != "" else []
        return _FakeEntry(raw["dn"], attrs)


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------

def _entry(sam, fio, groups=(), manager="", dept="", title="", mail="", uac=512):
    return {
        "dn": f"CN={fio},OU=OSWDOCS,DC=FIDELIO,DC=LOCAL",
        "sAMAccountName": sam,
        "displayName": fio,
        "manager": manager,
        "memberOf": list(groups),
        "department": dept,
        "title": title,
        "mail": mail,
        "userAccountControl": uac,
    }


def _directory():
    return [
        _entry(
            "t.testov",
            "Тестов Тест Тестович",
            groups=[SED_HR],
            dept="Тестовый отдел",
            title="Тестовый специалист",
            mail="t.testov@fidelio.local",
        ),
        _entry(
            "a.primerova",
            "Примерова Анна Тестовая",
            manager=USER_DN,
            dept="Тестовый отдел",
            title="Тестовая начальница",
        ),
    ]


def _settings(**over):
    params = {
        "ad_url": "ldaps://DC1.FIDELIO.LOCAL:636",
        "base_dn": BASE_DN,
        "reader_dn": READER_DN,
        "reader_secret": "reader-secret-test",
        "cache_ttl_seconds": 21600,
        "timeout_seconds": 5.0,
        "tls_validate": False,
    }
    params.update(over)
    return AdReaderSettings(**params)


def _fake(entries=None, credentials=None, fail_connect=False):
    return _FakeLdap3Module(
        entries or _directory(),
        credentials=credentials or {READER_DN: "reader-secret-test"},
        fail_connect=fail_connect,
    )


def _gateway(fake=None, **over):
    return Ldap3Gateway(_settings(**over), ldap3_module=fake or _fake())


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

def test_bind_reader_then_search_by_sam():
    fake = _fake()
    gw = _gateway(fake)
    gw.bind()
    # RO-учетка bind'ится с секретом из настроек (AD_READER_SECRET).
    assert (READER_DN, "reader-secret-test") in fake.bind_attempts
    raw = gw.search_user_by_sam("t.testov")
    assert raw is not None
    assert raw["sAMAccountName"] == "t.testov"
    assert raw["displayName"] == "Тестов Тест Тестович"
    # Фильтр экранирован, поиск — SUBTREE по BASE_DN.
    search = fake.searches[-1]
    assert search["filter"] == "(sAMAccountName=t.testov)"
    assert search["base"] == BASE_DN
    assert search["scope"] == _FakeLdap3Module.SUBTREE
    # Атрибуты запрошены только разрешенные для чтения.
    assert set(search["attributes"]) == set(Ldap3Gateway.SEARCH_ATTRS)


def test_search_by_dn_uses_base_scope():
    fake = _fake()
    gw = _gateway(fake)
    gw.bind()
    raw = gw.search_user_by_dn(USER_DN)
    assert raw is not None
    assert raw["dn"] == USER_DN
    search = fake.searches[-1]
    assert search["base"] == USER_DN
    assert search["scope"] == _FakeLdap3Module.BASE


def test_search_missing_returns_none():
    gw = _gateway()
    gw.bind()
    assert gw.search_user_by_sam("net.takogo") is None
    assert gw.search_user_by_dn("CN=Nobody,DC=FIDELIO,DC=LOCAL") is None


def test_search_users_by_display_name():
    fake = _fake()
    gw = _gateway(fake)
    gw.bind()
    raw = gw.search_users("Тестов Тест")
    assert len(raw) == 1
    assert raw[0]["sAMAccountName"] == "t.testov"
    assert raw[0]["displayName"] == "Тестов Тест Тестович"
    # Фильтр: displayName=*<q>*, SUBTREE по BASE_DN, атрибуты — SEARCH_ATTRS.
    search = fake.searches[-1]
    assert search["filter"] == "(displayName=*Тестов Тест*)"
    assert search["base"] == BASE_DN
    assert search["scope"] == _FakeLdap3Module.SUBTREE
    assert set(search["attributes"]) == set(Ldap3Gateway.SEARCH_ATTRS)
    # Несколько совпадений по подстроке.
    assert len(gw.search_users("Тест")) == 2


def test_search_lazy_binds_reader():
    """Поиск без явного bind: шлюз сам связывается RO-учеткой (ленивый bind).

    AD-блок карточки, ручная привязка и автосвязка используют ридер из
    get_ad_reader() без bind_reader() — шлюз обязан быть самодостаточным."""
    fake = _fake()
    gw = _gateway(fake)
    raw = gw.search_user_by_sam("t.testov")
    assert raw is not None
    assert raw["sAMAccountName"] == "t.testov"
    # Ленивый bind выполнен RO-учеткой из настроек.
    assert (READER_DN, "reader-secret-test") in fake.bind_attempts


def test_gateway_raw_parses_into_ad_user():
    # Разбор: списки ldap3-атрибутов схлопываются в скаляры, memberOf — список.
    gw = _gateway()
    gw.bind()
    raw = gw.search_user_by_sam("t.testov")
    user = parse_ldap_entry(raw)
    assert isinstance(user, AdUser)
    assert user.sam == "t.testov"
    assert user.display_name == "Тестов Тест Тестович"
    assert user.department == "Тестовый отдел"
    assert user.title == "Тестовый специалист"
    assert user.mail == "t.testov@fidelio.local"
    assert user.member_of == (SED_HR,)
    assert user.enabled is True


def test_bind_user_correct_and_wrong_password():
    fake = _fake(credentials={USER_DN: "very-secret"})
    gw = _gateway(fake)
    assert gw.bind_user(USER_DN, "very-secret") is True
    assert gw.bind_user(USER_DN, "wrong-password") is False
    assert gw.bind_user("CN=Nobody,DC=FIDELIO,DC=LOCAL", "x") is False
    # Каждая проверка — отдельный bind пользовательской учеткой (без записи).
    assert (USER_DN, "very-secret") in fake.bind_attempts
    assert (USER_DN, "wrong-password") in fake.bind_attempts


def test_bind_user_unavailable_on_network_error():
    fake = _fake(credentials={USER_DN: "x"}, fail_connect=True)
    gw = _gateway(fake)
    with pytest.raises(AdUnavailable, match="AD недоступен"):
        gw.bind_user(USER_DN, "x")


def test_tls_validate_false_uses_none_cert_validation():
    import ssl  # реальный ldap3.Tls принимает ssl-режим (VerifyMode)

    fake = _fake()
    gw = _gateway(fake, tls_validate=False)
    gw.bind()
    tls = fake.servers[-1].tls
    assert tls.validate == ssl.CERT_NONE


def test_tls_validate_true_uses_cert_validation():
    import ssl

    fake = _fake()
    gw = _gateway(
        fake,
        tls_validate=True,
        ca_certs_file="/etc/nginx/certs/fidelio-root-ca.pem",
    )
    gw.bind()
    tls = fake.servers[-1].tls
    assert tls.validate == ssl.CERT_REQUIRED
    # Корневой CA проброшен в ldap3.Tls для проверки цепочки.
    assert tls.kwargs["ca_certs_file"] == "/etc/nginx/certs/fidelio-root-ca.pem"


def test_tls_validate_true_without_ca_raises():
    # AD_TLS_VALIDATE=true без AD_CA_CERT — ошибка конфигурации, не тихий CERT_NONE.
    fake = _fake()
    gw = _gateway(fake, tls_validate=True)
    with pytest.raises(AdReaderError, match="AD_CA_CERT"):
        gw.bind()


def test_settings_from_env_reads_secret_and_tls_flag(monkeypatch):
    monkeypatch.setenv("AD_URL", "ldaps://DC1.FIDELIO.LOCAL:636")
    monkeypatch.setenv("AD_BASE_DN", BASE_DN)
    monkeypatch.setenv("AD_READER_DN", READER_DN)
    monkeypatch.setenv("AD_READER_SECRET", "s3cret-on-vm")
    monkeypatch.setenv("AD_TLS_VALIDATE", "false")
    s1 = AdReaderSettings.from_env()
    assert s1.reader_secret == "s3cret-on-vm"
    assert s1.tls_validate is False
    monkeypatch.setenv("AD_TLS_VALIDATE", "true")
    assert AdReaderSettings.from_env().tls_validate is True


def test_gateway_blocks_write_flag(monkeypatch):
    monkeypatch.setenv("AD_WRITE_ENABLED", "true")
    with pytest.raises(AdWriteBlocked):
        _gateway()
    monkeypatch.setenv("AD_WRITE_ENABLED", "false")


def test_no_write_methods_on_gateway():
    # В шлюзе нет записи/удаления/отключения УЗ — только чтение и bind.
    for forbidden in ("add", "modify", "delete", "disable", "enable", "write", "create"):
        assert not hasattr(Ldap3Gateway, forbidden), forbidden


def test_ldap_filter_is_escaped():
    # Спецсимволы sAMAccountName не должны ломать фильтр (инъекция в LDAP).
    fake = _fake()
    gw = _gateway(fake)
    gw.bind()
    gw.search_user_by_sam("(evil)\\x")
    assert fake.searches[-1]["filter"] == "(sAMAccountName=\\28evil\\29\\5cx)"


# --- состав группы: только чтение, фильтр по CN под BASE_DN ---

def _group_raw(cn, members):
    return {
        "dn": "CN=%s,OU=OSWDOCS,DC=FIDELIO,DC=LOCAL" % cn,
        "cn": cn,
        "member": list(members),
    }


def test_search_group_by_cn_returns_members():
    """Поиск группы по CN (SUBTREE по BASE_DN) отдаёт состав (member)."""
    fake = _fake(_directory() + [_group_raw("SED_HR", [USER_DN])])
    gw = _gateway(fake)
    gw.bind()
    raw = gw.search_group_by_cn("SED_HR")
    assert raw is not None
    assert raw["dn"] == SED_HR
    assert raw["members"] == [USER_DN]
    # Фильтр и запрошенные атрибуты — только чтение, никакой записи.
    search = fake.searches[-1]
    assert search["filter"] == "(&(objectClass=group)(cn=SED_HR))"
    assert search["base"] == BASE_DN
    assert search["scope"] == _FakeLdap3Module.SUBTREE
    assert set(search["attributes"]) == set(Ldap3Gateway.GROUP_SEARCH_ATTRS)


def test_search_group_empty_and_missing():
    """Группа без участников — запись с пустым составом; нет группы — None."""
    fake = _fake(_directory() + [_group_raw("SED_EMPTY", [])])
    gw = _gateway(fake)
    gw.bind()
    empty = gw.search_group_by_cn("SED_EMPTY")
    assert empty is not None
    assert empty["members"] == []
    assert gw.search_group_by_cn("SED_NET_TAKOY") is None


def test_search_group_cn_is_escaped():
    fake = _fake(_directory() + [_group_raw("SED_HR", [USER_DN])])
    gw = _gateway(fake)
    gw.bind()
    gw.search_group_by_cn("(evil)")
    assert fake.searches[-1]["filter"] == "(&(objectClass=group)(cn=\\28evil\\29))"


class _PagedConn:
    """Фейк соединения ldap3 с постраничной выдачей (cookie в result)."""

    def __init__(self, pages):
        self._pages = [list(p) for p in pages]
        self.entries = []
        self.result = {}
        self.calls = []

    def search(self, search_base, search_filter, search_scope, attributes,
               paged_size=None, paged_cookie=None):
        self.calls.append({
            "base": search_base,
            "filter": search_filter,
            "scope": search_scope,
            "attributes": list(attributes),
            "paged_size": paged_size,
            "paged_cookie": paged_cookie,
        })
        page = self._pages.pop(0) if self._pages else []
        self.entries = page
        cookie = "next" if self._pages else None
        self.result = {
            "controls": {"1.2.840.113556.1.4.319": {"value": {"cookie": cookie}}}
        }


def _paged_gateway(pages):
    import types

    ldap3_stub = types.SimpleNamespace(SUBTREE="SUBTREE")
    gw = Ldap3Gateway(_settings(), ldap3_module=ldap3_stub)
    gw._conn = _PagedConn(pages)
    return gw


def _title_entry(title, uac=512):
    return _FakeEntry(
        "CN=%s,OU=OSWDOCS,DC=FIDELIO,DC=LOCAL" % title,
        {"title": [title], "userAccountControl": [str(uac)]},
    )


def test_list_user_titles_paged():
    """Перечисление титулов идёт постранично (cookie) до пустого cookie."""
    gw = _paged_gateway([
        [_title_entry("Бухгалтер"), _title_entry("Кассир")],
        [_title_entry("Бухгалтер")],
    ])
    rows = gw.list_user_titles()
    assert len(gw._conn.calls) == 2
    assert gw._conn.calls[0]["paged_size"] == 1000
    assert "(title=*)" in gw._conn.calls[0]["filter"]
    assert [r["title"] for r in rows] == ["Бухгалтер", "Кассир", "Бухгалтер"]

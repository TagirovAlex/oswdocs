# СЭД, Волна A4: тесты AD-ридера на фейковом LDAP (только чтение).
#
# Все персоналии вымышлены. Живой bind — пометка «на стенде».

"""Тесты ad_reader: фейковые manager-цепочки, группы SED_*, истина — 1С."""

import os
import sys

import pytest

# Независимость от чужого conftest.py (A2): кладем api/ в sys.path локально.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import (  # noqa: E402
    AdNotFound,
    AdReader,
    AdReaderError,
    AdReaderSettings,
    AdUnavailable,
    AdUser,
    AdWriteBlocked,
    InMemoryCache,
    build_snapshot,
    ensure_read_only,
    group_cn,
)


# ---------------------------------------------------------------------------
# Фейковый LDAP (локальные фикстуры, вымышленные ПДн)
# ---------------------------------------------------------------------------

BASE_DN = "OU=SED,DC=example,DC=local"  # тестовое значение настройки BASE_DN
READER_DN = "CN=sed-reader,OU=SED,DC=example,DC=local"  # тестовый DN RO-учетки
SED_HR = "CN=SED_HR,OU=SED,DC=example,DC=local"
SED_ADMINS = "CN=SED_ADMINS,OU=SED,DC=example,DC=local"
SED_STEP_BUH = "CN=SED_STEP_BUH,OU=SED,DC=example,DC=local"


def _entry(sam, fio, manager="", groups=(), dept="", title="", mail="", uac=512):
    return {
        "dn": f"CN={fio},OU=SED,DC=example,DC=local",
        "sAMAccountName": sam,
        "displayName": fio,
        "manager": manager,
        "memberOf": list(groups),
        "department": dept,
        "title": title,
        "mail": mail,
        "userAccountControl": uac,
    }


class FakeLdapGateway:
    """Фейк границы LDAP: цепочки manager + группы SED_*."""

    def __init__(self, entries, fail_with=None, groups=None):
        self._by_sam = {e["sAMAccountName"].lower(): dict(e) for e in entries}
        self._by_dn = {e["dn"].lower(): dict(e) for e in entries}
        self._groups = {cn.lower(): dict(g) for cn, g in (groups or {}).items()}
        self._fail_with = fail_with
        self.bind_calls = 0
        self.search_calls = 0

    def bind(self):
        self.bind_calls += 1
        if self._fail_with is not None:
            raise self._fail_with

    def search_user_by_sam(self, sam):
        self.search_calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        found = self._by_sam.get(sam.strip().lower())
        return dict(found) if found else None

    def search_user_by_dn(self, dn):
        self.search_calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        found = self._by_dn.get(dn.strip().lower())
        return dict(found) if found else None

    def search_users(self, query):
        self.search_calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            dict(e)
            for e in self._by_sam.values()
            if needle in e["displayName"].lower()
        ]

    def search_group_by_cn(self, cn):
        self.search_calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        found = self._groups.get(cn.strip().lower())
        return dict(found) if found else None

    def mutate(self, sam, **kwargs):
        key = sam.strip().lower()
        self._by_sam[key].update(kwargs)
        self._by_dn[self._by_sam[key]["dn"].lower()].update(kwargs)


def _directory():
    # Вымышленные сотрудники: рядовой -> начальница -> директор (верх цепочки).
    top_dn = "CN=Директоров Выдуман Примерович,OU=SED,DC=example,DC=local"
    mid_dn = "CN=Примерова Анна Тестовая,OU=SED,DC=example,DC=local"
    return [
        _entry(
            "t.testov",
            "Тестов Тест Тестович",
            manager=mid_dn,
            groups=[SED_HR, SED_STEP_BUH],
            dept="Тестовый отдел",
            title="Тестовый специалист",
            mail="t.testov@example.local",
        ),
        _entry(
            "a.primerova",
            "Примерова Анна Тестовая",
            manager=top_dn,
            groups=[SED_HR, SED_ADMINS],
            dept="Тестовый отдел",
            title="Тестовая начальница",
            mail="a.primerova@example.local",
        ),
        _entry(
            "v.directorov",
            "Директоров Выдуман Примерович",
            manager="",
            groups=[SED_ADMINS],
            dept="Руководство",
            title="Выдуманный директор",
            mail="v.directorov@example.local",
        ),
        _entry(
            "p.postoronny",
            "Посторонний Петр Посторонович",
            manager="",
            groups=[],
            dept="Сторонний отдел",
            title="Сторонний наблюдатель",
            mail="p.postoronny@example.local",
        ),
    ]


def _group(cn, entries, *sams):
    """Запись группы AD: member — DN участников из каталога (по логинам)."""
    by_sam = {e["sAMAccountName"].lower(): e for e in entries}
    return {
        "dn": "CN=%s,%s" % (cn, BASE_DN),
        "cn": cn,
        "members": [by_sam[s.lower()]["dn"] for s in sams],
    }


def _settings(**over):
    params = {
        "ad_url": "ldaps://ad.example.local:636",
        "base_dn": BASE_DN,
        "reader_dn": READER_DN,
        "cache_ttl_seconds": 21600,
        "timeout_seconds": 5.0,
    }
    params.update(over)
    return AdReaderSettings(**params)


def _reader(entries=None, groups=None, **over):
    return AdReader(
        _settings(),
        FakeLdapGateway(entries or _directory(), groups=groups),
        InMemoryCache(),
    )


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------

def test_bind_and_read_user_fields():
    gateway = FakeLdapGateway(_directory())
    reader = AdReader(_settings(), gateway, InMemoryCache())
    reader.bind_reader()
    user = reader.get_user("t.testov")
    assert gateway.bind_calls == 1
    assert user.sam == "t.testov"
    assert user.display_name == "Тестов Тест Тестович"
    assert user.department == "Тестовый отдел"
    assert user.title == "Тестовый специалист"
    assert user.mail == "t.testov@example.local"
    assert user.manager_dn == "CN=Примерова Анна Тестовая,OU=SED,DC=example,DC=local"
    assert SED_HR in user.member_of
    assert user.enabled is True


def test_manager_chain_resolves_recursively():
    reader = _reader()
    chain = reader.resolve_manager_chain("t.testov")
    assert [u.sam for u in chain] == ["a.primerova", "v.directorov"]
    # Верх цепочки: дальше подниматься некуда.
    assert reader.resolve_manager_chain("v.directorov") == []


def test_manager_cycle_does_not_loop():
    dn_a = "CN=Кольцов Кольцо Кольцевич,OU=SED,DC=example,DC=local"
    dn_b = "CN=Петлев Петля Петлевич,OU=SED,DC=example,DC=local"
    entries = [
        _entry("k.kolcow", "Кольцов Кольцо Кольцевич", manager=dn_b),
        _entry("p.petlev", "Петлев Петля Петлевич", manager=dn_a),
    ]
    reader = _reader(entries)
    chain = reader.resolve_manager_chain("k.kolcow")
    assert [u.sam for u in chain] == ["p.petlev", "k.kolcow"]


def test_memberof_groups_sed():
    reader = _reader()
    # ОК из SED_HR проходит, посторонний — нет.
    assert reader.is_member_of("t.testov", SED_HR) is True
    assert reader.is_member_of("t.testov", "SED_HR") is True  # короткое CN-имя
    assert reader.is_member_of("p.postoronny", SED_HR) is False
    # Отметку шага ставит любой член owner_group (SED_STEP_BUH).
    assert reader.has_any_group("t.testov", [SED_STEP_BUH]) is True
    assert reader.has_any_group("p.postoronny", [SED_STEP_BUH]) is False
    assert group_cn(SED_STEP_BUH) == "SED_STEP_BUH"


def test_cache_ttl_from_settings():
    gateway = FakeLdapGateway(_directory())
    cache = InMemoryCache()
    reader = AdReader(_settings(cache_ttl_seconds=21600), gateway, cache)
    first = reader.get_user("t.testov")
    assert gateway.search_calls == 1
    # Меняем каталог: второй вызов обязан отдать кэш (TTL из настроек).
    gateway.mutate("t.testov", title="Измененная должность")
    second = reader.get_user("t.testov")
    assert second.title == first.title == "Тестовый специалист"
    assert gateway.search_calls == 1
    # Кнопка «обновить» сбрасывает кэш.
    third = reader.refresh("t.testov")
    assert third.title == "Измененная должность"


def test_cache_expires_after_ttl():
    now = [1000.0]
    gateway = FakeLdapGateway(_directory())
    cache = InMemoryCache(now=lambda: now[0])
    reader = AdReader(_settings(cache_ttl_seconds=60), gateway, cache)
    reader.get_user("t.testov")
    gateway.mutate("t.testov", title="Новая должность")
    now[0] += 61.0  # TTL истек
    assert reader.get_user("t.testov").title == "Новая должность"


def test_fio_divergence_truth_is_1c_snapshot():
    # При расхождении ФИО истина — снапшот 1С; оба значения видны.
    reader = _reader()
    ad_user = reader.get_user("t.testov")
    snapshot_1c = {
        "fio": "Тестов Тест Тестович (1С)",
        "department": "Тестовый отдел",
        "title": "Тестовый специалист",
    }
    snap = build_snapshot(snapshot_1c, ad_user)
    assert snap.truth_source == "1c"
    assert snap.fio.diverged is True
    assert snap.fio.truth == "Тестов Тест Тестович (1С)"
    assert snap.fio.value_ad == "Тестов Тест Тестович"
    assert snap.divergences() == ["fio"]
    # Совпавшие поля расхождением не считаются.
    assert snap.department.diverged is False
    assert snap.department.truth == "Тестовый отдел"


def test_unknown_user_raises_not_found():
    reader = _reader()
    with pytest.raises(AdNotFound):
        reader.get_user("net.takogo")


def test_search_users_by_name_substring():
    """Поиск по подстроке displayName: кандидаты для автосвязки 1С↔AD."""
    reader = _reader()
    hits = reader.search_users("Тестов Тест")
    assert [u.sam for u in hits] == ["t.testov"]
    assert hits[0].display_name == "Тестов Тест Тестович"
    # Несколько кандидатов: сортировка по sam (детерминированный порядок).
    hits = reader.search_users("Директоров")
    assert [u.sam for u in hits] == ["v.directorov"]


def test_search_users_empty_query_returns_empty():
    reader = _reader()
    assert reader.search_users("") == []
    assert reader.search_users("   ") == []


def test_search_users_ad_unavailable():
    gateway = FakeLdapGateway(_directory(), fail_with=TimeoutError("ldap timeout"))
    reader = AdReader(_settings(), gateway, InMemoryCache())
    with pytest.raises(AdUnavailable, match="AD недоступен"):
        reader.search_users("Тестов")


def test_ad_failure_raises_unavailable_not_crash():
    # Падение AD — понятная ошибка, API не падает.
    gateway = FakeLdapGateway(_directory(), fail_with=TimeoutError("ldap timeout"))
    reader = AdReader(_settings(), gateway, InMemoryCache())
    with pytest.raises(AdUnavailable, match="AD недоступен"):
        reader.bind_reader()
    with pytest.raises(AdUnavailable, match="AD недоступен"):
        reader.get_user("t.testov")


def test_only_ldaps_allowed():
    with pytest.raises(AdReaderError, match="LDAPS"):
        AdReader(
            _settings(ad_url="ldap://ad.example.local:389"),
            FakeLdapGateway(_directory()),
            InMemoryCache(),
        )


def test_no_write_methods_and_flag_guard(monkeypatch):
    # В ридере нет записи/удаления/отключения УЗ даже в виде заглушек.
    for forbidden in ("add", "modify", "delete", "disable", "enable", "write", "create"):
        assert not hasattr(AdReader, forbidden), forbidden
    # Флаг записи обязан оставаться false; включенный — стоп-ошибка.
    monkeypatch.setenv("AD_WRITE_ENABLED", "false")
    ensure_read_only()
    monkeypatch.setenv("AD_WRITE_ENABLED", "true")
    with pytest.raises(AdWriteBlocked):
        ensure_read_only()
    with pytest.raises(AdWriteBlocked):
        AdReader(_settings(), FakeLdapGateway(_directory()), InMemoryCache())
    monkeypatch.setenv("AD_WRITE_ENABLED", "false")


def test_disabled_account_visible_as_disabled():
    entries = _directory()
    for e in entries:
        if e["sAMAccountName"] == "p.postoronny":
            e["userAccountControl"] = 514  # бит ACCOUNTDISABLE
    reader = _reader(entries)
    assert reader.get_user("p.postoronny").enabled is False


def test_search_users_filters_disabled_accounts():
    """Поиск кандидатов возвращает только активные учётные записи."""
    entries = _directory()
    for e in entries:
        if e["sAMAccountName"] == "p.postoronny":
            e["userAccountControl"] = 514  # бит ACCOUNTDISABLE
    reader = _reader(entries)
    # Отключенная запись в результаты поиска не попадает.
    assert reader.search_users("Посторонний") == []
    # Активная — попадает.
    hits = reader.search_users("Тестов Тест")
    assert [u.sam for u in hits] == ["t.testov"]
    assert hits[0].enabled is True


def test_ad_user_model_defaults():
    user = AdUser(dn="dn", sam="s", display_name="ФИО Вымышленное")
    assert user.manager_dn == ""
    assert user.member_of == ()
    assert user.enabled is True


# ---------------------------------------------------------------------------
# Состав группы (group_members) — адресаты уведомлений
# ---------------------------------------------------------------------------

def test_group_members_returns_active_sorted():
    """Участники группы по member: только активные, порядок по sAMAccountName."""
    entries = _directory()
    groups = {"SED_STEP_BUH": _group("SED_STEP_BUH", entries, "t.testov", "a.primerova")}
    reader = _reader(entries, groups=groups)
    members = reader.group_members("SED_STEP_BUH")
    assert [u.sam for u in members] == ["a.primerova", "t.testov"]
    assert members[0].mail == "a.primerova@example.local"
    assert members[0].enabled is True


def test_group_members_filters_disabled_and_deleted():
    """Отключённые учётные записи и удалённые из каталога в рассылку не попадают."""
    entries = _directory()
    for e in entries:
        if e["sAMAccountName"] == "p.postoronny":
            e["userAccountControl"] = 514  # бит ACCOUNTDISABLE
    gone_dn = "CN=Удален Удалёнович,OU=SED,DC=example,DC=local"
    groups = {
        "SED_STEP_BUH": _group("SED_STEP_BUH", entries, "p.postoronny", "t.testov"),
        "SED_STEP_OTHER": {
            "dn": "CN=SED_STEP_OTHER,%s" % BASE_DN,
            "cn": "SED_STEP_OTHER",
            "members": [gone_dn, entries[0]["dn"]],
        },
    }
    reader = _reader(entries, groups=groups)
    assert [u.sam for u in reader.group_members("SED_STEP_BUH")] == ["t.testov"]
    # Несуществующий участник пропускается молча, ошибки не поднимает.
    assert [u.sam for u in reader.group_members("SED_STEP_OTHER")] == ["t.testov"]


def test_group_members_empty_group_and_empty_name():
    """Группа без участников и пустое имя — пустой список, не ошибка."""
    entries = _directory()
    groups = {"SED_STEP_EMPTY": _group("SED_STEP_EMPTY", entries)}
    reader = _reader(entries, groups=groups)
    assert reader.group_members("SED_STEP_EMPTY") == []
    assert reader.group_members("") == []
    assert reader.group_members("   ") == []


def test_group_members_unknown_group_raises_not_found():
    reader = _reader(groups={"SED_STEP_BUH": _group("SED_STEP_BUH", _directory())})
    with pytest.raises(AdNotFound, match="не найдена"):
        reader.group_members("SED_STEP_NET_TAKOY")


def test_group_members_accepts_full_group_dn():
    """Полный DN группы тоже принимается: берётся CN из настройки шага."""
    entries = _directory()
    groups = {"SED_STEP_BUH": _group("SED_STEP_BUH", entries, "t.testov")}
    reader = _reader(entries, groups=groups)
    members = reader.group_members("CN=SED_STEP_BUH,%s" % BASE_DN)
    assert [u.sam for u in members] == ["t.testov"]


def test_group_members_ad_unavailable():
    gateway = FakeLdapGateway(
        _directory(), fail_with=TimeoutError("ldap timeout"), groups={"SED_STEP_BUH": {"dn": "", "members": []}}
    )
    reader = AdReader(_settings(), gateway, InMemoryCache())
    with pytest.raises(AdUnavailable, match="AD недоступен"):
        reader.group_members("SED_STEP_BUH")


def test_get_user_by_dn_cached_between_calls():
    """Карточка по DN кэшируется: повторный резолв не идёт в LDAP.

    Без кэша group_members делал бы живой LDAPS-запрос на каждого участника
    (O(участников) на вызов, в том числе из worker-напоминаний).
    """
    entries = _directory()
    gateway = FakeLdapGateway(entries)
    reader = AdReader(_settings(), gateway, InMemoryCache())
    dn = entries[0]["dn"]
    first = reader.get_user_by_dn(dn)
    assert gateway.search_calls == 1
    second = reader.get_user_by_dn(dn)
    assert gateway.search_calls == 1, "повторный резолв должен брать кэш"
    assert second.sam == first.sam


def test_get_user_by_dn_does_not_cache_missing():
    """Не найденный по DN не кэшируется: поиск повторяется (запись могла появиться)."""
    entries = _directory()
    gateway = FakeLdapGateway(entries)
    reader = AdReader(_settings(), gateway, InMemoryCache())
    with pytest.raises(AdNotFound):
        reader.get_user_by_dn("CN=Нет Такого,OU=SED,DC=example,DC=local")
    with pytest.raises(AdNotFound):
        reader.get_user_by_dn("CN=Нет Такого,OU=SED,DC=example,DC=local")
    assert gateway.search_calls == 2


class _TitlesGateway:
    """Шлюз-заглушка перечисления титулов (сырые записи title+uac)."""

    def __init__(self, rows=None, fail=None):
        self._rows = rows or []
        self._fail = fail

    def list_user_titles(self):
        if self._fail is not None:
            raise self._fail
        return self._rows


def test_list_all_titles_sorted_unique_enabled_only():
    """Титулы: уникальные, сортированные; отключённые и пустые — мимо."""
    gateway = _TitlesGateway([
        {"title": "Кассир", "userAccountControl": 512},
        {"title": "Бухгалтер", "userAccountControl": 512},
        {"title": "Бухгалтер", "userAccountControl": 512},
        {"title": "", "userAccountControl": 512},
        {"title": "Уволенный", "userAccountControl": 514},
    ])
    reader = AdReader(_settings(), gateway, InMemoryCache())
    assert reader.list_all_titles() == ["Бухгалтер", "Кассир"]


def test_list_all_titles_failure():
    """Сбой перечисления — AdUnavailable (справочник не роняет синк молча)."""
    gateway = _TitlesGateway(fail=TimeoutError("ldap timeout"))
    reader = AdReader(_settings(), gateway, InMemoryCache())
    with pytest.raises(AdUnavailable):
        reader.list_all_titles()

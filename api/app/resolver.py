"""сценарий предприятие→база→сотрудник поверх OneCClient (только чтение).

Правила:
- Предприятие выбирается из настроек (индекс предприятие→базы), веерный опрос
  привязанных баз с таймаутом 5с на базу (таймаут — внутри OneCClient).
- Падение одной базы не валит остальные: ошибки базы собираются в список,
  опрос продолжается; исключение — только если не найдено нигде.
- Ключ карточки — составной enterprise+base_code+tab_num (таб. пересекаются).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .onec_client import EmployeeCard, OneCClient, OneCNotFound, OneCUnknownBase


class UnknownEnterpriseError(Exception):
    """Предприятие не описано в настройках баз."""


class EmployeeNotFoundError(Exception):
    """Сотрудник не найден ни в одной базе предприятия."""

    def __init__(self, enterprise: str, tab_num: str, errors: List[str]):
        self.enterprise = enterprise
        self.tab_num = tab_num
        self.errors = list(errors)
        super().__init__(
            "таб. %r не найден в предприятии %r (ошибок баз: %d)"
            % (tab_num, enterprise, len(errors))
        )


@dataclass
class ResolveResult:
    """Итог разрешения: карточка (если найдена) + ошибки упавших баз."""

    card: Optional[EmployeeCard]
    errors: List[str] = field(default_factory=list)
    # Все совпадения поиска (для search_enterprise); у resolve_employee — пусто.
    cards: List[EmployeeCard] = field(default_factory=list)

    @property
    def found(self) -> bool:
        """Найдена ли карточка хотя бы в одной базе."""
        return self.card is not None


def enterprise_bases(enterprise: str, client: OneCClient) -> List[str]:
    """Коды баз, привязанных к предприятию (из конфигурации клиента)."""
    return client.bases_for_enterprise(enterprise)


def resolve_employee(enterprise: str, tab_num: str, client: OneCClient) -> ResolveResult:
    """Найти сотрудника по предприятию и таб. номеру (веер по базам предприятия).

    Не бросает исключение при падении отдельных баз — копит их текст в errors.
    Бросает UnknownEnterpriseError (нет такого предприятия) и
    EmployeeNotFoundError (нигде не найден, включая случай «все базы упали»).
    """
    codes = enterprise_bases(enterprise, client)
    if not codes:
        raise UnknownEnterpriseError("предприятие %r не привязано ни к одной базе" % enterprise)
    errors: List[str] = []
    for code in codes:
        try:
            card = client.get_employee(code, tab_num, enterprise)
        except OneCNotFound:
            continue  # в этой базе нет — идём к следующей, это не падение
        except OneCUnknownBase as exc:
            errors.append("%s: %s" % (code, exc))
            continue
        except Exception as exc:  # падение базы: изолируем, опрос продолжаем
            errors.append("%s: %s: %s" % (code, type(exc).__name__, exc))
            continue
        return ResolveResult(card=card, errors=errors)
    raise EmployeeNotFoundError(enterprise, tab_num, errors)


def search_enterprise(enterprise: str, query: str, client: OneCClient) -> ResolveResult:
    """Поиск по ФИО в пределах предприятия: собрать совпадения со всех живых баз.

    Возвращает первую непустую выборку как карточку-заглушку? Нет: возвращает
    список через поле cards. Для совместимости с resolve_employee поле card —
    первое совпадение либо None. Падение баз — в errors, остальные опрашиваются.
    """
    codes = enterprise_bases(enterprise, client)
    if not codes:
        raise UnknownEnterpriseError("предприятие %r не привязано ни к одной базе" % enterprise)
    errors: List[str] = []
    found: List[EmployeeCard] = []
    for code in codes:
        try:
            found.extend(client.search(code, query, enterprise))
        except Exception as exc:  # падение базы: изолируем, опрос продолжаем
            errors.append("%s: %s: %s" % (code, type(exc).__name__, exc))
            continue
    first = found[0] if found else None
    # Список всех совпадений — штатным полем результата.
    return ResolveResult(card=first, errors=errors, cards=found)


# Снапшот карточки 1С в заявке: истина — 1С, протухший (>24 ч) помечать.
SNAPSHOT_TTL_HOURS = 24


def make_snapshot_1c(card: EmployeeCard, now: Optional[_dt.datetime] = None) -> Dict[str, object]:
    """Слепок карточки 1С для хранения в заявке (snapshot_1c)."""
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    return {
        "enterprise": card.enterprise,
        "base_code": card.base_code,
        "tab_num": card.tab_num,
        "key": card.key(),
        "fio": card.fio,
        "dept": card.dept,
        "position": card.position,
        "employment_type": card.employment_type,
        "hire_date": card.hire_date,
        "fetched_at": moment.isoformat(),
    }


def is_snapshot_stale(snapshot: Dict[str, object], now: Optional[_dt.datetime] = None) -> bool:
    """Протух ли снапшот (старше SNAPSHOT_TTL_HOURS)."""
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    raw = snapshot.get("fetched_at", "")
    try:
        fetched = _dt.datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=_dt.timezone.utc)
    return (moment - fetched) > _dt.timedelta(hours=SNAPSHOT_TTL_HOURS)

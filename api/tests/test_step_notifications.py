# Уведомления шага с несколькими ответственными (ТЗ BLANK_CONTRACT_STEPS, п.2):
# письмо «назначена» уходит ВСЕМ ответственным снимка (assignees), а не только
# первому; у одного ответственного письмо одно (без дублей), уже отметившийся
# повторно не уведомляется, групповые шаги (owner_group) ведут себя как раньше.
# Юнит-тесты mailer.step_owner_mails на заглушках AD (только чтение) — как в
# test_docs_mail.py. Все логины и почты вымышленные.

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mailer import (  # noqa: E402
    EVENT_ASSIGNED,
    FileMailQueue,
    step_logins,
    step_marked_sams,
    step_owner_mails,
)

DOMAIN = "example.local"
# Три ответственных реестра (снимок assignees) — ФИО/почты вымышленные.
ROSTER = [
    "reestr.pervyy",
    "reestr.vtoroy",
    "reestr.tretiy",
]
ROSTER_OFF = "reestr.povyshennyi"
STEP_GROUP = "SED_STEP_BUH"
TEMPLATES = [
    {
        "code": EVENT_ASSIGNED,
        "subject": "Назначено {{ request_id }}",
        "body_html": "<html>{{ fio }} {{ url }}</html>",
    }
]


def _mail(sam: str) -> str:
    return "%s@%s" % (sam, DOMAIN)


def _step(**kw):
    """Шаг-заглушка: снимок ответственных и отметок задаётся полями."""
    fields = dict(assignee=None, assignees=[], approvals=[], owner_group=None)
    fields.update(kw)
    return SimpleNamespace(**fields)


class FakeAdReader:
    """Заглушка AD (только чтение): карточки по логину, состав группы — задан."""

    def __init__(self, users=None, members=None, fail=False):
        self.users = users if users is not None else {
            sam: SimpleNamespace(sam=sam, mail=_mail(sam), manager_dn="")
            for sam in ROSTER
        }
        self.members = members or []
        self.fail = fail

    def get_user(self, sam):
        if self.fail:
            raise RuntimeError("AD недоступен")
        return self.users.get(sam)

    def group_members(self, group):
        if self.fail:
            raise RuntimeError("AD недоступен")
        return self.members


def _reader() -> FakeAdReader:
    """Ридер с почтами всех ответственных реестра."""
    return FakeAdReader()


# --- снимок логинов и отметок ---


def test_step_logins_from_snapshot_single_no_duplicates():
    """Один ответственный — список из одного: assignee в снимке не дублируется."""
    step = _step(assignee=ROSTER[0], assignees=[ROSTER[0]])
    assert step_logins(step) == [ROSTER[0]]


def test_step_logins_legacy_without_snapshot():
    """Заявка до миграции (снимка нет) — прежний одиночный assignee."""
    assert step_logins(_step(assignee=ROSTER[0])) == [ROSTER[0]]
    assert step_logins(_step(owner_group=STEP_GROUP)) == []


def test_step_logins_skips_garbage_and_repeats():
    """Мусор в jsonb и повторы логина отсекаются, порядок снимка сохраняется."""
    step = _step(assignees=["", "  ", ROSTER[1], ROSTER[0], ROSTER[1]], assignee=ROSTER[2])
    assert step_logins(step) == [ROSTER[1], ROSTER[0]]


def test_step_marked_sams_reads_approvals():
    """Отметки шага — логины из approvals (без ФИО); мусор пропускается."""
    step = _step(approvals=[
        {"sam": ROSTER[0], "at": "2026-10-06T09:00:00+00:00", "decision": "approve"},
        {"sam": None},
        {"at": "2026-10-06T09:05:00+00:00"},
        "не-словарь",
    ])
    assert step_marked_sams(step) == {ROSTER[0]}
    assert step_marked_sams(_step()) == set()


# --- письмо «назначена»: адресаты шага ---


def test_single_responsible_gets_one_mail():
    """Шаг с одним ответственным — одно письмо (assignee из снимка не дублируется)."""
    step = _step(assignee=ROSTER[0], assignees=[ROSTER[0]])
    assert step_owner_mails(step, _reader()) == [_mail(ROSTER[0])]


def test_legacy_single_responsible_gets_one_mail():
    """Заявка до миграции: прежние правила (assignee), адресат один."""
    step = _step(assignee=ROSTER[0], owner_group=STEP_GROUP)
    assert step_owner_mails(step, _reader()) == [_mail(ROSTER[0])]


def test_sequential_step_notifies_every_responsible():
    """Последовательный шаг из трёх: письмо каждому ответственному снимка."""
    step = _step(assignee=ROSTER[0], assignees=list(ROSTER))
    assert step_owner_mails(step, _reader()) == [_mail(sam) for sam in ROSTER]


def test_step_notifies_only_who_still_has_to_sign():
    """Отметившийся повторно письма не получает — остальным по снимку уходит."""
    step = _step(
        assignee=ROSTER[0],
        assignees=list(ROSTER),
        approvals=[{"sam": ROSTER[0], "at": "2026-10-06T09:00:00+00:00",
                    "decision": "approve", "comment": None}],
    )
    assert step_owner_mails(step, _reader()) == [_mail(ROSTER[1]), _mail(ROSTER[2])]


def test_step_without_reader_or_mail_is_empty():
    """Нет ридера или почты у ответственных — пустой список (без исключений)."""
    step = _step(assignee=ROSTER[0], assignees=list(ROSTER))
    assert step_owner_mails(step, None) == []
    assert step_owner_mails(step, FakeAdReader(users={sam: SimpleNamespace(
        sam=sam, mail=None) for sam in ROSTER})) == []


def test_step_ad_failure_is_swallowed():
    """Сбой AD на резолве почты — пустой список: уведомление тихо не ставится."""
    step = _step(assignee=ROSTER[0], assignees=list(ROSTER))
    assert step_owner_mails(step, FakeAdReader(fail=True)) == []


def test_step_duplicate_mail_sent_once():
    """У двух ответственных одна почта (общий ящик) — одно письмо, не два."""
    users = {
        ROSTER[0]: SimpleNamespace(sam=ROSTER[0], mail="obshchiy@%s" % DOMAIN),
        ROSTER[1]: SimpleNamespace(sam=ROSTER[1], mail="obshchiy@%s" % DOMAIN),
        ROSTER[2]: SimpleNamespace(sam=ROSTER[2], mail=_mail(ROSTER[2])),
    }
    step = _step(assignee=ROSTER[0], assignees=list(ROSTER))
    assert step_owner_mails(step, FakeAdReader(users=users)) == [
        "obshchiy@%s" % DOMAIN, _mail(ROSTER[2])
    ]


def test_off_roster_login_is_not_in_snapshot():
    """Отключённый участник реестра в снимок не попал — письма ему не уходит."""
    step = _step(assignee=ROSTER[0], assignees=list(ROSTER))
    mails = step_owner_mails(step, _reader())
    assert _mail(ROSTER_OFF) not in mails


# --- групповые шаги (owner_group): прежнее поведение без изменений ---


def test_group_step_notifies_all_active_members_no_duplicates():
    """Групповой шаг (снимок пуст) — все активные участники группы, без дублей."""
    members = [
        SimpleNamespace(sam="buh1", mail="buh1@%s" % DOMAIN, enabled=True),
        SimpleNamespace(sam="buh2", mail="buh2@%s" % DOMAIN, enabled=False),
        SimpleNamespace(sam="buh3", mail="buh1@%s" % DOMAIN, enabled=True),
        SimpleNamespace(sam="buh4", mail=None, enabled=True),
    ]
    reader = FakeAdReader(members=members)
    step = _step(owner_group=STEP_GROUP)
    assert step_owner_mails(step, reader) == ["buh1@%s" % DOMAIN]


def test_group_step_without_resolver_or_failure_is_empty():
    """Нет резолвера состава группы / сбой AD — пустой список, без исключений."""
    step = _step(owner_group=STEP_GROUP)
    assert step_owner_mails(step, FakeAdReader(members=[])) == []
    assert step_owner_mails(step, FakeAdReader(fail=True)) == []


# --- письмо в очередь: по строке на адресата ---


def test_assigned_letters_queued_for_every_responsible(tmp_path):
    """Рассылка «назначена» кладёт в очередь по письму на каждого адресата."""
    from app.mailer import enqueue_event

    queue = FileMailQueue(tmp_path / "mail_queue.json")
    step = _step(assignee=ROSTER[0], assignees=list(ROSTER))
    context = {"fio": "Вымышленный Сотрудник", "url": "https://x/REQ-0001"}
    for to in step_owner_mails(step, _reader()):
        enqueue_event(queue, to, "REQ-0001", EVENT_ASSIGNED, TEMPLATES, context)
    assert [(m.to, m.event) for m in queue.pending()] == [
        (_mail(sam), EVENT_ASSIGNED) for sam in ROSTER
    ]
    assert all("REQ-0001" in m.subject for m in queue.pending())


def test_single_responsible_queues_one_letter(tmp_path):
    """Один ответственный — в очереди одно письмо (не два)."""
    from app.mailer import enqueue_event

    queue = FileMailQueue(tmp_path / "mail_queue.json")
    step = _step(assignee=ROSTER[0], assignees=[ROSTER[0]])
    context = {"fio": "Вымышленный Сотрудник", "url": "https://x/REQ-0001"}
    for to in step_owner_mails(step, _reader()):
        enqueue_event(queue, to, "REQ-0001", EVENT_ASSIGNED, TEMPLATES, context)
    assert queue.pending_count() == 1

# Тесты B3: бланки DOCX + очередь писем (offline, только stdlib).
# Проверяется приемка волны: DOCX собирается, QR ведет на заявку,
# очередь переживает рестарт worker-мока. Все ФИО вымышлены.
# conftest.py волны A2 не используется (запрет правок); sys.path — локально.

import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ad_reader import AdNotFound, AdUser  # noqa: E402
from app.docs import PDF_STUB_MARK, StdlibDocxRenderer, next_version, request_url  # noqa: E402
from app.mailer import (  # noqa: E402
    EVENT_ASSIGNED,
    EVENT_REMINDER,
    FileMailQueue,
    MailMessage,
    MockMailer,
    MockWorker,
    build_mail,
    enqueue_event,
    step_owner_mails,
)

# Вымышленные данные фикстур (не ПДн реальных лиц).
REQUEST_ID = "REQ-TEST-001"
BASE_URL = "https://intranet-mock.local/sed"
TEMPLATE_BODY = "Бегунок увольнения: {{ fio }}, {{ department }}. Подпись: ______."


def _context():
    # vacation_balance — ПДн: обязано вычищаться из бланка/письма.
    return {
        "fio": "Сказочников Тест Тестович",
        "department": "Цех тестовый",
        "vacation_balance": "28",
    }


def test_docx_assembles_with_version_and_request():
    """DOCX собирается: валидный zip, внутри request_id и версия v2."""
    renderer = StdlibDocxRenderer()
    version = next_version([1])  # повторная печать -> v2
    assert version == 2
    blob = renderer.render_docx(REQUEST_ID, version, TEMPLATE_BODY, _context(), BASE_URL)
    assert blob[:2] == b"PK"  # сигнатура zip/DOCX
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert "word/document.xml" in names
        assert "word/media/qr.png" in names
        text = zf.read("word/document.xml").decode("utf-8")
    assert REQUEST_ID in text
    assert "v2" in text
    assert "Сказочников Тест Тестович" in text
    assert "28" not in text  # остаток отпуска в бланк не попал


def test_qr_points_to_request():
    """QR-заглушка ведет на заявку: URL с request_id в документе и в PNG."""
    renderer = StdlibDocxRenderer()
    url = request_url(BASE_URL, REQUEST_ID)
    assert REQUEST_ID in url
    blob = renderer.render_docx(REQUEST_ID, 1, TEMPLATE_BODY, _context(), BASE_URL)
    with zipfile.ZipFile(__import__("io").BytesIO(blob)) as zf:
        text = zf.read("word/document.xml").decode("utf-8")
        png = zf.read("word/media/qr.png")
    assert url in text  # ссылка читаема в теле бланка
    assert url.encode("utf-8") in png  # та же ссылка в QR-заглушке


def test_pdf_is_stub_for_stand():
    """PDF — стаб с пометкой про LibreOffice на стенде (не настоящий рендер)."""
    renderer = StdlibDocxRenderer()
    stub = renderer.render_pdf_stub(REQUEST_ID, 1)
    assert stub.startswith(b"%PDF")
    assert PDF_STUB_MARK.encode("utf-8") in stub
    assert REQUEST_ID.encode("utf-8") in stub


def test_mail_queue_survives_worker_restart(tmp_path):
    """Очередь переживает рестарт: новый экземпляр на том же файле видит письма."""
    path = tmp_path / "mail_queue.json"
    queue = FileMailQueue(path)
    templates = {EVENT_ASSIGNED: "<html>Заявка {{ request_id }} назначена {{ fio }}</html>"}
    queue.enqueue(build_mail("m1", "owner-mock@example.com", REQUEST_ID, EVENT_ASSIGNED, templates, _context()))
    assert queue.pending_count() == 1
    restarted = FileMailQueue(path)  # рестарт worker-мока: новый объект, тот же файл
    assert restarted.pending_count() == 1
    assert restarted.pending()[0].request_id == REQUEST_ID


def test_mail_retry_then_sent(tmp_path):
    """Ретраи: два падения мока — письмо в очереди, третий проход — отправлено."""
    path = tmp_path / "mail_queue.json"
    queue = FileMailQueue(path)
    templates = {EVENT_ASSIGNED: "<html>Заявка {{ request_id }}</html>"}
    queue.enqueue(
        MailMessage(id="m2", to="owner-mock@example.com", subject="s", html="<html>x</html>",
                    request_id=REQUEST_ID, event=EVENT_ASSIGNED, max_attempts=5)
    )
    worker = MockWorker()
    flaky = MockMailer(fail_times=2)
    assert worker.run_once(queue, flaky).sent == 0
    assert queue.pending_count() == 1  # первая неудача: письмо осталось
    assert worker.run_once(queue, flaky).sent == 0
    assert queue.pending_count() == 1  # вторая неудача: все еще в очереди
    assert worker.run_once(queue, flaky).sent == 1
    assert queue.pending_count() == 0  # третья попытка: ушло
    assert flaky.sent and flaky.sent[0]["to"] == "owner-mock@example.com"


# ---------------------------------------------------------------------------
# Адресаты шага: персональный исполнитель или рассылка группе
# ---------------------------------------------------------------------------

class _FakeStep:
    """Шаг заявки: персональный исполнитель либо группа-владелец."""

    def __init__(self, assignee=None, owner_group=None):
        self.assignee = assignee
        self.owner_group = owner_group


class _FakeAdReader:
    """Фейк ридера AD для писем: карточка по логину и состав группы."""

    def __init__(self, users=None, groups=None, group_error=None):
        self.users = users or {}
        self.groups = groups or {}
        self.group_error = group_error

    def get_user(self, sam):
        return self.users[sam]

    def group_members(self, group):
        if self.group_error is not None:
            raise self.group_error
        return list(self.groups.get(group, []))


def _ad_user(sam, fio, mail="", enabled=True):
    """Вымышленная карточка AD (только чтение)."""
    return AdUser(
        dn="CN=%s,OU=SED,DC=example,DC=local" % fio,
        sam=sam,
        display_name=fio,
        mail=mail,
        enabled=enabled,
    )


def test_step_owner_mails_personal_step_single_mail():
    """Персональный шаг — один адресат: почта исполнителя."""
    reader = _FakeAdReader(
        users={"step.buhgalter": _ad_user("step.buhgalter", "Бухгалтер Вымышленный",
                                          "buh@example.com")}
    )
    step = _FakeStep(assignee="step.buhgalter", owner_group="SED_STEP_BUH")
    assert step_owner_mails(step, reader) == ["buh@example.com"]


def test_step_owner_mails_group_step_all_active_no_duplicates():
    """Групповой шаг — почта всех активных участников, без дублей и пустых."""
    reader = _FakeAdReader(
        groups={
            "SED_STEP_BUH": [
                _ad_user("b.pervaia", "Первая Вымышленная", "b1@example.com"),
                _ad_user("b.vtoraia", "Вторая Вымышленная", "b1@example.com"),  # дубль почты
                _ad_user("b.tretia", "Третья Вымышленная", "  "),  # почты нет
                _ad_user("b.chetvertaia", "Четвертая Вымышленная",
                         "b2@example.com", enabled=False),  # отключена
            ]
        }
    )
    step = _FakeStep(owner_group="SED_STEP_BUH")
    assert step_owner_mails(step, reader) == ["b1@example.com"]


def test_step_owner_mails_without_reader_is_empty():
    """Нет ридера/адресатов/ошибки AD — пустой список, без исключений."""
    step = _FakeStep(assignee="step.buhgalter", owner_group="SED_STEP_BUH")
    assert step_owner_mails(step, None) == []
    # Групповой шаг: группа не найдена в AD — письмо просто не ставится.
    failing = _FakeAdReader(group_error=AdNotFound("группа не найдена"))
    assert step_owner_mails(_FakeStep(owner_group="SED_STEP_BUH"), failing) == []
    # Участники без почты — тоже нечего отправлять.
    without_mail = _FakeAdReader(
        groups={"SED_STEP_BUH": [_ad_user("b.odin", "Один Вымышленный")]}
    )
    assert step_owner_mails(_FakeStep(owner_group="SED_STEP_BUH"), without_mail) == []


def test_group_step_mail_broadcast_one_queue_row_per_recipient(tmp_path):
    """Рассылка группе: в очереди по строке на адресата (одно письмо = одна строка)."""
    queue = FileMailQueue(tmp_path / "q.json")
    templates = [
        {"code": "reminder", "subject": "Напоминание {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"}
    ]
    reader = _FakeAdReader(
        groups={
            "SED_STEP_BUH": [
                _ad_user("b.pervaia", "Первая Вымышленная", "b1@example.com"),
                _ad_user("b.vtoraia", "Вторая Вымышленная", "b2@example.com"),
            ]
        }
    )
    step = _FakeStep(owner_group="SED_STEP_BUH")
    context = {"fio": "Сказочников Тест Тестович", "url": BASE_URL + "/requests/REQ-TEST-001"}
    queued = [
        enqueue_event(queue, to, REQUEST_ID, EVENT_REMINDER, templates, context)
        for to in step_owner_mails(step, reader)
    ]
    assert len(queued) == 2
    assert queue.pending_count() == 2
    assert sorted(m.to for m in queue.pending()) == ["b1@example.com", "b2@example.com"]
    # Адресатов нет — в очередь ничего не попадает (без исключений).
    assert enqueue_event(queue, None, REQUEST_ID, EVENT_REMINDER, templates, context) is None
    assert queue.pending_count() == 2

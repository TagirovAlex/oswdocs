# Тесты B3: бланки DOCX + очередь писем (offline, только stdlib).
# Проверяется приемка волны: DOCX собирается, QR ведет на заявку,
# очередь переживает рестарт worker-мока. Все ФИО вымышлены.
# conftest.py волны A2 не используется (запрет правок); sys.path — локально.

import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.docs import PDF_STUB_MARK, StdlibDocxRenderer, next_version, request_url  # noqa: E402
from app.mailer import (  # noqa: E402
    EVENT_ASSIGNED,
    FileMailQueue,
    MailMessage,
    MockMailer,
    MockWorker,
    build_mail,
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

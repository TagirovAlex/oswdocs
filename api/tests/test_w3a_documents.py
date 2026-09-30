# Тесты W3a (Волна 3, backend): печать бегунков (v1/v2, ручной конструктор,
# 422 без шагов, офлайн), документы GET/PDF (роли, мок файла), почта
# (SmtpMailer STARTTLS, build_mail тема из шаблона, enqueue_event),
# worker (просрочка → «На доработке» + письмо «возврат», напоминание,
# эскалация) на InMemoryRequestsStore. Все ПДн вымышленные.

import base64
import json
import os
import sys
import types
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.documents import (  # noqa: E402
    DocumentRecord,
    InMemoryDocumentsStore,
    get_documents_store,
    next_version_label,
    version_number,
)
from app.docs import BypassResult  # noqa: E402
from app.main import app  # noqa: E402
from app.mailer import (  # noqa: E402
    EVENT_ASSIGNED,
    EVENT_ESCALATION,
    EVENT_REMINDER,
    EVENT_RETURNED,
    FileMailQueue,
    MockMailer,
    SmtpMailer,
    build_mail,
    enqueue_event,
    get_mail_queue,
    mail_templates_map,
)
from app.requests import (  # noqa: E402
    IN_APPROVAL,
    REWORK,
    STEP_EXPIRED,
    STEP_PENDING,
    _Request,
    _Step,
    _utcnow,
    get_memory_requests_store,
    get_route_settings,
    RouteSettings,
)
from app.requests_store import get_requests_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402
from app.worker import WorkerResult, run_once  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"
FAKE_ENTERPRISE = "Вымышленное предприятие"
FAKE_BODY = "Бегунок увольнения: {{ fio }}, {{ department }}"
BASE_URL = "https://sed-mock.local"

DOC_TEMPLATES_SEED = json.dumps(
    [{"service": FAKE_SERVICE, "category": "линейный", "body": FAKE_BODY}],
    ensure_ascii=False,
)
MAIL_TEMPLATES_SEED = json.dumps(
    [
        {"code": "assigned", "subject": "Назначена {{ request_id }}",
         "body_html": "<html>{{ fio }}</html>"},
        {"code": "reminder", "subject": "Напоминание {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"},
        {"code": "escalation", "subject": "Эскалация {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"},
        {"code": "returned", "subject": "Возврат {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"},
        {"code": "closed", "subject": "Завершена {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"},
    ],
    ensure_ascii=False,
)


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _headers_for(sam: str, groups: list[str]) -> dict:
    return {
        "X-Mock-Sam": sam,
        "X-Mock-Fio": _b64("Вымышленный Пользователь Тестовый"),
        "X-Mock-Mail": _b64(f"{sam}@example.com"),
        "X-Mock-Department": _b64("Вымышленный отдел"),
        "X-Mock-Title": _b64("Вымышленная должность"),
        "X-Mock-Groups": ",".join(groups),
    }


class InMemorySettingsStore:
    """Мок хранилища настроек (сид-формат значений), как в test_settings_api."""

    def __init__(self, initial=None):
        self._data = dict(initial or {})

    def get(self, key):
        return self._data.get(key)


@pytest.fixture(autouse=True)
def settings_override():
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
        APP_BASE_URL=BASE_URL,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture(autouse=True)
def route_override():
    route = RouteSettings(approval_ttl_days=7)
    app.dependency_overrides[get_route_settings] = lambda: route
    yield route
    app.dependency_overrides.pop(get_route_settings, None)


@pytest.fixture
def requests_store():
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_requests_store, None)


@pytest.fixture
def documents_store():
    store = InMemoryDocumentsStore()
    app.dependency_overrides[get_documents_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_documents_store, None)


@pytest.fixture(autouse=True)
def settings_store():
    store = InMemorySettingsStore(
        initial={
            "doc_templates": DOC_TEMPLATES_SEED,
            "mail_templates": MAIL_TEMPLATES_SEED,
            "smtp_from": '"sed-mock@example.com"',
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture(autouse=True)
def mail_queue(tmp_path):
    queue = FileMailQueue(tmp_path / "mail_queue.json")
    app.dependency_overrides[get_mail_queue] = lambda: queue
    yield queue
    app.dependency_overrides.pop(get_mail_queue, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store, documents_store):
    requests_store.reset()
    documents_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    documents_store.reset()
    audit_log.clear_for_tests()


@pytest.fixture
def hr():
    return _headers_for("ok.vymyshlennaya", ["SED_HR"])


@pytest.fixture
def buh_owner():
    return _headers_for("step.buhgalter", ["SED_STEP_BUH"])


@pytest.fixture
def other_user():
    return _headers_for("step.chuzhoi", ["SED_STEP_OTHER"])


def _create(client, headers, **kw) -> dict:
    body = {
        "enterprise": FAKE_ENTERPRISE,
        "fio": "Вымышленный Сотрудник Полный",
        "tab_num": "В-0001",
        "department": FAKE_SERVICE,
        "position": FAKE_POSITION,
    }
    body.update(kw)
    response = client.post("/requests", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _fake_result(**kw) -> BypassResult:
    defaults = dict(
        generated=True,
        docx_path="/app/files/bypass.docx",
        pdf_path="/app/files/bypass.pdf",
        qr_payload=BASE_URL + "/requests/REQ-0001",
    )
    defaults.update(kw)
    return BypassResult(**defaults)


# --- версии бегунков (unit) ---

def test_version_number_and_next_label():
    assert version_number("v1") == 1
    assert version_number("v12") == 12
    assert version_number("x") == 0
    assert next_version_label([]) == "v1"
    assert next_version_label(
        [DocumentRecord(request_id="R", version="v1")] + [DocumentRecord(request_id="R", version="v2")]
    ) == "v3"


# --- POST /requests/{id}/print ---

def test_print_v1_then_v2(client, hr, documents_store, monkeypatch):
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    monkeypatch.setattr("app.documents.generate_bypass", lambda **kw: _fake_result())
    first = client.post(f"/requests/{rid}/print", headers=hr)
    assert first.status_code == 200
    assert first.json()["version"] == "v1"
    assert first.json()["generated"] is True
    assert first.json()["pdf_path"] == "/app/files/bypass.pdf"
    assert first.json()["qr_payload"]
    second = client.post(f"/requests/{rid}/print", headers=hr)
    assert second.status_code == 200
    assert second.json()["version"] == "v2"
    assert [d.version for d in documents_store.list_by_request(rid)] == ["v1", "v2"]


def test_print_uses_doc_template_body(client, hr, monkeypatch):
    """Шаблон бегунка по службе+категории: generate_bypass получает body шаблона."""
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return _fake_result()

    monkeypatch.setattr("app.documents.generate_bypass", fake)
    response = client.post(f"/requests/{rid}/print", headers=hr)
    assert response.status_code == 200
    assert captured["template_body"] == FAKE_BODY
    assert captured["request_id"] == rid
    assert captured["version"] == "v1"


def test_print_without_template_manual_constructor(client, hr, monkeypatch, settings_store):
    """Нет шаблона бегунка → ручной конструктор из шагов заявки."""
    settings_store._data["doc_templates"] = "[]"
    rid = _create(client, hr,
                  steps=[{"owner_group": "SED_STEP_BUH"}, {"owner_group": "SED_STEP_HR"}])["id"]
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return _fake_result()

    monkeypatch.setattr("app.documents.generate_bypass", fake)
    response = client.post(f"/requests/{rid}/print", headers=hr)
    assert response.status_code == 200
    assert "SED_STEP_BUH" in captured["template_body"]
    assert "SED_STEP_HR" in captured["template_body"]


def test_print_422_without_template_and_steps(client, hr, requests_store):
    """Нет шаблона бегунка и нет шагов → 422 (не 500)."""
    request = _Request(
        id="REQ-9001", status="Черновик", route_origin="custom",
        enterprise=FAKE_ENTERPRISE, fio="Вымышленный Сотрудник Полный",
        tab_num="В-0001", department=FAKE_SERVICE, position=FAKE_POSITION,
        created_by="ok.vymyshlennaya", steps=[],
    )
    requests_store.create(request)
    response = client.post("/requests/REQ-9001/print", headers=hr)
    assert response.status_code == 422


def test_print_offline_generated_false(client, hr, documents_store, monkeypatch):
    """Офлайн/нет LibreOffice → 200 {"generated": false, reason}, без записи в documents."""
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    monkeypatch.setattr(
        "app.documents.generate_bypass",
        lambda **kw: BypassResult(generated=False, reason="нет LibreOffice (soffice): PDF не создан"),
    )
    response = client.post(f"/requests/{rid}/print", headers=hr)
    assert response.status_code == 200
    body = response.json()
    assert body["generated"] is False
    assert "LibreOffice" in body["reason"]
    assert documents_store.list_by_request(rid) == []


def test_print_forbidden_for_owner_and_anonymous(client, buh_owner):
    rid = _create(client, _headers_for("ok.vymyshlennaya", ["SED_HR"]), category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert client.post(f"/requests/{rid}/print", headers=buh_owner).status_code == 403
    assert client.post(f"/requests/{rid}/print", headers={}).status_code == 401
    assert client.post("/requests/REQ-9999/print", headers=buh_owner).status_code in (403, 404)


def test_print_not_found_request(client, hr):
    response = client.post("/requests/REQ-9999/print", headers=hr)
    assert response.status_code == 404


# --- GET /documents/{id}, GET /documents/{id}/pdf ---

def test_documents_get_meta_roles(client, hr, buh_owner, other_user, documents_store):
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    documents_store.create(
        DocumentRecord(request_id=rid, version="v1", pdf_path="/app/files/a.pdf",
                       qr_payload="https://x/req", created_by="ok.vymyshlennaya")
    )
    as_hr = client.get(f"/documents/{rid}", headers=hr)
    assert as_hr.status_code == 200
    assert as_hr.json()[0]["version"] == "v1"
    assert as_hr.json()[0]["pdf_path"] == "/app/files/a.pdf"
    assert client.get(f"/documents/{rid}", headers=buh_owner).status_code == 200
    assert client.get(f"/documents/{rid}", headers=other_user).status_code == 403
    assert client.get(f"/documents/{rid}", headers={}).status_code == 401
    assert client.get("/documents/REQ-9999", headers=hr).status_code == 404


def test_documents_pdf_file_response(client, hr, documents_store, tmp_path):
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    pdf = tmp_path / "bypass.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake-bytes")
    documents_store.create(
        DocumentRecord(request_id=rid, version="v1", pdf_path=str(pdf), qr_payload="x")
    )
    response = client.get(f"/documents/{rid}/pdf?version=v1", headers=hr)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 fake-bytes"
    assert response.headers["content-type"] == "application/pdf"


def test_documents_pdf_404(client, hr, documents_store):
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert client.get(f"/documents/{rid}/pdf?version=v1", headers=hr).status_code == 404
    documents_store.create(
        DocumentRecord(request_id=rid, version="v1", pdf_path="/app/files/absent.pdf")
    )
    assert client.get(f"/documents/{rid}/pdf?version=v1", headers=hr).status_code == 404


def test_documents_pdf_owner_can_download(client, hr, buh_owner, documents_store, tmp_path):
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"data")
    documents_store.create(DocumentRecord(request_id=rid, version="v1", pdf_path=str(pdf)))
    assert client.get(f"/documents/{rid}/pdf?version=v1", headers=buh_owner).status_code == 200


# --- почта: build_mail/enqueue_event/SmtpMailer ---

def test_build_mail_subject_rendered():
    templates = {
        EVENT_ASSIGNED: {"subject": "Заявка {{ request_id }} назначена",
                         "body_html": "<html>{{ fio }} ({{ url }})</html>"}
    }
    message = build_mail(
        "m1", "owner@example.com", "REQ-0001", EVENT_ASSIGNED,
        templates, {"fio": "Иванов Иван", "url": "https://x/REQ-0001"},
    )
    assert message.subject == "Заявка REQ-0001 назначена"
    assert "Иванов Иван" in message.html
    assert "https://x/REQ-0001" in message.html


def test_mail_templates_map():
    items = [
        {"code": "assigned", "subject": "S", "body_html": "H"},
        {"code": "reminder", "subject": "S2", "body_html": "H2"},
        "не-словарь",
    ]
    mapped = mail_templates_map(items)
    assert set(mapped) == {"assigned", "reminder"}
    assert mapped["assigned"]["subject"] == "S"


def test_enqueue_event_skips_without_recipient_or_template(tmp_path):
    queue = FileMailQueue(tmp_path / "q.json")
    assert enqueue_event(queue, None, "REQ-0001", EVENT_RETURNED, [], {}) is None
    assert queue.pending_count() == 0
    assert enqueue_event(queue, "a@example.com", "REQ-0001", EVENT_RETURNED, [], {}) is None
    assert queue.pending_count() == 0
    message = enqueue_event(
        queue, "a@example.com", "REQ-0001", EVENT_RETURNED,
        [{"code": "returned", "subject": "Возврат {{ request_id }}",
          "body_html": "<html>{{ url }}</html>"}],
        {"url": "https://x"},
    )
    assert message is not None
    assert queue.pending_count() == 1


def test_smtp_mailer_starttls_and_login(monkeypatch):
    class FakeSMTP:
        instances = []

        def __init__(self, host, port, timeout=None):
            self.host, self.port, self.timeout = host, port, timeout
            self.started_tls = False
            self.credentials = None
            self.sent = []
            FakeSMTP.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            self.started_tls = True

        def login(self, user, password):
            self.credentials = (user, password)

        def send_message(self, message):
            self.sent.append(message)

    monkeypatch.setattr("app.mailer.smtplib", types.SimpleNamespace(SMTP=FakeSMTP))
    mailer = SmtpMailer("smtp-mock.local", 587, "bot", "secret", "sed@example.com")
    mailer.send("to@example.com", "Тема", "<html>тело</html>")
    instance = FakeSMTP.instances[-1]
    assert instance.host == "smtp-mock.local"
    assert instance.started_tls is True
    assert instance.credentials == ("bot", "secret")
    assert instance.sent and instance.sent[0]["To"] == "to@example.com"


# --- worker ---

class FakeUser:
    def __init__(self, sam, mail=None, manager_dn=""):
        self.sam = sam
        self.mail = mail
        self.manager_dn = manager_dn


class FakeAdReader:
    def __init__(self, users=None, by_dn=None):
        self.users = users or {}
        self.by_dn = by_dn or {}

    def get_user(self, sam):
        return self.users[sam]

    def get_user_by_dn(self, dn):
        return self.by_dn[dn]


def _make_request(request_id="REQ-0001", *, expires_in, status=IN_APPROVAL,
                 escalation_hours=None, assignee=None, owner_group="SED_STEP_BUH"):
    return _Request(
        id=request_id, status=status, route_origin="custom",
        enterprise=FAKE_ENTERPRISE, fio="Вымышленный Сотрудник Полный",
        tab_num="В-0001", department=FAKE_SERVICE, position=FAKE_POSITION,
        escalation_hours=escalation_hours, created_by="ok.vymyshlennaya",
        steps=[
            _Step(order=1, owner_group=owner_group, assignee=assignee,
                  status=STEP_PENDING, expires_at=_utcnow() + expires_in)
        ],
    )


def test_worker_overdue_to_rework_with_return_letter(tmp_path):
    store = get_memory_requests_store()
    store.create(_make_request(expires_in=timedelta(hours=-1)))
    queue = FileMailQueue(tmp_path / "q.json")
    mailer = MockMailer()
    ad = FakeAdReader(
        {"ok.vymyshlennaya": FakeUser("ok.vymyshlennaya", mail="ok@example.com")}
    )
    templates = [
        {"code": "returned", "subject": "Возврат {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"}
    ]
    result = run_once(
        store, queue, mailer, templates, {}, 7, ad, BASE_URL,
        smtp_from="", now=_utcnow(),
    )
    stored = store.get("REQ-0001")
    assert stored.status == REWORK
    assert stored.steps[0].status == STEP_EXPIRED
    assert result.expired == 1
    assert result.sent == 1
    assert mailer.sent and mailer.sent[0]["to"] == "ok@example.com"
    assert "REQ-0001" in mailer.sent[0]["subject"]


def test_worker_reminder_queued_once(tmp_path):
    store = get_memory_requests_store()
    store.create(_make_request(expires_in=timedelta(hours=24), assignee="step.buhgalter"))
    queue = FileMailQueue(tmp_path / "q.json")
    mailer = MockMailer()
    ad = FakeAdReader(
        {"step.buhgalter": FakeUser("step.buhgalter", mail="buh@example.com")}
    )
    templates = [
        {"code": "reminder", "subject": "Напоминание {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"}
    ]
    now = _utcnow()
    result = run_once(store, queue, mailer, templates, {}, 7, ad, BASE_URL, now=now)
    assert result.reminders == 1
    assert result.sent == 1
    assert mailer.sent[0]["to"] == "buh@example.com"
    # Второй проход: письмо уже поставлено (дедупликация) — повторно не шлем.
    mailer.sent.clear()
    again = run_once(store, queue, mailer, templates, {}, 7, ad, BASE_URL, now=now)
    assert again.reminders == 0
    assert again.sent == 0


def test_worker_escalation_to_manager(tmp_path):
    store = get_memory_requests_store()
    store.create(
        _make_request(expires_in=timedelta(hours=1), escalation_hours=6, assignee="step.buhgalter")
    )
    queue = FileMailQueue(tmp_path / "q.json")
    mailer = MockMailer()
    ad = FakeAdReader(
        {
            "step.buhgalter": FakeUser("step.buhgalter", mail="buh@example.com",
                                       manager_dn="cn=buh.manager"),
            "ok.vymyshlennaya": FakeUser("ok.vymyshlennaya", mail="ok@example.com"),
        },
        by_dn={"cn=buh.manager": FakeUser("buh.manager", mail="manager@example.com")},
    )
    templates = [
        {"code": "escalation", "subject": "Эскалация {{ request_id }}",
         "body_html": "<html>{{ url }}</html>"}
    ]
    result = run_once(store, queue, mailer, templates, {}, 7, ad, BASE_URL, now=_utcnow())
    assert result.escalations == 1
    assert result.sent == 1
    assert mailer.sent[0]["to"] == "manager@example.com"


def test_worker_ignores_non_approval_requests(tmp_path):
    store = get_memory_requests_store()
    store.create(_make_request(expires_in=timedelta(hours=-5), status="Черновик"))
    queue = FileMailQueue(tmp_path / "q.json")
    result = run_once(
        store, queue, MockMailer(), [], {}, 7, None, BASE_URL, now=_utcnow(),
    )
    assert result.expired == 0
    assert store.get("REQ-0001").status == "Черновик"
    assert isinstance(result, WorkerResult)
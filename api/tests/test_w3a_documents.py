# Тесты W3a (Волна 3, backend): печать бегунков (v1/v2, ручной конструктор,
# 422 без шагов, офлайн), документы GET/PDF (роли, мок файла), почта
# (SmtpMailer STARTTLS, build_mail тема из шаблона, enqueue_event),
# worker (просрочка → «На доработке» + письмо «возврат», напоминание,
# эскалация) на InMemoryRequestsStore. Все ПДн вымышленные.

import base64
import json
import os
import re
import subprocess
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
from app import docs as docs_module  # noqa: E402
from app.docs import BypassResult, generate_bypass  # noqa: E402
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
        "subject": "Вымышленная тема",
        "content": "Вымышленное содержание",
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

def test_print_returns_pdf_and_does_not_store_versions(
    client, hr, documents_store, monkeypatch, tmp_path
):
    """Вариант 1: печать = форма текущего состояния. PDF в ответе (base64),
    версии не накапливаются, документы в БД не пишутся, повтор печати не даёт v2."""
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    pdf = tmp_path / "bypass.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    docx = tmp_path / "bypass.docx"
    docx.write_bytes(b"docx")

    def fake(**kw):
        # Каждый вызов «генерирует» файл заново (в проде так и делает generate_bypass;
        # print_bypass после чтения удаляет временные файлы — повтор не находит старого).
        pdf.write_bytes(b"%PDF-1.4 fake")
        return _fake_result(pdf_path=str(pdf), docx_path=str(docx))

    monkeypatch.setattr("app.documents.generate_bypass", fake)
    first = client.post(f"/requests/{rid}/print", headers=hr)
    assert first.status_code == 200
    body = first.json()
    assert body["generated"] is True
    assert base64.b64decode(body["pdf_b64"]) == b"%PDF-1.4 fake"
    assert "version" not in body
    assert documents_store.list_by_request(rid) == []
    second = client.post(f"/requests/{rid}/print", headers=hr)
    assert second.json()["pdf_b64"] == body["pdf_b64"]
    assert documents_store.list_by_request(rid) == []


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
    assert captured["version"] == "current"


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


# --- W3a: зависимости стенда и разведение «офлайн»/настоящего сбоя ---

DOCS_PY_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "docs.py")
REQUIREMENTS_PATH = os.path.join(os.path.dirname(__file__), "..", "requirements.txt")

# Имя импорта может отличаться от имени пакета: docx приходит вместе с docxtpl
# (python-docx — его зависимость, отдельной строкой не объявляем).
IMPORT_TO_PACKAGE = {"docx": "docxtpl"}
# PNG-бэкенд qrcode (img.save(format="PNG")) требует Pillow; по импортам docs.py
# это не видно — PIL импортирует сам qrcode, поэтому проверяем пакет явно.
RENDER_PACKAGES = ("pillow",)

_IMPORT_RE = re.compile(
    r"^[ \t]*(?:from[ \t]+([A-Za-z_][\w.]*)[ \t]+import|import[ \t]+([A-Za-z_][\w.]*))",
    re.MULTILINE,
)


def _imported_modules(source: str) -> set:
    """Верхнеуровневые модули из импортов исходника (файл + тела функций)."""
    found = set()
    for from_name, import_name in _IMPORT_RE.findall(source):
        for name in (from_name, import_name):
            for part in name.split(","):
                top = part.strip().split(".")[0]
                if top and top not in sys.stdlib_module_names:
                    found.add(top)
    return found


def _declared_packages(requirements_text: str) -> set:
    """Имена пакетов из requirements.txt: без версий, экстра и комментариев."""
    names = set()
    for line in requirements_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[\s\[\]<>=!~;]", line, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def test_docs_imports_declared_in_requirements():
    """Каждый сторонний импорт docs.py объявлен в requirements.txt.

    Список импортов выводится регуляркой из исходника docs.py (хардкод-списка
    нет), имена пакетов читаются из requirements.txt без версий.
    """
    with open(DOCS_PY_PATH, encoding="utf-8") as fh:
        imported = _imported_modules(fh.read())
    assert "qrcode" in imported, "регулярка не нашла импорты docs.py — тест врёт"
    with open(REQUIREMENTS_PATH, encoding="utf-8") as fh:
        declared = _declared_packages(fh.read())
    missing = sorted(
        name
        for name in imported | set(RENDER_PACKAGES)
        if IMPORT_TO_PACKAGE.get(name, name).replace("_", "-") not in declared
    )
    assert not missing, f"в requirements.txt нет пакетов для docs.py: {missing}"


def _ok_docx(template_body: str, context: dict) -> bytes:
    """DOCX-рендер-заглушка: docx/docxtpl на машине может не быть установлен."""
    return b"docx-bytes"


def _raise_missing_pil(url: str) -> bytes:
    """QR-рендер без Pillow (состояние стенда до добавления pillow)."""
    raise ModuleNotFoundError("No module named 'PIL'")


def _bypass_kwargs(tmp_path) -> dict:
    return dict(
        request_id="REQ-0001", version="v1", template_body=FAKE_BODY,
        context={}, base_url=BASE_URL, files_dir=str(tmp_path),
    )


def test_generate_bypass_offline_reason_names_module(monkeypatch, tmp_path):
    """ModuleNotFoundError → generated=False, причина называет сам модуль."""
    monkeypatch.setattr(docs_module, "_render_docx_stand", _ok_docx)
    monkeypatch.setattr(docs_module, "_render_qr_png", _raise_missing_pil)
    result = generate_bypass(**_bypass_kwargs(tmp_path))
    assert result.generated is False
    assert result.reason.startswith("офлайн")
    assert "PIL" in result.reason


def test_generate_bypass_reraises_real_errors(monkeypatch, tmp_path):
    """Настоящий сбой рендера не превращается в generated=False — проброс."""
    monkeypatch.setattr(docs_module, "_render_docx_stand", _ok_docx)

    def broken(url: str) -> bytes:
        raise RuntimeError("сбой рендера QR")

    monkeypatch.setattr(docs_module, "_render_qr_png", broken)
    with pytest.raises(RuntimeError):
        generate_bypass(**_bypass_kwargs(tmp_path))


def test_print_offline_missing_module_is_not_500(client, hr, monkeypatch):
    """Офлайн без библиотеки → 200 {"generated": false, "reason": ...}, не 500."""
    monkeypatch.setattr(docs_module, "_render_docx_stand", _ok_docx)
    monkeypatch.setattr(docs_module, "_render_qr_png", _raise_missing_pil)
    rid = _create(client, hr, category="линейный",
                  steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = client.post(f"/requests/{rid}/print", headers=hr)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] is False
    assert "PIL" in body["reason"]


# --- W3a: конвертация LibreOffice (профиль в /tmp) и диагностика в reason ---
# Локально LibreOffice нет — подменяем subprocess.run, сам soffice не запускаем
# (иначе тесты зависели бы от наличия софта на машине).

def test_convert_to_pdf_uses_tmp_profile_and_ignores_home(tmp_path, monkeypatch):
    """Профиль soffice — в /tmp с суффиксом pid, HOME не используется.

    В контейнере /home/appuser не существует, и soffice без профиля падает с
    RC=77; общий профиль на все процессы конфликтует при параллельной печати.
    """
    docx = tmp_path / "bypass.docx"
    docx.write_bytes(b"docx-bytes")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        (tmp_path / "bypass.pdf").write_bytes(b"%PDF-1.4")  # конвертер положил PDF
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", "/home/does-not-exist")
    pdf = docs_module._convert_to_pdf(str(docx), str(tmp_path), "/usr/bin/soffice")
    assert pdf == str(tmp_path / "bypass.pdf")
    argv = calls[0]
    profiles = [a for a in argv if a.startswith("-env:UserInstallation=")]
    assert len(profiles) == 1, f"в argv нет профиля LibreOffice: {argv}"
    assert profiles[0].startswith("-env:UserInstallation=file:///tmp/")
    assert f"-{os.getpid()}" in profiles[0], "профиль должен быть уникален на процесс"
    assert "/home/does-not-exist" not in " ".join(argv)
    assert "--headless" in argv


def test_generate_bypass_reason_has_soffice_returncode_and_stderr(tmp_path, monkeypatch):
    """Сбой конвертации: в причине код возврата soffice и хвост его stderr."""
    monkeypatch.setattr(docs_module, "_render_docx_stand", _ok_docx)
    monkeypatch.setattr(docs_module, "_render_qr_png", lambda url: b"qr-bytes")
    monkeypatch.setattr(docs_module, "_soffice_binary", lambda: "/usr/bin/soffice")
    stderr = "\n".join([
        "dconf-CRITICAL: unable to create directory '/home/appuser/.cache/dconf'",
        "Fontconfig error: No writable cache directories",
        "строка перед хвостом",
        "convert ... -> ... using filter : writer_pdf_Export",
        "LibreOffice 25.2 - Fatal Error: The application cannot be started.",
    ])

    def fake_run(argv, **kw):
        return types.SimpleNamespace(returncode=77, stdout="", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = generate_bypass(**_bypass_kwargs(tmp_path))
    assert result.generated is False
    assert "LibreOffice" in result.reason
    assert "77" in result.reason
    assert "The application cannot be started" in result.reason
    # В причину идёт только хвост stderr, без раннего шума конвертера.
    assert "dconf" not in result.reason
    assert "Fontconfig" not in result.reason
    # Неуспешный бегунок файлов не оставляет (поведение прежнее).
    assert not (tmp_path / "bypass_REQ-0001_v1.docx").exists()


def test_generate_bypass_reason_without_soffice(tmp_path, monkeypatch):
    """Нет soffice в PATH — причина называет LibreOffice, конвертер не запускается."""
    monkeypatch.setattr(docs_module, "_render_docx_stand", _ok_docx)
    monkeypatch.setattr(docs_module, "_render_qr_png", lambda url: b"qr-bytes")
    monkeypatch.setattr(docs_module, "_soffice_binary", lambda: None)

    def forbidden_run(argv, **kw):
        raise AssertionError("без soffice конвертер запускаться не должен")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    result = generate_bypass(**_bypass_kwargs(tmp_path))
    assert result.generated is False
    assert "LibreOffice" in result.reason

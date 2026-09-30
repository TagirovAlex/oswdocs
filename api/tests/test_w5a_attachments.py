# Тесты W5a скан-вложений: POST /requests/{id}/attachments (multipart),
# GET /requests/{id}/attachments (мета), GET /attachments/{id}/file.
# Лимиты — из settings (scan_max_mb, scan_allowed_types): нет ключа -> 409,
# превышение размера -> 413, MIME вне allowlist -> 415. Роли — как у заявки:
# ОК/админы и владелец своего шага; хранилища подменяются in-memory моками
# (мета и файлы), БД/ФС не вызываются. ПДн вымышленные.

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.attachments import (  # noqa: E402
    AttachmentRecord,
    InMemoryAttachmentsStore,
    InMemoryFilesStore,
    get_attachments_store,
    get_files_store,
)
from app.audit import audit_log  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.requests import (  # noqa: E402
    get_memory_requests_store,
    get_route_settings,
    RouteSettings,
)
from app.requests_store import get_requests_store  # noqa: E402
from app.settings_routes import get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"
FAKE_ENTERPRISE = "Вымышленное предприятие"

SCAN_MAX_MB = 2
ALLOWED_TYPES = ["application/pdf", "image/jpeg", "image/png"]
SCAN_MAX_MB_SEED = str(SCAN_MAX_MB)
ALLOWED_TYPES_SEED = json.dumps(ALLOWED_TYPES)


def _b64(value: str) -> str:
    return __import__("base64").b64encode(value.encode("utf-8")).decode("ascii")


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
    """Мок хранилища настроек (сид-формат значений), как в test_w3a_documents."""

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
        FILES_DIR="/app/files",
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
def attachments_store():
    store = InMemoryAttachmentsStore()
    app.dependency_overrides[get_attachments_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_attachments_store, None)


@pytest.fixture
def files_store():
    store = InMemoryFilesStore()
    app.dependency_overrides[get_files_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_files_store, None)


@pytest.fixture(autouse=True)
def settings_store():
    store = InMemorySettingsStore(
        initial={
            "scan_max_mb": SCAN_MAX_MB_SEED,
            "scan_allowed_types": ALLOWED_TYPES_SEED,
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store, attachments_store, files_store):
    requests_store.reset()
    attachments_store.reset()
    files_store.reset()
    audit_log.clear_for_tests()
    yield
    requests_store.reset()
    attachments_store.reset()
    files_store.reset()
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


def _upload(client, rid, headers, filename="заявление.pdf",
            content_type="application/pdf", content=b"%PDF-1.4 fake"):
    return client.post(
        f"/requests/{rid}/attachments",
        headers=headers,
        files={"file": (filename, content, content_type)},
    )


# --- POST /requests/{id}/attachments ---

def test_upload_ok_201_meta_and_stores(client, hr, attachments_store, files_store):
    """Загрузка валидного скана: 201, мета без file_path, записаны файл и мета."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = _upload(client, rid, hr)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["request_id"] == rid
    assert body["file_name"] == "заявление.pdf"
    assert body["mime"] == "application/pdf"
    assert body["size_bytes"] == len(b"%PDF-1.4 fake")
    assert body["uploaded_by"] == "ok.vymyshlennaya"
    assert "file_path" not in body
    assert len(attachments_store.list_by_request(rid)) == 1
    assert len(files_store._files) == 1


def test_upload_oversize_413(client, hr, files_store, attachments_store):
    """Файл больше scan_max_mb — 413, ничего не сохранено."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    big = b"x" * (SCAN_MAX_MB * 1024 * 1024 + 1)
    response = _upload(client, rid, hr, content=big)
    assert response.status_code == 413
    assert attachments_store.list_by_request(rid) == []
    assert files_store._files == {}


def test_upload_ok_boundary_size(client, hr):
    """Ровно scan_max_mb — допустимо (граница включительно)."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    content = b"x" * (SCAN_MAX_MB * 1024 * 1024)
    response = _upload(client, rid, hr, content=content)
    assert response.status_code == 201


def test_upload_bad_mime_415(client, hr):
    """MIME вне allowlist — 415 (до сохранения)."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = _upload(client, rid, hr, content_type="text/plain")
    assert response.status_code == 415
    assert "не разрешен" in response.json()["detail"]


def test_upload_no_scan_max_mb_409(client, hr, settings_store):
    """Нет ключа scan_max_mb в settings — 409 «лимит не задан»."""
    settings_store._data.pop("scan_max_mb", None)
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = _upload(client, rid, hr)
    assert response.status_code == 409
    assert "scan_max_mb" in response.json()["detail"]


def test_upload_no_allowed_types_409(client, hr, settings_store):
    """Нет/пуст scan_allowed_types — 409 «лимит не задан»."""
    settings_store._data.pop("scan_allowed_types", None)
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert _upload(client, rid, hr).status_code == 409
    settings_store._data["scan_allowed_types"] = "[]"
    assert _upload(client, rid, hr).status_code == 409


def test_upload_owner_of_step_ok(client, hr, buh_owner):
    """Владелец своего шага может грузить скан заявки."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert _upload(client, rid, buh_owner).status_code == 201


def test_upload_forbidden_for_others(client, hr, other_user):
    """Не владелец шага — 403; без логина — 401."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert _upload(client, rid, other_user).status_code == 403
    assert _upload(client, rid, {}).status_code == 401


def test_upload_missing_request_404(client, hr):
    """Заявки нет — 404 (вложения к чужому/несуществующему id)."""
    assert _upload(client, "REQ-9999", hr).status_code == 404


def test_upload_sanitizes_filename(client, hr, attachments_store):
    """Имя файла — только basename (без path traversal)."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = _upload(client, rid, hr, filename="../../etc/заявление.pdf")
    assert response.status_code == 201
    assert response.json()["file_name"] == "заявление.pdf"


# --- GET /requests/{id}/attachments ---

def test_list_meta_roles(client, hr, buh_owner, other_user):
    """Мета списка: ОК и владелец шага читают, чужой — 403, без логина — 401."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    _upload(client, rid, hr)
    as_hr = client.get(f"/requests/{rid}/attachments", headers=hr)
    assert as_hr.status_code == 200
    assert len(as_hr.json()) == 1
    assert as_hr.json()[0]["file_name"] == "заявление.pdf"
    assert client.get(f"/requests/{rid}/attachments", headers=buh_owner).status_code == 200
    assert client.get(f"/requests/{rid}/attachments", headers=other_user).status_code == 403
    assert client.get(f"/requests/{rid}/attachments", headers={}).status_code == 401
    assert client.get("/requests/REQ-9999/attachments", headers=hr).status_code == 404


# --- GET /attachments/{id}/file ---

def test_download_file_200(client, hr, buh_owner):
    """Файл скачивается с контентом и MIME; владелец шага — тоже."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    uploaded = _upload(client, rid, hr)
    attachment_id = uploaded.json()["id"]
    as_hr = client.get(f"/attachments/{attachment_id}/file", headers=hr)
    assert as_hr.status_code == 200
    assert as_hr.content == b"%PDF-1.4 fake"
    assert as_hr.headers["content-type"] == "application/pdf"
    as_owner = client.get(f"/attachments/{attachment_id}/file", headers=buh_owner)
    assert as_owner.status_code == 200
    assert as_owner.content == b"%PDF-1.4 fake"


def test_download_file_roles(client, hr, other_user):
    """Чужой пользователь файл не скачивает — 403."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    attachment_id = _upload(client, rid, hr).json()["id"]
    assert client.get(f"/attachments/{attachment_id}/file", headers=other_user).status_code == 403
    assert client.get(f"/attachments/{attachment_id}/file", headers={}).status_code == 401


def test_download_file_404(client, hr):
    """Нет вложения — 404; есть мета, но файла нет — 404."""
    assert client.get("/attachments/9999/file", headers=hr).status_code == 404


def test_download_file_missing_bytes_404(client, hr, attachments_store, files_store):
    """Мета есть, а файл не сохранен — 404 «файл не найден»."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    record = attachments_store.create(
        AttachmentRecord(
            request_id=rid, file_path="/inmemory/attachments/missing.bin",
            file_name="missing.bin", mime="application/pdf",
        )
    )
    assert client.get(f"/attachments/{record.id}/file", headers=hr).status_code == 404


# --- аудит ---

def test_audit_upload_and_read(client, hr):
    """Загрузка и чтение пишут attachment.upload/attachment.read."""
    rid = _create(client, hr, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    _upload(client, rid, hr)
    client.get(f"/requests/{rid}/attachments", headers=hr)
    events = [e for e in audit_log.all() if e.entity == "attachment"]
    actions = [e.action for e in events]
    assert "attachment.upload" in actions
    assert "attachment.read" in actions
# Тесты удаления заявки (тестовый период): DELETE /requests/{id} — только админ.
# Хранилища in-memory через dependency_overrides (БД/ФС не вызываются), поэтому
# тесты не зависят от Postgres и не висят. ПДн вымышленные.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.attachments import (  # noqa: E402
    AttachmentsUnavailable,
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
    RouteSettings,
    get_memory_requests_store,
    get_route_settings,
    reset_state_for_tests,
)
from app.requests_store import RequestsUnavailable, get_requests_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"

FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"
FAKE_ENTERPRISE = "Вымышленное предприятие"


@pytest.fixture(autouse=True)
def settings_override():
    settings = Settings(
        ALLOWED_AD_GROUPS=TEST_ALLOWED,
        ADMIN_GROUPS=TEST_ADMINS,
        HR_GROUPS=TEST_HR,
        STEP_GROUP_PREFIX=TEST_STEP_PREFIX,
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


@pytest.fixture(autouse=True)
def requests_store():
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_requests_store, None)


@pytest.fixture(autouse=True)
def clean_state(requests_store):
    reset_state_for_tests()
    audit_log.clear_for_tests()
    yield
    reset_state_for_tests()
    audit_log.clear_for_tests()


@pytest.fixture(autouse=True)
def attachments_store():
    store = InMemoryAttachmentsStore()
    app.dependency_overrides[get_attachments_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_attachments_store, None)


@pytest.fixture(autouse=True)
def files_store():
    store = InMemoryFilesStore()
    app.dependency_overrides[get_files_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_files_store, None)


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


def test_admin_deletes_request(client, hr_headers, admin_headers, requests_store):
    """Админ удаляет заявку: 200, повторный GET — 404, хранилище пусто, есть аудит."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = client.delete(f"/requests/{rid}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"deleted": rid}
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 404
    assert requests_store.list_all() == []
    events = [e for e in audit_log.all() if e.action == "request.delete"]
    assert len(events) == 1
    assert events[0].entity_id == rid
    assert events[0].actor == "adm.petrov"


def test_hr_cannot_delete(client, hr_headers, admin_headers):
    """Удаление — только админ: ОК получает 403, заявка на месте."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    response = client.delete(f"/requests/{rid}", headers=hr_headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Удаление заявок — только админ"
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 200


def test_delete_missing_request_404(client, admin_headers):
    """Несуществующей заявки нет — 404."""
    response = client.delete("/requests/REQ-9999", headers=admin_headers)
    assert response.status_code == 404


def test_delete_clears_request_history_and_comments(
    client, hr_headers, admin_headers, requests_store
):
    """Удаление заявки уносит её историю и комментарии.

    Раньше след оставался в журнале навсегда (FK нет, append-only), и после
    очистки списка заявок новая REQ-0001 показывала историю удалённой. Здесь
    проверяется поведение офлайн-хранилища; путь Postgres (та же зачистка в
    DbRequestsStore.delete) — в test_requests_store.py."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    comment = client.post(
        f"/requests/{rid}/comments",
        json={"body": "Вымышленный комментарий к заявке"},
        headers=hr_headers,
    )
    assert comment.status_code == 200, comment.text
    assert client.get(f"/requests/{rid}/history", headers=admin_headers).json()
    assert client.get(f"/requests/{rid}/comments", headers=admin_headers).json()

    assert client.delete(f"/requests/{rid}", headers=admin_headers).status_code == 200
    left = [
        e
        for e in audit_log.all()
        if e.entity_id == rid and e.action != "request.delete"
    ]
    assert left == [], "история удалённой заявки осталась в журнале"
    assert not requests_store.list_comments(rid)


def test_request_code_not_reused_after_delete(
    client, hr_headers, admin_headers, requests_store
):
    """Номер удалённой заявки не выдаётся снова.

    Офлайн-хранилище считало монотонно и раньше; контракт зафиксирован, чтобы
    правка счётчика в боевом DbRequestsStore (миграция 0021, nextval) не
    нарушила его — проверка боевого пути в test_requests_store.py."""
    first = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert client.delete(f"/requests/{first}", headers=admin_headers).status_code == 200
    second = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    assert second != first


def _attach(attachments_store, files_store, request_id: str, name: str) -> str:
    """Вложение с настоящим содержимым: файл в files_store + мета в attachments."""
    path = files_store.save(request_id, name, "Вымышленный скан".encode("utf-8"))
    attachments_store.create(
        AttachmentRecord(request_id=request_id, file_path=path, file_name=name)
    )
    return path


def test_delete_removes_attachment_files(
    client, hr_headers, admin_headers, attachments_store, files_store
):
    """Удаление заявки снимает и файлы вложений, а не только мета-строки.

    Без этого файлы копились в FILES_DIR вечно: мета исчезала каскадом, пути
    терялись, и на тестовом стенде оставались осиротевшие сканы."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    path = _attach(attachments_store, files_store, rid, "scan.docx")
    other = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    other_path = _attach(attachments_store, files_store, other, "other.docx")
    assert files_store._files

    assert client.delete(f"/requests/{rid}", headers=admin_headers).status_code == 200
    assert path not in files_store._files, "файл удалённой заявки остался на диске"
    assert other_path in files_store._files, "чужой файл трогать нельзя"


def test_delete_survives_file_error(
    client, hr_headers, admin_headers, attachments_store, files_store, monkeypatch
):
    """Ошибка удаления файла не ломает удаление заявки.

    Заявка к этому моменту уже удалена из БД: 5xx означал бы, что клиент
    повторит удаление и получит 404 вместо успеха."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    _attach(attachments_store, files_store, rid, "scan.docx")

    def boom(_path: str) -> None:
        raise OSError("Вымышленная ошибка удаления файла")

    monkeypatch.setattr(files_store, "delete", boom)
    assert client.delete(f"/requests/{rid}", headers=admin_headers).status_code == 200
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 404


def test_delete_survives_unavailable_attachment_meta(
    client, hr_headers, admin_headers, files_store, monkeypatch
):
    """Недоступная мета вложений не мешает удалить заявку.

    Хранилище вложений — отдельная граница: её отказ не должен превращать
    удаление заявки в 503, файлы в таком случае просто осиротеют."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]

    def unavailable(_self, _request_id: str):
        raise AttachmentsUnavailable("Вымышленная недоступность хранилища вложений")

    monkeypatch.setattr(
        InMemoryAttachmentsStore, "list_by_request", unavailable, raising=True
    )
    assert client.delete(f"/requests/{rid}", headers=admin_headers).status_code == 200
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 404


def test_failed_db_delete_keeps_files(
    client, hr_headers, admin_headers, requests_store, attachments_store,
    files_store, monkeypatch
):
    """Сбой БД при удалении — файлы остаются: заявка жива, терять нечего.

    Это и есть порядок «собрать пути → удалить из БД → снять файлы»: файлы
    снимаются только после успешного удаления заявки, поэтому недоступность
    хранилища не превращается в потерю данных."""
    rid = _create(client, hr_headers, steps=[{"owner_group": "SED_STEP_BUH"}])["id"]
    path = _attach(attachments_store, files_store, rid, "scan.docx")

    def down(_request_id: str) -> None:
        raise RequestsUnavailable("Вымышленная недоступность хранилища заявок")

    monkeypatch.setattr(requests_store, "delete", down)
    assert client.delete(f"/requests/{rid}", headers=admin_headers).status_code == 503
    assert path in files_store._files, "файл снесли, хотя заявка осталась"
    assert client.get(f"/requests/{rid}", headers=admin_headers).status_code == 200

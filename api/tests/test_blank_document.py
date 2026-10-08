# Тесты печати бланка ИЗ ДАННЫХ (волна «Справочник бланков»):
# генератор docs.build_blank_document (python-docx, два пресета макета из снимка
# blank_layout), санирование текстов этапа (HTML из визуального редактора),
# QR на заявку, ответственные (ФИО персональных исполнителей / группы AD),
# generate_bypass без файлов-шаблонов и ручка печати (макет в blank_kind,
# пустые шаги — 422). Все ПДн вымышленные.

from __future__ import annotations

import base64
import inspect
import io
import os
import sys
import zipfile
from datetime import timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import docs as docs_module  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.docs import (  # noqa: E402
    DEFAULT_BLANK_LAYOUT,
    LAYOUT_PRESETS,
    BypassResult,
    build_blank_document,
    generate_bypass,
    resolve_blank_layout,
)
from app.main import app  # noqa: E402
from app.requests import _Request, _Step, _utcnow, get_memory_requests_store  # noqa: E402
from app.requests import (  # noqa: E402
    IN_APPROVAL,
    STEP_PENDING,
    get_requests_store,
)
from app.settings_routes import get_settings_store  # noqa: E402

TEST_ALLOWED = "SED_HR,SED_ADMINS"
TEST_ADMINS = "SED_ADMINS"
TEST_HR = "SED_HR"
TEST_STEP_PREFIX = "SED_STEP_"
BASE_URL = "https://sed-mock.local"

FAKE_ENTERPRISE_CODE = "ВЫМЫШЛЕННОЕ-ПРЕДПРИЯТИЕ"
FAKE_ENTERPRISE_NAME = "Вымышленное предприятие"
FAKE_SERVICE = "Служба вымышленного учета"
FAKE_POSITION = "Старший вымышленный кассир"
FAKE_FIO = "Вымышленный Сотрудник Полный"
FAKE_TAB_NUM = "В-0001"
FAKE_BLANK = "Бланк вымышленного увольнения"
FAKE_STAGE = "Бухгалтерия вымышленная"
FAKE_ASSIGNEE_FIO = "Бухгалтер Вымышленный"
FAKE_GROUP = "SED_STEP_BUH"
QR_URL = BASE_URL + "/requests/REQ-0001"


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _hr_headers() -> dict:
    """Заголовки сотрудника ОК (роль печати)."""
    return {
        "X-Mock-Sam": "ok.vymyshlennaya",
        "X-Mock-Fio": _b64("Вымышленный Пользователь Тестовый"),
        "X-Mock-Mail": _b64("ok.vymyshlennaya@example.com"),
        "X-Mock-Department": _b64("Вымышленный отдел"),
        "X-Mock-Title": _b64("Вымышленная должность"),
        "X-Mock-Groups": TEST_HR,
    }


def _context(**overrides) -> dict:
    """Контекст бланка из данных заявки (как build_bypass_context documents.py)."""
    context = {
        "request_id": "REQ-0001",
        "fio": FAKE_FIO,
        "position": FAKE_POSITION,
        "department": FAKE_SERVICE,
        "tab_num": FAKE_TAB_NUM,
        "enterprise": FAKE_ENTERPRISE_NAME,
        "blank_name": FAKE_BLANK,
        "blank_version": 2,
        "blank_layout": "office",
        "qr_url": QR_URL,
        "steps": [
            {
                "order": 1,
                "owner": FAKE_GROUP,
                "title": FAKE_STAGE,
                "stage_lines": ["Проверить расчёты", "Подписать акт"],
                "fio": FAKE_ASSIGNEE_FIO,
                "status": "Согласовано",
                "done_at": "05.10.2026 12:00",
            },
            {
                "order": 2,
                "owner": "SED_STEP_HR",
                "title": "Отдел кадров вымышленный",
                "stage_lines": ["Оформить приказ"],
                "fio": "",
                "status": STEP_PENDING,
                "done_at": "",
            },
        ],
    }
    context.update(overrides)
    return context


def _docx_text(blob: bytes) -> str:
    """Текст собранного документа (XML как есть — тест ищет вхождения)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _docx_names(blob: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return archive.namelist()


def _opened(blob: bytes):
    from docx import Document

    return Document(io.BytesIO(blob))


def _steps_rows(blob: bytes) -> list[list[str]]:
    """Строки таблицы шагов (последняя таблица документа)."""
    table = _opened(blob).tables[-1]
    return [[cell.text for cell in row.cells] for row in table.rows]


def _request(**overrides) -> _Request:
    """Заявка со снимком бланка и одним шагом (печатная форма)."""
    fields = dict(
        id="REQ-0001",
        status=IN_APPROVAL,
        route_origin="auto",
        enterprise=FAKE_ENTERPRISE_CODE,
        fio=FAKE_FIO,
        tab_num=FAKE_TAB_NUM,
        department=FAKE_SERVICE,
        position=FAKE_POSITION,
        created_by="ok.vymyshlennaya",
        blank_id=7,
        blank_name=FAKE_BLANK,
        blank_version=2,
        blank_layout="line",
        steps=[
            _Step(
                order=1,
                owner_group=FAKE_GROUP,
                assignee="step.buhgalter",
                status=STEP_PENDING,
                expires_at=_utcnow() + timedelta(days=3),
                stage_title=FAKE_STAGE,
                stage_lines=["Проверить расчёты"],
            )
        ],
    )
    fields.update(overrides)
    return _Request(**fields)


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


class MemSettingsStore:
    """Хранилище настроек в сид-формате (значения — строки JSON)."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key):
        return self.values.get(key)


@pytest.fixture(autouse=True)
def settings_store():
    """doc_templates заполнен намеренно: печать его читать не должна."""
    store = MemSettingsStore(
        {
            "doc_templates": (
                '[{"service": "%s", "category": "линейный", '
                '"body": "ШАБЛОН-НЕ-ИСПОЛЬЗУЕТСЯ", "file": "blank.docx"}]'
                % FAKE_SERVICE
            ),
            "enterprises": (
                '[{"code": "%s", "name": "%s"}]'
                % (FAKE_ENTERPRISE_CODE, FAKE_ENTERPRISE_NAME)
            ),
        }
    )
    app.dependency_overrides[get_settings_store] = lambda: store
    yield store
    app.dependency_overrides.pop(get_settings_store, None)


@pytest.fixture
def requests_store():
    store = get_memory_requests_store()
    app.dependency_overrides[get_requests_store] = lambda: store
    store.reset()
    yield store
    store.reset()
    app.dependency_overrides.pop(get_requests_store, None)


def _fake_result(pdf_path: str) -> BypassResult:
    return BypassResult(
        generated=True,
        docx_path=pdf_path.replace(".pdf", ".docx"),
        pdf_path=pdf_path,
        qr_payload=QR_URL,
    )


# ---------------------------------------------------------------------------
# Выбор макета: единственная точка resolve_blank_layout + два пресета
# ---------------------------------------------------------------------------

def test_resolve_blank_layout_office_by_default():
    """Пустой/неизвестный снимок макета — office (дефолт миграции 0012)."""
    assert DEFAULT_BLANK_LAYOUT == "office"
    assert resolve_blank_layout(None) == "office"
    assert resolve_blank_layout("") == "office"
    assert resolve_blank_layout("  ") == "office"
    assert resolve_blank_layout("неизвестный макет") == "office"
    assert resolve_blank_layout("линейный") == "office"


def test_resolve_blank_layout_line_ignores_case_and_spaces():
    """line из снимка — макет line; регистр и пробелы не важны."""
    assert resolve_blank_layout("line") == "line"
    assert resolve_blank_layout(" LINE ") == "line"
    assert resolve_blank_layout("Line") == "line"


def test_layout_presets_cover_contract_layouts():
    """Пресеты ровно office/line, дефолт среди них, доли колонок дают 100%."""
    assert set(LAYOUT_PRESETS) == {"office", "line"}
    assert DEFAULT_BLANK_LAYOUT in LAYOUT_PRESETS
    for name, preset in LAYOUT_PRESETS.items():
        shares = preset["step_widths"]
        assert len(shares) == 4, name
        assert abs(sum(shares) - 1.0) < 0.001, name


# ---------------------------------------------------------------------------
# Генератор: шапка, таблица шагов, подвал
# ---------------------------------------------------------------------------

def test_build_blank_document_is_docx_with_header_table_and_footer():
    """DOCX из данных: название бланка, поля сотрудника, таблица шагов, подвал."""
    blob = build_blank_document(_context())
    assert blob[:2] == b"PK"
    text = _docx_text(blob)
    for expected in (
        FAKE_BLANK,      # название бланка из снимка
        FAKE_FIO,        # ФИО
        FAKE_POSITION,   # должность
        FAKE_SERVICE,    # служба
        FAKE_TAB_NUM,    # табельный номер
        FAKE_ENTERPRISE_NAME,  # предприятие
    ):
        assert expected in text, expected
    rows = _steps_rows(blob)
    assert rows[0] == ["№", "Этап", "Ответственный", "Отметка/дата"]
    assert rows[1][0] == "1"
    assert FAKE_STAGE in rows[1][1]      # название этапа
    assert "Проверить расчёты" in rows[1][1]  # его пункты
    assert rows[1][2] == FAKE_ASSIGNEE_FIO  # ФИО персонального исполнителя
    assert "Согласовано" in rows[1][3] and "05.10.2026 12:00" in rows[1][3]
    assert rows[2][2] == "SED_STEP_HR"   # группа AD — печатаем как есть
    footer = _opened(blob).sections[0].footer.paragraphs[0].text
    assert "REQ-0001" in footer and FAKE_BLANK in footer and "v2" in footer


def test_build_blank_document_step_mark_without_date_has_paper_line():
    """Шаг без даты: статус и место под росчерк (печать от руки)."""
    rows = _steps_rows(build_blank_document(_context()))
    marks = rows[2][3].splitlines()
    assert marks[0] == STEP_PENDING
    assert set(marks[1]) == {"_"}


def test_build_blank_document_layout_comes_from_snapshot():
    """Макет берётся из снимка blank_layout, а не из службы/настроек."""
    office = _opened(build_blank_document(_context(blank_layout="office")))
    line = _opened(build_blank_document(_context(blank_layout="line")))
    # Линейный пресет компактнее офисного (поля страницы и кегль меньше).
    assert line.sections[0].left_margin < office.sections[0].left_margin
    assert line.styles["Normal"].font.size < office.styles["Normal"].font.size
    # Без макета в снимке — office (дефолт), а не иной пресет.
    default = _opened(build_blank_document(_context(blank_layout="")))
    assert default.sections[0].left_margin == office.sections[0].left_margin


def test_build_blank_document_qr_image_and_caption():
    """QR подставлен картинкой (qr_url контекста), рядом читается ссылка."""
    blob = build_blank_document(_context())
    assert any(name.startswith("word/media/") for name in _docx_names(blob))
    assert QR_URL in _docx_text(blob)


def test_build_blank_document_without_qr_url_still_builds():
    """Без qr_url (его кладёт generate_bypass) документ тоже собирается."""
    context = _context()
    context.pop("qr_url")
    blob = build_blank_document(context)
    assert not any(name.startswith("word/media/") for name in _docx_names(blob))


def test_build_blank_document_drops_forbidden_context_fields():
    """ПДн-поля в бланок не попадают (чистит sanitize_context)."""
    blob = build_blank_document(_context(vacation_balance="ДВАДЦАТЬ ВОСЕМЬ ДНЕЙ"))
    assert "ДВАДЦАТЬ" not in _docx_text(blob)


def test_build_blank_document_title_falls_back_to_doc_type():
    """Без названия бланка печатаем вид документа (doc_type_code)."""
    context = _context()
    context["blank_name"] = ""
    context["doc_type_code"] = "УВОЛЬНЕНИЕ"
    assert "УВОЛЬНЕНИЕ" in _docx_text(build_blank_document(context))


# ---------------------------------------------------------------------------
# Санирование текстов этапа (HTML из визуального редактора)
# ---------------------------------------------------------------------------

def test_build_blank_document_keeps_only_allowed_tags():
    """В печать идут разрешённые теги; служебные и прочие — без разметки."""
    context = _context()
    context["steps"] = [
        {
            "order": 1,
            "owner": FAKE_GROUP,
            "title": "Этап <b>итог</b>",
            "stage_lines": [
                "Проверить <b>расчёты</b> и <i>долги</i>",
                "Подпись <script>alert(1)</script>",
                "Проверить <img src=x onerror=alert(2)> табель",
                "Сумма 5 < 7 и 8 > 3",
            ],
            "fio": "",
            "status": STEP_PENDING,
            "done_at": "",
        }
    ]
    blob = build_blank_document(context)
    text = _docx_text(blob)
    stage_text = _steps_rows(blob)[1][1]
    # Текст печатается, форматирование разрешённых тегов — работает.
    assert "Проверить расчёты и долги" in stage_text
    assert "Подпись" in stage_text
    assert "Проверить  табель" in stage_text
    # Содержимое служебных тегов и атрибуты прочих — на бланок не попадают.
    assert "alert" not in text
    assert "onerror" not in text
    assert "<img" not in text
    # Голые спецсимволы остаются текстом (Word экранирует их сам).
    assert "Сумма 5 < 7 и 8 > 3" in stage_text
    stage_paragraphs = _opened(blob).tables[-1].rows[1].cells[1].paragraphs
    bolded = [run.text for paragraph in stage_paragraphs for run in paragraph.runs if run.bold]
    italiced = [run.text for paragraph in stage_paragraphs for run in paragraph.runs if run.italic]
    assert "расчёты" in bolded
    assert "долги" in italiced


def test_build_blank_document_breaks_line_on_allowed_tag():
    """Разрешённый перенос строки (br) печатается переносом, а не тегом."""
    context = _context()
    context["steps"] = [
        {
            "order": 1,
            "owner": FAKE_GROUP,
            "title": "Этап",
            "stage_lines": ["Первый пункт<br>второй пункт"],
            "fio": "",
            "status": STEP_PENDING,
            "done_at": "",
        }
    ]
    assert _steps_rows(build_blank_document(context))[1][1].splitlines() == [
        "Этап",
        "Первый пункт",
        "второй пункт",
    ]


# ---------------------------------------------------------------------------
# generate_bypass: DOCX из данных -> PDF (LibreOffice), без файлов-шаблонов
# ---------------------------------------------------------------------------

def test_generate_bypass_signature_has_no_template_arguments():
    """Пути через файлы-шаблоны в печати нет: ни body, ни .docx-файла."""
    params = inspect.signature(generate_bypass).parameters
    assert "template_body" not in params
    assert "template_file" not in params


def test_generate_bypass_builds_from_context_and_qr_url(tmp_path, monkeypatch):
    """В сборщик идёт контекст с qr_url = URL заявки (payload QR)."""
    captured = {}
    monkeypatch.setattr(
        docs_module, "build_blank_document",
        lambda context: captured.update(context) or b"docx-bytes",
    )
    monkeypatch.setattr(docs_module, "_render_qr_png", lambda url: b"qr-bytes")
    monkeypatch.setattr(docs_module, "_soffice_binary", lambda: None)
    result = generate_bypass(
        request_id="REQ-0001", version="v1", context=_context(),
        base_url=BASE_URL, files_dir=str(tmp_path),
    )
    assert captured["qr_url"] == QR_URL
    assert captured["blank_layout"] == "office"
    assert result.generated is False  # нет soffice — PDF не создан


def test_generate_bypass_offline_missing_library_reason(tmp_path, monkeypatch):
    """Нет библиотеки рендера → generated=False с причиной «офлайн»."""

    def missing(context):
        raise ModuleNotFoundError("No module named 'docx'")

    monkeypatch.setattr(docs_module, "build_blank_document", missing)
    result = generate_bypass(
        request_id="REQ-0001", version="v1", context=_context(),
        base_url=BASE_URL, files_dir=str(tmp_path),
    )
    assert result.generated is False
    assert result.reason.startswith("офлайн")
    assert "docx" in result.reason


# ---------------------------------------------------------------------------
# Ручка печати: макет из снимка в blank_kind, doc_templates не читается
# ---------------------------------------------------------------------------

def test_print_uses_blank_snapshot_and_reports_layout(
    client, requests_store, monkeypatch, tmp_path
):
    """Печать собирается из снимка бланка заявки; blank_kind = макет бланка."""
    requests_store.create(_request())
    captured = {}
    pdf = tmp_path / "bypass.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    def fake(**kwargs):
        captured.update(kwargs)
        return _fake_result(str(pdf))

    monkeypatch.setattr("app.documents.generate_bypass", fake)
    response = client.post("/requests/REQ-0001/print", headers=_hr_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] is True and body["blank_kind"] == "line"
    assert "template_body" not in captured and "template_file" not in captured
    context = captured["context"]
    assert context["blank_name"] == FAKE_BLANK
    assert context["blank_layout"] == "line"
    assert context["tab_num"] == FAKE_TAB_NUM
    assert context["enterprise"] == FAKE_ENTERPRISE_NAME  # названием из справочника
    assert context["steps"][0]["title"] == FAKE_STAGE
    assert context["steps"][0]["stage_lines"] == ["Проверить расчёты"]


def test_print_blank_kind_defaults_to_office(
    client, requests_store, monkeypatch, tmp_path
):
    """Без макета в снимке (старая заявка) печатаем офисный бланк."""
    requests_store.create(_request(blank_layout=None, blank_name=None, blank_version=None))
    pdf = tmp_path / "bypass.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(
        "app.documents.generate_bypass", lambda **kwargs: _fake_result(str(pdf))
    )
    body = client.post("/requests/REQ-0001/print", headers=_hr_headers()).json()
    assert body["blank_kind"] == "office"


def test_print_ignores_doc_templates_setting(client, requests_store, monkeypatch, tmp_path):
    """Ключ doc_templates печать не читает: маркер шаблона в данных печати нет."""
    requests_store.create(_request())
    captured = {}
    pdf = tmp_path / "bypass.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    def fake(**kwargs):
        captured.update(kwargs)
        return _fake_result(str(pdf))

    monkeypatch.setattr("app.documents.generate_bypass", fake)
    response = client.post("/requests/REQ-0001/print", headers=_hr_headers())
    assert response.status_code == 200, response.text
    assert "ШАБЛОН-НЕ-ИСПОЛЬЗУЕТСЯ" not in str(captured)
    assert base64.b64decode(response.json()["pdf_b64"]) == b"%PDF-1.4 fake"


def test_print_422_without_steps(client, requests_store):
    """Шагов нет — 422 с понятным текстом (даже при заполненном doc_templates)."""
    requests_store.create(_request(steps=[]))
    response = client.post("/requests/REQ-0001/print", headers=_hr_headers())
    assert response.status_code == 422
    assert "шаг" in response.json()["detail"].lower()


def test_print_offline_is_not_500(client, requests_store, monkeypatch):
    """Офлайн/нет LibreOffice → 200 {"generated": false, reason}, не 500."""
    requests_store.create(_request())

    def missing(context):
        raise ModuleNotFoundError("No module named 'PIL'")

    monkeypatch.setattr(docs_module, "build_blank_document", missing)
    response = client.post("/requests/REQ-0001/print", headers=_hr_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["generated"] is False
    assert "PIL" in body["reason"]


def test_print_forbidden_for_step_owner(client, requests_store):
    """Печать — только разрешенной группе: владелец шага 403, аноним 401."""
    requests_store.create(_request())
    owner = {
        "X-Mock-Sam": "step.buhgalter",
        "X-Mock-Fio": _b64(FAKE_ASSIGNEE_FIO),
        "X-Mock-Mail": _b64("step.buhgalter@example.com"),
        "X-Mock-Department": _b64("Бухгалтерия"),
        "X-Mock-Title": _b64("Главный бухгалтер"),
        "X-Mock-Groups": FAKE_GROUP,
    }
    assert client.post("/requests/REQ-0001/print", headers=owner).status_code == 403
    assert client.post("/requests/REQ-0001/print", headers={}).status_code == 401
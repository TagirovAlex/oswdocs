# Печать бегунка собирается ИЗ ДАННЫХ заявки (волна «Справочник бланков»):
# файлы-шаблоны .docx как источник оформления удалены, поэтому ручек
# /settings/doc-templates/files/* и хелпера docs._render_docx_from_file в проекте
# больше нет, ключ настроек doc_templates вне контракта (см. test_settings_api.py).
# Здесь проверяется, что печать работает без файлов-шаблонов и без их хелпера.

from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import docs as docs_module  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def settings_override(tmp_path):
    """Тестовые группы + FILES_DIR в tmp_path (печать пишет временные файлы)."""
    settings = Settings(
        ALLOWED_AD_GROUPS="SED_HR,SED_ADMINS,SED_HR_ADMIN",
        ADMIN_GROUPS="SED_ADMINS",
        HR_GROUPS="SED_HR",
        HR_ADMIN_GROUPS="SED_HR_ADMIN",
        STEP_GROUP_PREFIX="SED_STEP_",
        FILES_DIR=str(tmp_path / "files"),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


def test_render_docx_from_file_is_gone():
    """Хелпера рендера .docx-файла в модуле docs больше нет (иначе он бы импортировал
    docxtpl, которого на офлайн-машине нет)."""
    assert not hasattr(docs_module, "_render_docx_from_file")


def test_settings_routes_has_no_doc_template_file_handles():
    """Ручек файлов-бланков в приложении нет: путей /settings/doc-templates нет."""
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not [path for path in paths if "doc-templates" in path]


def test_generate_bypass_has_no_template_file_argument():
    """У печати нет параметров файла/текста шаблона (сборка из данных)."""
    params = inspect.signature(docs_module.generate_bypass).parameters
    assert "template_file" not in params
    assert "template_body" not in params


def test_generate_bypass_ignores_template_file_on_disk(tmp_path, monkeypatch):
    """Файл .docx в FILES_DIR/templates печать не открывает: сборщик получает
    контекст заявки, шаблон остаётся лежать нетронутым."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    tpl = tpl_dir / "blank.docx"
    tpl.write_bytes(b"docx-bytes")
    calls = []

    def fake_build(context):
        calls.append(context)
        return b"docx-from-data"

    monkeypatch.setattr(docs_module, "build_blank_document", fake_build)
    monkeypatch.setattr(docs_module, "_render_qr_png", lambda url: b"qr")
    monkeypatch.setattr(docs_module, "_soffice_binary", lambda: None)
    result = docs_module.generate_bypass(
        request_id="REQ-0001", version="v1", context={"fio": "Вымышленный Сотрудник"},
        base_url="https://x", files_dir=str(tmp_path),
    )
    assert len(calls) == 1
    assert calls[0]["qr_url"] == "https://x/requests/REQ-0001"
    assert tpl.read_bytes() == b"docx-bytes"
    assert result.generated is False  # нет soffice — PDF не создан


def test_generate_bypass_offline_reason_names_missing_library(tmp_path, monkeypatch):
    """Нет библиотеки сборки (python-docx) → generated=False с причиной «офлайн»."""

    def missing(context):
        raise ModuleNotFoundError("No module named 'docx'")

    monkeypatch.setattr(docs_module, "build_blank_document", missing)
    result = docs_module.generate_bypass(
        request_id="REQ-0001", version="v1", context={},
        base_url="https://x", files_dir=str(tmp_path),
    )
    assert result.generated is False
    assert result.reason.startswith("офлайн")
    assert "docx" in result.reason

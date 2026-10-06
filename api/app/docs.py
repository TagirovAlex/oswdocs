# Интерфейсы и offline-реализация генерации бланков (волна B3).
# Генерация — логически часть worker, не api-запроса (см. скил mail-docs).
# Здесь только stdlib: DOCX собирается вручную (zip + document.xml),
# QR — заглушка (PNG с URL заявки в tEXt-блоке, матрица — на стенде
# библиотекой qrcode). Боевой рендер (python-docx-template/Jinja +
# настоящий QR + LibreOffice PDF) подменяется без смены вызовов.
# ПДн (остаток отпуска и др.) в бланки не включаются — см. sanitize_context.
# Зависимости стенда (python-docx-template/qrcode/LibreOffice) — раскомментирует стенд, offline только stdlib.

from __future__ import annotations

import io
import logging
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Protocol

# Логгер модуля: сбои генерации бегунка пишем в журнал (см. generate_bypass).
_LOGGER = logging.getLogger(__name__)

# Метка PDF-стаба: рендер настоящего PDF — только на стенде через LibreOffice.
PDF_STUB_MARK = "рендер на стенде (LibreOffice)"

# Поля, запрещенные в бланках/письмах (ПДн, только для ОК).
FORBIDDEN_CONTEXT_FIELDS = frozenset({"vacation_balance", "vacation", "mail", "manager_dn"})

# Подстановка вида {{ имя }} (минимальный Jinja для offline; на стенде — настоящий Jinja).
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def sanitize_context(context: Dict[str, object]) -> Dict[str, object]:
    """Убрать из контекста ПДн-поля (в бланки/письма они не включаются)."""
    return {k: v for k, v in context.items() if k not in FORBIDDEN_CONTEXT_FIELDS}


def render_text(template: str, context: Dict[str, object]) -> str:
    """Подставить {{var}} из контекста; неизвестное — пустая строка."""
    safe = sanitize_context(context)
    return _PLACEHOLDER_RE.sub(lambda m: str(safe.get(m.group(1), "")), template)


def request_url(base_url: str, request_id: str) -> str:
    """Ссылка на заявку для QR (базовый URL — из настроек, не из кода)."""
    return base_url.rstrip("/") + "/requests/" + request_id


def next_version(existing: List[int]) -> int:
    """Следующая версия бланка (v1/v2...); старые версии не удаляются."""
    return (max(existing) if existing else 0) + 1


def qr_stub_png(url: str) -> bytes:
    """Заглушка QR: валидный PNG, URL заявки — в tEXt-блоке (читается тестом).

    Настоящая QR-матрица генерируется на стенде библиотекой qrcode;
    здесь — белый квадрат 8x8 + текстовый чанк с URL (stdlib: struct+zlib).
    """
    url_bytes = url.encode("utf-8")

    def chunk(ctype: bytes, data: bytes) -> bytes:
        # Длина + тип + данные + CRC32 (формат PNG).
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)  # 8x8 truecolor
    # tEXt: ключевое слово + нулевой байт + текст (URL заявки).
    text = b"RequestURL\x00" + url_bytes
    # Белый квадрат 8x8 (каждая строка: фильтр-байт + пиксели).
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * 8 for _ in range(8))
    return sig + chunk(b"IHDR", ihdr) + chunk(b"tEXt", text) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _xml_escape(text: str) -> str:
    """Экранирование для document.xml."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _paragraph(text: str) -> str:
    """Один абзац WordprocessingML (каждая строка — свой абзац)."""
    return "<w:p><w:r><w:t xml:space=\"preserve\">" + _xml_escape(text) + "</w:t></w:r></w:p>"


@dataclass(frozen=True)
class RenderedDoc:
    """Результат рендера бланка (байты + метаданные версии)."""

    request_id: str
    version: int
    url: str
    docx: bytes
    pdf_stub: bytes


class DocRenderer(Protocol):
    """Граница рендера бланков (на стенде — реализация на python-docx-template)."""

    def render_docx(
        self,
        request_id: str,
        version: int,
        template_body: str,
        context: Dict[str, object],
        base_url: str,
    ) -> bytes:
        """Собрать DOCX-бегунок из тела шаблона doc_templates (Jinja)."""
        ...  # pragma: no cover

    def render_pdf_stub(self, request_id: str, version: int) -> bytes:
        """Стаб PDF с пометкой про LibreOffice (настоящий рендер — на стенде)."""
        ...  # pragma: no cover


class StdlibDocxRenderer:
    """Offline-рендер DOCX на stdlib (zipfile): шаблон + QR-заглушка + версия."""

    def render_docx(
        self,
        request_id: str,
        version: int,
        template_body: str,
        context: Dict[str, object],
        base_url: str,
    ) -> bytes:
        """Собрать DOCX: отрендеренный текст + request_id + версия + URL заявки."""
        url = request_url(base_url, request_id)
        body = render_text(template_body, context)
        lines = [body, "Заявка: " + request_id, "Версия: v" + str(version), "QR: " + url]
        paragraphs = "".join(_paragraph(line) for line in lines)
        document_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            "<w:body>" + paragraphs + "</w:body></w:document>"
        )
        content_types = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
            "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
            "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
            "<Default Extension=\"png\" ContentType=\"image/png\"/>"
            "<Override PartName=\"/word/document.xml\" "
            "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/>"
            "</Types>"
        )
        rels = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            "<Relationship Id=\"rId1\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" "
            "Target=\"word/document.xml\"/></Relationships>"
        )
        # Связь картинки QR с документом (заглушка; на стенде — настоящая вставка).
        doc_rels = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            "<Relationship Id=\"rIdQr\" "
            "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image\" "
            "Target=\"media/qr.png\"/></Relationships>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("_rels/.rels", rels)
            zf.writestr("word/document.xml", document_xml)
            zf.writestr("word/_rels/document.xml.rels", doc_rels)
            zf.writestr("word/media/qr.png", qr_stub_png(url))
        return buf.getvalue()

    def render_pdf_stub(self, request_id: str, version: int) -> bytes:
        """Минимальный PDF-стаб с пометкой про стенд (не настоящий рендер)."""
        # Минимальный однострочный PDF: текст с пометкой и номером заявки/версией.
        text = "Bypass %s v%d (%s)" % (request_id, version, PDF_STUB_MARK)
        # Экранирование скобок по спецификации PDF (иначе строка рвется).
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = "BT /F1 12 Tf 72 720 Td (" + escaped + ") Tj ET"
        content_bytes = content.encode("utf-8")
        header = b"%PDF-1.4\n"
        # Комментарий с пометкой дословно (проверяется тестом; комментарии в PDF легальны).
        comment = ("% " + PDF_STUB_MARK + "\n").encode("utf-8")
        header = header + comment
        body = (
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R>>endobj\n"
            + b"4 0 obj<</Length "
            + str(len(content_bytes)).encode("ascii")
            + b">>stream\n"
            + content_bytes
            + b"\nendstream\nendobj\n"
        )
        trailer = b"trailer<</Root 1 0 R>>\n%%EOF"
        return header + body + trailer

    def render(
        self,
        request_id: str,
        version: int,
        template_body: str,
        context: Dict[str, object],
        base_url: str,
    ) -> RenderedDoc:
        """Удобная обертка: DOCX + PDF-стаб одним вызовом."""
        url = request_url(base_url, request_id)
        return RenderedDoc(
            request_id=request_id,
            version=version,
            url=url,
            docx=self.render_docx(request_id, version, template_body, context, base_url),
            pdf_stub=self.render_pdf_stub(request_id, version),
        )


# ---------------------------------------------------------------------------
# W3a: боевая генерация бегунка (DOCX -> PDF + QR) на стенде.
# Зависимости стенда (python-docx-template/qrcode/Pillow/LibreOffice)
# импортируются лениво: офлайн их нет — generate_bypass вернет generated=False
# с причиной (не 500); прочие сбои не глотаются. Тексты шаблонов — только из
# doc_templates/settings, хардкода нет. Профиль soffice — в /tmp (см.
# _convert_to_pdf_report): HOME контейнера не существует, софт без профиля RC=77.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BypassResult:
    """Итог генерации бегунка: файлы на диске или причина отказа (не ошибка)."""

    generated: bool = False
    reason: str = ""
    docx_path: str = ""
    pdf_path: str = ""
    qr_payload: str = ""


def _norm_position(value: object) -> str:
    """Должность к сравнению: регистр/пробелы не различаются (как _norm ФИО)."""
    return " ".join(str(value or "").strip().lower().split())


def _set_positions(position_sets: object, set_name: object) -> list[str]:
    """Должности именованного набора (position_sets): нормализованные, без пустых."""
    if not set_name or not isinstance(position_sets, list):
        return []
    wanted = _norm_position(set_name)
    for item in position_sets:
        if (
            isinstance(item, dict)
            and _norm_position(item.get("name")) == wanted
            and isinstance(item.get("positions"), list)
        ):
            return [
                norm
                for norm in (_norm_position(p) for p in item["positions"])
                if norm
            ]
    return []


def _matches_field(entry_value: object, request_value: object) -> bool:
    """Совпадение поля бланка: пустое в записи — wildcard (любое значение)."""
    entry = str(entry_value or "").strip()
    if not entry:
        return True
    return entry == str(request_value or "").strip()


def find_doc_template(
    templates: object,
    service: str,
    category: str | None,
    position: object = None,
    position_sets: object = None,
) -> dict | None:
    """Подбор шаблона бегунка: служба+категория (поля 1С), затем набор должностей.

    Пустые служба/категория в записи — wildcard (бланк только по набору:
    ручные заявки часто без категории). Среди подходящих (с непустым
    body/file) приоритет — бланку, чей набор (position_set) содержит должность
    сотрудника; иначе — бланк без набора (по умолчанию); иначе None.
    Без должности — первый подходящий (прежнее поведение).
    """
    if not isinstance(templates, list):
        return None
    matched = [
        item
        for item in templates
        if isinstance(item, dict)
        and _matches_field(item.get("service"), service)
        and _matches_field(item.get("category"), category)
        and ((item.get("body") or "").strip() or (item.get("file") or "").strip())
    ]
    if not matched:
        return None
    wanted = _norm_position(position)
    if not wanted:
        return matched[0]
    for item in matched:
        if wanted in _set_positions(position_sets, item.get("position_set")):
            return item
    for item in matched:
        if not (item.get("position_set") or "").strip():
            return item
    return None


def manual_bypass_body(request: object) -> str:
    """Тело бегунка без шаблона: ручной конструктор из шагов заявки
    (состав маршрута; тексты-шаблоны в код не зашиты)."""
    lines = [
        "Сотрудник: {{ fio }}",
        "Служба: {{ department }}",
        "Должность: {{ position }}",
        "",
        "Маршрут согласования:",
    ]
    for step in sorted(request.steps, key=lambda s: s.order):
        owner = getattr(step, "assignee", None) or step.owner_group
        lines.append(f"{step.order}. {owner} — {step.status}")
    return "\n".join(lines)


def _step_done_text(value: object) -> str:
    """Дата отметки шага строкой ДД.ММ.ГГГГ ЧЧ:ММ (пусто — шаг не отмечен).

    Принимаем и datetime модели шага, и строку (уже отформатированную дату) —
    контекст собирается из «птичьего» объекта запроса, как и весь остальной."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    text = str(value).strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return text[:16].replace("T", " ")


def build_bypass_context(request: object) -> Dict[str, object]:
    """Контекст бегунка: поля 1С заявки без ПДн (mail/отпуск — не включаем)
    + шаги маршрута (владельцы шагов — участники процесса, не ПДн).

    По каждому шагу отдаём снимок этапа из справочников (position/stage_lines)
    и отметку (done_at): бланк печатается по маршруту, собранному из этапов.
    ФИО исполнителя (fio) здесь пустое намеренно: ФИО подставляет documents.py
    по логину исполнителя (assignee) из зеркала AD — docs.py про хранилище
    заявок не знает (импорт был бы циклическим, его не делаем)."""
    steps = []
    for step in sorted(request.steps, key=lambda s: s.order):
        owner = getattr(step, "assignee", None) or step.owner_group
        stage_lines = [
            str(line)
            for line in (getattr(step, "stage_lines", None) or [])
            if str(line).strip()
        ]
        steps.append(
            {
                "order": step.order,
                "owner": owner,
                "status": step.status,
                "position": str(getattr(step, "stage_title", None) or step.owner_group or ""),
                # Логин персонального исполнителя: documents.py по нему подставит
                # ФИО из AD (в бланке колонка «Должность/ФИО»).
                "assignee": getattr(step, "assignee", None) or "",
                "fio": "",
                "stage_lines": stage_lines or [owner],
                "done_at": _step_done_text(getattr(step, "done_at", None)),
            }
        )
    return {
        "request_id": request.id,
        "fio": request.fio,
        "department": request.department,
        "position": request.position,
        "category": request.category or "",
        "enterprise": request.enterprise or "",
        "steps": steps,
    }


def _render_docx_stand(template_body: str, context: Dict[str, object]) -> bytes:
    """DOCX через python-docx-template (docxtpl): body — Jinja-подобный текст."""
    import io

    from docx import Document
    from docxtpl import DocxTemplate

    base = Document()
    for line in template_body.splitlines():
        base.add_paragraph(line)
    buf = io.BytesIO()
    base.save(buf)
    buf.seek(0)
    tpl = DocxTemplate(buf)
    tpl.render(context)
    out = io.BytesIO()
    tpl.save(out)
    return out.getvalue()


def _render_docx_from_file(
    template_path: str | Path, context: Dict[str, object], url: str
) -> bytes:
    """DOCX через python-docx-template (docxtpl) из НАСТОЯЩЕГО .docx-файла.

    Шапка/строки/подвал/вёрстка живут в файле; {{ qr }} подменяется
    сгенерированным QR (InlineImage; размер 30x30 мм — константа вёрстки,
    не настройка). Ожидает docxtpl/qrcode (стенд), как _render_docx_stand."""
    from docx.shared import Mm
    from docxtpl import DocxTemplate, InlineImage

    tpl = DocxTemplate(template_path)
    render_context = dict(context)
    render_context["qr"] = InlineImage(
        tpl, io.BytesIO(_render_qr_png(url)), width=Mm(30), height=Mm(30)
    )
    tpl.render(render_context)
    out = io.BytesIO()
    tpl.save(out)
    return out.getvalue()


def _render_qr_png(url: str) -> bytes:
    """QR-матрица библиотекой qrcode (payload — URL заявки)."""
    import io

    import qrcode

    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _soffice_binary() -> str | None:
    """Путь к soffice (системный LibreOffice на ВМ) либо None."""
    import shutil

    return shutil.which("soffice")


# Сколько последних строк stderr soffice показываем в причине отказа.
_SOFFICE_STDERR_LINES = 3


@dataclass(frozen=True)
class SofficeReport:
    """Итог конвертации: путь к PDF (либо None) + служебная диагностика."""

    pdf_path: str | None = None
    detail: str = ""


def _soffice_detail(returncode: int, stderr: str) -> str:
    """Код возврата soffice и хвост stderr — только служебный вывод конвертера
    (пути файлов, коды), ПДн заявки туда не попадают. Хвост ограничен, чтобы
    ответ API не раздувался шумом вроде сообщений dconf/fontconfig."""
    tail = [line for line in (stderr or "").splitlines() if line.strip()]
    detail = f"код возврата {returncode}"
    if tail:
        detail += ": " + " | ".join(line.strip()[:200] for line in tail[-_SOFFICE_STDERR_LINES:])
    return detail


def _convert_to_pdf_report(docx_path: str, out_dir: str, soffice: str | None) -> SofficeReport:
    """soffice --convert-to pdf с профилем в /tmp; PDF либо None + диагностика."""
    if soffice is None:
        return SofficeReport(detail="soffice не найден в PATH (LibreOffice не установлен)")
    import os
    import subprocess

    # Профиль LibreOffice — отдельный каталог в /tmp с суффиксом pid: HOME
    # контейнера (/home/appuser) не существует, и soffice без профиля падает с
    # RC=77 («application cannot be started»); общий профиль на все процессы
    # при параллельной печати даёт «another instance running».
    profile = f"-env:UserInstallation=file:///tmp/soffice-profile-{os.getpid()}"
    result = subprocess.run(
        [soffice, profile, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return SofficeReport(detail=_soffice_detail(result.returncode, result.stderr))
    pdf = str(Path(docx_path).with_suffix(".pdf"))
    if not Path(pdf).exists():
        return SofficeReport(detail=_soffice_detail(result.returncode, "PDF-файл не появился"))
    return SofficeReport(pdf_path=pdf)


def _convert_to_pdf(docx_path: str, out_dir: str, soffice: str | None) -> str | None:
    """soffice --convert-to pdf; путь к PDF либо None при сбое (без диагностики)."""
    return _convert_to_pdf_report(docx_path, out_dir, soffice).pdf_path


# Текст «No module named '...'» в ImportError/ModuleNotFoundError (у вручную
# созданных исключений атрибут name пустой — берём имя из текста).
_MISSING_MODULE_RE = re.compile(r"No module named [\"']([\w.]+)[\"']")


def _missing_library(exc: BaseException) -> str:
    """Имя модуля, которого нет: exc.name, иначе разбор текста ошибки.

    Для вложенных импортов берётся верхний уровень (PIL.Image -> PIL)."""
    name = getattr(exc, "name", None) or ""
    if not name:
        match = _MISSING_MODULE_RE.search(str(exc))
        name = match.group(1) if match else "неизвестно"
    return name.split(".")[0]


def generate_bypass(
    request_id: str,
    version: str,
    template_body: str,
    context: Dict[str, object],
    base_url: str,
    files_dir: str,
    template_file: str | None = None,
) -> BypassResult:
    """Собрать бегунок: DOCX (python-docx-template) -> PDF (LibreOffice) + QR.

    template_file — имя .docx-файла шаблона в FILES_DIR/templates/: если задано
    и файл есть на диске — рендер из файла (вёрстка из .docx), иначе фолбэк
    на текстовый body (template_body). sanitize_context применяется ДО
    добавления steps/qr (эти ключи не под фильтр ПДн).

    Офлайн (нет python-docx-template/qrcode/PIL) или нет soffice —
    BypassResult(generated=False, reason=...): файлы не записываются в документы
    (в БД только мета успешной генерации). QR payload — URL заявки.
    Причина сбоя конвертации — код возврата soffice и хвост его stderr.
    Прочие сбои не глотаются: пишем в лог и пробрасываем наружу.
    """
    safe = sanitize_context(dict(context, request_id=request_id))
    url = request_url(base_url, request_id)
    try:
        if template_file:
            template_path = Path(files_dir) / "templates" / template_file
            if template_path.is_file():
                docx_bytes = _render_docx_from_file(template_path, safe, url)
            else:
                docx_bytes = _render_docx_stand(template_body, safe)
        else:
            docx_bytes = _render_docx_stand(template_body, safe)
        qr_bytes = _render_qr_png(url)
    except ImportError as exc:
        # Офлайн: нет python-docx-template/qrcode/PIL (в т.ч. вложенный импорт).
        # Называем саму библиотеку, а не только класс исключения.
        return BypassResult(
            generated=False,
            reason=(
                f"офлайн: нет библиотеки {_missing_library(exc)} "
                f"({exc.__class__.__name__}: {exc})"
            ),
        )
    except Exception:
        # Настоящий сбой (шаблон, QR, файлы) — под «офлайн» его прятать нельзя.
        _LOGGER.exception("Ошибка генерации бегунка по заявке %s", request_id)
        raise
    out_dir = Path(files_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docx_path = out_dir / f"bypass_{request_id}_v{version}.docx"
    docx_path.write_bytes(docx_bytes)
    report = _convert_to_pdf_report(str(docx_path), str(out_dir), _soffice_binary())
    pdf_path = report.pdf_path
    if pdf_path is None:
        # PDF не создан: бегунок не состоялся, файл не оставляем.
        try:
            docx_path.unlink(missing_ok=True)
        except OSError:
            pass
        return BypassResult(
            generated=False,
            reason=f"LibreOffice: PDF не создан ({report.detail})",
        )
    qr_path = out_dir / f"bypass_{request_id}_v{version}_qr.png"
    qr_path.write_bytes(qr_bytes)
    return BypassResult(
        generated=True,
        docx_path=str(docx_path),
        pdf_path=str(pdf_path),
        qr_payload=url,
    )

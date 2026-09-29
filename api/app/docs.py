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
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from typing import Dict, List, Protocol

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

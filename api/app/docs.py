# Интерфейсы и offline-реализация генерации бланков (волна B3).
# Генерация — логически часть worker, не api-запроса (см. скил mail-docs).
# Здесь только stdlib: DOCX собирается вручную (zip + document.xml),
# QR — заглушка (PNG с URL заявки в tEXt-блоке, матрица — на стенде
# библиотекой qrcode).
# Печать бланков (волна «Справочник бланков»): документ собирается ИЗ ДАННЫХ
# заявки — build_blank_document на python-docx, единая вёрстка (A4, поля
# ~20/15/18/15 мм, Times New Roman 11); шаги печатаются блоками строк.

# Файлы-шаблоны .docx источником оформления больше не являются (ключ настроек
# doc_templates и ручки файлов-шаблонов удалены— печать собирает бланок из данных).
# ПДн (остаток отпуска и др.) в бланки не включаются — см. sanitize_context.
# Зависимости стенда (python-docx/qrcode/Pillow/LibreOffice) — раскомментирует
# стенд, offline только stdlib.

from __future__ import annotations

import io
import logging
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
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
        """Собрать DOCX-бегунок из переданного текста-шаблона (Jinja-подстановка)."""
        ...  # pragma: no cover

    def render_pdf_stub(self, request_id: str, version: int) -> bytes:
        """Стаб PDF с пометкой про LibreOffice (настоящий рендер — на стенде)."""
        ...  # pragma: no cover


class StdlibDocxRenderer:
    """Offline-рендер DOCX на stdlib (zipfile): текст + QR-заглушка + версия.

    Остался от волны B3 как заглушка офлайна; печать заявки (documents.py) его
    не вызывает — она собирает бланк из данных через build_blank_document.
    """

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
# Зависимости стенда (python-docx/qrcode/Pillow/LibreOffice)
# импортируются лениво: офлайн их нет — generate_bypass вернет generated=False
# с причиной (не 500); прочие сбои не глотаются. Оформление бланка — из данных
# заявки и снимка бланка (см. build_blank_document), файлов-шаблонов нет.
# Профиль soffice — в /tmp (см. _convert_to_pdf_report): HOME контейнера не
# существует, софт без профиля RC=77.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BypassResult:
    """Итог генерации бегунка: файлы на диске или причина отказа (не ошибка)."""

    generated: bool = False
    reason: str = ""
    docx_path: str = ""
    pdf_path: str = ""
    qr_payload: str = ""


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


def _step_logins(step: object) -> List[str]:
    """Логины ответственных шага из снимка (assignees, миграция 0013).

    Пустой снимок — прежний одиночный assignee (заявки, выданные до миграции):
    печатать и уведомлять надо прежнего исполнителя. Мусор в jsonb и дубли
    отсекаются, порядок снимка сохраняется."""
    logins: List[str] = []
    for raw in getattr(step, "assignees", None) or []:
        sam = str(raw).strip()
        if sam and sam not in logins:
            logins.append(sam)
    if not logins:
        single = str(getattr(step, "assignee", None) or "").strip()
        if single:
            logins.append(single)
    return logins


def build_bypass_context(request: object) -> Dict[str, object]:
    """Контекст бланка: поля 1С заявки без ПДн (mail/отпуск — не включаем)
    + снимок бланка (название/версия) + шаги маршрута (владельцы шагов —
    участники процесса, не ПДн).

    По каждому шагу отдаём снимок этапа из справочников (title/stage_lines),
    снимок ответственных (assignees — все логины, один ответственный — список
    из одного), отметку (done_at) и режим шага (approval_mode): бланк печатается
    по маршруту, собранному из этапов, и в блоке шага «Ответственный»
    печатаются ВСЕ ответственные шага.
    assignee_names здесь пустой намеренно: ФИО по логинам подставляет
    documents.py из зеркала AD (docs.py про хранилище заявок не знает — импорт
    был бы циклическим, его не делаем); fio — ФИО первого ответственного
    (прежнее поле контекста, оставлено для одиночного исполнителя)."""
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
                # Название этапа (снимок этапа) и текст его пунктов: в печать
                # идут через build_blank_document (HTML из редактора — там).
                # Рукной шаг (шаги задаёт ОК) этапа не имеет: название пустое,
                # логин согласующего вместо названия печатать нельзя — на бланке
                # он повторялся дважды (жалоба человека 2026-10-12). Должность
                # согласующего подставляет documents._fill_step_fio.
                "title": str(getattr(step, "stage_title", None) or ""),
                "position": str(getattr(step, "stage_title", None) or ""),
                # Снимок ответственных: логины (assignees, миграция 0013) плюс
                # прежний одиночный assignee — у заявок, выданных до миграции,
                # снимка нет, печатать надо прежнего исполнителя.
                "assignees": _step_logins(step),
                "assignee": getattr(step, "assignee", None) or "",
                "fio": "",
                # Имя группы AD, если шаг групповой: под ним печатается состав
                # группы (documents._fill_step_group_members) — по строке на
                # сотрудника, иначе на бланке только служебное имя группы.
                "owner_group": "" if _step_logins(step) else step.owner_group,
                # ФИО ответственных по логинам: подставляет documents.py
                # (в бланке колонка «Ответственный» — по строке на ответственного).
                "assignee_names": [],
                # Должности ответственных по логинам (title_ad зеркала):
                # подставляет documents._fill_step_fio. Нужны ручному шагу —
                # у него нет названия этапа, на бланке идёт «должность, ФИО».
                "assignee_positions": [],
                # Текст шага — только свой (снимок этапа/бланка). Подставлять
                # владельца по умолчанию нельзя: у ручного шага это логин, и он
                # печатался вторым дублем под названием (жалоба человека
                # 2026-10-12).
                "stage_lines": stage_lines,
                "done_at": _step_done_text(getattr(step, "done_at", None)),
                # Режим шага (снимок): sequential — «все ответственные», parallel —
                # «любой ответственный». Печатается пометкой в блоке шага.
                "approval_mode": getattr(step, "approval_mode", None),
            }
        )
    return {
        "request_id": request.id,
        "fio": request.fio,
        "department": request.department,
        "position": request.position,
        "category": request.category or "",
        "enterprise": request.enterprise or "",
        "tab_num": getattr(request, "tab_num", None) or "",
        "doc_type_code": getattr(request, "doc_type_code", None) or "",
        # Тема и содержание заявки — печатаются только если бланк их подставил
        # ({subject}/{content}); в шаги и письма само по себе не идут.
        "subject": getattr(request, "subject", None) or "",
        "content": getattr(request, "content", None) or "",
        # Снимок выбранного бланка: правка справочника не меняет выданную
        # заявку, поэтому печатаем ровно то, что зафиксировано на момент выдачи.
        "blank_id": getattr(request, "blank_id", None),
        "blank_name": getattr(request, "blank_name", None) or "",
        "blank_version": getattr(request, "blank_version", None),
        # Шапка/подвал бланка — тоже снимок (миграция 0014): печатаем то, что
        # было в бланке на момент выдачи заявки.
        "blank_header_html": getattr(request, "blank_header_html", None) or "",
        "blank_footer_lines": [
            str(line)
            for line in (getattr(request, "blank_footer_lines", None) or [])
            if str(line).strip()
        ],
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Бланк из данных (волна «Справочник бланков»): DOCX собирается python-docx
# единой вёрсткой (A4, поля ~20/15/18/15 мм, Times New Roman 11): шаги
# маршрута печатаются блоками строк, макета/пресетов оформления больше нет.
# Файлы-шаблоны .docx печать не читает. Зависимости стенда (python-docx/
# qrcode/Pillow) импортируются лениво: офлайн их нет — generate_bypass вернет
# generated=False с причиной (не 500).
# ---------------------------------------------------------------------------

# Шрифт печатной формы бланка (константа вёрстки, не настройка).
BLANK_FONT_NAME = "Times New Roman"

# Константы вёрстки печатной формы: поля страницы (В/П/Н/Л, мм), кегль
# обычного текста и заголовка, сторона QR (мм). Единая вёрстка — макета/пресетов
# оформления больше нет.
BLANK_MARGINS_MM = (20, 15, 18, 15)
BLANK_FONT_PT = 11
BLANK_TITLE_PT = 14
BLANK_QR_MM = 30

# Подписи полей шапки бланка: (подпись, ключ контекста).
_HEADER_FIELDS = (
    ("ФИО", "fio"),
    ("Должность", "position"),
    ("Служба", "department"),
    ("Табельный номер", "tab_num"),
    ("Предприятие", "enterprise"),
)

# Теги текста этапа, разрешённые к печати (визуальный редактор отдаёт HTML):
# начертание и перенос строки. Остальные теги — обычный текст без разметки,
# содержимое служебных тегов на бланок не попадает вовсе.
_BOLD_TAGS = frozenset({"b", "strong"})
_ITALIC_TAGS = frozenset({"i", "em"})
_UNDERLINE_TAGS = frozenset({"u"})
_BREAK_TAGS = frozenset({"br", "p", "li"})
_HIDDEN_TAGS = frozenset(
    {
        "script", "style", "head", "title", "noscript", "template",
        "iframe", "object", "embed", "svg", "math",
    }
)
# Заголовки редактора (h1–h3) печатаются жирным абзацем увеличенного кегля:
# кегль — константы вёрстки (от BLANK_TITLE_PT вниз по ступени).
_HEADING_PT = {
    "h1": BLANK_TITLE_PT,
    "h2": BLANK_TITLE_PT - 1,
    "h3": BLANK_TITLE_PT - 2,
}
# Выравнивание абзаца из атрибута style редактора (text-align). Значения —
# имена выравниваний python-docx; прочие свойства style на бланок не идут.
_PRINT_ALIGN_RE = re.compile(r"(?i)text-align\s*:\s*(left|center|right|justify)\b")
# Тег разбирается как имя + атрибуты: атрибуты на бланок не печатаются, из них
# читается только выравнивание (_PRINT_ALIGN_RE).
_HTML_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")


def _html_has_text(chunk: str) -> bool:
    """Есть ли в HTML-фрагменте видимый текст (без тегов и сущностей).

    Нужна печати подвала: пустой абзац редактора («<p></p>») после разбора по
    закрывающему `</p>` даёт фрагмент «<p>» — без этой проверки на бланке
    печаталась бы пустая строка, которой раньше (текстовые строки) не было."""
    return bool(unescape(_HTML_TAG_RE.sub("", str(chunk))).strip())


def _blank_title(context: Dict[str, object]) -> str:
    """Название бланка на бланке: снимок blank_name, иначе вид документа.

    Вид документа печатается НАЗВАНИЕМ (doc_type_name): код вида служебный,
    числовой (миграция 0019), в шапке бланка человеку нечего читать. Название
    кладёт в контекст documents.py (у резолвера здесь доступа к справочнику
    видов нет); нет названия — откат на код.
    """
    return (
        str(context.get("blank_name") or "").strip()
        or str(context.get("doc_type_name") or "").strip()
        or str(context.get("doc_type_code") or "").strip()
        or "Бланк"
    )


# Плейсхолдер шапки/подвала и текста шага: {имя}. Двойные скобки ({{ ... }} из
# шаблонов писем) не трогаем — печати это не касается.
_PRINT_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{\s*(\w+)\s*\}(?!\})")

# Граница абзацев пользовательской шапки (HTML редактора): каждый закрытый
# <p> и каждый закрытый заголовок печатаются своим абзацем DOCX.
_PRINT_BLOCK_RE = re.compile(r"(?i)</\s*(?:p|h[1-6])\s*>")


def _format_print_date(value: object) -> str:
    """Дата плейсхолдера строкой ДД.ММ.ГГГГ (пусто — даты нет/не разобрали)."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%d.%m.%Y")
    text = str(value).strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def blank_placeholders(context: Dict[str, object]) -> Dict[str, str]:
    """Значения плейсхолдеров бланка из контекста печати.

    Набор — по контракту печати: {fio} {position} {department} {tab_num}
    {enterprise} {blank_name} {subject} {content} {date} {dismissal_date}
    {steps} {manager}. Даты берутся из контекста в любом разбираемом виде и
    печатаются ДД.ММ.ГГГГ (нет даты — пусто), {manager} — ФИО руководителя,
    подставленное в контекст печати (нет — пусто), {steps} — количество шагов
    заявки."""
    steps = [step for step in (context.get("steps") or []) if isinstance(step, dict)]
    return {
        "fio": str(context.get("fio") or "").strip(),
        "position": str(context.get("position") or "").strip(),
        "department": str(context.get("department") or "").strip(),
        "tab_num": str(context.get("tab_num") or "").strip(),
        "enterprise": str(context.get("enterprise") or "").strip(),
        "blank_name": _blank_title(context),
        "subject": str(context.get("subject") or "").strip(),
        "content": str(context.get("content") or "").strip(),
        "date": _format_print_date(context.get("date")),
        "dismissal_date": _format_print_date(context.get("dismissal_date")),
        "steps": f"количество шагов: {len(steps)}",
        "manager": str(context.get("manager") or "").strip(),
    }


def render_blank_text(
    text: str, context: Dict[str, object], *, escape: bool = False
) -> str:
    """Текст шапки/подвала/шага с подставленными плейсхолдерами контекста.

    Неизвестный плейсхолдер остаётся текстом: опечатка в бланке — не сбой печати
    (иначе бланк с опечаткой не напечатать вовсе). escape=True — для HTML-шапки:
    значения (например {content} заявки) печатаются текстом, а не разметкой."""
    values = blank_placeholders(context)
    if escape:
        import html as _html

        values = {key: _html.escape(value) for key, value in values.items()}
    return _PRINT_PLACEHOLDER_RE.sub(
        lambda match: values.get(match.group(1), match.group(0)), text
    )


def _docx_document():
    """Пустой документ A4 единой вёрстки: поля страницы и базовый шрифт."""
    from docx import Document
    from docx.shared import Mm, Pt

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    top, right, bottom, left = BLANK_MARGINS_MM
    section.top_margin, section.right_margin = Mm(top), Mm(right)
    section.bottom_margin, section.left_margin = Mm(bottom), Mm(left)
    style = document.styles["Normal"]
    style.font.name = BLANK_FONT_NAME
    style.font.size = Pt(BLANK_FONT_PT)
    return document


def _add_runs(
    paragraph: object,
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    size_pt: int | None = None,
) -> None:
    """Текст в абзац с начертанием; переносы строк — разрывом прогона.

    size_pt — кегль прогона (заголовок бланка); None — базовый кегль вёрстки."""
    for index, line in enumerate(text.split("\n")):
        if index:
            paragraph.add_run().add_break()
        if not line:
            continue
        run = paragraph.add_run(line)
        run.bold = bold
        run.italic = italic
        run.underline = underline
        if size_pt is not None:
            from docx.shared import Pt

            run.font.size = Pt(size_pt)


def _apply_paragraph_alignment(paragraph: object, align: str) -> None:
    """Выравнивание абзаца печати по имени из style разметки редактора.

    Имя приходит как left|center|right|justify — это имена выравниваний
    python-docx; неизвестное имя выравнивание не меняет."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    alignment = getattr(WD_ALIGN_PARAGRAPH, align.upper(), None)
    if alignment is not None:
        paragraph.alignment = alignment


def _add_stage_text(paragraph: object, text: str, *, bold: bool = False) -> None:
    """Текст этапа (может прийти HTML из визуального редактора) в абзац DOCX.

    В печать идут только разрешённые теги (_BOLD_TAGS/_ITALIC_TAGS/
    _UNDERLINE_TAGS/_BREAK_TAGS), заголовки редактора (_HEADING_PT) — жирным
    абзацем увеличенного кегля, выравнивание из style (_PRINT_ALIGN_RE) — как
    выбрано в редакторе; содержимое служебных тегов выбрасывается, любые другие
    теги идут обычным текстом без разметки. Свой санитайзер не пишем: ПДн-поля
    вычищает sanitize_context, здесь только отбор тегов, разрешённых к печати
    (контракт фазы).
    """
    cur_bold, cur_italic, cur_underline = bold, False, False
    size_pt: int | None = None
    hidden: List[str] = []
    emitted = False
    pos = 0
    for match in _HTML_TAG_RE.finditer(text):
        closing, tag = match.group(1) == "/", match.group(2).lower()
        chunk, pos = text[pos : match.start()], match.end()
        # Текст печатается, пока не открыт служебный тег: внутри него (script,
        # style и т.п.) на бланок не попадает ничего.
        if chunk and not hidden:
            _add_runs(
                paragraph,
                unescape(chunk),
                bold=cur_bold,
                italic=cur_italic,
                underline=cur_underline,
                size_pt=size_pt,
            )
            emitted = True
        if tag in _HIDDEN_TAGS:
            if hidden:
                if closing and hidden[-1] == tag:
                    hidden.pop()
            elif not closing:
                hidden.append(tag)
            continue
        if hidden:
            continue
        # Выравнивание абзаца — из style открывающего тега (атрибуты на бланок
        # не печатаются, читается только text-align).
        align = _PRINT_ALIGN_RE.search(match.group(3) or "") if not closing else None
        if align:
            _apply_paragraph_alignment(paragraph, align.group(1).lower())
        if tag in _BREAK_TAGS:
            if not closing and emitted:
                paragraph.add_run().add_break()
                emitted = True
        elif tag in _BOLD_TAGS:
            cur_bold = not closing
        elif tag in _ITALIC_TAGS:
            cur_italic = not closing
        elif tag in _UNDERLINE_TAGS:
            cur_underline = not closing
        elif tag in _HEADING_PT:
            # Заголовок редактора — жирный абзац своего кегля до конца тега.
            size_pt = None if closing else _HEADING_PT[tag]
            cur_bold = bold if closing else True
    tail = text[pos:]
    if tail and not hidden:
        _add_runs(
            paragraph,
            unescape(tail),
            bold=cur_bold,
            italic=cur_italic,
            underline=cur_underline,
            size_pt=size_pt,
        )


def _add_qr_inline(paragraph: object, context: Dict[str, object]) -> None:
    """Вставить QR как картинку В ПОТОКЕ абзаца (плейсхолдер {qr}).

    Так QR стоит там, где админ его поставил в шапке, и не выносится отдельным
    блоком, который ломал вёрстку шапки. URL пуст — молча пропускаем (печать не
    падает), плейсхолдер без картинки просто исчезает."""
    url = str(context.get("qr_url") or "").strip()
    if not url:
        return
    from docx.shared import Mm

    run = paragraph.add_run()
    run.add_picture(
        io.BytesIO(_render_qr_png(url)), width=Mm(BLANK_QR_MM), height=Mm(BLANK_QR_MM)
    )


def _add_blank_header_html(document: object, context: Dict[str, object]) -> None:
    """Шапка бланка из снимка заявки (HTML редактора) вместо списка полей.

    Абзац на каждый закрытый <p> и каждый закрытый заголовок редактора,
    начертания те же, что у текста этапа: санитайзер _add_stage_text вырезает
    содержимое служебных тегов (script, style, iframe и прочие), любые атрибуты
    (обработчики событий и внешние ресурсы) и неразрешённые теги — на бланке
    остаются только начертания, кегль заголовка, выравнивание и переносы строк."""
    # Подстановка значений ЗДЕСЬ, в HTML-шапке: значения экранируются, чтобы
    # свободный текст заявки не ломал разметку (ранее порядок был обратный).
    # {qr} помечаем служебным маркером ДО подстановки и с допуском пробелов
    # ({ qr } — так же, как принимает регекс остальных плейсхолдеров).
    rendered = _QR_PLACEHOLDER_RE.sub(_QR_TOKEN, str(context.get("blank_header_html") or ""))
    rendered = render_blank_text(rendered, context, escape=True)
    for chunk in _PRINT_BLOCK_RE.split(rendered):
        parts = chunk.split(_QR_TOKEN)
        # Части с маркером QR (нечётные индексы) текстом не считаются, а сами
        # теги HTML не текст (проверка та же, что у подвала — _html_has_text):
        # иначе абзац с одним {qr} без QR-ссылки остался бы пустой строкой.
        has_text = any(
            _html_has_text(part) for index, part in enumerate(parts) if index % 2 == 0
        )
        has_qr = len(parts) > 1 and bool(str(context.get("qr_url") or "").strip())
        # Пустой абзац не заводим: без QR-ссылки {qr} не должен оставлять на
        # бланке лишнюю строку (как в подвале, где проверяет _html_has_text).
        if not has_text and not has_qr:
            continue
        paragraph = document.add_paragraph()
        for index, part in enumerate(parts):
            if index:
                _add_qr_inline(paragraph, context)
            if part.strip():
                _add_stage_text(paragraph, part)


def _add_blank_header(document: object, context: Dict[str, object]) -> None:
    """Шапка бланка: название, поля сотрудника, QR на заявку (qr_url контекста).

Название — по центру жирным; если в снимке заявки есть blank_header_html,
    печатается он вместо полей сотрудника (санитатизация _add_stage_text +
    плейсхолдеры с escape); шапки нет — поля сотрудника списком с жирной подписью.

    QR печатается ТОЛЬКО на месте плейсхолдера {qr} в шапке (_add_blank_header_html).
    Отдельного блока QR после шапки нет: он ломал вёрстку шапки (жалоба человека
    2026-10-12), поэтому без плейсхолдера QR просто не печатается.
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading = title.add_run(_blank_title(context))
    heading.bold = True
    heading.font.size = Pt(BLANK_TITLE_PT)
    if str(context.get("blank_header_html") or "").strip():
        _add_blank_header_html(document, context)
    else:
        for label, key in _HEADER_FIELDS:
            paragraph = document.add_paragraph()
            paragraph.add_run(label + ": ").bold = True
            paragraph.add_run(str(context.get(key) or "").strip())


def _step_responsible(step: Dict[str, object]) -> List[str]:
    """Ответственные шага на бланке — список имён (в столбец «Ответственные»).

    ФИО всех персональных исполнителей снимка (assignee_names — их подставляет
    documents._fill_step_fio по логинам), по одному в строке: так подписывать
    удобнее. Прежнее одиночное fio — как есть.

    Групповой шаг (исполнителей-людей нет, владелец — группа AD): печатаем имя
    группы, затем СПИСОК сотрудников группы (group_member_names — живой состав
    подставляет documents._fill_step_group_members): иначе сотруднику не к кому
    обращаться, на бланке было только служебное имя группы вроде SED_STEP_HR.
    Состав не получили (AD недоступен, группа не найдена) — печатается имя группы
    как раньше, печать не падает."""
    names = [
        str(name).strip() for name in (step.get("assignee_names") or []) if str(name).strip()
    ]
    if names:
        return names
    members = [
        str(name).strip()
        for name in (step.get("group_member_names") or [])
        if str(name).strip()
    ]
    if members:
        group = str(step.get("owner") or "").strip()
        return ([group] if group else []) + members
    single = str(step.get("fio") or "").strip()
    return [single or str(step.get("owner") or "").strip()]


def _step_mode_text(mode: object) -> str:
    """Режим шага текстом для печати: parallel — «параллельно», иначе «последовательно»."""
    return "параллельно" if str(mode or "").strip().casefold() == "parallel" else "последовательно"


def _step_marks(step: Dict[str, object]) -> List[str]:
    """Отметка/дата шага: статус и дата выполнения; без даты — место под росчерк."""
    done_at = str(step.get("done_at") or "").strip()
    return [
        line
        for line in (str(step.get("status") or "").strip(), done_at or "_" * 10)
        if line
    ]


# Колонки таблицы шагов (мм). Полезная ширина A4 при полях 20/15 — 175 мм.
STEP_TABLE_WIDTHS_MM = (10.0, 85.0, 45.0, 35.0)
STEP_TABLE_HEADERS = ("№", "Содержание шага", "Ответственные", "Отметка")
# Кегль таблицы шагов на пункт меньше основного текста: бланк должен укладываться
# в одну страницу (решение человека 2026-10-12).
STEP_TABLE_PT = BLANK_FONT_PT - 1
# Плейсхолдер QR в бланке: {qr} печатается картинкой В потоке текста, иначе QR
# выносился отдельным блоком и ломал вёрстку шапки (жалоба человека 2026-10-12).
QR_PLACEHOLDER = "{qr}"
_QR_TOKEN = "\u241eQR\u241e"
# Регекс допускает пробелы внутри плейсхолдера ({ qr }) - как остальные.
_QR_PLACEHOLDER_RE = re.compile(r"\{\s*qr\s*\}")


def _cell_paragraph(cell: object, *, bold: bool = False, center: bool = False, first: bool = True):
    """Абзац ячейки с плотными отступами (шаг не должен «раздувать» строку).

    first=True — взять пустой абзац, который python-docx создаёт в ячейке;
    False — добавить следующий (иначе все строки ячейки слиплись бы в одну)."""
    from docx.shared import Pt

    paragraph = cell.paragraphs[0] if first else cell.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0
    if center:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if bold:
        run = paragraph.add_run("")
        run.bold = True
    return paragraph


def _row_flag(row: object, tag: str) -> None:
    """Включить свойство строки таблицы: повтор «шапки» на странице / запрет разрыва."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tr_pr = row._tr.get_or_add_trPr()
    element = OxmlElement("w:%s" % tag)
    element.set(qn("w:val"), "true")
    tr_pr.append(element)


def _table_compact(table: object) -> None:
    """Плотная вёрстка таблицы шагов: единый кегль и минимальные поля ячеек.

    Сжатие нужно, чтобы типовой бланк укладывался в одну страницу (решение
    человека 2026-10-12): кегль на пункт меньше основного текста бланка, поля
    ячеек — 1 мм вместо умолчания Word (~2 мм слева и справа)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    # Заголовок внутри текста шага (h1–h3) печатается своим
                    # кеглем (_HEADING_PT) — его не трогаем, ужимаем только
                    # обычный текст, у которого размера нет.
                    if run.font.size is None:
                        run.font.size = Pt(STEP_TABLE_PT)
    # Поля ячеек таблицы: w:tblCellMar (стар/право/низ/лево) в двадцатых пункта.
    # Порядок элементов w:tblPr задан схемой CT_TblPrBase (ECMA-376), поэтому
    # tblCellMar вставляем ПЕРЕД w:tblLook, иначе Word считает файл битым.
    tbl_pr = table._tbl.tblPr
    cell_mar = tbl_pr.find(qn("w:tblCellMar"))
    if cell_mar is None:
        cell_mar = OxmlElement("w:tblCellMar")
        tbl_pr.insert_element_before(cell_mar, "w:tblLook", "w:tblCaption", "w:tblDescription")
    for tag in ("w:top", "w:left", "w:bottom", "w:right"):
        element = cell_mar.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            cell_mar.append(element)
        element.set(qn("w:w"), "57")  # 1 мм
        element.set(qn("w:type"), "dxa")


def _step_heading(step: Dict[str, object], context: Dict[str, object]) -> str:
    """Заголовок шага в таблице: «название (режим)», а у ручного — «должность (режим)».

    Название берём у шага, а если его нет (ручной маршрут: этапа справочника за
    шагом не стоит) — должность согласующего из assignee_positions. Режим
    согласования печатается всегда: он объясняет, подписываются все или любой.
    Ни названия, ни должности нет — печатаем один режим, пустую строку не оставляем."""
    title = render_blank_text(
        str(step.get("title") or "").strip(), context, escape=True
    ).strip()
    if not title:
        positions = [
            render_blank_text(str(item).strip(), context, escape=True).strip()
            for item in (step.get("assignee_positions") or [])
        ]
        title = ", ".join(item for item in positions if item)
    mode = _step_mode_text(step.get("approval_mode"))
    if title and mode:
        return "%s (%s)" % (title, mode)
    return title or mode


def _add_steps(document: object, context: Dict[str, object]) -> None:
    """Шаги маршрута — таблицей: №, содержание, ответственные, отметка.

    По решению человека 2026-10-12 шаги печатаются таблицей, а не набором
    абзацев: номер, содержание шага (название с режимом и текст), ответственные
    (каждый с новой строки — так подписывать удобнее) и отметка (статус с датой
    либо пустая строка под росчерк на бумаге). Над таблицей — строка заголовков,
    повторяющаяся на следующей странице; строка шага не разрывается между
    страницами, отступы плотные — типовой бланк из 5–8 шагов укладывается в
    одну страницу (гарантии на сотню шагов нет, это свойство объёма текста).

    Плейсхолдеры подставляются в названии и тексте с ЭКРАНИРОВАНИЕМ значений
    (как в шапке): свободный текст заявки из {content}/{subject} не должен
    становиться тегами бланка."""
    from docx.shared import Mm

    steps = [item for item in (context.get("steps") or []) if isinstance(item, dict)]
    if not steps:
        return
    table = document.add_table(rows=1, cols=len(STEP_TABLE_HEADERS))
    try:
        table.style = "Table Grid"
    except KeyError:
        # Стиля нет в минимальном шаблоне — печатаем без явной рамки.
        pass
    # Фиксированная раскладка: без неё Word/LibreOffice подгоняет столбцы под
    # содержимое и колонка «№» оказывается такой же широкой, как содержание.
    table.autofit = False
    for index, width_mm in enumerate(STEP_TABLE_WIDTHS_MM):
        table.columns[index].width = Mm(width_mm)
    header = table.rows[0]
    _row_flag(header, "tblHeader")  # повтор заголовков на новой странице
    for index, (cell, title) in enumerate(zip(header.cells, STEP_TABLE_HEADERS)):
        paragraph = _cell_paragraph(cell, bold=True, center=True)
        paragraph.runs[0].text = title
        cell.width = Mm(STEP_TABLE_WIDTHS_MM[index])
    for position, step in enumerate(steps, start=1):
        row = table.add_row()
        _row_flag(row, "cantSplit")  # шаг не рвётся между страницами
        cells = row.cells
        for index, cell in enumerate(cells):
            cell.width = Mm(STEP_TABLE_WIDTHS_MM[index])
        # Столбец «№».
        number = _cell_paragraph(cells[0], center=True)
        number.add_run(str(position))
        # Столбец «Содержание шага»: название с режимом + текст шага.
        # У ручного шага своего названия нет — печатаем ДОЛЖНОСТЬ согласующего
        # (documents._fill_step_fio), ФИО идёт в «Ответственные». Должности и
        # названия не заменяем логином: на бланке он повторялся дважды.
        title_cell = _cell_paragraph(cells[1])
        heading = render_blank_text(
            _step_heading(step, context), context, escape=True
        ).strip()
        if heading:
            _add_stage_text(title_cell, heading, bold=True)
        for line in step.get("stage_lines") or []:
            text = str(line).strip()
            if text:
                _add_stage_text(
                    _cell_paragraph(cells[1], first=False),
                    render_blank_text(text, context, escape=True),
                )
        # Столбец «Ответственные»: каждый — с новой строки.
        for index, name in enumerate(_step_responsible(step)):
            paragraph = _cell_paragraph(cells[2], bold=index == 0, first=index == 0)
            if index == 0:
                paragraph.runs[0].text = name
            else:
                paragraph.add_run(name)
        # Столбец «Отметка»: статус и дата выполнения либо место под росчерк.
        marks_cell = _cell_paragraph(cells[3], bold=True)
        marks_cell.runs[0].text = ", ".join(_step_marks(step))
    # Плотная вёрстка всего стола (кегль и поля ячеек) — после сборки строк.
    _table_compact(table)
    document.add_paragraph()


def _add_blank_footer_lines(document: object, context: Dict[str, object]) -> None:
    """Подвал бланка из снимка заявки: строки blank_footer_lines по центру.

    Строка подвала — абзац HTML визуального редактора (контракт хранения —
    список строк, TEMPLATES.md §1), поэтому абзацы печатаются тем же разбором,
    что шапка (_PRINT_BLOCK_RE + _add_stage_text): начертания, кегль заголовка и
    выравнивание редактора доходят до бланка. Правило печати подвала — по
    центру; выравнивание, заданное в редакторе, переопределяет его (как в шапке).

    Обратная совместимость: строка БЕЗ разметки разбор не меняет — это один
    абзац по центру с текстом, как печаталось до появления редактора подвала.
    Значения плейсхолдеров подставляются с ЭКРАНИРОВАНИЕМ (как в шапке и тексте
    шага): свободный текст заявки не должен стать разметкой подвала."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    for raw in context.get("blank_footer_lines") or []:
        text = str(raw).strip()
        if not text:
            continue
        rendered = render_blank_text(text, context, escape=True)
        for chunk in _PRINT_BLOCK_RE.split(rendered):
            # Чанк без текста пропускаем: пустой абзац редактора («<p></p>»)
            # печатать нельзя — иначе на бланке появляется пустая строка.
            if not _html_has_text(chunk):
                continue
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _add_stage_text(paragraph, chunk)


def _add_blank_footer(document: object, context: Dict[str, object]) -> None:
    """Подвал бланка: номер заявки, название бланка и его версия (снимок)."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    version = context.get("blank_version")
    parts = [
        str(context.get("request_id") or "").strip(),
        _blank_title(context) + (f" v{version}" if version else ""),
    ]
    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(" · ".join(part for part in parts if part))


def build_blank_document(context: Dict[str, object]) -> bytes:
    """Бланк заявки как DOCX: собирается из данных (python-docx), не из файла.

    Единая вёрстка (макета/пресетов нет): шапка берёт blank_name и поля
    сотрудника (либо blank_header_html снимка, если она есть), шаги печатаются
    отдельными блоками строк с ответственными — ВСЕМИ по снимку (ФИО подставил
    documents.py по логинам assignee_names), группами AD — как есть, и пометкой
    режима шага (последовательно/параллельно); после шагов идут строки подвала
    blank_footer_lines снимка, QR — на qr_url из контекста (его кладёт
    generate_bypass: URL заявки). Плейсхолдеры вида {fio}/{date}/{steps}
    подставляются в шапке, подвале и тексте шага; неизвестные остаются текстом.
    ПДн-поля вычищает sanitize_context.
    Заявку без шагов не печатает пустым документом — печать отвечает 422
    (documents.print_bypass). Ожидает python-docx/qrcode/Pillow (стенд);
    офлайн ImportError разбирает generate_bypass (generated=False).
    """
    safe = sanitize_context(dict(context))
    document = _docx_document()
    _add_blank_header(document, safe)
    _add_steps(document, safe)
    _add_blank_footer_lines(document, safe)
    _add_blank_footer(document, safe)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


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
    context: Dict[str, object],
    base_url: str,
    files_dir: str,
) -> BypassResult:
    """Собрать бланк заявки: DOCX (python-docx) -> PDF (LibreOffice) + QR.

    Оформление целиком из данных: build_blank_document по контексту (снимок
    бланка: шапка, шаги блоками строк со всеми ответственными и режимом шага);
    файлы-шаблоны .docx и ключ настроек doc_templates удалены. qr_url кладёт сам
    generate_bypass — payload QR и подпись под картинкой это URL заявки;
    sanitize_context применяется ДО сборки (qr_url/steps под фильтр ПДн
    не подпадают).

    Офлайн (нет python-docx/qrcode/PIL) или нет soffice —
    BypassResult(generated=False, reason=...): файлы не записываются в документы
    (в БД только мета успешной генерации). QR payload — URL заявки.
    Причина сбоя конвертации — код возврата soffice и хвост его stderr.
    Прочие сбои не глотаются: пишем в лог и пробрасываем наружу.
    """
    url = request_url(base_url, request_id)
    safe = sanitize_context(dict(context, request_id=request_id, qr_url=url))
    try:
        docx_bytes = build_blank_document(safe)
        qr_bytes = _render_qr_png(url)
    except ImportError as exc:
        # Офлайн: нет python-docx/qrcode/PIL (в т.ч. вложенный импорт).
        # Называем саму библиотеку, а не только класс исключения.
        return BypassResult(
            generated=False,
            reason=(
                f"офлайн: нет библиотеки {_missing_library(exc)} "
                f"({exc.__class__.__name__}: {exc})"
            ),
        )
    except Exception:
        # Настоящий сбой (сборка бланка, QR, файлы) — под «офлайн» его прятать нельзя.
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

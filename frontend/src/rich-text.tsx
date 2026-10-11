// Визуальный редактор текста этапа (TipTap): начертания, заголовки H1–H3,
// списки, абзац и выравнивание по краям. Значение — HTML в том же поле,
// которое приходит из справочника этапов (title/stage_lines), поэтому разметка
// попадёт в печать (её санирует рендер печати на бэкенде).
// Редактор НЕ хранит состояние сам: value (HTML) → onChange(HTML), иначе
// содержимое терялось бы при перерисовке родителя. Правка снаружи (переход на
// другой этап) применяется через setContent; собственные правки редактора в
// effect не возвращаются обратно — иначе курсор прыгал бы на каждый ввод.
import TextAlign from "@tiptap/extension-text-align";
import { EditorContent, useEditor } from "@tiptap/react";
import type { ChainedCommands } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import type { ReactNode } from "react";
import { Fragment, useEffect, useRef } from "react";

interface RichTextEditorProps {
  // Подпись поля (aria-label редактора и подпись блока).
  label: string;
  // Текущее значение — HTML (пустая строка — пустой абзац).
  value: string;
  // Новое значение при правке (HTML). Пустая строка — очистка поля.
  onChange: (html: string) => void;
  // Скрытая подпись под редактором (например, про печать); без неё — ничего.
  hint?: string;
  // Запрос вставки текста извне (кнопки плейсхолдеров шапки бланка): текст
  // вставляется в позицию курсора. seq — счётчик запросов: одинаковый текст
  // вставляется повторно только с новым seq.
  insert?: { text: string; seq: number } | null;
  // Курсор/фокус в редакторе (строки подвала: так выбранная строка сама
  // становится целью вставки плейсхолдера). Без него — ничего не сообщается.
  onFocus?: () => void;
}

// Общие атрибуты иконок панели (конвенция layout.tsx): инлайн-SVG, currentColor,
// штрихи 1.75, viewBox 24×24. Подпись кнопки живёт в title/aria-label — иконка
// для скринридера скрыта.
const ICON = {
  width: 18,
  height: 18,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
} as const;

// Иконки кнопок панели (начертания, списки, выравнивание, заголовки, абзац).
// Каталога иконок в проекте нет — иконки инлайн-SVG, как в layout.tsx.
const ICONS: Record<string, ReactNode> = {
  bold: (
    <svg {...ICON}>
      <path d="M6 4h7a4 4 0 0 1 0 8H6z" />
      <path d="M6 12h8a4 4 0 0 1 0 8H6z" />
    </svg>
  ),
  italic: (
    <svg {...ICON}>
      <path d="M19 4h-8M13 20H5M15 4L9 20" />
    </svg>
  ),
  underline: (
    <svg {...ICON}>
      <path d="M6 3v7a6 6 0 0 0 12 0V3" />
      <path d="M4 21h16" />
    </svg>
  ),
  bulletList: (
    <svg {...ICON}>
      <path d="M9 6h11M9 12h11M9 18h11" />
      <circle cx="4.5" cy="6" r="1.25" />
      <circle cx="4.5" cy="12" r="1.25" />
      <circle cx="4.5" cy="18" r="1.25" />
    </svg>
  ),
  orderedList: (
    <svg {...ICON}>
      <path d="M10 6h11M10 12h11M10 18h11" />
      <path d="M4 4.5h1v4M4 8.5h2M6 16.5H4c0-1 2-2 2-3s-1-1.5-2-1" />
    </svg>
  ),
  alignLeft: (
    <svg {...ICON}>
      <path d="M4 6h16M4 12h10M4 18h16" />
    </svg>
  ),
  alignCenter: (
    <svg {...ICON}>
      <path d="M4 6h16M7 12h10M4 18h16" />
    </svg>
  ),
  alignRight: (
    <svg {...ICON}>
      <path d="M4 6h16M10 12h10M4 18h16" />
    </svg>
  ),
  heading1: (
    <svg {...ICON}>
      <path d="M3.5 7v10M9.5 7v10M3.5 12h6" />
      <path d="M15.4 9.4L17.2 8.6V17" />
    </svg>
  ),
  heading2: (
    <svg {...ICON}>
      <path d="M3.5 7v10M9.5 7v10M3.5 12h6" />
      <path d="M14.2 10.2A2.2 2.2 0 0 1 18.4 12c0 2-4.2 2.4-4.2 4.6h4.4" />
    </svg>
  ),
  heading3: (
    <svg {...ICON}>
      <path d="M3.5 7v10M9.5 7v10M3.5 12h6" />
      <path d="M14.5 10.3a2.2 2.2 0 0 1 0 4.2 2.2 2.2 0 0 1 0 4.2" />
    </svg>
  ),
  paragraph: (
    <svg {...ICON}>
      <path d="M17 5h-5a5 5 0 0 1 0 10" />
      <path d="M12 5v14" />
    </svg>
  ),
};

// Описание одной кнопки панели. Кнопки описаны данными (без логики в
// компоненте): следующая волна добавляет кнопку строкой в TOOLBAR.
interface ToolbarItem {
  // Ключ кнопки (React key и идентификатор кнопки в тестах).
  key: string;
  // Подпись кнопки: title и aria-label (текстом, как у прочих кнопок).
  title: string;
  // Иконка кнопки — инлайн-SVG из ICONS.
  icon: ReactNode;
  // Активное состояние: имя расширения TipTap и его атрибуты (уровень
  // заголовка, выравнивание). Без имени проверяются только атрибуты — так
  // выравнивание подсвечивается и в абзаце, и в заголовке.
  active: { name?: string; attrs?: Record<string, unknown> };
  // Отбивка слева — разделяет группы кнопок (чисто вид панели).
  sep?: boolean;
  // Команда TipTap цепочки кнопки.
  run: (chain: ChainedCommands) => ChainedCommands;
}

// Кнопки панели форматирования в порядке вывода: начертания, списки,
// выравнивание, заголовки H1–H3, абзац.
const TOOLBAR: readonly ToolbarItem[] = [
  { key: "bold", title: "Полужирный", icon: ICONS.bold, active: { name: "bold" }, run: (c) => c.toggleBold() },
  { key: "italic", title: "Курсив", icon: ICONS.italic, active: { name: "italic" }, run: (c) => c.toggleItalic() },
  { key: "underline", title: "Подчёркнутый", icon: ICONS.underline, active: { name: "underline" }, run: (c) => c.toggleUnderline() },
  { key: "bulletList", title: "Маркированный список", icon: ICONS.bulletList, active: { name: "bulletList" }, sep: true, run: (c) => c.toggleBulletList() },
  { key: "orderedList", title: "Нумерованный список", icon: ICONS.orderedList, active: { name: "orderedList" }, run: (c) => c.toggleOrderedList() },
  { key: "alignLeft", title: "По левому краю", icon: ICONS.alignLeft, active: { attrs: { textAlign: "left" } }, sep: true, run: (c) => c.setTextAlign("left") },
  { key: "alignCenter", title: "По центру", icon: ICONS.alignCenter, active: { attrs: { textAlign: "center" } }, run: (c) => c.setTextAlign("center") },
  { key: "alignRight", title: "По правому краю", icon: ICONS.alignRight, active: { attrs: { textAlign: "right" } }, run: (c) => c.setTextAlign("right") },
  { key: "heading1", title: "Заголовок 1", icon: ICONS.heading1, active: { name: "heading", attrs: { level: 1 } }, sep: true, run: (c) => c.toggleHeading({ level: 1 }) },
  { key: "heading2", title: "Заголовок 2", icon: ICONS.heading2, active: { name: "heading", attrs: { level: 2 } }, run: (c) => c.toggleHeading({ level: 2 }) },
  { key: "heading3", title: "Заголовок 3", icon: ICONS.heading3, active: { name: "heading", attrs: { level: 3 } }, run: (c) => c.toggleHeading({ level: 3 }) },
  { key: "paragraph", title: "Абзац", icon: ICONS.paragraph, active: { name: "paragraph" }, sep: true, run: (c) => c.setParagraph() },
];

// Текст редактора без тегов: нужен админке, чтобы не отправить пустое значение
// (сервер отвергает пустое название этапа и пустой пункт).
export function richTextToPlain(html: string): string {
  const text = html
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"');
  return text.replace(/\s+/g, " ").trim();
}

// ---------------------------------------------------------------------------
// Строки бланка (<-> HTML редактора). Подвал (footer_lines) и текст шага
// (stage_lines) хранятся СПИСКОМ СТРОК — это контракт хранения (TEMPLATES.md §1/§2),
// поэтому редактор показывает все строки одним полем, а в список они делятся и
// собираются обратно без потерь: границы строк те же, что у печати (абзац,
// заголовок, пункт списка, <br>).
// ---------------------------------------------------------------------------

// Тег разбирается как имя + атрибуты: атрибуты на границы строк не влияют.
const RICH_TEXT_TAG_RE = /<\s*(\/?)\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*)>/g;
// Похожий разбор без флага g — для проверки «значение уже размечено». Якорь в
// начале обязателен: без него обычный текст вида «Подпись <b>взаимно» считался
// бы разметкой и ушёл бы в редактор как есть, минуя обёртку в <p>.
const RICH_TEXT_TAG_START_RE = /^<[a-zA-Z/!]/;
// Теги-контейнеры: внутри них строка не режется (иначе блок списка уедет на две
// строки и обратная сборка его разорвёт).
const RICH_TEXT_CONTAINER_TAGS = new Set(["ul", "ol", "blockquote", "pre", "table", "div"]);
// Теги конца строки: после закрывающего строка заканчивается.
const RICH_TEXT_END_TAGS = new Set(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]);
// Теги без закрытия: на них счётчик вложенности не растёт.
const RICH_TEXT_VOID_TAGS = new Set([
  "br", "hr", "img", "input", "meta", "link", "source", "wbr", "col", "area", "base",
  "embed", "param", "track",
]);
// Закрывающий тег в самом конце значения — вставка идёт перед ним (конец абзаца).
const RICH_TEXT_TAIL_END_RE = /<\s*\/\s*(?:p|h[1-6]|li)\s*>\s*$/i;

// HTML редактора → список строк бланка (footer_lines/stage_lines). Строка
// заканчивается там же, где заканчивается абзац печати: закрытый абзац,
// заголовок, пункт списка (на верхнем уровне) или <br>. Разметка самой строки
// сохраняется — её печать разбирает тем же санитайзером, что шапку.
export function richTextHtmlToLines(html: string): string[] {
  const lines: string[] = [];
  let start = 0;
  let depth = 0;
  for (const match of html.matchAll(RICH_TEXT_TAG_RE)) {
    const closing = match[1] === "/";
    const tag = match[2].toLowerCase();
    const selfClosed = match[3].trimEnd().endsWith("/") || RICH_TEXT_VOID_TAGS.has(tag);
    if (RICH_TEXT_CONTAINER_TAGS.has(tag)) {
      if (!closing && !selfClosed) depth += 1;
      if (closing) depth = Math.max(depth - 1, 0);
      continue;
    }
    // Внутри контейнера строка не заканчивается: блок должен остаться целым.
    if (depth > 0) continue;
    const isEnd = closing ? RICH_TEXT_END_TAGS.has(tag) : tag === "br";
    if (!isEnd) continue;
    const cut = (match.index ?? 0) + match[0].length;
    lines.push(html.slice(start, cut));
    start = cut;
  }
  if (start < html.length) lines.push(html.slice(start));
  return lines;
}

// Список строк бланка → HTML редактора: размеченные строки идут как есть,
// простой текст оборачивается абзацем. Пустые строки пропускаются (пустой
// абзац редактор всё равно создаёт сам).
export function richTextLinesToHtml(lines: string[]): string {
  const parts: string[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (line === "") continue;
    parts.push(RICH_TEXT_TAG_START_RE.test(line) ? line : `<p>${line}</p>`);
  }
  return parts.join("");
}

// Дописать текст в конец значения редактора (плейсхолдер в выбранную строку
// подвала): в конец последнего абзаца разметки, иначе — в конец строки.
export function richTextAppend(html: string, text: string): string {
  const tail = RICH_TEXT_TAIL_END_RE.exec(html);
  const at = tail ? (tail.index ?? html.length) : html.length;
  return html.slice(0, at) + text + html.slice(at);
}

// Визуальный редактор текста этапа: панель кнопок + область ввода.
export function RichTextEditor(props: RichTextEditorProps) {
  const { label, value, onChange, hint, insert, onFocus } = props;
  // Последнее значение, отправленное редактором: правки изнутри в effect не
  // возвращаются (иначе setContent на каждом вводе сбрасывал бы курсор).
  const emitted = useRef<string>(value);
  const editor = useEditor({
    // Выравнивание по краям — абзацу и заголовкам (то, что печатается по
    // центру/вправо, доходит до бланка разметкой).
    extensions: [StarterKit, TextAlign.configure({ types: ["paragraph", "heading"] })],
    content: value,
    // Панель кнопок показывает активное форматирование — перерисовываем её
    // на каждой транзакции редактора.
    shouldRerenderOnTransaction: true,
    onUpdate: ({ editor: ed }) => {
      const html = ed.getHTML();
      emitted.current = html;
      onChange(html);
    },
  });

  // Правка значения снаружи (смена этапа/пункта) — применить в редакторе.
  useEffect(() => {
    if (!editor) return;
    if (value === emitted.current) return;
    emitted.current = value;
    editor.commands.setContent(value, { emitUpdate: false });
  }, [editor, value]);

  // Вставка текста извне (плейсхолдеры шапки бланка): в позицию курсора.
  // Последний обработанный seq — чтобы один и тот же плейсхолдер вставлялся
  // повторно по новому нажатию, а не игнорировался как «уже вставленный».
  const insertedSeq = useRef(0);
  useEffect(() => {
    if (!editor || !insert || insert.seq === insertedSeq.current) return;
    insertedSeq.current = insert.seq;
    editor.chain().focus().insertContent(insert.text).run();
  }, [editor, insert]);

  return (
    <div className="sed-rte" onFocus={onFocus}>
      <div className="sed-rte__toolbar" role="toolbar" aria-label={`Форматирование: ${label}`}>
        {TOOLBAR.map((item) => {
          const { name, attrs } = item.active;
          const active = !editor
            ? false
            : name
              ? editor.isActive(name, attrs)
              : editor.isActive(attrs ?? {});
          return (
            <Fragment key={item.key}>
              {item.sep && <span className="sed-rte__sep" aria-hidden="true" />}
              <button
                type="button"
                className={active ? "sed-btn sed-rte__btn sed-rte__btn--on" : "sed-btn sed-rte__btn"}
                aria-label={item.title}
                aria-pressed={active}
                title={item.title}
                disabled={!editor}
                onClick={() => {
                  if (!editor) return;
                  item.run(editor.chain().focus()).run();
                }}
              >
                {item.icon}
              </button>
            </Fragment>
          );
        })}
      </div>
      <div className="sed-rte__field" role="group" aria-label={label}>
        <EditorContent editor={editor} />
      </div>
      {hint && <div className="sed-note">{hint}</div>}
    </div>
  );
}
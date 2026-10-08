// Визуальный редактор текста этапа (TipTap): полужирный, курсив, подчёркивание,
// маркированный и нумерованный списки, абзац. Значение — HTML в том же поле,
// которое приходит из справочника этапов (title/stage_lines), поэтому разметка
// попадёт в печать (её санирует рендер печати на бэкенде).
// Редактор НЕ хранит состояние сам: value (HTML) → onChange(HTML), иначе
// содержимое терялось бы при перерисовке родителя. Правка снаружи (переход на
// другой этап) применяется через setContent; собственные правки редактора в
// effect не возвращаются обратно — иначе курсор прыгал бы на каждый ввод.
import { EditorContent, useEditor } from "@tiptap/react";
import type { ChainedCommands } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef } from "react";

interface RichTextEditorProps {
  // Подпись поля (aria-label редактора и подпись блока).
  label: string;
  // Текущее значение — HTML (пустая строка — пустой абзац).
  value: string;
  // Новое значение при правке (HTML). Пустая строка — очистка поля.
  onChange: (html: string) => void;
  // Скрытая подпись под редактором (например, про печать); без неё — ничего.
  hint?: string;
}

// Кнопки панели форматирования: подпись, aria-label, активное состояние и
// команда TipTap. Список в порядке вывода; подписи текстовые (как у писем).
const TOOLBAR: readonly {
  key: string;
  title: string;
  mark: string;
  run: (chain: ChainedCommands) => ChainedCommands;
}[] = [
  { key: "bold", title: "Полужирный", mark: "bold", run: (c) => c.toggleBold() },
  { key: "italic", title: "Курсив", mark: "italic", run: (c) => c.toggleItalic() },
  { key: "underline", title: "Подчёркнутый", mark: "underline", run: (c) => c.toggleUnderline() },
  { key: "bulletList", title: "Маркированный список", mark: "bulletList", run: (c) => c.toggleBulletList() },
  { key: "orderedList", title: "Нумерованный список", mark: "orderedList", run: (c) => c.toggleOrderedList() },
  { key: "paragraph", title: "Абзац", mark: "paragraph", run: (c) => c.setParagraph() },
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

// Визуальный редактор текста этапа: панель кнопок + область ввода.
export function RichTextEditor(props: RichTextEditorProps) {
  const { label, value, onChange, hint } = props;
  // Последнее значение, отправленное редактором: правки изнутри в effect не
  // возвращаются (иначе setContent на каждом вводе сбрасывал бы курсор).
  const emitted = useRef<string>(value);
  const editor = useEditor({
    extensions: [StarterKit],
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

  return (
    <div className="sed-rte">
      <div className="sed-rte__toolbar" role="toolbar" aria-label={`Форматирование: ${label}`}>
        {TOOLBAR.map((item) => {
          const active = editor?.isActive(item.mark) ?? false;
          return (
            <button
              key={item.key}
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
              {item.title}
            </button>
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
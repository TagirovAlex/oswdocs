// Тесты визуального редактора текста этапа (TipTap): значение — HTML, кнопки
// форматирования дают разметку, правка снаружи применяется в редакторе.
// Сеть не нужна: редактор работает локально, значение приходит пропсом (как
// title/stage_lines этапа в админке).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  RichTextEditor,
  richTextAppend,
  richTextHtmlToLines,
  richTextLinesToHtml,
} from "./rich-text";

// Выделение всего текста редактора и синхронизация его с состоянием ProseMirror.
// jsdom не шлёт selectionchange сам и не фокусирует contenteditable без
// tabindex, а редактор читает DOM-выделение только у сфокусированного поля.
async function selectAllInEditor(): Promise<HTMLElement> {
  const area = (await waitFor(() =>
    document.querySelector<HTMLElement>(".sed-rte__field .ProseMirror"),
  )) as HTMLElement;
  area.setAttribute("tabindex", "-1");
  area.focus();
  const range = document.createRange();
  range.selectNodeContents(area);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
  await waitFor(() => expect(area.textContent).not.toBe(""));
  return area;
}

describe("RichTextEditor", () => {
  // Панель форматирования и предупреждение о печати на месте.
  it("рисует панель кнопок и предупреждение о печати", async () => {
    render(
      <RichTextEditor
        label="Название этапа"
        value="<p>Этап</p>"
        onChange={() => undefined}
        hint="Разметка попадёт в печать"
      />,
    );
    const toolbar = await screen.findByRole("toolbar", { name: "Форматирование: Название этапа" });
    for (const title of ["Полужирный", "Курсив", "Подчёркнутый", "Маркированный список", "Нумерованный список", "Абзац"]) {
      expect(toolbar).toContainElement(screen.getByRole("button", { name: title }));
    }
    expect(screen.getByText("Разметка попадёт в печать")).toBeInTheDocument();
  });

  // Панель расширена: выравнивание по краям и заголовки H1–H3, у каждой кнопки
  // подпись текстом и иконка-инлайн-SVG (графика, не текстовый глиф).
  it("рисует кнопки выравнивания и заголовков иконками", async () => {
    render(
      <RichTextEditor label="Шапка бланка" value="<p>Акт</p>" onChange={() => undefined} />,
    );
    const toolbar = await screen.findByRole("toolbar", { name: "Форматирование: Шапка бланка" });
    const titles = [
      "Полужирный",
      "Курсив",
      "Подчёркнутый",
      "Маркированный список",
      "Нумерованный список",
      "По левому краю",
      "По центру",
      "По правому краю",
      "Заголовок 1",
      "Заголовок 2",
      "Заголовок 3",
      "Абзац",
    ];
    for (const title of titles) {
      const button = screen.getByRole("button", { name: title });
      expect(toolbar).toContainElement(button);
      // Подпись — только подсказкой, в кнопке иконка инлайн-SVG.
      expect(button).toHaveAttribute("title", title);
      expect(button.querySelector("svg")).not.toBeNull();
      expect(button).toHaveTextContent("");
    }
    expect(toolbar.querySelectorAll("button")).toHaveLength(titles.length);
  });

  // Исходное значение показывается в области редактора.
  it("показывает переданное значение", async () => {
    render(
      <RichTextEditor
        label="Название этапа"
        value="<p>Непосредственный руководитель</p>"
        onChange={() => undefined}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("Непосредственный руководитель")).toBeInTheDocument(),
    );
  });

  // Кнопка форматирования помечает выделенное и попадает в значение HTML.
  it("кнопка «Полужирный» отдаёт разметку в значение", async () => {
    const seen: string[] = [];
    render(
      <RichTextEditor
        label="Название этапа"
        value="<p>Подпись</p>"
        onChange={(html) => seen.push(html)}
      />,
    );
    await selectAllInEditor();
    fireEvent.click(screen.getByRole("button", { name: "Полужирный" }));
    await waitFor(() => expect(seen[seen.length - 1]).toContain("<strong>"));
  });

  // Списки и абзац — те же команды, значение остаётся HTML.
  it("кнопка «Маркированный список» отдаёт список в значение", async () => {
    const seen: string[] = [];
    render(
      <RichTextEditor
        label="Пункт этапа"
        value="<p>Пункт</p>"
        onChange={(html) => seen.push(html)}
      />,
    );
    await selectAllInEditor();
    fireEvent.click(screen.getByRole("button", { name: "Маркированный список" }));
    await waitFor(() => expect(seen[seen.length - 1]).toContain("<ul>"));
  });

  // Правка значения снаружи (переход на другой этап) применяется в редакторе.
  it("правка значения снаружи применяется", async () => {
    const props = { label: "Название этапа", onChange: () => undefined };
    const { rerender } = render(<RichTextEditor {...props} value="<p>Первый этап</p>" />);
    await waitFor(() => expect(screen.getByText("Первый этап")).toBeInTheDocument());
    rerender(<RichTextEditor {...props} value="<p>Второй этап</p>" />);
    await waitFor(() => expect(screen.getByText("Второй этап")).toBeInTheDocument());
    expect(screen.queryByText("Первый этап")).not.toBeInTheDocument();
  });

  // Выравнивание по центру сохраняется в значении (печать читает его из style).
  it("кнопка «По центру» отдаёт выравнивание в значение", async () => {
    const seen: string[] = [];
    render(
      <RichTextEditor
        label="Шапка бланка"
        value="<p>Подпись сотрудника</p>"
        onChange={(html) => seen.push(html)}
      />,
    );
    await selectAllInEditor();
    fireEvent.click(screen.getByRole("button", { name: "По центру" }));
    await waitFor(() => expect(seen[seen.length - 1]).toContain("text-align: center"));
    // Кнопка выравнивания остаётся нажатой (следующий шаг печати по центру).
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "По центру" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
  });

  // Заголовок H2 сохраняется тегом в значении.
  it("кнопка «Заголовок 2» отдаёт тег заголовка в значение", async () => {
    const seen: string[] = [];
    render(
      <RichTextEditor label="Пункт этапа" value="<p>Раздел</p>" onChange={(html) => seen.push(html)} />,
    );
    await selectAllInEditor();
    fireEvent.click(screen.getByRole("button", { name: "Заголовок 2" }));
    await waitFor(() => expect(seen[seen.length - 1]).toContain("<h2>"));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Заголовок 2" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
  });

  // Выравнивание и заголовок переживают перерисовку: значение, полученное
  // снаружи (из сохранённого бланка), редактору не теряется.
  it("значение с выравниванием и заголовком показывается без потерь", async () => {
    render(
      <RichTextEditor
        label="Шапка бланка"
        value='<h1 style="text-align: center">Акт вымышленного увольнения</h1>'
        onChange={() => undefined}
      />,
    );
    const area = await waitFor(() =>
      document.querySelector<HTMLElement>(".sed-rte__field .ProseMirror h1"),
    ) as HTMLElement;
    expect(area.textContent).toBe("Акт вымышленного увольнения");
    expect(area.style.textAlign).toBe("center");
  });
});

// Строки бланка (<-> HTML редактора): контракт хранения подвала и текста шага —
// список строк, а редактор показывает их одним полем.
describe("строки бланка визуального редактора", () => {
  // Абзацы редактора делятся на строки печати (границы те же, что у печати).
  it("делит HTML редактора на строки по абзацам, заголовкам и <br>", () => {
    expect(
      richTextHtmlToLines("<p>Первый</p><p>Второй</p>"),
    ).toEqual(["<p>Первый</p>", "<p>Второй</p>"]);
    expect(richTextHtmlToLines("<h2>Раздел</h2><p>Текст</p>")).toEqual([
      "<h2>Раздел</h2>",
      "<p>Текст</p>",
    ]);
    expect(richTextHtmlToLines("<p>Первая<br>вторая</p>")).toEqual([
      "<p>Первая<br>",
      "вторая</p>",
    ]);
    expect(richTextHtmlToLines("")).toEqual([]);
  });

  // Список и разметка блока не рвутся: строка остаётся целым блоком (иначе
  // обратная сборка разорвала бы его и редактор терял бы позицию каретки).
  it("не рвёт список и оформленные абзацы пополам", () => {
    const html = "<p>Текст</p><ul><li><p>Пункт</p></li></ul>";
    expect(richTextHtmlToLines(html)).toEqual(["<p>Текст</p>", "<ul><li><p>Пункт</p></li></ul>"]);
    expect(richTextHtmlToLines('<p style="text-align: right"><b>Дата</b></p>')).toEqual([
      '<p style="text-align: right"><b>Дата</b></p>',
    ]);
  });

  // Сборка и разбор — обратные операции: правка редактора не теряется при
  // возврате значения в редактор (иначе каретка прыгала бы на каждый ввод).
  it("собирает строки обратно в HTML без потерь", () => {
    const html = '<p><strong>Первый</strong></p><h3 style="text-align: center">Заголовок</h3>';
    expect(richTextHtmlToLines(richTextLinesToHtml(richTextHtmlToLines(html)))).toEqual(
      richTextHtmlToLines(html),
    );
    expect(richTextLinesToHtml(richTextHtmlToLines(html))).toBe(html);
    // Уже сохранённые бланки: простой текст оборачивается абзацем редактора.
    expect(richTextLinesToHtml(["Подпись: {manager}", "<p>Дата {date}</p>"])).toBe(
      "<p>Подпись: {manager}</p><p>Дата {date}</p>",
    );
    expect(richTextLinesToHtml([])).toBe("");
  });

// Плейсхолдер дописывается в конец содержимого выбранной строки подвала.
  it("дописывает текст в конец абзаца значения", () => {
    expect(richTextAppend("<p></p>", "{date}")).toBe("<p>{date}</p>");
    expect(richTextAppend("Подпись: {manager}", "{date}")).toBe("Подпись: {manager}{date}");
    expect(richTextAppend("<p>Первый</p><p>Дата</p>", "{fio}")).toBe(
      "<p>Первый</p><p>Дата{fio}</p>",
    );
  });
});
// Тесты визуального редактора текста этапа (TipTap): значение — HTML, кнопки
// форматирования дают разметку, правка снаружи применяется в редакторе.
// Сеть не нужна: редактор работает локально, значение приходит пропсом (как
// title/stage_lines этапа в админке).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RichTextEditor } from "./rich-text";

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
});
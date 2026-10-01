// Тесты окна создания заявки (?view=create): кнопка «Закрыть» — внизу формы
// (не в верхней липкой полосе) и с подтверждением при несохранённых данных.
// Данные — из requests-client (мокается), сеть не нужна.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CreateWindow } from "./create-window";
import { getEnterprises, getStepGroups } from "./requests-client";

vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    getStepGroups: vi.fn(),
  };
});

beforeEach(() => {
  vi.restoreAllMocks();
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(getStepGroups).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue([
    { code: "ENT_PRIMER_1", name: "Предприятие «Пример-1» (вымышленное)" },
  ]);
  vi.mocked(getStepGroups).mockResolvedValue(["SED_STEP_BUH"]);
});

describe("CreateWindow", () => {
  // Кнопка «Закрыть» — после формы, внизу окна.
  it("кнопка «Закрыть» внизу, после формы", async () => {
    render(<CreateWindow role="hr" />);
    await waitFor(() => expect(screen.getByText("Создание заявки")).toBeInTheDocument());
    const heading = screen.getByText("Создание заявки");
    const close = screen.getByRole("button", { name: "Закрыть" });
    expect(
      heading.compareDocumentPosition(close) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // Верхней липкой полосы нет — «Закрыть» последняя кнопка окна.
    const buttons = screen.getAllByRole("button");
    expect(buttons[buttons.length - 1].textContent).toBe("Закрыть");
  });

  // Чистая форма — закрытие без подтверждения.
  it("«Закрыть» без несохранённых данных закрывает окно без подтверждения", async () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    const confirm = vi.spyOn(window, "confirm");

    render(<CreateWindow role="hr" />);
    await waitFor(() => expect(screen.getByText("Создание заявки")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));

    expect(close).toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  // Несохранённые данные (форма изменена) — закрытие с подтверждением.
  it("«Закрыть» при несохранённых данных спрашивает подтверждение", async () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<CreateWindow role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), {
      target: { value: "ENT_PRIMER_1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));

    expect(confirm).toHaveBeenCalledWith("Есть несохранённые данные. Закрыть без сохранения?");
    expect(close).not.toHaveBeenCalled();
  });
});
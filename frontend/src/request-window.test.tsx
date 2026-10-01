// Тесты окна карточки заявки (?view=request&id=…): кнопка «Закрыть» — внизу
// содержимого (после карточки), а не в верхней липкой полосе. Данные — из
// requests-client (мокается), сеть не нужна.
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequestWindow } from "./request-window";
import { getAttachments, getDocuments, getRequest } from "./requests-client";
import type { RequestOut } from "./requests-client";

vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getRequest: vi.fn(),
    getDocuments: vi.fn(),
    getAttachments: vi.fn(),
  };
});

const request: RequestOut = {
  id: "REQ-0001",
  status: "На согласовании",
  route_origin: "custom",
  enterprise: "ENT_PRIMER_1",
  tab_num: "Т-000201",
  department: "Цех № 1",
  position: "Слесарь",
  fio: "Громов Игорь Олегович",
  created_by: "petrov.pp",
  steps: [
    {
      order: 1,
      owner_group: "SED_STEP_BUH",
      resolver: "by_group",
      can_act: false,
      status: "ожидает",
      expires_at: "2026-10-05T10:00:00+00:00",
    },
  ],
};

beforeEach(() => {
  vi.mocked(getRequest).mockReset();
  vi.mocked(getDocuments).mockReset();
  vi.mocked(getAttachments).mockReset();
  vi.mocked(getRequest).mockResolvedValue(request);
  vi.mocked(getDocuments).mockResolvedValue([]);
  vi.mocked(getAttachments).mockResolvedValue([]);
});

describe("RequestWindow", () => {
  // Кнопка «Закрыть» присутствует и расположена ПОСЛЕ карточки (внизу окна).
  it("кнопка «Закрыть» внизу, после содержимого карточки", async () => {
    render(<RequestWindow requestId="REQ-0001" role="hr" />);
    await waitFor(() => expect(screen.getByText(/Карточка заявки REQ-0001/)).toBeInTheDocument());
    const heading = screen.getByText(/Карточка заявки REQ-0001/);
    const close = screen.getByRole("button", { name: "Закрыть" });
    // Кнопка идёт после содержимого карточки в DOM-порядке.
    expect(
      heading.compareDocumentPosition(close) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  // Верхней липкой полосы с кнопкой нет: «Закрыть» — единственная такая кнопка
  // и она последняя в окне.
  it("кнопка «Закрыть» единственная и последняя в окне", async () => {
    render(<RequestWindow requestId="REQ-0001" role="hr" />);
    await waitFor(() => expect(screen.getByText(/Карточка заявки REQ-0001/)).toBeInTheDocument());
    const buttons = screen.getAllByRole("button");
    expect(buttons.filter((b) => b.textContent === "Закрыть")).toHaveLength(1);
    expect(buttons[buttons.length - 1].textContent).toBe("Закрыть");
  });
});
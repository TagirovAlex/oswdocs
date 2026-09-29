// Тесты экрана списка (волна B4): данные — из requests-client (мокается),
// фильтры работают на клиенте, ролевая обрезка — маска для владельца.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { getEnterprises, getRequests } from "./requests-client";
import type { RequestOut } from "./requests-client";
import { RequestsScreen } from "./requests";

// Мок клиента заявок; чистые функции (toRequestRow/filterRequests) — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    getRequests: vi.fn(),
  };
});

const enterprises = [{ code: "ENT_PRIMER_1", name: "Предприятие «Пример-1» (вымышленное)" }];

// Заявка из GET /api/requests (RequestOut; для владельца fio=null).
function requestWith(id: string, fio: string | null, enterprise: string): RequestOut {
  return {
    id,
    status: "На согласовании",
    route_origin: "custom",
    enterprise,
    tab_num: "Т-000201",
    department: "Цех № 1",
    position: "Слесарь",
    fio,
    created_by: "petrov.pp",
    steps: [
      { order: 1, owner_group: "SED_STEP_BUH", resolver: "by_group", status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
    ],
  };
}

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(getRequests).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
});

describe("RequestsScreen", () => {
  // ОК видит полные подписи.
  it("показывает заявки роли ОК", async () => {
    vi.mocked(getRequests).mockResolvedValue([requestWith("REQ-0001", "Петров Пётр Петрович", "ENT_PRIMER_1")]);
    render(<RequestsScreen role="hr" />);
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(screen.getByText(/Петров Пётр/)).toBeInTheDocument();
  });

  // Владелец видит только маски (fio=null → «Сотрудник № {id}»).
  it("владельцу показывает маски вместо ФИО", async () => {
    vi.mocked(getRequests).mockResolvedValue([requestWith("REQ-0001", null, "ENT_PRIMER_1")]);
    render(<RequestsScreen role="owner" />);
    await waitFor(() => expect(screen.getByText("Сотрудник № REQ-0001")).toBeInTheDocument());
    expect(screen.queryByText(/Петров Пётр/)).not.toBeInTheDocument();
  });

  // Гостю список закрыт (403 от API).
  it("гостю показывает ошибку доступа", async () => {
    vi.mocked(getRequests).mockRejectedValue(new ApiHttpError(403, "Гостю список недоступен"));
    render(<RequestsScreen role="guest" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  // Фильтр поиска сужает список на клиенте.
  it("фильтр поиска работает", async () => {
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("REQ-0001", "Петров Пётр Петрович", "ENT_PRIMER_1"),
      requestWith("REQ-0002", "Сидорова Анна Сергеевна", "ENT_PRIMER_1"),
    ]);
    render(<RequestsScreen role="hr" />);
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "Сидорова" } });
    await waitFor(() => expect(screen.getByText("REQ-0002")).toBeInTheDocument());
    expect(screen.queryByText("REQ-0001")).not.toBeInTheDocument();
  });

  // Пустой список — «Заявок нет».
  it("пустой список показывает «Заявок нет»", async () => {
    vi.mocked(getRequests).mockResolvedValue([]);
    render(<RequestsScreen role="hr" />);
    await waitFor(() => expect(screen.getByText("Заявок нет")).toBeInTheDocument());
  });
});
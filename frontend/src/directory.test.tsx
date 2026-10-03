// Тесты справочника сотрудников (Directory, Задача 3): поиск, окно карточки
// (window.open), автосвязка. Карточка сотрудника и привязка AD — в окне
// (EmployeeCardView, см. employee-card-view.test.tsx).
// Сеть не нужна: requests-client мокается; ФИО/предприятия вымышленные.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Directory } from "./directory";
import { getEnterprises, searchEmployees, syncLinks } from "./requests-client";

vi.mock("./requests-client", () => ({
  getEnterprises: vi.fn(),
  searchEmployees: vi.fn(),
  syncLinks: vi.fn(),
}));

const enterprises = [{ code: "A", name: "ООО Альфа" }];
const hits = [
  {
    key: "A|zup|001",
    tab_num: "001",
    fio: "Сказочников Тест Тестович",
    dept: "Цех",
    position: "Тестировщик",
    hire_date: null,
    ad_sam: null,
    ad_status: "match",
    needs_manual_review: false,
  },
];

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(searchEmployees).mockReset();
  vi.mocked(syncLinks).mockReset();
});

describe("Directory", () => {
  it("ОК находит сотрудников; клик по строке открывает окно карточки", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({ items: hits });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    render(<Directory role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() => expect(screen.getByText("Сказочников Тест Тестович")).toBeInTheDocument());

    // ОК — просмотр: кнопки автосвязки нет.
    expect(screen.queryByText("Автосвязка 1С↔AD")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Сказочников Тест Тестович"));
    expect(open).toHaveBeenCalledWith("?view=employee&key=A%7Czup%7C001", "_blank", expect.stringContaining("popup"));
    open.mockRestore();
  });

  it("админ запускает автосвязку 1С↔AD", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({ items: [] });
    vi.mocked(syncLinks).mockResolvedValue({
      synced: true,
      scanned: 5,
      created: 2,
      skipped_linked: 1,
      skipped_1c_duplicates: 0,
      skipped_ad_no_match: 1,
      skipped_ad_duplicates: 1,
      errors: [],
    });

    render(<Directory role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Автосвязка 1С↔AD"));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/создано 2, просмотрено 5/),
    );
    expect(syncLinks).toHaveBeenCalledTimes(1);
  });

  // Поиск с запросом идёт большим размером страницы (500), чтобы показывать все
  // совпадения, а не дефолтные 50; без запроса — дефолтный размер.
  it("поиск с запросом использует page_size=500, без запроса — дефолтный", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({ items: hits });

    render(<Directory role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());

    // Пустой запрос (весь список справочника) — дефолтный размер страницы.
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() => expect(screen.getByText("Сказочников Тест Тестович")).toBeInTheDocument());
    expect(searchEmployees).toHaveBeenLastCalledWith("A", "", 1, 50);

    // Запрос введён — большой размер страницы, чтобы были видны все совпадения.
    fireEvent.change(screen.getByLabelText("Поиск по справочнику"), { target: { value: "Сказочников" } });
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() =>
      expect(searchEmployees).toHaveBeenLastCalledWith("A", "Сказочников", 1, 500),
    );
  });

  // Пейджер справочника: номера страниц и «Первая/Последняя».
  it("пейджер справочника: номера страниц и Первая/Последняя", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({
      items: hits,
      total: 120,
      page: 1,
      page_size: 50,
    });

    render(<Directory role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() => expect(screen.getByText("стр 1 из 3")).toBeInTheDocument());

    // Номера страниц, «Первая» недоступна на первой, «Последняя» доступна.
    expect(screen.getByRole("button", { name: "Страница 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Страница 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Первая страница" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Последняя страница" })).not.toBeDisabled();

    // Клик по номеру 3 → запрос page=3; «Последняя» становится недоступной.
    fireEvent.click(screen.getByRole("button", { name: "Страница 3" }));
    await waitFor(() => expect(searchEmployees).toHaveBeenLastCalledWith("A", "", 3, 50));
    expect(screen.getByRole("button", { name: "Последняя страница" })).toBeDisabled();

    // «Первая» → возврат на первую страницу.
    fireEvent.click(screen.getByRole("button", { name: "Первая страница" }));
    await waitFor(() => expect(searchEmployees).toHaveBeenLastCalledWith("A", "", 1, 50));
  });
});
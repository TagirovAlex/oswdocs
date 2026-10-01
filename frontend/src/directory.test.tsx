// Тесты справочника сотрудников (Directory): поиск, карточка, привязка AD (админ).
// Сеть не нужна: requests-client мокается; ФИО/предприятия вымышленные.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Directory } from "./directory";
import { createLink, getEmployeeCard, getEnterprises, searchEmployees } from "./requests-client";

vi.mock("./requests-client", () => ({
  getEnterprises: vi.fn(),
  searchEmployees: vi.fn(),
  getEmployeeCard: vi.fn(),
  createLink: vi.fn(),
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
    needs_manual_review: false,
  },
];
const card = {
  key: "A|zup|001",
  enterprise: "A",
  base_code: "zup",
  tab_num: "001",
  truth_source: "1c",
  link: { linked: false },
  divergences: [],
  needs_manual_review: false,
  fio: "Сказочников Тест Тестович",
  dept: "Цех",
  position: "Тестировщик",
  hire_date: "2020-01-15",
  dismissal_date: null,
  ad_sam: null,
  snapshot_1c: null,
  snapshot_ad: null,
};

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(searchEmployees).mockReset();
  vi.mocked(getEmployeeCard).mockReset();
  vi.mocked(createLink).mockReset();
});

describe("Directory", () => {
  it("ОК находит сотрудников и видит карточку (просмотр)", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({ items: hits });
    vi.mocked(getEmployeeCard).mockResolvedValue(card);

    render(<Directory role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() => expect(screen.getByText("Сказочников Тест Тестович")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Сказочников Тест Тестович"));
    await waitFor(() => expect(screen.getByText(/Карточка:/)).toBeInTheDocument());
    // ОК — просмотр: кнопки привязки нет.
    expect(screen.queryByText("Привязать AD")).not.toBeInTheDocument();
  });

  it("админ привязывает AD к карточке", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({ items: hits });
    vi.mocked(getEmployeeCard).mockResolvedValue(card);
    vi.mocked(createLink).mockResolvedValue({});

    render(<Directory role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() => expect(screen.getByText("Сказочников Тест Тестович")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Сказочников Тест Тестович"));
    await waitFor(() => expect(screen.getByLabelText("Логин AD для привязки")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Логин AD для привязки"), { target: { value: "t.skaz" } });
    fireEvent.click(screen.getByText("Привязать AD"));
    await waitFor(() => expect(createLink).toHaveBeenCalledWith({ enterprise: "A", base_code: "zup", tab_num: "001", sam: "t.skaz" }));
  });
});
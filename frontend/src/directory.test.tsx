// Тесты справочника сотрудников (Directory): поиск, карточка, привязка AD (админ).
// Сеть не нужна: requests-client мокается; ФИО/предприятия вымышленные.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Directory } from "./directory";
import {
  createLink,
  getEmployeeCard,
  getEnterprises,
  searchAd,
  searchEmployees,
  syncLinks,
} from "./requests-client";

vi.mock("./requests-client", () => ({
  getEnterprises: vi.fn(),
  searchEmployees: vi.fn(),
  getEmployeeCard: vi.fn(),
  createLink: vi.fn(),
  searchAd: vi.fn(),
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
  ad_status: "match",
  snapshot_1c: null,
  snapshot_ad: null,
};
const adCandidate = {
  sam: "t.skaz",
  display_name: "Сказочников Тест Тестович",
  department: "Цех",
  title: "Тестировщик",
  mail: "t.skaz@example.local",
};

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(searchEmployees).mockReset();
  vi.mocked(getEmployeeCard).mockReset();
  vi.mocked(createLink).mockReset();
  vi.mocked(searchAd).mockReset();
  vi.mocked(syncLinks).mockReset();
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

  it("админ ищет в AD и привязывает выбранного кандидата", async () => {
    vi.mocked(getEnterprises).mockResolvedValue(enterprises);
    vi.mocked(searchEmployees).mockResolvedValue({ items: hits });
    vi.mocked(getEmployeeCard).mockResolvedValue(card);
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createLink).mockResolvedValue({});

    render(<Directory role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие справочника")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Найти"));
    await waitFor(() => expect(screen.getByText("Сказочников Тест Тестович")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Сказочников Тест Тестович"));
    await waitFor(() => expect(screen.getByLabelText("Поиск в AD")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Сказочников" } });
    fireEvent.click(screen.getByText("Найти в AD"));
    await waitFor(() => expect(screen.getByLabelText("Кандидаты AD")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Привязать"));
    await waitFor(() => expect(createLink).toHaveBeenCalledWith({ enterprise: "A", base_code: "zup", tab_num: "001", sam: "t.skaz" }));
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
});
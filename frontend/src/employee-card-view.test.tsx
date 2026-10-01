// Тесты карточки сотрудника (EmployeeCardView, Задача 3): блок 1С + статус AD,
// интерактивный поиск кандидатов AD и привязка (только админ). Компонент
// используется в окне ?view=employee&key=… (см. employee-window.tsx).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EmployeeCardView } from "./employee-card-view";
import { createLink, getEmployeeCard, searchAd } from "./requests-client";

vi.mock("./requests-client", () => ({
  getEmployeeCard: vi.fn(),
  createLink: vi.fn(),
  searchAd: vi.fn(),
}));

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
  vi.mocked(getEmployeeCard).mockReset();
  vi.mocked(createLink).mockReset();
  vi.mocked(searchAd).mockReset();
  vi.mocked(getEmployeeCard).mockResolvedValue(card);
});

function renderView(role: "hr" | "admin" = "hr") {
  return render(<EmployeeCardView enterprise="A" baseCode="zup" tabNum="001" role={role} />);
}

describe("EmployeeCardView", () => {
  it("показывает 1С-блок и статус «совпадение найдено»", async () => {
    renderView("hr");
    await waitFor(() => expect(screen.getByText(/Карточка:/)).toBeInTheDocument());
    expect(screen.getByText(/подразделение Цех/)).toBeInTheDocument();
    expect(screen.getByText(/Точное совпадение ФИО в AD найдено/)).toBeInTheDocument();
    // ОК — просмотр: поиска AD нет.
    expect(screen.queryByLabelText("Поиск в AD")).not.toBeInTheDocument();
  });

  it("админ ищет в AD и привязывает выбранного кандидата", async () => {
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createLink).mockResolvedValue({});

    renderView("admin");
    await waitFor(() => expect(screen.getByLabelText("Поиск в AD")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Сказочников" } });
    fireEvent.click(screen.getByText("Найти в AD"));
    await waitFor(() => expect(screen.getByLabelText("Кандидаты AD")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Привязать"));
    await waitFor(() =>
      expect(createLink).toHaveBeenCalledWith({ enterprise: "A", base_code: "zup", tab_num: "001", sam: "t.skaz" }),
    );
  });

  it("показывает статус «синхронизация не прошла» при no_match", async () => {
    vi.mocked(getEmployeeCard).mockResolvedValue({ ...card, ad_status: "no_match" });
    renderView("hr");
    await waitFor(() => expect(screen.getByText(/Синхронизация не прошла/)).toBeInTheDocument());
  });
});
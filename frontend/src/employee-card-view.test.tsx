// Тесты учётной карточки сотрудника (EmployeeCardView, редизайн): блоки
// «Должность»/«Контактные данные» (1С и AD), подтверждение привязки по
// найденному совпадению (админ, один клик), сохранение связки через поиск AD,
// закрытие окна с подтверждением несохранённых изменений, ОК — только чтение.
// Компонент используется в окне ?view=employee&key=… (см. employee-window.tsx).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  phone: "8-800-555-35-35",
  email: "skaz@example.local",
  ad_sam: null,
  ad_status: "match",
  snapshot_1c: null,
  snapshot_ad: null,
  ad: {
    sam: "t.skaz",
    display_name: "Сказочников Т. Тестович",
    department: "Цех",
    title: "Инженер-тестировщик",
    manager_dn: "CN=Начальник Тестович,OU=SED,DC=example,DC=local",
    mail: "t.skaz@example.local",
  },
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
  vi.spyOn(window, "close").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderView(role: "hr" | "hr_admin" | "admin" = "hr") {
  return render(<EmployeeCardView enterprise="A" baseCode="zup" tabNum="001" role={role} />);
}

async function waitCard() {
  await waitFor(() => expect(screen.getByRole("heading", { level: 3 })).toBeInTheDocument());
}

describe("EmployeeCardView", () => {
  it("показывает блоки учётной карточки: ФИО 1С и AD, должность, контакты", async () => {
    renderView("hr");
    await waitCard();
    // Шапка: ФИО по 1С крупно, ниже — ФИО из AD.
    expect(screen.getByRole("heading", { level: 3, name: "Сказочников Тест Тестович" })).toBeInTheDocument();
    expect(screen.getByText("Сказочников Т. Тестович")).toBeInTheDocument();
    // Должность: значение 1С и AD.
    expect(screen.getByText("Тестировщик")).toBeInTheDocument();
    expect(screen.getByText("Инженер-тестировщик")).toBeInTheDocument();
    // Контактные данные: телефон/e-mail 1С и e-mail/руководитель AD.
    expect(screen.getByText("8-800-555-35-35")).toBeInTheDocument();
    expect(screen.getByText("skaz@example.local")).toBeInTheDocument();
    expect(screen.getByText("t.skaz@example.local")).toBeInTheDocument();
    expect(screen.getByText("CN=Начальник Тестович,OU=SED,DC=example,DC=local")).toBeInTheDocument();
  });

  // Даты работы из 1С в блоке «Должность»: приём выводится, увольнение — прочерк.
  it("показывает даты приёма и увольнения из 1С", async () => {
    renderView("hr");
    await waitCard();
    expect(screen.getByText("Дата приёма").nextElementSibling).toHaveTextContent("2020-01-15");
    // Увольнения не было — прочерк вместо пустой даты.
    expect(screen.getByText("Дата увольнения").nextElementSibling).toHaveTextContent("—");
  });

  it("админ подтверждает привязку по найденному совпадению одним кликом", async () => {
    vi.mocked(createLink).mockResolvedValue({});

    renderView("admin");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Подтвердить привязку" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Подтвердить привязку" }));

    await waitFor(() =>
      expect(createLink).toHaveBeenCalledWith({ enterprise: "A", base_code: "zup", tab_num: "001", sam: "t.skaz" }),
    );
    await waitFor(() => expect(screen.getByText("Связка сохранена")).toBeInTheDocument());
    // «Подтвердить привязку» НЕ закрывает окно (карточка обновляется).
    expect(window.close).not.toHaveBeenCalled();
    // Карточка перечитана после сохранения (связка отразится на экране).
    await waitFor(() => expect(vi.mocked(getEmployeeCard).mock.calls.length).toBeGreaterThan(1));
  });

  it("ОК видит подсказку «подтверждение выполняет админ» без кнопки", async () => {
    renderView("hr");
    await waitCard();
    expect(screen.getByText(/подтверждение выполняет админ/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подтвердить привязку" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("админ сохраняет связку: поиск AD, выбор кандидата и «Сохранить» → createLink", async () => {
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createLink).mockResolvedValue({});

    renderView("admin");
    await waitFor(() => expect(screen.getByLabelText("Поиск в AD")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Сказочников" } });
    fireEvent.click(screen.getByText("Найти в AD"));
    await waitFor(() => expect(screen.getByLabelText("Кандидаты AD")).toBeInTheDocument());

    const save = screen.getByRole("button", { name: "Сохранить" });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByText("Привязать"));
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() =>
      expect(createLink).toHaveBeenCalledWith({ enterprise: "A", base_code: "zup", tab_num: "001", sam: "t.skaz" }),
    );
    await waitFor(() => expect(screen.getByText("Связка сохранена")).toBeInTheDocument());
    // После сохранения попап закрывается.
    expect(window.close).toHaveBeenCalled();
  });

  it("«Закрыть» без изменений закрывает окно", async () => {
    renderView("hr");
    await waitCard();
    fireEvent.click(screen.getByText("Закрыть"));
    expect(window.close).toHaveBeenCalled();
  });

  it("Закрытие с несохранённой связкой спрашивает подтверждение (отмена — без сохранения)", async () => {
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createLink).mockResolvedValue({});
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderView("admin");
    await waitFor(() => expect(screen.getByLabelText("Поиск в AD")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Сказочников" } });
    fireEvent.click(screen.getByText("Найти в AD"));
    await waitFor(() => expect(screen.getByLabelText("Кандидаты AD")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Привязать"));
    fireEvent.click(screen.getByText("Закрыть"));

    expect(window.confirm).toHaveBeenCalledWith("Есть несохранённые изменения связки. Сохранить?");
    expect(window.close).toHaveBeenCalled();
    expect(createLink).not.toHaveBeenCalled();
  });
});
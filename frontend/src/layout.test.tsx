// Тесты сетки скелета (Задача 3): вкладки, дерево, тулбар, фильтры, таблица,
// выход; клик по строке и «Создать заявку» — окна-попы (window.open).
// Данные — из requests-client (мокается), сеть не нужна.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SedLayout } from "./layout";
import { ThemeProvider } from "./theme";
import { getEnterprises, getFolders, getRequests } from "./requests-client";
import type { Folder, RequestOut } from "./requests-client";
import type { Role } from "./api-mock";

// Мок клиента заявок; чистые функции (toRequestRow/filterRequests) — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    getFolders: vi.fn(),
    getRequests: vi.fn(),
  };
});

// Предприятие из настроек (код — значение фильтра).
const enterprises = [{ code: "ENT_PRIMER_1", name: "Предприятие «Пример-1» (вымышленное)" }];

// Папки по контракту GET /api/folders.
const folders: Folder[] = [
  { id: "agreement", title: "На согласовании", count: 1 },
  { id: "revision", title: "На доработке", count: 0 },
  { id: "done", title: "Завершённые", count: 1 },
  { id: "mine", title: "Мои задачи", count: 1 },
];

// Заявка из GET /api/requests (RequestOut; для владельца fio=null).
function requestWith(fio: string | null, status: string, id: string = "REQ-0001"): RequestOut {
  return {
    id,
    status,
    route_origin: "custom",
    enterprise: "ENT_PRIMER_1",
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

// Обёртка с темой для рендера каркаса.
function renderWithTheme(role: Role = "hr", onLogout: () => void = () => undefined) {
  return render(
    <ThemeProvider initial="light">
      <SedLayout role={role} onLogout={onLogout} />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(getFolders).mockReset();
  vi.mocked(getRequests).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
});

describe("SedLayout", () => {
  // Базовая сетка для роли ОК.
  it("показывает вкладки, папки, тулбар, фильтры и таблицу", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);

    renderWithTheme("hr");
    // Вкладки (ОК: без «Настроек»).
    expect(screen.getByText("Заявки")).toBeInTheDocument();
    expect(screen.getByText("Создание")).toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
    // Тулбар: создание в отдельном окне, печать — в карточке окна.
    expect(screen.getByText("Создать заявку")).toBeInTheDocument();
    expect(screen.queryByText("Печать")).not.toBeInTheDocument();
    // Фильтры.
    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
    expect(screen.getByLabelText("Предприятие")).toBeInTheDocument();
    // Дерево и таблица подгрузятся из API-клиента.
    await waitFor(() => expect(screen.getByText("На согласовании")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(screen.getByText(/Громов Игорь/)).toBeInTheDocument();
  });

  // Владелец видит маски вместо ФИО (API отдаёт fio=null).
  it("владелец видит маски вместо ФИО", async () => {
    vi.mocked(getFolders).mockResolvedValue([{ id: "mine", title: "Мои задачи", count: 1 }]);
    vi.mocked(getRequests).mockResolvedValue([requestWith(null, "На согласовании")]);

    renderWithTheme("owner");
    await waitFor(() => expect(screen.getByText("Сотрудник № REQ-0001")).toBeInTheDocument());
    expect(screen.queryByText(/Громов/)).not.toBeInTheDocument();
  });

  // Папка «Завершённые» фильтрует по статусу на клиенте.
  it("папка фильтрует строки по статусу", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("Громов Игорь Олегович", "На согласовании"),
      requestWith("Сидорова Анна Сергеевна", "Завершено", "REQ-0002"),
    ]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    // Переход в «Завершённые»: остаётся только завершённая заявка.
    fireEvent.click(screen.getByText("Завершённые"));
    await waitFor(() => expect(screen.getByText("REQ-0002")).toBeInTheDocument());
    expect(screen.queryByText("REQ-0001")).not.toBeInTheDocument();
  });

  // Пустой список — «Заявок нет».
  it("пустой список показывает «Заявок нет»", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("Заявок нет")).toBeInTheDocument());
  });

  // Селектор ролей убран: роль приходит из сессии.
  it("селектора ролей на экране нет", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("hr");
    expect(screen.queryByLabelText("Роль пользователя")).not.toBeInTheDocument();
  });

  // Вкладка «Настройки» — админу.
  it("вкладка «Настройки» видна админу", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("admin");
    expect(screen.getByText("Настройки")).toBeInTheDocument();
    expect(screen.getByText("Создание")).toBeInTheDocument();
  });

  // Вкладка «Создание» — только ОК и админу.
  it("вкладка «Создание» скрыта у владельца", () => {
    vi.mocked(getFolders).mockResolvedValue([{ id: "mine", title: "Мои задачи", count: 0 }]);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("owner");
    expect(screen.queryByText("Создание")).not.toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
  });

  // Руководитель ОК видит «Создание» и «Настройки» (контент-настройки, Фаза 2).
  it("руководитель ОК видит «Создание» и «Настройки»", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("hr_admin");
    expect(screen.getByText("Создание")).toBeInTheDocument();
    expect(screen.getByText("Настройки")).toBeInTheDocument();
  });

  // Кнопка «Выйти» вызывает сброс сессии.
  it("кнопка «Выйти» вызывает onLogout", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    const onLogout = vi.fn();
    renderWithTheme("hr", onLogout);
    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  // Клик по строке открывает окно карточки заявки (?view=request&id=…).
  it("клик по строке открывает окно карточки заявки", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    fireEvent.click(screen.getByText("REQ-0001"));
    expect(open).toHaveBeenCalledWith("?view=request&id=REQ-0001", "_blank", expect.stringContaining("popup"));
    open.mockRestore();
  });

  // «Создать заявку» открывает окно создания (?view=create).
  it("«Создать заявку» открывает окно создания", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("Создать заявку")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Создать заявку"));
    expect(open).toHaveBeenCalledWith("?view=create", "_blank", expect.stringContaining("popup"));
    open.mockRestore();
  });
});
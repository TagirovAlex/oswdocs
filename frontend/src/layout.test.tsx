// Тесты сетки скелета: вкладки, дерево, тулбар, фильтры, таблица, выход.
// Данные — из requests-client (мокается), сеть не нужна.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { SedLayout } from "./layout";
import { ThemeProvider } from "./theme";
import { getDocuments, getEnterprises, getFolders, getRequests, printRequest } from "./requests-client";
import type { DocumentMeta, Folder, RequestOut } from "./requests-client";
import type { Role } from "./api-mock";

// Мок клиента заявок; чистые функции (toRequestRow/filterRequests) — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    getFolders: vi.fn(),
    getRequests: vi.fn(),
    printRequest: vi.fn(),
    getDocuments: vi.fn(),
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
  vi.mocked(printRequest).mockReset();
  vi.mocked(getDocuments).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
  vi.mocked(getDocuments).mockResolvedValue([]);
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
    // Тулбар.
    expect(screen.getByText("Создать заявку")).toBeInTheDocument();
    expect(screen.getByText("Печать")).toBeInTheDocument();
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

  // Вкладка «Настройки» — только админу.
  it("вкладка «Настройки» видна только админу", () => {
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

  // Кнопка «Выйти» вызывает сброс сессии.
  it("кнопка «Выйти» вызывает onLogout", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    const onLogout = vi.fn();
    renderWithTheme("hr", onLogout);
    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  // Печать бегунка: до выбора заявки кнопка недоступна, после — статус «v1/v2 сгенерирован».
  it("кнопка «Печать» генерирует бегунок и показывает версию", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    vi.mocked(printRequest).mockResolvedValue({ version: "v2", generated: true, pdf_path: "/data/2.pdf", qr_payload: "q" });

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Печать" })).toBeDisabled();
    fireEvent.click(screen.getByText("REQ-0001"));
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("Бегунок v2 сгенерирован")).toBeInTheDocument());
    expect(vi.mocked(printRequest)).toHaveBeenCalledWith("REQ-0001");
  });

  // generated=false с reason (нет LibreOffice/шаблона) — показываем reason как статус, не ошибку.
  it("печать без LibreOffice показывает reason как статус, не как ошибку", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    vi.mocked(printRequest).mockResolvedValue({ version: "v1", generated: false, reason: "LibreOffice не настроен" });

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    fireEvent.click(screen.getByText("REQ-0001"));
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("LibreOffice не настроен")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // Ошибка печати (403) — понятный текст в alert.
  it("ошибка печати показывает понятный текст", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    vi.mocked(printRequest).mockRejectedValue(new ApiHttpError(403, "Печать доступна только ОК"));

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    fireEvent.click(screen.getByText("REQ-0001"));
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("Печать доступна только ОК")).toBeInTheDocument());
  });

  // Блок «Документы»: версии выбранной заявки со ссылками на PDF (URL строится из id).
  it("блок «Документы» показывает версии со ссылками на PDF", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    const docs: DocumentMeta[] = [
      { version: "v1", pdf_path: "/data/1.pdf", qr_payload: "q", created_at: "2026-09-28T10:00:00+00:00" },
      { version: "v2", pdf_path: "/data/2.pdf", qr_payload: "q", created_at: "2026-09-29T10:00:00+00:00" },
    ];
    vi.mocked(getDocuments).mockResolvedValue(docs);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(screen.queryByLabelText("Документы")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("REQ-0001"));
    await waitFor(() => expect(screen.getByLabelText("Документы")).toBeInTheDocument());
    expect(screen.getByText(/Бегунок v1/)).toBeInTheDocument();
    expect(screen.getByText(/Бегунок v2/)).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/api/documents/REQ-0001/pdf?version=v1",
      "/api/documents/REQ-0001/pdf?version=v2",
    ]);
  });

  // 404 по документам — понятный текст, не пустой экран.
  it("404 документов показывает понятный текст", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    vi.mocked(getDocuments).mockRejectedValue(new ApiHttpError(404, "Не найдено"));

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    fireEvent.click(screen.getByText("REQ-0001"));
    await waitFor(() => expect(screen.getByText("Не найдено")).toBeInTheDocument());
  });
});
// Тесты формы создания ОК (Задача 3.3): единая форма без стадий — блоки
// активируются по зависимостям (предприятие → сотрудник → маршрут → «Создать»).
// Сотрудник — живой поиск (debounce), данные из 1С справочные; маршрут —
// конструктор блоков с исполнителями из AD. Сеть не нужна: модуль
// requests-client мокается (как в admin-settings.test.tsx).
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError, me } from "./auth-client";
import { CreateForm } from "./create-form";
import { createRequest, getAdGroupMembers, getEmployeeCard, getEnterprises, getMyLinks, getRoutingCatalogs, getStepGroups, previewRoute, searchAd, searchEmployees, submitRequest } from "./requests-client";
import type { AdGroupMember, RoutePreview } from "./requests-client";

// Мок клиента заявок; чистые функции — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    searchEmployees: vi.fn(),
    getEmployeeCard: vi.fn(),
    searchAd: vi.fn(),
    getStepGroups: vi.fn(),
    getAdGroupMembers: vi.fn(),
    createRequest: vi.fn(),
    submitRequest: vi.fn(),
    getMyLinks: vi.fn(),
    previewRoute: vi.fn(),
    getRoutingCatalogs: vi.fn(),
  };
});

// Мок сессии: правой панели нужен инициатор (me), остальное — реальное.
vi.mock("./auth-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./auth-client")>();
  return { ...actual, me: vi.fn() };
});

// Предприятия из настроек (код — значение, название — подпись).
const enterprises = [{ code: "ENT_PRIMER_1", name: "Предприятие «Пример-1» (вымышленное)" }];
// Исполнитель AD для конструктора (GET /api/ad/search).
const adCandidate = {
  sam: "petrov.pp",
  display_name: "Петров Пётр Петрович",
  department: "Бухгалтерия",
  title: "Бухгалтер",
  mail: "petrov.pp@example.test",
};
// Группы-владельцы шагов из settings (GET /api/step-groups, {id,name}) и их
// состав из AD. В селекте — наименование (name), в owner_group уходит id.
const stepGroups = [
  { id: "SED_STEP_BUH", name: "Бухгалтерия (вымышленная группа)" },
  { id: "SED_STEP_OK", name: "Отдел кадров (вымышленная группа)" },
];
const groupMember = {
  sam: "sidorova.as",
  display_name: "Сидорова Анна Сергеевна",
  mail: "sidorova.as@example.test",
  department: "Бухгалтерия",
  title: "Главный бухгалтер",
};
// Состав второй группы-владельца (тесты удаления блока и гонки ответов).
const okGroupMember = {
  sam: "ivanov.ii",
  display_name: "Иванов Иван Иванович",
  mail: "ivanov.ii@example.test",
  department: "Отдел кадров",
  title: "Начальник",
};

// Предпросмотр маршрута по профилю (POST /api/requests/route/preview):
// профиль службы, два этапа — обязательный (не снимается) и необязательный.
const routePreview: RoutePreview = {
  profile: { id: 1, code: "uvol_base", name: "Увольнение (базовый профиль)" },
  service: { id: 3, dept_name: "Цех № 1", blank_kind: "office" },
  reason: "service_profile",
  stages: [
    {
      stage_id: 1,
      code: "rukovoditel",
      title: "Непосредственный руководитель",
      stage_lines: ["Строка руководителя"],
      owner_kind: "manager_ad",
      owner_group: null,
      owner_name: "Иванов Иван Иванович",
      optional: false,
      blocked_reason: null,
    },
    {
      stage_id: 2,
      code: "buhgalteriya",
      title: "Бухгалтерия",
      stage_lines: [],
      owner_kind: "ad_group",
      owner_group: "SED_STEP_BUH",
      owner_name: "Бухгалтерия (вымышленная группа)",
      optional: true,
      blocked_reason: null,
    },
  ],
  blank: "office",
};

// Справочник маршрутов админа (GET /api/settings/routing/catalogs): активные этапы.
const routingCatalogs = {
  stages: [
    {
      id: 3,
      code: "sluzhba_ok",
      title: "Служба ОК",
      owner_kind: "ad_group",
      owner_group: "SED_STEP_OK",
      optional: true,
      active: true,
    },
  ],
};

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(searchEmployees).mockReset();
  vi.mocked(getEmployeeCard).mockReset();
  vi.mocked(searchAd).mockReset();
  vi.mocked(getStepGroups).mockReset();
  vi.mocked(getAdGroupMembers).mockReset();
  vi.mocked(createRequest).mockReset();
  vi.mocked(submitRequest).mockReset();
  vi.mocked(getMyLinks).mockReset();
  vi.mocked(previewRoute).mockReset();
  vi.mocked(getRoutingCatalogs).mockReset();
  // По умолчанию маршрут подбирается по профилю, справочник этапов доступен.
  vi.mocked(previewRoute).mockResolvedValue(routePreview);
  vi.mocked(getRoutingCatalogs).mockResolvedValue(routingCatalogs);
  // По умолчанию связки АД-1С нет: инициатор — readonly-текст (старое поведение).
  vi.mocked(getMyLinks).mockResolvedValue([]);
  vi.mocked(me).mockReset();
  vi.mocked(me).mockResolvedValue({
    sam: "petrov.pp",
    fio: "Петров Пётр Петрович",
    groups: ["SED_HR"],
    role: "hr",
  });
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
  vi.mocked(getStepGroups).mockResolvedValue(stepGroups);
  vi.mocked(getAdGroupMembers).mockResolvedValue([groupMember]);
});

// Переключение формы в ручной режим маршрута (по умолчанию — по профилю).
async function switchToManualRoute(): Promise<void> {
  const manual = screen.getByRole("radio", { name: "Вручную" }) as HTMLInputElement;
  if (!manual.checked) fireEvent.click(manual);
}

// Заполнение формы до маршрута: предприятие → поиск 1С (503) → ручной ввод.
// mode="custom" (по умолчанию в тестах) — переключает форму в ручной режим
// маршрута; mode="auto" оставляет подбор по профилю.
async function fillEmployeeManually(mode: "auto" | "custom" = "custom"): Promise<void> {
  await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
  // Поиск; без баз 1С — 503 → ручной ввод с пометкой.
  fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });
  await waitFor(() => expect(screen.getByLabelText("Табельный №")).toBeInTheDocument());
  expect(screen.getByText(/введите вручную/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("ФИО"), { target: { value: "Громов Игорь Олегович" } });
  fireEvent.change(screen.getByLabelText("Табельный №"), { target: { value: "Т-000201" } });
  fireEvent.change(screen.getByLabelText("Подразделение"), { target: { value: "Цех № 1" } });
  fireEvent.change(screen.getByLabelText("Должность"), { target: { value: "Слесарь" } });
  // Обязательные поля макета: тема и содержание (иначе «Создать» недоступен).
  fireEvent.change(screen.getByLabelText("Тема"), { target: { value: "Увольнение сотрудника" } });
  fireEvent.change(screen.getByLabelText("Содержание"), {
    target: { value: "Прошу согласовать увольнение" },
  });
  // Маршрут появляется, когда сотрудник заполнен (без стадий и «Далее»).
  await waitFor(() => expect(screen.getByText("Маршрут согласования")).toBeInTheDocument());
  if (mode === "custom") await switchToManualRoute();
}

// Добавление исполнителя из AD в первый блок конструктора маршрута:
// модалка (кнопка «+ Добавить») → поиск → чекбокс → ОК.
async function addAdExecutor(): Promise<void> {
  fireEvent.click(screen.getByText("Добавить последовательный блок"));
  fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
  await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Петров" } });
  await waitFor(() => expect(screen.getByText("Петров Пётр Петрович")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" }));
  fireEvent.click(screen.getByRole("button", { name: "ОК" }));
}

describe("CreateForm", () => {
  // Полный путь: предприятие → сотрудник → маршрут (блоками) → 201 → статус + сброс.
  it("создаёт заявку через API с блоками и сбрасывает форму", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0001",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/Заявка REQ-0001 создана/),
    );
    expect(createRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        enterprise: "ENT_PRIMER_1",
        tab_num: "Т-000201",
        fio: "Громов Игорь Олегович",
        blocks: [{ mode: "sequential", steps: [{ sam: "petrov.pp" }] }],
      }),
    );
    // Поле steps (группы) больше не отправляется; blocks уходят в custom.
    const body = vi.mocked(createRequest).mock.calls[0][0];
    expect(body.steps).toBeUndefined();
    expect(body.route_mode).toBe("custom");
    // Сброс формы: предприятие пусто → маршрут скрыт, «Создать» недоступен.
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toHaveValue(""));
    expect(screen.queryByText("Маршрут согласования")).not.toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeDisabled();
  });

  // Живой поиск: ввод → кандидаты 1С в выпадающем списке, клик заполняет данные.
  it("живой поиск: список кандидатов 1С, клик заполняет справочные поля и открывает маршрут", async () => {
    vi.mocked(searchEmployees).mockResolvedValue({
      items: [
        {
          key: "ENT_PRIMER_1|zup_t1|Т-000201",
          tab_num: "Т-000201",
          fio: "Громов Игорь Олегович",
          dept: "Цех № 1",
          position: "Слесарь",
          needs_manual_review: false,
        },
      ],
    });
    // Подразделение/должность — из карточки (в списке справочника их нет).
    vi.mocked(getEmployeeCard).mockResolvedValue({
      key: "ENT_PRIMER_1|zup_t1|Т-000201",
      enterprise: "ENT_PRIMER_1",
      base_code: "zup_t1",
      tab_num: "Т-000201",
      truth_source: "1c",
      link: { linked: false },
      divergences: [],
      needs_manual_review: false,
      fio: "Громов Игорь Олегович",
      dept: "Цех № 1",
      position: "Слесарь",
    });

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });

    await waitFor(() => expect(screen.getByText("Громов Игорь Олегович")).toBeInTheDocument());
    // Клик по кандидату заполняет поля (карточка догружает подразделение/должность)
    // → появляется маршрут (без «Далее»).
    fireEvent.click(screen.getByText("Громов Игорь Олегович"));
    await waitFor(() => expect(getEmployeeCard).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Маршрут согласования")).toBeInTheDocument());
    // Данные справочные: поля не редактируемые.
    expect(screen.queryByRole("textbox", { name: "ФИО" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Табельный №" })).not.toBeInTheDocument();
  });

  // Выбранный сотрудник — текст-ссылка на карточку (окно-попап), рядом крестик
  // очистки в ту же строку; крестик возвращает поиск.
  it("выбранный сотрудник — ссылка на карточку, крестик возвращает поиск", async () => {
    vi.mocked(searchEmployees).mockResolvedValue({
      items: [
        {
          key: "ENT_PRIMER_1|zup_t1|Т-000201",
          tab_num: "Т-000201",
          fio: "Громов Игорь Олегович",
          dept: "Цех № 1",
          position: "Слесарь",
          needs_manual_review: false,
        },
      ],
    });
    vi.mocked(getEmployeeCard).mockResolvedValue({
      key: "ENT_PRIMER_1|zup_t1|Т-000201",
      enterprise: "ENT_PRIMER_1",
      base_code: "zup_t1",
      tab_num: "Т-000201",
      truth_source: "1c",
      link: { linked: false },
      divergences: [],
      needs_manual_review: false,
      fio: "Громов Игорь Олегович",
      dept: "Цех № 1",
      position: "Слесарь",
    });

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });
    await waitFor(() => expect(screen.getByText("Громов Игорь Олегович")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Громов Игорь Олегович"));

    // Вместо поля поиска — ссылка на карточку сотрудника.
    const link = await screen.findByRole("link", { name: "Громов Игорь Олегович" });
    expect(link.getAttribute("href")).toBe(
      `?view=employee&key=${encodeURIComponent("ENT_PRIMER_1|zup_t1|Т-000201")}`,
    );
    expect(screen.queryByLabelText("Поиск сотрудника")).not.toBeInTheDocument();
    // Крестик очистки — в той же строке, что ссылка.
    const clear = screen.getByLabelText("Очистить выбор сотрудника");
    expect(clear.closest(".sed-fieldrow")).toBe(link.closest(".sed-fieldrow"));
    // Очистка возвращает поле поиска.
    fireEvent.click(clear);
    await waitFor(() => expect(screen.getByLabelText("Поиск сотрудника")).toBeInTheDocument());
  });

  // Кнопка «Закрыть» (окно): в том же тулбаре, что «Создать» — одна строка.
  it("onClose: «Закрыть» в одной строке с «Создать»", async () => {
    render(<CreateForm role="hr" onClose={() => undefined} />);
    await waitFor(() => expect(screen.getByText("Создать")).toBeInTheDocument());
    const close = screen.getByRole("button", { name: "Закрыть" });
    const toolbar = close.closest(".sed-toolbar");
    expect(toolbar).not.toBeNull();
    expect(toolbar?.textContent).toContain("Создать");
    expect(toolbar?.textContent).toContain("Отмена");
  });

  // Без onClose кнопки «Закрыть» в форме нет (старое поведение для тестов).
  it("без onClose кнопки «Закрыть» нет", async () => {
    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByText("Создать")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Закрыть" })).not.toBeInTheDocument();
  });

  // Пагинация живого поиска: больше одной страницы → пейджер, навигация по total.
  it("живой поиск: пагинация результатов (Первая/Последняя и номера страниц)", async () => {
    const hit = (tab: string, fio: string) => ({
      key: `ENT_PRIMER_1|zup_t1|${tab}`,
      tab_num: tab,
      fio,
      dept: "Цех № 1",
      position: "Слесарь",
      needs_manual_review: false,
    });
    // Ответ зависит от запрошенной страницы (как серверная пагинация).
    const byPage: Record<number, { tab: string; fio: string }> = {
      1: { tab: "Т-000201", fio: "Громов Игорь Олегович" },
      2: { tab: "Т-000202", fio: "Громова Анна Петровна" },
      3: { tab: "Т-000203", fio: "Громовой Вера Ивановна" },
    };
    vi.mocked(searchEmployees).mockImplementation(async (_ent, _q, page) => {
      const d = byPage[page ?? 1] ?? byPage[1];
      return { items: [hit(d.tab, d.fio)], total: 120, page: page ?? 1, page_size: 50 };
    });

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });

    // Первая страница: запрос page=1, размер 50, пейджер «стр 1 из 3», все номера.
    await waitFor(() => expect(screen.getByText("Громов Игорь Олегович")).toBeInTheDocument());
    expect(searchEmployees).toHaveBeenLastCalledWith("ENT_PRIMER_1", "Громов", 1, 50);
    expect(screen.getByText("стр 1 из 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Страница 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Страница 3" })).toBeInTheDocument();
    // На первой странице «Первая» недоступна.
    expect(screen.getByRole("button", { name: "Первая страница" })).toBeDisabled();

    // Клик по номеру 3 → страница 3 («Последняя» становится недоступной).
    fireEvent.click(screen.getByRole("button", { name: "Страница 3" }));
    await waitFor(() => expect(screen.getByText("Громовой Вера Ивановна")).toBeInTheDocument());
    expect(searchEmployees).toHaveBeenLastCalledWith("ENT_PRIMER_1", "Громов", 3, 50);
    expect(screen.getByText("стр 3 из 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Последняя страница" })).toBeDisabled();

    // «Первая» → возврат на первую страницу.
    fireEvent.click(screen.getByRole("button", { name: "Первая страница" }));
    await waitFor(() => expect(searchEmployees).toHaveBeenLastCalledWith("ENT_PRIMER_1", "Громов", 1, 50));
    expect(screen.getByText("стр 1 из 3")).toBeInTheDocument();
  });

  // Окно страниц пейджера при большом числе страниц: 1, пять вокруг текущей,
  // пять с конца и разрывы «…».
  it("живой поиск: пейджер с большим числом страниц (окно + «…», Первая/Последняя)", async () => {
    const hit = (tab: string, fio: string) => ({
      key: `ENT_PRIMER_1|zup_t1|${tab}`,
      tab_num: tab,
      fio,
      dept: "Цех № 1",
      position: "Слесарь",
      needs_manual_review: false,
    });
    // 1200 совпадений / 50 на страницу = 24 страницы; эмпирически на 1-й.
    vi.mocked(searchEmployees).mockResolvedValue({
      items: [hit("Т-000201", "Громов Игорь Олегович")],
      total: 1200,
      page: 1,
      page_size: 50,
    });

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });

    await waitFor(() => expect(screen.getByText("стр 1 из 24")).toBeInTheDocument());
    // Окно: всегда 1 + пять вокруг текущей (1..3) + пять с конца (20..24); разрывы «…».
    for (const page of [1, 2, 3, 20, 21, 22, 23, 24]) {
      expect(screen.getByRole("button", { name: `Страница ${page}` })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Страница 1" })).toBeDisabled(); // текущая
    expect(screen.getAllByText("…").length).toBeGreaterThan(0); // разрывы
    expect(screen.getByRole("button", { name: "Первая страница" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Последняя страница" })).not.toBeDisabled();
  });

  // Данные сотрудника из 1С — справочные (не input), подразделение/должность — текст.
  it("данные сотрудника из 1С показываются как текст, не input", async () => {
    vi.mocked(searchEmployees).mockResolvedValue({
      items: [
        {
          key: "ENT_PRIMER_1|zup_t1|Т-000201",
          tab_num: "Т-000201",
          fio: "Громов Игорь Олегович",
          dept: "Цех № 1",
          position: "Слесарь",
          needs_manual_review: false,
        },
      ],
    });
    vi.mocked(getEmployeeCard).mockResolvedValue({
      key: "ENT_PRIMER_1|zup_t1|Т-000201",
      enterprise: "ENT_PRIMER_1",
      base_code: "zup_t1",
      tab_num: "Т-000201",
      truth_source: "1c",
      link: { linked: false },
      divergences: [],
      needs_manual_review: false,
      fio: "Громов Игорь Олегович",
      dept: "Цех № 1",
      position: "Слесарь",
    });

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });
    await waitFor(() => expect(screen.getByText("Громов Игорь Олегович")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Громов Игорь Олегович"));
    await waitFor(() => expect(getEmployeeCard).toHaveBeenCalled());
    // Подразделение/должность — текст из карточки, не input (название службы
    // может совпадать с подразделением — берём все вхождения).
    await waitFor(() => expect(screen.getAllByText("Цех № 1").length).toBeGreaterThan(0));
    expect(screen.queryByRole("textbox", { name: "Подразделение" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Должность" })).not.toBeInTheDocument();
    expect(screen.getByText("Подразделение:")).toBeInTheDocument();
    expect(screen.getByText("Должность:")).toBeInTheDocument();
  });

  // Ручной режим (503) — редактируемые поля с пометкой.
  it("при 503 — ручной ввод полями", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });

    await waitFor(() => expect(screen.getByLabelText("Табельный №")).toBeInTheDocument());
    expect(screen.getByText(/введите вручную/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "ФИО" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Подразделение" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Должность" })).toBeInTheDocument();
  });

  // Ошибка 422 при создании — alert с текстом от API.
  it("при 422 показывает понятный alert", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockRejectedValue(
      new ApiHttpError(422, "Шаблон не найден: задайте ручной маршрут (blocks)"),
    );

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/задайте ручной маршрут/),
    );
  });

  // Конструктор маршрута: блоки, исполнители AD, режим параллельный, удаление шага.
  it("конструктор: блоки, исполнители из AD, режим параллельный, удаление шага", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    // Пока нет блоков/исполнителей — «Создать» недоступен.
    expect(screen.getByText("Создать")).toBeDisabled();

    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    expect(screen.getByText("Блок 1")).toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeDisabled();

    // Модалка AD: живой поиск → чекбокс → ОК добавляет исполнителя.
    fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Петров" } });
    await waitFor(() => expect(searchAd).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Петров Пётр Петрович")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" }));
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText(/petrov\.pp/)).toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeEnabled();

    // Режим блока — параллельный.
    fireEvent.change(screen.getByLabelText("Режим блока 1"), { target: { value: "parallel" } });
    expect(screen.getByLabelText("Режим блока 1")).toHaveValue("parallel");

    // Удаление шага → маршрут снова неполный, «Создать» недоступен.
    fireEvent.click(screen.getByLabelText("Удалить исполнителя Петров Пётр Петрович"));
    await waitFor(() => expect(screen.queryByText(/petrov\.pp/)).not.toBeInTheDocument());
    expect(screen.getByText("Создать")).toBeDisabled();
  });

  // Модалка AD: несколько чекбоксов → нижняя панель в порядке кликов → ОК
  // добавляет всех в том же порядке.
  it("модалка: выбор нескольких и ОК в порядке кликов", async () => {
    const second = {
      sam: "sidorova.as",
      display_name: "Сидорова Анна Сергеевна",
      department: "Бухгалтерия",
      title: "Главный бухгалтер",
      mail: "sidorova.as@example.test",
    };
    vi.mocked(searchAd).mockResolvedValue([adCandidate, second]);
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "о" } });
    await waitFor(() => expect(screen.getByText("Сидорова Анна Сергеевна")).toBeInTheDocument());
    // Порядок кликов: сначала Сидорова, потом Петров.
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Сидорова Анна Сергеевна" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" }));
    // Нижняя панель модалки — в порядке кликов.
    const panel = screen.getByLabelText("Выбранные исполнители");
    const picked = within(panel).getAllByRole("listitem").map((li) => li.textContent);
    expect(picked[0]).toContain("Сидорова Анна Сергеевна");
    expect(picked[1]).toContain("Петров Пётр Петрович");
    fireEvent.click(screen.getByRole("button", { name: "ОК" }));
    // В блок встали в том же порядке.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const items = screen.getAllByText(/petrov\.pp|sidorova\.as/);
    expect(items[0].textContent).toContain("sidorova.as");
    expect(items[1].textContent).toContain("petrov.pp");
  });

  // Модалка AD: Отмена закрывает без добавления.
  it("модалка: Отмена закрывает без добавления", async () => {
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Петров" } });
    await waitFor(() => expect(screen.getByText("Петров Пётр Петрович")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" }));
    // «Отмена» модалки (внизу формы своя кнопка с тем же именем — берём в диалоге).
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByText(/petrov\.pp/)).not.toBeInTheDocument();
  });

  // Модалка AD: Escape закрывает без добавления.
  it("модалка: Escape закрывает без добавления", async () => {
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByText(/petrov\.pp/)).not.toBeInTheDocument();
  });

  // Модалка AD: крестик в нижней панели убирает, Очистить — всех; ОК без
  // выбора недоступна.
  it("модалка: крестик и Очистить управляют выбором", async () => {
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "ОК" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Петров" } });
    await waitFor(() => expect(screen.getByText("Петров Пётр Петрович")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" }));
    expect(screen.getByRole("button", { name: "ОК" })).toBeEnabled();
    // Крестик в нижней панели убирает одного.
    fireEvent.click(screen.getByRole("button", { name: "Убрать Петров Пётр Петрович" }));
    expect(screen.getByRole("button", { name: "ОК" })).toBeDisabled();
    // Повторный выбор + Очистить — всех.
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" }));
    fireEvent.click(screen.getByRole("button", { name: "Очистить" }));
    expect(screen.getByRole("button", { name: "ОК" })).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: "Выбрать Петров Пётр Петрович" })).not.toBeChecked();
  });

  // Без предприятия/сотрудника/маршрута «Создать» недоступен.
  it("кнопка «Создать» недоступна без полных данных", () => {
    render(<CreateForm role="hr" />);
    expect(screen.getByText("Создать")).toBeDisabled();
    // Блоки сотрудника и маршрута не видны, пока не выбрано предприятие.
    expect(screen.queryByLabelText("Поиск сотрудника")).not.toBeInTheDocument();
    expect(screen.queryByText("Маршрут согласования")).not.toBeInTheDocument();
  });

  // Шапка формы макета: этап/статус; правая панель: инициатор из сессии, read-only.
  it("показывает этап/статус и инициатора из сессии (только чтение)", async () => {
    render(<CreateForm role="hr" />);
    expect(screen.getByText("Этап: создание заявки · Статус: черновик")).toBeInTheDocument();
    const initiator = await screen.findByLabelText("Инициатор");
    await waitFor(() => expect(initiator).toHaveValue("Петров Пётр Петрович"));
    expect(initiator).toHaveAttribute("readonly");
  });

  // Инициатор с однозначной связкой АД-1С: всё ФИО — ссылка на свою карточку.
  it("инициатор со связкой — ссылка на свою карточку сотрудника", async () => {
    vi.mocked(getMyLinks).mockResolvedValue([
      {
        enterprise: "ENT_PRIMER_1",
        base_code: "zup_t1",
        tab_num: "Т-000201",
        key: "ENT_PRIMER_1|zup_t1|Т-000201",
        verified: true,
        is_current: true,
      },
    ]);

    render(<CreateForm role="hr" />);
    const link = await screen.findByRole("link", { name: "Петров Пётр Петрович" });
    expect(link.getAttribute("href")).toBe(
      `?view=employee&key=${encodeURIComponent("ENT_PRIMER_1|zup_t1|Т-000201")}`,
    );
    expect(screen.queryByLabelText("Инициатор")).not.toBeInTheDocument();
  });

  // Инициатор с неоднозначной связкой (две карточки): ссылка не показывается.
  it("инициатор с двумя связками — текст без ссылки", async () => {
    vi.mocked(getMyLinks).mockResolvedValue([
      {
        enterprise: "ENT_PRIMER_1",
        base_code: "zup_t1",
        tab_num: "Т-000201",
        key: "ENT_PRIMER_1|zup_t1|Т-000201",
        verified: true,
        is_current: null,
      },
      {
        enterprise: "ENT_PRIMER_1",
        base_code: "zup_t2",
        tab_num: "Т-000202",
        key: "ENT_PRIMER_1|zup_t2|Т-000202",
        verified: false,
        is_current: null,
      },
    ]);

    render(<CreateForm role="hr" />);
    const initiator = await screen.findByLabelText("Инициатор");
    await waitFor(() => expect(initiator).toHaveValue("Петров Пётр Петрович"));
  });

  // Инициатор с двумя связками, одна — текущее место работы: ссылка сразу,
  // без выбора предприятия (правило задачи K).
  it("инициатор с текущей связкой — ссылка без выбора предприятия", async () => {
    vi.mocked(getMyLinks).mockResolvedValue([
      {
        enterprise: "ENT_PRIMER_1",
        base_code: "zup_t1",
        tab_num: "Т-000201",
        key: "ENT_PRIMER_1|zup_t1|Т-000201",
        verified: true,
        is_current: true,
      },
      {
        enterprise: "ENT_OTHER",
        base_code: "zup",
        tab_num: "00ЗП-02642",
        key: "ENT_OTHER|zup|00ЗП-02642",
        verified: true,
        is_current: false,
      },
    ]);

    render(<CreateForm role="hr" />);
    const link = await screen.findByRole("link", { name: "Петров Пётр Петрович" });
    expect(link.getAttribute("href")).toBe(
      `?view=employee&key=${encodeURIComponent("ENT_PRIMER_1|zup_t1|Т-000201")}`,
    );
  });

  // Инициатор с двумя связками: после выбора предприятия ссылка ведёт
  // на карточку в этом предприятии (молчаливого выбора нет — решает форма).
  it("инициатор с двумя связками — ссылка после выбора предприятия", async () => {
    vi.mocked(getMyLinks).mockResolvedValue([
      {
        enterprise: "ENT_PRIMER_1",
        base_code: "zup_t1",
        tab_num: "Т-000201",
        key: "ENT_PRIMER_1|zup_t1|Т-000201",
        verified: true,
        is_current: false,
      },
      {
        enterprise: "ENT_OTHER",
        base_code: "zup",
        tab_num: "00ЗП-02642",
        key: "ENT_OTHER|zup|00ЗП-02642",
        verified: true,
        is_current: null,
      },
    ]);

    render(<CreateForm role="hr" />);
    // Без предприятия — текст без ссылки.
    const initiator = await screen.findByLabelText("Инициатор");
    await waitFor(() => expect(initiator).toHaveValue("Петров Пётр Петрович"));
    // Выбираем предприятие первой связки — ФИО становится ссылкой на неё.
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    const link = await screen.findByRole("link", { name: "Петров Пётр Петрович" });
    expect(link.getAttribute("href")).toBe(
      `?view=employee&key=${encodeURIComponent("ENT_PRIMER_1|zup_t1|Т-000201")}`,
    );
  });

  // Автоматический маршрут (по умолчанию): предпросмотр рисует профиль,
  // службу и этапы; конструктор блоков скрыт, blocks в тело не уходят.
  it("режим «по профилю»: предпросмотр показывает профиль, службу и этапы", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0100",
      status: "Черновик",
      route_origin: "template",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");

    // Режим по умолчанию — auto, ручной конструктор скрыт.
    expect(screen.getByRole("radio", { name: "По профилю (рекомендуется)" })).toBeChecked();
    expect(screen.queryByText("Добавить последовательный блок")).not.toBeInTheDocument();

    // Предпросмотр вызван по предприятию и табельному номеру сотрудника.
    await waitFor(() =>
      expect(previewRoute).toHaveBeenCalledWith(
        expect.objectContaining({ enterprise: "ENT_PRIMER_1", tab_num: "Т-000201" }),
      ),
    );
    expect(screen.getByText("Увольнение (базовый профиль)")).toBeInTheDocument();
    expect(screen.getByText(/Служба:/)).toHaveTextContent("Цех № 1");
    expect(screen.getByText(/Причина подбора:/)).toHaveTextContent("профиль службы");
    // Этапы предпросмотра — с исполнителями.
    expect(screen.getByText("Непосредственный руководитель")).toBeInTheDocument();
    expect(screen.getByText("Иванов Иван Иванович")).toBeInTheDocument();
    expect(screen.getByText("Бухгалтерия")).toBeInTheDocument();

    // В auto payload без blocks, с route_mode=auto.
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(createRequest).toHaveBeenCalled());
    const body = vi.mocked(createRequest).mock.calls[0][0];
    expect(body.route_mode).toBe("auto");
    expect(body.blocks).toBeUndefined();
    expect(body.dismissed_stages).toEqual([]);
    expect(body.added_stages).toEqual([]);
  });

  // Руководитель не найден в AD: блок показывает причину и предлагает подобрать
  // замену вручную; выбранный логин уходит и в предпросмотр, и в создание.
  it("при отсутствии руководителя в AD предлагает подобрать замену вручную", async () => {
    const boss = {
      sam: "petrov.pp",
      display_name: "Петров Пётр Петрович",
      department: "Цех № 1",
      title: "Начальник цеха",
      mail: "petrov.pp@example.test",
    };
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([boss]);
    vi.mocked(previewRoute).mockImplementation(async (body) => ({
      ...routePreview,
      stages: routePreview.stages.map((s) =>
        s.owner_kind === "manager_ad"
          ? { ...s, owner_name: body.manager === boss.sam ? boss.display_name : null,
              blocked_reason: body.manager === boss.sam ? null : "Руководитель в AD не определён" }
          : s,
      ),
    }));
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0201",
      status: "Черновик",
      route_origin: "template",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");

    // Причина блокировки этапа видна в таблице этапов.
    expect(screen.getByText("Руководитель в AD не определён")).toBeInTheDocument();
    // Блок «Руководитель»: этап заблокирован — подбор замены открыт сразу.
    expect(screen.getByRole("checkbox", { name: "Выбрать руководителя вручную" })).toBeDisabled();
    expect(screen.getByText(/не определён в AD/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("ФИО руководителя"), { target: { value: "Петров" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти руководителя" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Выбрать руководителя Петров Пётр Петрович" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Выбрать руководителя Петров Пётр Петрович" }));

    // Замена ушла в предпросмотр и показывается как выбранная.
    await waitFor(() =>
      expect(previewRoute).toHaveBeenLastCalledWith(
        expect.objectContaining({ manager: "petrov.pp" }),
      ),
    );
    expect(screen.getByText(/Петров Пётр Петрович \(выбран вручную\)/)).toBeInTheDocument();

    // И в тело создания.
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(createRequest).toHaveBeenCalled());
    expect(vi.mocked(createRequest).mock.calls[0][0].manager).toBe("petrov.pp");
  });

  // Руководитель найден в AD: ФИО видно в этапе, замена не требуется, но её
  // можно подобрать (чекбокс активен).
  it("найденный руководитель: ФИО в этапе, ручной подбор доступен по желанию", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(previewRoute).mockResolvedValue(routePreview);
    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");
    expect(screen.getByText("Иванов Иван Иванович")).toBeInTheDocument();
    expect(screen.getByText(/Руководитель:/)).toHaveTextContent("найден в AD");
    const box = screen.getByRole("checkbox", { name: "Выбрать руководителя вручную" });
    expect(box).toBeEnabled();
    expect(box).not.toBeChecked();
    expect(screen.queryByLabelText("ФИО руководителя")).not.toBeInTheDocument();
  });

  // Снятие галочки этапа → код в dismissed_stages; обязательный этап (optional=false)
  // снять нельзя.
  it("снятие чекбокса этапа уходит в dismissed_stages, обязательный этап неактивен", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(previewRoute).mockImplementation(async (body) => ({
      ...routePreview,
      stages: routePreview.stages.filter((s) => !body.dismissed_stages?.includes(s.code ?? "")),
    }));
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0101",
      status: "Черновик",
      route_origin: "template",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");

    // Обязательный этап — чекбокс включён и неактивен, с подсказкой.
    const required = screen.getByRole("checkbox", { name: "Этап Непосредственный руководитель" });
    expect(required).toBeChecked();
    expect(required).toBeDisabled();
    expect(required).toHaveAttribute("title", "Этап обязательный");

    // Необязательный этап снимается галочкой → предпросмотр перезапрашивается.
    fireEvent.click(screen.getByRole("checkbox", { name: "Этап Бухгалтерия" }));
    await waitFor(() =>
      expect(previewRoute).toHaveBeenLastCalledWith(
        expect.objectContaining({ dismissed_stages: ["buhgalteriya"] }),
      ),
    );

    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(createRequest).toHaveBeenCalled());
    expect(vi.mocked(createRequest).mock.calls[0][0].dismissed_stages).toEqual(["buhgalteriya"]);
  });

  // Причину блокировки этапа (важно для ОК) показываем рядом с этапом.
  it("blocked_reason этапа показан рядом с этапом", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(previewRoute).mockResolvedValue({
      ...routePreview,
      stages: [
        {
          ...routePreview.stages[0],
          owner_name: null,
          blocked_reason: "Не найден непосредственный руководитель в AD - этап заблокирован",
        },
      ],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");
    await waitFor(() =>
      expect(screen.getByText(/Не найден непосредственный руководитель в AD/)).toBeInTheDocument(),
    );
  });

  // Маршрут не подобрался: причина reason показывается по-русски, этапов нет,
  // «Создать» недоступно — ручной режим остаётся путём.
  it("маршрут не подобрался: причина по-русски, ручной режим доступен", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(previewRoute).mockResolvedValue({
      profile: null,
      service: null,
      reason: "service_not_registered",
      stages: [],
      blank: null,
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");

    await waitFor(() => expect(screen.getByText(/Маршрут не подобрался/)).toBeInTheDocument());
    expect(screen.getByText(/Маршрут не подобрался/)).toHaveTextContent("служба не заведена");
    expect(screen.getByText("Этапы не подобраны.")).toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeDisabled();

    // Переключение вручную открывает конструктор — деградация без ошибки.
    await switchToManualRoute();
    expect(screen.getByText("Добавить последовательный блок")).toBeInTheDocument();
  });

  // 422 предпросмотра: текст ошибки показан, создание в ручном режиме доступно.
  it("422 предпросмотра показан текстом и не блокирует ручной режим", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(previewRoute).mockRejectedValue(
      new ApiHttpError(422, "Не удалось определить службу: не задано подразделение"),
    );

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Не удалось определить службу/),
    );
    expect(screen.getByText("Создать")).toBeDisabled();
    await switchToManualRoute();
    expect(screen.getByText("Добавить последовательный блок")).toBeInTheDocument();
  });

  // 422 при создании в auto: detail показывается как есть, молчаливого
  // переключения на ручной режим нет.
  it("422 при создании в auto показывается как есть, режим не меняется", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(createRequest).mockRejectedValue(
      new ApiHttpError(422, "Не удалось подобрать маршрут: профиль не найден"),
    );

    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Не удалось подобрать маршрут/),
    );
    // Режим остался автоматическим — ручной конструктор не показан.
    expect(screen.getByRole("radio", { name: "По профилю (рекомендуется)" })).toBeChecked();
    expect(screen.queryByText("Добавить последовательный блок")).not.toBeInTheDocument();
  });

  // «Добавить этап»: код уходит в added_stages. Справочник админский — при 403
  // кнопки нет (деградация без ошибки).
  it("добавление этапа уходит в added_stages; при 403 справочника кнопки нет", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0102",
      status: "Черновик",
      route_origin: "template",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    const { unmount } = render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");
    fireEvent.click(screen.getByRole("button", { name: "Добавить этап" }));
    fireEvent.change(screen.getByLabelText("Этап для добавления"), {
      target: { value: "sluzhba_ok" },
    });
    await waitFor(() =>
      expect(previewRoute).toHaveBeenLastCalledWith(
        expect.objectContaining({ added_stages: ["sluzhba_ok"] }),
      ),
    );
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(createRequest).toHaveBeenCalled());
    expect(vi.mocked(createRequest).mock.calls[0][0].added_stages).toEqual(["sluzhba_ok"]);
    unmount();

    // Не-админ: справочник 403 — кнопки добавления нет, ошибки тоже.
    vi.mocked(getRoutingCatalogs).mockRejectedValue(new ApiHttpError(403, "Справочник — только админам"));
    render(<CreateForm role="hr" />);
    await fillEmployeeManually("auto");
    expect(screen.queryByRole("button", { name: "Добавить этап" })).not.toBeInTheDocument();
    expect(screen.getByText("Увольнение (базовый профиль)")).toBeInTheDocument();
  });

  // Конструктор блоков — карточки «Рассмотрение»; ссылки задают режим блока.
  it("ссылки добавляют последовательный и параллельный блоки в таблицу «Рассмотрение»", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    expect(screen.queryByText("Последовательно")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.click(screen.getByText("Добавить параллельный блок"));
    expect(screen.getByText("Последовательно")).toBeInTheDocument();
    expect(screen.getByText("Параллельно")).toBeInTheDocument();
    expect(screen.getByLabelText("Режим блока 1")).toHaveValue("sequential");
    expect(screen.getByLabelText("Режим блока 2")).toHaveValue("parallel");
  });

  // Админ тоже может создавать (роль admin, как в API _is_hr).
  it("админу создание доступно", async () => {
    render(<CreateForm role="admin" />);
    await waitFor(() => expect(screen.getByText("Создание заявки")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // Руководитель ОК тоже может создавать (роль hr_admin, как в API _is_hr).
  it("руководителю ОК создание доступно", async () => {
    render(<CreateForm role="hr_admin" />);
    await waitFor(() => expect(screen.getByText("Создание заявки")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // Не-ОК создание закрыто.
  it("владельцу и гостю создание закрыто", () => {
    render(<CreateForm role="owner" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/только ОК/);
  });

  // onDirtyChange: true после первого ввода, false после успешного создания.
  it("onDirtyChange: true после ввода, false после создания", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0002",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });
    const onDirty = vi.fn();

    render(<CreateForm role="hr" onDirtyChange={onDirty} />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    expect(onDirty).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    expect(onDirty).toHaveBeenCalledWith(true);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(false));
  });

  // В окне-попе (?view=create): после успешного создания окно закрывается.
  it("в окне-попе закрывает окно после создания (closeOnCreate)", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0003",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });
    const close = vi.spyOn(window, "close").mockImplementation(() => {});

    render(<CreateForm role="hr" closeOnCreate />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(close).toHaveBeenCalled());
    close.mockRestore();
  });

  // «Отправить на согласование»: создаёт заявку, сразу отправляет её
  // (submitRequest(result.id)) и в окне-попе закрывает окно.
  it("«Отправить на согласование» создаёт, отправляет и закрывает окно", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0001",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });
    vi.mocked(submitRequest).mockResolvedValue({
      id: "REQ-0001",
      status: "На согласовании",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });
    const close = vi.spyOn(window, "close").mockImplementation(() => {});

    render(<CreateForm role="hr" closeOnCreate />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Отправить на согласование"));

    await waitFor(() => expect(submitRequest).toHaveBeenCalledWith("REQ-0001"));
    expect(createRequest).toHaveBeenCalledWith(
      expect.objectContaining({ enterprise: "ENT_PRIMER_1", fio: "Громов Игорь Олегович" }),
    );
    await waitFor(() => expect(close).toHaveBeenCalled());
    close.mockRestore();
  });

  // «Создать» (черновик): заявка создаётся, на согласование НЕ отправляется.
  it("«Создать» создаёт черновик без отправки на согласование", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0004",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() => expect(createRequest).toHaveBeenCalled());
    expect(submitRequest).not.toHaveBeenCalled();
  });

  // Ошибка submit: текст ошибки виден, окно не закрывается.
  it("при ошибке «Отправить на согласование» окно не закрывается, показан текст", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0005",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });
    vi.mocked(submitRequest).mockRejectedValue(
      new ApiHttpError(422, "Маршрут не согласован"),
    );
    const close = vi.spyOn(window, "close").mockImplementation(() => {});

    render(<CreateForm role="hr" closeOnCreate />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Отправить на согласование"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Маршрут не согласован/),
    );
    expect(close).not.toHaveBeenCalled();
    close.mockRestore();
  });

  // Повтор «Отправить» после сбоя submit: черновик не создаётся заново,
  // повторно отправляется тот же id (защита от дублей Черновиков).
  it("повтор «Отправить» после сбоя не создаёт второй черновик", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0006",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });
    vi.mocked(submitRequest)
      .mockRejectedValueOnce(new ApiHttpError(422, "Маршрут не согласован"))
      .mockResolvedValueOnce({
        id: "REQ-0006",
        status: "На согласовании",
        route_origin: "custom",
        department: "Цех № 1",
        position: "Слесарь",
        created_by: "petrov.pp",
        steps: [],
      });
    const close = vi.spyOn(window, "close").mockImplementation(() => {});

    render(<CreateForm role="hr" closeOnCreate />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Отправить на согласование"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Маршрут не согласован/),
    );

    fireEvent.click(screen.getByText("Отправить на согласование"));
    await waitFor(() => expect(submitRequest).toHaveBeenCalledTimes(2));
    // Оба раза — по одному и тому же черновику; создание было одно.
    expect(vi.mocked(submitRequest).mock.calls).toEqual([["REQ-0006"], ["REQ-0006"]]);
    expect(createRequest).toHaveBeenCalledTimes(1);
    close.mockRestore();
  });

  // Повтор «Отправить» после сбоя с ИЗМЕНЁННЫМИ полями: создаётся новый
  // черновик с новыми данными (правки не теряются молча).
  it("повтор «Отправить» после сбоя с правками создаёт новый черновик", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(searchAd).mockResolvedValue([adCandidate]);
    vi.mocked(createRequest)
      .mockResolvedValueOnce({
        id: "REQ-0006",
        status: "Черновик",
        route_origin: "custom",
        department: "Цех № 1",
        position: "Слесарь",
        created_by: "petrov.pp",
        steps: [],
      })
      .mockResolvedValueOnce({
        id: "REQ-0007",
        status: "Черновик",
        route_origin: "custom",
        department: "Цех № 1",
        position: "Слесарь",
        created_by: "petrov.pp",
        steps: [],
      });
    vi.mocked(submitRequest)
      .mockRejectedValueOnce(new ApiHttpError(422, "Маршрут не согласован"))
      .mockResolvedValueOnce({
        id: "REQ-0007",
        status: "На согласовании",
        route_origin: "custom",
        department: "Цех № 1",
        position: "Слесарь",
        created_by: "petrov.pp",
        steps: [],
      });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    await addAdExecutor();
    fireEvent.click(screen.getByText("Отправить на согласование"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Маршрут не согласован/),
    );

    fireEvent.change(screen.getByLabelText("ФИО"), { target: { value: "Громов Игорь Петрович" } });
    fireEvent.click(screen.getByText("Отправить на согласование"));
    await waitFor(() => expect(createRequest).toHaveBeenCalledTimes(2));
    expect(vi.mocked(submitRequest)).toHaveBeenLastCalledWith("REQ-0007");
  });

  // Тип исполнителя шага — «Группа»: список групп из settings, состав из AD
  // (счётчик + раскрытый список), в теле создания owner_group + by_group.
  it("тип исполнителя «Группа»: список групп, состав и owner_group в теле", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0001",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));

    // Тип исполнителя блока: по умолчанию сотрудник, есть вариант «Группа».
    const kind = screen.getByLabelText("Тип исполнителя блока 1");
    expect(kind).toHaveValue("user");
    fireEvent.change(kind, { target: { value: "group" } });

    // Группы — из /api/step-groups (settings), без хардкода: в селекте
    // наименование (name), значение — id группы.
    await waitFor(() => expect(screen.getByLabelText("Группа блока 1")).toBeInTheDocument());
    const groupOption = screen.getByRole("option", {
      name: "Бухгалтерия (вымышленная группа)",
    }) as HTMLOptionElement;
    expect(groupOption.value).toBe("SED_STEP_BUH");

    // Выбор группы подгружает состав из AD (по id, не по наименованию).
    fireEvent.change(screen.getByLabelText("Группа блока 1"), {
      target: { value: "SED_STEP_BUH" },
    });
    await waitFor(() => expect(vi.mocked(getAdGroupMembers)).toHaveBeenCalledWith("SED_STEP_BUH"));
    await waitFor(() => expect(screen.getByText("Состав группы: 1")).toBeInTheDocument());

    // Состав раскрывается по кнопке: ФИО и почта членов группы.
    expect(screen.queryByText("Сидорова Анна Сергеевна")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать состав" }));
    expect(screen.getByText(/Сидорова Анна Сергеевна/)).toBeInTheDocument();
    expect(screen.getByText(/sidorova\.as@example\.test/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Добавить группу"));
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(vi.mocked(createRequest)).toHaveBeenCalledWith(
        expect.objectContaining({
          blocks: [
            { mode: "sequential", steps: [{ owner_group: "SED_STEP_BUH", resolver: "by_group" }] },
          ],
        }),
      ),
    );
  });

  // Регресс п. 3 ревью: состав групп не общий — у каждого блока своя группа
  // и свой состав (раньше второй блок показывал членов первой группы).
  it("два групповых блока держат свои составы независимо", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(getAdGroupMembers).mockImplementation(async (group: string) =>
      group === "SED_STEP_BUH"
        ? [groupMember]
        : [
            {
              sam: "ivanov.ii",
              display_name: "Иванов Иван Иванович",
              mail: "ivanov.ii@example.test",
              department: "Отдел кадров",
              title: "Начальник",
            },
          ],
    );

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();

    // Блок 1 — группа SED_STEP_BUH.
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.change(screen.getByLabelText("Тип исполнителя блока 1"), { target: { value: "group" } });
    await waitFor(() => expect(screen.getByLabelText("Группа блока 1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Группа блока 1"), { target: { value: "SED_STEP_BUH" } });
    await waitFor(() => expect(vi.mocked(getAdGroupMembers)).toHaveBeenCalledWith("SED_STEP_BUH"));
    fireEvent.click(screen.getByRole("button", { name: "Показать состав" }));
    expect(screen.getByText(/Сидорова Анна Сергеевна/)).toBeInTheDocument();

    // Блок 2 — группа SED_STEP_OK со своим составом.
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    const groupKind2 = screen.getByLabelText("Тип исполнителя блока 2");
    fireEvent.change(groupKind2, { target: { value: "group" } });
    await waitFor(() => expect(screen.getByLabelText("Группа блока 2")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Группа блока 2"), { target: { value: "SED_STEP_OK" } });
    await waitFor(() => expect(vi.mocked(getAdGroupMembers)).toHaveBeenCalledWith("SED_STEP_OK"));

    // Обе кнопки «Показать состав» раскрывают свои списки; ФИО не перепутаны.
    const showButtons = screen.getAllByRole("button", { name: "Показать состав" });
    fireEvent.click(showButtons[showButtons.length - 1]);
    await waitFor(() => expect(screen.getByText(/Иванов Иван Иванович/)).toBeInTheDocument());
    expect(screen.getByText(/Сидорова Анна Сергеевна/)).toBeInTheDocument();
    // Второй блок не показывает члена первой группы.
    expect(screen.getByLabelText("Состав группы SED_STEP_OK")).toHaveTextContent("Иванов Иван Иванович");
    expect(screen.getByLabelText("Состав группы SED_STEP_BUH")).not.toHaveTextContent("Иванов Иван Иванович");
  });

  // Регресс B1: удаление блока не отдаёт его группу и состав следующему. Раньше
  // состояние висело на индексе блока, и у оставшегося подменялись чужие группа
  // и состав (в теле уходил неверный owner_group).
  it("удаление первого блока не подменяет группу и состав второму", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(getAdGroupMembers).mockImplementation(async (group: string) =>
      group === "SED_STEP_BUH" ? [groupMember] : [okGroupMember],
    );
    vi.mocked(createRequest).mockResolvedValue({
      id: "REQ-0006",
      status: "Черновик",
      route_origin: "custom",
      department: "Цех № 1",
      position: "Слесарь",
      created_by: "petrov.pp",
      steps: [],
    });

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();

    // Блок 1 — группа SED_STEP_BUH (состав из AD загрузился).
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.change(screen.getByLabelText("Тип исполнителя блока 1"), { target: { value: "group" } });
    await waitFor(() => expect(screen.getByLabelText("Группа блока 1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Группа блока 1"), { target: { value: "SED_STEP_BUH" } });
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Показать состав" }).length).toBe(1),
    );

    // Блок 2 — группа SED_STEP_OK со своим составом.
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.change(screen.getByLabelText("Тип исполнителя блока 2"), { target: { value: "group" } });
    await waitFor(() => expect(screen.getByLabelText("Группа блока 2")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Группа блока 2"), { target: { value: "SED_STEP_OK" } });
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Показать состав" }).length).toBe(2),
    );
    // Раскрываем состав второго блока — члены SED_STEP_OK, а не первой группы.
    const showButtons = screen.getAllByRole("button", { name: "Показать состав" });
    fireEvent.click(showButtons[showButtons.length - 1]);
    await waitFor(() =>
      expect(screen.getByLabelText("Состав группы SED_STEP_OK")).toHaveTextContent("Иванов Иван Иванович"),
    );
    // Группу второго блока добавляем в маршрут (кнопка под его селектом).
    fireEvent.click(screen.getAllByRole("button", { name: "Добавить группу" })[1]);

    // Удаляем ПЕРВЫЙ блок: у оставшегося своя группа, свой состав и свой шаг.
    fireEvent.click(screen.getAllByRole("button", { name: "Удалить блок" })[0]);
    expect(screen.queryByLabelText("Группа блока 2")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Группа блока 1")).toHaveValue("SED_STEP_OK");
    expect(screen.getByLabelText("Состав группы SED_STEP_OK")).toHaveTextContent("Иванов Иван Иванович");
    expect(screen.getByLabelText("Состав группы SED_STEP_OK")).not.toHaveTextContent("Сидорова");

    // В теле создания owner_group второго блока (не группы удалённого блока).
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() =>
      expect(vi.mocked(createRequest)).toHaveBeenCalledWith(
        expect.objectContaining({
          blocks: [
            { mode: "sequential", steps: [{ owner_group: "SED_STEP_OK", resolver: "by_group" }] },
          ],
        }),
      ),
    );
  });

  // Регресс B1 (гонка): медленный ответ состава по прежней группе не должен
  // перезаписывать состав текущей (счётчик запросов на блок, как adSeq).
  it("устаревший ответ состава не перезатирает текущий", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    // Ответ по SED_STEP_BUH приходит только вручную (после переключения группы).
    let releaseSlow: (members: AdGroupMember[]) => void = () => {};
    const slow = new Promise<AdGroupMember[]>((resolve) => {
      releaseSlow = resolve;
    });
    vi.mocked(getAdGroupMembers).mockImplementation((group: string) =>
      group === "SED_STEP_BUH" ? slow : Promise.resolve([okGroupMember]),
    );

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.change(screen.getByLabelText("Тип исполнителя блока 1"), { target: { value: "group" } });
    await waitFor(() => expect(screen.getByLabelText("Группа блока 1")).toBeInTheDocument());

    // Выбрали группу с медленным ответом, сразу переключились на другую.
    fireEvent.change(screen.getByLabelText("Группа блока 1"), { target: { value: "SED_STEP_BUH" } });
    fireEvent.change(screen.getByLabelText("Группа блока 1"), { target: { value: "SED_STEP_OK" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Показать состав" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Показать состав" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Состав группы SED_STEP_OK")).toHaveTextContent("Иванов Иван Иванович"),
    );

    // Ответ по прежней группе приходит позже — состав текущей не меняется.
    await act(async () => {
      releaseSlow([groupMember]);
    });
    expect(screen.getByLabelText("Состав группы SED_STEP_OK")).toHaveTextContent("Иванов Иван Иванович");
    expect(screen.getByLabelText("Состав группы SED_STEP_OK")).not.toHaveTextContent("Сидорова");
  });

  // Недоступность состава группы — понятный текст, форма не падает.
  it("недоступность состава группы показывает текст и не ломает форму", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(getAdGroupMembers).mockRejectedValue(new ApiHttpError(503, "AD недоступен"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.change(screen.getByLabelText("Тип исполнителя блока 1"), {
      target: { value: "group" },
    });
    await waitFor(() => expect(screen.getByLabelText("Группа блока 1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Группа блока 1"), {
      target: { value: "SED_STEP_OK" },
    });

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("AD недоступен"));
    // Форма жива: можно добавить группу и создать заявку.
    fireEvent.click(screen.getByText("Добавить группу"));
    expect(screen.getByText("Создать")).toBeEnabled();
  });

  // Недоступность списка групп (403 у не-ОК) — текст без падения формы.
  it("ошибка загрузки групп показывает текст", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(getStepGroups).mockRejectedValue(new ApiHttpError(403, "Доступ запрещён"));

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    await waitFor(() => expect(screen.getByText(/Доступ запрещён/)).toBeInTheDocument());
    // Персональный сценарий конструктора продолжает работать.
    fireEvent.click(screen.getByText("Добавить последовательный блок"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить исполнителя" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("Поиск в AD")).toBeInTheDocument();
  });
});

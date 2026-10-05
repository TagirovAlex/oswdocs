// Тесты сетки скелета (Задача 3): вкладки, дерево, тулбар, фильтры, таблица,
// выход; клик по строке и «Создать заявку» — окна-попы (window.open).
// Данные — из requests-client (мокается), сеть не нужна.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SedLayout } from "./layout";
import { ThemeProvider } from "./theme";
import { getEnterprises, getFolders, getRequests } from "./requests-client";
import { toRequestRow } from "./requests-client";
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
// Шаг по умолчанию — групповой, can_act=false (кнопок согласования нет).
// owner_name бэкенд резолвит из assignee, поэтому у группового шага его нет —
// подпись шага строится по названию группы.
function requestWith(
  fio: string | null,
  status: string,
  id: string = "REQ-0001",
  steps?: RequestOut["steps"],
): RequestOut {
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
    steps: steps ?? [
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
}

// Персональный шаг, который может отметить текущий пользователь (can_act).
const myStep: RequestOut["steps"] = [
  {
    order: 1,
    owner_group: "petrov.pp",
    resolver: "by_user",
    owner_name: "Сидорова Анна Сергеевна",
    can_act: true,
    status: "ожидает",
    expires_at: "2026-10-05T10:00:00+00:00",
  },
];

// Ключ карточки сотрудника, который бэкенд резолвит по локальному справочнику
// (только привилегированным; в нём табельный номер — ПДн).
const EMP_KEY = "ENT_PRIMER_1|zup|Т-000201";

// Идентификаторы строк таблицы в порядке отображения (первая ячейка — «№»).
function visibleIds(): (string | null)[] {
  const table = screen.getByLabelText("Заявки");
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent ?? null);
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
    // Вкладки (ОК: «Справочник» есть, «Настроек» нет, «Создание» убрана).
    expect(screen.getByText("Заявки")).toBeInTheDocument();
    expect(screen.getByText("Справочник")).toBeInTheDocument();
    expect(screen.queryByText("Создание")).not.toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
    // Тулбар: создание в отдельном окне (иконочная кнопка), печать — в карточке окна.
    expect(screen.getByRole("button", { name: "Создать заявку" })).toBeInTheDocument();
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

  // Папка «Черновики» (id=draft из GET /api/folders) отображается в дереве;
  // активная по умолчанию остаётся «agreement», переход фильтрует черновики.
  it("папка «Черновики» из API отображается и фильтрует черновики", async () => {
    vi.mocked(getFolders).mockResolvedValue([
      { id: "agreement", title: "На согласовании", count: 1 },
      { id: "draft", title: "Черновики", count: 1 },
    ]);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "Черновик")]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("Черновики")).toBeInTheDocument());
    // Черновик не виден в «На согласовании», но виден после перехода в «Черновики».
    expect(screen.queryByText("REQ-0001")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Черновики"));
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
  });

  // Папка «К исполнению» (id=execution из GET /api/folders): статусы
  // «Согласовано» и «К исполнению» видны только в ней.
  it("папка «К исполнению» из API отображается и фильтрует согласованные", async () => {
    vi.mocked(getFolders).mockResolvedValue([
      { id: "agreement", title: "На согласовании", count: 0 },
      { id: "execution", title: "К исполнению", count: 2 },
    ]);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("Громов Игорь Олегович", "Согласовано", "REQ-0001"),
      requestWith("Громов Игорь Олегович", "К исполнению", "REQ-0002"),
    ]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("К исполнению")).toBeInTheDocument());
    // В «На согласовании» их нет, после перехода в «К исполнению» — обе.
    expect(screen.queryByText("REQ-0001")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("К исполнению"));
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(screen.getByText("REQ-0002")).toBeInTheDocument();
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

  // Вкладка «Настройки» — админу; «Создание» в меню больше нет.
  it("вкладка «Настройки» видна админу, «Создание» убрана", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("admin");
    expect(screen.getByText("Настройки")).toBeInTheDocument();
    expect(screen.queryByText("Создание")).not.toBeInTheDocument();
  });

  // «Создание» отсутствует в меню у всех ролей (создание — кнопка «Создать заявку»).
  it("«Создание» скрыта у владельца", () => {
    vi.mocked(getFolders).mockResolvedValue([{ id: "mine", title: "Мои задачи", count: 0 }]);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("owner");
    expect(screen.queryByText("Создание")).not.toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
  });

  // Руководитель ОК видит «Справочник» и «Настройки» (контент-настройки, Фаза 2).
  it("руководитель ОК видит «Справочник» и «Настройки»", () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([]);
    renderWithTheme("hr_admin");
    expect(screen.getByText("Справочник")).toBeInTheDocument();
    expect(screen.getByText("Настройки")).toBeInTheDocument();
    expect(screen.queryByText("Создание")).not.toBeInTheDocument();
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
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Создать заявку" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));
    expect(open).toHaveBeenCalledWith("?view=create", "_blank", expect.stringContaining("popup"));
    open.mockRestore();
  });

  // Возврат фокуса в основное окно (закрыт попап) — папки и список
  // перезагружаются (счётчики и новые заявки актуальны без жёсткой перезагрузки).
  it("после возврата фокуса папки и список перезагружаются", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    const foldersCalls = vi.mocked(getFolders).mock.calls.length;
    const requestsCalls = vi.mocked(getRequests).mock.calls.length;

    fireEvent(window, new Event("focus"));

    await waitFor(() =>
      expect(vi.mocked(getFolders).mock.calls.length).toBeGreaterThan(foldersCalls),
    );
    expect(vi.mocked(getRequests).mock.calls.length).toBeGreaterThan(requestsCalls);
  });

  // ФИО в таблице — ссылка на карточку сотрудника (employee_key от бэкенда):
  // окно сотрудника открывается по клику, карточка заявки при этом НЕ открывается
  // (событие гасится), иначе клик по ФИО открывал бы оба окна.
  it("ФИО в таблице — ссылка на карточку сотрудника, карточка заявки не открывается", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([
      { ...requestWith("Громов Игорь Олегович", "На согласовании"), employee_key: EMP_KEY },
    ]);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    const link = screen.getByRole("link", { name: "Громов Игорь Олегович" });
    expect(link).toHaveAttribute("href", `?view=employee&key=${encodeURIComponent(EMP_KEY)}`);
    fireEvent.click(link);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(
      `?view=employee&key=${encodeURIComponent(EMP_KEY)}`,
      "_blank",
      expect.stringContaining("popup"),
    );
    open.mockRestore();
  });

  // Без employee_key (неоднозначное совпадение в справочнике, не привилегированный)
  // ФИО остаётся текстом — клик по строке открывает карточку заявки, как раньше.
  it("без employee_key ФИО — текст, клик по строке открывает карточку заявки", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Громов Игорь Олегович" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Громов Игорь Олегович"));
    expect(open).toHaveBeenCalledWith("?view=request&id=REQ-0001", "_blank", expect.stringContaining("popup"));
    open.mockRestore();
  });

  // employee_key бэкенда — источник ключа строки (fallback на enterprise|base_code|
  // tab_num остаётся для старых ответов без employee_key).
  it("toRequestRow берёт ключ из employee_key, иначе собирает из полей заявки", () => {
    const withKey = toRequestRow({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      employee_key: EMP_KEY,
    });
    expect(withKey.employeeKey).toBe(EMP_KEY);
    const legacy = toRequestRow({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      base_code: "zup",
    });
    expect(legacy.employeeKey).toBe("ENT_PRIMER_1|zup|Т-000201");
    // Без ключа и без base_code — ссылки нет.
    expect(toRequestRow(requestWith("Громов Игорь Олегович", "На согласовании")).employeeKey).toBe("");
  });

  // Колонка «Текущий согласующий» — только привилегированным: ФИО исполнителя
  // текущего шага (у сотрудника колонки нет). Персональный шаг (by_user) несёт
  // логин в owner_group — в списке он не должен появляться.
  it("колонка «Текущий согласующий» показывает ФИО персонального шага", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001", myStep),
    ]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("Текущий согласующий")).toBeInTheDocument());
    // ФИО выводится в двух колонках — «Текущий согласующий» и «Шаг».
    expect(screen.getAllByText("Сидорова Анна Сергеевна").length).toBeGreaterThan(0);
    // Логин персонального исполнителя не показывается ни в одной колонке.
    expect(screen.queryByText("petrov.pp")).not.toBeInTheDocument();
  });

  // Регресс п. 3.2: у персонального шага owner_group = sAMAccountName, поэтому
  // подпись шага строится по ФИО (owner_name), а без неё — нейтральный текст,
  // но не owner_group.
  it("персональный шаг: в списке ФИО, а не логин (can_act и без него)", () => {
    const withoutCanAct = myStep.map((s) => ({ ...s, can_act: false }));
    const rows = [toRequestRow(requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001", myStep))];
    const rowsOther = [
      toRequestRow(requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001", withoutCanAct)),
    ];
    expect(rows[0].ownerName).toBe("Сидорова Анна Сергеевна");
    expect(rows[0].step).toBe("Сидорова Анна Сергеевна");
    expect(rowsOther[0].ownerName).toBe("Сидорова Анна Сергеевна");
    expect(rowsOther[0].step).toBe("Сидорова Анна Сергеевна");
  });

  // Персональный шаг без ФИО (AD не отдал displayName) — нейтральный текст, не логин.
  it("персональный шаг без owner_name: нейтральный текст, не логин", () => {
    const noName = myStep.map((s) => ({ ...s, owner_name: null }));
    const row = toRequestRow(requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001", noName));
    expect(row.ownerName).toBe("Персональный исполнитель");
    expect(row.step).toBe("Персональный исполнитель");
  });

  // Групповой шаг (ФИО нет) — название группы, а не логин согласующего.
  it("групповой шаг в колонке согласующего — название группы", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001"),
    ]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("Текущий согласующий")).toBeInTheDocument());
    // Название группы — в колонках «Шаг» и «Текущий согласующий».
    expect(screen.getAllByText("SED_STEP_BUH").length).toBeGreaterThan(0);
    expect(screen.queryByText(/petrov\.pp/)).not.toBeInTheDocument();
  });

  // У сотрудника (роль owner) колонки текущего согласующего нет.
  it("у сотрудника колонки «Текущий согласующий» нет", async () => {
    vi.mocked(getFolders).mockResolvedValue([{ id: "mine", title: "Мои задачи", count: 1 }]);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith(null, "На согласовании", "REQ-0001", myStep),
    ]);

    renderWithTheme("owner");
    await waitFor(() => expect(screen.getByText("Сотрудник № REQ-0001")).toBeInTheDocument());
    expect(screen.queryByText("Текущий согласующий")).not.toBeInTheDocument();
  });

  // Сортировка по умолчанию — новые сверху (id REQ-XXXX по убыванию);
  // клик по заголовку «№» переключает направление.
  it("по умолчанию новые сверху, клик по «№» переключает направление", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001"),
      requestWith("Сидорова Анна Сергеевна", "На согласовании", "REQ-0002"),
    ]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    expect(visibleIds()).toEqual(["REQ-0002", "REQ-0001"]);

    // Регресс п. 4 ревью: смена сортировки — чистая перерисовка, без нового запроса.
    const requestsCalls = vi.mocked(getRequests).mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Сортировать по «№»" }));
    await waitFor(() => expect(visibleIds()).toEqual(["REQ-0001", "REQ-0002"]));
    expect(vi.mocked(getRequests).mock.calls.length).toBe(requestsCalls);
  });

  // Сортировка по «Сроку» — по сроку текущего шага (не по id).
  it("сортировка по «Срок» — по сроку шага", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([
      requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001"),
      requestWith("Сидорова Анна Сергеевна", "На согласовании", "REQ-0002", [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "by_group",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-09T10:00:00+00:00",
        },
      ]),
    ]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Сортировать по «Срок»" }));
    // REQ-0001 — срок 2026-10-05, REQ-0002 — 2026-10-09 → возрастание по сроку.
    await waitFor(() => expect(visibleIds()).toEqual(["REQ-0001", "REQ-0002"]));
  });

  // Оповещение из окна-попа об удалении/создании заявки (localStorage storage):
  // список и счётчики папок обновляются без возврата фокуса.
  it("оповещение localStorage обновляет список и папки", async () => {
    vi.mocked(getFolders).mockResolvedValue(folders);
    vi.mocked(getRequests).mockResolvedValue([requestWith("Громов Игорь Олегович", "На согласовании")]);

    renderWithTheme("hr");
    await waitFor(() => expect(screen.getByText("REQ-0001")).toBeInTheDocument());
    const foldersCalls = vi.mocked(getFolders).mock.calls.length;
    const requestsCalls = vi.mocked(getRequests).mock.calls.length;

    fireEvent(
      window,
      new StorageEvent("storage", {
        key: "sed:requests-changed",
        newValue: String(Date.now()),
      }),
    );

    await waitFor(() =>
      expect(vi.mocked(getFolders).mock.calls.length).toBeGreaterThan(foldersCalls),
    );
    expect(vi.mocked(getRequests).mock.calls.length).toBeGreaterThan(requestsCalls);
  });
});

// Тесты формы создания ОК (Задача 3.3): единая форма без стадий — блоки
// активируются по зависимостям (предприятие → сотрудник → маршрут → «Создать»).
// Сеть не нужна: модуль requests-client мокается (как в admin-settings.test.tsx).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { CreateForm } from "./create-form";
import { createRequest, getEmployeeCard, getEnterprises, getStepGroups, searchEmployees } from "./requests-client";

// Мок клиента заявок; чистые функции — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    getStepGroups: vi.fn(),
    searchEmployees: vi.fn(),
    getEmployeeCard: vi.fn(),
    createRequest: vi.fn(),
  };
});

// Предприятия из настроек (код — значение, название — подпись).
const enterprises = [{ code: "ENT_PRIMER_1", name: "Предприятие «Пример-1» (вымышленное)" }];
// Группы ручного конструктора из settings (allowed_ad_groups).
const groups = ["SED_STEP_BUH", "SED_STEP_SEC"];

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(getStepGroups).mockReset();
  vi.mocked(searchEmployees).mockReset();
  vi.mocked(getEmployeeCard).mockReset();
  vi.mocked(createRequest).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
  vi.mocked(getStepGroups).mockResolvedValue(groups);
});

// Заполнение формы до маршрута: предприятие → поиск 1С (503) → ручной ввод.
async function fillEmployeeManually(): Promise<void> {
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
  // Маршрут появляется, когда сотрудник заполнен (без стадий и «Далее»).
  await waitFor(() => expect(screen.getByText("Маршрут согласования")).toBeInTheDocument());
}

describe("CreateForm", () => {
  // Полный путь: предприятие → сотрудник → маршрут → 201 → статус + сброс.
  it("создаёт заявку через API и сбрасывает форму", async () => {
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
    fireEvent.click(screen.getByText("SED_STEP_BUH"));
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/Заявка REQ-0001 создана/),
    );
    expect(createRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        enterprise: "ENT_PRIMER_1",
        tab_num: "Т-000201",
        fio: "Громов Игорь Олегович",
        steps: [{ owner_group: "SED_STEP_BUH" }],
      }),
    );
    // Сброс формы: предприятие пусто → маршрут скрыт, «Создать» недоступен.
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toHaveValue(""));
    expect(screen.queryByText("Маршрут согласования")).not.toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeDisabled();
  });

  // При 200 поиск показывает список найденных сотрудников.
  it("при 200 показывает список сотрудников 1С", async () => {
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

    await waitFor(() => expect(screen.getByLabelText("Сотрудник")).toBeInTheDocument());
    // Выбор сотрудника заполняет поля (карточка догружает подразделение/должность)
    // → появляется маршрут (без «Далее»).
    fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "Т-000201" } });
    await waitFor(() => expect(getEmployeeCard).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Маршрут согласования")).toBeInTheDocument());
  });

  // Ошибка 422 при создании — alert с текстом от API.
  it("при 422 показывает понятный alert", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(createRequest).mockRejectedValue(
      new ApiHttpError(422, "Шаблон не найден: задайте ручной маршрут (steps)"),
    );

    render(<CreateForm role="hr" />);
    await fillEmployeeManually();
    fireEvent.click(screen.getByText("SED_STEP_BUH"));
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/задайте ручной маршрут/),
    );
  });

  // Без предприятия/сотрудника/маршрута «Создать» недоступен.
  it("кнопка «Создать» недоступна без полных данных", () => {
    render(<CreateForm role="hr" />);
    expect(screen.getByText("Создать")).toBeDisabled();
    // Блоки сотрудника и маршрута не видны, пока не выбрано предприятие.
    expect(screen.queryByLabelText("Поиск сотрудника")).not.toBeInTheDocument();
    expect(screen.queryByText("Маршрут согласования")).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByText("SED_STEP_BUH"));
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(false));
  });

  // В окне-попе (?view=create): после успешного создания окно закрывается.
  it("в окне-попе закрывает окно после создания (closeOnCreate)", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
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
    fireEvent.click(screen.getByText("SED_STEP_BUH"));
    fireEvent.click(screen.getByText("Создать"));
    await waitFor(() => expect(close).toHaveBeenCalled());
    close.mockRestore();
  });
});

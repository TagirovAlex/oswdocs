// Тесты формы создания ОК (волна B4): шаги предприятие→сотрудник→маршрут.
// Сеть не нужна: модуль requests-client мокается (как в admin-settings.test.tsx).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { CreateForm } from "./create-form";
import { createRequest, getEnterprises, getStepGroups, searchEmployees } from "./requests-client";

// Мок клиента заявок; чистые функции (toRequestRow и др.) — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    getStepGroups: vi.fn(),
    searchEmployees: vi.fn(),
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
  vi.mocked(createRequest).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
  vi.mocked(getStepGroups).mockResolvedValue(groups);
});

// Проход мастера до шага «Маршрут»: предприятие → поиск 1С → 503 → ручной ввод.
async function walkToRouteStep(): Promise<void> {
  render(<CreateForm role="hr" />);
  await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
  fireEvent.click(screen.getByText("Далее"));
  // Шаг 2: поиск; без баз 1С — 503 → ручной ввод с пометкой.
  fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });
  await waitFor(() => expect(screen.getByLabelText("Табельный №")).toBeInTheDocument());
  expect(screen.getByText(/введите вручную/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("ФИО"), { target: { value: "Громов Игорь Олегович" } });
  fireEvent.change(screen.getByLabelText("Табельный №"), { target: { value: "Т-000201" } });
  fireEvent.change(screen.getByLabelText("Подразделение"), { target: { value: "Цех № 1" } });
  fireEvent.change(screen.getByLabelText("Должность"), { target: { value: "Слесарь" } });
  fireEvent.click(screen.getByText("Далее"));
  expect(screen.getByText("Маршрут согласования")).toBeInTheDocument();
}

describe("CreateForm", () => {
  // Полный проход мастера до создания: 201 → статус + сброс на шаг 1.
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

    await walkToRouteStep();
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
    // Сброс формы на шаг 1.
    expect(screen.getByText("Создание заявки (шаг 1 из 3)")).toBeInTheDocument();
  });

  // При 200 поиск показывает список найденных сотрудников.
  it("при 200 показывает список сотрудников 1С", async () => {
    vi.mocked(searchEmployees).mockResolvedValue({
      items: [
        {
          key: "ENT_PRIMER_1#Т-000201",
          tab_num: "Т-000201",
          fio: "Громов Игорь Олегович",
          dept: "Цех № 1",
          position: "Слесарь",
          needs_manual_review: false,
        },
      ],
    });

    render(<CreateForm role="hr" />);
    await waitFor(() => expect(screen.getByLabelText("Предприятие")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "ENT_PRIMER_1" } });
    fireEvent.click(screen.getByText("Далее"));
    fireEvent.change(screen.getByLabelText("Поиск сотрудника"), { target: { value: "Громов" } });

    await waitFor(() => expect(screen.getByLabelText("Сотрудник")).toBeInTheDocument());
    expect(screen.queryByLabelText("Табельный №")).not.toBeInTheDocument();
    // Выбор сотрудника заполняет поля → можно перейти к маршруту.
    fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "Т-000201" } });
    fireEvent.click(screen.getByText("Далее"));
    expect(screen.getByText("Маршрут согласования")).toBeInTheDocument();
  });

  // Ошибка 422 при создании — alert с текстом от API.
  it("при 422 показывает понятный alert", async () => {
    vi.mocked(searchEmployees).mockRejectedValue(new ApiHttpError(503, "Клиент 1С не настроен"));
    vi.mocked(createRequest).mockRejectedValue(
      new ApiHttpError(422, "Шаблон не найден: задайте ручной маршрут (steps)"),
    );

    await walkToRouteStep();
    fireEvent.click(screen.getByText("SED_STEP_BUH"));
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/задайте ручной маршрут/),
    );
  });

  // Без предприятия дальше не пускает.
  it("не пускает дальше без предприятия", () => {
    render(<CreateForm role="hr" />);
    expect(screen.getByText("Далее")).toBeDisabled();
  });

  // Админ тоже может создавать (роль admin, как в API _is_hr).
  it("админу создание доступно", async () => {
    render(<CreateForm role="admin" />);
    await waitFor(() => expect(screen.getByText("Создание заявки (шаг 1 из 3)")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // Не-ОК создание закрыто.
  it("владельцу и гостю создание закрыто", () => {
    render(<CreateForm role="owner" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/только ОК/);
  });
});
// Тесты формы создания ОК (Задача 3.3): единая форма без стадий — блоки
// активируются по зависимостям (предприятие → сотрудник → маршрут → «Создать»).
// Сотрудник — живой поиск (debounce), данные из 1С справочные; маршрут —
// конструктор блоков с исполнителями из AD. Сеть не нужна: модуль
// requests-client мокается (как в admin-settings.test.tsx).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { CreateForm } from "./create-form";
import { createRequest, getEmployeeCard, getEnterprises, searchAd, searchEmployees, submitRequest } from "./requests-client";

// Мок клиента заявок; чистые функции — реальные.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getEnterprises: vi.fn(),
    searchEmployees: vi.fn(),
    getEmployeeCard: vi.fn(),
    searchAd: vi.fn(),
    createRequest: vi.fn(),
    submitRequest: vi.fn(),
  };
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

beforeEach(() => {
  vi.mocked(getEnterprises).mockReset();
  vi.mocked(searchEmployees).mockReset();
  vi.mocked(getEmployeeCard).mockReset();
  vi.mocked(searchAd).mockReset();
  vi.mocked(createRequest).mockReset();
  vi.mocked(submitRequest).mockReset();
  vi.mocked(getEnterprises).mockResolvedValue(enterprises);
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

// Добавление исполнителя из AD в первый блок конструктора маршрута.
async function addAdExecutor(): Promise<void> {
  fireEvent.click(screen.getByText("Добавить блок"));
  fireEvent.click(screen.getByText("Добавить исполнителя"));
  fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Петров" } });
  await waitFor(() => expect(screen.getByText("Петров Пётр Петрович")).toBeInTheDocument());
  fireEvent.click(screen.getByText("Петров Пётр Петрович"));
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
    // Поле steps (группы) больше не отправляется.
    const body = vi.mocked(createRequest).mock.calls[0][0];
    expect(body.steps).toBeUndefined();
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
    // Подразделение/должность — текст из карточки, не input.
    await waitFor(() => expect(screen.getByText("Цех № 1")).toBeInTheDocument());
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

    fireEvent.click(screen.getByText("Добавить блок"));
    expect(screen.getByText("Блок 1")).toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeDisabled();

    // Панель AD: живой поиск → кандидат → клик добавляет исполнителя.
    fireEvent.click(screen.getByText("Добавить исполнителя"));
    fireEvent.change(screen.getByLabelText("Поиск в AD"), { target: { value: "Петров" } });
    await waitFor(() => expect(searchAd).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Петров Пётр Петрович")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Петров Пётр Петрович"));
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
});

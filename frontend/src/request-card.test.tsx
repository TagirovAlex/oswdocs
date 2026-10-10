// Тесты карточки заявки (Задача 3): шаги, отметки владельца, действия ОК,
// печать бегунка, скан-вложения. Компонент RequestCard используется в
// окне-попе (?view=request&id=…). Данные — из requests-client (мокается).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError, me } from "./auth-client";
import type { AuthUser } from "./auth-client";
import { RequestCard } from "./request-card";
import {
  addComment,
  decideStep,
  deleteAttachment,
  deleteRequest,
  finishRequest,
  getAdGroupMembers,
  getAttachments,
  getRequest,
  getStepGroups,
  printRequest,
  replaceRequestSteps,
  submitRequest,
  toExecution,
  uploadAttachment,
  withdrawRequest,
} from "./requests-client";
import type { AttachmentMeta, RequestOut } from "./requests-client";
import type { Role } from "./api-mock";

vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getRequest: vi.fn(),
    decideStep: vi.fn(),
    submitRequest: vi.fn(),
    toExecution: vi.fn(),
    finishRequest: vi.fn(),
    withdrawRequest: vi.fn(),
    deleteRequest: vi.fn(),
    getAttachments: vi.fn(),
    uploadAttachment: vi.fn(),
    deleteAttachment: vi.fn(),
    printRequest: vi.fn(),
    getStepGroups: vi.fn(),
    getAdGroupMembers: vi.fn(),
    addComment: vi.fn(),
    replaceRequestSteps: vi.fn(),
  };
});

vi.mock("./auth-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./auth-client")>();
  return { ...actual, me: vi.fn() };
});

// Пользователь сессии для кнопки «Удалить» у автора вложения.
function sessionUser(sam: string): AuthUser {
  return { sam, groups: ["SED_HR"], role: "hr" };
}

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

// Заявка с шагом, который может отметить текущий пользователь (can_act=true).
function requestForAction(fio: string | null): RequestOut {
  return requestWith(fio, "На согласовании", "REQ-0001", [
    {
      order: 1,
      owner_group: "petrov.pp",
      resolver: "by_user",
      assignee: "petrov.pp",
      owner_name: "Петров Пётр Петрович",
      can_act: true,
      status: "ожидает",
      expires_at: "2026-10-05T10:00:00+00:00",
    },
  ]);
}

// Ключ карточки сотрудника, который бэкенд резолвит по локальному справочнику
// (только привилегированным; в нём табельный номер — ПДн).
const EMP_KEY = "ENT_PRIMER_1|zup|Т-000202";

function renderCard(requestId: string = "REQ-0001", role: Role = "hr") {
  return render(<RequestCard requestId={requestId} role={role} />);
}

// Имитация окна-попа (есть opener) и его отсутствия (та же вкладка).
function setOpener(value: unknown): void {
  Object.defineProperty(window, "opener", { value, writable: true, configurable: true });
}
function clearOpener(): void {
  setOpener(null);
}

beforeEach(() => {
  // Снимаем спаи window.confirm/window.close между тестами (jsdom-заглушки).
  vi.restoreAllMocks();
  window.localStorage.clear();
  clearOpener();
  vi.mocked(getRequest).mockReset();
  vi.mocked(decideStep).mockReset();
  vi.mocked(submitRequest).mockReset();
  vi.mocked(toExecution).mockReset();
  vi.mocked(finishRequest).mockReset();
  vi.mocked(withdrawRequest).mockReset();
  vi.mocked(deleteRequest).mockReset();
  vi.mocked(getAttachments).mockReset();
  vi.mocked(uploadAttachment).mockReset();
  vi.mocked(deleteAttachment).mockReset();
  vi.mocked(printRequest).mockReset();
  vi.mocked(getStepGroups).mockReset();
  // Справочник групп по умолчанию пуст: в должности — код группы.
  vi.mocked(getStepGroups).mockResolvedValue([]);
  vi.mocked(addComment).mockReset();
  vi.mocked(replaceRequestSteps).mockReset();
  vi.mocked(getAdGroupMembers).mockReset();
  // Состав по умолчанию недоступен: в сотруднике — название/код группы.
  vi.mocked(getAdGroupMembers).mockRejectedValue(new ApiHttpError(503, "AD недоступен"));
  vi.mocked(me).mockReset();
  // По умолчанию сессии нет: кнопок удаления у не-админов нет (старое поведение).
  vi.mocked(me).mockRejectedValue(new ApiHttpError(401, "Нет токена"));
  vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
  vi.mocked(getAttachments).mockResolvedValue([]);
  vi.mocked(deleteRequest).mockResolvedValue({ deleted: "REQ-0001" });
});

describe("RequestCard", () => {
  // Печать бегунка (вариант 1): PDF base64 в ответе, версий нет — статус
  // «Бегунок сгенерирован», запрос с requestId.
  it("печать генерирует бегунок и показывает статус без версии", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(printRequest).mockResolvedValue({ generated: true, reason: null, pdf_b64: "AAAA" });

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Печать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("Бегунок сгенерирован")).toBeInTheDocument());
    expect(vi.mocked(printRequest)).toHaveBeenCalledWith("REQ-0001");
  });

  // generated=false с reason (нет LibreOffice/шаблона) — статус, не ошибка.
  it("печать без LibreOffice показывает reason как статус, не как ошибку", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(printRequest).mockResolvedValue({ generated: false, reason: "LibreOffice не настроен", pdf_b64: null });

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Печать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("LibreOffice не настроен")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  // Ошибка печати (403) — понятный текст в alert.
  it("ошибка печати показывает понятный текст", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(printRequest).mockRejectedValue(new ApiHttpError(403, "Печать доступна только ОК"));

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Печать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("Печать доступна только ОК")).toBeInTheDocument());
  });

  // Карточка показывает шаги выбранной заявки (одна таблица рассмотрения).
  it("карточка показывает шаги заявки", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        { order: 1, owner_group: "SED_STEP_BUH", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
        { order: 2, owner_group: "SED_STEP_OK", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getAllByLabelText("Шаги заявки").length).toBeGreaterThan(0));
    const steps = within(screen.getAllByLabelText("Шаги заявки")[0]);
    // Групповой шаг без состава: код виден и в «Должность / Группа», и в «Исполнитель».
    expect(steps.getAllByText("SED_STEP_BUH").length).toBeGreaterThan(0);
    expect(steps.getAllByText("SED_STEP_OK").length).toBeGreaterThan(0);
  });

  // Шаги с блоками: колонки «№» нет; персональный исполнитель — ФИО
  // (owner_name), логин AD не выводится.
  it("шаги с блоками: без колонки «№», персональный исполнитель — ФИО без логина", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      enterprise_name: "Предприятие «Пример-1» (вымышленное)",
      steps: [
        { order: 1101, owner_group: "petrov.pp", resolver: "by_user", assignee: "petrov.pp", owner_name: "Петров Пётр Петрович", can_act: false, status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
        { order: 1102, owner_group: "SED_STEP_OK", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getAllByLabelText("Шаги заявки")[0]);
    // Колонки «№» нет: номеров шагов в таблице рассмотрения нет.
    expect(steps.queryByText("№")).not.toBeInTheDocument();
    expect(steps.queryByText("2.1")).not.toBeInTheDocument();
    expect(steps.queryByText("2.2")).not.toBeInTheDocument();
    expect(steps.getByText("Петров Пётр Петрович")).toBeInTheDocument();
    expect(steps.getAllByText("SED_STEP_OK").length).toBeGreaterThan(0);
    // Логин AD согласующего в карточке не выводится.
    expect(screen.queryByText(/petrov\.pp/)).not.toBeInTheDocument();
    // Предприятие — названием, а не кодом.
    expect(screen.getByText(/Предприятие «Пример-1»/)).toBeInTheDocument();
    expect(screen.queryByText(/ENT_PRIMER_1/)).not.toBeInTheDocument();
  });

  // Рассмотрение — одна сетка на все шаги: шапка Вид/Должность/Сотрудник,
  // вид блока с номером при нескольких блоках.
  it("рассмотрение — одна таблица с видом блоков", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        { order: 1, owner_group: "SED_STEP_BUH", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
        { order: 1101, owner_group: "SED_STEP_OK", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    // Одна таблица на все шаги, а не карточка на блок.
    expect(screen.getAllByLabelText("Шаги заявки")).toHaveLength(1);
    const table = screen.getByLabelText("Шаги заявки");
    expect(within(table).getByText("Вид рассмотрения")).toBeInTheDocument();
    expect(within(table).getByText("Должность")).toBeInTheDocument();
    expect(within(table).getByText("Сотрудник")).toBeInTheDocument();
    expect(within(table).getByText("Последовательно")).toBeInTheDocument();
    expect(within(table).getByText("Параллельно")).toBeInTheDocument();
  });

  // Вид рассмотрения пишется один раз на блок (rowSpan по строкам блока).
  it("вид рассмотрения объединён на весь блок", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        { order: 1101, owner_group: "SED_STEP_BUH", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
        { order: 1102, owner_group: "SED_STEP_OK", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
        { order: 2, owner_group: "SED_STEP_OK", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const table = screen.getByLabelText("Шаги заявки");
    // Параллельный блок из двух шагов — одна ячейка вида на две строки.
    const parallel = within(table).getByText("Параллельно");
    expect(parallel.getAttribute("rowspan")).toBe("2");
    // Последовательный шаг — вид без объединения.
    const sequential = within(table).getByText("Последовательно");
    expect(sequential.getAttribute("rowspan")).toBe("1");
  });

  // Ширины колонок — через colgroup (по колонкам, не nth-child): в строках
  // 2+ блока нет ячейки с rowSpan, и позиционные селекторы давят не те ячейки.
  it("ширины колонок рассмотрения — colgroup на 6 колонок", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const table = screen.getByLabelText("Шаги заявки");
    const cols = table.querySelectorAll("colgroup > col");
    expect(cols).toHaveLength(6);
    expect(cols[0].getAttribute("class")).toContain("sed-review__col-kind");
    expect(cols[1].getAttribute("class")).toContain("sed-review__col-duty");
  });

  // Должность — читаемое наименование группы из справочника настроек.
  it("должность — наименование группы из справочника", async () => {
    vi.mocked(getStepGroups).mockResolvedValue([
      { id: "SED_STEP_BUH", name: "Бухгалтерия" },
    ]);
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.getByText("Бухгалтерия")).toBeInTheDocument();
  });

  // Сотрудник — состав группы из AD, даже когда наименование группы известно
  // (регрессия: условие !owner_name пропускало загрузку состава).
  it("сотрудник — состав группы при известном наименовании", async () => {
    vi.mocked(getStepGroups).mockResolvedValue([
      { id: "SED_STEP_BUH", name: "Бухгалтерия" },
    ]);
    vi.mocked(getAdGroupMembers).mockResolvedValue([
      { sam: "step.buhgalter", display_name: "Вымышленный Бухгалтер", mail: "", department: "", title: "Бухгалтер" },
    ]);
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "by_group",
          owner_name: "Бухгалтерия",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(vi.mocked(getAdGroupMembers)).toHaveBeenCalledWith("SED_STEP_BUH");
    await waitFor(() => expect(screen.getByText("Вымышленный Бухгалтер")).toBeInTheDocument());
    // Должность участника в списке не дублируется (она — в колонке «Должность»).
    expect(screen.queryByText("Бухгалтер")).not.toBeInTheDocument();
  });

  // Регресс ревью: персональный шаг «замена руководителя»
  // (resolver=ad_direct_manager, исполнитель в assignee) отличается от by_user
  // только набором полей — по resolver его не опознать, поэтому в колонке
  // «Исполнитель» должно быть ФИО, а не название группы-владельца.
  it("персональный шаг ad_direct_manager: ФИО замены руководителя, а не группа", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "ad_direct_manager",
          assignee: "sidorova.as",
          owner_name: "Сидорова Анна Сергеевна",
          can_act: true,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getByLabelText("Шаги заявки"));
    expect(steps.getByText("Сидорова Анна Сергеевна")).toBeInTheDocument();
    expect(steps.queryByText("SED_STEP_BUH")).not.toBeInTheDocument();
    // Логин AD замены руководителя в карточке не выводится.
    expect(screen.queryByText(/sidorova\.as/)).not.toBeInTheDocument();
  });

  // Должность персонального шага — owner_duty от бэкенда (без прочерка).
  it("должность персонального шага — owner_duty от бэкенда", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          assignee: "petrov.pp",
          owner_name: "Петров Пётр Петрович",
          owner_duty: "Главный бухгалтер",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.getByText("Главный бухгалтер")).toBeInTheDocument();
  });

  // Неразрывные пробелы из 1С/AD в должности нормализуем в обычные,
  // иначе браузер не переносит строку и она вылезает из колонки.
  it("должность с неразрывными пробелами — переносимая", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          assignee: "petrov.pp",
          owner_name: "Петров Пётр Петрович",
          owner_duty: "Заместитель\u00a0директора\u00a0по\u00a0информационным\u00a0технологиям",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const table = screen.getByLabelText("Шаги заявки");
    const dutyCell = within(table).getByText(/Заместитель/);
    // Неразрывных пробелов в ячейке не осталось — только обычные.
    expect(dutyCell.textContent).not.toContain("\u00a0");
    expect(dutyCell.textContent).toContain("Заместитель директора по ");
  });

  // Ключ карточки сотрудника резолвит бэкенд (только привилегированным).
  // С ним ФИО исполнителя в таблице шагов — ссылка на карточку сотрудника.
  it("шаг с employee_key: ФИО исполнителя — ссылка на карточку сотрудника", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "ad_direct_manager",
          assignee: "sidorova.as",
          owner_name: "Сидорова Анна Сергеевна",
          employee_key: EMP_KEY,
          emp_enterprise: "ENT_PRIMER_1",
          emp_base_code: "zup",
          emp_tab_num: "Т-000202",
          can_act: true,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getByLabelText("Шаги заявки"));
    const link = steps.getByRole("link", { name: "Сидорова Анна Сергеевна" });
    expect(link).toHaveAttribute("href", `?view=employee&key=${encodeURIComponent(EMP_KEY)}`);
    fireEvent.click(link);
    expect(open).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith(
      `?view=employee&key=${encodeURIComponent(EMP_KEY)}`,
      "_blank",
      expect.stringContaining("popup"),
    );
  });

  // Без employee_key (неоднозначное совпадение в справочнике или не привилегированный
  // пользователь) ФИО остаётся текстом — ссылки на карточку сотрудника нет.
  it("шаг без employee_key: ФИО исполнителя — текст, не ссылка", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "ad_direct_manager",
          assignee: "sidorova.as",
          owner_name: "Сидорова Анна Сергеевна",
          can_act: true,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });
    const open = vi.spyOn(window, "open").mockImplementation(() => null);

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getByLabelText("Шаги заявки"));
    expect(steps.getByText("Сидорова Анна Сергеевна")).toBeInTheDocument();
    expect(steps.queryByRole("link", { name: "Сидорова Анна Сергеевна" })).not.toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
  });

  // Групповой шаг без ФИО — название группы владельцев (не прочерк).
  it("групповой шаг без ФИО: в колонке «Исполнитель» название группы", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "by_group",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getByLabelText("Шаги заявки"));
    expect(steps.getAllByText("SED_STEP_BUH").length).toBeGreaterThan(0);
  });

  // Действия по шагу — строго по can_act: при can_act=false кнопок нет даже у владельца.
  it("can_act=false — кнопок согласования нет даже у владельца", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith(null, "На согласовании"));

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отказать" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Вернуть" })).not.toBeInTheDocument();
  });

  // can_act=true — кнопки есть; согласованный шаг кнопок не даёт.
  it("согласованный шаг (can_act=false) кнопок согласования не даёт", async () => {
    vi.mocked(getRequest).mockResolvedValue(
      requestWith(null, "Завершено", "REQ-0001", [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          owner_name: "Петров Пётр Петрович",
          can_act: false,
          status: "согласован",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument();
  });

  // can_act=true — кнопки согласования появляются (по can_act, а не по роли).
  it("can_act=true — кнопки согласования появляются у владельца шага", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestForAction(null));

    renderCard("REQ-0001", "owner");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Согласовать" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Отказать" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вернуть" })).toBeInTheDocument();
  });

  // can_act=false — кнопок нет даже у сотрудника.
  it("can_act=false у сотрудника — кнопок нет", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith(null, "На согласовании"));

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument();
  });

  // ОК, не владеющий шагом (can_act=false), кнопок согласования не видит;
  // его собственные действия ОК остаются.
  it("hr, не владеющий шагом, кнопок согласования не видит", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "Черновик"));

    renderCard("REQ-0001", "hr");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Отправить на согласование" })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Отказать" })).not.toBeInTheDocument();
  });

  // Отметка владельца: при отказе без комментария отметка не отправляется.
  it("владелец: при отказе без комментария отметка не отправляется", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestForAction(null));

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByRole("button", { name: "Отказать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Отказать" }));
    expect(screen.getByText("При отказе/возврате комментарий обязателен")).toBeInTheDocument();
    expect(vi.mocked(decideStep)).not.toHaveBeenCalled();
  });

  // Отметка владельца: согласование с комментарием уходит в API, карточка обновляется.
  it("владелец согласовывает свой шаг с комментарием", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestForAction(null));
    vi.mocked(decideStep).mockResolvedValue(requestForAction(null));

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByRole("button", { name: "Согласовать" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Комментарий к решению"), { target: { value: "Согласовано" } });
    fireEvent.click(screen.getByRole("button", { name: "Согласовать" }));
    await waitFor(() =>
      expect(vi.mocked(decideStep)).toHaveBeenCalledWith("REQ-0001", 1, "approve", "Согласовано"),
    );
    await waitFor(() => expect(screen.getByText("Отметка сохранена")).toBeInTheDocument());
  });

  // Согласование в окне-попе: список оповещается, окно закрывается.
  it("согласование в попапе закрывает окно и оповещает список", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestForAction(null));
    vi.mocked(decideStep).mockResolvedValue(requestForAction(null));
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    setOpener({});

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByRole("button", { name: "Согласовать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Согласовать" }));
    await waitFor(() => expect(close).toHaveBeenCalled());
    expect(window.localStorage.getItem("sed:requests-changed")).not.toBeNull();
    clearOpener();
  });

  // Отказ без комментария на ходу: форма решения скрывается по can_act
  // обновлённой карточки, но комментарий к заявке на листе рассмотрения
  // остаётся (issue_report п.2) — дописать заявке можно, не листая «Историю».
  it("после отказа комментарий к заявке остаётся доступен на листе рассмотрения", async () => {
    // Обновлённая карточка после отказа: шаг закрыт, заявка на доработке.
    const refused = requestWith(null, "На доработке", "REQ-0001", [
      {
        order: 1,
        owner_group: "petrov.pp",
        resolver: "by_user",
        assignee: "petrov.pp",
        can_act: false,
        status: "отклонен",
        expires_at: "2026-10-05T10:00:00+00:00",
      },
    ]);
    vi.mocked(getRequest)
      .mockResolvedValueOnce(requestForAction(null))
      .mockResolvedValue(refused);
    vi.mocked(decideStep).mockResolvedValue(refused);
    vi.mocked(addComment).mockResolvedValue({
      id: "c-1",
      request_id: "REQ-0001",
      author: "petrov.pp",
      body: "Дописка после отказа",
      at: "2026-10-05T11:00:00+00:00",
      kind: "request",
    });

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByRole("button", { name: "Отказать" })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Комментарий к решению"), {
      target: { value: "Вымышленная причина отказа" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Отказать" }));
    await waitFor(() =>
      expect(vi.mocked(decideStep)).toHaveBeenCalledWith(
        "REQ-0001",
        1,
        "reject",
        "Вымышленная причина отказа",
      ),
    );
    // Форма решения исчезла (can_act больше не true), комментарий к заявке — нет.
    expect(screen.queryByRole("button", { name: "Отказать" })).not.toBeInTheDocument();
    const field = screen.getByLabelText("Текст комментария");
    fireEvent.change(field, { target: { value: "Дописка после отказа" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить комментарий" }));
    await waitFor(() =>
      expect(vi.mocked(addComment)).toHaveBeenCalledWith("REQ-0001", "Дописка после отказа"),
    );
  });

  // Шаг с require_comment: комментарий обязателен и при согласии (issue_report п.3).
  it("шаг с require_comment требует комментарий и при согласовании", async () => {
    vi.mocked(getRequest).mockResolvedValue(
      requestWith(null, "На согласовании", "REQ-0001", [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          assignee: "petrov.pp",
          can_act: true,
          status: "ожидает",
          require_comment: true,
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );

    renderCard("REQ-0001", "owner");
    const field = await waitFor(() => screen.getByLabelText("Комментарий к решению"));
    expect(field).toBeRequired();
    fireEvent.click(screen.getByRole("button", { name: "Согласовать" }));
    expect(screen.getByText("Шаг требует комментарий и при согласовании")).toBeInTheDocument();
    expect(vi.mocked(decideStep)).not.toHaveBeenCalled();
  });

  // Отклонённая заявка: инициатору «Повторить» и «Перенаправить»
  // (issue_report п.7). Статус заявки «Отклонено» не используется — признак
  // отказа шаг со статусом «отклонен».
  it("отклонённая заявка даёт инициатору «Повторить» и «Перенаправить»", async () => {
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "На доработке", "REQ-0001", [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "by_group",
          can_act: false,
          status: "согласован",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
        {
          order: 1001,
          owner_group: "SED_STEP_HR",
          resolver: "by_group",
          can_act: false,
          status: "отклонен",
          comment: "Вымышленная причина отказа",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );
    vi.mocked(submitRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001"),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument(),
    );
    // Отказ виден в карточке, согласованный шаг не потерян.
    expect(screen.getByText("Вымышленная причина отказа")).toBeInTheDocument();
    // «Перенаправить» открывает редактор маршрута.
    expect(screen.getByRole("button", { name: "Перенаправить" })).toBeInTheDocument();
    // После отправки заявка ушла на согласование: блок доработки исчез, а в
    // общем тулбаре появилась «Отправить на согласование» для следующего круга.
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "На согласовании", "REQ-0001"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(vi.mocked(submitRequest)).toHaveBeenCalledWith("REQ-0001"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Перенаправить" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Скорректировать маршрут" }),
    ).not.toBeInTheDocument();
  });

  // Правка маршрута заявки (issue_report п.4): редактор открывается, шаг
  // добавляется и порядок блоков уходит в PATCH /requests/{id}/steps. В теле
  // только ОЖИДАЮЩИЕ шаги: закрытые («согласован»/«отклонен») бэкенд сохраняет
  // сам, иначе они бы задвоились.
  it("скорректировать маршрут: добавленный шаг уходит в PATCH /steps", async () => {
    vi.mocked(getStepGroups).mockResolvedValue([
      { id: "SED_STEP_BUH", name: "Бухгалтерия" },
      { id: "SED_STEP_HR", name: "Кадры" },
    ]);
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "На доработке", "REQ-0001", [
        {
          order: 1,
          owner_group: "SED_STEP_BUH",
          resolver: "by_group",
          can_act: false,
          status: "согласован",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
        {
          order: 1001,
          owner_group: "SED_STEP_OK",
          resolver: "by_group",
          can_act: false,
          status: "отклонен",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
        {
          order: 1002,
          owner_group: "SED_STEP_BUH",
          resolver: "by_group",
          can_act: false,
          status: "ожидает",
          require_comment: true,
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );
    vi.mocked(replaceRequestSteps).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "Черновик", "REQ-0001"),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скорректировать маршрут" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать маршрут" }));
    // Открытый редактор подставил текущий ожидающий шаг (первый блок).
    expect(within(screen.getByLabelText("Правка маршрута")).getByLabelText("Группа блока 1, шаг 1"))
      .toHaveValue("SED_STEP_BUH");

    // Добавляем шаг-группу из справочника и сохраняем.
    const addSelect = within(screen.getByLabelText("Правка маршрута")).getByLabelText(
      "Группа для блока 1",
    );
    fireEvent.change(addSelect, { target: { value: "SED_STEP_HR" } });
    fireEvent.change(screen.getByLabelText("Причина правки маршрута"), {
      target: { value: "Вымышленная причина правки" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маршрут" }));
    await waitFor(() =>
      expect(vi.mocked(replaceRequestSteps)).toHaveBeenCalledWith(
        "REQ-0001",
        [
          {
            mode: "sequential",
            steps: [
              // require_comment шага переносится: правка маршрута не должна
              // молча отменять требование комментария, заданное бланком.
              { owner_group: "SED_STEP_BUH", resolver: "by_group", require_comment: true },
              { owner_group: "SED_STEP_HR", resolver: "by_group", require_comment: false },
            ],
          },
        ],
        "Вымышленная причина правки",
      ),
    );
  });

  // Шаг с несколькими ответственными в StepSpec не помещается: правка такого
  // маршрута блокируется понятным сообщением, а не молча отнимает согласующих.
  it("скорректировать маршрут: шаг с несколькими ответственными блокирует правку", async () => {
    vi.mocked(getStepGroups).mockResolvedValue([{ id: "SED_STEP_BUH", name: "Бухгалтерия" }]);
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "На доработке", "REQ-0001", [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          assignee: "petrov.pp",
          assignees: ["petrov.pp", "sidorova.as"],
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скорректировать маршрут" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать маршрут" }));
    // Неизменяемый шаг показан списком ответственных, кнопок правки у него нет.
    expect(screen.getByText(/Ответственные: petrov\.pp, sidorova\.as/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Удалить шаг 1.1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маршрут" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Правка недоступна: petrov.pp, sidorova.as",
    );
    expect(vi.mocked(replaceRequestSteps)).not.toHaveBeenCalled();
  });

  // Удаление блока с неизменяемым шагом спрашивает подтверждение: PATCH
  // пересобирает маршрут целиком, и молча снесённый шаг унёс бы согласующих.
  it("скорректировать маршрут: удаление блока с несколькими ответственными спрашивает подтверждение", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "На доработке", "REQ-0001", [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          assignee: "petrov.pp",
          assignees: ["petrov.pp", "sidorova.as"],
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скорректировать маршрут" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать маршрут" }));
    fireEvent.click(screen.getByRole("button", { name: "Удалить блок 1" }));
    expect(confirmSpy).toHaveBeenCalled();
    // Отказ — блок на месте, PATCH не отправляется.
    expect(screen.getByText(/Ответственные: petrov\.pp, sidorova\.as/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маршрут" }));
    expect(vi.mocked(replaceRequestSteps)).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  // Группа шага вне справочника (этап-реестр, удалённая группа) не выражается
// через owner_group: такой шаг не переписывается молча, а блокирует правку —
  // иначе в PATCH ушёл бы мёртвый код и шаг не отметил бы никто.
  it("скорректировать маршрут: группа вне справочника блокирует правку", async () => {
    vi.mocked(getStepGroups).mockResolvedValue([{ id: "SED_STEP_HR", name: "Кадры" }]);
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "Черновик", "REQ-0001", [
        {
          order: 1,
          owner_group: "hr",
          resolver: "by_group",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скорректировать маршрут" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать маршрут" }));
    expect(screen.getByText(/Ответственные: hr \(группы нет в справочнике\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маршрут" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Правка недоступна: hr (группы нет в справочнике)",
    );
    expect(vi.mocked(replaceRequestSteps)).not.toHaveBeenCalled();
  });

  // С пустым исполнителем шаг не должен молча выпасть из маршрута.
  it("скорректировать маршрут: шаг без исполнителя блокирует сохранение", async () => {
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "Черновик", "REQ-0001", [
        {
          order: 1,
          owner_group: "petrov.pp",
          resolver: "by_user",
          assignee: "petrov.pp",
          can_act: false,
          status: "ожидает",
          expires_at: "2026-10-05T10:00:00+00:00",
        },
      ]),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скорректировать маршрут" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать маршрут" }));
    fireEvent.change(screen.getByLabelText("Логин исполнителя 1.1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маршрут" }));
    expect(screen.getByRole("alert")).toHaveTextContent("шаг без исполнителя");
    expect(vi.mocked(replaceRequestSteps)).not.toHaveBeenCalled();
  });

  // Владельцу шага и администратору СЭД новые действия не показываются: правку
  // маршрута и отправку заявки делает роль, которой API открывает _require_hr.
  it("владельцу и администратору СЭД не показываются правка маршрута и действия по доработке", async () => {
    const refused = requestWith(null, "На доработке", "REQ-0001", [
      {
        order: 1,
        owner_group: "petrov.pp",
        resolver: "by_user",
        assignee: "petrov.pp",
        can_act: false,
        status: "отклонен",
        expires_at: "2026-10-05T10:00:00+00:00",
      },
    ]);
    vi.mocked(getRequest).mockResolvedValue(refused);

    for (const role of ["owner", "sed_admin"] as Role[]) {
      const view = renderCard("REQ-0001", role);
      await waitFor(() => expect(view.getByLabelText("Шаги заявки")).toBeInTheDocument());
      expect(view.queryByRole("button", { name: "Скорректировать маршрут" })).not.toBeInTheDocument();
      expect(view.queryByRole("button", { name: "Перенаправить" })).not.toBeInTheDocument();
      expect(view.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
      view.unmount();
    }
  });

  // Пустой маршрут в редакторе — понятная ошибка, запрос не уходит.
  it("скорректировать маршрут: пустой маршрут не отправляется", async () => {
    vi.mocked(getRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "Черновик", "REQ-0001", []),
    );

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Скорректировать маршрут" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скорректировать маршрут" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маршрут" }));
    expect(screen.getByText("Маршрут пуст: добавьте блок с исполнителем")).toBeInTheDocument();
    expect(vi.mocked(replaceRequestSteps)).not.toHaveBeenCalled();
  });

  // Завершение в окне-попе: окно закрывается.
  it("завершение в попапе закрывает окно", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "К исполнению"));
    vi.mocked(finishRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "Завершено"));
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    setOpener({});

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Завершить" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Завершить" }));
    await waitFor(() => expect(close).toHaveBeenCalled());
    expect(window.localStorage.getItem("sed:requests-changed")).not.toBeNull();
    clearOpener();
  });

  // Действие ОК: submit отправляет заявку и перезагружает карточку.
  it("hr отправляет заявку на согласование", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "Черновик"));
    vi.mocked(submitRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));

    renderCard();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Отправить на согласование" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Отправить на согласование" }));
    await waitFor(() => expect(vi.mocked(submitRequest)).toHaveBeenCalledWith("REQ-0001"));
    await waitFor(() => expect(screen.getByText("Заявка отправлена на согласование")).toBeInTheDocument());
  });

  // Скан-вложения: мета со ссылкой на окно просмотра.
  it("карточка показывает мета вложений со ссылкой на просмотр", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    const attachments: AttachmentMeta[] = [
      {
        id: 1,
        request_id: "REQ-0001",
        file_name: "scan.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
        uploaded_by: "ok.vymyshlennaya",
        uploaded_at: "2026-09-29T10:00:00+00:00",
      },
    ];
    vi.mocked(getAttachments).mockResolvedValue(attachments);

    renderCard();
    await waitFor(() => expect(screen.getByText(/scan\.pdf/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Открыть" }).getAttribute("href")).toBe(
      "?view=attachment&id=1&name=scan.pdf&mime=application%2Fpdf",
    );
  });

  // Скан-вложения: 413 при загрузке — понятный текст.
  it("загрузка скана: 413 показывает понятный текст", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(getAttachments).mockResolvedValue([]);
    vi.mocked(uploadAttachment).mockRejectedValue(new ApiHttpError(413, "Файл больше лимита"));

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Файл скана")).toBeInTheDocument());
    const file = new File(["x".repeat(100)], "scan.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Файл скана"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByText("Файл больше лимита")).toBeInTheDocument());
    expect(vi.mocked(uploadAttachment)).toHaveBeenCalledWith("REQ-0001", file);
  });

  // Вложение чужого автора: кнопки «Удалить» нет (роль hr, сессия чужая).
  function attachmentBy(author: string): AttachmentMeta[] {
    return [
      {
        id: 1,
        request_id: "REQ-0001",
        file_name: "scan.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
        uploaded_by: author,
        uploaded_at: "2026-09-29T10:00:00+00:00",
      },
    ];
  }

  // Удаление вложения: автор видит кнопку, подтверждение — удаление и обновление списка.
  it("автор удаляет своё вложение после подтверждения", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(me).mockResolvedValue(sessionUser("ok.vymyshlennaya"));
    vi.mocked(getAttachments).mockResolvedValue(attachmentBy("ok.vymyshlennaya"));
    vi.mocked(deleteAttachment).mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(confirm).toHaveBeenCalledWith("Удалить вложение scan.pdf? Действие необратимо.");
    await waitFor(() => expect(vi.mocked(deleteAttachment)).toHaveBeenCalledWith(1));
    // После удаления список вложений перезапрашивается.
    await waitFor(() => expect(vi.mocked(getAttachments).mock.calls.length).toBeGreaterThan(1));
  });

  // Удаление вложения: отказ в подтверждении — запрос не уходит.
  it("отказ в подтверждении не удаляет вложение", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(me).mockResolvedValue(sessionUser("ok.vymyshlennaya"));
    vi.mocked(getAttachments).mockResolvedValue(attachmentBy("ok.vymyshlennaya"));
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(vi.mocked(deleteAttachment)).not.toHaveBeenCalled();
  });

  // Удаление вложения: админ видит кнопку у чужого файла.
  it("admin удаляет чужое вложение", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(me).mockResolvedValue({ sam: "admin.vymyshlenny", groups: ["SED_ADMINS"], role: "admin" });
    vi.mocked(getAttachments).mockResolvedValue(attachmentBy("ok.vymyshlennaya"));
    vi.mocked(deleteAttachment).mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderCard("REQ-0001", "admin");
    await waitFor(() => expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(vi.mocked(deleteAttachment)).toHaveBeenCalledWith(1));
  });

  // Удаление вложения: чужой файл не-автору и не-админу — кнопки нет.
  it("чужое вложение не-автору без кнопки удаления", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(me).mockResolvedValue(sessionUser("step.chuzhoi"));
    vi.mocked(getAttachments).mockResolvedValue(attachmentBy("ok.vymyshlennaya"));

    renderCard();
    await waitFor(() => expect(screen.getByText(/scan\.pdf/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Удалить" })).not.toBeInTheDocument();
  });

  // Удаление вложения: 403 от API — понятный текст, список не ломается.
  it("удаление чужого: 403 показывает понятный текст", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(me).mockResolvedValue(sessionUser("ok.vymyshlennaya"));
    vi.mocked(getAttachments).mockResolvedValue(attachmentBy("ok.vymyshlennaya"));
    vi.mocked(deleteAttachment).mockRejectedValue(new ApiHttpError(403, "Доступ запрещён"));
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Удалить" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(screen.getByText("Доступ запрещён")).toBeInTheDocument());
  });

  // Удаление заявки (только админ; для тестового периода): кнопка видна,
  // подтверждение; при успехе в окне-попе — оповещение списка через
  // localStorage (sed:requests-changed) и закрытие окна.
  it("admin: успешное удаление в попапе оповещает список и закрывает окно", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    setOpener({});

    renderCard("REQ-0001", "admin");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Удалить заявку" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить заявку" }));
    expect(confirm).toHaveBeenCalledWith("Удалить заявку REQ-0001? Действие необратимо.");
    await waitFor(() => expect(vi.mocked(deleteRequest)).toHaveBeenCalledWith("REQ-0001"));
    await waitFor(() => expect(close).toHaveBeenCalled());
    expect(window.localStorage.getItem("sed:requests-changed")).not.toBeNull();
    clearOpener();
  });

  // Та же вкладка (?view=request в основном окне): window.close() не закрывает —
  // показываем «Заявка удалена» и ссылку «К списку заявок».
  it("admin: удаление в той же вкладке показывает «Заявка удалена» и ссылку к списку", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    clearOpener();

    renderCard("REQ-0001", "admin");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Удалить заявку" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить заявку" }));

    await waitFor(() => expect(screen.getByText("Заявка удалена")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "К списку заявок" })).toBeInTheDocument();
    expect(close).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("sed:requests-changed")).not.toBeNull();
  });

  // Отмена подтверждения — заявка не удаляется, попап не закрывается.
  it("admin: при отмене подтверждения deleteRequest не вызывается", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);

    renderCard("REQ-0001", "admin");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Удалить заявку" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить заявку" }));
    expect(confirm).toHaveBeenCalled();
    expect(vi.mocked(deleteRequest)).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
  });

  // Ошибка удаления — текст ошибки, попап остаётся открытым.
  it("admin: ошибка удаления показывает текст и не закрывает попап", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);
    vi.mocked(deleteRequest).mockRejectedValue(new ApiHttpError(403, "Удаление заявок — только админ"));

    renderCard("REQ-0001", "admin");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Удалить заявку" })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить заявку" }));
    await waitFor(() =>
      expect(screen.getByText("Удаление заявок — только админ")).toBeInTheDocument(),
    );
    expect(close).not.toHaveBeenCalled();
  });

  // Метаданные заявки — данными (.sed-meta), «Этап/Статус» — пояснением (.sed-note).
  it("метаданные карточки выведены .sed-meta, этап/статус — .sed-note", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(
      screen.getByText(/^Этап: карточка заявки · Статус: На согласовании$/),
    ).toHaveClass("sed-note");
    const meta = screen.getByText(/Таб\.№ Т-000201/);
    expect(meta).toHaveClass("sed-meta");
    expect(meta.textContent).toContain("Цех № 1");
  });

  // Опасное действие (удаление) — отдельный класс; обычные действия остаются sed-btn.
  it("admin: удаление заявки помечено «опасной» кнопкой", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));

    renderCard("REQ-0001", "admin");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Удалить заявку" })).toHaveClass(
      "sed-btn",
      "sed-btn--danger",
    );
    expect(screen.getByRole("button", { name: "Печать" })).not.toHaveClass("sed-btn--danger");
  });

  // Матрица ролей: не-админу кнопка удаления не показывается.
  it("hr: кнопка «Удалить заявку» не показывается", async () => {
    renderCard("REQ-0001", "hr");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Удалить заявку" })).not.toBeInTheDocument();
  });

  // Отзыв заявки (ОК/админ, POST /requests/{id}/withdraw): кнопка у активной
  // заявки, после отзыва статус «Отозвано» и действие больше не предлагается.
  it("hr: отзыв заявки вызывает withdraw и закрытую заявку кнопки не показывает", async () => {
    vi.mocked(getRequest)
      .mockResolvedValueOnce(requestWith("Громов Игорь Олегович", "На согласовании"))
      .mockResolvedValue(requestWith("Громов Игорь Олегович", "Отозвано"));
    vi.mocked(withdrawRequest).mockResolvedValue(
      requestWith("Громов Игорь Олегович", "Отозвано"),
    );

    renderCard("REQ-0001", "hr");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Отозвать заявку" }));

    await waitFor(() => expect(vi.mocked(withdrawRequest)).toHaveBeenCalledWith("REQ-0001"));
    await waitFor(() => expect(screen.getByText("Заявка отозвана")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Отозвать заявку" })).not.toBeInTheDocument(),
    );
  });

  // Владельцу шага (роль owner) блок действий ОК не показывается вовсе.
  it("owner: кнопка «Отозвать заявку» не показывается", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith(null, "На согласовании"));

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Отозвать заявку" })).not.toBeInTheDocument();
  });
});

// Шаги с несколькими ответственными (миграция 0013): ФИО по логинам,
// прогресс «N из M согласовали», режим шага и отметки ответственных.
describe("RequestCard: шаг с несколькими ответственными", () => {
  // Состав ответственных шага (снимок логинов) и группа-владелец этапа.
  const MULTI_SAMS = ["sidorova.as", "petrov.pp", "kozlov.da"];
  const MULTI_NAMES: Record<string, string> = {
    "sidorova.as": "Сидорова Анна Сергеевна",
    "petrov.pp": "Петров Пётр Петрович",
    "kozlov.da": "Козлов Дмитрий Андреевич",
  };

  // Шаг с тремя ответственными: последовательный режим, прогресс 2 из 3.
  function multiStep(
    overrides: Partial<RequestOut["steps"][number]> = {},
  ): RequestOut["steps"][number] {
    return {
      order: 1,
      owner_group: "SED_STEP_OK",
      resolver: "by_user",
      assignee: "sidorova.as",
      owner_name: "Сидорова Анна Сергеевна",
      assignees: MULTI_SAMS,
      approval_mode: "sequential",
      approved_count: 2,
      assignee_count: 3,
      can_act: false,
      status: "ожидает",
      expires_at: "2026-10-05T10:00:00+00:00",
      ...overrides,
    };
  }

  // Заявка с одним таким шагом.
  function multiRequest(
    overrides: Partial<RequestOut["steps"][number]> = {},
    fio: string | null = "Громов Игорь Олегович",
  ): RequestOut {
    return requestWith(fio, "На согласовании", "REQ-0001", [multiStep(overrides)]);
  }

  // ФИО ответственных по логинам приходят из состава группы AD.
  function mockGroupMembers(): void {
    vi.mocked(getAdGroupMembers).mockResolvedValue(
      MULTI_SAMS.map((sam) => ({
        sam,
        display_name: MULTI_NAMES[sam],
        mail: "",
        department: "",
        title: "",
      })),
    );
  }

  // Последовательный шаг: ФИО по логинам, прогресс «2 из 3 согласовали» и
  // режим «отметки всех ответственных»; логины в UI не выводятся.
  it("последовательный шаг: ФИО ответственных, прогресс 2 из 3 и режим", async () => {
    mockGroupMembers();
    vi.mocked(getRequest).mockResolvedValue(multiRequest());

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    await waitFor(() =>
      expect(
        screen.getByText(
          `Ответственные: ${MULTI_NAMES["sidorova.as"]}, ${MULTI_NAMES["petrov.pp"]}, ${MULTI_NAMES["kozlov.da"]}`,
        ),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("2 из 3 согласовали")).toBeInTheDocument();
    expect(screen.getByText("отметки всех ответственных")).toBeInTheDocument();
    // Логины ответственных не выводятся.
    expect(screen.queryByText(/sidorova\.as/)).not.toBeInTheDocument();
    expect(screen.queryByText(/kozlov\.da/)).not.toBeInTheDocument();
    // can_act=false — кнопок решения нет.
    expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument();
  });

  // Параллельный режим шага показывается словами, а не кодом.
  it("параллельный шаг: режим «согласование любым из ответственных»", async () => {
    mockGroupMembers();
    vi.mocked(getRequest).mockResolvedValue(
      multiRequest({ approval_mode: "parallel", approved_count: 0 }),
    );

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.getByText("согласование любым из ответственных")).toBeInTheDocument();
    expect(screen.getByText("0 из 3 согласовали")).toBeInTheDocument();
  });

  // Отметки ответственных: список «кто, когда, решение, комментарий». Чужие
  // отметки приходят с sam=null — решение и комментарий видны, логин нет.
  it("последовательный шаг: чужие отметки видны, свои логины не показываются", async () => {
    mockGroupMembers();
    vi.mocked(getRequest).mockResolvedValue(
      multiRequest({
        approvals: [
          {
            sam: "sidorova.as",
            at: "2026-10-05T09:00:00+00:00",
            decision: "approve",
            comment: "Возражений нет",
          },
          // Чужая отметка непривилегированному: логин скрыт бэкендом.
          { sam: null, at: "2026-10-05T10:30:00+00:00", decision: "approve", comment: null },
        ],
      }),
    );

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const marks = await screen.findByLabelText("Отметки шага 1");
    expect(within(marks).getByText(/Сидорова Анна Сергеевна/)).toBeInTheDocument();
    expect(within(marks).getByText(/Возражений нет/)).toBeInTheDocument();
    // Чужая отметка: решение видно, автор — нейтральной подписью, без логина.
    expect(within(marks).getByText(/Ответственный/)).toBeInTheDocument();
    expect(within(marks).getAllByText(/согласовал/)).toHaveLength(2);
    expect(marks.textContent).not.toContain("kozlov.da");
  });

  // Параллельный шаг у того, кто может действовать (can_act): кнопки есть,
  // после отметки — «отметка учтена», шаг остаётся «На согласовании», окно-попу
  // не закрывается (ждём остальных), повторных кнопок нет.
  it("параллельный шаг: после моей отметки ждём остальных, кнопок больше нет", async () => {
    mockGroupMembers();
    vi.mocked(me).mockResolvedValue({
      sam: "petrov.pp",
      fio: "Петров Пётр Петрович",
      groups: ["SED_STEP_OK"],
      role: "owner",
    });
    const before = multiRequest(
      { approval_mode: "parallel", approved_count: 0, can_act: true },
      null,
    );
    const after = multiRequest({
      approval_mode: "parallel",
      approved_count: 1,
      can_act: false,
      approvals: [
        { sam: "petrov.pp", at: "2026-10-05T11:00:00+00:00", decision: "approve", comment: null },
      ],
    });
    vi.mocked(getRequest)
      .mockResolvedValueOnce(before)
      .mockResolvedValue(after);
    vi.mocked(decideStep).mockResolvedValue(after);
    const close = vi.spyOn(window, "close").mockImplementation(() => undefined);

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByRole("button", { name: "Согласовать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Согласовать" }));

    await waitFor(() =>
      expect(vi.mocked(decideStep)).toHaveBeenCalledWith("REQ-0001", 1, "approve", undefined),
    );
    await waitFor(() =>
      expect(
        screen.getByText("Ваша отметка учтена, ожидаются отметки остальных ответственных"),
      ).toBeInTheDocument(),
    );
    // Шаг ждёт остальных: заявка «На согласовании», прогресс 1 из 3, окно открыто.
    expect(
      screen.getByText(/^Этап: карточка заявки · Статус: На согласовании$/),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("1 из 3 согласовали").length).toBeGreaterThan(0));
    expect(close).not.toHaveBeenCalled();
    // Повторную отметку бэкенд не примет (409) — кнопки больше не предлагаем.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Согласовать" })).not.toBeInTheDocument(),
    );
    clearOpener();
  });

  // Непривилегированному приходит только его логин в assignees, но счётчики
  // от бэкенда полные: прогресс «1 из 3», а ответственные — его одна строка.
  it("владелец шага: урезанный assignees не ломает прогресс", async () => {
    mockGroupMembers();
    vi.mocked(getRequest).mockResolvedValue(
      multiRequest({ assignees: ["petrov.pp"], approved_count: 1 }),
    );

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    expect(screen.getByText("1 из 3 согласовали")).toBeInTheDocument();
    expect(screen.getByText("Ответственные: Петров Пётр Петрович")).toBeInTheDocument();
    expect(screen.queryByText(/sidorova\.as/)).not.toBeInTheDocument();
  });
});

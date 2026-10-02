// Тесты карточки заявки (Задача 3): шаги, отметки владельца, действия ОК,
// печать бегунка, скан-вложения. Компонент RequestCard используется в
// окне-попе (?view=request&id=…). Данные — из requests-client (мокается).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { RequestCard } from "./request-card";
import {
  decideStep,
  deleteRequest,
  finishRequest,
  getAttachments,
  getRequest,
  printRequest,
  submitRequest,
  toExecution,
  uploadAttachment,
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
    deleteRequest: vi.fn(),
    getAttachments: vi.fn(),
    uploadAttachment: vi.fn(),
    printRequest: vi.fn(),
  };
});

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
  vi.mocked(deleteRequest).mockReset();
  vi.mocked(getAttachments).mockReset();
  vi.mocked(uploadAttachment).mockReset();
  vi.mocked(printRequest).mockReset();
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

  // Карточка показывает шаги выбранной заявки.
  it("карточка показывает шаги заявки", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        { order: 1, owner_group: "SED_STEP_BUH", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
        { order: 2, owner_group: "SED_STEP_OK", resolver: "by_group", can_act: false, status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getByLabelText("Шаги заявки"));
    expect(steps.getByText("SED_STEP_BUH")).toBeInTheDocument();
    expect(steps.getByText("SED_STEP_OK")).toBeInTheDocument();
  });

  // Шаги с блоками: order кодирует блок/режим — «№» через stepLabel;
  // персональный исполнитель — ФИО (owner_name), логин AD не выводится.
  it("шаги с блоками: «№» через stepLabel, персональный исполнитель — ФИО без логина", async () => {
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
    const steps = within(screen.getByLabelText("Шаги заявки"));
    expect(steps.getByText("2.1 ‖")).toBeInTheDocument();
    expect(steps.getByText("2.2 ‖")).toBeInTheDocument();
    expect(steps.getByText("Петров Пётр Петрович")).toBeInTheDocument();
    expect(steps.getByText("SED_STEP_OK")).toBeInTheDocument();
    // Логин AD согласующего в карточке не выводится.
    expect(screen.queryByText(/petrov\.pp/)).not.toBeInTheDocument();
    // Предприятие — названием, а не кодом.
    expect(screen.getByText(/Предприятие «Пример-1»/)).toBeInTheDocument();
    expect(screen.queryByText(/ENT_PRIMER_1/)).not.toBeInTheDocument();
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
    expect(steps.getByText("SED_STEP_BUH")).toBeInTheDocument();
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

  // Скан-вложения: мета со ссылкой на скачивание.
  it("карточка показывает мета вложений со ссылкой на скачивание", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    const attachments: AttachmentMeta[] = [
      { id: "ATT-1", filename: "scan.pdf", size: 1024, created_at: "2026-09-29T10:00:00+00:00" },
    ];
    vi.mocked(getAttachments).mockResolvedValue(attachments);

    renderCard();
    await waitFor(() => expect(screen.getByText(/scan\.pdf/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Скачать" }).getAttribute("href")).toBe(
      "/api/attachments/ATT-1/file",
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
});

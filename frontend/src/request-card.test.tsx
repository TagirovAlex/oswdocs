// Тесты карточки заявки (Задача 3): шаги, отметки владельца, действия ОК,
// документы/печать, скан-вложения. Компонент RequestCard используется в
// окне-попе (?view=request&id=…). Данные — из requests-client (мокается).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "./auth-client";
import { RequestCard } from "./request-card";
import {
  decideStep,
  finishRequest,
  getAttachments,
  getDocuments,
  getRequest,
  printRequest,
  submitRequest,
  toExecution,
  uploadAttachment,
} from "./requests-client";
import type { AttachmentMeta, DocumentMeta, RequestOut } from "./requests-client";
import type { Role } from "./api-mock";

vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getDocuments: vi.fn(),
    getRequest: vi.fn(),
    decideStep: vi.fn(),
    submitRequest: vi.fn(),
    toExecution: vi.fn(),
    finishRequest: vi.fn(),
    getAttachments: vi.fn(),
    uploadAttachment: vi.fn(),
    printRequest: vi.fn(),
  };
});

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

function renderCard(requestId: string = "REQ-0001", role: Role = "hr") {
  return render(<RequestCard requestId={requestId} role={role} />);
}

beforeEach(() => {
  vi.mocked(getDocuments).mockReset();
  vi.mocked(getRequest).mockReset();
  vi.mocked(decideStep).mockReset();
  vi.mocked(submitRequest).mockReset();
  vi.mocked(toExecution).mockReset();
  vi.mocked(finishRequest).mockReset();
  vi.mocked(getAttachments).mockReset();
  vi.mocked(uploadAttachment).mockReset();
  vi.mocked(printRequest).mockReset();
  vi.mocked(getDocuments).mockResolvedValue([]);
  vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
  vi.mocked(getAttachments).mockResolvedValue([]);
});

describe("RequestCard", () => {
  // Печать бегунка: версия в статусе, запрос с requestId.
  it("печать генерирует бегунок и показывает версию", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(printRequest).mockResolvedValue({ version: "v2", generated: true, pdf_path: "/data/2.pdf", qr_payload: "q" });

    renderCard();
    await waitFor(() => expect(screen.getByRole("button", { name: "Печать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Печать" }));
    await waitFor(() => expect(screen.getByText("Бегунок v2 сгенерирован")).toBeInTheDocument());
    expect(vi.mocked(printRequest)).toHaveBeenCalledWith("REQ-0001");
  });

  // generated=false с reason (нет LibreOffice/шаблона) — статус, не ошибка.
  it("печать без LibreOffice показывает reason как статус, не как ошибку", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    vi.mocked(printRequest).mockResolvedValue({ version: "v1", generated: false, reason: "LibreOffice не настроен" });

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

  // Блок «Документы»: версии со ссылками на PDF (URL строится из id).
  it("блок «Документы» показывает версии со ссылками на PDF", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith("Громов Игорь Олегович", "На согласовании"));
    const docs: DocumentMeta[] = [
      { version: "v1", pdf_path: "/data/1.pdf", qr_payload: "q", created_at: "2026-09-28T10:00:00+00:00" },
      { version: "v2", pdf_path: "/data/2.pdf", qr_payload: "q", created_at: "2026-09-29T10:00:00+00:00" },
    ];
    vi.mocked(getDocuments).mockResolvedValue(docs);

    renderCard();
    await waitFor(() => expect(screen.getByText(/Бегунок v1/)).toBeInTheDocument());
    expect(screen.getByText(/Бегунок v2/)).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/api/documents/REQ-0001/pdf?version=v1",
      "/api/documents/REQ-0001/pdf?version=v2",
    ]);
  });

  // Карточка показывает шаги выбранной заявки.
  it("карточка показывает шаги заявки", async () => {
    vi.mocked(getRequest).mockResolvedValue({
      ...requestWith("Громов Игорь Олегович", "На согласовании"),
      steps: [
        { order: 1, owner_group: "SED_STEP_BUH", resolver: "by_group", status: "ожидает", expires_at: "2026-10-05T10:00:00+00:00" },
        { order: 2, owner_group: "SED_STEP_OK", resolver: "by_group", status: "ожидает", expires_at: "2026-10-08T10:00:00+00:00" },
      ],
    });

    renderCard();
    await waitFor(() => expect(screen.getByLabelText("Шаги заявки")).toBeInTheDocument());
    const steps = within(screen.getByLabelText("Шаги заявки"));
    expect(steps.getByText("SED_STEP_BUH")).toBeInTheDocument();
    expect(steps.getByText("SED_STEP_OK")).toBeInTheDocument();
  });

  // Отметка владельца: при отказе без комментария отметка не отправляется.
  it("владелец: при отказе без комментария отметка не отправляется", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith(null, "На согласовании"));

    renderCard("REQ-0001", "owner");
    await waitFor(() => expect(screen.getByRole("button", { name: "Отказать" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Отказать" }));
    expect(screen.getByText("При отказе/возврате комментарий обязателен")).toBeInTheDocument();
    expect(vi.mocked(decideStep)).not.toHaveBeenCalled();
  });

  // Отметка владельца: согласование с комментарием уходит в API, карточка обновляется.
  it("владелец согласовывает свой шаг с комментарием", async () => {
    vi.mocked(getRequest).mockResolvedValue(requestWith(null, "На согласовании"));
    vi.mocked(decideStep).mockResolvedValue(requestWith(null, "На согласовании"));

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
});
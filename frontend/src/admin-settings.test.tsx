// Тесты админки настроек (волна B4 / B3 Волны 2): данные — из /api/settings
// (settings-client). Сеть не нужна: модуль settings-client мокается,
// сценарии — загрузка полного объекта, сохранение, добавление/удаление
// предприятий и групп, рендер шаблонов, успех/ошибка/403.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSettings } from "./admin-settings";
import { ApiHttpError } from "./auth-client";
import { getSettings, saveSettings } from "./settings-client";
import type { SettingsData } from "./settings-client";

// Мок клиента настроек (fetch не вызывается).
vi.mock("./settings-client", () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
}));

// Настройки, как их отдаёт GET /api/settings (полный объект по контракту B2).
const settings: SettingsData = {
  session_ttl_minutes: 600,
  approval_ttl_days: 3,
  scan_retention_days: 30,
  scan_max_mb: 10,
  require_paper_signature: true,
  smtp_host: "intsrvmail.fidelio.local",
  smtp_port: 587,
  smtp_from: "sed@example.com",
  smtp_user: "",
  smtp_password: null,
  require_comment: true,
  enterprises: [{ code: "OOO_ALFA", name: "ООО Альфа" }],
  allowed_ad_groups: ["SED_Vlastelcy", "SED_HR"],
  position_to_category: { "Руководитель": "Руководители" },
  position_escalation: { "Руководитель": 48 },
  templates: [
    {
      service: "Бухгалтерия",
      category: "Увольнение",
      steps: [{ owner_group: "SED_HR" }, { owner_group: "SED_Vlastelcy", require_comment: true }],
    },
  ],
};

beforeEach(() => {
  vi.mocked(getSettings).mockReset();
  vi.mocked(saveSettings).mockReset();
});

describe("AdminSettings", () => {
  // Админ загружает настройки, правит TTL и сохраняет через PUT.
  it("админ правит TTL и сохраняет", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data: SettingsData) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("TTL отметок"), { target: { value: "7" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/TTL=7/));
    expect(saveSettings).toHaveBeenCalledWith(expect.objectContaining({ approval_ttl_days: 7 }));
  });

  // Загрузка полного объекта: видны все секции контента.
  it("загружает полный объект настроек (контент)", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);

    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());
    expect(screen.getByLabelText("Комментарий обязателен при согласовании")).toBeChecked();
    expect(screen.getByLabelText("Код предприятия 1")).toHaveValue("OOO_ALFA");
    expect(screen.getByLabelText("Название предприятия 1")).toHaveValue("ООО Альфа");
    expect(screen.getByLabelText("Группа доступа 1")).toHaveValue("SED_Vlastelcy");
    expect(screen.getByLabelText("Группа доступа 2")).toHaveValue("SED_HR");
    expect(screen.getByLabelText("Должность 1")).toHaveValue("Руководитель");
    expect(screen.getByLabelText("Категория 1")).toHaveValue("Руководители");
  });

  // Рендер шаблонов: служба, категория и шаги owner_group.
  it("рендерит шаблоны маршрутов и добавляет шаг", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);

    await waitFor(() => expect(screen.getByLabelText("Служба шаблона 1")).toBeInTheDocument());
    expect(screen.getByLabelText("Служба шаблона 1")).toHaveValue("Бухгалтерия");
    expect(screen.getByLabelText("Категория шаблона 1")).toHaveValue("Увольнение");
    expect(screen.getByLabelText("Владелец шага 1.1")).toHaveValue("SED_HR");
    expect(screen.getByLabelText("Владелец шага 1.2")).toHaveValue("SED_Vlastelcy");

    fireEvent.click(screen.getByText("Добавить шаг"));
    await waitFor(() => expect(screen.getByLabelText("Владелец шага 1.3")).toBeInTheDocument());
  });

  // Добавление предприятия и сохранение: PUT уходит с новыми данными.
  it("добавляет предприятие и сохраняет через PUT", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data: SettingsData) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("Код предприятия 1")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Добавить предприятие"));
    const code = screen.getByLabelText("Код предприятия 2");
    fireEvent.change(code, { target: { value: "OOO_BETA" } });
    fireEvent.change(screen.getByLabelText("Название предприятия 2"), { target: { value: "ООО Бета" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/предприятий 2/));
    expect(saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        enterprises: [
          { code: "OOO_ALFA", name: "ООО Альфа" },
          { code: "OOO_BETA", name: "ООО Бета" },
        ],
      }),
    );
  });

  // Удаление предприятия убирает строку из формы.
  it("удаляет предприятие", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("Код предприятия 1")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Удалить предприятие"));

    await waitFor(() => expect(screen.queryByLabelText("Код предприятия 1")).not.toBeInTheDocument());
    expect(screen.getByText("не задано")).toBeInTheDocument();
  });

  // Группы доступа: добавление и удаление строк.
  it("добавляет и удаляет группы доступа", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("Группа доступа 1")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Добавить группу"));
    await waitFor(() => expect(screen.getByLabelText("Группа доступа 3")).toBeInTheDocument());

    fireEvent.click(screen.getAllByText("Удалить группу")[0]);
    await waitFor(() => expect(screen.queryByLabelText("Группа доступа 3")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Группа доступа 1")).toHaveValue("SED_HR");
  });

  // Сбой сервера при загрузке — понятный alert.
  it("при ошибке загрузки показывает alert", async () => {
    vi.mocked(getSettings).mockRejectedValue(new Error("Сервис недоступен"));

    render(<AdminSettings role="admin" />);

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Сервис недоступен/));
  });

  // Не-админ получает 403 — alert вместо формы.
  it("не-админам доступ закрыт (403)", async () => {
    vi.mocked(getSettings).mockRejectedValue(new ApiHttpError(403, "Настройки — только админам"));

    render(<AdminSettings role="hr" />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
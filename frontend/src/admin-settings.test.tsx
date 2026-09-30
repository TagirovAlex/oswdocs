// Тесты админки настроек (волна B4 / B3 Волны 2): данные — из /api/settings
// (settings-client). Сеть не нужна: модуль settings-client мокается,
// сценарии — загрузка полного объекта, сохранение, добавление/удаление
// предприятий и групп, рендер шаблонов, успех/ошибка/403.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSettings } from "./admin-settings";
import { ApiHttpError } from "./auth-client";
import { getSettings, getSettingsContent, saveSettings, saveSettingsContent, syncEnterprises } from "./settings-client";
import type { SettingsData } from "./settings-client";

// Мок клиента настроек (fetch не вызывается).
vi.mock("./settings-client", () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getSettingsContent: vi.fn(),
  saveSettingsContent: vi.fn(),
  syncEnterprises: vi.fn(),
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
  doc_templates: [
    { service: "Бухгалтерия", category: "Увольнение", body: "Бегунок: {{ fio }}" },
  ],
  mail_templates: [{ code: "assigned", subject: "Заявка {{ request_id }}", body_html: "<html>{{ fio }}</html>" }],
  onec_bases: [],
};

// Контентная часть (как отдаёт GET /api/settings/content для руководителя ОК).
const contentOnly: SettingsData = {
  session_ttl_minutes: null,
  approval_ttl_days: 3,
  scan_retention_days: null,
  scan_max_mb: null,
  require_paper_signature: true,
  smtp_host: null,
  smtp_port: null,
  smtp_from: null,
  smtp_user: null,
  smtp_password: null,
  require_comment: true,
  enterprises: [{ code: "OOO_ALFA", name: "ООО Альфа" }],
  allowed_ad_groups: ["SED_HR"],
  position_to_category: { "Руководитель": "Руководители" },
  position_escalation: {},
  templates: [],
  doc_templates: [],
  mail_templates: [],
  onec_bases: [],
};

beforeEach(() => {
  vi.mocked(getSettings).mockReset();
  vi.mocked(saveSettings).mockReset();
  vi.mocked(getSettingsContent).mockReset();
  vi.mocked(saveSettingsContent).mockReset();
  vi.mocked(syncEnterprises).mockReset();
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

  // Загрузка полного объекта: видны все секции контента (после перехода на вкладку).
  it("загружает полный объект настроек (контент)", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);

    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());
    expect(screen.getByLabelText("Комментарий обязателен при согласовании")).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    await waitFor(() => expect(screen.getByLabelText("Код предприятия 1")).toBeInTheDocument());
    expect(screen.getByLabelText("Код предприятия 1")).toHaveValue("OOO_ALFA");
    expect(screen.getByLabelText("Название предприятия 1")).toHaveValue("ООО Альфа");
    expect(screen.getByLabelText("Группа доступа 1")).toHaveValue("SED_Vlastelcy");
    expect(screen.getByLabelText("Группа доступа 2")).toHaveValue("SED_HR");
    expect(screen.getByLabelText("Должность 1")).toHaveValue("Руководитель");
    expect(screen.getByLabelText("Категория 1")).toHaveValue("Руководители");
  });

  // Рендер шаблонов: служба, категория и шаги owner_group (вкладка «Шаблоны»).
  it("рендерит шаблоны маршрутов и добавляет шаг", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
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
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
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
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    await waitFor(() => expect(screen.getByLabelText("Код предприятия 1")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Удалить предприятие"));

    await waitFor(() => expect(screen.queryByLabelText("Код предприятия 1")).not.toBeInTheDocument());
    expect(screen.getByText("не задано")).toBeInTheDocument();
  });

  // Группы доступа: добавление и удаление строк.
  it("добавляет и удаляет группы доступа", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
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

  // Не-админ получает 403 — alert вместо формы (для hr компонент грузит content).
  it("не-админам доступ закрыт (403)", async () => {
    vi.mocked(getSettingsContent).mockRejectedValue(new ApiHttpError(403, "Настройки — только админам"));

    render(<AdminSettings role="hr" />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  // Руководитель ОК: только контент-вкладки, загрузка/сохранение через /settings/content.
  it("руководитель ОК видит только контент и сохраняет через content", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);
    vi.mocked(saveSettingsContent).mockImplementation(async (data) => data);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());

    // Инфра-вкладки и инфра-поля недоступны руководителю ОК.
    expect(screen.queryByRole("button", { name: "Инфра" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Длительность сессии")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("TTL отметок"), { target: { value: "7" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/TTL=7/));
    expect(saveSettingsContent).toHaveBeenCalledWith(expect.objectContaining({ approval_ttl_days: 7 }));
    expect(saveSettings).not.toHaveBeenCalled();
  });

  // Админ: видит вкладку «Инфра» и грузит полный объект через /api/settings.
  it("админ видит вкладку Инфра и сохраняет полный объект", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Инфра" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Инфра" }));
    await waitFor(() => expect(screen.getByLabelText("Длительность сессии")).toBeInTheDocument());
    expect(screen.getByLabelText("Длительность сессии")).toHaveValue(600);
    expect(screen.getByLabelText("Хост SMTP-релея")).toHaveValue("intsrvmail.fidelio.local");
  });

  // Админ: на вкладке «Инфра» видит 1С-базы, добавляет базу и сохраняет (PUT с onec_bases).
  it("добавляет базу 1С и сохраняет через PUT", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Инфра" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Инфра" }));
    await waitFor(() => expect(screen.getByText("1С-базы (подключения)")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Добавить базу 1С"));
    fireEvent.change(screen.getByLabelText("Код базы 1С 1"), { target: { value: "zup_t1" } });
    fireEvent.change(screen.getByLabelText("Название базы 1С 1"), { target: { value: "База ЗУП" } });
    fireEvent.change(screen.getByLabelText("OData URL базы 1С 1"), {
      target: { value: "http://1c-mock.local/zup/odata/standard.odata/" },
    });
    fireEvent.change(screen.getByLabelText("УЗ базы 1С 1"), { target: { value: "reader" } });
    fireEvent.change(screen.getByLabelText("Пароль базы 1С 1"), { target: { value: "secret-1" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          onec_bases: [
            {
              code: "zup_t1",
              name: "База ЗУП",
              url: "http://1c-mock.local/zup/odata/standard.odata/",
              user: "reader",
              password: "secret-1",
              employee_entity: "Catalog_СотрудникиОрганизаций",
              organization_entity: "Catalog_Организации",
              tab_num_field: "ТабельныйНомер",
              fio_field: "Сотрудник/Description",
              department_field: "Подразделение",
              position_field: "Должность",
              hire_date_field: "ДатаПриема",
              organization_code_field: "Ref_Key",
              organization_name_field: "Description",
            },
          ],
        }),
      ),
    );
  });

  // Админ: на вкладке «Инфра» кнопка «Обновить из 1С» синхронизирует предприятия из баз.
  it("обновляет предприятия из 1С по кнопке", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(syncEnterprises).mockResolvedValue({ synced: true, count: 3, enterprises: [] });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Инфра" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Инфра" }));
    await waitFor(() => expect(screen.getByText("1С-базы (подключения)")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Обновить из 1С"));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/Обновлено: 3 предприятий/));
    expect(syncEnterprises).toHaveBeenCalledTimes(1);
  });

  // Админ: на вкладке «Шаблоны» видит редакторы бланков/писем, добавляет и сохраняет.
  it("админ видит редакторы бланков/писем и сохраняет их", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("Бланки бегунков (doc_templates)")).toBeInTheDocument());
    expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument();
    // Существующие бланк и письмо загружены из настроек.
    expect(screen.getByLabelText("Служба бланка 1")).toHaveValue("Бухгалтерия");
    expect(screen.getByLabelText("Код письма 1")).toHaveValue("assigned");

    fireEvent.click(screen.getByText("Добавить бланк"));
    fireEvent.change(screen.getByLabelText("Служба бланка 2"), { target: { value: "Служба-2" } });
    fireEvent.change(screen.getByLabelText("Категория бланка 2"), { target: { value: "линейный" } });
    fireEvent.change(screen.getByLabelText("Тело бланка 2"), { target: { value: "Бегунок 2: {{ fio }}" } });

    fireEvent.click(screen.getByText("Добавить письмо"));
    fireEvent.change(screen.getByLabelText("Код письма 2"), { target: { value: "reminder" } });
    fireEvent.change(screen.getByLabelText("Тема письма 2"), { target: { value: "Напоминание" } });
    fireEvent.change(screen.getByLabelText("HTML письма 2"), { target: { value: "<html>напоминание</html>" } });

    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          doc_templates: [
            { service: "Бухгалтерия", category: "Увольнение", body: "Бегунок: {{ fio }}" },
            { service: "Служба-2", category: "линейный", body: "Бегунок 2: {{ fio }}" },
          ],
          mail_templates: [
            { code: "assigned", subject: "Заявка {{ request_id }}", body_html: "<html>{{ fio }}</html>" },
            { code: "reminder", subject: "Напоминание", body_html: "<html>напоминание</html>" },
          ],
        }),
      ),
    );
  });

  // Руководитель ОК: редакторы бланков/писем на «Шаблонах», сохранение через content.
  it("руководитель ОК редактирует бланки/письма и сохраняет через content", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);
    vi.mocked(saveSettingsContent).mockImplementation(async (data) => data);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("Бланки бегунков (doc_templates)")).toBeInTheDocument());
    expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument();
    // contentOnly: бланков/писем нет — редакторы пустые, но доступны.

    fireEvent.click(screen.getByText("Добавить бланк"));
    fireEvent.change(screen.getByLabelText("Служба бланка 1"), { target: { value: "Служба" } });
    fireEvent.change(screen.getByLabelText("Категория бланка 1"), { target: { value: "линейный" } });
    fireEvent.change(screen.getByLabelText("Тело бланка 1"), { target: { value: "Бегунок: {{ fio }}" } });

    fireEvent.click(screen.getByText("Добавить письмо"));
    fireEvent.change(screen.getByLabelText("Код письма 1"), { target: { value: "assigned" } });
    fireEvent.change(screen.getByLabelText("Тема письма 1"), { target: { value: "Заявка" } });
    fireEvent.change(screen.getByLabelText("HTML письма 1"), { target: { value: "<html>заявка</html>" } });

    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettingsContent).toHaveBeenCalledWith(
        expect.objectContaining({
          doc_templates: [{ service: "Служба", category: "линейный", body: "Бегунок: {{ fio }}" }],
          mail_templates: [{ code: "assigned", subject: "Заявка", body_html: "<html>заявка</html>" }],
        }),
      ),
    );
    expect(saveSettings).not.toHaveBeenCalled();
  });
});
// Тесты админки настроек (волна B4 / B3 Волны 2): данные — из /api/settings
// (settings-client). Сеть не нужна: модуль settings-client мокается,
// сценарии — загрузка полного объекта, сохранение, добавление/удаление
// предприятий и групп, рендер шаблонов, успех/ошибка/403.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSettings } from "./admin-settings";
import { ApiHttpError } from "./auth-client";
import {
  deleteBackup,
  deleteDocTemplateFile,
  downloadBackup,
  downloadDocTemplateFile,
  getArchiveSettings,
  getSettings,
  getSettingsContent,
  listBackups,
  previewDocTemplateFile,
  runBackup,
  saveArchiveSettings,
  saveSettings,
  saveSettingsContent,
  syncEnterprises,
  uploadDocTemplateFile,
} from "./settings-client";
import type { SettingsData } from "./settings-client";

// Мок клиента настроек (fetch не вызывается).
vi.mock("./settings-client", () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getSettingsContent: vi.fn(),
  saveSettingsContent: vi.fn(),
  syncEnterprises: vi.fn(),
  getArchiveSettings: vi.fn(),
  saveArchiveSettings: vi.fn(),
  listBackups: vi.fn(),
  runBackup: vi.fn(),
  deleteBackup: vi.fn(),
  downloadBackup: vi.fn(),
  uploadDocTemplateFile: vi.fn(),
  downloadDocTemplateFile: vi.fn(),
  deleteDocTemplateFile: vi.fn(),
  previewDocTemplateFile: vi.fn(),
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
  onec_enterprises_synced_at: "2026-09-30T14:00:00+00:00",
  schedule_enterprises_sync: {
    mode: "interval",
    interval_hours: 3,
    notify: true,
    subject: "Синхронизация выполнена",
    body: "Сводка: {{summary}}",
    recipients: ["adm.petrov@example.com"],
  },
  schedule_ad_links_sync: null,
  // Группы входа и ролей (списки, как в GET /settings) — вымышленные значения теста.
  access_groups: ["TEST_VKHOD", "TEST_VKHOD_2"],
  admin_groups: ["TEST_ADMINS"],
  hr_groups: ["TEST_OK"],
  hr_admin_groups: ["TEST_RUK_OK"],
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
  onec_enterprises_synced_at: null,
  // Групп входа и ролей у руководителя ОК нет: ключи инфра-раздела (только админ).
  access_groups: null,
  admin_groups: null,
  hr_groups: null,
  hr_admin_groups: null,
};

// Настройки с бланком на .docx-файле (file задан): редактор в режиме файла.
const withFileSettings: SettingsData = {
  ...settings,
  doc_templates: [{ service: "Бухгалтерия", category: "Увольнение", body: "", file: "bланк_v1.docx" }],
};

beforeEach(() => {
  vi.mocked(getSettings).mockReset();
  vi.mocked(saveSettings).mockReset();
  vi.mocked(getSettingsContent).mockReset();
  vi.mocked(saveSettingsContent).mockReset();
  vi.mocked(syncEnterprises).mockReset();
  vi.mocked(getArchiveSettings).mockReset();
  vi.mocked(saveArchiveSettings).mockReset();
  vi.mocked(listBackups).mockReset();
  vi.mocked(runBackup).mockReset();
  vi.mocked(deleteBackup).mockReset();
  vi.mocked(downloadBackup).mockReset();
  vi.mocked(uploadDocTemplateFile).mockReset();
  vi.mocked(downloadDocTemplateFile).mockReset();
  vi.mocked(deleteDocTemplateFile).mockReset();
  vi.mocked(previewDocTemplateFile).mockReset();
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
    expect(screen.getByLabelText("ID группы 1")).toHaveValue("SED_Vlastelcy");
    expect(screen.getByLabelText("Наименование группы 1")).toHaveValue("SED_Vlastelcy");
    expect(screen.getByLabelText("ID группы 2")).toHaveValue("SED_HR");
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

  // Группы доступа: добавление и удаление строк (ID + наименование).
  it("добавляет и удаляет группы доступа", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    // Старый формат (строки) читается как id=name.
    await waitFor(() => expect(screen.getByLabelText("ID группы 1")).toBeInTheDocument());
    expect(screen.getByLabelText("ID группы 1")).toHaveValue("SED_Vlastelcy");
    expect(screen.getByLabelText("Наименование группы 1")).toHaveValue("SED_Vlastelcy");

    fireEvent.click(screen.getByText("Добавить группу"));
    await waitFor(() => expect(screen.getByLabelText("ID группы 3")).toBeInTheDocument());

    fireEvent.click(screen.getAllByText("Удалить группу")[0]);
    await waitFor(() => expect(screen.queryByLabelText("ID группы 3")).not.toBeInTheDocument());
    expect(screen.getByLabelText("ID группы 1")).toHaveValue("SED_HR");
  });

  // Группы доступа: наименование сохраняется и уходит в PUT объектами {id, name}.
  it("сохраняет наименование группы объектами", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    await waitFor(() => expect(screen.getByLabelText("ID группы 1")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Наименование группы 1"), {
      target: { value: "Владельцы (вымышленные)" },
    });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(vi.mocked(saveSettings)).toHaveBeenCalled());
    const sent = vi.mocked(saveSettings).mock.calls[0][0];
    expect(sent.allowed_ad_groups).toContainEqual({ id: "SED_Vlastelcy", name: "Владельцы (вымышленные)" });
    expect(sent.allowed_ad_groups).toContainEqual({ id: "SED_HR", name: "SED_HR" });
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
              employee_entity: "Catalog_Сотрудники",
              organization_entity: "Catalog_Организации",
              employee_org_field: "ГоловнаяОрганизация_Key",
              tab_num_field: "Code",
              fio_field: "Description",
              department_field: "ТекущееПодразделение/Description",
              position_field: "ТекущаяДолжность/Description",
              hire_date_field: "ДатаПриема",
              termination_date_field: "ДатаУвольнения",
              hr_entity: "InformationRegister_ТекущиеКадровыеДанныеСотрудников",
              hr_employee_field: "Сотрудник_Key",
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

  // Админ: вкладка «Регламенты» видна только админу, расписания грузятся из
  // настроек и сохраняются через общий PUT /settings (поля schedule_*).
  it("админ видит вкладку Регламенты и сохраняет расписания через PUT", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data: SettingsData) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Регламенты" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Регламенты" }));
    await waitFor(() => expect(screen.getByText("Синхронизация предприятий из 1С")).toBeInTheDocument());
    expect(screen.getByText("Автосвязка 1С↔AD")).toBeInTheDocument();

    // Загруженное расписание предприятий видно в форме (mode/интервал).
    expect(screen.getByLabelText("Режим расписания предприятий")).toHaveValue("interval");
    expect(screen.getByLabelText("Интервал часов предприятий")).toHaveValue(3);

    // Изменяем интервал и сохраняем: PUT уходит с расписаниями schedule_*.
    fireEvent.change(screen.getByLabelText("Интервал часов предприятий"), { target: { value: "6" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          schedule_enterprises_sync: expect.objectContaining({ mode: "interval", interval_hours: 6 }),
          schedule_ad_links_sync: null,
        }),
      ),
    );
  });

  // Руководитель ОК: вкладки «Регламенты» нет (инфра — только админ).
  it("руководитель ОК не видит вкладку Регламенты", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: "Регламенты" })).not.toBeInTheDocument();
  });

  // Админ: вкладка «Доступ и роли» видна только админу; списки из GET /settings
  // склеиваются в текст формы, правка уходит в общий PUT /settings списком.
  it("админ правит группы входа и роли и сохраняет через PUT", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data: SettingsData) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Доступ и роли" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Доступ и роли" }));
    await waitFor(() => expect(screen.getByLabelText("Группы для входа")).toBeInTheDocument());

    // Загрузка: список групп из GET показан одной строкой через запятую.
    expect(screen.getByLabelText("Группы для входа")).toHaveValue("TEST_VKHOD, TEST_VKHOD_2");
    expect(screen.getByLabelText("Группы администраторов")).toHaveValue("TEST_ADMINS");
    expect(screen.getByLabelText("Группы сотрудников ОК")).toHaveValue("TEST_OK");
    expect(screen.getByLabelText("Группы руководителей ОК")).toHaveValue("TEST_RUK_OK");

    // Сохранение: текст режется на список (trim, пустые отброшены), пустой ввод →
    // null (сброс ключа в БД, фолбэк env).
    fireEvent.change(screen.getByLabelText("Группы администраторов"), {
      target: { value: "TEST_ADMINS, TEST_ADMINS_2 ,, " },
    });
    fireEvent.change(screen.getByLabelText("Группы для входа"), { target: { value: "" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        access_groups: null,
        admin_groups: ["TEST_ADMINS", "TEST_ADMINS_2"],
        hr_groups: ["TEST_OK"],
        hr_admin_groups: ["TEST_RUK_OK"],
      }),
    );
    expect(saveSettingsContent).not.toHaveBeenCalled();
  });

  // Руководитель ОК: вкладки «Доступ и роли» нет (группы ролей правит админ).
  it("руководитель ОК не видит вкладку Доступ и роли", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: "Доступ и роли" })).not.toBeInTheDocument();
  });

  // Админ: вкладка «Архивация» — размер в МБ (меньше 1 КБ — в КБ), кнопки
  // «Скачать» (новая вкладка) и «Удалить» (подтверждение + перечитать список).
  it("вкладка Архивация: размер в МБ, скачивание и удаление бэкапа", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(getArchiveSettings).mockResolvedValue({
      storage_path: "/backups",
      file_pattern: "sed_{ts}.dump",
      keep_copies: 10,
      schedule: null,
    });
    vi.mocked(listBackups).mockResolvedValue([
      { name: "sed_2026-10-01.dump", size: 5242880, created_at: "2026-10-01T12:00:00+00:00" },
      { name: "small.dump", size: 500, created_at: "2026-10-01T12:00:00+00:00" },
    ]);
    vi.mocked(downloadBackup).mockResolvedValue(undefined);
    vi.mocked(deleteBackup).mockResolvedValue({ ok: true });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Архивация" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Архивация" }));
    await waitFor(() => expect(screen.getByText("sed_2026-10-01.dump")).toBeInTheDocument());

    // Размер — в мегабайтах (1 знак после запятой); меньше 1 КБ — в килобайтах.
    expect(screen.getByText("5.0 МБ")).toBeInTheDocument();
    expect(screen.getByText("0.5 КБ")).toBeInTheDocument();

    // Дата и время бэкапа (ru-RU, часы:минуты) — по локальному времени браузера.
    const expectedWhen = new Date("2026-10-01T12:00:00+00:00").toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    expect(screen.getAllByText(expectedWhen).length).toBe(2);
    // Время есть — значит показывается не одна только дата.
    expect(screen.queryByText("2026-10-01")).not.toBeInTheDocument();

    // «Скачать» — blob-скачивание с токеном.
    fireEvent.click(screen.getByRole("button", { name: "Скачать бэкап sed_2026-10-01.dump" }));
    expect(downloadBackup).toHaveBeenCalledWith("sed_2026-10-01.dump");

    // «Удалить»: подтверждение, DELETE и перечитывание списка (после удаления пусто).
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(listBackups).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Удалить бэкап sed_2026-10-01.dump" }));
    await waitFor(() => expect(screen.getByText("Бэкапов нет")).toBeInTheDocument());
    expect(deleteBackup).toHaveBeenCalledWith("sed_2026-10-01.dump");
    confirm.mockRestore();
  });

  // Задача H: бланк без file — textarea-фолбэк и кнопка «Загрузить .docx».
  it("бланк без файла: textarea-фолбэк и кнопка «Загрузить .docx»", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByLabelText("Тело бланка 1")).toBeInTheDocument());
    expect(screen.getByLabelText("Тело бланка 1")).toHaveValue("Бегунок: {{ fio }}");
    expect(screen.getByText("Загрузить .docx")).toBeInTheDocument();
  });

  // Задача H: бланк с file — имя файла и файловые операции, textarea скрыта.
  it("бланк с файлом: имя файла и кнопки операций, textarea скрыта", async () => {
    vi.mocked(getSettings).mockResolvedValue(withFileSettings);
    vi.mocked(downloadDocTemplateFile).mockResolvedValue(undefined);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("bланк_v1.docx")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Скачать файл бланка 1" })).toBeInTheDocument();
    expect(screen.getByText("Заменить")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Удалить файл бланка 1" })).toBeInTheDocument();
    expect(screen.getByText("Предпросмотр")).toBeInTheDocument();
    expect(screen.queryByLabelText("Тело бланка 1")).not.toBeInTheDocument();

    // «Скачать» — вызов downloadDocTemplateFile с именем файла.
    fireEvent.click(screen.getByRole("button", { name: "Скачать файл бланка 1" }));
    expect(downloadDocTemplateFile).toHaveBeenCalledWith("bланк_v1.docx");
  });

  // Задача H: новая загрузка .docx без previous → uploadDocTemplateFile(file).
  it("загрузка .docx: вызов без previous, имя файла появляется в карточке", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(uploadDocTemplateFile).mockResolvedValue({ name: "bланк.docx" });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByLabelText("Загрузить файл бланка 1")).toBeInTheDocument());

    const file = new File(["docx"], "bланк.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(screen.getByLabelText("Загрузить файл бланка 1"), { target: { files: [file] } });

    await waitFor(() => expect(uploadDocTemplateFile).toHaveBeenCalledWith(file, undefined));
    // После успеха бланк переключается в режим файла (textarea скрыта).
    await waitFor(() => expect(screen.getByText("bланк.docx")).toBeInTheDocument());
    expect(screen.queryByLabelText("Тело бланка 1")).not.toBeInTheDocument();
  });

  // Задача H: замена файла → uploadDocTemplateFile с previous=текущий file.
  it("замена файла: uploadDocTemplateFile с previous", async () => {
    vi.mocked(getSettings).mockResolvedValue(withFileSettings);
    vi.mocked(uploadDocTemplateFile).mockResolvedValue({ name: "bланк_v2.docx" });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("bланк_v1.docx")).toBeInTheDocument());

    const file = new File(["docx"], "bланк_v2.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(screen.getByLabelText("Заменить файл бланка 1"), { target: { files: [file] } });

    await waitFor(() => expect(uploadDocTemplateFile).toHaveBeenCalledWith(file, "bланк_v1.docx"));
    await waitFor(() => expect(screen.getByText("bланк_v2.docx")).toBeInTheDocument());
  });

  // Задача H: удаление файла с подтверждением → DELETE, file сбрасывается в null.
  it("удаление файла бланка: подтверждение, DELETE и возврат к textarea", async () => {
    vi.mocked(getSettings).mockResolvedValue(withFileSettings);
    vi.mocked(deleteDocTemplateFile).mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("bланк_v1.docx")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Удалить файл бланка 1" }));
    await waitFor(() => expect(deleteDocTemplateFile).toHaveBeenCalledWith("bланк_v1.docx"));
    // После удаления file=null — снова текстовый фолбэк.
    await waitFor(() => expect(screen.getByLabelText("Тело бланка 1")).toBeInTheDocument());
    expect(screen.queryByText("bланк_v1.docx")).not.toBeInTheDocument();
    confirm.mockRestore();
  });

  // Задача H: предпросмотр — при generated=true PDF открывается в новом окне.
  it("предпросмотр бланка открывает PDF в новом окне", async () => {
    vi.mocked(getSettings).mockResolvedValue(withFileSettings);
    vi.mocked(previewDocTemplateFile).mockResolvedValue({ generated: true, pdf_b64: "AAAA", reason: null });
    // jsdom не реализует URL.createObjectURL — стаб возвращает blob-адрес.
    const createObjectURL = vi.fn(() => "blob:mock-pdf");
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    const open = vi.spyOn(window, "open").mockReturnValue(null);

    try {
      render(<AdminSettings role="admin" />);
      await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
      fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
      await waitFor(() => expect(screen.getByText("bланк_v1.docx")).toBeInTheDocument());

      fireEvent.click(screen.getByText("Предпросмотр"));
      await waitFor(() => expect(previewDocTemplateFile).toHaveBeenCalledWith("bланк_v1.docx"));
      expect(createObjectURL).toHaveBeenCalled();
      expect(open).toHaveBeenCalledWith("blob:mock-pdf", "_blank");
    } finally {
      delete (URL as { createObjectURL?: unknown }).createObjectURL;
      open.mockRestore();
    }
  });

  // Задача H: предпросмотр — generated=false с reason показывает причину.
  it("предпросмотр при generated=false показывает reason", async () => {
    vi.mocked(getSettings).mockResolvedValue(withFileSettings);
    vi.mocked(previewDocTemplateFile).mockResolvedValue({
      generated: false,
      pdf_b64: null,
      reason: "LibreOffice не настроен",
    });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("bланк_v1.docx")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Предпросмотр"));
    await waitFor(() => expect(screen.getByText("LibreOffice не настроен")).toBeInTheDocument());
  });
});

// Тесты админки настроек (волна B4 / B3 Волны 2): данные — из /api/settings
// (settings-client) и справочников бланков (requests-client). Сеть не нужна:
// модули settings-client и requests-client мокаются, сценарии — загрузка полного
// объекта, сохранение, добавление/удаление предприятий и групп, рендер шаблонов,
// справочник бланков с визуальным редактором текста этапа, успех/ошибка/403.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSettings } from "./admin-settings";
import { ApiHttpError } from "./auth-client";
import {
  createBlank,
  getBlankSteps,
  getBlanks,
  getDocTypes,
  getRoutingCatalogs,
  setBlankSteps,
  updateBlank,
  updateRoutingStage,
} from "./requests-client";
import {
  deleteBackup,
  downloadBackup,
  getArchiveSettings,
  getSettings,
  getSettingsContent,
  listBackups,
  runBackup,
  saveArchiveSettings,
  saveSettings,
  saveSettingsContent,
  syncAdGroups,
  syncEnterprises,
} from "./settings-client";
import type { SettingsData } from "./settings-client";

// Мок клиента настроек (fetch не вызывается).
vi.mock("./settings-client", () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getSettingsContent: vi.fn(),
  saveSettingsContent: vi.fn(),
  syncEnterprises: vi.fn(),
  syncAdGroups: vi.fn(),
  getArchiveSettings: vi.fn(),
  saveArchiveSettings: vi.fn(),
  listBackups: vi.fn(),
  runBackup: vi.fn(),
  deleteBackup: vi.fn(),
  downloadBackup: vi.fn(),
}));

// Мок клиента заявок: нужны только методы справочника бланков и виды документов
// (остальное — реальное, чтобы пустые вызовы fetch не шумели).
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getBlanks: vi.fn(),
    createBlank: vi.fn(),
    updateBlank: vi.fn(),
    getBlankSteps: vi.fn(),
    setBlankSteps: vi.fn(),
    getRoutingCatalogs: vi.fn(),
    updateRoutingStage: vi.fn(),
    getDocTypes: vi.fn(),
  };
});

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
  mail_templates: [],
  onec_bases: [],
  onec_enterprises_synced_at: null,
  // Групп входа и ролей у руководителя ОК нет: ключи инфра-раздела (только админ).
  access_groups: null,
  admin_groups: null,
  hr_groups: null,
  hr_admin_groups: null,
};

// Вымышленные бланки справочника (GET /api/settings/routing/blanks) и состав
// шагов одного из них (GET .../blanks/{id}/steps).
const blanks = [
  {
    id: 10,
    code: "uvol_base",
    name: "Увольнение (базовый)",
    doc_type_code: "uvol",
    description: "Пояснение для сотрудника ОК",
    layout: "office",
    active: true,
    version: 3,
    step_count: 2,
  },
  {
    id: 11,
    code: "uvol_line",
    name: "Увольнение (линейный)",
    doc_type_code: null,
    description: null,
    layout: "line",
    active: false,
    version: 1,
    step_count: 0,
  },
];

const blankSteps = [
  {
    blank_id: 10,
    stage_id: 1,
    step_order: 1,
    optional_override: null,
    require_comment_override: null,
    stage_code: "rukovoditel",
    title: "Непосредственный руководитель",
    stage_lines: ["Ознакомить с приказом"],
    owner_kind: "manager_ad",
    owner_group: null,
    optional: false,
    print_assignee: true,
    require_comment: false,
    stage_active: true,
  },
  {
    blank_id: 10,
    stage_id: 2,
    step_order: 2,
    optional_override: true,
    require_comment_override: true,
    stage_code: "buhgalteriya",
    title: "Бухгалтерия",
    stage_lines: [],
    owner_kind: "ad_group",
    owner_group: "SED_STEP_BUH",
    optional: true,
    print_assignee: false,
    require_comment: false,
    stage_active: true,
  },
];

// Справочник этапов для добавления в состав бланка (только активные).
const catalogs = {
  stages: [
    {
      id: 1,
      code: "rukovoditel",
      title: "Непосредственный руководитель",
      owner_kind: "manager_ad",
      owner_group: null,
      optional: false,
      active: true,
    },
    {
      id: 2,
      code: "buhgalteriya",
      title: "Бухгалтерия",
      owner_kind: "ad_group",
      owner_group: "SED_STEP_BUH",
      optional: true,
      active: true,
    },
    {
      id: 3,
      code: "sluzhba_ok",
      title: "Служба ОК",
      owner_kind: "ad_group",
      owner_group: "SED_STEP_OK",
      optional: true,
      active: true,
    },
  ],
};

beforeEach(() => {
  vi.mocked(getSettings).mockReset();
  vi.mocked(saveSettings).mockReset();
  vi.mocked(getSettingsContent).mockReset();
  vi.mocked(saveSettingsContent).mockReset();
  vi.mocked(syncEnterprises).mockReset();
  vi.mocked(syncAdGroups).mockReset();
  vi.mocked(getArchiveSettings).mockReset();
  vi.mocked(saveArchiveSettings).mockReset();
  vi.mocked(listBackups).mockReset();
  vi.mocked(runBackup).mockReset();
  vi.mocked(deleteBackup).mockReset();
  vi.mocked(downloadBackup).mockReset();
  vi.mocked(getBlanks).mockReset();
  vi.mocked(createBlank).mockReset();
  vi.mocked(updateBlank).mockReset();
  vi.mocked(getBlankSteps).mockReset();
  vi.mocked(setBlankSteps).mockReset();
  vi.mocked(getRoutingCatalogs).mockReset();
  vi.mocked(updateRoutingStage).mockReset();
  vi.mocked(getDocTypes).mockReset();
  // Виды документов (doc_types) — пустой справочник по умолчанию.
  vi.mocked(getDocTypes).mockResolvedValue([
    { code: "uvol", name: "Увольнение", is_active: true, sort_order: 1 },
  ]);
  // Справочники бланков по умолчанию: один бланк с двумя шагами, этапы и вид
  // документа (тесты без бланков переопределяют getBlanks пустым списком).
  vi.mocked(getBlanks).mockResolvedValue(blanks);
  vi.mocked(getBlankSteps).mockResolvedValue(blankSteps);
  vi.mocked(getRoutingCatalogs).mockResolvedValue(catalogs);
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

  // Группы доступа: таблица с подписанными колонками ID и наименования.
  it("справочник групп — таблица с подписями колонок", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    const table = await screen.findByRole("table", { name: "Группы доступа" });
    expect(within(table).getByText("ID группы AD")).toBeInTheDocument();
    expect(within(table).getByText("Наименование")).toBeInTheDocument();
    expect(within(table).getByText("Действие")).toBeInTheDocument();
  });

  // Ручной синк состава групп: кнопка вызывает syncAdGroups и показывает итог.
  it("обновляет состав групп из AD по кнопке", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(syncAdGroups).mockResolvedValue({ synced_groups: 2, members: 5, titles: 3, errors: [] });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    fireEvent.click(screen.getByRole("button", { name: "Обновить состав групп из AD" }));
    await waitFor(() =>
      expect(screen.getByText("Состав обновлён: групп 2, участников 5, должностей в справочнике 3")).toBeInTheDocument(),
    );
    expect(syncAdGroups).toHaveBeenCalledTimes(1);
  });

  // Ручной синк состава групп: ошибка API — понятный текст.
  it("ошибка синка состава групп показывает текст", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(syncAdGroups).mockRejectedValue(new ApiHttpError(503, "AD или справочник групп недоступен"));

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Справочники" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    fireEvent.click(screen.getByRole("button", { name: "Обновить состав групп из AD" }));
    await waitFor(() =>
      expect(screen.getByText("AD или справочник групп недоступен")).toBeInTheDocument(),
    );
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

  // Админ: на вкладке «Шаблоны» редактор маршрутов и писем; бланков бегунков
  // (doc_templates) в UI больше нет — печать собирается из данных бланка.
  it("админ правит шаблоны маршрутов и письма, бланков бегунков нет", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument());
    // Редактор бланков бегунков и наборов должностей удалён.
    expect(screen.queryByText("Бланки бегунков (doc_templates)")).not.toBeInTheDocument();
    expect(screen.queryByText("Наборы должностей (для бланков)")).not.toBeInTheDocument();
    // Существующие шаблон маршрута и письмо загружены из настроек.
    expect(screen.getByLabelText("Служба шаблона 1")).toHaveValue("Бухгалтерия");
    expect(screen.getByLabelText("Код письма 1")).toHaveValue("assigned");

    fireEvent.click(screen.getByText("Добавить письмо"));
    fireEvent.change(screen.getByLabelText("Код письма 2"), { target: { value: "reminder" } });
    fireEvent.change(screen.getByLabelText("Тема письма 2"), { target: { value: "Напоминание" } });
    fireEvent.change(screen.getByLabelText("HTML письма 2"), { target: { value: "<html>напоминание</html>" } });

    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          mail_templates: [
            { code: "assigned", subject: "Заявка {{ request_id }}", body_html: "<html>{{ fio }}</html>" },
            { code: "reminder", subject: "Напоминание", body_html: "<html>напоминание</html>" },
          ],
        }),
      ),
    );
    // Мёртвые ключи настроек в PUT не уходят.
    const sent = vi.mocked(saveSettings).mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(sent).not.toHaveProperty("doc_templates");
    expect(sent).not.toHaveProperty("position_sets");
  });

  // Руководитель ОК: редакторы шаблонов/писем на «Шаблонах», сохранение через content.
  it("руководитель ОК редактирует шаблоны/письма и сохраняет через content", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);
    vi.mocked(saveSettingsContent).mockImplementation(async (data) => data);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument());
    // Редактор бланков бегунков убран и у руководителя ОК.
    expect(screen.queryByText("Бланки бегунков (doc_templates)")).not.toBeInTheDocument();
    // contentOnly: шаблонов/писем нет — редакторы пустые, но доступны.

    fireEvent.click(screen.getByText("Добавить шаблон"));
    fireEvent.change(screen.getByLabelText("Служба шаблона 1"), { target: { value: "Служба" } });
    fireEvent.change(screen.getByLabelText("Категория шаблона 1"), { target: { value: "Увольнение" } });

    fireEvent.click(screen.getByText("Добавить письмо"));
    fireEvent.change(screen.getByLabelText("Код письма 1"), { target: { value: "assigned" } });
    fireEvent.change(screen.getByLabelText("Тема письма 1"), { target: { value: "Заявка" } });
    fireEvent.change(screen.getByLabelText("HTML письма 1"), { target: { value: "<html>заявка</html>" } });

    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettingsContent).toHaveBeenCalledWith(
        expect.objectContaining({
          templates: [{ service: "Служба", category: "Увольнение", steps: [] }],
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

  // --- Справочник бланков (вкладка «Бланки», только админ) --------------------

  // Открытие вкладки бланков и список из справочника (код, название, макет, шаги).
  it("вкладка «Бланки»: список бланков из справочника", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));

    const table = await screen.findByRole("table", { name: "Справочник бланков" });
    expect(within(table).getByText("uvol_base")).toBeInTheDocument();
    expect(within(table).getByText("Увольнение (базовый)")).toBeInTheDocument();
    expect(within(table).getByText("office")).toBeInTheDocument();
    // Отключённый бланк виден админу и помечен как неактивный.
    expect(within(table).getByText("uvol_line")).toBeInTheDocument();
    expect(getBlanks).toHaveBeenCalled();
  });

  // Роль: руководителю ОК вкладки «Бланки» нет (справочник — админский).
  it("вкладка «Бланки» недоступна руководителю ОК", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Бланки" })).not.toBeInTheDocument();
    expect(getBlanks).not.toHaveBeenCalled();
  });

  // Создание бланка: код, название, вид документа, описание, макет, активность.
  it("создание бланка уходит в createBlank с полями карточки", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(createBlank).mockResolvedValue({ id: 12, code: "uvol_new" });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    await waitFor(() => expect(screen.getByLabelText("Код бланка")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Код бланка"), { target: { value: "uvol_new" } });
    fireEvent.change(screen.getByLabelText("Название бланка"), {
      target: { value: "Увольнение (новый)" },
    });
    fireEvent.change(screen.getByLabelText("Вид документа бланка"), { target: { value: "uvol" } });
    fireEvent.change(screen.getByLabelText("Макет печати бланка"), { target: { value: "line" } });
    fireEvent.change(screen.getByLabelText("Описание бланка"), {
      target: { value: "Пояснение для ОК" },
    });
    fireEvent.click(screen.getByText("Создать бланк"));

    await waitFor(() => expect(createBlank).toHaveBeenCalled());
    expect(createBlank).toHaveBeenCalledWith({
      code: "uvol_new",
      name: "Увольнение (новый)",
      doc_type_code: "uvol",
      description: "Пояснение для ОК",
      layout: "line",
      active: true,
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Бланк создан"));
  });

  // Правка бланка: код неизменен (read-only), остальные поля уходят в updateBlank.
  it("правка бланка: код неизменен, поля уходят в updateBlank", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(updateBlank).mockResolvedValue({ id: 10 });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Править бланк uvol_base" }));

    const code = screen.getByLabelText("Код бланка");
    expect(code).toHaveValue("uvol_base");
    expect(code).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Название бланка")).toHaveValue("Увольнение (базовый)");

    fireEvent.change(screen.getByLabelText("Название бланка"), {
      target: { value: "Увольнение (базовый, правка)" },
    });
    fireEvent.click(screen.getByLabelText("Бланк активен"));
    fireEvent.click(screen.getByText("Сохранить бланк"));

    await waitFor(() =>
      expect(updateBlank).toHaveBeenCalledWith(10, {
        name: "Увольнение (базовый, правка)",
        doc_type_code: "uvol",
        description: "Пояснение для сотрудника ОК",
        layout: "office",
        active: false,
      }),
    );
  });

  // Состав бланка: этапы из справочника, порядок кнопками «вверх/вниз»,
  // необязательный этап и обязательный комментарий, удаление шага.
  it("состав бланка: добавление этапа, порядок, флаги и удаление", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(setBlankSteps).mockResolvedValue({ blank_id: 10, count: 2 });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));

    // Состав пришёл по GET .../blanks/{id}/steps: два шага по порядку.
    await waitFor(() => expect(getBlankSteps).toHaveBeenCalledWith(10));
    const table = await screen.findByRole("table", { name: "Состав бланка uvol_base" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Непосредственный руководитель")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Бухгалтерия")).toBeInTheDocument();
    // Крайние шаги не двигаются: первый вверх, последний вниз — неактивны.
    expect(within(rows[0]).getByRole("button", { name: "Поднять шаг 1" })).toBeDisabled();
    expect(within(rows[1]).getByRole("button", { name: "Опустить шаг 2" })).toBeDisabled();

    // Шаг 1 обязательный: переопределение «как в этапе», значение показано подсказкой.
    expect(within(rows[0]).getByLabelText("Необязательность шага 1")).toHaveValue("");
    expect(within(rows[0]).getByText("в этапе: обязательный")).toBeInTheDocument();
    // Шаг 2 в бланке необязательный и с обязательным комментарием.
    expect(within(rows[1]).getByLabelText("Необязательность шага 2")).toHaveValue("true");
    expect(within(rows[1]).getByLabelText("Комментарий шага 2")).toHaveValue("true");

    // Меняем флаги первого шага и двигаем его вниз, затем удаляем второй шаг.
    fireEvent.change(within(rows[0]).getByLabelText("Необязательность шага 1"), {
      target: { value: "true" },
    });
    fireEvent.change(within(rows[0]).getByLabelText("Комментарий шага 1"), {
      target: { value: "true" },
    });
    fireEvent.click(within(rows[0]).getByRole("button", { name: "Опустить шаг 1" }));
    await waitFor(() =>
      expect(
        within(
          within(screen.getByRole("table", { name: "Состав бланка uvol_base" })).getAllByRole("row")[2],
        ).getByText("Непосредственный руководитель"),
      ).toBeInTheDocument(),
    );
    // Удаляем второй шаг (после перестановки это руководитель).
    fireEvent.click(screen.getByRole("button", { name: "Удалить шаг 2" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Удалить шаг 2" })).toBeNull());

    // Этап, уже добавленный в состав, в селекте добавления не предлагается.
    const add = screen.getByLabelText("Этап для добавления в бланк") as HTMLSelectElement;
    expect(within(add).getByRole("option", { name: "Служба ОК" })).toBeInTheDocument();
    expect(within(add).queryByRole("option", { name: "Бухгалтерия" })).not.toBeInTheDocument();

    // Добавляем этап и сохраняем состав: порядок пересчитан с 1, флаги ушли.
    fireEvent.change(add, { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить этап" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Удалить шаг 2" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Сохранить состав" }));

    await waitFor(() =>
      expect(setBlankSteps).toHaveBeenCalledWith(10, [
        // Порядок после перестановки: бухгалтерия первой (её флаги из бланка).
        { stage_id: 2, step_order: 1, optional_override: true, require_comment_override: true },
        // Добавленный этап — без переопределений («как в этапе»).
        { stage_id: 3, step_order: 2, optional_override: null, require_comment_override: null },
      ]),
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Состав бланка сохранён"));
  });

  // Текст этапа и его пунктов — визуальный редактор: разметка уходит в те же
  // поля справочника этапов (title/stage_lines), с предупреждением о печати.
  it("текст этапа: визуальный редактор сохраняет HTML в справочник этапов", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(updateRoutingStage).mockResolvedValue({ id: 1, updated: "title,stage_lines" });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));
    fireEvent.click(await screen.findByRole("button", { name: "Текст этапа 1" }));

    // Название этапа и его пункт редактируются визуально; предупреждение о печати.
    expect(await screen.findByText("Разметка попадёт в печатный документ.")).toBeInTheDocument();
    await waitFor(() =>
      // Название и пункт этапа показаны в редакторах (в таблице они тоже есть).
      expect(screen.getAllByText("Непосредственный руководитель").length).toBeGreaterThan(1),
    );
    expect(screen.getByText("Ознакомить с приказом")).toBeInTheDocument();
    const toolbars = screen.getAllByRole("toolbar");
    expect(toolbars.length).toBeGreaterThanOrEqual(2);

    // Помечаем выделенное в названии этапа: полужирный.
    const area = document.querySelector<HTMLElement>(".sed-blanktext .ProseMirror");
    expect(area).not.toBeNull();
    area?.setAttribute("tabindex", "-1");
    area?.focus();
    const range = document.createRange();
    range.selectNodeContents(area as HTMLElement);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
    await waitFor(() => expect(area?.textContent).not.toBe(""));
    fireEvent.click(screen.getAllByRole("button", { name: "Полужирный" })[0]);

    // Добавляем пункт и сохраняем текст этапа.
    fireEvent.click(screen.getByRole("button", { name: "Добавить пункт" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить текст этапа" }));

    await waitFor(() => expect(updateRoutingStage).toHaveBeenCalled());
    const [stageId, patch] = vi.mocked(updateRoutingStage).mock.calls[0];
    expect(stageId).toBe(1);
    expect(patch.title).toContain("<strong>");
    expect(patch.title).toContain("Непосредственный руководитель");
    // Пункты: прежний остался, добавленный пустой не уходит.
    expect(patch.stage_lines).toEqual(["Ознакомить с приказом"]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Текст этапа сохранён"));
  });

  // Ошибка сервера при сохранении состава — понятным текстом, форма жива.
  it("ошибка сохранения состава показывается текстом", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(setBlankSteps).mockRejectedValue(new ApiHttpError(422, "Этап вне маршрутов: Служба ОК"));

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить состав" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Этап вне маршрутов: Служба ОК"),
    );
  });

  // Недоступность справочника бланков (503) — понятный текст, админка жива.
  it("недоступность справочника бланков показывает текст", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(getBlanks).mockRejectedValue(new ApiHttpError(503, "Хранилище справочников недоступно"));

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Хранилище справочников недоступно"),
    );
    // Карточка нового бланка остаётся доступной.
    expect(screen.getByLabelText("Код бланка")).toBeInTheDocument();
  });

  // Мёртвый UI бланков бегунков убран целиком: ни редактора doc_templates/наборов
  // должностей, ни загрузки/предпросмотра файлов .docx; печать — из данных бланка
  // (вкладка «Бланки»).
  it("в админке нет UI бланков бегунков и файлов .docx", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаблоны" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Шаблоны" }));
    await waitFor(() => expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument());
    expect(screen.queryByText("Бланки бегунков (doc_templates)")).not.toBeInTheDocument();
    expect(screen.queryByText("Добавить бланк")).not.toBeInTheDocument();
    expect(screen.queryByText("Загрузить .docx")).not.toBeInTheDocument();
    expect(screen.queryByText("Предпросмотр")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Справочники" }));
    await waitFor(() => expect(screen.getByLabelText("Код предприятия 1")).toBeInTheDocument());
    expect(screen.queryByText("Наборы должностей (для бланков)")).not.toBeInTheDocument();
    expect(screen.queryByText("Добавить набор")).not.toBeInTheDocument();
    expect(screen.queryByText("Выбрать из справочника")).not.toBeInTheDocument();
  });
});

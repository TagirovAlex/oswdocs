// Тесты админки настроек (волна B4 / B3 Волны 2): данные — из /api/settings
// (settings-client) и справочников бланков (requests-client). Сеть не нужна:
// модули settings-client и requests-client мокаются, сценарии — загрузка полного
// объекта, сохранение, добавление/удаление предприятий и групп, рендер шаблонов,
// справочник бланков с визуальным редактором текста этапа, успех/ошибка/403.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSettings } from "./admin-settings";
import { ApiHttpError } from "./auth-client";
import type { BlankStepRow, StepCatalogRow } from "./requests-client";
import {
  createBlank,
  createStepCatalog,
  deleteStepCatalog,
  getBlankSteps,
  getBlanks,
  getDocTypes,
  getStepCatalog,
  getStepGroups,
  searchAd,
  setBlankSteps,
  updateBlank,
  updateStepCatalog,
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

// Мок клиента заявок: нужны только методы справочника бланков, шагов и виды
// документов (остальное — реальное, чтобы пустые вызовы fetch не шумели).
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return {
    ...actual,
    getBlanks: vi.fn(),
    createBlank: vi.fn(),
    updateBlank: vi.fn(),
    getBlankSteps: vi.fn(),
    setBlankSteps: vi.fn(),
    getStepGroups: vi.fn(),
    searchAd: vi.fn(),
    getDocTypes: vi.fn(),
    getStepCatalog: vi.fn(),
    createStepCatalog: vi.fn(),
    updateStepCatalog: vi.fn(),
    deleteStepCatalog: vi.fn(),
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
  position_escalation: { "Руководитель": 48 },
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
  position_escalation: {},
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
// СВОИХ шагов одного из них (GET .../blanks/{id}/steps): у шага нет этапа из
// справочника, есть свой текст и свой исполнитель.
const blanks = [
  {
    id: 10,
    code: "uvol_base",
    name: "Увольнение (базовый)",
    doc_type_code: "uvol",
    description: "Пояснение для сотрудника ОК",
    active: true,
    version: 3,
    step_count: 2,
    header_html: "<p>Заявка на увольнение {fio}</p>",
    footer_lines: ["Подпись: {manager}"],
  },
  {
    id: 11,
    code: "uvol_line",
    name: "Увольнение (линейный)",
    doc_type_code: null,
    description: null,
    active: false,
    version: 1,
    step_count: 0,
    header_html: null,
    footer_lines: [],
  },
];

const blankSteps: BlankStepRow[] = [
  {
    blank_id: 10,
    step_order: 1,
    title: "Непосредственный руководитель",
    stage_lines: ["Ознакомить с приказом"],
    executor_kind: "manager_ad",
    assignees: [],
    owner_group: null,
    approval_mode: "parallel",
    optional: false,
    require_comment: false,
  },
  {
    blank_id: 10,
    step_order: 2,
    title: "Бухгалтерия",
    stage_lines: [],
    executor_kind: "ad_group",
    assignees: [],
    owner_group: "SED_STEP_BUH",
    approval_mode: "sequential",
    optional: true,
    require_comment: true,
  },
];

// Группы-владельцы шагов из settings (GET /api/step-groups) — значение owner_group.
const stepGroups = [
  { id: "SED_STEP_BUH", name: "Бухгалтерия (вымышленная группа)" },
  { id: "SED_STEP_OK", name: "Отдел кадров (вымышленная группа)" },
];

// Кандидаты AD для подсказки согласующих (GET /api/ad/search).
const adCandidate = {
  sam: "petrov.pp",
  display_name: "Петров Пётр Петрович",
  department: "Бухгалтерия",
  title: "Бухгалтер",
  mail: "petrov.pp@example.test",
};

// Справочник шагов (GET /api/settings/routing/step-catalog): заготовки для
// состава бланка, включая неактивный шаг.
const stepCatalog: StepCatalogRow[] = [
  {
    id: 1,
    code: "soglas_rukovod",
    title: "Согласование с руководителем",
    stage_lines: ["<p>Ознакомить с приказом</p>"],
    executor_kind: "people",
    assignees: ["petrov.pp"],
    owner_group: null,
    approval_mode: "parallel",
    optional: false,
    require_comment: false,
    active: true,
    updated_by: "admin.test",
    updated_at: "2026-10-01T12:00:00+00:00",
  },
  {
    id: 2,
    code: "buh_proverka",
    title: "Проверка бухгалтерии",
    stage_lines: [],
    executor_kind: "ad_group",
    assignees: [],
    owner_group: "SED_STEP_BUH",
    approval_mode: "sequential",
    optional: true,
    require_comment: true,
    active: false,
    updated_by: null,
    updated_at: null,
  },
];

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
  vi.mocked(getStepGroups).mockReset();
  vi.mocked(searchAd).mockReset();
  vi.mocked(getDocTypes).mockReset();
  vi.mocked(getStepCatalog).mockReset();
  vi.mocked(createStepCatalog).mockReset();
  vi.mocked(updateStepCatalog).mockReset();
  vi.mocked(deleteStepCatalog).mockReset();
  // Виды документов (doc_types) — пустой справочник по умолчанию.
  vi.mocked(getDocTypes).mockResolvedValue([
    { code: "uvol", name: "Увольнение", is_active: true, sort_order: 1 },
  ]);
  // Справочники бланков по умолчанию: бланк с двумя своими шагами, группы шагов
  // и вид документа (тесты без бланков переопределяют getBlanks пустым списком).
  vi.mocked(getBlanks).mockResolvedValue(blanks);
  vi.mocked(getBlankSteps).mockResolvedValue(blankSteps);
  vi.mocked(getStepGroups).mockResolvedValue(stepGroups);
  vi.mocked(searchAd).mockResolvedValue([adCandidate]);
  vi.mocked(getStepCatalog).mockResolvedValue(stepCatalog);
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
  });

  // Легаси-вкладка «Шаблоны» (ключи templates/position_to_category) удалена:
  // в навигации её нет ни админу, ни руководителю ОК.
  it("вкладки «Шаблоны» нет в навигации", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Шаблоны" })).not.toBeInTheDocument();

    cleanup();
    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Шаблоны" })).not.toBeInTheDocument();
  });

  // Вкладка «Письма» (бывшие «Шаблоны»): редактор писем на месте, маршрутных
  // шаблонов больше нет.
  it("вкладка «Письма»: редактор писем без шаблонов маршрутов", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data: SettingsData) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Письма" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Письма" }));
    await waitFor(() => expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument());
    expect(screen.queryByText("Шаблоны маршрутов")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Служба шаблона 1")).not.toBeInTheDocument();
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
    expect(sent).not.toHaveProperty("templates");
    expect(sent).not.toHaveProperty("position_to_category");
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

  // Админ: на вкладке «Письма» только редактор писем; бланков бегунков
  // (doc_templates) и шаблонов маршрутов в UI больше нет — печать собирается из
  // данных бланка, маршрут задаёт бланк или сотрудник ОК вручную.
  it("админ правит письма, бланков бегунков и шаблонов маршрутов нет", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(saveSettings).mockImplementation(async (data) => data);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Письма" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Письма" }));
    await waitFor(() => expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument());
    // Редактор бланков бегунков, наборов должностей и шаблонов маршрутов удалены.
    expect(screen.queryByText("Бланки бегунков (doc_templates)")).not.toBeInTheDocument();
    expect(screen.queryByText("Наборы должностей (для бланков)")).not.toBeInTheDocument();
    expect(screen.queryByText("Шаблоны маршрутов")).not.toBeInTheDocument();
    // Существующее письмо загружено из настроек.
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
    expect(sent).not.toHaveProperty("templates");
    expect(sent).not.toHaveProperty("position_to_category");
  });

  // Руководитель ОК: редактор писем на «Письмах», сохранение через content.
  it("руководитель ОК редактирует письма и сохраняет через content", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);
    vi.mocked(saveSettingsContent).mockImplementation(async (data) => data);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Письма" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Письма" }));
    await waitFor(() => expect(screen.getByText("Письма (mail_templates)")).toBeInTheDocument());
    // Редактор бланков бегунков убран и у руководителя ОК.
    expect(screen.queryByText("Бланки бегунков (doc_templates)")).not.toBeInTheDocument();
    // contentOnly: писем нет — редактор пустой, но доступен.

    fireEvent.click(screen.getByText("Добавить письмо"));
    fireEvent.change(screen.getByLabelText("Код письма 1"), { target: { value: "assigned" } });
    fireEvent.change(screen.getByLabelText("Тема письма 1"), { target: { value: "Заявка" } });
    fireEvent.change(screen.getByLabelText("HTML письма 1"), { target: { value: "<html>заявка</html>" } });

    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() =>
      expect(saveSettingsContent).toHaveBeenCalledWith(
        expect.objectContaining({
          mail_templates: [{ code: "assigned", subject: "Заявка", body_html: "<html>заявка</html>" }],
        }),
      ),
    );
    // Легаси-ключи настроек ушли с бэкенда — в content их нет.
    const sent = vi.mocked(saveSettingsContent).mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(sent).not.toHaveProperty("templates");
    expect(sent).not.toHaveProperty("position_to_category");
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

  // Открытие вкладки бланков и список из справочника (код, название, шаги).
  it("вкладка «Бланки»: список бланков из справочника", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));

    const table = await screen.findByRole("table", { name: "Справочник бланков" });
    expect(within(table).getByText("uvol_base")).toBeInTheDocument();
    expect(within(table).getByText("Увольнение (базовый)")).toBeInTheDocument();
    // Отключённый бланк виден админу и помечен как неактивный.
    expect(within(table).getByText("uvol_line")).toBeInTheDocument();
    // Колонки «Макет печати» нет (поле layout убрано из бланка).
    expect(within(table).queryByText("Макет")).not.toBeInTheDocument();
    expect(getBlanks).toHaveBeenCalled();
  });

  // Роль: руководителю ОК вкладки «Бланки» нет (справочник — админский).
  it("вкладка «Бланки» недоступна руководителю ОК", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Письма" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Бланки" })).not.toBeInTheDocument();
    expect(getBlanks).not.toHaveBeenCalled();
  });

  // Создание бланка: код, название, вид документа, описание, активность.
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
      active: true,
      // Новый бланк без шапки и подвала — пустые значения уходят как есть.
      header_html: null,
      footer_lines: [],
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Бланк создан"));
  });

  // Шапка бланка: визуальный редактор + вставка плейсхолдера; подвал — список
  // строк с добавлением строки и вставкой плейсхолдера в выбранную строку.
  it("редакторы шапки и подвала: плейсхолдеры и строки уходят в карточку", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(updateBlank).mockResolvedValue({ id: 10 });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Править бланк uvol_base" }));

    // Шапка из справочника показана в визуальном редакторе.
    expect(await screen.findByText("Заявка на увольнение {fio}")).toBeInTheDocument();
    // Подвал: строка из справочника + добавление новой.
    expect(screen.getByLabelText("Строка подвала 1")).toHaveValue("Подпись: {manager}");
    fireEvent.click(screen.getByText("Добавить строку подвала"));
    const line2 = await screen.findByLabelText("Строка подвала 2");
    fireEvent.change(line2, { target: { value: "Дата" } });

    // Плейсхолдер в выбранную строку подвала (после правки поля цель — строка 2).
    fireEvent.click(screen.getByRole("button", { name: "Вставить {date} в подвал" }));
    await waitFor(() => expect(screen.getByLabelText("Строка подвала 2")).toHaveValue("Дата{date}"));

    // Плейсхолдер в шапку: уходит в визуальный редактор.
    fireEvent.click(screen.getByRole("button", { name: "Вставить {fio} в шапку" }));
    await waitFor(() => expect(screen.getByText(/Заявка на увольнение \{fio\}\{fio\}/)).toBeInTheDocument());

    fireEvent.click(screen.getByText("Сохранить бланк"));
    await waitFor(() =>
      expect(updateBlank).toHaveBeenCalledWith(
        10,
        expect.objectContaining({
          header_html: expect.stringContaining("Заявка на увольнение {fio}{fio}"),
          footer_lines: ["Подпись: {manager}", "Дата{date}"],
        }),
      ),
    );
  });

  // Пустой подвал: кнопки вставки плейсхолдера в подвал выключены и рядом есть
  // объяснение; после добавления строки кнопки включаются.
  it("пустой подвал: кнопки плейсхолдеров неактивны с объяснением", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    // uvol_line — подвал пуст (footer_lines: []).
    fireEvent.click(await screen.findByRole("button", { name: "Править бланк uvol_line" }));

    expect(await screen.findByText("строк нет")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Вставить {fio} в подвал" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Вставить {date} в подвал" })).toBeDisabled();
    // Понятное объяснение, почему вставлять некуда.
    expect(screen.getByText("Добавьте строку, затем вставьте в неё плейсхолдер.")).toBeInTheDocument();
    // В шапку вставка доступна всегда — там редактор есть.
    expect(screen.getByRole("button", { name: "Вставить {fio} в шапку" })).not.toBeDisabled();

    // Строка появилась — кнопки подвала активны, подсказка сменилась на номер строки.
    fireEvent.click(screen.getByText("Добавить строку подвала"));
    await screen.findByLabelText("Строка подвала 1");
    expect(screen.getByRole("button", { name: "Вставить {fio} в подвал" })).not.toBeDisabled();
    expect(screen.getByText("Плейсхолдер вставится в строку 1.")).toBeInTheDocument();
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
      expect(updateBlank).toHaveBeenCalledWith(
        10,
        expect.objectContaining({
          name: "Увольнение (базовый, правка)",
          doc_type_code: "uvol",
          description: "Пояснение для сотрудника ОК",
          active: false,
          // Шапка и подвал без правок уходят как из справочника.
          header_html: "<p>Заявка на увольнение {fio}</p>",
          footer_lines: ["Подпись: {manager}"],
        }),
      ),
    );
  });

  // Состав бланка: СВОИ шаги (название, текст, вид исполнителя, согласующие,
  // режим, необязательный, комментарий), порядок кнопками «вверх/вниз», удаление.
  it("состав бланка: свои шаги, порядок, флаги и удаление", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(setBlankSteps).mockResolvedValue({ blank_id: 10, count: 2 });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));

    // Состав пришёл по GET .../blanks/{id}/steps: два своих шага по порядку.
    await waitFor(() => expect(getBlankSteps).toHaveBeenCalledWith(10));
    expect(screen.getByLabelText("Название шага 1")).toHaveValue("Непосредственный руководитель");
    expect(screen.getByLabelText("Название шага 2")).toHaveValue("Бухгалтерия");
    // Крайние шаги не двигаются: первый вверх, последний вниз — неактивны.
    expect(screen.getByRole("button", { name: "Поднять шаг 1" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Опустить шаг 2" })).toBeDisabled();

    // Вид исполнителя и режим — из бланка (миграция 0013/0014).
    expect(screen.getByLabelText("Вид исполнителя шага 1")).toHaveValue("manager_ad");
    expect(screen.getByLabelText("Режим шага 1")).toHaveValue("parallel");
    expect(screen.getByLabelText("Вид исполнителя шага 2")).toHaveValue("ad_group");
    expect(screen.getByLabelText("Режим шага 2")).toHaveValue("sequential");
    expect(screen.getByLabelText("Группа AD шага 2")).toHaveValue("SED_STEP_BUH");
    // Шаг 2 в бланке необязательный и с обязательным комментарием.
    expect(screen.getByLabelText("Шаг 2 необязательный")).toBeChecked();
    expect(screen.getByLabelText("Комментарий шага 2 обязателен")).toBeChecked();

    // Меняем первый шаг на согласующих: подсказка по поиску AD и добавление.
    fireEvent.change(screen.getByLabelText("Вид исполнителя шага 1"), { target: { value: "people" } });
    expect(screen.getByText(/Добавьте хотя бы одного согласующего/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Логин согласующего шага 1"), { target: { value: "Петров" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти сотрудников" }));
    await waitFor(() => expect(searchAd).toHaveBeenCalledWith("Петров"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить согласующего petrov.pp в шаг 1" }));

    // Двигаем первый шаг вниз (он становится вторым) и удаляем второй шаг.
    fireEvent.click(screen.getByRole("button", { name: "Опустить шаг 1" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Название шага 2")).toHaveValue("Непосредственный руководитель"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Удалить шаг 2" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Удалить шаг 2" })).toBeNull());

    // Добавляем свой шаг (без этапа из справочника) и сохраняем состав.
    fireEvent.click(screen.getByRole("button", { name: "Добавить шаг" }));
    await waitFor(() => expect(screen.getByLabelText("Название шага 2")).toHaveValue(""));
    fireEvent.change(screen.getByLabelText("Название шага 2"), { target: { value: "Служба ОК" } });
    fireEvent.change(screen.getByLabelText("Вид исполнителя шага 2"), { target: { value: "ad_group" } });
    fireEvent.change(screen.getByLabelText("Группа AD шага 2"), { target: { value: "SED_STEP_OK" } });
    // Режим первого шага меняем с parallel на sequential («все ответственные»).
    fireEvent.change(screen.getByLabelText("Режим шага 1"), { target: { value: "sequential" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить состав" }));

    await waitFor(() =>
      expect(setBlankSteps).toHaveBeenCalledWith(10, [
        // Порядок после перестановки: бухгалтерия первой (её флаги из бланка).
        {
          step_order: 1,
          title: "Бухгалтерия",
          stage_lines: [],
          executor_kind: "ad_group",
          assignees: [],
          owner_group: "SED_STEP_BUH",
          approval_mode: "sequential",
          optional: true,
          require_comment: true,
        },
        // Новый шаг бланка — свой текст и своя группа AD.
        {
          step_order: 2,
          title: "Служба ОК",
          stage_lines: [],
          executor_kind: "ad_group",
          assignees: [],
          owner_group: "SED_STEP_OK",
          approval_mode: "sequential",
          optional: false,
          require_comment: false,
        },
      ]),
    );
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Состав бланка сохранён"));
  });

  // Некорректный шаг (people без согласующих) сервер бы отверг 422 — форма
  // предупреждает заранее и состав не отправляет.
  it("состав бланка: шаг без согласующих не отправляется", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));

    // Первый шаг — manager_ad (данные не нужны), переключаем на согласующих.
    const kind = await screen.findByLabelText("Вид исполнителя шага 1");
    fireEvent.change(kind, { target: { value: "people" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить состав" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("добавьте хотя бы одного согласующего"),
    );
    expect(setBlankSteps).not.toHaveBeenCalled();
  });

  // Текст шага и его пунктов — визуальный редактор: разметка уходит в
  // stage_lines шага бланка, с предупреждением о печати.
  it("текст шага: визуальный редактор сохраняет HTML в stage_lines шага", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(setBlankSteps).mockResolvedValue({ blank_id: 10, count: 2 });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));

    // Пункт первого шага редактируется визуально.
    expect(await screen.findByText("Ознакомить с приказом")).toBeInTheDocument();
    const toolbars = screen.getAllByRole("toolbar");
    expect(toolbars.length).toBeGreaterThanOrEqual(1);

    // Помечаем выделенное в пункте шага: полужирный (панель редактора этого пункта).
    const editorBlock = (await screen.findByLabelText("Пункт шага 1.1")).closest(".sed-rte");
    expect(editorBlock).not.toBeNull();
    const area = editorBlock?.querySelector<HTMLElement>(".ProseMirror");
    expect(area).not.toBeNull();
    area?.setAttribute("tabindex", "-1");
    area?.focus();
    const range = document.createRange();
    range.selectNodeContents(area as HTMLElement);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
    await waitFor(() => expect(area?.textContent).not.toBe(""));
    fireEvent.click(
      within(editorBlock as HTMLElement).getByRole("button", { name: "Полужирный" }),
    );

    // Добавляем пункт и сохраняем состав: разметка уходит в stage_lines.
    fireEvent.click(screen.getByLabelText("Добавить пункт шага 1"));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить состав" }));

    await waitFor(() => expect(setBlankSteps).toHaveBeenCalled());
    const sentSteps = vi.mocked(setBlankSteps).mock.calls[0][1];
    expect(sentSteps[0].title).toBe("Непосредственный руководитель");
    expect(sentSteps[0].stage_lines[0]).toContain("<strong>");
    expect(sentSteps[0].stage_lines[0]).toContain("Ознакомить с приказом");
    // Пункты: прежний остался, добавленный пустой не уходит.
    expect(sentSteps[0].stage_lines).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Состав бланка сохранён"));
  });

  // Ошибка сервера при сохранении состава — понятным текстом, форма жива.
  it("ошибка сохранения состава показывается текстом", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(setBlankSteps).mockRejectedValue(
      new ApiHttpError(422, "Шаг с executor_kind=people обязан содержать согласующих (assignees)"),
    );

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));
    fireEvent.click(await screen.findByRole("button", { name: "Сохранить состав" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Шаг с executor_kind=people обязан содержать согласующих (assignees)",
      ),
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

  // --- Справочник шагов (вкладка «Шаги», только админ) ------------------------

  // Вкладка «Шаги» открывается и показывает справочник: код, название, вид
  // исполнителя, режим, активность; неактивный шаг тоже виден админу.
  it("вкладка «Шаги»: список справочника шагов", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаги" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаги" }));

    const table = await screen.findByRole("table", { name: "Справочник шагов" });
    expect(within(table).getByText("soglas_rukovod")).toBeInTheDocument();
    expect(within(table).getByText("Согласование с руководителем")).toBeInTheDocument();
    expect(within(table).getByText("согласующие")).toBeInTheDocument();
    expect(within(table).getByText("любой ответственный")).toBeInTheDocument();
    // Неактивный шаг виден и помечен.
    expect(within(table).getByText("buh_proverka")).toBeInTheDocument();
    expect(within(table).getByText("нет")).toBeInTheDocument();
    expect(getStepCatalog).toHaveBeenCalled();
  });

  // Вкладка «Шаги» недоступна руководителю ОК (как «Бланки»).
  it("вкладка «Шаги» недоступна руководителю ОК", async () => {
    vi.mocked(getSettingsContent).mockResolvedValue(contentOnly);

    render(<AdminSettings role="hr_admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Письма" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Шаги" })).not.toBeInTheDocument();
    expect(getStepCatalog).not.toHaveBeenCalled();
  });

  // Создание шага справочника: код, название, согласующие через поиск AD,
  // режим и флаги уходят в createStepCatalog.
  it("создание шага справочника уходит в createStepCatalog", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(createStepCatalog).mockResolvedValue({ id: 5, code: "soglas_ok" });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Шаги" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Шаги" }));
    await waitFor(() => expect(screen.getByLabelText("Код шага справочника")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Код шага справочника"), { target: { value: "soglas_ok" } });
    fireEvent.change(screen.getByLabelText("Название шага справочника"), {
      target: { value: "Согласование с ОК" },
    });
    // Согласующие по умолчанию — люди: добавляем через поиск AD.
    fireEvent.change(screen.getByLabelText("Логин согласующего шага справочника"), {
      target: { value: "Петров" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Найти сотрудников" }));
    await waitFor(() => expect(searchAd).toHaveBeenCalledWith("Петров"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить согласующего petrov.pp в шаг справочника" }));
    fireEvent.click(screen.getByLabelText("Комментарий шага справочника обязателен"));
    fireEvent.click(screen.getByText("Создать шаг"));

    await waitFor(() => expect(createStepCatalog).toHaveBeenCalled());
    expect(createStepCatalog).toHaveBeenCalledWith({
      code: "soglas_ok",
      title: "Согласование с ОК",
      stage_lines: [],
      executor_kind: "people",
      assignees: ["petrov.pp"],
      owner_group: null,
      approval_mode: "sequential",
      optional: false,
      require_comment: true,
      active: true,
    });
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Шаг справочника создан"),
    );
  });

  // «Взять из справочника» в составе бланка: раскрывает активные шаги справочника,
  // выбор копирует шаг заготовкой в конец состава (независимая копия).
  it("«Взять из справочника» копирует шаг в состав бланка", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);
    vi.mocked(setBlankSteps).mockResolvedValue({ blank_id: 10, count: 3 });

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Бланки" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Бланки" }));
    fireEvent.click(await screen.findByRole("button", { name: "Состав бланка uvol_base" }));
    await waitFor(() => expect(getBlankSteps).toHaveBeenCalledWith(10));

    // Кнопка раскрывает список активных шагов справочника.
    fireEvent.click(screen.getByRole("button", { name: "Взять из справочника" }));
    expect(await screen.findByRole("button", { name: "Взять из справочника Согласование с руководителем" }))
      .toBeInTheDocument();
    // Неактивный шаг справочника в списке не предлагается.
    expect(screen.queryByRole("button", { name: "Взять из справочника Проверка бухгалтерии" }))
      .not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Взять из справочника Согласование с руководителем" }));

    // Шаг скопирован заготовкой в конец состава (после двух своих шагов).
    await waitFor(() => expect(screen.getByLabelText("Название шага 3")).toHaveValue("Согласование с руководителем"));
    expect(screen.getByLabelText("Вид исполнителя шага 3")).toHaveValue("people");
    expect(screen.getByText(/petrov\.pp/)).toBeInTheDocument();

    // Сохранение состава уходит со скопированным шагом как с обычным шагом бланка.
    fireEvent.click(screen.getByRole("button", { name: "Сохранить состав" }));
    await waitFor(() =>
      expect(setBlankSteps).toHaveBeenCalledWith(
        10,
        expect.arrayContaining([
          expect.objectContaining({
            step_order: 3,
            title: "Согласование с руководителем",
            stage_lines: ["<p>Ознакомить с приказом</p>"],
            executor_kind: "people",
            assignees: ["petrov.pp"],
            approval_mode: "parallel",
            optional: false,
            require_comment: false,
          }),
        ]),
      ),
    );
  });

  // Мёртвый UI бланков бегунков убран целиком: ни редактора doc_templates/наборов
  // должностей, ни загрузки/предпросмотра файлов .docx; печать — из данных бланка
  // (вкладка «Бланки»).
  it("в админке нет UI бланков бегунков и файлов .docx", async () => {
    vi.mocked(getSettings).mockResolvedValue(settings);

    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Письма" })).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Письма" }));
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

// Клиент настроек СЭД (волна B4 / B3 Волны 2): GET/PUT /api/settings (только SED_ADMINS).
// Значения — из settings БД (в коде не хардкодятся); токен — Bearer из localStorage.
import { ApiHttpError, getToken } from "./auth-client";

// Предприятие из настроек (settings.enterprises).
export interface SettingsEnterprise {
  code: string;
  name: string;
}

// Шаг шаблона маршрута (settings.templates[].steps[]).
export interface SettingsTemplateStep {
  owner_group: string;
  resolver?: string | null;
  require_comment?: boolean | null;
}

// Шаблон маршрута (settings.templates[]): служба + категория + шаги владельцев.
export interface SettingsTemplate {
  service: string;
  category: string;
  steps: SettingsTemplateStep[];
}

// Шаблон бегунка (settings.doc_templates[]): служба + категория + тело DOCX.
export interface SettingsDocTemplate {
  service: string;
  category: string;
  body: string;
}

// Шаблон письма (settings.mail_templates[]): код события + тема + HTML-тело.
export interface SettingsMailTemplate {
  code: string;
  subject: string;
  body_html: string;
}

// Подключение к базе 1С (settings.onec_bases): OData-параметры + схема ЗУП.
export interface SettingsOnecBase {
  // Код базы 1С (base_code, уникален).
  code: string;
  // Название базы.
  name: string;
  // OData-URL публикации базы (до /odata/standard.odata/).
  url: string;
  // Сервисная УЗ чтения (роль OData).
  user: string;
  // Пароль УЗ (в GET — маска либо null; записывается только при вводе).
  password: string | null;
  // Сущность сотрудников OData (дефолт ЗУП).
  employee_entity: string;
  // Сущность организаций (предприятий) OData (дефолт ЗУП).
  organization_entity: string;
  // Поле таб.№.
  tab_num_field: string;
  // Поле ФИО (может требовать $expand).
  fio_field: string;
  // Поле подразделения.
  department_field: string;
  // Поле должности.
  position_field: string;
  // Поле даты приёма.
  hire_date_field: string;
  // Поле кода организации (у ЗУП-«Организаций» кода нет — Ref_Key/ИНН).
  organization_code_field: string;
  // Поле названия организации.
  organization_name_field: string;
}

// Настройки СЭД из таблицы settings (типы — по контракту API, поля nullable:
// ключа нет в БД — null, значений в коде нет, AGENTS.md п.3).
export interface SettingsData {
  // TTL сессии, минут (session_ttl_minutes; 600 = 10 ч).
  session_ttl_minutes: number | null;
  // TTL отметок, дней (approval_ttl_days).
  approval_ttl_days: number | null;
  // Срок хранения сканов, дней (scan_retention_days).
  scan_retention_days: number | null;
  // Лимит размера скана, МБ (scan_max_mb).
  scan_max_mb: number | null;
  // Требовать бумажное заявление (require_paper_signature).
  require_paper_signature: boolean | null;
  // Хост SMTP-релея (smtp_host).
  smtp_host: string | null;
  // Порт SMTP-релея (smtp_port).
  smtp_port: number | null;
  // Отправитель уведомлений, e-mail (smtp_from).
  smtp_from: string | null;
  // Логин SMTP-релея (smtp_user; пусто — без авторизации).
  smtp_user: string | null;
  // Пароль SMTP-релея (smtp_password): в GET — null либо маска «задан»;
  // записывается только при вводе нового значения.
  smtp_password: string | null;
  // Комментарий обязателен при согласовании (require_comment).
  require_comment: boolean | null;
  // Предприятия (enterprises).
  enterprises: SettingsEnterprise[] | null;
  // Группы доступа — владельцы шагов (allowed_ad_groups).
  allowed_ad_groups: string[] | null;
  // Должность → категория (position_to_category).
  position_to_category: Record<string, string> | null;
  // Эскалация по должностям, часов (position_escalation).
  position_escalation: Record<string, number> | null;
  // Шаблоны маршрутов (templates).
  templates: SettingsTemplate[] | null;
  // Шаблоны бегунков (doc_templates).
  doc_templates: SettingsDocTemplate[] | null;
  // Шаблоны писем (mail_templates).
  mail_templates: SettingsMailTemplate[] | null;
  // Подключения к базам 1С (onec_bases; пароль маскируется в GET).
  onec_bases: SettingsOnecBase[] | null;
  // Дата/время последней синхронизации предприятий (read-only, пишет синхронизация).
  onec_enterprises_synced_at: string | null;
}

// Контент-настройки (GET/PUT /api/settings/content): контент-ключи для
// руководителя ОК и админа (инфра-ключи — только админ через /api/settings).
export interface ContentSettingsData {
  // TTL отметок, дней (approval_ttl_days).
  approval_ttl_days: number | null;
  // Комментарий обязателен при согласовании (require_comment).
  require_comment: boolean | null;
  // Требовать бумажное заявление (require_paper_signature).
  require_paper_signature: boolean | null;
  // Предприятия (enterprises).
  enterprises: SettingsEnterprise[] | null;
  // Группы доступа — владельцы шагов (allowed_ad_groups).
  allowed_ad_groups: string[] | null;
  // Должность → категория (position_to_category).
  position_to_category: Record<string, string> | null;
  // Эскалация по должностям, часов (position_escalation).
  position_escalation: Record<string, number> | null;
  // Шаблоны маршрутов (templates).
  templates: SettingsTemplate[] | null;
  // Шаблоны бегунков (doc_templates).
  doc_templates: SettingsDocTemplate[] | null;
  // Шаблоны писем (mail_templates).
  mail_templates: SettingsMailTemplate[] | null;
}

// Запрос к /api/settings* с Bearer-токеном; ответ — настройки.
// Ошибки: 401 — нет сессии, 403 — доступ закрыт, 422 — неверные типы, 503 — сервис недоступен.
async function requestSettings<T>(path: string, method: "GET" | "PUT", data?: T): Promise<T> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  const init: RequestInit = { method, headers: { Authorization: `Bearer ${token}` } };
  if (data !== undefined) {
    init.headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
    init.body = JSON.stringify(data);
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new Error("Сервис недоступен");
  }
  if (res.status === 401) throw new ApiHttpError(401, "Сессия истекла");
  if (res.status === 403) throw new ApiHttpError(403, "Настройки — только админам");
  if (res.status === 422) throw new ApiHttpError(422, "Неверные значения настроек");
  if (res.status === 503) throw new ApiHttpError(503, "Сервис настроек недоступен");
  if (!res.ok) {
    const verb = method === "PUT" ? "сохранения" : "загрузки";
    throw new ApiHttpError(res.status, `Ошибка ${verb} настроек`);
  }
  return (await res.json()) as T;
}

// GET /api/settings: текущие настройки (все ключи, только админ).
export async function getSettings(): Promise<SettingsData> {
  return requestSettings<SettingsData>("/api/settings", "GET");
}

// PUT /api/settings: частичное сохранение (передаём только редактируемые ключи),
// ответ — полное текущее состояние.
export async function saveSettings(data: SettingsData): Promise<SettingsData> {
  return requestSettings<SettingsData>("/api/settings", "PUT", data);
}

// GET /api/settings/content: контент-настройки (руководитель ОК + админ).
export async function getSettingsContent(): Promise<ContentSettingsData> {
  return requestSettings<ContentSettingsData>("/api/settings/content", "GET");
}

// PUT /api/settings/content: частичное сохранение контента (только контент-ключи).
export async function saveSettingsContent(data: ContentSettingsData): Promise<ContentSettingsData> {
  return requestSettings<ContentSettingsData>("/api/settings/content", "PUT", data);
}

// Результат синхронизации предприятий из 1С (POST /api/settings/enterprises/sync).
export interface EnterprisesSyncResult {
  // Флаг успеха.
  synced: boolean;
  // Сколько предприятий записано в settings.
  count: number;
  // Обновлённый список [{"code", "name"}, ...].
  enterprises: unknown[];
}

// POST /api/settings/enterprises/sync: принудительная синхронизация предприятий
// (только админ). Ошибки: 401/403/503 — понятным текстом из клиента.
export async function syncEnterprises(): Promise<EnterprisesSyncResult> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  let res: Response;
  try {
    res = await fetch("/api/settings/enterprises/sync", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new Error("Сервис недоступен");
  }
  if (res.status === 401) throw new ApiHttpError(401, "Сессия истекла");
  if (res.status === 403) throw new ApiHttpError(403, "Настройки — только админам");
  if (res.status === 503) throw new ApiHttpError(503, "Источник предприятий 1С недоступен");
  if (!res.ok) {
    throw new ApiHttpError(res.status, "Ошибка синхронизации предприятий");
  }
  return (await res.json()) as EnterprisesSyncResult;
}
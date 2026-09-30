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

// Настройки СЭД из таблицы settings (типы — по контракту API, поля nullable:
// ключа нет в БД — null, значений в коде нет, AGENTS.md п.3).
export interface SettingsData {
  // TTL отметок, дней (approval_ttl_days).
  approval_ttl_days: number | null;
  // Срок хранения сканов, дней (scan_retention_days).
  scan_retention_days: number | null;
  // Лимит размера скана, МБ (scan_max_mb).
  scan_max_mb: number | null;
  // Требовать бумажное заявление (require_paper_signature).
  require_paper_signature: boolean | null;
  // Отправитель уведомлений, e-mail (smtp_from).
  smtp_from: string | null;
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
}

// Запрос к /api/settings с Bearer-токеном; ответ — настройки.
// Ошибки: 401 — нет сессии, 403 — не админ, 422 — неверные типы, 503 — сервис недоступен.
async function requestSettings(method: "GET" | "PUT", data?: SettingsData): Promise<SettingsData> {
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
    res = await fetch("/api/settings", init);
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
  return (await res.json()) as SettingsData;
}

// GET /api/settings: текущие настройки.
export async function getSettings(): Promise<SettingsData> {
  return requestSettings("GET");
}

// PUT /api/settings: частичное сохранение (передаём только редактируемые ключи),
// ответ — полное текущее состояние.
export async function saveSettings(data: SettingsData): Promise<SettingsData> {
  return requestSettings("PUT", data);
}
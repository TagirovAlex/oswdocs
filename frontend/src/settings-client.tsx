// Клиент настроек СЭД (волна B4): GET/PUT /api/settings (только SED_ADMINS).
// Значения — из settings БД (в коде не хардкодятся); токен — Bearer из localStorage.
import { ApiHttpError, getToken } from "./auth-client";

// Настройки СЭД из таблицы settings (типы — по контракту API).
// Поля nullable: ключа нет в БД — null (значения живут только в settings БД,
// дефолтов в коде нет, AGENTS.md п.3); админ заполняет их перед сохранением.
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

// PUT /api/settings: сохранение, ответ — сохранённые значения.
export async function saveSettings(data: SettingsData): Promise<SettingsData> {
  return requestSettings("PUT", data);
}
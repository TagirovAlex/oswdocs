// Клиент заявок СЭД (волна B4): /api/requests, /api/folders, /api/enterprises,
// /api/step-groups, /api/employees. Токен — Bearer из localStorage (как в
// settings-client); ошибки 401/403/422/503 — ApiHttpError с текстом от API.
// Значения (предприятия/группы) — только из settings БД, хардкода нет (AGENTS.md п.3).
import { ApiHttpError, getToken } from "./auth-client";

// Предприятие из настроек (GET /api/enterprises); code — в составном ключе сотрудника.
export interface Enterprise {
  code: string;
  name: string;
}

// Идентификатор папки дерева (контракт GET /api/folders).
export type FolderId = "agreement" | "revision" | "done" | "mine";

// Папка дерева со счётчиком.
export interface Folder {
  id: FolderId;
  title: string;
  count: number;
}

// Шаг маршрута (RequestOut.steps).
export interface RequestStep {
  order: number;
  owner_group: string;
  resolver: string;
  assignee?: string | null;
  status: string;
  require_comment?: boolean;
  expires_at: string;
  done_by?: string | null;
  done_at?: string | null;
  comment?: string | null;
}

// Заявка из API (RequestOut). fio у владельца — null (ПДн режет сервер).
export interface RequestOut {
  id: string;
  status: string;
  route_origin: string;
  enterprise?: string | null;
  tab_num?: string | null;
  department: string;
  position: string;
  fio?: string | null;
  category?: string | null;
  escalation_hours?: number | null;
  created_by: string;
  steps: RequestStep[];
}

// Строка таблицы заявок (маппинг RequestOut → таблица).
export interface RequestRow {
  id: string;
  fio: string;
  enterprise: string;
  status: string;
  step: string;
  dueDate: string;
  department: string;
  position: string;
  steps: RequestStep[];
}

// Тело POST /api/requests (ручной маршрут — шаги с группами владельцев).
export interface CreateRequestBody {
  enterprise: string;
  tab_num: string;
  department: string;
  position: string;
  fio: string;
  steps?: Array<{ owner_group: string }>;
}

// Найденный сотрудник 1С (GET /api/employees; полная карточка — только ОК/админу).
export interface EmployeeHit {
  key: string;
  tab_num: string;
  fio: string;
  dept: string;
  position: string;
  needs_manual_review: boolean;
}

// Ответ поиска сотрудников.
export interface EmployeeSearchResult {
  items: EmployeeHit[];
  errors?: string[];
  needs_manual_review?: boolean;
}

// Фильтры над таблицей заявок.
export interface RequestFilters {
  query: string;
  enterprise: string;
  status: string;
}

// Пустой фильтр по умолчанию.
export const EMPTY_FILTERS: RequestFilters = { query: "", enterprise: "", status: "" };

// Папка → статусы для клиентской фильтрации (контракт GET /api/folders).
// mine: владельцу API уже вернул свои; для ОК — все загруженные.
export const FOLDER_STATUSES: Record<FolderId, string[] | null> = {
  agreement: ["На согласовании"],
  revision: ["На доработке"],
  done: ["Завершено", "Отклонено", "Отозвано"],
  mine: null,
};

// Деталь ошибки из тела FastAPI (detail), иначе null.
async function readDetail(res: Response): Promise<string | null> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string" && data.detail !== "") return data.detail;
  } catch {
    // тело не JSON — общий текст ниже
  }
  return null;
}

// Запрос к /api/* с Bearer-токеном; 401/403/422/503 → ApiHttpError с понятным текстом.
async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (init?.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new Error("Сервис недоступен");
  }
  if (res.ok) return (await res.json()) as T;
  const detail = await readDetail(res);
  switch (res.status) {
    case 401:
      throw new ApiHttpError(401, detail ?? "Сессия истекла");
    case 403:
      throw new ApiHttpError(403, detail ?? "Доступ запрещён");
    case 422:
      throw new ApiHttpError(422, detail ?? "Неверные данные запроса");
    case 503:
      throw new ApiHttpError(503, detail ?? "Сервис недоступен");
    default:
      throw new ApiHttpError(res.status, detail ?? "Ошибка сервера");
  }
}

// GET /api/enterprises: предприятия из settings (роли hr/admin).
export async function getEnterprises(): Promise<Enterprise[]> {
  return requestJson<Enterprise[]>("/api/enterprises");
}

// GET /api/step-groups: группы для ручного конструктора шагов.
export async function getStepGroups(): Promise<string[]> {
  return requestJson<string[]>("/api/step-groups");
}

// GET /api/folders: папки со счётчиками.
export async function getFolders(): Promise<Folder[]> {
  return requestJson<Folder[]>("/api/folders");
}

// GET /api/requests: список заявок (владельцу — только свои, fio — null).
export async function getRequests(): Promise<RequestOut[]> {
  return requestJson<RequestOut[]>("/api/requests");
}

// POST /api/requests: создание заявки от ОК/админа (201 → созданная заявка).
export async function createRequest(body: CreateRequestBody): Promise<RequestOut> {
  return requestJson<RequestOut>("/api/requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// GET /api/employees: поиск сотрудников предприятия; без баз 1С — 503.
export async function searchEmployees(
  enterprise: string,
  q: string,
): Promise<EmployeeSearchResult> {
  return requestJson<EmployeeSearchResult>(
    `/api/employees?enterprise=${encodeURIComponent(enterprise)}&q=${encodeURIComponent(q)}`,
  );
}

// Маппинг RequestOut → строка таблицы: шаг — первый ожидающий, срок — его expires_at.
// ПДн: у владельца fio=null → маска «Сотрудник № {id}».
export function toRequestRow(request: RequestOut): RequestRow {
  const pending = request.steps.find((s) => s.status === "ожидает");
  const current = pending ?? request.steps[0];
  const expires = current?.expires_at ?? "";
  return {
    id: request.id,
    fio: request.fio ?? `Сотрудник № ${request.id}`,
    enterprise: request.enterprise ?? "",
    status: request.status,
    step: current?.owner_group ?? "—",
    dueDate: expires ? expires.slice(0, 10) : "—",
    department: request.department,
    position: request.position,
    steps: request.steps,
  };
}

// Клиентская фильтрация загруженных строк (поиск/предприятие/статус + папка).
export function filterRequests(
  rows: RequestRow[],
  folder: FolderId,
  filters: RequestFilters,
): RequestRow[] {
  const statuses = FOLDER_STATUSES[folder];
  return rows.filter((row) => {
    if (statuses && !statuses.includes(row.status)) return false;
    if (filters.query && !row.fio.toLowerCase().includes(filters.query.toLowerCase())) return false;
    if (filters.enterprise && row.enterprise !== filters.enterprise) return false;
    if (filters.status && row.status !== filters.status) return false;
    return true;
  });
}
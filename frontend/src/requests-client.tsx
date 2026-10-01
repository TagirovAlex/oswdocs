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
  hire_date?: string | null;
  ad_sam?: string | null;
  // Статус стыковки 1С↔AD: linked | match | no_match (без записи в БД).
  ad_status?: string | null;
  needs_manual_review: boolean;
}

// Ответ поиска сотрудников.
export interface EmployeeSearchResult {
  items: EmployeeHit[];
  errors?: string[];
  needs_manual_review?: boolean;
}

// Результат POST /api/requests/{id}/print: сгенерированный бегунок (v1/v2).
// generated=false + reason — НЕ ошибка (нет LibreOffice/шаблона), текст показывает экран.
export interface PrintResult {
  version: "v1" | "v2";
  pdf_path?: string;
  qr_payload?: string;
  generated: boolean;
  reason?: string;
}

// Мета документа заявки (GET /api/documents/{id}).
export interface DocumentMeta {
  version: "v1" | "v2";
  pdf_path?: string;
  qr_payload?: string;
  created_at: string;
}

// Решение владельца шага (контракт POST /requests/{id}/steps/{order}/decision).
export type StepDecision = "approve" | "reject" | "return";

// Мета вложения заявки (GET /api/requests/{id}/attachments).
export interface AttachmentMeta {
  id: string;
  filename: string;
  size: number;
  created_at: string;
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
  // Multipart (FormData) — Content-Type ставит браузер с границей, вручную нельзя.
  if (init?.body !== undefined && !(init.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
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
    case 404:
      throw new ApiHttpError(404, detail ?? "Не найдено");
    case 409:
      throw new ApiHttpError(409, detail ?? "Состояние заявки изменилось");
    case 410:
      throw new ApiHttpError(410, detail ?? "Срок шага истек: нужен повтор");
    case 413:
      throw new ApiHttpError(413, detail ?? "Файл больше допустимого лимита");
    case 415:
      throw new ApiHttpError(415, detail ?? "Недопустимый тип файла");
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

// GET /api/requests/{id}: карточка заявки (шаги, сроки, отметки).
export async function getRequest(id: string): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}`);
}

// POST /api/requests/{id}/steps/{order}/decision: отметка владельца шага.
// 410 — просрочен, 409 — шаг закрыт/не по порядку, 422 — нет комментария при отказе/возврате.
export async function decideStep(
  id: string,
  order: number,
  decision: StepDecision,
  comment?: string,
): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}/steps/${order}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, comment }),
  });
}

// POST /api/requests/{id}/submit: Черновик/На доработке → На согласовании (ОК/админ).
export async function submitRequest(id: string): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}/submit`, {
    method: "POST",
  });
}

// POST /api/requests/{id}/to-execution: Согласовано → К исполнению (ОК/админ).
export async function toExecution(id: string): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}/to-execution`, {
    method: "POST",
  });
}

// POST /api/requests/{id}/finish: К исполнению → Завершено (ОК/админ).
export async function finishRequest(id: string): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}/finish`, {
    method: "POST",
  });
}

// POST /api/requests: создание заявки от ОК/админа (201 → созданная заявка).
export async function createRequest(body: CreateRequestBody): Promise<RequestOut> {
  return requestJson<RequestOut>("/api/requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// POST /api/requests/{id}/print: генерация бегунка (ОК/админ). При отсутствии
// LibreOffice/шаблона — generated=false с reason (не ошибка).
export async function printRequest(id: string): Promise<PrintResult> {
  return requestJson<PrintResult>(`/api/requests/${encodeURIComponent(id)}/print`, {
    method: "POST",
  });
}

// GET /api/documents/{id}: мета документов заявки (версии, пути, QR).
export async function getDocuments(id: string): Promise<DocumentMeta[]> {
  return requestJson<DocumentMeta[]>(`/api/documents/${encodeURIComponent(id)}`);
}

// GET /api/requests/{id}/attachments: мета вложений заявки.
export async function getAttachments(id: string): Promise<AttachmentMeta[]> {
  return requestJson<AttachmentMeta[]>(`/api/requests/${encodeURIComponent(id)}/attachments`);
}

// POST /api/requests/{id}/attachments: загрузка скана (multipart).
// 413 — больше лимита, 415 — тип вне allowlist, 409 — лимит не задан.
export async function uploadAttachment(id: string, file: File): Promise<AttachmentMeta> {
  const form = new FormData();
  form.append("file", file);
  return requestJson<AttachmentMeta>(`/api/requests/${encodeURIComponent(id)}/attachments`, {
    method: "POST",
    body: form,
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

// Карточка сотрудника: 1С-блок + AD-блок + связка и расхождения (GET /api/employees/card).
export interface EmployeeCardData {
  key: string;
  enterprise: string;
  base_code: string;
  tab_num?: string | null;
  truth_source: string;
  link: { linked: boolean; sam?: string | null; by?: string | null; at?: string | null; verified?: boolean | null };
  divergences: string[];
  needs_manual_review: boolean;
  fio?: string | null;
  dept?: string | null;
  position?: string | null;
  hire_date?: string | null;
  dismissal_date?: string | null;
  ad_sam?: string | null;
  // Статус стыковки 1С↔AD: linked | match | no_match (без записи в БД).
  ad_status?: string | null;
  snapshot_1c?: Record<string, unknown> | null;
  snapshot_ad?: {
    sam?: string | null;
    display_name?: string | null;
    department?: string | null;
    title?: string | null;
    mail?: string | null;
    manager_dn?: string | null;
  } | null;
  ad_error?: string | null;
}

export async function getEmployeeCard(
  enterprise: string,
  baseCode: string,
  tabNum: string,
): Promise<EmployeeCardData> {
  const params = new URLSearchParams({ enterprise, base_code: baseCode, tab_num: tabNum });
  return requestJson<EmployeeCardData>(`/api/employees/card?${params.toString()}`);
}

// POST /api/link_1c_ad: привязка 1С→AD (только админ).
export async function createLink(body: {
  enterprise: string;
  base_code: string;
  tab_num: string;
  sam: string;
}): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`/api/link_1c_ad`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Кандидат AD для ручной привязки (GET /api/ad/search, только админ).
export interface AdCandidate {
  sam: string;
  display_name: string;
  department: string;
  title: string;
  mail: string;
}

// GET /api/ad/search: поиск кандидатов AD по ФИО (только админ).
export async function searchAd(q: string): Promise<AdCandidate[]> {
  const res = await requestJson<{ items: AdCandidate[] }>(
    `/api/ad/search?q=${encodeURIComponent(q)}`,
  );
  return res.items;
}

// Итог автосвязки 1С↔AD (POST /api/link_1c_ad/sync, только админ).
export interface AdSyncResult {
  synced: boolean;
  scanned: number;
  created: number;
  skipped_linked: number;
  skipped_1c_duplicates: number;
  skipped_ad_no_match: number;
  skipped_ad_duplicates: number;
  errors: string[];
}

// POST /api/link_1c_ad/sync: принудительная автосвязка по точному ФИО (только админ).
export async function syncLinks(): Promise<AdSyncResult> {
  return requestJson<AdSyncResult>("/api/link_1c_ad/sync", { method: "POST" });
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
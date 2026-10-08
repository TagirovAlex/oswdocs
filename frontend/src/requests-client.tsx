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

// Вид документа (таблица doc_types; GET /api/doc-types). active_only=true — для
// селекта в форме создания (только активные), false — админ-справочник.
export interface DocType {
  code: string;
  name: string;
  is_active: boolean;
  sort_order: number;
}

// Группа-владелец шага из настроек (GET /api/step-groups): id уходит в
// owner_group, name — человекочитаемое наименование в селекте формы.
export interface StepGroup {
  id: string;
  name: string;
}

// Идентификатор папки дерева (контракт GET /api/folders).
export type FolderId = "agreement" | "revision" | "execution" | "done" | "draft" | "mine";

// Папка дерева со счётчиком. depth — уровень вложенности (задел под дерево).
export interface Folder {
  id: FolderId;
  title: string;
  count: number;
  depth?: number;
}

// Режим шага с несколькими ответственными (миграция 0013): parallel —
// согласование любым из ответственных (отметка одного закрывает шаг),
// sequential — отметки всех ответственных.
export type StepApprovalMode = "sequential" | "parallel";

// Отметка ответственного по шагу (StepApprovalOut). sam — это sAMAccountName:
// бэкенд отдаёт его только привилегированным и владельцу своей отметки, у чужих
// отметок sam=null (сами решения и комментарии при этом видны).
export interface StepApproval {
  sam: string | null;
  at: string;
  decision: string;
  comment?: string | null;
}

// Шаг маршрута (RequestOut.steps).
export interface RequestStep {
  order: number;
  owner_group: string;
  resolver: string;
  // Персональный исполнитель (sAMAccountName); может быть null. В UI НЕ
  // выводится — вместо него ФИО согласующего (owner_name).
  assignee?: string | null;
  // ФИО согласующего шага (резолв бэкенда); для владельца приходит всегда.
  owner_name?: string | null;
  // Должность персонального исполнителя из AD (резолв бэкенда, рядом с ФИО);
  // у группового шага — null (там должность — наименование группы).
  owner_duty?: string | null;
  // Ключ карточки сотрудника шага (enterprise|base_code|tab_num) — только
  // привилегированным (бэк решает по локальному справочнику, при неоднозначном
  // совпадении не приходит). Есть — ФИО в таблице шагов кликабельно.
  employee_key?: string | null;
  emp_enterprise?: string | null;
  emp_base_code?: string | null;
  emp_tab_num?: string | null;
  // Единственный источник истины для кнопок согласования: может ли ТЕКУЩИЙ
  // пользователь поставить отметку по этому шагу.
  can_act?: boolean;
  status: string;
  require_comment?: boolean;
  expires_at: string;
  done_by?: string | null;
  done_at?: string | null;
  comment?: string | null;
  // Несколько ответственных (миграция 0013): снимок логинов, режим шага и
  // собранные отметки. assignee — первый из assignees (прежний UI/выборки),
  // поэтому ФИО берём по логинам отдельно (см. request-card).
  assignees?: string[];
  approvals?: StepApproval[];
  approval_mode?: StepApprovalMode | null;
  // Прогресс отметок «N из M согласовали»: считает бэкенд по полному снимку,
  // поэтому показывается и непривилегированному, у которого в assignees
  // остаётся только собственный логин.
  approved_count?: number;
  assignee_count?: number;
}

// Заявка из API (RequestOut). fio у владельца — null (ПДн режет сервер).
export interface RequestOut {
  id: string;
  status: string;
  route_origin: string;
  enterprise?: string | null;
  // Название предприятия (из settings); приходит вместе с enterprise
  // привилегированным, при отсутствии — откат на код предприятия.
  enterprise_name?: string | null;
  // Код базы 1С (часть ключа карточки сотрудника; когда есть у запроса).
  base_code?: string | null;
  // Ключ карточки сотрудника (enterprise|base_code|tab_num), собранный бэкендом по
  // локальному справочнику; только привилегированным (ПДн: табельный номер).
  employee_key?: string | null;
  tab_num?: string | null;
  department: string;
  position: string;
  fio?: string | null;
  category?: string | null;
  escalation_hours?: number | null;
  // Автор заявки (sAMAccountName) — только привилегированным, иначе null.
  created_by?: string | null;
  // Тема, содержание и вид документа (таблица doc_types) — правятся админом СЭД.
  subject?: string | null;
  content?: string | null;
  doc_type_code?: string | null;
  steps: RequestStep[];
}

// Строка таблицы заявок (маппинг RequestOut → таблица).
export interface RequestRow {
  id: string;
  fio: string;
  enterprise: string;
  status: string;
  step: string;
  // Текущий согласующий: исполнитель ПЕРВОГО ожидающего шага (can_act здесь не
  // участвует — это про отметку, а не про подпись): ФИО, иначе группа, иначе прочерк.
  ownerName: string;
  dueDate: string;
  department: string;
  position: string;
  steps: RequestStep[];
  // Составной ключ карточки сотрудника (enterprise|base_code|tab_num);
  // непустой только когда все части есть (для ссылки на карточку).
  employeeKey?: string;
}

// Тело POST /api/requests.
export interface CreateRequestBody {
  enterprise: string;
  tab_num: string;
  department: string;
  position: string;
  fio: string;
  steps?: Array<{ owner_group: string }>;
  // Маршрут блоками: последовательный/параллельный (приоритетнее steps).
  blocks?: Array<{
    mode: "sequential" | "parallel";
    // Шаг блока: персональный исполнитель (sam) либо группа (owner_group).
    steps: Array<{ sam?: string; owner_group?: string; resolver?: string }>;
  }>;
  // Тема и содержание заявки (обязательные поля формы).
  subject: string;
  content: string;
  // Вид документа (GET /api/doc-types; пустой справочник — поле не уходит).
  doc_type_code?: string;
  // Бланк из справочника (GET /api/requests/route/blanks; выбирает ОК). В auto
  // его шаги становятся маршрутом заявки, в custom бланк указывать не обязательно.
  blank_id?: number;
  // Режим маршрута: auto — сборка из профиля службы (по умолчанию), custom —
  // ручной конструктор (blocks/steps). В auto блоки не отправляются.
  route_mode?: RouteMode;
  // Коды этапов, снятых ОК из маршрута (auto).
  dismissed_stages?: string[];
  // Коды этапов, добавленных ОК в конец маршрута (auto).
  added_stages?: string[];
  // Код базы 1С и логин AD сотрудника — источник карточки для auto-маршрута.
  base_code?: string;
  ad_sam?: string;
  // Замена руководителя, выбранная ОК вручную (логин AD): приоритетнее
  // руководителя из AD для этапов manager_ad («Руководитель сотрудника»).
  manager?: string;
}

// Режим маршрута заявки (POST /api/requests, route_mode).
export type RouteMode = "auto" | "custom";

// Профиль маршрута из предпросмотра (RoutePreviewProfileOut).
export interface RoutePreviewProfile {
  id: number;
  code: string;
  name: string;
}

// Служба заявки из предпросмотра (RoutePreviewServiceOut).
export interface RoutePreviewService {
  id: number;
  dept_name: string;
  blank_kind: string | null;
}

// Этап маршрута из предпросмотра (RoutePreviewStageOut). blocked_reason —
// почему этап нельзя закрыть (руководитель не найден в AD, группа не задана,
// состав этапа пуст), иначе null.
export interface RoutePreviewStage {
  stage_id: number | null;
  code: string | null;
  title: string | null;
  stage_lines: string[];
  owner_kind: string;
  owner_group: string | null;
  owner_name: string | null;
  // Этап необязательный: optional=false — снять его нельзя.
  optional: boolean;
  blocked_reason: string | null;
}

// Тело POST /api/requests/route/preview (RoutePreviewIn).
export interface RoutePreviewBody {
  enterprise: string;
  tab_num: string;
  base_code?: string;
  ad_sam?: string;
  department?: string;
  position?: string;
  // Снятые и добавленные этапы — те же коды, что уходят в создание.
  dismissed_stages?: string[];
  added_stages?: string[];
  // Замена руководителя (логин AD): показываем в предпросмотре именно того,
  // кто пойдёт в маршрут, вместо того, кто найден по manager_dn из AD.
  manager?: string;
  // Бланк из справочника: его шаги и ответственные — в предпросмотре. Не задан —
  // маршрут подбирается по службе (если включена настройка blank_autopick).
  blank_id?: number;
}

// Снимок выбранного бланка в предпросмотре (RoutePreviewBlankOut).
export interface RoutePreviewBlank {
  id: number;
  code: string;
  name: string;
  layout: string | null;
  version: number | null;
  step_count: number;
}

// Ответ предпросмотра маршрута (RoutePreviewOut). reason — причина подбора
// (service_profile / default_profile / service_not_registered /
// profile_not_found, возможно с пометками через «+»).
export interface RoutePreview {
  profile: RoutePreviewProfile | null;
  service: RoutePreviewService | null;
  reason: string;
  stages: RoutePreviewStage[];
  // Выбранный бланк — снимок (RoutePreviewBlank); при подборе по службе прежнее
  // значение — вид бланка печати из справочника служб (office/line, строка).
  blank: RoutePreviewBlank | string | null;
  // Откуда взят маршрут: chosen — бланк выбрал ОК, autopick — подобран по службе
  // (запасной механизм, настройка blank_autopick).
  blank_source?: "chosen" | "autopick" | null;
  // Предупреждение для ОК: например, сотрудник не найден в AD — маршрут по
  // службе не подбирается, печать пойдёт бланком по умолчанию.
  notice?: string | null;
  // Состояние связи 1С↔AD выбранного сотрудника:
  //   linked — связь есть; need_link — кандидат AD один, связь не подтверждена
  //   (её подтверждает кнопка в форме); ambiguous — в AD несколько записей с этим
  //   ФИО; absent — в AD нет записи с таким ФИО.
  link_state?: "linked" | "need_link" | "ambiguous" | "absent" | null;
  link_candidate?: {
    sam: string;
    fio: string | null;
    dept_ad: string | null;
    title_ad: string | null;
    manager_sam: string | null;
  } | null;
}

/** Результат подтверждения связи при создании заявки (link-employee). */
export interface LinkEmployeeResult {
  linked: boolean;
  already: boolean;
  sam: string;
  fio: string | null;
  dept_ad: string | null;
  title_ad: string | null;
}

// POST /api/requests/route/link-employee: оформить связь 1С↔AD по сотруднику.
// Кандидат в AD должен быть ровно один; при нескольких — 422 (выбор за человеком).
export async function linkEmployee(payload: {
  enterprise: string;
  base_code?: string | null;
  tab_num: string;
  fio?: string | null;
}): Promise<LinkEmployeeResult> {
  return requestJson<LinkEmployeeResult>("/api/requests/route/link-employee", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Этап справочника маршрутов (GET /api/settings/routing/catalogs → stages).
export interface RoutingCatalogStage {
  id: number;
  code: string;
  title: string;
  owner_kind: string;
  owner_group: string | null;
  optional: boolean;
  active: boolean;
}

// Справочник маршрутов админа (службы/профили/этапы/шаги). Форме создания
// нужен список этапов (активных) для добавления в маршрут; остальное —
// задел под админку.
export interface RoutingCatalogs {
  stages: RoutingCatalogStage[];
  [key: string]: unknown;
}

// Ключ сортировки списка заявок (клик по заголовку колонки переключает знак).
export type RequestSortKey = "id" | "status" | "dueDate";

export interface RequestSort {
  key: RequestSortKey;
  dir: "asc" | "desc";
}

// Сортировка по умолчанию: новые сверху (id вида REQ-XXXX, строковое убывание
// корректно из-за дополнения нулями).
export const DEFAULT_REQUEST_SORT: RequestSort = { key: "id", dir: "desc" };

// Метка шага в UI: в step_order закодирован блок и режим (см. backend requests.py):
// order = блок*1000 + (100 если параллельный) + позиция. Без кода — обычный порядок.
// Значок параллельности не выводим: режим виден по карточке блока.
export function stepLabel(order: number): string {
  if (order < 1000) return String(order);
  const block = Math.floor(order / 1000) + 1;
  const pos = order % 100;
  return `${block}.${pos}`;
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

// Ответ поиска сотрудников. Пагинация локального справочника: total/page/
// page_size приходят, когда серверная пагинация включена (совместимо: items
// остаётся, остальных полей может не быть — считаем по длине items).
export interface EmployeeSearchResult {
  items: EmployeeHit[];
  // Всего найдено (серверная пагинация; отсутствует — считаем по items).
  total?: number;
  // Текущая страница (1-based).
  page?: number;
  // Размер страницы (page_size из запроса).
  page_size?: number;
  errors?: string[];
  needs_manual_review?: boolean;
}

// Результат POST /api/requests/{id}/print (вариант 1): печатная форма без версий,
// PDF приходит base64 в ответе, документы в БД не пишутся. generated=false +
// reason — НЕ ошибка (нет LibreOffice/шаблона), текст показывает экран.
export interface PrintResult {
  generated: boolean;
  reason?: string | null;
  pdf_b64?: string | null;
}

// Декодирование base64 в Blob (PDF печати из ответа): atob → байты → Blob.
export function base64ToBlob(b64: string, mime: string): Blob {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
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

// Мета вложения заявки (GET /api/requests/{id}/attachments; контракт AttachmentOut
// бэкенда: file_name/size_bytes/uploaded_at, без внутреннего file_path).
export interface AttachmentMeta {
  id: number;
  request_id: string;
  file_name: string;
  mime: string | null;
  size_bytes: number | null;
  uploaded_by: string;
  uploaded_at: string | null;
}

// Комментарий заявки (таблица request_comments; GET/POST /api/requests/{id}/comments).
export interface RequestComment {
  id: string;
  request_id: string;
  author: string;
  body: string;
  at: string;
  // Вид комментария: request | step | rollback (контракт API).
  kind?: string | null;
  // Шаг, к которому привязан комментарий (если есть).
  step_id?: string | null;
}

// Запись истории заявки (GET /api/requests/{id}/history; audit_log).
export interface RequestHistoryItem {
  at: string;
  actor: string;
  action: string;
  entity?: string | null;
  entity_id?: string | null;
  // JSONB «было/стало» либо id/названия затронутых шагов (без раскрытия схемы).
  details?: Record<string, unknown> | null;
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
  execution: ["Согласовано", "К исполнению"],
  done: ["Завершено", "Отклонено", "Отозвано"],
  draft: ["Черновик"],
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

// GET /api/doc-types: виды документов (active_only=true — только активные, селект
// формы создания; без флага — весь справочник, в т.ч. деактивированные).
export async function getDocTypes(activeOnly?: boolean): Promise<DocType[]> {
  const query = activeOnly ? "?active_only=true" : "";
  return requestJson<DocType[]>(`/api/doc-types${query}`);
}

// GET /api/step-groups: группы для ручного конструктора шагов — {id, name}
// (name — наименование из settings, id уходит в owner_group).
export async function getStepGroups(): Promise<StepGroup[]> {
  return requestJson<StepGroup[]>("/api/step-groups");
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

// GET /api/requests/{id}/history: история изменений заявки (видна участникам).
export async function getHistory(id: string): Promise<RequestHistoryItem[]> {
  return requestJson<RequestHistoryItem[]>(`/api/requests/${encodeURIComponent(id)}/history`);
}

// GET /api/requests/{id}/comments: комментарии заявки (отдельная таблица).
export async function getComments(id: string): Promise<RequestComment[]> {
  return requestJson<RequestComment[]>(`/api/requests/${encodeURIComponent(id)}/comments`);
}

// POST /api/requests/{id}/comments: добавить комментарий к заявке.
export async function addComment(id: string, body: string, stepId?: string): Promise<RequestComment> {
  return requestJson<RequestComment>(`/api/requests/${encodeURIComponent(id)}/comments`, {
    method: "POST",
    body: JSON.stringify({ body, step_id: stepId }),
  });
}

// PATCH /api/requests/{id}: правка полей карточки (только админ СЭД).
export async function updateRequest(
  id: string,
  patch: { subject?: string; content?: string; doc_type_code?: string },
): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

// POST /api/requests/{id}/rollback: откат заявки к шагу (только админ СЭД).
export async function rollbackRequest(id: string, toStepId: number): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}/rollback`, {
    method: "POST",
    body: JSON.stringify({ to_step_id: toStepId }),
  });
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

// POST /api/requests/{id}/withdraw: отзыв заявки → Отозвано (ОК/админ).
export async function withdrawRequest(id: string): Promise<RequestOut> {
  return requestJson<RequestOut>(`/api/requests/${encodeURIComponent(id)}/withdraw`, {
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

// DELETE /api/requests/{id}: удаление заявки (только админ; для тестового периода).
export async function deleteRequest(id: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`/api/requests/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

// POST /api/requests: создание заявки от ОК/админа (201 → созданная заявка).
export async function createRequest(body: CreateRequestBody): Promise<RequestOut> {
  return requestJson<RequestOut>("/api/requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// POST /api/requests/route/preview: предпросмотр маршрута по профилю службы
// (только чтение, ничего не создаётся). 422 — неполные/неверные данные,
// 503 — хранилище справочников недоступно.
export async function previewRoute(body: RoutePreviewBody): Promise<RoutePreview> {
  return requestJson<RoutePreview>("/api/requests/route/preview", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// GET /api/settings/routing/catalogs: справочник маршрутов (только админ);
// остальным 403 — вызывающий обязан деградировать (форма не ломается).
export async function getRoutingCatalogs(): Promise<RoutingCatalogs> {
  return requestJson<RoutingCatalogs>("/api/settings/routing/catalogs");
}

// --- Бланки (справочник blanks/blank_steps) --------------------------------

// Бланк для выбора при создании заявки (RouteBlankOut; GET
// /api/requests/route/blanks): только активные бланки, без ПДн. step_count —
// число шагов (для подписи в селекте). autopick — включена ли автоподстановка
// бланка по службе (настройка blank_autopick): если выключена, без выбора
// бланка маршрут собран не будет.
export interface RouteBlank {
  id: number;
  code: string;
  name: string;
  description: string | null;
  layout: string | null;
  step_count: number;
  autopick: boolean;
}

// GET /api/requests/route/blanks: доступные бланки для селекта ОК (роль как у
// создания заявки). Пустой список — выбирать нечего, форма покажет подсказку.
export async function getRouteBlanks(): Promise<RouteBlank[]> {
  return requestJson<RouteBlank[]>("/api/requests/route/blanks");
}

// Бланк справочника админа (строка GET /api/settings/routing/blanks): все,
// включая отключённые. step_count — число шагов в составе.
export interface BlankRow {
  id: number;
  code: string;
  name: string;
  doc_type_code: string | null;
  description: string | null;
  layout: string;
  active: boolean;
  version: number;
  updated_at?: string | null;
  updated_by?: string | null;
  step_count?: number | null;
}

// Тело POST /api/settings/routing/blanks (BlankCatalogIn). layout — встроенный
// пресет печати (office|line), файлов-шаблонов нет.
export interface BlankInput {
  code: string;
  name: string;
  doc_type_code?: string | null;
  description?: string | null;
  layout?: "office" | "line";
  active?: boolean;
}

// Тело PUT /api/settings/routing/blanks/{id} (BlankCatalogUpdateIn): код бланка
// неизменен, поэтому его нет среди полей.
export type BlankPatch = Omit<BlankInput, "code">;

// Шаг бланка с текстом этапа (строка GET /api/settings/routing/blanks/{id}/steps).
// title/stage_lines — из approval_stages; stage_lines — список пунктов, каждый
// может содержать HTML визуального редактора.
export interface BlankStepRow {
  blank_id: number;
  stage_id: number;
  step_order: number;
  optional_override: boolean | null;
  require_comment_override: boolean | null;
  stage_code: string | null;
  title: string | null;
  stage_lines: string[];
  owner_kind: string | null;
  owner_group: string | null;
  optional: boolean | null;
  print_assignee: boolean | null;
  require_comment: boolean | null;
  stage_active: boolean | null;
}

// Шаг в теле PUT /api/settings/routing/blanks/{id}/steps (BlankStepIn).
// null в переопределениях — «взять значение этапа» (optional/require_comment).
export interface BlankStepInput {
  stage_id: number;
  step_order: number;
  optional_override?: boolean | null;
  require_comment_override?: boolean | null;
}

// GET /api/settings/routing/blanks: справочник бланков (только админ, иначе 403).
export async function getBlanks(): Promise<BlankRow[]> {
  return requestJson<BlankRow[]>("/api/settings/routing/blanks");
}

// POST /api/settings/routing/blanks: создать бланк (409 на дубль кода).
export async function createBlank(payload: BlankInput): Promise<{ id: number; code: string }> {
  return requestJson<{ id: number; code: string }>("/api/settings/routing/blanks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// PUT /api/settings/routing/blanks/{id}: частичная правка бланка.
export async function updateBlank(blankId: number, patch: BlankPatch): Promise<{ id: number }> {
  return requestJson<{ id: number }>(`/api/settings/routing/blanks/${blankId}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

// GET /api/settings/routing/blanks/{id}/steps: состав шагов бланка с текстом этапов.
export async function getBlankSteps(blankId: number): Promise<BlankStepRow[]> {
  return requestJson<BlankStepRow[]>(`/api/settings/routing/blanks/${blankId}/steps`);
}

// PUT /api/settings/routing/blanks/{id}/steps: полная замена состава шагов
// (одна транзакция, версия бланка растёт; этап вне маршрутов — 422).
export async function setBlankSteps(
  blankId: number,
  steps: BlankStepInput[],
): Promise<{ blank_id: number; count: number }> {
  return requestJson<{ blank_id: number; count: number }>(
    `/api/settings/routing/blanks/${blankId}/steps`,
    { method: "PUT", body: JSON.stringify({ steps }) },
  );
}

// PUT /api/settings/routing/stages/{id}: правка этапа справочника. title и
// stage_lines приходят из визуального редактора (HTML), поэтому в них
// допустима разметка — её санирует рендер печати (см. api/app/docs.py).
export async function updateRoutingStage(
  stageId: number,
  patch: { title?: string; stage_lines?: string[] },
): Promise<{ id: number; updated: string }> {
  return requestJson<{ id: number; updated: string }>(`/api/settings/routing/stages/${stageId}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

// POST /api/requests/{id}/print: генерация бегунка (ОК/админ). При отсутствии
// LibreOffice/шаблона — generated=false с reason (не ошибка); при успехе PDF —
// base64 (pdf_b64), версии не создаются.
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

// Связка текущего пользователя (GET /api/link_1c_ad/mine): ключ своей
// карточки сотрудника (enterprise|base_code|tab_num) для ссылки инициатора.
// Пусто — связки нет; несколько — неоднозначность (фронт показывает текст).
// is_current — работает сейчас (правило задачи K для дублей).
export interface MyLink {
  enterprise: string;
  base_code: string;
  tab_num: string;
  key: string;
  verified: boolean;
  is_current: boolean | null;
}

// GET /api/link_1c_ad/mine: свои связки АД-1С (sam — из сессии, не из параметров).
export async function getMyLinks(): Promise<MyLink[]> {
  const res = await requestJson<{ items: MyLink[] }>("/api/link_1c_ad/mine");
  return res.items ?? [];
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

// GET /api/attachments/{id}/file: содержимое файла вложением (Blob).
// Прямая ссылка в <a>/<iframe> не подходит: браузер не шлёт Bearer-токен
// за субресурсами — качаем через fetch и показываем blob-адресом.
export async function getAttachmentFile(id: number): Promise<Blob> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  let res: Response;
  try {
    res = await fetch(`/api/attachments/${encodeURIComponent(id)}/file`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new Error("Сервис недоступен");
  }
  if (res.ok) return await res.blob();
  const detail = await readDetail(res);
  switch (res.status) {
    case 401:
      throw new ApiHttpError(401, detail ?? "Сессия истекла");
    case 403:
      throw new ApiHttpError(403, detail ?? "Доступ запрещён");
    case 404:
      throw new ApiHttpError(404, detail ?? "Не найдено");
    case 503:
      throw new ApiHttpError(503, detail ?? "Сервис недоступен");
    default:
      throw new ApiHttpError(res.status, detail ?? "Ошибка сервера");
  }
}

// DELETE /api/attachments/{id}: удаление вложения (автор, admin, sed_admin).
// Успех — 204 без тела, поэтому отдельный fetch без разбора JSON.
export async function deleteAttachment(id: number): Promise<void> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  let res: Response;
  try {
    res = await fetch(`/api/attachments/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    throw new Error("Сервис недоступен");
  }
  if (res.ok) return;
  const detail = await readDetail(res);
  switch (res.status) {
    case 401:
      throw new ApiHttpError(401, detail ?? "Сессия истекла");
    case 403:
      throw new ApiHttpError(403, detail ?? "Доступ запрещён");
    case 404:
      throw new ApiHttpError(404, detail ?? "Не найдено");
    case 503:
      throw new ApiHttpError(503, detail ?? "Сервис недоступен");
    default:
      throw new ApiHttpError(res.status, detail ?? "Ошибка сервера");
  }
}

// GET /api/employees: поиск сотрудников предприятия; без баз 1С — 503.
// Пагинация локального справочника: page (1-based) и pageSize — размер
// страницы; без них сервер отдаёт прежний ответ (до limit).
export async function searchEmployees(
  enterprise: string,
  q: string,
  page?: number,
  pageSize?: number,
): Promise<EmployeeSearchResult> {
  let path = `/api/employees?enterprise=${encodeURIComponent(enterprise)}&q=${encodeURIComponent(q)}`;
  if (page !== undefined && page > 0) path += `&page=${page}`;
  if (pageSize !== undefined && pageSize > 0) path += `&page_size=${pageSize}`;
  return requestJson<EmployeeSearchResult>(path);
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
  phone?: string | null;
  email?: string | null;
  ad_sam?: string | null;
  // Статус стыковки 1С↔AD: linked | match | no_match (без записи в БД).
  ad_status?: string | null;
  // Блок AD для отображения (только ОК/админу): при связке — снапшот,
  // без связки — уникальное точное совпадение ФИО (кандидат на привязку).
  ad?: {
    sam?: string | null;
    display_name?: string | null;
    department?: string | null;
    title?: string | null;
    manager_dn?: string | null;
    mail?: string | null;
  } | null;
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

// Кандидат AD для выбора исполнителя маршрута (GET /api/ad/search, ОК и админ).
export interface AdCandidate {
  sam: string;
  display_name: string;
  department: string;
  title: string;
  mail: string;
}

// GET /api/ad/search: поиск кандидатов AD по ФИО (ОК и админ; для выбора исполнителей маршрута).
export async function searchAd(q: string): Promise<AdCandidate[]> {
  const res = await requestJson<{ items: AdCandidate[] }>(
    `/api/ad/search?q=${encodeURIComponent(q)}`,
  );
  return res.items;
}

// Член группы AD (GET /api/ad/groups/{group}/members; ОК/админ) — состав группы
// для показа в конструкторе маршрута. Значения — из AD, хардкода нет.
export interface AdGroupMember {
  sam: string;
  display_name: string;
  mail: string;
  department: string;
  title: string;
}

export async function getAdGroupMembers(group: string): Promise<AdGroupMember[]> {
  const res = await requestJson<{ items: AdGroupMember[] }>(
    `/api/ad/groups/${encodeURIComponent(group)}/members`,
  );
  return res.items;
}

// Ключ localStorage для оповещения открытого списка заявок об изменениях
// (событие storage в соседнем окне: удаление/создание заявки в попапе).
export const REQUESTS_CHANGED_KEY = "sed:requests-changed";

// Оповестить другие окна/вкладки: список заявок изменился.
export function notifyRequestsChanged(): void {
  try {
    localStorage.setItem(REQUESTS_CHANGED_KEY, String(Date.now()));
  } catch {
    // localStorage недоступен (приватный режим) — список обновится по focus.
  }
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
  /** Сколько строк расхождений записано (миграция 0010). Необязательно:
   *  ответы без этого поля (старый бэкенд, офлайн-моки) считаются пустыми. */
  discrepancies_saved?: number;
  /** Счётчики расхождений по причинам (см. LinkDiscrepancy.reason). */
  discrepancies?: Record<string, number>;
}

// POST /api/link_1c_ad/sync: принудительная автосвязка по точному ФИО (только админ).
export async function syncLinks(): Promise<AdSyncResult> {
  return requestJson<AdSyncResult>("/api/link_1c_ad/sync", { method: "POST" });
}

// Подпись исполнителя шага. Приоритет у ФИО (owner_name): бэкенд присылает его
// для персональных шагов, в т.ч. замены руководителя (resolver=ad_direct_manager,
// исполнитель в assignee) — по одному resolver их отличить нельзя, а потерять
// ФИО нельзя. Без ФИО показываем группу (owner_group, это не ПДн), а для by_user
// (бэкенд кладёт туда логин) — нейтральный текст: логин AD в UI не выводится.
// Логика совпадает с stepOwnerCell в request-card.
function stepOwnerLabel(step: RequestStep | undefined): string {
  if (!step) return "—";
  // Несколько ответственных (миграция 0013): ФИО каждого в списке заявок
  // недоступно (бэкенд отдаёт логины, а список их не резолвит), поэтому
  // показываем число ответственных и прогресс отметок. Считаем по
  // assignee_count: непривилегированному assignees урезан до его логина.
  const total = step.assignee_count ?? step.assignees?.length ?? 0;
  if (total > 1) {
    return `Ответственные: ${total} · ${step.approved_count ?? 0} из ${total} согласовали`;
  }
  if (step.owner_name) return step.owner_name;
  if (step.resolver === "by_user") return "Персональный исполнитель";
  return step.owner_group || "—";
}

// Маппинг RequestOut → строка таблицы: шаг — первый ожидающий, срок — его expires_at.
// Предприятие — названием (enterprise_name), при отсутствии — кодом, иначе прочерк.
// ПДн: у владельца fio=null → маска «Сотрудник № {id}».
export function toRequestRow(request: RequestOut): RequestRow {
  const pending = request.steps.find((s) => s.status === "ожидает");
  const current = pending ?? request.steps[0];
  const expires = current?.expires_at ?? "";
  // Текущий согласующий: исполнитель текущего шага — ФИО или группа, но не логин.
  const ownerName = stepOwnerLabel(current);
  // Ключ карточки сотрудника: employee_key от бэкенда (собран по справочнику),
  // иначе — как раньше, из enterprise|base_code|tab_num самого запроса.
  const employeeKey =
    request.employee_key ||
    (request.enterprise && request.base_code && request.tab_num
      ? `${request.enterprise}|${request.base_code}|${request.tab_num}`
      : "");
  return {
    id: request.id,
    fio: request.fio ?? `Сотрудник № ${request.id}`,
    enterprise: request.enterprise_name ?? request.enterprise ?? "—",
    status: request.status,
    step: stepOwnerLabel(current),
    ownerName,
    dueDate: expires ? expires.slice(0, 10) : "—",
    department: request.department,
    position: request.position,
    steps: request.steps,
    employeeKey,
  };
}

// Сравнение строк по кодам символов (без локали): детерминированный порядок.
function compareText(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

// Сортировка строк списка (чистая функция). id у строки всегда есть (номер REQ-XXXX
// из API), ветка «без id» оставлена как страховка: такие записи остаются в исходном
// порядке. Порядок по умолчанию — новые сверху (см. DEFAULT_REQUEST_SORT).
export function sortRequests(rows: RequestRow[], sort: RequestSort): RequestRow[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      // Страховка для записей без id: индексный порядок (сравнение несогласовано,
      // но такие строки в API не появляются).
      if (!a.row.id || !b.row.id) return a.index - b.index;
      return sign * compareText(a.row[sort.key], b.row[sort.key]) || a.index - b.index;
    })
    .map((item) => item.row);
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


// --- Сопоставление 1С↔AD: расхождения и массовое подтверждение ---------------

/** Строка расхождения автосопоставления (GET /api/link_1c_ad/discrepancies). */
export interface LinkDiscrepancy {
  key: string;
  enterprise: string;
  base_code: string;
  tab_num: string;
  fio: string;
  /** one_c_duplicate — в 1С несколько карточек ФИО; ad_duplicate — в AD несколько
   *  записей ФИО; not_in_ad — в AD нет записи с таким ФИО. */
  reason: string;
  ad_sam: string | null;
  ad_fio: string | null;
  ad_dept: string | null;
  ad_title: string | null;
  one_c_dept: string | null;
  one_c_position: string | null;
  /** Должность/служба карточки 1С совпали с кандидатом AD — связка выглядит верной. */
  recommended: boolean;
  /** Кандидат AD ровно один: строку можно подтвердить пакетом. */
  can_confirm: boolean;
  candidates: string[];
  sibling_tabs: string[];
  detected_at: string | null;
  resolved_at: string | null;
}

export interface DiscrepancyPage {
  items: LinkDiscrepancy[];
  counts: Record<string, number>;
  page: number;
  page_size: number;
  total: number;
}

export interface DiscrepancyFilters {
  reason?: string;
  q?: string;
  onlyOpen?: boolean;
  page?: number;
  pageSize?: number;
}

// GET /api/link_1c_ad/discrepancies: страница расхождений + счётчики по причинам.
export async function getLinkDiscrepancies(
  filters: DiscrepancyFilters = {},
): Promise<DiscrepancyPage> {
  const params = new URLSearchParams();
  if (filters.reason) params.set("reason", filters.reason);
  if (filters.q) params.set("q", filters.q);
  if (filters.onlyOpen === false) params.set("only_open", "false");
  params.set("page", String(filters.page ?? 1));
  params.set("page_size", String(filters.pageSize ?? 50));
  return requestJson<DiscrepancyPage>(`/api/link_1c_ad/discrepancies?${params.toString()}`);
}

// POST /api/link_1c_ad/discrepancies/confirm: подтвердить связки пачкой.
export interface ConfirmResult {
  linked: number;
  skipped: string[];
  errors: string[];
  linked_sams: string[];
}

export async function confirmLinkDiscrepancies(keys: string[]): Promise<ConfirmResult> {
  return requestJson<ConfirmResult>("/api/link_1c_ad/discrepancies/confirm", {
    method: "POST",
    body: JSON.stringify({ keys }),
  });
}

// POST /api/link_1c_ad/sync — ручной запуск сопоставления (уже был в справочнике).
export interface SyncLinksResult {
  synced: boolean;
  scanned: number;
  created: number;
  skipped_linked: number;
  skipped_1c_duplicates: number;
  skipped_ad_no_match: number;
  skipped_ad_duplicates: number;
  errors: string[];
  discrepancies_saved: number;
  discrepancies: Record<string, number>;
}

// Клиент настроек СЭД (волна B4 / B3 Волны 2): GET/PUT /api/settings (только SED_ADMINS).
// Значения — из settings БД (в коде не хардкодятся); токен — Bearer из localStorage.
import { ApiHttpError, getToken } from "./auth-client";
import type { DocType } from "./requests-client";

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

// Шаблон бегунка (settings.doc_templates[]): служба + категория + тело DOCX
// (текстовый фолбэк) либо имя .docx-файла в FILES_DIR/templates/ (file).
export interface SettingsDocTemplate {
  service: string;
  category: string;
  body: string;
  // Имя .docx-файла бланка в FILES_DIR/templates/; null/отсутствует — текстовый body.
  file?: string | null;
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
  // Сущность сотрудников OData (справочник; таб.№ = Code, ФИО = Description).
  employee_entity: string;
  // Сущность организаций (предприятий) OData (дефолт ЗУП).
  organization_entity: string;
  // Поле предприятия в справочнике сотрудников (код = Ref_Key организации).
  employee_org_field: string;
  // Поле таб.№.
  tab_num_field: string;
  // Поле ФИО.
  fio_field: string;
  // Поле подразделения (регистр кадровых данных, $expand).
  department_field: string;
  // Поле должности (регистр кадровых данных, $expand).
  position_field: string;
  // Поле даты приёма (регистр кадровых данных).
  hire_date_field: string;
  // Поле даты увольнения (регистр кадровых данных).
  termination_date_field: string;
  // Регистр текущих кадровых данных (второй запрос карточки).
  hr_entity: string;
  // Поле сотрудника (Ref_Key) в регистре кадровых данных.
  hr_employee_field: string;
  // Поле кода организации (у ЗУП-«Организаций» кода нет — Ref_Key/ИНН).
  organization_code_field: string;
  // Поле названия организации.
  organization_name_field: string;
}

// Расписание регламентной операции (settings.schedule_*): режим повтора
// (interval — каждые N часов, daily — ежедневно в HH:MM) и уведомление о
// выполнении (письмо каждому адресату; body с {{summary}}). Поля опциональны:
// пустое/незаполненное расписание в коде не подставляется («не настроено»).
export interface ScheduleReglament {
  // Режим: interval — повтор каждые interval_hours, daily — ежедневно в daily_time.
  mode?: "interval" | "daily";
  // Интервал повтора в часах (mode=interval).
  interval_hours?: number;
  // Время ежедневного запуска HH:MM (mode=daily).
  daily_time?: string;
  // Отправлять ли письмо о выполненной операции.
  notify?: boolean;
  // Тема письма.
  subject?: string;
  // Тело письма ({{summary}} — сводка операции).
  body?: string;
  // Адресаты уведомления (e-mail).
  recipients?: string[];
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
  // Расписание синхронизации предприятий из 1С (schedule_enterprises_sync).
  schedule_enterprises_sync?: ScheduleReglament | null;
  // Расписание автосвязки 1С↔AD (schedule_ad_links_sync).
  schedule_ad_links_sync?: ScheduleReglament | null;
  // Группы AD, которым разрешён вход (access_groups): список (JSON-массив).
  // null — ключа нет в БД, сервер берёт значение из env (фолбэк).
  access_groups: string[] | null;
  // Группы AD роли «Админ» (admin_groups): список; GET отдаёт эффективное
  // значение (из БД, иначе фолбэк env ADMIN_GROUPS) — отсортированным.
  admin_groups: string[] | null;
  // Группы AD роли «ОК» (hr_groups): список; GET — эффективное значение.
  hr_groups: string[] | null;
  // Группы AD роли «Руководитель ОК» (hr_admin_groups): список; GET — эффективное.
  hr_admin_groups: string[] | null;
  // Сколько сотрудников на страницу справочника (directory_page_size).
  directory_page_size?: number | null;
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
  // Сколько сотрудников на страницу справочника (directory_page_size).
  directory_page_size?: number | null;
}

// Запрос к /api/settings* с Bearer-токеном; ответ — настройки.
// Ошибки: 401 — нет сессии, 403 — доступ закрыт, 422 — неверные типы, 503 — сервис недоступен.
async function requestSettings<T>(path: string, method: "GET" | "PUT" | "POST" | "DELETE", data?: T): Promise<T> {
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
    const verb = method === "PUT" ? "сохранения" : method === "DELETE" ? "удаления" : "загрузки";
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

// Запрос к /api/doc-types с Bearer-токеном (POST/PATCH/DELETE — только админ).
// Ответ может быть пустым (204 при удалении) — отдаём пустой объект.
async function requestDocType<T>(path: string, method: "POST" | "PATCH" | "DELETE", data?: unknown): Promise<T> {
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
  if (res.status === 403) throw new ApiHttpError(403, "Доступ запрещён");
  if (res.status === 409) throw new ApiHttpError(409, "Вид документа используется в заявках");
  if (res.status === 422) throw new ApiHttpError(422, "Неверные данные вида документа");
  if (res.status === 503) throw new ApiHttpError(503, "Сервис недоступен");
  if (!res.ok) throw new ApiHttpError(res.status, "Ошибка операции с видом документа");
  if (res.status === 204) return {} as T;
  return (await res.json()) as T;
}

// POST /api/doc-types: создать вид документа (только админ).
export async function createDocType(data: { code: string; name: string }): Promise<DocType> {
  return requestDocType<DocType>("/api/doc-types", "POST", data);
}

// PATCH /api/doc-types/{code}: переименовать/деактивировать/изменить порядок (только админ).
export async function updateDocType(code: string, patch: Partial<DocType>): Promise<DocType> {
  return requestDocType<DocType>(`/api/doc-types/${encodeURIComponent(code)}`, "PATCH", patch);
}

// DELETE /api/doc-types/{code}: удалить вид документа (только админ; при ссылках
// в заявках бэкенд отвечает 409 — остаётся мягкое отключение is_active=false).
export async function deleteDocType(code: string): Promise<Record<string, unknown>> {
  return requestDocType<Record<string, unknown>>(`/api/doc-types/${encodeURIComponent(code)}`, "DELETE");
}

// ---------------------------------------------------------------------------
// Архивация (бэкапы) — GET/PUT /api/archive, POST /api/archive/backup,
// GET /api/archive/files. Только админ; значения — из settings БД (вкладка
// «Архивация» админки), хранилище — каталог с правами контейнера api/worker.
// ---------------------------------------------------------------------------

// Настройки архивации (GET/PUT /api/archive): место хранения, шаблон имени
// файла, количество хранимых копий и расписание (как у других регламентов).
export interface ArchiveSettingsData {
  // Каталог хранения бэкапов (с правами контейнера api/worker).
  storage_path: string;
  // Шаблон имени файла бэкапа (без хардкода значений — только из settings).
  file_pattern: string;
  // Сколько хранимых копий бэкапов.
  keep_copies: number;
  // Расписание регламентного бэкапа (null — «не настроено»).
  schedule: ScheduleReglament | null;
  // Метка последнего бэкапа (read-only, может отсутствовать).
  backup_at?: string | null;
}

// Файл сохранённого бэкапа (GET /api/archive/files).
export interface BackupFile {
  // Имя файла бэкапа.
  name: string;
  // Размер в байтах.
  size: number;
  // Дата создания (ISO).
  created_at: string;
}

// GET /api/archive: настройки архивации (бэкенд отдаёт archive_* ключи — маппим в форму).
export async function getArchiveSettings(): Promise<ArchiveSettingsData> {
  const raw = await requestSettings<Record<string, unknown>>("/api/archive", "GET");
  return {
    storage_path: String(raw["archive_backup_dir"] ?? ""),
    file_pattern: String(raw["archive_name_template"] ?? ""),
    keep_copies: Number(raw["archive_keep_copies"] ?? 10),
    schedule: (raw["archive_schedule"] as ScheduleReglament | null) ?? null,
    backup_at: raw["archive_backup_at"] != null ? String(raw["archive_backup_at"]) : null,
  };
}

// PUT /api/archive: сохранение настроек архивации (маппим из формы в archive_* ключи).
export async function saveArchiveSettings(data: ArchiveSettingsData): Promise<ArchiveSettingsData> {
  const raw = await requestSettings<Record<string, unknown>>("/api/archive", "PUT", {
    archive_backup_dir: data.storage_path,
    archive_name_template: data.file_pattern,
    archive_keep_copies: data.keep_copies,
    archive_schedule: data.schedule,
  });
  return {
    storage_path: String(raw["archive_backup_dir"] ?? ""),
    file_pattern: String(raw["archive_name_template"] ?? ""),
    keep_copies: Number(raw["archive_keep_copies"] ?? 10),
    schedule: (raw["archive_schedule"] as ScheduleReglament | null) ?? null,
    backup_at: raw["archive_backup_at"] != null ? String(raw["archive_backup_at"]) : null,
  };
}

// Результат ручного бэкапа (POST /api/archive/backup): ok — создан ли файл,
// path/files — путь и список бэкапов, error — причина сбоя (успех — не ошибка).
export interface RunBackupResult {
  ok: boolean;
  path?: string | null;
  files?: BackupFile[];
  error?: string | null;
}

// POST /api/archive/backup: ручной запуск бэкапа (только админ).
export async function runBackup(): Promise<RunBackupResult> {
  return requestSettings<RunBackupResult>("/api/archive/backup", "POST");
}

// GET /api/archive/files: список сохранённых бэкапов (бэкенд отдаёт поле date — маппим в created_at).
export async function listBackups(): Promise<BackupFile[]> {
  const raw = await requestSettings<{ name: string; size: number; date: string }[]>("/api/archive/files", "GET");
  return (raw ?? []).map((f) => ({ name: f.name, size: f.size, created_at: f.date }));
}

// Скачивание файла бэкапа (GET /api/archive/files/{name}/download, только админ).
// Качаем blob-ом с Bearer-токеном и запускаем скачивание: прямая навигация по
// URL дала бы 401 (навигация браузера заголовок не передаёт).
export async function downloadBackup(name: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`/api/archive/files/${encodeURIComponent(name)}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new ApiHttpError(res.status, "Не удалось скачать бэкап");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body?.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// DELETE /api/archive/files/{name}: удалить файл бэкапа (только админ);
// ответ — {ok: true}; при отсутствии файла бэкенд отвечает 404.
export async function deleteBackup(name: string): Promise<{ ok: boolean }> {
  return requestSettings<{ ok: boolean }>(`/api/archive/files/${encodeURIComponent(name)}`, "DELETE");
}

// ---------------------------------------------------------------------------
// Файлы бланков .docx (doc_templates[].file): загрузка/скачивание/удаление/
// предпросмотр. Хранятся в FILES_DIR/templates/, в settings — только имя файла;
// текстовый body остаётся фолбэком, когда file не задан. Только админ.
// ---------------------------------------------------------------------------

// Результат предпросмотра бланка (POST .../preview): generated=false с reason —
// НЕ ошибка (нет LibreOffice/шаблона), текст показывает редактор.
export interface DocTemplatePreviewResult {
  // Флаг успешной генерации PDF.
  generated: boolean;
  // PDF рендера на тестовых данных (base64; при generated=false — null).
  pdf_b64: string | null;
  // Причина, почему бланк не сгенерирован (generated=false).
  reason?: string | null;
}

// POST /api/settings/doc-templates/files/upload: импорт .docx-бланка (multipart;
// Content-Type ставит браузер с границей — вручную нельзя). previous — имя
// старого файла при замене (удаляется на сервере); без него — новая загрузка.
export async function uploadDocTemplateFile(file: File, previous?: string): Promise<{ name: string }> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  const form = new FormData();
  form.append("file", file);
  if (previous) form.append("previous", previous);
  let res: Response;
  try {
    res = await fetch("/api/settings/doc-templates/files/upload", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
  } catch {
    throw new Error("Сервис недоступен");
  }
  if (res.status === 401) throw new ApiHttpError(401, "Сессия истекла");
  if (res.status === 403) throw new ApiHttpError(403, "Настройки — только админам");
  if (res.status === 422) throw new ApiHttpError(422, "Неверные значения настроек");
  if (res.status === 503) throw new ApiHttpError(503, "Сервис настроек недоступен");
  if (!res.ok) throw new ApiHttpError(res.status, "Ошибка загрузки файла бланка");
  return (await res.json()) as { name: string };
}

// GET /api/settings/doc-templates/files/{name}/download: скачивание файла бланка.
// Качаем blob-ом с Bearer-токеном (прямая навигация заголовок не передала бы).
export async function downloadDocTemplateFile(name: string): Promise<void> {
  const token = getToken();
  const res = await fetch(`/api/settings/doc-templates/files/${encodeURIComponent(name)}/download`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new ApiHttpError(res.status, "Не удалось скачать файл бланка");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body?.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

// DELETE /api/settings/doc-templates/files/{name}: удалить файл бланка (только
// админ); ответ — {ok: true}; при отсутствии файла бэкенд отвечает 404.
export async function deleteDocTemplateFile(name: string): Promise<void> {
  await requestSettings<{ ok: boolean }>(`/api/settings/doc-templates/files/${encodeURIComponent(name)}`, "DELETE");
}

// POST /api/settings/doc-templates/files/{name}/preview: рендер файла бланка на
// тестовых данных → PDF (base64 в ответе); generated=false + reason — не ошибка.
export async function previewDocTemplateFile(name: string): Promise<DocTemplatePreviewResult> {
  return requestSettings<DocTemplatePreviewResult>(
    `/api/settings/doc-templates/files/${encodeURIComponent(name)}/preview`,
    "POST",
  );
}

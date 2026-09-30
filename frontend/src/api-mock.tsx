// Мок API-клиента скелета (волна A5).
// Все персональные данные ниже — ВЫМЫШЛЕННЫЕ, для верстки и тестов.
// Урезанная карточка владельцу формируется УЖЕ на уровне мока
// (полные поля сюда не попадают, а не скрываются в UI).
// Интерфейс MockApi повторяет будущие реальные эндпоинты (/api/*),
// чтобы на волне B1–B3 подменить реализацию/MSW без правки экранов.

// Роль текущего пользователя (матрица README п.1).
export type Role = "hr" | "hr_admin" | "owner" | "admin" | "guest";

// Идентификатор папки в дереве слева.
export type FolderId = "agreement" | "revision" | "done" | "mine";

// Папка дерева со счётчиком.
export interface Folder {
  id: FolderId;
  // Название папки.
  title: string;
  // Число заявок в папке.
  count: number;
}

// Фильтры над таблицей заявок.
export interface RequestFilters {
  // Поиск по ФИО/логину (для владельца игнорируется — видит только свои).
  query: string;
  // Фильтр по предприятию (значение — код из настроек, здесь мок).
  enterprise: string;
  // Фильтр по статусу.
  status: string;
}

// Строка таблицы заявок (без ПДн в явном виде для владельца).
export interface RequestRow {
  id: string;
  // Подпись сотрудника: ФИО для ОК/админа, маска для владельца.
  employeeLabel: string;
  enterprise: string;
  status: string;
  // Текущий шаг маршрута.
  step: string;
  // Срок отметки.
  dueDate: string;
}

// Полная карточка сотрудника (только для ОК, руководителей ОК и админов).
export interface EmployeeFull {
  kind: "full";
  requestId: string;
  fio: string;
  enterprise: string;
  tabNum: string;
  department: string;
  position: string;
  hireDate: string;
  // Остаток отпуска — только ОК (см. README п.1).
  vacationBalance: number;
  sam: string;
  mail: string;
  manager: string;
}

// Урезанная карточка для владельца (без ПДн: нет ФИО, таб.№, почты, руководителя).
export interface EmployeeBrief {
  kind: "brief";
  requestId: string;
  // Обезличенная подпись вместо ФИО.
  employeeLabel: string;
  enterprise: string;
  status: string;
  step: string;
  dueDate: string;
}

// Пустой фильтр по умолчанию.
export const EMPTY_FILTERS: RequestFilters = { query: "", enterprise: "", status: "" };

// Вымышленные папки (счётчики для дерева слева).
const MOCK_FOLDERS: Folder[] = [
  { id: "agreement", title: "На согласовании", count: 3 },
  { id: "revision", title: "На доработке", count: 1 },
  { id: "done", title: "Завершённые", count: 5 },
  { id: "mine", title: "Мои задачи", count: 2 },
];

// Вымышленные строки заявок (ФИО вымышлены).
// Владелец получает только свои две строки с маской вместо ФИО.
const MOCK_ROWS: RequestRow[] = [
  { id: "REQ-001", employeeLabel: "Петров Пётр Петрович", enterprise: "Завод «Север»", status: "На согласовании", step: "Бухгалтерия", dueDate: "2026-10-05" },
  { id: "REQ-002", employeeLabel: "Сидорова Анна Сергеевна", enterprise: "Завод «Север»", status: "На согласовании", step: "Безопасность", dueDate: "2026-10-06" },
  { id: "REQ-003", employeeLabel: "Козлов Дмитрий Андреевич", enterprise: "Филиал «Восток»", status: "На доработке", step: "ОК", dueDate: "2026-10-03" },
];

// Маскированные строки для владельца (уже обезличены в моке).
const MOCK_OWNER_ROWS: RequestRow[] = [
  { id: "REQ-001", employeeLabel: "Сотрудник № 101", enterprise: "Завод «Север»", status: "На согласовании", step: "Бухгалтерия", dueDate: "2026-10-05" },
  { id: "REQ-002", employeeLabel: "Сотрудник № 102", enterprise: "Завод «Север»", status: "На согласовании", step: "Безопасность", dueDate: "2026-10-06" },
];

// Вымышленная полная карточка (только ОК/админ).
const MOCK_FULL: Record<string, EmployeeFull> = {
  "REQ-001": {
    kind: "full",
    requestId: "REQ-001",
    fio: "Петров Пётр Петрович",
    enterprise: "Завод «Север»",
    tabNum: "Т-000101",
    department: "Цех № 1",
    position: "Слесарь",
    hireDate: "2020-03-11",
    vacationBalance: 14,
    sam: "petrov.pp",
    mail: "petrov.pp@example.local",
    manager: "Начальник цеха № 1",
  },
  "REQ-002": {
    kind: "full",
    requestId: "REQ-002",
    fio: "Сидорова Анна Сергеевна",
    enterprise: "Завод «Север»",
    tabNum: "Т-000102",
    department: "Склад",
    position: "Кладовщик",
    hireDate: "2021-07-01",
    vacationBalance: 9,
    sam: "sidorova.as",
    mail: "sidorova.as@example.local",
    manager: "Заведующий складом",
  },
};

// Небольшая задержка для имитации сети.
function delay(ms = 30): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Ошибка доступа (аналог 403 от API).
export class MockForbidden extends Error {
  code = 403;
  constructor(message = "Доступ запрещён") {
    super(message);
    this.name = "MockForbidden";
  }
}

// Дефолт TTL мока совпадает с сидами (approval_ttl_days=3); на стенде — из таблицы settings.
const DEFAULT_APPROVAL_TTL_DAYS = 3;
// Дефолт отправителя мока совпадает с сидом smtp_from; на стенде — из таблицы settings.
const DEFAULT_SMTP_FROM = "sed@example.com";

// Мок API: тот же набор методов, что будет у реального клиента.
export const mockApi = {
  // Папки дерева. Гостю — пусто (матрица: остальные — ничего).
  async getFolders(role: Role): Promise<Folder[]> {
    await delay();
    if (role === "guest") return [];
    if (role === "owner") return MOCK_FOLDERS.filter((f) => f.id === "mine");
    return MOCK_FOLDERS;
  },

  // Таблица заявок с учётом фильтров. Владелец — только свои, обезличенные.
  async getRequests(folder: FolderId, filters: RequestFilters, role: Role): Promise<RequestRow[]> {
    await delay();
    if (role === "guest") throw new MockForbidden("Гостю список недоступен");
    const base = role === "owner" ? MOCK_OWNER_ROWS : MOCK_ROWS;
    void folder;
    return base.filter((row) => {
      // Поиск по подписи и предприятию (для владельца подпись — маска).
      if (filters.query && !row.employeeLabel.toLowerCase().includes(filters.query.toLowerCase())) return false;
      if (filters.enterprise && row.enterprise !== filters.enterprise) return false;
      if (filters.status && row.status !== filters.status) return false;
      return true;
    });
  },

  // Карточка сотрудника: полная для ОК/админа, урезанная для владельца.
  async getEmployee(requestId: string, role: Role): Promise<EmployeeFull | EmployeeBrief> {
    await delay();
    if (role === "guest") throw new MockForbidden("Гостю карточка недоступна");
    if (role === "owner") {
      const row = MOCK_OWNER_ROWS.find((r) => r.id === requestId);
      if (!row) throw new MockForbidden("Чужая задача владельцу недоступна");
      // Урезанная карточка собирается здесь, полные поля не покидают мок.
      const brief: EmployeeBrief = {
        kind: "brief",
        requestId: row.id,
        employeeLabel: row.employeeLabel,
        enterprise: row.enterprise,
        status: row.status,
        step: row.step,
        dueDate: row.dueDate,
      };
      return brief;
    }
    const full = MOCK_FULL[requestId];
    if (!full) throw new Error(`Заявка ${requestId} не найдена в моке`);
    return full;
  },

  // Настройки — только админам (заглушка под будущий экран админки).
  async getSettings(
    role: Role,
    overrides?: { approvalTtlDays?: number; smtpFrom?: string },
  ): Promise<{ approvalTtlDays: number; smtpFrom: string }> {
    await delay();
    if (role !== "admin") throw new MockForbidden("Настройки — только админам");
    return {
      approvalTtlDays: overrides?.approvalTtlDays ?? DEFAULT_APPROVAL_TTL_DAYS,
      smtpFrom: overrides?.smtpFrom ?? DEFAULT_SMTP_FROM,
    };
  },
};

// Перехват fetch для /api/* в демо-режиме (замена MSW на волне A5).
// Реальные MSW-обработчики подключаются на волне B4 без смены интерфейса.
export function installMockFetch(role: Role): () => void {
  const original = window.fetch.bind(window);
  const handler = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    if (!url.includes("/api/")) return original(input, init);
    try {
      if (url.includes("/api/folders")) {
        const data = await mockApi.getFolders(role);
        return Response.json(data);
      }
      if (url.includes("/api/requests")) {
        const data = await mockApi.getRequests("agreement", EMPTY_FILTERS, role);
        return Response.json(data);
      }
      return Response.json({ ok: true, mock: true }, { status: 200 });
    } catch (e) {
      const message = e instanceof Error ? e.message : "Ошибка мока";
      const status = e instanceof MockForbidden ? 403 : 500;
      return Response.json({ error: message }, { status });
    }
  };
  window.fetch = handler as typeof window.fetch;
  // Возврат — функция отключения перехвата.
  return () => {
    window.fetch = original;
  };
}

// Сетка скелета (Задача 3): вкладки, дерево папок со счётчиками, тулбар,
// фильтры, таблица. Карточка заявки и создание — в ОТДЕЛЬНЫХ окнах-попах
// (клик по строке / «Создать заявку»), карточка — ?view=request (см. windows.tsx).
// Данные — из реального API (requests-client), тема — из theme.tsx.
import { useEffect, useMemo, useState } from "react";
import {
  DEFAULT_REQUEST_SORT,
  EMPTY_FILTERS,
  filterRequests,
  getEnterprises,
  getFolders,
  getRequests,
  REQUESTS_CHANGED_KEY,
  sortRequests,
  toRequestRow,
} from "./requests-client";
import type {
  Enterprise,
  Folder,
  FolderId,
  RequestFilters,
  RequestRow,
  RequestSort,
  RequestSortKey,
} from "./requests-client";
import type { Role } from "./api-mock";
import { useTheme } from "./theme";
import { AdminSettings } from "./admin-settings";
import { Directory } from "./directory";
import { createUrl, openPopup, requestUrl } from "./windows";

// Вкладки скелета. «Создание» убрана: создание заявки — кнопка «Создать
// заявку» (окно ?view=create), приватный маршрут создания сохранён.
const TABS = ["Заявки", "Справочник", "Настройки"] as const;
type Tab = (typeof TABS)[number];

// Иконки вкладок-модулей и кнопки темы — инлайн-SVG (currentColor,
// viewBox/штрихи как в исходниках дизайн-задачи; каталог `ico/` в проекте
// отсутствует, отдельных svg-файлов нет). Подпись вкладки — текстом кнопки.
function TabIcon(props: { tab: Tab }) {
  const { tab } = props;
  const common = {
    width: 24,
    height: 24,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  } as const;
  if (tab === "Заявки") {
    return (
      <svg {...common}>
        <rect x="5" y="4.5" width="14" height="17" rx="2" />
        <rect x="9" y="2.5" width="6" height="4" rx="1" />
        <path d="M9 14l2.2 2.2L15.5 12" />
      </svg>
    );
  }
  if (tab === "Справочник") {
    return (
      <svg {...common}>
        <path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5v-15Z" />
        <path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3" />
        <path d="M9 7.5h6M9 11h4" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1" />
      <circle cx="15" cy="6" r="2" />
      <circle cx="9" cy="12" r="2" />
      <circle cx="17" cy="18" r="2" />
    </svg>
  );
}

// Иконка кнопки темы — инлайн-SVG (луна/солнце, currentColor).
function ThemeIcon(props: { theme: "light" | "dark" }) {
  const { theme } = props;
  const common = {
    width: 20,
    height: 20,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.75,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
  } as const;
  if (theme === "light") {
    return (
      <svg {...common}>
        <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </svg>
  );
}

// Иконка кнопки «Создать заявку» — инлайн-SVG: плюс в рамке документа
// (мотив заявки, без текстового глифа).
function CreateIcon() {
  return (
    <svg
      width={20}
      height={20}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5" y="4.5" width="14" height="17" rx="2" />
      <rect x="9" y="2.5" width="6" height="4" rx="1" />
      <path d="M12 11v6M9 14h6" />
    </svg>
  );
}

// Иконка кнопки «Обновить список» — инлайн-SVG (стрелка обновления,
// текстовый глиф заменён графикой под макет .ibtn).
function RefreshIcon() {
  return (
    <svg
      width={20}
      height={20}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 3v4h-4" />
    </svg>
  );
}

// Заголовки сортируемых колонок: подпись → ключ сортировки (§3.8 хендоффа).
const SORTABLE_COLUMNS: { label: string; key: RequestSortKey }[] = [
  { label: "№", key: "id" },
  { label: "Статус", key: "status" },
  { label: "Срок", key: "dueDate" },
];

// Подписи ролей в шапке (матрица README п.1).
const ROLE_LABELS: Record<Role, string> = {
  hr: "ОК",
  hr_admin: "Руководитель ОК",
  owner: "Владелец",
  admin: "Админ",
  guest: "Гость",
};

interface SedLayoutProps {
  // Роль текущей сессии (из /auth/me); выбора роли на экране нет.
  role: Role;
  // Выход из системы: очистка сессии и возврат на экран логина.
  onLogout: () => void;
}

// Основной каркас экрана.
export function SedLayout(props: SedLayoutProps) {
  const { role, onLogout } = props;
  const { theme, setTheme } = useTheme();
  const [tab, setTab] = useState<Tab>("Заявки");
  const [folder, setFolder] = useState<FolderId>("agreement");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [filters, setFilters] = useState<RequestFilters>(EMPTY_FILTERS);
  // Сортировка списка: по умолчанию новые сверху (по id вида REQ-XXXX).
  const [sort, setSort] = useState<RequestSort>(DEFAULT_REQUEST_SORT);
  const [error, setError] = useState<string>("");
  // Версия списка: перезагрузка после правок в окне-попе (возврат фокуса).
  const [listVersion, setListVersion] = useState<number>(0);
  // Версия папок: при возврате фокуса перезагружаются и счётчики папок.
  const [foldersVersion, setFoldersVersion] = useState<number>(0);

  // Видимые вкладки по роли: «Настройки» — админу и руководителю ОК (контент),
  // «Справочник» — ОК, руководителю ОК и админу.
  const visibleTabs = useMemo(() => {
    const tabs: Tab[] = ["Заявки"];
    if (role === "hr" || role === "hr_admin" || role === "admin") {
      tabs.push("Справочник");
    }
    if (role === "admin" || role === "hr_admin") tabs.push("Настройки");
    return tabs;
  }, [role]);

  // Загрузка папок при смене роли и при возврате фокуса (закрыт попап — счётчики
  // актуальны); активная папка — первая доступная.
  useEffect(() => {
    let alive = true;
    getFolders()
      .then((data) => {
        if (alive) {
          setFolders(data);
          if (data.length > 0 && !data.some((f) => f.id === folder)) setFolder(data[0].id);
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки папок");
      });
    return () => {
      alive = false;
    };
  }, [role, foldersVersion]);

  // Загрузка предприятий для фильтра таблицы (без хардкод-массивов).
  useEffect(() => {
    let alive = true;
    getEnterprises()
      .then((data) => {
        if (alive) setEnterprises(data);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки предприятий");
      });
    return () => {
      alive = false;
    };
  }, [role]);

  // Загрузка таблицы: GET /api/requests; папка, фильтры и сортировка — на клиенте.
  // Переход на вкладку «Заявки» обновляет список (новая заявка видна сразу).
  // Сортировка здесь не участвует: клик по заголовку не должен перезапрашивать
  // список (сортировка применяется при отрисовке — см. sortedRows).
  useEffect(() => {
    let alive = true;
    getRequests()
      .then((data) => {
        if (alive) {
          setRows(filterRequests(data.map(toRequestRow), folder, filters));
          setError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setRows([]);
          setError(e instanceof Error ? e.message : "Ошибка загрузки заявок");
        }
      });
    return () => {
      alive = false;
    };
  }, [folder, filters, role, tab, listVersion]);

  // Порядок строк для отрисовки: сортировка чисто клиентская, перезагрузки списка
  // не требует.
  const sortedRows = useMemo(() => sortRequests(rows, sort), [rows, sort]);

  // Возврат фокуса в основное окно (закрыт попап) — обновить список и папки.
  useEffect(() => {
    const onFocus = () => {
      setListVersion((v) => v + 1);
      setFoldersVersion((v) => v + 1);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  // Оповещение из другого окна/вкладки (удаление заявки в попапе):
  // localStorage → событие storage. Список и счётчики папок обновляются сразу,
  // не дожидаясь фокуса (создание заявки обновляет список по возврату фокуса).
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== null && e.key !== REQUESTS_CHANGED_KEY) return;
      setListVersion((v) => v + 1);
      setFoldersVersion((v) => v + 1);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // Клик по заголовку сортируемой колонки: та же колонка — сменить знак,
  // новая колонка — сортировка по ней в порядке по умолчанию (возр./убыв.).
  function toggleSort(key: RequestSortKey): void {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "id" ? "desc" : "asc" },
    );
  }

  // Заголовок таблицы зависит от роли (владельцу — без колонок ПДн и без
  // текущего согласующего).
  const columns = useMemo(() => {
    if (role === "owner") return ["№", "Сотрудник (маска)", "Шаг", "Срок"];
    return ["№", "Сотрудник", "Предприятие", "Статус", "Текущий согласующий", "Шаг", "Срок"];
  }, [role]);

  return (
    <div className="sed-shell">
      {/* Ряд модулей одной строкой (как .mods макета): логотип, вкладки,
          справа — тема и «Выйти». Отдельной верхней строки нет. */}
      <nav className="sed-tabs" aria-label="Вкладки">
        <span className="sed-logo">
          <img
            src={theme === "dark" ? "/logo-oswdocs-dark.svg" : "/logo-oswdocs.svg"}
            alt="СЭД — Увольнение"
          />
        </span>
        {visibleTabs.map((name) => (
          <button
            key={name}
            type="button"
            className={tab === name ? "sed-tab sed-tab--active" : "sed-tab"}
            onClick={() => setTab(name)}
          >
            <span className="sed-tab__icon" aria-hidden="true">
              <TabIcon tab={name} />
            </span>
            {name}
          </button>
        ))}
        <span className="sed-tabs__spacer" />
        <button
          type="button"
          className="sed-tab sed-themebtn"
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          title="Светлая/тёмная тема"
          aria-label="Переключить тему"
        >
          <ThemeIcon theme={theme} />
        </button>
        <button type="button" className="sed-tab" onClick={onLogout} title="Выход из системы">
          Выйти
        </button>
      </nav>

      <div className={tab === "Заявки" ? "sed-body" : "sed-body sed-body--wide"}>
        {/* Список папок со счётчиками (плоский; depth — задел под дерево).
            Только на вкладке «Заявки»: в настройках/справочнике дерево заявок
            не показываем (клик по счётчикам там бессмысленен). */}
        {tab === "Заявки" && (
        <aside className="sed-folders" aria-label="Папки заявок">
          {folders.length === 0 && <div className="sed-note">Папок нет (гость)</div>}
          {folders.map((item) => (
            <button
              key={item.id}
              type="button"
              className={
                folder === item.id
                  ? "sed-folder sed-folder--active"
                  : (item.depth ?? 0) > 0
                    ? "sed-folder sed-folder--nested"
                    : "sed-folder"
              }
              style={{ paddingLeft: 10 + (item.depth ?? 0) * 14 }}
              onClick={() => setFolder(item.id)}
            >
              <span>{item.title}</span>
              <span className="sed-folder__count">({item.count})</span>
            </button>
          ))}
          {folders.length > 0 && (
            <div className="sed-note">Дерево папок (уровни — по отступу)</div>
          )}
        </aside>
        )}

        {/* Контент: справочник/настройки — экраны B4, иначе таблица. */}
        <main className="sed-content">
          {tab === "Справочник" && <Directory role={role} />}
          {tab === "Настройки" && <AdminSettings role={role} />}
          {tab === "Заявки" && (
          <>
          {/* Фильтры одним блоком во всю ширину правой колонки, выше тулбара. */}
          <div className="sed-filterbar" aria-label="Фильтры">
            <input
              aria-label="Поиск"
              placeholder="Поиск по ФИО/логину"
              value={filters.query}
              onChange={(e) => setFilters({ ...filters, query: e.target.value })}
            />
            <select
              aria-label="Предприятие"
              value={filters.enterprise}
              onChange={(e) => setFilters({ ...filters, enterprise: e.target.value })}
            >
              <option value="">Все предприятия</option>
              {enterprises.map((ent) => (
                <option key={ent.code} value={ent.code}>
                  {ent.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Статус"
              value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            >
              <option value="">Все статусы</option>
              <option value="Черновик">Черновик</option>
              <option value="На согласовании">На согласовании</option>
              <option value="На доработке">На доработке</option>
              <option value="Согласовано">Согласовано</option>
              <option value="К исполнению">К исполнению</option>
              <option value="Завершено">Завершено</option>
              <option value="Отклонено">Отклонено</option>
              <option value="Отозвано">Отозвано</option>
            </select>
            <button
              type="button"
              className="sed-btn sed-btn--neutral"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              Сбросить фильтры
            </button>
          </div>

          <div className="sed-toolbar" aria-label="Панель действий">
            <button
              type="button"
              className="sed-ibtn"
              onClick={() => openPopup(createUrl())}
              title="Создать заявку"
              aria-label="Создать заявку"
            >
              <CreateIcon />
            </button>
            <button
              type="button"
              className="sed-ibtn"
              onClick={() => setListVersion((v) => v + 1)}
              title="Обновить список"
              aria-label="Обновить список"
            >
              <RefreshIcon />
            </button>
          </div>

          {error && <div role="alert">Ошибка: {error}</div>}

          {/* Таблица заявок: клик по строке — окно карточки заявки (вместо «под списком»),
              клик по заголовку сортируемой колонки — сортировка. */}
          <table className="sed-table sed-table--clickable" aria-label="Заявки">
            <thead>
              <tr>
                {columns.map((col) => {
                  const sortable = SORTABLE_COLUMNS.find((item) => item.label === col);
                  if (!sortable) return <th key={col}>{col}</th>;
                  const active = sort.key === sortable.key;
                  return (
                    <th
                      key={col}
                      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    >
                      <button
                        type="button"
                        className="sed-th-sort"
                        aria-label={`Сортировать по «${col}»`}
                        onClick={() => toggleSort(sortable.key)}
                      >
                        {col}
                        {active ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => {
                return (
                  <tr key={row.id} onClick={() => openPopup(requestUrl(row.id))}>
                    <td>
                      <span className="sed-regnum">{row.id}</span>
                    </td>
                    <td>{row.fio}</td>
                    {role !== "owner" && (
                      <>
                        <td>{row.enterprise}</td>
                        <td>{row.status}</td>
                        <td>{row.ownerName}</td>
                      </>
                    )}
                    <td>{row.step}</td>
                    <td>{row.dueDate}</td>
                  </tr>
                );
              })}
              {rows.length === 0 && !error && (
                <tr>
                  <td colSpan={columns.length}>Заявок нет</td>
                </tr>
              )}
            </tbody>
          </table>

          <div className="sed-note">
            Карточка заявки открывается в отдельном окне (клик по строке).
          </div>
          </>
          )}
        </main>
      </div>
    </div>
  );
}
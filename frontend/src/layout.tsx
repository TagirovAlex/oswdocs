// Сетка скелета (Задача 3): вкладки, дерево папок со счётчиками, тулбар,
// фильтры, таблица. Карточка заявки и создание — в ОТДЕЛЬНЫХ окнах-попах
// (клик по строке / «Создать заявку»), карточка — ?view=request (см. windows.tsx).
// Данные — из реального API (requests-client), тема — из theme.tsx.
import { useEffect, useMemo, useState } from "react";
import {
  EMPTY_FILTERS,
  filterRequests,
  getEnterprises,
  getFolders,
  getRequests,
  toRequestRow,
} from "./requests-client";
import type { Enterprise, Folder, FolderId, RequestFilters, RequestRow } from "./requests-client";
import type { Role } from "./api-mock";
import { useTheme } from "./theme";
import { AdminSettings } from "./admin-settings";
import { CreateForm } from "./create-form";
import { Directory } from "./directory";
import { createUrl, openPopup, requestUrl } from "./windows";

// Вкладки скелета.
const TABS = ["Заявки", "Создание", "Справочник", "Настройки"] as const;
type Tab = (typeof TABS)[number];

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
  const [error, setError] = useState<string>("");
  // Версия списка: перезагрузка после правок в окне-попе (возврат фокуса).
  const [listVersion, setListVersion] = useState<number>(0);
  // Версия папок: при возврате фокуса перезагружаются и счётчики папок.
  const [foldersVersion, setFoldersVersion] = useState<number>(0);

  // Видимые вкладки по роли: «Настройки» — админу и руководителю ОК (контент),
  // «Создание» и «Справочник» — ОК, руководителю ОК и админу.
  const visibleTabs = useMemo(() => {
    const tabs: Tab[] = ["Заявки"];
    if (role === "hr" || role === "hr_admin" || role === "admin") {
      tabs.push("Создание", "Справочник");
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

  // Загрузка таблицы: GET /api/requests, папка и фильтры — на клиенте.
  // Переход на вкладку «Заявки» обновляет список (новая заявка видна сразу).
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

  // Возврат фокуса в основное окно (закрыт попап) — обновить список и папки.
  useEffect(() => {
    const onFocus = () => {
      setListVersion((v) => v + 1);
      setFoldersVersion((v) => v + 1);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  // Заголовок таблицы зависит от роли (владельцу — без колонки ПДн).
  const columns = useMemo(() => {
    if (role === "owner") return ["№", "Сотрудник (маска)", "Шаг", "Срок"];
    return ["№", "Сотрудник", "Предприятие", "Статус", "Шаг", "Срок"];
  }, [role]);

  return (
    <div className="sed-shell">
      {/* Шапка: роль из сессии (без выбора), тема и выход. */}
      <header className="sed-header">
        СЭД — Увольнение (скелет) · роль: {ROLE_LABELS[role]}
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
          style={{ marginLeft: 8, borderColor: "#fff", color: "#fff" }}
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          title="Задел тёмной темы"
        >
          Тема: {theme === "light" ? "светлая" : "тёмная"}
        </button>
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
          style={{ marginLeft: 8, borderColor: "#fff", color: "#fff" }}
          onClick={onLogout}
          title="Выход из системы"
        >
          Выйти
        </button>
      </header>

      {/* Вкладки скелета (по роли сессии). */}
      <nav className="sed-tabs" aria-label="Вкладки">
        {visibleTabs.map((name) => (
          <button
            key={name}
            type="button"
            className={tab === name ? "sed-tab sed-tab--active" : "sed-tab"}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      <div className="sed-body">
        {/* Дерево папок со счётчиками. */}
        <aside className="sed-folders" aria-label="Папки заявок">
          {folders.length === 0 && <div className="sed-note">Папок нет (гость)</div>}
          {folders.map((item) => (
            <button
              key={item.id}
              type="button"
              className={folder === item.id ? "sed-folder sed-folder--active" : "sed-folder"}
              onClick={() => setFolder(item.id)}
            >
              <span>{item.title}</span>
              <span className="sed-folder__count">{item.count}</span>
            </button>
          ))}
        </aside>

        {/* Контент: вкладка создания/настроек — экраны B4, иначе таблица. */}
        <main className="sed-content">
          {tab === "Создание" && <CreateForm role={role} />}
          {tab === "Справочник" && <Directory role={role} />}
          {tab === "Настройки" && <AdminSettings role={role} />}
          {tab === "Заявки" && (
          <>
          <div className="sed-toolbar" aria-label="Панель действий">
            <button type="button" className="sed-btn" onClick={() => openPopup(createUrl())}>
              Создать заявку
            </button>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              Сбросить фильтры
            </button>
          </div>

          {/* Фильтры таблицы (предприятия — из API). */}
          <div className="sed-filters" aria-label="Фильтры">
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
          </div>

          {error && <div role="alert">Ошибка: {error}</div>}

          {/* Таблица заявок: клик по строке — окно карточки заявки (вместо «под списком»). */}
          <table className="sed-table" aria-label="Заявки">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => openPopup(requestUrl(row.id))}
                  style={{ cursor: "pointer" }}
                >
                  <td>{row.id}</td>
                  <td>{row.fio}</td>
                  {role !== "owner" && (
                    <>
                      <td>{row.enterprise}</td>
                      <td>{row.status}</td>
                    </>
                  )}
                  <td>{row.step}</td>
                  <td>{row.dueDate}</td>
                </tr>
              ))}
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
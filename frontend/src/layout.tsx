// Сетка скелета по ориентиру A5: вкладки, дерево папок со счётчиками,
// тулбар, фильтры, таблица заявок.
// Данные берутся из мок API-клиента (api-mock), тема — из theme.tsx.
import { useEffect, useMemo, useState } from "react";
import { EMPTY_FILTERS, mockApi } from "./api-mock";
import type { Folder, FolderId, RequestFilters, RequestRow, Role } from "./api-mock";
import { useTheme } from "./theme";
// Экраны волны B4 (минимальная стыковка к вкладкам и карточке под таблицей).
import { AdminSettings } from "./admin-settings";
import { CreateForm } from "./create-form";
import { EmployeeCard } from "./employee-card";
import { OwnerView } from "./owner-view";

// Вкладки скелета.
const TABS = ["Заявки", "Создание", "Настройки"] as const;
type Tab = (typeof TABS)[number];

interface SedLayoutProps {
  // Роль для демонстрации матрицы (по умолчанию ОК).
  initialRole?: Role;
}

// Основной каркас экрана.
export function SedLayout(props: SedLayoutProps) {
  const { theme, setTheme } = useTheme();
  const [role, setRole] = useState<Role>(props.initialRole ?? "hr");
  const [tab, setTab] = useState<Tab>("Заявки");
  const [folder, setFolder] = useState<FolderId>("agreement");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [filters, setFilters] = useState<RequestFilters>(EMPTY_FILTERS);
  const [error, setError] = useState<string>("");
  // Выбранная заявка для карточки под таблицей (волна B4).
  const [selectedId, setSelectedId] = useState<string>("");

  // Загрузка папок при смене роли.
  useEffect(() => {
    let alive = true;
    mockApi
      .getFolders(role)
      .then((data) => {
        if (alive) setFolders(data);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки папок");
      });
    return () => {
      alive = false;
    };
  }, [role]);

  // Загрузка таблицы при смене папки/фильтров/роли.
  useEffect(() => {
    let alive = true;
    mockApi
      .getRequests(folder, filters, role)
      .then((data) => {
        if (alive) {
          setRows(data);
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
  }, [folder, filters, role]);

  // Заголовок таблицы зависит от роли (владельцу — без колонки ПДн).
  const columns = useMemo(() => {
    if (role === "owner") return ["№", "Сотрудник (маска)", "Шаг", "Срок"];
    return ["№", "Сотрудник", "Предприятие", "Статус", "Шаг", "Срок"];
  }, [role]);

  return (
    <div className="sed-shell">
      {/* Шапка с переключателем роли и темы (демо матрицы ролей). */}
      <header className="sed-header">
        СЭД — Увольнение (скелет) · роль:
        <select
          aria-label="Роль пользователя"
          value={role}
          onChange={(e) => setRole(e.target.value as Role)}
          style={{ marginLeft: 8 }}
        >
          <option value="hr">ОК</option>
          <option value="owner">Владелец</option>
          <option value="admin">Админ</option>
          <option value="guest">Гость</option>
        </select>
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
          style={{ marginLeft: 8, borderColor: "#fff", color: "#fff" }}
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          title="Задел тёмной темы"
        >
          Тема: {theme === "light" ? "светлая" : "тёмная"}
        </button>
      </header>

      {/* Вкладки скелета. */}
      <nav className="sed-tabs" aria-label="Вкладки">
        {TABS.map((name) => (
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
          {tab === "Настройки" && <AdminSettings role={role} />}
          {tab === "Заявки" && (
          <>
          <div className="sed-toolbar" aria-label="Панель действий">
            <button type="button" className="sed-btn">
              Создать заявку
            </button>
            <button type="button" className="sed-btn sed-btn--ghost">
              Печать
            </button>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              Сбросить фильтры
            </button>
          </div>

          {/* Фильтры таблицы. */}
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
              <option value="Завод «Север»">Завод «Север»</option>
              <option value="Филиал «Восток»">Филиал «Восток»</option>
            </select>
            <select
              aria-label="Статус"
              value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            >
              <option value="">Все статусы</option>
              <option value="На согласовании">На согласовании</option>
              <option value="На доработке">На доработке</option>
            </select>
          </div>

          {error && <div role="alert">Ошибка: {error}</div>}

          {/* Таблица заявок. */}
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
                  onClick={() => setSelectedId(row.id)}
                  style={selectedId === row.id ? { background: "var(--sed-primary-soft)" } : undefined}
                >
                  <td>{row.id}</td>
                  <td>{row.employeeLabel}</td>
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
            Скелет волны A5: вкладка «{tab}», данные — мок (вымышленные). Полные экраны — волна B4.
          </div>
          {/* Карточка под таблицей: владельцу — урезанная без ПДн, остальным — полная. */}
          {selectedId && (
            <div style={{ marginTop: 12 }}>
              {role === "owner" ? (
                <OwnerView requestId={selectedId} />
              ) : (
                <EmployeeCard requestId={selectedId} role={role} />
              )}
            </div>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}

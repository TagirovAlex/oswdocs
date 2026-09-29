// Экран списка заявок (волна B4).
// Роль: ОК/админ — полные подписи, владелец — маски (уже обезличены в моке).
// Все персональные данные ниже — ВЫМЫШЛЕННЫЕ.
import { useEffect, useState } from "react";
import { EMPTY_FILTERS, mockApi } from "./api-mock";
import type { RequestFilters, RequestRow, Role } from "./api-mock";

interface RequestsScreenProps {
  // Роль текущего пользователя (матрица README п.1).
  role: Role;
  // Колбэк выбора заявки (открытие карточки под таблицей).
  onSelect?: (requestId: string) => void;
  // Выбранная заявка (подсветка строки).
  selectedId?: string;
}

// Экран списка: фильтры + таблица заявок.
export function RequestsScreen(props: RequestsScreenProps) {
  const { role, onSelect, selectedId } = props;
  const [filters, setFilters] = useState<RequestFilters>(EMPTY_FILTERS);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);

  // Загрузка списка при смене фильтров/роли.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    mockApi
      .getRequests("agreement", filters, role)
      .then((data) => {
        if (alive) {
          setRows(data);
          setError("");
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setRows([]);
          setError(e instanceof Error ? e.message : "Ошибка загрузки заявок");
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [filters, role]);

  // Заголовки с учётом роли (владельцу — без колонок ПДн).
  const showFull = role !== "owner";

  if (error) {
    return <div role="alert">Ошибка: {error}</div>;
  }

  return (
    <section aria-label="Список заявок">
      {/* Фильтры списка. */}
      <div className="sed-filters" aria-label="Фильтры списка">
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
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
          onClick={() => setFilters(EMPTY_FILTERS)}
        >
          Сбросить фильтры
        </button>
      </div>

      {loading && <div className="sed-note">Загрузка…</div>}

      {/* Таблица заявок. */}
      {!loading && (
        <table className="sed-table" aria-label="Заявки">
          <thead>
            <tr>
              <th>№</th>
              <th>{showFull ? "Сотрудник" : "Сотрудник (маска)"}</th>
              {showFull && (
                <>
                  <th>Предприятие</th>
                  <th>Статус</th>
                </>
              )}
              <th>Шаг</th>
              <th>Срок</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                onClick={() => onSelect?.(row.id)}
                style={
                  selectedId === row.id
                    ? { background: "var(--sed-primary-soft)", cursor: "pointer" }
                    : { cursor: onSelect ? "pointer" : "default" }
                }
              >
                <td>{row.id}</td>
                <td>{row.employeeLabel}</td>
                {showFull && (
                  <>
                    <td>{row.enterprise}</td>
                    <td>{row.status}</td>
                  </>
                )}
                <td>{row.step}</td>
                <td>{row.dueDate}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={showFull ? 6 : 4}>Заявок нет</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}

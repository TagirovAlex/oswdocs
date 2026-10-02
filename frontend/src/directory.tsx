// Справочник сотрудников (админ + ОК + руководитель ОК).
// Поиск/список — GET /api/employees (предприятие + подстрока ФИО/таб.№/логина);
// карточка сотрудника — в ОТДЕЛЬНОМ окне (?view=employee&key=…, см. windows.tsx):
// GET /api/employees/card (1С-блок + AD-блок + связка + расхождения);
// привязка 1С→AD (POST /api/link_1c_ad) — ТОЛЬКО админ в окне карточки;
// «Автосвязка 1С↔AD» (POST /api/link_1c_ad/sync) — только админ.
// ПДн в фикстурах/тестах вымышленные; в коде значений нет.
import { useEffect, useState } from "react";
import type { Role } from "./api-mock";
import { getEnterprises, searchEmployees, syncLinks } from "./requests-client";
import type { EmployeeHit, Enterprise } from "./requests-client";
import { getSettingsContent } from "./settings-client";
import { employeeUrl, openPopup } from "./windows";

// Размер страницы справочника по умолчанию: если настройка directory_page_size
// недоступна (нет прав/ключа), берём 50 (без хардкода значения настройки).
const DEFAULT_PAGE_SIZE = 50;
// Размер страницы поиска по запросу (максимум бэкенда): большой, чтобы при
// поиске показывались ВСЕ совпадения, а не дефолтные 50.
const SEARCH_PAGE_SIZE = 500;
// Варианты селекта размера страницы (значение по умолчанию — из настроек).
const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];

interface DirectoryProps {
  // Роль (привязку AD и автосвязку видит только админ; ОК/руководитель — просмотр).
  role: Role;
}

export function Directory(props: DirectoryProps) {
  const { role } = props;
  const canLink = role === "admin";
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [enterprise, setEnterprise] = useState<string>("");
  const [query, setQuery] = useState<string>("");
  const [items, setItems] = useState<EmployeeHit[]>([]);
  const [listError, setListError] = useState<string>("");
  // Пагинация справочника: текущая страница (1-based), размер страницы и
  // всего найдено (total от сервера; нет — по длине выдачи текущей страницы).
  const [page, setPage] = useState<number>(1);
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [total, setTotal] = useState<number>(0);
  // Принудительная автосвязка 1С↔AD по точному ФИО (админ).
  const [syncStatus, setSyncStatus] = useState<string>("");
  const [syncError, setSyncError] = useState<string>("");

  useEffect(() => {
    let alive = true;
    getEnterprises()
      .then((list) => {
        if (!alive) return;
        setEnterprises(list);
        if (list.length > 0) setEnterprise(list[0].code);
      })
      .catch((e: unknown) => {
        if (alive) setListError(e instanceof Error ? e.message : "Ошибка загрузки предприятий");
      });
    return () => {
      alive = false;
    };
  }, []);

  // Размер страницы — из настроек (directory_page_size): читаем только теми,
  // кому доступно (руководитель ОК/админ); иначе — дефолт. Настройка — в
  // settings БД, хардкода нет.
  useEffect(() => {
    let alive = true;
    getSettingsContent()
      .then((data) => {
        if (!alive) return;
        if (typeof data.directory_page_size === "number" && data.directory_page_size > 0) {
          setPageSize(data.directory_page_size);
        }
      })
      .catch(() => {
        // 403/503 (ОК без прав настроек) — дефолтный размер, не ошибка.
      });
    return () => {
      alive = false;
    };
  }, []);

  async function runSearch(targetPage: number, targetPageSize: number): Promise<void> {
    if (!enterprise) {
      setListError("Выберите предприятие");
      return;
    }
    setListError("");
    setSyncStatus("");
    setSyncError("");
    try {
      const result = await searchEmployees(enterprise, query, targetPage, targetPageSize);
      setItems(result.items);
      setPage(targetPage);
      setPageSize(targetPageSize);
      // total от серверной пагинации; нет — считаем по текущей выдаче.
      setTotal(typeof result.total === "number" ? result.total : result.items.length);
      if (result.errors?.length) setListError(result.errors.join("; "));
    } catch (e: unknown) {
      setListError(e instanceof Error ? e.message : "Ошибка поиска сотрудников");
    }
  }

  // «Найти» — с первой страницы (новая выдача). Поиск с запросом идёт большим
  // размером страницы (500), чтобы показывать все совпадения; без запроса
  // (весь список справочника) — выбранный размер страницы. Больше 500
  // совпадений — пагинация работает как есть.
  async function handleSearch(): Promise<void> {
    await runSearch(1, query.trim() !== "" ? SEARCH_PAGE_SIZE : pageSize);
  }

  // Переход на другую страницу: повторный поиск с новой страницей.
  function goToPage(next: number): void {
    if (next < 1) return;
    void runSearch(next, pageSize);
  }

  // Смена размера страницы: возврат на первую страницу и поиск заново.
  function changePageSize(next: number): void {
    if (next === pageSize) return;
    void runSearch(1, next);
  }

  // Число страниц (для счётчика «стр N из M»).
  const totalPages = total > 0 ? Math.max(1, Math.ceil(total / pageSize)) : 0;

  // Клик по строке — окно карточки сотрудника (вместо «под списком»).
  function handleCard(hit: EmployeeHit): void {
    openPopup(employeeUrl(hit.key));
  }

  // Принудительная автосвязка 1С↔AD по точному ФИО (только админ).
  async function handleSyncLinks(): Promise<void> {
    if (!enterprise) {
      setSyncError("Выберите предприятие");
      return;
    }
    setSyncError("");
    setSyncStatus("");
    try {
      const result = await syncLinks();
      setSyncStatus(
        `Автосвязка: создано ${result.created}, просмотрено ${result.scanned}` +
          (result.errors?.length ? `, ошибок: ${result.errors.length}` : ""),
      );
    } catch (e: unknown) {
      setSyncError(e instanceof Error ? e.message : "Ошибка автосвязки");
    }
  }

  // Подпись AD в таблице: логин при связке, признак совпадения при match.
  function adLabel(hit: EmployeeHit): string {
    if (hit.ad_sam) return hit.ad_sam;
    if (hit.ad_status === "match") return "совпадение найдено";
    return "—";
  }

  return (
    <section aria-label="Справочник сотрудников">
      <h3>Справочник сотрудников</h3>
      <div className="sed-filters" aria-label="Фильтры справочника">
        <label>
          Предприятие
          <select aria-label="Предприятие справочника" value={enterprise} onChange={(e) => setEnterprise(e.target.value)}>
            {enterprises.length === 0 && <option value="">—</option>}
            {enterprises.map((ent) => (
              <option key={ent.code} value={ent.code}>
                {ent.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Поиск (ФИО / таб.№ / логин)
          <input
            aria-label="Поиск по справочнику"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSearch();
            }}
          />
        </label>
        <button type="button" className="sed-btn" onClick={handleSearch}>
          Найти
        </button>
        {canLink && (
          <button type="button" className="sed-btn" onClick={handleSyncLinks}>
            Автосвязка 1С↔AD
          </button>
        )}
      </div>
      {syncError && <div role="alert">{syncError}</div>}
      {syncStatus && <div role="status">{syncStatus}</div>}
      {listError && <div role="alert">{listError}</div>}
      <table className="sed-table sed-table--clickable" aria-label="Справочник сотрудников">
        <thead>
          <tr>
            <th>ФИО</th>
            <th>Таб.№</th>
            <th>AD</th>
          </tr>
        </thead>
        <tbody>
          {items.map((hit) => (
            <tr key={hit.key} onClick={() => handleCard(hit)}>
              <td>{hit.fio}</td>
              <td>{hit.tab_num}</td>
              <td>{adLabel(hit)}</td>
            </tr>
          ))}
          {items.length === 0 && !listError && (
            <tr>
              <td colSpan={3}>Нажмите «Найти», чтобы загрузить список.</td>
            </tr>
          )}
        </tbody>
      </table>
      {/* Пагинация справочника: счётчик, навигация и селект размера страницы.
          totalPages=0 до первого поиска — блок не показываем. */}
      {totalPages > 0 && (
        <div className="sed-pager" aria-label="Пагинация справочника">
          <button
            type="button"
            className="sed-btn"
            aria-label="Предыдущая страница"
            disabled={page <= 1}
            onClick={() => goToPage(page - 1)}
          >
            ← Назад
          </button>
          <span role="status">стр {page} из {totalPages}</span>
          <button
            type="button"
            className="sed-btn"
            aria-label="Следующая страница"
            disabled={page >= totalPages}
            onClick={() => goToPage(page + 1)}
          >
            Вперёд →
          </button>
          <label>
            На странице
            <select
              aria-label="Размер страницы справочника"
              value={pageSize}
              onChange={(e) => changePageSize(Number(e.target.value))}
            >
              {/* Значение из настроек может не входить в дефолтные варианты —
                  добавляем его, чтобы селект всегда показывал текущий размер. */}
              {[...new Set([...PAGE_SIZE_OPTIONS, pageSize])]
                .sort((a, b) => a - b)
                .map((size) => (
                  <option key={size} value={size}>
                    {size}
                  </option>
                ))}
            </select>
          </label>
        </div>
      )}
      <div className="sed-note">
        Карточка сотрудника открывается в отдельном окне (клик по строке).
      </div>
    </section>
  );
}
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
import { employeeUrl, openPopup } from "./windows";

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

  async function handleSearch(): Promise<void> {
    if (!enterprise) {
      setListError("Выберите предприятие");
      return;
    }
    setListError("");
    setSyncStatus("");
    setSyncError("");
    try {
      const result = await searchEmployees(enterprise, query);
      setItems(result.items);
      if (result.errors?.length) setListError(result.errors.join("; "));
    } catch (e: unknown) {
      setListError(e instanceof Error ? e.message : "Ошибка поиска сотрудников");
    }
  }

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
      <table className="sed-table" aria-label="Справочник сотрудников">
        <thead>
          <tr>
            <th>ФИО</th>
            <th>Таб.№</th>
            <th>AD</th>
          </tr>
        </thead>
        <tbody>
          {items.map((hit) => (
            <tr key={hit.key} onClick={() => handleCard(hit)} style={{ cursor: "pointer" }}>
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
      <div className="sed-note">
        Карточка сотрудника открывается в отдельном окне (клик по строке).
      </div>
    </section>
  );
}
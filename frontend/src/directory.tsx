// Справочник сотрудников (админ + ОК + руководитель ОК).
// Поиск/список — GET /api/employees (предприятие + подстрока ФИО/таб.№/логина);
// карточка — GET /api/employees/card (1С-блок + AD-блок + связка + расхождения);
// привязка 1С→AD (POST /api/link_1c_ad) — ТОЛЬКО админ, ОК — просмотр.
// ПДн в фикстурах/тестах вымышленные; в коде значений нет.
import { useEffect, useState } from "react";
import type { Role } from "./api-mock";
import {
  createLink,
  getEmployeeCard,
  getEnterprises,
  searchEmployees,
} from "./requests-client";
import type { EmployeeCardData, EmployeeHit, Enterprise } from "./requests-client";

interface DirectoryProps {
  // Роль (привязку AD видит только админ; ОК/руководитель — просмотр).
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
  const [card, setCard] = useState<EmployeeCardData | null>(null);
  const [cardError, setCardError] = useState<string>("");
  const [linkSam, setLinkSam] = useState<string>("");
  const [linkStatus, setLinkStatus] = useState<string>("");
  const [linkError, setLinkError] = useState<string>("");

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
    setCard(null);
    setLinkStatus("");
    setLinkError("");
    try {
      const result = await searchEmployees(enterprise, query);
      setItems(result.items);
      if (result.errors?.length) setListError(result.errors.join("; "));
    } catch (e: unknown) {
      setListError(e instanceof Error ? e.message : "Ошибка поиска сотрудников");
    }
  }

  async function handleCard(hit: EmployeeHit): Promise<void> {
    setCardError("");
    setLinkStatus("");
    setLinkError("");
    try {
      setCard(await getEmployeeCard(enterprise, hit.key.split("|")[1], hit.tab_num));
    } catch (e: unknown) {
      setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
    }
  }

  async function handleLink(): Promise<void> {
    if (!card || linkSam.trim() === "") {
      setLinkError("Введите логин AD (sAMAccountName)");
      return;
    }
    setLinkError("");
    setLinkStatus("");
    try {
      await createLink({
        enterprise: card.enterprise,
        base_code: card.base_code,
        tab_num: card.tab_num ?? "",
        sam: linkSam.trim(),
      });
      setLinkStatus(`Привязано: ${linkSam.trim()}`);
      setLinkSam("");
      // Обновить карточку — покажет блок AD и расхождения.
      setCard(await getEmployeeCard(card.enterprise, card.base_code, card.tab_num ?? ""));
    } catch (e: unknown) {
      setLinkError(e instanceof Error ? e.message : "Ошибка привязки AD");
    }
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
      </div>
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
            <tr key={hit.key} onClick={() => void handleCard(hit)} style={{ cursor: "pointer" }}>
              <td>{hit.fio}</td>
              <td>{hit.tab_num}</td>
              <td>{hit.ad_sam ? hit.ad_sam : "—"}</td>
            </tr>
          ))}
          {items.length === 0 && !listError && (
            <tr>
              <td colSpan={3}>Нажмите «Найти», чтобы загрузить список.</td>
            </tr>
          )}
        </tbody>
      </table>

      {card && (
        <section aria-label="Карточка сотрудника (справочник)">
          <h4>Карточка: {card.fio ?? card.key}</h4>
          <div>
            <strong>1С:</strong> подразделение {card.dept ?? "—"} · должность {card.position ?? "—"} · приём{" "}
            {card.hire_date ?? "—"}
            {card.dismissal_date ? ` · уволен ${card.dismissal_date}` : ""}
          </div>
          {card.link.linked && (
            <div>
              <strong>AD-связка:</strong> {card.link.sam}
              {card.link.verified ? " (подтверждена)" : " (требует сверки)"}
            </div>
          )}
          {card.divergences.length > 0 && (
            <div role="status">Расхождения (истина — 1С): {card.divergences.join(", ")}</div>
          )}
          {card.snapshot_ad && (
            <div>
              <strong>AD:</strong> {card.snapshot_ad.display_name ?? ""} · {card.snapshot_ad.department ?? ""} ·{" "}
              {card.snapshot_ad.title ?? ""}
            </div>
          )}
          {card.ad_error && <div className="sed-note">AD: {card.ad_error}</div>}
          {canLink && (
            <div className="sed-toolbar" style={{ marginTop: 8 }}>
              <input
                aria-label="Логин AD для привязки"
                placeholder="sAMAccountName"
                value={linkSam}
                onChange={(e) => setLinkSam(e.target.value)}
              />
              <button type="button" className="sed-btn" onClick={handleLink}>
                Привязать AD
              </button>
            </div>
          )}
          {linkStatus && <div role="status">{linkStatus}</div>}
          {linkError && <div role="alert">{linkError}</div>}
        </section>
      )}
      {cardError && <div role="alert">{cardError}</div>}
    </section>
  );
}
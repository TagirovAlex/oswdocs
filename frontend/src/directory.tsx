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
  searchAd,
  searchEmployees,
  syncLinks,
} from "./requests-client";
import type { AdCandidate, EmployeeCardData, EmployeeHit, Enterprise } from "./requests-client";

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
  // Интерактивная привязка AD (админ): поиск кандидатов вместо голого логина.
  const [adQuery, setAdQuery] = useState<string>("");
  const [adCandidates, setAdCandidates] = useState<AdCandidate[]>([]);
  const [adSearchError, setAdSearchError] = useState<string>("");
  const [linkStatus, setLinkStatus] = useState<string>("");
  const [linkError, setLinkError] = useState<string>("");
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
    setCard(null);
    setLinkStatus("");
    setLinkError("");
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

  async function handleCard(hit: EmployeeHit): Promise<void> {
    setCardError("");
    setLinkStatus("");
    setLinkError("");
    setAdCandidates([]);
    setAdSearchError("");
    try {
      setCard(await getEmployeeCard(enterprise, hit.key.split("|")[1], hit.tab_num));
    } catch (e: unknown) {
      setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
    }
  }

  // Поиск кандидатов AD по ФИО карточки (админ, интерактивная привязка).
  async function handleAdSearch(): Promise<void> {
    if (!card || adQuery.trim() === "") {
      setAdSearchError("Введите подстроку ФИО для поиска в AD");
      return;
    }
    setAdSearchError("");
    setLinkError("");
    setLinkStatus("");
    try {
      const items = await searchAd(adQuery.trim());
      setAdCandidates(items);
      if (items.length === 0) setAdSearchError("Ничего не найдено в AD");
    } catch (e: unknown) {
      setAdSearchError(e instanceof Error ? e.message : "Ошибка поиска в AD");
    }
  }

  async function handleLink(sam: string): Promise<void> {
    if (!card) return;
    setLinkError("");
    setLinkStatus("");
    try {
      await createLink({
        enterprise: card.enterprise,
        base_code: card.base_code,
        tab_num: card.tab_num ?? "",
        sam,
      });
      setLinkStatus(`Привязано: ${sam}`);
      setAdCandidates([]);
      // Обновить карточку — покажет блок AD и расхождения.
      setCard(await getEmployeeCard(card.enterprise, card.base_code, card.tab_num ?? ""));
    } catch (e: unknown) {
      setLinkError(e instanceof Error ? e.message : "Ошибка привязки AD");
    }
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
            <tr key={hit.key} onClick={() => void handleCard(hit)} style={{ cursor: "pointer" }}>
              <td>{hit.fio}</td>
              <td>{hit.tab_num}</td>
              <td>{hit.ad_sam ?? (hit.ad_status === "match" ? "совпадение найдено" : "—")}</td>
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
          {!card.link.linked && card.ad_status === "match" && (
            <div role="status">Точное совпадение ФИО в AD найдено — подтвердите привязку</div>
          )}
          {!card.link.linked && card.ad_status === "no_match" && (
            <div className="sed-note">Синхронизация не прошла: точного ФИО в AD нет (или дубли)</div>
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
                aria-label="Поиск в AD"
                placeholder="Подстрока ФИО для поиска в AD"
                value={adQuery}
                onChange={(e) => setAdQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleAdSearch();
                }}
              />
              <button type="button" className="sed-btn" onClick={handleAdSearch}>
                Найти в AD
              </button>
            </div>
          )}
          {adSearchError && <div role="alert">{adSearchError}</div>}
          {adCandidates.length > 0 && canLink && (
            <ul aria-label="Кандидаты AD">
              {adCandidates.map((c) => (
                <li key={c.sam}>
                  {c.display_name} · {c.sam} · {c.department} · {c.title}
                  <button type="button" className="sed-btn" onClick={() => void handleLink(c.sam)}>
                    Привязать
                  </button>
                </li>
              ))}
            </ul>
          )}
          {linkStatus && <div role="status">{linkStatus}</div>}
          {linkError && <div role="alert">{linkError}</div>}
        </section>
      )}
      {cardError && <div role="alert">{cardError}</div>}
    </section>
  );
}
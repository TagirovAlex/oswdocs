// Карточка сотрудника (Задача 3): блок 1С + статус AD + расхождения + привязка.
// Используется в окне «карточка сотрудника» (?view=employee&key=…) и в
// справочнике (раньше — под списком). AD-поиск/привязка — только админ.
import { useEffect, useState } from "react";
import type { Role } from "./api-mock";
import { createLink, getEmployeeCard, searchAd } from "./requests-client";
import type { AdCandidate, EmployeeCardData } from "./requests-client";

interface EmployeeCardViewProps {
  // Составной ключ 1С: enterprise | base_code | tab_num.
  enterprise: string;
  baseCode: string;
  tabNum: string;
  role: Role;
}

// Карточка сотрудника справочника (1С + AD + связка).
export function EmployeeCardView(props: EmployeeCardViewProps) {
  const { enterprise, baseCode, tabNum, role } = props;
  const canLink = role === "admin";
  const [card, setCard] = useState<EmployeeCardData | null>(null);
  const [cardError, setCardError] = useState<string>("");
  const [adQuery, setAdQuery] = useState<string>("");
  const [adCandidates, setAdCandidates] = useState<AdCandidate[]>([]);
  const [adSearchError, setAdSearchError] = useState<string>("");
  const [linkStatus, setLinkStatus] = useState<string>("");
  const [linkError, setLinkError] = useState<string>("");

  useEffect(() => {
    let alive = true;
    setCard(null);
    setCardError("");
    setLinkStatus("");
    setLinkError("");
    setAdCandidates([]);
    setAdSearchError("");
    getEmployeeCard(enterprise, baseCode, tabNum)
      .then((data) => {
        if (alive) {
          setCard(data);
          setCardError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
      });
    return () => {
      alive = false;
    };
  }, [enterprise, baseCode, tabNum]);

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
      setCard(await getEmployeeCard(card.enterprise, card.base_code, card.tab_num ?? ""));
    } catch (e: unknown) {
      setLinkError(e instanceof Error ? e.message : "Ошибка привязки AD");
    }
  }

  return (
    <section aria-label="Карточка сотрудника">
      <h3>Карточка: {card?.fio ?? card?.key ?? "…"}</h3>
      {cardError && <div role="alert">{cardError}</div>}
      {!card && !cardError && <div className="sed-note">Загрузка карточки…</div>}
      {card && (
        <>
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
        </>
      )}
    </section>
  );
}
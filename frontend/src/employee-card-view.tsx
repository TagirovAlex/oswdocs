// Учётная карточка сотрудника (Задача 3, редизайн): шапка (ФИО 1С + AD),
// блок «Должность» (1С/AD), «Контактные данные» (телефон/e-mail 1С и e-mail/
// руководитель AD), «Связка 1С↔AD» с привязкой админом и подтверждением
// несохранённых изменений при закрытии. Окно: ?view=employee&key=…
// (см. employee-window.tsx). AD-поиск/привязка — только админ; ОК и
// руководитель ОК — только чтение.
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

// Учётная карточка сотрудника (1С + AD + связка).
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
  // Режим правки связки (кнопка «Изменить» у связанной карточки).
  const [editMode, setEditMode] = useState<boolean>(false);
  // Выбранный кандидат AD — несохранённое изменение связки.
  const [pendingSam, setPendingSam] = useState<string | null>(null);
  const [saving, setSaving] = useState<boolean>(false);
  // Закрыть после успешного сохранения (подтверждение «Сохранить?» при закрытии).
  const [closeAfterSave, setCloseAfterSave] = useState<boolean>(false);

  useEffect(() => {
    let alive = true;
    setCard(null);
    setCardError("");
    setLinkStatus("");
    setLinkError("");
    setAdCandidates([]);
    setAdSearchError("");
    setEditMode(false);
    setPendingSam(null);
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

  // Выбор кандидата AD — несохранённое изменение связки (активирует «Сохранить»).
  function pickCandidate(sam: string): void {
    setPendingSam(sam);
    setLinkError("");
    setLinkStatus("");
  }

  async function handleSave(): Promise<void> {
    if (!card || !pendingSam) return;
    setSaving(true);
    setLinkError("");
    setLinkStatus("");
    try {
      await createLink({
        enterprise: card.enterprise,
        base_code: card.base_code,
        tab_num: card.tab_num ?? "",
        sam: pendingSam,
      });
      setLinkStatus("Связка сохранена");
      const fresh = await getEmployeeCard(card.enterprise, card.base_code, card.tab_num ?? "");
      setCard(fresh);
      setPendingSam(null);
      setEditMode(false);
      setAdCandidates([]);
      setAdQuery("");
      if (closeAfterSave) {
        setCloseAfterSave(false);
        window.close();
      }
    } catch (e: unknown) {
      setLinkError(e instanceof Error ? e.message : "Ошибка сохранения связки");
    } finally {
      setSaving(false);
    }
  }

  // Закрыть: при несохранённой связке — подтверждение (OK → сохранить и закрыть).
  function handleClose(): void {
    if (pendingSam) {
      if (window.confirm("Есть несохранённые изменения связки. Сохранить?")) {
        setCloseAfterSave(true);
        void handleSave();
        return;
      }
    }
    window.close();
  }

  const adInfo = card ? (card.ad ?? card.snapshot_ad) : null;
  const linked = card?.link.linked === true;

  return (
    <section aria-label="Карточка сотрудника">
      <h3>{card?.fio ?? card?.key ?? "…"}</h3>
      {card && <div className="sed-note">{adInfo?.display_name ?? "—"}</div>}
      {cardError && <div role="alert">{cardError}</div>}
      {!card && !cardError && <div className="sed-note">Загрузка карточки…</div>}
      {card && (
        <>
          <div className="sed-table">
            <div>
              <strong>Должность:</strong> 1С: {card.position ?? "—"} · AD: {adInfo?.title ?? "—"}
            </div>
          </div>
          <div className="sed-table">
            <div>
              <strong>Контактные данные:</strong>
            </div>
            <div>1С: телефон {card.phone || "—"}, e-mail {card.email || "—"}</div>
            <div>AD: e-mail {adInfo?.mail || "—"}, руководитель {adInfo?.manager_dn || "—"}</div>
          </div>
          <div className="sed-table">
            <div>
              <strong>Связка 1С↔AD:</strong>
              {linked ? (
                <span>
                  {" "}Связан: {card.link.sam ?? "—"}
                  {card.link.verified ? " (подтверждена)" : " (требует сверки)"}
                </span>
              ) : (
                <>
                  {card.ad_status === "match" && (
                    <div role="status">Точное совпадение ФИО в AD найдено — подтвердите привязку</div>
                  )}
                  {card.ad_status === "no_match" && (
                    <div className="sed-note">Синхронизация не прошла: точного ФИО в AD нет (или дубли)</div>
                  )}
                </>
              )}
            </div>
            {canLink && linked && (
              <button
                type="button"
                className="sed-btn"
                onClick={() => {
                  if (editMode) setPendingSam(null); // отмена правки — сброс кандидата
                  setEditMode(!editMode);
                }}
              >
                {editMode ? "Отменить" : "Изменить"}
              </button>
            )}
          </div>
          {canLink && (!linked || editMode) && (
            <div className="sed-toolbar">
              <input
                aria-label="Поиск в AD"
                placeholder="Подстрока ФИО для поиска в AD"
                value={adQuery}
                onChange={(e) => setAdQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleAdSearch();
                }}
              />
              <button type="button" className="sed-btn" onClick={() => void handleAdSearch()}>
                Найти в AD
              </button>
            </div>
          )}
          {adSearchError && <div role="alert">{adSearchError}</div>}
          {canLink && adCandidates.length > 0 && (
            <ul aria-label="Кандидаты AD">
              {adCandidates.map((c) => (
                <li key={c.sam}>
                  {c.display_name} · {c.sam} · {c.department} · {c.title}
                  <button
                    type="button"
                    className="sed-btn"
                    onClick={() => pickCandidate(c.sam)}
                    aria-pressed={pendingSam === c.sam}
                  >
                    Привязать
                  </button>
                </li>
              ))}
            </ul>
          )}
          {card.divergences.length > 0 && (
            <div role="status">Расхождения (истина — 1С): {card.divergences.join(", ")}</div>
          )}
          {card.ad_error && <div className="sed-note">AD: {card.ad_error}</div>}
          {linkStatus && <div role="status">{linkStatus}</div>}
          {linkError && <div role="alert">{linkError}</div>}
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            {canLink && (
              <button
                type="button"
                className="sed-btn"
                disabled={!pendingSam || saving}
                onClick={() => void handleSave()}
              >
                Сохранить
              </button>
            )}
            <button type="button" className="sed-btn" onClick={handleClose}>
              Закрыть
            </button>
          </div>
        </>
      )}
    </section>
  );
}

// Учётная карточка сотрудника (редизайн): упорядоченная, блоками, с отступами
// от краёв окна. Шапка (ФИО 1С + AD), «Должность» (1С/AD), «Контактные данные»
// (два блока: 1С и AD), «Связка 1С↔AD» — при найденном точном совпадении админ
// подтверждает привязку ОДНИМ кликом («Подтвердить привязку»), ОК видит подсказку
// «подтверждение выполняет админ». Сохранить/Закрыть внизу.
// Окно: ?view=employee&key=… (см. employee-window.tsx). AD-поиск/привязка — только
// админ; ОК и руководитель ОК — только чтение.
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

// Стиль строки «поле — значение» внутри блока.
const rowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  padding: "6px 0",
  borderBottom: "1px solid #e5e7eb",
};
const labelStyle: React.CSSProperties = { opacity: 0.7, whiteSpace: "nowrap" };

// Строка «подпись: значение» в блоке.
function FieldRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={rowStyle}>
      <span style={labelStyle}>{label}</span>
      <span>{value || "—"}</span>
    </div>
  );
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

  // Сохранение связки (sam — выбранный кандидат либо подтверждаемое совпадение).
  async function saveLink(sam: string): Promise<void> {
    if (!card || saving) return;
    setSaving(true);
    setLinkError("");
    setLinkStatus("");
    try {
      await createLink({
        enterprise: card.enterprise,
        base_code: card.base_code,
        tab_num: card.tab_num ?? "",
        sam,
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

  async function handleSave(): Promise<void> {
    if (pendingSam) await saveLink(pendingSam);
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
  const matchSam = !linked && card?.ad_status === "match" ? (adInfo?.sam ?? null) : null;

  return (
    <section
      aria-label="Карточка сотрудника"
      style={{ maxWidth: 760, margin: "0 auto", padding: "28px 32px", background: "#fff", borderRadius: 8 }}
    >
      {/* Шапка: ФИО по 1С (крупно) и ФИО из AD (мельче). */}
      <header>
        <h3 style={{ margin: 0 }}>{card?.fio ?? "…"}</h3>
        {card && (
          <div className="sed-note" style={{ margin: "4px 0 0" }}>
            {adInfo?.display_name ?? "—"}
          </div>
        )}
      </header>

      {cardError && <div role="alert">{cardError}</div>}
      {!card && !cardError && <div className="sed-note">Загрузка карточки…</div>}

      {card && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20, marginTop: 20 }}>
          {/* Блок: должность (1С и AD). */}
          <fieldset style={{ margin: 0, border: "1px solid #e5e7eb", borderRadius: 8, padding: "12px 16px" }}>
            <legend style={{ padding: "0 6px", fontWeight: 600 }}>Должность</legend>
            <FieldRow label="1С" value={card.position ?? "—"} />
            <FieldRow label="AD" value={adInfo?.title ?? "—"} />
          </fieldset>

          {/* Блок: контактные данные — двумя колонками (1С и AD). */}
          <fieldset style={{ margin: 0, border: "1px solid #e5e7eb", borderRadius: 8, padding: "12px 16px" }}>
            <legend style={{ padding: "0 6px", fontWeight: 600 }}>Контактные данные</legend>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>1С</div>
                <FieldRow label="Телефон" value={card.phone ?? "—"} />
                <FieldRow label="E-mail" value={card.email ?? "—"} />
              </div>
              <div>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>AD</div>
                <FieldRow label="E-mail" value={adInfo?.mail ?? "—"} />
                <FieldRow label="Руководитель" value={adInfo?.manager_dn ?? "—"} />
              </div>
            </div>
          </fieldset>

          {/* Блок: связка 1С↔AD. */}
          <fieldset style={{ margin: 0, border: "1px solid #e5e7eb", borderRadius: 8, padding: "12px 16px" }}>
            <legend style={{ padding: "0 6px", fontWeight: 600 }}>Связка 1С↔AD</legend>
            {linked ? (
              <FieldRow
                label="Связан"
                value={`${card.link.sam ?? "—"}${card.link.verified ? " (подтверждена)" : " (требует сверки)"}`}
              />
            ) : (
              <>
                {matchSam && canLink && (
                  <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                    <div role="status">Точное совпадение ФИО в AD найдено: {matchSam}</div>
                    <button
                      type="button"
                      className="sed-btn"
                      disabled={saving}
                      onClick={() => void saveLink(matchSam)}
                    >
                      Подтвердить привязку
                    </button>
                    <span className="sed-note">или найдите учётку вручную ниже</span>
                  </div>
                )}
                {matchSam && !canLink && (
                  <div className="sed-note">
                    Точное совпадение ФИО в AD найдено: {matchSam} — подтверждение выполняет админ
                  </div>
                )}
                {card.ad_status === "no_match" && (
                  <div className="sed-note">
                    Синхронизация не прошла: точного ФИО в AD нет (или дубли)
                  </div>
                )}
              </>
            )}
            {canLink && linked && (
              <div style={{ marginTop: 8 }}>
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
              </div>
            )}
            {canLink && (!linked || editMode) && (
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
                <button type="button" className="sed-btn" onClick={() => void handleAdSearch()}>
                  Найти в AD
                </button>
              </div>
            )}
            {adSearchError && <div role="alert">{adSearchError}</div>}
            {canLink && adCandidates.length > 0 && (
              <ul aria-label="Кандидаты AD" style={{ margin: "8px 0 0", paddingLeft: 20 }}>
                {adCandidates.map((c) => (
                  <li key={c.sam} style={{ marginTop: 6 }}>
                    {c.display_name} · {c.sam} · {c.department} · {c.title}
                    <button
                      type="button"
                      className="sed-btn"
                      onClick={() => pickCandidate(c.sam)}
                      aria-pressed={pendingSam === c.sam}
                      style={{ marginLeft: 8 }}
                    >
                      Привязать
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </fieldset>

          {card.divergences.length > 0 && (
            <div role="status">Расхождения (истина — 1С): {card.divergences.join(", ")}</div>
          )}
          {card.ad_error && <div className="sed-note">AD: {card.ad_error}</div>}
          {linkStatus && <div role="status">{linkStatus}</div>}
          {linkError && <div role="alert">{linkError}</div>}

          {/* Кнопки: Сохранить (админ, при изменении) и Закрыть. */}
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
        </div>
      )}
    </section>
  );
}
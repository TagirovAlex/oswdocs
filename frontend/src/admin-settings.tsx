// Админка настроек: контент (руководитель ОК + админ) и инфра (только админ).
// Значения — из settings БД (GET/PUT /api/settings для админа, /settings/content
// для руководителя ОК), в коде не хардкодятся. Вкладки: Процесс / Справочники /
// Шаблоны (контент) и Инфра (только админ).
import { useEffect, useState } from "react";
import {
  getSettings,
  getSettingsContent,
  saveSettings,
  saveSettingsContent,
  syncEnterprises,
} from "./settings-client";
import type {
  ContentSettingsData,
  SettingsData,
  SettingsDocTemplate,
  SettingsEnterprise,
  SettingsMailTemplate,
  SettingsOnecBase,
  SettingsTemplate,
  SettingsTemplateStep,
} from "./settings-client";
import type { Role } from "./api-mock";

interface AdminSettingsProps {
  // Роль (админ — все вкладки; руководитель ОК — только контент; проверку
  // доступа делает сервер, 403 для остальных).
  role: Role;
}

// Вкладки админки: контент (Процесс/Справочники/Шаблоны) + Инфра (только админ).
const CONTENT_TABS = ["Процесс", "Справочники", "Шаблоны"] as const;
const ALL_TABS = ["Процесс", "Справочники", "Шаблоны", "Инфра"] as const;
type SettingsTab = (typeof ALL_TABS)[number];

// Пара «должность → категория» для формы (порядок строк сохраняется).
interface PositionCategoryPair {
  position: string;
  category: string;
}

// Record (должность → категория) из API → список пар формы.
function pairsFromRecord(record: Record<string, string> | null | undefined): PositionCategoryPair[] {
  return Object.entries(record ?? {}).map(([position, category]) => ({ position, category }));
}

// Список пар формы → Record для PUT (пустая должность не пишется).
function recordFromPairs(pairs: PositionCategoryPair[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const pair of pairs) {
    if (pair.position.trim() !== "") out[pair.position] = pair.category;
  }
  return out;
}

// Редактор предприятий: список code+name, добавить/удалить/переименовать.
function EnterprisesEditor(props: { value: SettingsEnterprise[]; onChange: (v: SettingsEnterprise[]) => void }) {
  const { value, onChange } = props;
  function update(index: number, patch: Partial<SettingsEnterprise>): void {
    onChange(value.map((ent, i) => (i === index ? { ...ent, ...patch } : ent)));
  }
  return (
    <fieldset>
      <legend>Предприятия</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((ent, i) => (
        <div key={i} style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <label>
            Код
            <input
              aria-label={`Код предприятия ${i + 1}`}
              value={ent.code}
              onChange={(e) => update(i, { code: e.target.value })}
            />
          </label>
          <label>
            Название
            <input
              aria-label={`Название предприятия ${i + 1}`}
              value={ent.name}
              onChange={(e) => update(i, { name: e.target.value })}
            />
          </label>
          <button
            type="button"
            className="sed-btn sed-btn--ghost"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
          >
            Удалить предприятие
          </button>
        </div>
      ))}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button type="button" className="sed-btn" onClick={() => onChange([...value, { code: "", name: "" }])}>
          Добавить предприятие
        </button>
      </div>
    </fieldset>
  );
}

// Редактор групп доступа (владельцев шагов): список строк, добавить/удалить.
function GroupsEditor(props: { value: string[]; onChange: (v: string[]) => void }) {
  const { value, onChange } = props;
  return (
    <fieldset>
      <legend>Группы доступа (владельцы шагов)</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((group, i) => (
        <div key={i} style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
          <input
            aria-label={`Группа доступа ${i + 1}`}
            value={group}
            onChange={(e) => onChange(value.map((g, j) => (j === i ? e.target.value : g)))}
          />
          <button
            type="button"
            className="sed-btn sed-btn--ghost"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
          >
            Удалить группу
          </button>
        </div>
      ))}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button type="button" className="sed-btn" onClick={() => onChange([...value, ""])}>
          Добавить группу
        </button>
      </div>
    </fieldset>
  );
}

// Редактор соответствий «должность → категория»: пары, добавить/удалить/изменить.
function PositionCategoryEditor(props: { value: PositionCategoryPair[]; onChange: (v: PositionCategoryPair[]) => void }) {
  const { value, onChange } = props;
  function update(index: number, patch: Partial<PositionCategoryPair>): void {
    onChange(value.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  }
  return (
    <fieldset>
      <legend>Должность → категория</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((pair, i) => (
        <div key={i} style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <label>
            Должность
            <input
              aria-label={`Должность ${i + 1}`}
              value={pair.position}
              onChange={(e) => update(i, { position: e.target.value })}
            />
          </label>
          <label>
            Категория
            <input
              aria-label={`Категория ${i + 1}`}
              value={pair.category}
              onChange={(e) => update(i, { category: e.target.value })}
            />
          </label>
          <button
            type="button"
            className="sed-btn sed-btn--ghost"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
          >
            Удалить соответствие
          </button>
        </div>
      ))}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="sed-btn"
          onClick={() => onChange([...value, { position: "", category: "" }])}
        >
          Добавить соответствие
        </button>
      </div>
    </fieldset>
  );
}

// Редактор шаблонов маршрутов: карточка (служба, категория, шаги owner_group).
// Минимальный редактор: добавить/удалить шаблон, добавить/удалить/переименовать шаг.
function TemplatesEditor(props: { value: SettingsTemplate[]; onChange: (v: SettingsTemplate[]) => void }) {
  const { value, onChange } = props;
  function updateTemplate(index: number, patch: Partial<SettingsTemplate>): void {
    onChange(value.map((t, i) => (i === index ? { ...t, ...patch } : t)));
  }
  function addStep(index: number): void {
    const step: SettingsTemplateStep = { owner_group: "" };
    updateTemplate(index, { steps: [...value[index].steps, step] });
  }
  function updateStep(templateIndex: number, stepIndex: number, ownerGroup: string): void {
    onChange(
      value.map((t, i) =>
        i !== templateIndex
          ? t
          : { ...t, steps: t.steps.map((s, j) => (j !== stepIndex ? s : { ...s, owner_group: ownerGroup })) },
      ),
    );
  }
  function removeStep(templateIndex: number, stepIndex: number): void {
    onChange(
      value.map((t, i) => (i !== templateIndex ? t : { ...t, steps: t.steps.filter((_, j) => j !== stepIndex) })),
    );
  }
  return (
    <fieldset>
      <legend>Шаблоны маршрутов</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((template, i) => (
        <div key={i} style={{ border: "1px solid #ccc", marginTop: 8, padding: 8 }}>
          <label style={{ display: "block" }}>
            Служба
            <input
              aria-label={`Служба шаблона ${i + 1}`}
              value={template.service}
              onChange={(e) => updateTemplate(i, { service: e.target.value })}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Категория
            <input
              aria-label={`Категория шаблона ${i + 1}`}
              value={template.category}
              onChange={(e) => updateTemplate(i, { category: e.target.value })}
            />
          </label>
          <div style={{ marginTop: 8 }}>
            {template.steps.length === 0 && <div className="sed-note">шагов нет</div>}
            {template.steps.map((step, j) => (
              <div key={j} style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
                <input
                  aria-label={`Владелец шага ${i + 1}.${j + 1}`}
                  value={step.owner_group}
                  onChange={(e) => updateStep(i, j, e.target.value)}
                />
                <button type="button" className="sed-btn sed-btn--ghost" onClick={() => removeStep(i, j)}>
                  Удалить шаг
                </button>
              </div>
            ))}
          </div>
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            <button type="button" className="sed-btn" onClick={() => addStep(i)}>
              Добавить шаг
            </button>
          </div>
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
            >
              Удалить шаблон
            </button>
          </div>
        </div>
      ))}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="sed-btn"
          onClick={() => onChange([...value, { service: "", category: "", steps: [] }])}
        >
          Добавить шаблон
        </button>
      </div>
    </fieldset>
  );
}

// Редактор бланков бегунков (doc_templates): служба + категория + тело DOCX (Jinja).
function DocTemplatesEditor(props: { value: SettingsDocTemplate[]; onChange: (v: SettingsDocTemplate[]) => void }) {
  const { value, onChange } = props;
  function update(index: number, patch: Partial<SettingsDocTemplate>): void {
    onChange(value.map((doc, i) => (i === index ? { ...doc, ...patch } : doc)));
  }
  return (
    <fieldset>
      <legend>Бланки бегунков (doc_templates)</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((doc, i) => (
        <div key={i} style={{ border: "1px solid #ccc", marginTop: 8, padding: 8 }}>
          <label style={{ display: "block" }}>
            Служба
            <input
              aria-label={`Служба бланка ${i + 1}`}
              value={doc.service}
              onChange={(e) => update(i, { service: e.target.value })}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Категория
            <input
              aria-label={`Категория бланка ${i + 1}`}
              value={doc.category}
              onChange={(e) => update(i, { category: e.target.value })}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Тело бегунка (Jinja-плейсхолдеры)
            <textarea
              aria-label={`Тело бланка ${i + 1}`}
              rows={4}
              value={doc.body}
              onChange={(e) => update(i, { body: e.target.value })}
            />
          </label>
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
            >
              Удалить бланк
            </button>
          </div>
        </div>
      ))}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="sed-btn"
          onClick={() => onChange([...value, { service: "", category: "", body: "" }])}
        >
          Добавить бланк
        </button>
      </div>
    </fieldset>
  );
}

// Редактор писем (mail_templates): код события + тема + HTML-тело (Jinja).
function MailTemplatesEditor(props: { value: SettingsMailTemplate[]; onChange: (v: SettingsMailTemplate[]) => void }) {
  const { value, onChange } = props;
  function update(index: number, patch: Partial<SettingsMailTemplate>): void {
    onChange(value.map((mail, i) => (i === index ? { ...mail, ...patch } : mail)));
  }
  return (
    <fieldset>
      <legend>Письма (mail_templates)</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((mail, i) => (
        <div key={i} style={{ border: "1px solid #ccc", marginTop: 8, padding: 8 }}>
          <label style={{ display: "block" }}>
            Код события (code)
            <input
              aria-label={`Код письма ${i + 1}`}
              value={mail.code}
              onChange={(e) => update(i, { code: e.target.value })}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Тема письма (subject)
            <input
              aria-label={`Тема письма ${i + 1}`}
              value={mail.subject}
              onChange={(e) => update(i, { subject: e.target.value })}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            HTML-тело (body_html; Jinja-плейсхолдеры)
            <textarea
              aria-label={`HTML письма ${i + 1}`}
              rows={4}
              value={mail.body_html}
              onChange={(e) => update(i, { body_html: e.target.value })}
            />
          </label>
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
            >
              Удалить письмо
            </button>
          </div>
        </div>
      ))}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="sed-btn"
          onClick={() => onChange([...value, { code: "", subject: "", body_html: "" }])}
        >
          Добавить письмо
        </button>
      </div>
    </fieldset>
  );
}

// Админка: контент (TTL/флаги, справочники, шаблоны) + инфра (сессия/сканы/SMTP).
// Админ видит все вкладки (GET/PUT /api/settings), руководитель ОК — только
// контент (GET/PUT /api/settings/content). Всё — из settings БД.
export function AdminSettings(props: AdminSettingsProps) {
  const { role } = props;
  const isAdmin = role === "admin";
  const [activeTab, setActiveTab] = useState<SettingsTab>("Процесс");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [saveError, setSaveError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  // Значения формы — только из API (без хардкод-дефолтов).
  const [sessionTtl, setSessionTtl] = useState<number | null>(null);
  const [ttl, setTtl] = useState<number | null>(null);
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [maxMb, setMaxMb] = useState<number | null>(null);
  const [paperRequired, setPaperRequired] = useState<boolean | null>(null);
  const [smtpHost, setSmtpHost] = useState<string | null>(null);
  const [smtpPort, setSmtpPort] = useState<number | null>(null);
  const [smtpFrom, setSmtpFrom] = useState<string | null>(null);
  const [smtpUser, setSmtpUser] = useState<string | null>(null);
  // Пароль релея в форме: поле всегда пустое; smtpPasswordSet — признак «задан».
  const [smtpPassword, setSmtpPassword] = useState<string>("");
  const [smtpPasswordSet, setSmtpPasswordSet] = useState<boolean>(false);
  const [requireComment, setRequireComment] = useState<boolean | null>(null);
  const [enterprises, setEnterprises] = useState<SettingsEnterprise[]>([]);
  const [adGroups, setAdGroups] = useState<string[]>([]);
  const [positionCategory, setPositionCategory] = useState<PositionCategoryPair[]>([]);
  const [templates, setTemplates] = useState<SettingsTemplate[]>([]);
  const [docTemplates, setDocTemplates] = useState<SettingsDocTemplate[]>([]);
  const [mailTemplates, setMailTemplates] = useState<SettingsMailTemplate[]>([]);
  // Базы 1С: поля формы + параллельный признак «пароль задан» для placeholder.
  const [onecBases, setOnecBases] = useState<SettingsOnecBase[]>([]);
  const [onecBasesSet, setOnecBasesSet] = useState<boolean[]>([]);
  // Дата/время последней синхронизации предприятий (read-only).
  const [onecSyncedAt, setOnecSyncedAt] = useState<string | null>(null);
  // Статус принудительной синхронизации предприятий из баз 1С.
  const [syncStatus, setSyncStatus] = useState<string>("");
  const [syncError, setSyncError] = useState<string>("");
  // Эскалация в форме не редактируется (отдельная волна), передаём как загружено.
  const [positionEscalation, setPositionEscalation] = useState<Record<string, number> | null>(null);

  // Загрузка: админ — полный объект /api/settings, руководитель ОК — контент
  // /api/settings/content (инфра-поля у него не приходят и не показываются).
  useEffect(() => {
    let alive = true;
    const load = isAdmin ? getSettings() : getSettingsContent();
    load
      .then((data) => {
        if (!alive) return;
        if (isAdmin) {
          const full = data as SettingsData;
          setSessionTtl(full.session_ttl_minutes);
          setRetentionDays(full.scan_retention_days);
          setMaxMb(full.scan_max_mb);
          setSmtpHost(full.smtp_host);
          setSmtpPort(full.smtp_port);
          setSmtpFrom(full.smtp_from);
          setSmtpUser(full.smtp_user ?? "");
          // Пароль из API не приходит (маска/null): поле пустое, только признак.
          setSmtpPassword("");
          setSmtpPasswordSet(full.smtp_password !== null);
          // Базы 1С: пароль очищаем, признак «задан» — из маски/None.
          setOnecBases((full.onec_bases ?? []).map((b) => ({ ...b, password: "" })));
          setOnecBasesSet((full.onec_bases ?? []).map((b) => b.password !== null));
          setOnecSyncedAt(full.onec_enterprises_synced_at);
        }
        setTtl(data.approval_ttl_days);
        setPaperRequired(data.require_paper_signature);
        setRequireComment(data.require_comment);
        setEnterprises(data.enterprises ?? []);
        setAdGroups(data.allowed_ad_groups ?? []);
        setPositionCategory(pairsFromRecord(data.position_to_category));
        setPositionEscalation(data.position_escalation);
        setTemplates(data.templates ?? []);
        setDocTemplates(data.doc_templates ?? []);
        setMailTemplates(data.mail_templates ?? []);
        setError("");
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки настроек");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [isAdmin]);

  // Сохранение: тем же объектом, каким грузили (полным для админа, контентным
  // для руководителя ОК); ошибки 403/422/503 приходят понятным текстом из клиента.
  async function handleSave(): Promise<void> {
    if (busy) return;
    setSaveError("");
    setSaved("");
    // Пустые поля (ключа нет в БД) — честно просим заполнить, а не подставляем дефолты.
    const contentMissing = ttl === null || paperRequired === null;
    const infraMissing =
      isAdmin &&
      (sessionTtl === null || retentionDays === null || maxMb === null || smtpHost === null || smtpPort === null || smtpFrom === null);
    if (contentMissing || infraMissing) {
      setSaveError("Заполните все поля настроек (значения хранятся в settings БД)");
      return;
    }
    const content: ContentSettingsData = {
      approval_ttl_days: ttl,
      require_comment: requireComment ?? false,
      require_paper_signature: paperRequired,
      enterprises,
      allowed_ad_groups: adGroups,
      position_to_category: recordFromPairs(positionCategory),
      position_escalation: positionEscalation,
      templates,
      doc_templates: docTemplates,
      mail_templates: mailTemplates,
    };
    setBusy(true);
    try {
      if (isAdmin) {
        const full: SettingsData = {
          ...content,
          session_ttl_minutes: sessionTtl,
          scan_retention_days: retentionDays,
          scan_max_mb: maxMb,
          smtp_host: smtpHost,
          smtp_port: smtpPort,
          smtp_from: smtpFrom,
          smtp_user: smtpUser ?? "",
          // Пустое значение — сервер сохранит текущий пароль (не перезапишет).
          smtp_password: smtpPassword,
          // Пароль базы — как введено (пустое → сервер сохранит текущий).
          onec_bases: onecBases,
          // Read-only: пишет синхронизация (сервер игнорирует на PUT).
          onec_enterprises_synced_at: onecSyncedAt,
        };
        const result = await saveSettings(full);
        setSaved(
          `Сохранено: TTL=${result.approval_ttl_days} дн., сканы ${result.scan_retention_days} дн./${result.scan_max_mb} МБ, от ${result.smtp_from}, предприятий ${result.enterprises?.length ?? 0}, групп ${result.allowed_ad_groups?.length ?? 0}, шаблонов ${result.templates?.length ?? 0}`,
        );
      } else {
        const result = await saveSettingsContent(content);
        setSaved(
          `Сохранено: TTL=${result.approval_ttl_days} дн., предприятий ${result.enterprises?.length ?? 0}, групп ${result.allowed_ad_groups?.length ?? 0}, шаблонов ${result.templates?.length ?? 0}`,
        );
      }
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Ошибка сохранения настроек");
    } finally {
      setBusy(false);
    }
  }

  // Принудительная синхронизация предприятий из 1С (POST /settings/enterprises/sync).
  async function handleSyncEnterprises(): Promise<void> {
    setSyncError("");
    setSyncStatus("");
    try {
      const result = await syncEnterprises();
      setSyncStatus(`Обновлено: ${result.count} предприятий`);
    } catch (e: unknown) {
      setSyncError(e instanceof Error ? e.message : "Ошибка синхронизации предприятий");
    }
  }

  // Ошибка загрузки (в т.ч. 403 для не-админа) — alert вместо формы.
  if (error) return <div role="alert">Ошибка: {error}</div>;
  if (loading) return <div className="sed-note">Загрузка настроек…</div>;

  const tabs: readonly SettingsTab[] = isAdmin ? ALL_TABS : CONTENT_TABS;

  return (
    <section aria-label="Настройки СЭД">
      <h3>Настройки{isAdmin ? "" : " (Режим: руководитель ОК)"}</h3>
      <div className="sed-note">Значения хранятся в settings БД и применяются без пересборки.</div>
      <nav className="sed-tabs" aria-label="Вкладки настроек">
        {tabs.map((name) => (
          <button
            key={name}
            type="button"
            className={activeTab === name ? "sed-tab sed-tab--active" : "sed-tab"}
            onClick={() => setActiveTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      {activeTab === "Процесс" && (
        <fieldset>
          <legend>Процесс</legend>
          <label style={{ display: "block", marginTop: 8 }}>
            TTL отметок, дней (approval_ttl_days)
            <input
              aria-label="TTL отметок"
              type="number"
              value={ttl ?? ""}
              onChange={(e) => setTtl(Number(e.target.value))}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            <input
              type="checkbox"
              aria-label="Требовать бумажное заявление"
              checked={paperRequired ?? false}
              onChange={(e) => setPaperRequired(e.target.checked)}
            />
            Требовать бумажное заявление (require_paper_signature)
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            <input
              type="checkbox"
              aria-label="Комментарий обязателен при согласовании"
              checked={requireComment ?? false}
              onChange={(e) => setRequireComment(e.target.checked)}
            />
            Комментарий обязателен при согласовании (require_comment)
          </label>
        </fieldset>
      )}

      {activeTab === "Справочники" && (
        <>
          <EnterprisesEditor value={enterprises} onChange={setEnterprises} />
          <GroupsEditor value={adGroups} onChange={setAdGroups} />
          <PositionCategoryEditor value={positionCategory} onChange={setPositionCategory} />
        </>
      )}

      {activeTab === "Шаблоны" && (
        <>
          <TemplatesEditor value={templates} onChange={setTemplates} />
          <DocTemplatesEditor value={docTemplates} onChange={setDocTemplates} />
          <MailTemplatesEditor value={mailTemplates} onChange={setMailTemplates} />
        </>
      )}

      {activeTab === "Инфра" && isAdmin && (
        <fieldset>
          <legend>Инфра (сессия, сканы, SMTP)</legend>
          <label style={{ display: "block", marginTop: 8 }}>
            Длительность сессии, минут (session_ttl_minutes; 600 = 10 часов)
            <input
              aria-label="Длительность сессии"
              type="number"
              value={sessionTtl ?? ""}
              onChange={(e) => setSessionTtl(Number(e.target.value))}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Хранение сканов, дней (scan_retention_days)
            <input
              aria-label="Хранение сканов"
              type="number"
              value={retentionDays ?? ""}
              onChange={(e) => setRetentionDays(Number(e.target.value))}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Лимит скана, МБ (scan_max_mb)
            <input
              aria-label="Лимит скана"
              type="number"
              value={maxMb ?? ""}
              onChange={(e) => setMaxMb(Number(e.target.value))}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Хост SMTP-релея (smtp_host)
            <input
              aria-label="Хост SMTP-релея"
              type="text"
              value={smtpHost ?? ""}
              onChange={(e) => setSmtpHost(e.target.value)}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Порт SMTP-релея (smtp_port)
            <input
              aria-label="Порт SMTP-релея"
              type="number"
              value={smtpPort ?? ""}
              onChange={(e) => setSmtpPort(Number(e.target.value))}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Отправитель уведомлений, e-mail (smtp_from)
            <input
              aria-label="Отправитель уведомлений"
              type="email"
              value={smtpFrom ?? ""}
              onChange={(e) => setSmtpFrom(e.target.value)}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Логин SMTP-релея (smtp_user; пусто — отправка без авторизации)
            <input
              aria-label="Логин SMTP-релея"
              type="text"
              value={smtpUser ?? ""}
              onChange={(e) => setSmtpUser(e.target.value)}
            />
          </label>
          <label style={{ display: "block", marginTop: 8 }}>
            Пароль SMTP-релея (smtp_password; оставьте пустым, чтобы сохранить текущий)
            <input
              aria-label="Пароль SMTP-релея"
              type="password"
              placeholder={smtpPasswordSet ? "задан (не менять)" : "не задан"}
              value={smtpPassword}
              onChange={(e) => setSmtpPassword(e.target.value)}
            />
          </label>
        </fieldset>
      )}

      {activeTab === "Инфра" && isAdmin && (
        <fieldset>
          <legend>1С-базы (подключения)</legend>
          <div className="sed-note">
            В каждой базе может быть несколько предприятий: справочник предприятий и
            маппинг «предприятие → базы» собирает синхронизация «Обновить из 1С»
            (сущность организаций базы; еженедельно + по кнопке). Имена сущностей/полей
            OData — дефолты ЗУП 3.х, при необходимости правятся по факту из базы.
          </div>
          {onecBases.length === 0 && <div className="sed-note">не задано</div>}
          {onecBases.map((base, i) => (
            <div key={i} style={{ border: "1px solid #ccc", marginTop: 8, padding: 8 }}>
              <label style={{ display: "block" }}>
                Код базы 1С (base_code)
                <input
                  aria-label={`Код базы 1С ${i + 1}`}
                  type="text"
                  value={base.code}
                  onChange={(e) =>
                    setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, code: e.target.value } : b)))
                  }
                />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Название базы
                <input
                  aria-label={`Название базы 1С ${i + 1}`}
                  type="text"
                  value={base.name}
                  onChange={(e) =>
                    setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, name: e.target.value } : b)))
                  }
                />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                OData-URL публикации базы (до /odata/standard.odata/)
                <input
                  aria-label={`OData URL базы 1С ${i + 1}`}
                  type="text"
                  placeholder="http://intsrvterm0/zup/odata/standard.odata/"
                  value={base.url}
                  onChange={(e) =>
                    setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, url: e.target.value } : b)))
                  }
                />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Сервисная УЗ чтения (user, роль OData)
                <input
                  aria-label={`УЗ базы 1С ${i + 1}`}
                  type="text"
                  value={base.user}
                  onChange={(e) =>
                    setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, user: e.target.value } : b)))
                  }
                />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Пароль УЗ (оставьте пустым, чтобы сохранить текущий)
                <input
                  aria-label={`Пароль базы 1С ${i + 1}`}
                  type="password"
                  placeholder={onecBasesSet[i] ? "задан (не менять)" : "не задан"}
                  value={base.password ?? ""}
                  onChange={(e) =>
                    setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, password: e.target.value } : b)))
                  }
                />
              </label>
              <details>
                <summary>Схема OData (ЗУП 3.х; имена уточняет ИТ по факту)</summary>
                <label style={{ display: "block", marginTop: 8 }}>
                  Сущность сотрудников
                  <input
                    aria-label={`Сущность сотрудников базы 1С ${i + 1}`}
                    type="text"
                    value={base.employee_entity}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, employee_entity: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Сущность организаций (предприятий)
                  <input
                    aria-label={`Сущность организаций базы 1С ${i + 1}`}
                    type="text"
                    value={base.organization_entity}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, organization_entity: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле таб.№
                  <input
                    aria-label={`Поле таб.№ базы 1С ${i + 1}`}
                    type="text"
                    value={base.tab_num_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, tab_num_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле ФИО (может требовать $expand, напр. «Сотрудник/Description»)
                  <input
                    aria-label={`Поле ФИО базы 1С ${i + 1}`}
                    type="text"
                    value={base.fio_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, fio_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле подразделения
                  <input
                    aria-label={`Поле подразделения базы 1С ${i + 1}`}
                    type="text"
                    value={base.department_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, department_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле должности
                  <input
                    aria-label={`Поле должности базы 1С ${i + 1}`}
                    type="text"
                    value={base.position_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, position_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле даты приёма
                  <input
                    aria-label={`Поле даты приёма базы 1С ${i + 1}`}
                    type="text"
                    value={base.hire_date_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, hire_date_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле кода организации (у ЗУП кода нет — Ref_Key/ИНН)
                  <input
                    aria-label={`Поле кода организации базы 1С ${i + 1}`}
                    type="text"
                    value={base.organization_code_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, organization_code_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label style={{ display: "block", marginTop: 8 }}>
                  Поле названия организации
                  <input
                    aria-label={`Поле названия организации базы 1С ${i + 1}`}
                    type="text"
                    value={base.organization_name_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, organization_name_field: e.target.value } : b)))
                    }
                  />
                </label>
              </details>
              <div className="sed-toolbar" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => {
                    setOnecBases(onecBases.filter((_, j) => j !== i));
                    setOnecBasesSet(onecBasesSet.filter((_, j) => j !== i));
                  }}
                >
                  Удалить базу
                </button>
              </div>
            </div>
          ))}
          <div className="sed-toolbar" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="sed-btn"
              onClick={() => {
                setOnecBases([
                  ...onecBases,
                  {
                    code: "",
                    name: "",
                    url: "",
                    user: "",
                    password: "",
                    employee_entity: "Catalog_СотрудникиОрганизаций",
                    organization_entity: "Catalog_Организации",
                    tab_num_field: "ТабельныйНомер",
                    fio_field: "Сотрудник/Description",
                    department_field: "Подразделение",
                    position_field: "Должность",
                    hire_date_field: "ДатаПриема",
                    organization_code_field: "Ref_Key",
                    organization_name_field: "Description",
                  },
                ]);
                setOnecBasesSet([...onecBasesSet, false]);
              }}
            >
              Добавить базу 1С
            </button>
          </div>
          <div className="sed-toolbar" style={{ marginTop: 12 }}>
            <button type="button" className="sed-btn" onClick={handleSyncEnterprises}>
              Обновить из 1С
            </button>
          </div>
          <div className="sed-note">
            {onecSyncedAt
              ? `Последняя синхронизация предприятий: ${new Date(onecSyncedAt).toLocaleString("ru-RU")}`
              : "Синхронизация предприятий ещё не выполнялась"}
          </div>
          {syncStatus && <div role="status">{syncStatus}</div>}
          {syncError && <div role="alert">{syncError}</div>}
        </fieldset>
      )}

      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button type="button" className="sed-btn" onClick={handleSave} disabled={busy}>
          {busy ? "Сохранение…" : "Сохранить"}
        </button>
      </div>
      {saveError && <div role="alert">{saveError}</div>}
      {saved && <div role="status">{saved}</div>}
    </section>
  );
}
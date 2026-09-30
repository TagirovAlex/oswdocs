// Админка настроек для SED_ADMINS (волна B4 / B3 Волны 2).
// Значения — из settings БД (GET/PUT /api/settings), в коде не хардкодятся.
// Все персональные данные отсутствуют (только технические настройки).
// Секции: базовые поля, предприятия, группы доступа, должность→категория, шаблоны.
import { useEffect, useState } from "react";
import { getSettings, saveSettings } from "./settings-client";
import type { SettingsData, SettingsEnterprise, SettingsTemplate, SettingsTemplateStep } from "./settings-client";
import type { Role } from "./api-mock";

interface AdminSettingsProps {
  // Роль (форма — только SED_ADMINS; проверку доступа делает сервер, 403 для не-админа).
  role: Role;
}

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

// Админка: TTL отметок + лимиты скана + флаги процесса + контент (предприятия,
// группы, должность→категория, шаблоны). Всё — из settings БД.
export function AdminSettings(props: AdminSettingsProps) {
  void props; // Доступ проверяет сервер (403), на клиенте роль не нужна.
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
  // Эскалация в форме не редактируется (отдельная волна), передаём как загружено.
  const [positionEscalation, setPositionEscalation] = useState<Record<string, number> | null>(null);

  // Загрузка настроек с сервера (доступ — только админам, иначе 403).
  useEffect(() => {
    let alive = true;
    getSettings()
      .then((data) => {
        if (alive) {
          setSessionTtl(data.session_ttl_minutes);
          setTtl(data.approval_ttl_days);
          setRetentionDays(data.scan_retention_days);
          setMaxMb(data.scan_max_mb);
          setPaperRequired(data.require_paper_signature);
          setSmtpHost(data.smtp_host);
          setSmtpPort(data.smtp_port);
          setSmtpFrom(data.smtp_from);
          setSmtpUser(data.smtp_user ?? "");
          // Пароль из API не приходит (маска/null): поле пустое, только признак.
          setSmtpPassword("");
          setSmtpPasswordSet(data.smtp_password !== null);
          setRequireComment(data.require_comment);
          setEnterprises(data.enterprises ?? []);
          setAdGroups(data.allowed_ad_groups ?? []);
          setPositionCategory(pairsFromRecord(data.position_to_category));
          setPositionEscalation(data.position_escalation);
          setTemplates(data.templates ?? []);
          setError("");
        }
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
  }, []);

  // Сохранение: PUT /api/settings (объект со всеми редактируемыми ключами);
  // ошибки 403/422/503 приходят понятным текстом из клиента.
  async function handleSave(): Promise<void> {
    if (busy) return;
    setSaveError("");
    setSaved("");
    // Пустые поля (ключа нет в БД) — честно просим заполнить, а не подставляем дефолты.
    if (
      sessionTtl === null ||
      ttl === null ||
      retentionDays === null ||
      maxMb === null ||
      paperRequired === null ||
      smtpHost === null ||
      smtpPort === null ||
      smtpFrom === null
    ) {
      setSaveError("Заполните все поля настроек (значения хранятся в settings БД)");
      return;
    }
    setBusy(true);
    try {
      const data: SettingsData = {
        session_ttl_minutes: sessionTtl,
        approval_ttl_days: ttl,
        scan_retention_days: retentionDays,
        scan_max_mb: maxMb,
        require_paper_signature: paperRequired,
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        smtp_from: smtpFrom,
        smtp_user: smtpUser ?? "",
        // Пустое значение — сервер сохранит текущий пароль (не перезапишет).
        smtp_password: smtpPassword,
        require_comment: requireComment ?? false,
        enterprises,
        allowed_ad_groups: adGroups,
        position_to_category: recordFromPairs(positionCategory),
        position_escalation: positionEscalation,
        templates,
      };
      const result = await saveSettings(data);
      setSaved(
        `Сохранено: TTL=${result.approval_ttl_days} дн., сканы ${result.scan_retention_days} дн./${result.scan_max_mb} МБ, от ${result.smtp_from}, предприятий ${result.enterprises?.length ?? 0}, групп ${result.allowed_ad_groups?.length ?? 0}, шаблонов ${result.templates?.length ?? 0}`,
      );
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Ошибка сохранения настроек");
    } finally {
      setBusy(false);
    }
  }

  // Ошибка загрузки (в т.ч. 403 для не-админа) — alert вместо формы.
  if (error) return <div role="alert">Ошибка: {error}</div>;
  if (loading) return <div className="sed-note">Загрузка настроек…</div>;

  return (
    <section aria-label="Настройки СЭД">
      <h3>Настройки (только SED_ADMINS)</h3>
      <div className="sed-note">Значения хранятся в settings БД и применяются без пересборки.</div>
      <fieldset>
        <legend>Базовые настройки</legend>
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
          TTL отметок, дней (approval_ttl_days)
          <input
            aria-label="TTL отметок"
            type="number"
            value={ttl ?? ""}
            onChange={(e) => setTtl(Number(e.target.value))}
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
          <input
            type="checkbox"
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
      <EnterprisesEditor value={enterprises} onChange={setEnterprises} />
      <GroupsEditor value={adGroups} onChange={setAdGroups} />
      <PositionCategoryEditor value={positionCategory} onChange={setPositionCategory} />
      <TemplatesEditor value={templates} onChange={setTemplates} />
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
// Админка настроек: контент (руководитель ОК + админ) и инфра (только админ).
// Значения — из settings БД (GET/PUT /api/settings для админа, /settings/content
// для руководителя ОК), в коде не хардкодятся. Вкладки: Процесс / Справочники
// (контент) и Бланки / Инфра / Регламенты / Доступ и роли / Архивация
// (только админ). Вкладка «Бланки» — справочник бланков из requests-client.
import { useEffect, useState } from "react";
import {
  createBlank,
  createStepCatalog,
  deleteStepCatalog,
  getBlankSteps,
  getBlanks,
  getDocTypes,
  getStepCatalog,
  getStepGroups,
  searchAd,
  setBlankSteps,
  updateBlank,
  updateStepCatalog,
} from "./requests-client";
import type {
  AdCandidate,
  BlankRow,
  BlankStepRow,
  DocType,
  StepCatalogRow,
  StepGroup,
} from "./requests-client";
import {
  createDocType,
  deleteBackup,
  deleteDocType,
  downloadBackup,
  getArchiveSettings,
  getSettings,
  getSettingsContent,
  listBackups,
  runBackup,
  saveArchiveSettings,
  saveSettings,
  saveSettingsContent,
  syncAdGroups,
  syncEnterprises,
  updateDocType,
} from "./settings-client";
import type {
  ArchiveSettingsData,
  BackupFile,
  ContentSettingsData,
  ScheduleReglament,
  SettingsData,
  SettingsEnterprise,
  SettingsMailTemplate,
  SettingsOnecBase,
  StepGroupRef,
} from "./settings-client";
import { RichTextEditor, richTextToPlain } from "./rich-text";
import type { Role } from "./api-mock";

interface AdminSettingsProps {
  // Роль (админ — все вкладки; руководитель ОК — только контент; проверку
  // доступа делает сервер, 403 для остальных).
  role: Role;
}

// Вкладки админки: контент (Процесс/Справочники/Письма) + Инфра, Регламенты,
// Доступ и роли, Архивация, Бланки и Шаги — только админ. Легаси-вкладка
// «Шаблоны» (ключи templates/position_to_category) удалена вместе с ключами.
const CONTENT_TABS = ["Процесс", "Справочники", "Письма"] as const;
const ALL_TABS = [
  "Процесс",
  "Справочники",
  "Письма",
  "Бланки",
  "Шаги",
  "Инфра",
  "Регламенты",
  "Доступ и роли",
  "Архивация",
] as const;
type SettingsTab = (typeof ALL_TABS)[number];

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
        <div key={i} className="sed-editor-row">
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
      <div className="sed-toolbar sed-mt-12">
        <button type="button" className="sed-btn" onClick={() => onChange([...value, { code: "", name: "" }])}>
          Добавить предприятие
        </button>
      </div>
    </fieldset>
  );
}

// Нормализация справочника групп из GET: строки старого формата читаются
// как id=name (как на бэкенде _groups_with_names); пустые id отбрасываются.
export function toStepGroupRefs(raw: (string | StepGroupRef)[]): StepGroupRef[] {
  const refs: StepGroupRef[] = [];
  raw.forEach((item) => {
    if (typeof item === "string") {
      const id = item.trim();
      if (id) refs.push({ id, name: id });
    } else if (item && typeof item.id === "string" && item.id.trim()) {
      refs.push({
        id: item.id.trim(),
        name: typeof item.name === "string" && item.name.trim() ? item.name.trim() : item.id.trim(),
      });
    }
  });
  return refs;
}

// Редактор групп доступа (владельцев шагов): пары ID группы AD +
// читаемое наименование для карточки заявки; добавить/удалить.
function GroupsEditor(props: { value: StepGroupRef[]; onChange: (v: StepGroupRef[]) => void }) {
  const { value, onChange } = props;
  return (
    <fieldset>
      <legend>Группы доступа (владельцы шагов)</legend>
      <div className="sed-note">
        Наименование показывается в карточке заявки вместо кода группы.
      </div>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.length > 0 && (
        <table className="sed-table" aria-label="Группы доступа">
          <thead>
            <tr>
              <th scope="col">ID группы AD</th>
              <th scope="col">Наименование</th>
              <th scope="col">Действие</th>
            </tr>
          </thead>
          <tbody>
            {value.map((group, i) => (
              <tr key={i}>
                <td>
                  <input
                    aria-label={`ID группы ${i + 1}`}
                    placeholder="ID группы AD"
                    value={group.id}
                    onChange={(e) =>
                      onChange(value.map((g, j) => (j === i ? { ...g, id: e.target.value } : g)))
                    }
                  />
                </td>
                <td>
                  <input
                    aria-label={`Наименование группы ${i + 1}`}
                    placeholder="Читаемое наименование"
                    value={group.name}
                    onChange={(e) =>
                      onChange(value.map((g, j) => (j === i ? { ...g, name: e.target.value } : g)))
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="sed-btn sed-btn--ghost"
                    onClick={() => onChange(value.filter((_, j) => j !== i))}
                  >
                    Удалить группу
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="sed-toolbar sed-mt-12">
        <button type="button" className="sed-btn" onClick={() => onChange([...value, { id: "", name: "" }])}>
          Добавить группу
        </button>
      </div>
    </fieldset>
  );
}

// Ключи секции «Доступ и роли»: группы AD для входа и группы ролей. В settings
// и в API это списки (JSON-массив), в форме — текст через запятую; null —
// значения нет в БД, сервер берёт его из env (фолбэк).
type AccessRoleKey = "access_groups" | "admin_groups" | "hr_groups" | "hr_admin_groups";

// Значения формы секции «Доступ и роли» (пустая строка — «не задано в БД»).
interface AccessRoleGroups {
  access_groups: string;
  admin_groups: string;
  hr_groups: string;
  hr_admin_groups: string;
}

// Список групп из GET → текст формы через запятую (null — пустое поле).
function groupsToText(groups: string[] | null): string {
  return (groups ?? []).join(", ");
}

// Текст формы → список групп для PUT: разбор по запятым, trim, пустые отброшены.
// Пустой ввод → null (ключ сбрасывается в БД, действует фолбэк env на сервере).
function groupsFromText(text: string): string[] | null {
  const list = text
    .split(",")
    .map((g) => g.trim())
    .filter((g) => g !== "");
  return list.length > 0 ? list : null;
}

// Описание поля секции: ключ, подпись с ключом, подсказка (назначение роли).
const ACCESS_ROLE_FIELDS: readonly {
  key: AccessRoleKey;
  ariaLabel: string;
  label: string;
  hint: string;
}[] = [
  {
    key: "access_groups",
    ariaLabel: "Группы для входа",
    label: "Кто может войти — группы AD (access_groups)",
    hint: "Список групп через запятую; пусто — значение из env.",
  },
  {
    key: "admin_groups",
    ariaLabel: "Группы администраторов",
    label: "Роль «Админ» — группы AD (admin_groups)",
    hint: "Админ: все заявки и админка настроек; пусто — значение из env.",
  },
  {
    key: "hr_groups",
    ariaLabel: "Группы сотрудников ОК",
    label: "Роль «ОК» — группы AD (hr_groups)",
    hint: "ОК: все заявки и полная карточка; пусто — значение из env.",
  },
  {
    key: "hr_admin_groups",
    ariaLabel: "Группы руководителей ОК",
    label: "Роль «Руководитель ОК» — группы AD (hr_admin_groups)",
    hint: "Руководитель ОК: контентные настройки и конструктор шагов; пусто — значение из env.",
  },
];

// Секция «Доступ и роли» (только админ): группы AD для входа и группы ролей. В
// форме — текст через запятую, в settings — списки. Ключи инфра-настроек (правит
// только админ); пустое поле — сброс ключа в БД и фолбэк env на стороне сервера.
function AccessRolesEditor(props: { value: AccessRoleGroups; onChange: (v: AccessRoleGroups) => void }) {
  const { value, onChange } = props;
  return (
    <fieldset>
      <legend>Доступ и роли (группы AD)</legend>
      <div className="sed-note">
        Группы перечисляются через запятую (в settings хранятся списком). Пустое
        поле — значение не задано в settings, сервер берёт его из env. Ключи групп
        ролей показывают действующее значение (из БД или env), поэтому список может
        прийти отсортированным. Группы владельцев шагов (allowed_ad_groups) правит
        руководитель ОК во вкладке «Справочники».
      </div>
      {ACCESS_ROLE_FIELDS.map((field) => (
        <label key={field.key} className="sed-field">
          {field.label}
          <input
            aria-label={field.ariaLabel}
            type="text"
            value={value[field.key]}
            onChange={(e) => onChange({ ...value, [field.key]: e.target.value })}
          />
          <div className="sed-note">{field.hint}</div>
        </label>
      ))}
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
        <div key={i} className="sed-editor-card">
          <label className="sed-field">
            Код события (code)
            <input
              aria-label={`Код письма ${i + 1}`}
              value={mail.code}
              onChange={(e) => update(i, { code: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Тема письма (subject)
            <input
              aria-label={`Тема письма ${i + 1}`}
              value={mail.subject}
              onChange={(e) => update(i, { subject: e.target.value })}
            />
          </label>
          <label className="sed-field">
            HTML-тело (body_html; Jinja-плейсхолдеры)
            <textarea
              aria-label={`HTML письма ${i + 1}`}
              rows={4}
              value={mail.body_html}
              onChange={(e) => update(i, { body_html: e.target.value })}
            />
          </label>
          <div className="sed-toolbar sed-mt-8">
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
      <div className="sed-toolbar sed-mt-12">
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

// Редактор расписания регламентной операции (settings.schedule_*): режим
// (interval/daily), время/интервал и уведомление о выполнении. value=null —
// «не настроено»; дефолтов в коде нет (значения — только из settings).
function ScheduleReglamentEditor(props: {
  // Суффикс подписей формы (различает два регламента).
  title: string;
  value: ScheduleReglament | null;
  onChange: (v: ScheduleReglament | null) => void;
}) {
  const { title, value, onChange } = props;
  const mode = value?.mode ?? "";
  const update = (patch: Partial<ScheduleReglament>): void => {
    // Поля формы опциональны: правим только переданное, остальное как было.
    onChange({ ...(value ?? {}), ...patch });
  };
  return (
    <div className="sed-stack-8">
      <label className="sed-field">
        Режим
        <select
          aria-label={`Режим расписания ${title}`}
          value={mode}
          onChange={(e) => {
            const next = e.target.value;
            if (next === "interval" || next === "daily") {
              update({ mode: next });
            } else {
              onChange(null); // «не настроено»
            }
          }}
        >
          <option value="">не настроено</option>
          <option value="interval">Интервал (часы)</option>
          <option value="daily">Ежедневно в …</option>
        </select>
      </label>
      {mode === "interval" && (
        <label className="sed-field">
          Интервал, часов
          <input
            aria-label={`Интервал часов ${title}`}
            type="number"
            min={1}
            value={value?.interval_hours ?? ""}
            onChange={(e) => update({ interval_hours: Number(e.target.value) })}
          />
        </label>
      )}
      {mode === "daily" && (
        <label className="sed-field">
          Время (HH:MM)
          <input
            aria-label={`Время ${title}`}
            type="time"
            value={value?.daily_time ?? ""}
            onChange={(e) => update({ daily_time: e.target.value })}
          />
          <div className="sed-note">Время местное (МСК); пустое время = «не настроено»</div>
        </label>
      )}
      {mode !== "" && (
        <>
          <label className="sed-field sed-mt-8">
            <input
              type="checkbox"
              aria-label={`Отправлять уведомление ${title}`}
              checked={value?.notify ?? false}
              onChange={(e) => update({ notify: e.target.checked })}
            />
            Отправлять уведомление о выполненной операции
          </label>
          <label className="sed-field">
            Тема письма
            <input
              aria-label={`Тема письма ${title}`}
              type="text"
              value={value?.subject ?? ""}
              onChange={(e) => update({ subject: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Текст письма ({"{{summary}}"} — сводка операции)
            <textarea
              aria-label={`Текст письма ${title}`}
              rows={3}
              value={value?.body ?? ""}
              onChange={(e) => update({ body: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Адресаты (по одному e-mail на строку)
            <textarea
              aria-label={`Адресаты ${title}`}
              rows={3}
              value={(value?.recipients ?? []).join("\n")}
              onChange={(e) =>
                update({ recipients: e.target.value.split("\n").map((s) => s.trim()) })
              }
            />
          </label>
        </>
      )}
    </div>
  );
}

// Редактор видов документов (таблица doc_types; только админ). Список —
// code/name/is_active/sort_order; добавить (code+name), переименовать,
// деактивировать, удалить (DELETE; при ссылках в заявках бэкенд отвечает 409).
// Операции идут сразу в API, не через общий PUT /settings.
function DocTypesEditor() {
  const [items, setItems] = useState<DocType[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");
  const [newCode, setNewCode] = useState<string>("");
  const [newName, setNewName] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  function load(): void {
    setError("");
    getDocTypes(false)
      .then((data) => setItems(data))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Ошибка загрузки видов документов"))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function patch(index: number, p: Partial<DocType>): void {
    setItems(items.map((it, i) => (i === index ? { ...it, ...p } : it)));
  }

  async function handleCreate(): Promise<void> {
    const code = newCode.trim();
    const name = newName.trim();
    if (code === "" || name === "") {
      setError("Укажите код и название вида документа");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await createDocType({ code, name });
      setNewCode("");
      setNewName("");
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка добавления вида документа");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(item: DocType): Promise<void> {
    setError("");
    setBusy(true);
    try {
      await updateDocType(item.code, {
        name: item.name,
        is_active: item.is_active,
        sort_order: item.sort_order,
      });
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения вида документа");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(code: string): Promise<void> {
    setError("");
    setBusy(true);
    try {
      await deleteDocType(code);
      load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка удаления вида документа");
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset>
      <legend>Виды документов</legend>
      {loading && <div className="sed-note">Загрузка видов документов…</div>}
      {error && <div className="sed-note">Виды документов: {error}</div>}
      {!loading && !error && items.length === 0 && (
        <div className="sed-note">Видов документов нет</div>
      )}
      {items.map((item, i) => (
        <div key={item.code} className="sed-doc-row">
          <label>
            Код
            <input aria-label={`Код вида ${item.code}`} value={item.code} readOnly />
          </label>
          <label>
            Название
            <input
              aria-label={`Название вида ${item.code}`}
              value={item.name}
              onChange={(e) => patch(i, { name: e.target.value })}
            />
          </label>
          <label>
            Порядок
            <input
              aria-label={`Порядок вида ${item.code}`}
              type="number"
              value={item.sort_order}
              onChange={(e) => patch(i, { sort_order: Number(e.target.value) })}
            />
          </label>
          <label>
            <input
              type="checkbox"
              aria-label={`Вид ${item.code} активен`}
              checked={item.is_active}
              onChange={(e) => patch(i, { is_active: e.target.checked })}
            />
            Активен
          </label>
            <div className="sed-toolbar sed-mt-8">
              <button type="button" className="sed-btn" onClick={() => handleUpdate(item)} disabled={busy}>
                Применить
              </button>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => handleDelete(item.code)}
              disabled={busy}
            >
              Удалить вид
            </button>
          </div>
        </div>
      ))}
      <div className="sed-doc-row">
        <label>
          Код нового вида
          <input aria-label="Код нового вида" value={newCode} onChange={(e) => setNewCode(e.target.value)} />
        </label>
        <label>
          Название нового вида
          <input aria-label="Название нового вида" value={newName} onChange={(e) => setNewName(e.target.value)} />
        </label>
        <div className="sed-toolbar sed-mt-8">
          <button type="button" className="sed-btn" onClick={handleCreate} disabled={busy}>
            Добавить вид
          </button>
        </div>
      </div>
    </fieldset>
  );
}

// Карточка бланка в админке: код (только при создании — потом неизменен),
// название, вид документа, описание, шапка (header_html), подвал (footer_lines)
// и активность.
interface BlankForm {
  // null — новый бланк; число — правка существующего.
  id: number | null;
  code: string;
  name: string;
  doc_type_code: string;
  description: string;
  active: boolean;
  // Шапка бланка — HTML визуального редактора (печать санирует его бэкенд).
  header_html: string;
  // Подвал бланка — строки печати после таблицы шагов.
  footer_lines: string[];
}

// Пустая карточка нового бланка; наборы должностей/имена — только из справочников.
const EMPTY_BLANK_FORM: BlankForm = {
  id: null,
  code: "",
  name: "",
  doc_type_code: "",
  description: "",
  active: true,
  header_html: "",
  footer_lines: [],
};

// Плейсхолдеры подстановки в шапке/подвале бланка (контракт печати): список
// задан решением человека, подстановка выполняется бэкендом при печати.
const BLANK_PLACEHOLDERS: readonly string[] = [
  "{fio}",
  "{position}",
  "{department}",
  "{tab_num}",
  "{enterprise}",
  "{blank_name}",
  "{subject}",
  "{content}",
  "{date}",
  "{dismissal_date}",
  "{steps}",
  "{manager}",
];

// Кнопки вставки плейсхолдера (набор один и тот же для шапки и подвала).
function PlaceholderButtons(props: {
  onInsert: (token: string) => void;
  target: string;
  // Цели нет (например, строк подвала ещё не заведено) — кнопки выключены.
  disabled?: boolean;
}) {
  const { onInsert, target, disabled } = props;
  return (
    <div className="sed-toolbar sed-mt-8">
      <span className="sed-sub">Плейсхолдеры:</span>
      {BLANK_PLACEHOLDERS.map((token) => (
        <button
          key={token}
          type="button"
          className="sed-btn sed-btn--ghost"
          aria-label={`Вставить ${token} в ${target}`}
          disabled={disabled === true}
          onClick={() => onInsert(token)}
        >
          {token}
        </button>
      ))}
    </div>
  );
}

// Вкладка «Бланки» (только админ): справочник бланков — карточка (код, название,
// вид документа, описание, шапка, подвал, активность) и состав СВОИХ шагов
// (название, текст, вид исполнителя, согласующие/группа AD, режим, необязательный,
// обязательный комментарий). Этап из справочника у шага нет. Шаг можно скопировать
// из справочника шагов («Взять из справочника») — он становится независимой
// заготовкой в составе. Файлов-шаблонов .docx нет: печать из данных бланка.
function BlanksEditor() {
  const [blanks, setBlanks] = useState<BlankRow[]>([]);
  // Группы-владельцы шагов из settings (GET /api/step-groups) — селект группы AD
  // для шага с executor_kind=ad_group.
  const [groups, setGroups] = useState<StepGroup[]>([]);
  // Виды документов (doc_types) — значение doc_type_code бланка.
  const [docTypes, setDocTypes] = useState<DocType[]>([]);
  // Справочник шагов (GET /api/settings/routing/step-catalog) — источник
  // «Взять из справочника» в составе бланка (копируются активные шаги).
  const [stepCatalog, setStepCatalog] = useState<StepCatalogRow[]>([]);
  // Список шагов справочника для раскрытия при «Взять из справочника».
  const [catalogOpen, setCatalogOpen] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [form, setForm] = useState<BlankForm>(EMPTY_BLANK_FORM);
  // id выбранного бланка (null — состав не открыт).
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [steps, setSteps] = useState<BlankStepRow[]>([]);
  // Запрос подсказки согласующих (GET /api/ad/search) и найденные кандидаты:
  // логины хранятся как есть, ФИО показываем, если AD его отдал.
  const [adQuery, setAdQuery] = useState<string>("");
  const [adHits, setAdHits] = useState<AdCandidate[]>([]);
  const [adError, setAdError] = useState<string>("");
  // Плейсхолдер шапки: текст для вставки и счётчик нажатий (вставка в редактор).
  const [headerInsert, setHeaderInsert] = useState<{ text: string; seq: number } | null>(null);
  const [insertSeq, setInsertSeq] = useState<number>(0);
  // Индекс строки подвала, в которую вставляется плейсхолдер.
  const [footerTarget, setFooterTarget] = useState<number>(0);

  // Список бланков после любой правки (step_count/version из ответа сервера).
  function loadBlanks(): void {
    getBlanks()
      .then((items) => {
        setBlanks(items);
        setLoadError("");
      })
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "Ошибка загрузки справочника бланков"),
      );
  }

  useEffect(() => {
    loadBlanks();
    // Группы шагов, виды документов и справочник шагов — для выбора группы AD,
    // вида документа и «Взять из справочника». Справочники общие; недоступность
    // не ломает список бланков.
    getStepGroups()
      .then((items) => setGroups(items))
      .catch(() => setGroups([]));
    getDocTypes(false)
      .then((items) => setDocTypes(items))
      .catch(() => setDocTypes([]));
    getStepCatalog()
      .then((items) => setStepCatalog(items))
      .catch(() => setStepCatalog([]));
  }, []);

  // Состав выбранного бланка — по его id (состав пустым не бывает: шаги нет — []).
  function loadSteps(blankId: number): void {
    getBlankSteps(blankId)
      .then((items) => {
        setSteps(items);
        setAdQuery("");
        setAdHits([]);
        setAdError("");
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Ошибка загрузки состава бланка"));
  }

  function selectBlank(blankId: number): void {
    setSelectedId(blankId);
    setError("");
    setSaved("");
    loadSteps(blankId);
  }

  // Создание/правка карточки бланка. Код неизменен после создания — при правке
  // он показывается read-only, поэтому в PUT не уходит. Шапка и подвал уходят
  // вместе с карточкой (снимок в заявку берёт бэкенд при выдаче).
  async function handleSaveForm(): Promise<void> {
    const name = form.name.trim();
    if (name === "") {
      setError("Укажите название бланка");
      return;
    }
    if (form.id === null && form.code.trim() === "") {
      setError("Укажите код бланка");
      return;
    }
    setError("");
    setSaved("");
    setBusy(true);
    const payload = {
      name,
      doc_type_code: form.doc_type_code === "" ? null : form.doc_type_code,
      description: form.description === "" ? null : form.description,
      active: form.active,
      header_html: form.header_html === "" ? null : form.header_html,
      footer_lines: form.footer_lines,
    };
    try {
      if (form.id === null) {
        await createBlank({ code: form.code.trim(), ...payload });
        setSaved("Бланк создан");
        setForm(EMPTY_BLANK_FORM);
      } else {
        await updateBlank(form.id, payload);
        setSaved("Бланк сохранён");
      }
      loadBlanks();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения бланка");
    } finally {
      setBusy(false);
    }
  }

  // Вставка плейсхолдера в шапку: текст уходит в визуальный редактор, который
  // вставит его в позицию курсора и вернёт обновлённый HTML. Счётчик seq
  // отделяет одинаковые нажатия друг от друга, поэтому состояние правится
  // последовательно, без побочных эффектов внутри апдейтера.
  function insertHeaderToken(token: string): void {
    setHeaderInsert({ text: token, seq: insertSeq + 1 });
    setInsertSeq(insertSeq + 1);
  }

  // Вставка плейсхолдера в выбранную строку подвала (обычный текст).
  function insertFooterToken(token: string): void {
    // Строк нет — вставлять некуда: кнопки выключены (disabled) на стороне UI.
    setForm((prev) => {
      const lines = [...prev.footer_lines];
      const index = Math.min(Math.max(footerTarget, 0), Math.max(lines.length - 1, 0));
      if (lines.length === 0) return prev;
      lines[index] = `${lines[index]}${token}`;
      return { ...prev, footer_lines: lines };
    });
  }

  // Новый пустой шаг бланка в конец состава (заготовка под заполнение).
  function addStep(): void {
    setSteps((prev) => [
      ...prev,
      {
        blank_id: selectedId ?? 0,
        step_order: prev.length + 1,
        title: "",
        stage_lines: [],
        executor_kind: "people",
        assignees: [],
        owner_group: null,
        approval_mode: "sequential",
        optional: false,
        require_comment: false,
      },
    ]);
  }

  // Копирование активного шага справочника в конец состава бланка: заготовка
  // (title, текст, исполнитель, режим, флаги) — шаг остаётся независимой копией,
  // правится в составе как обычный шаг бланка.
  function copyFromCatalog(catalogId: number): void {
    const source = stepCatalog.find((c) => c.id === catalogId);
    if (!source) return;
    setSteps((prev) => [
      ...prev,
      {
        blank_id: selectedId ?? 0,
        step_order: prev.length + 1,
        title: source.title,
        stage_lines: [...source.stage_lines],
        executor_kind: source.executor_kind,
        assignees: [...source.assignees],
        owner_group: source.owner_group,
        approval_mode: source.approval_mode ?? "sequential",
        optional: source.optional,
        require_comment: source.require_comment,
      },
    ]);
    setCatalogOpen(false);
  }

  // Порядок шага: кнопки «вверх/вниз» (доступнее перетаскивания). step_order
  // пересчитывается при сохранении состава, поэтому здесь достаточно сдвига.
  function moveStep(index: number, delta: number): void {
    const target = index + delta;
    if (target < 0 || target >= steps.length) return;
    setSteps((prev) => {
      const next = [...prev];
      const [row] = next.splice(index, 1);
      next.splice(target, 0, row);
      return next;
    });
  }

  function patchStep(index: number, patch: Partial<BlankStepRow>): void {
    setSteps((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  }

  // Подсказки согласующих по ФИО (GET /api/ad/search, только чтение AD).
  function searchAssignees(): void {
    const q = adQuery.trim();
    if (q === "") {
      setAdHits([]);
      setAdError("");
      return;
    }
    searchAd(q)
      .then((items) => {
        setAdHits(items);
        setAdError("");
      })
      .catch((e: unknown) => setAdError(e instanceof Error ? e.message : "Ошибка поиска сотрудников в AD"));
  }

  // Добавление согласующего к шагу по логину (логины хранятся как есть).
  function addAssignee(index: number, sam: string): void {
    const login = sam.trim();
    if (login === "") return;
    setSteps((prev) =>
      prev.map((s, i) =>
        i === index && !s.assignees.includes(login)
          ? { ...s, assignees: [...s.assignees, login] }
          : s,
      ),
    );
  }

  function removeAssignee(index: number, sam: string): void {
    setSteps((prev) =>
      prev.map((s, i) => (i === index ? { ...s, assignees: s.assignees.filter((a) => a !== sam) } : s)),
    );
  }

  // Название шага пустое или для вида исполнителя не хватает данных — сервер
  // ответит 422. Предупреждаем заранее и не отправляем некорректный состав.
  function stepsProblem(): string {
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      const no = i + 1;
      if (step.title.trim() === "") {
        return `Шаг ${no}: укажите название шага`;
      }
      if (step.executor_kind === "people" && step.assignees.length === 0) {
        return `Шаг ${no}: добавьте хотя бы одного согласующего`;
      }
      if (step.executor_kind === "ad_group" && (step.owner_group ?? "") === "") {
        return `Шаг ${no}: выберите группу AD`;
      }
    }
    return "";
  }

  // Сохранение состава: полная замена одной транзакцией (порядок — с 1).
  async function handleSaveSteps(): Promise<void> {
    if (selectedId === null) return;
    const problem = stepsProblem();
    if (problem !== "") {
      setError(problem);
      return;
    }
    setError("");
    setSaved("");
    setBusy(true);
    try {
      await setBlankSteps(
        selectedId,
        steps.map((s, i) => ({
          step_order: i + 1,
          title: s.title.trim(),
          // Пустые пункты текста в PUT не уходят (бэкенд их и не хранит).
          stage_lines: s.stage_lines.filter((line) => richTextToPlain(line) !== ""),
          executor_kind: s.executor_kind,
          assignees: s.assignees,
          owner_group: s.executor_kind === "ad_group" ? s.owner_group : null,
          approval_mode: s.approval_mode ?? null,
          optional: s.optional,
          require_comment: s.require_comment,
        })),
      );
      setSaved("Состав бланка сохранён");
      loadSteps(selectedId);
      loadBlanks();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения состава бланка");
    } finally {
      setBusy(false);
    }
  }

  const selectedBlank = blanks.find((b) => b.id === selectedId) ?? null;

  return (
    <fieldset>
      <legend>Бланки (справочник заявок)</legend>
      <div className="sed-note">
        Бланк — набор СВОИХ шагов (название, текст, исполнитель). Его выбирает
        сотрудник ОК при создании заявки: система формирует шаги и ответственных, а
        снимок бланка (шапка, подвал, состав) пишется в заявку. Бланк без шагов
        выбрать нельзя. Файлов-шаблонов .docx нет — печатный документ собирается из
        данных.
      </div>
      {loadError && <div role="alert">Бланки: {loadError}</div>}
      {!loadError && blanks.length === 0 && <div className="sed-note">Бланков нет</div>}
      {blanks.length > 0 && (
        <table className="sed-table" aria-label="Справочник бланков">
          <thead>
            <tr>
              <th scope="col">Код</th>
              <th scope="col">Название</th>
              <th scope="col">Вид документа</th>
              <th scope="col">Шагов</th>
              <th scope="col">Версия</th>
              <th scope="col">Активен</th>
              <th scope="col">Действия</th>
            </tr>
          </thead>
          <tbody>
            {blanks.map((blank) => (
              <tr key={blank.id} className={blank.id === selectedId ? "sed-table__row--active" : undefined}>
                <td>{blank.code}</td>
                <td>
                  {blank.name}
                  {blank.description && <div className="sed-sub">{blank.description}</div>}
                </td>
                <td>{blank.doc_type_code ?? "—"}</td>
                <td>{blank.step_count ?? 0}</td>
                <td>{blank.version}</td>
                <td>{blank.active ? "да" : "нет"}</td>
                <td>
                  <div className="sed-toolbar sed-mt-0">
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Состав бланка ${blank.code}`}
                      onClick={() => selectBlank(blank.id)}
                    >
                      Состав
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Править бланк ${blank.code}`}
                      onClick={() => {
                        setForm({
                          id: blank.id,
                          code: blank.code,
                          name: blank.name,
                          doc_type_code: blank.doc_type_code ?? "",
                          description: blank.description ?? "",
                          active: blank.active,
                          header_html: blank.header_html ?? "",
                          footer_lines: [...(blank.footer_lines ?? [])],
                        });
                        setFooterTarget(0);
                      }}
                    >
                      Править
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Карточка бланка: создание или правка выбранного. */}
      <div className="sed-editor-card">
        <div className="sed-blockcard__head">
          <span className="sed-blockcard__title">{form.id === null ? "Новый бланк" : `Правка бланка: ${form.code}`}</span>
          <span className="sed-blockcard__spacer" />
          {form.id !== null && (
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setForm(EMPTY_BLANK_FORM)}
            >
              Отменить правку
            </button>
          )}
        </div>
        <div className="sed-editor-row">
          <label className="sed-field">
            Код
            <input
              aria-label="Код бланка"
              value={form.code}
              readOnly={form.id !== null}
              title={form.id !== null ? "Код бланка после создания не меняется" : undefined}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Название
            <input
              aria-label="Название бланка"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
        </div>
        <div className="sed-editor-row">
          <label className="sed-field">
            Вид документа
            <select
              aria-label="Вид документа бланка"
              value={form.doc_type_code}
              onChange={(e) => setForm({ ...form, doc_type_code: e.target.value })}
            >
              <option value="">— не задан —</option>
              {docTypes.map((dt) => (
                <option key={dt.code} value={dt.code}>
                  {dt.name}
                </option>
              ))}
            </select>
          </label>
          <label className="sed-field">
            <input
              type="checkbox"
              aria-label="Бланк активен"
              checked={form.active}
              onChange={(e) => setForm({ ...form, active: e.target.checked })}
            />
            Активен
          </label>
        </div>
        <label className="sed-field">
          Описание (пояснение сотруднику ОК в селекте бланка)
          <textarea
            aria-label="Описание бланка"
            rows={2}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </label>
        {/* Шапка бланка (header_html): визуальный редактор + кнопки вставки
            плейсхолдеров. Санирует HTML бэкенд при печати. */}
        <div className="sed-mt-8">
          <RichTextEditor
            label="Шапка бланка"
            value={form.header_html}
            hint="Разметка попадёт в печатный документ; подставьте плейсхолдеры кнопками ниже."
            insert={headerInsert}
            onChange={(html) => setForm({ ...form, header_html: html })}
          />
          <PlaceholderButtons target="шапку" onInsert={insertHeaderToken} />
        </div>

        {/* Подвал бланка (footer_lines): список строк печати после таблицы шагов. */}
        <fieldset className="sed-mt-8">
          <legend>Подвал бланка</legend>
          {form.footer_lines.length === 0 && <div className="sed-note">строк нет</div>}
          {form.footer_lines.map((line, i) => (
            <div key={i} className="sed-editor-row sed-editor-row--center">
              <input
                aria-label={`Строка подвала ${i + 1}`}
                value={line}
                onFocus={() => setFooterTarget(i)}
                onClick={() => setFooterTarget(i)}
                onChange={(e) =>
                  setForm({
                    ...form,
                    footer_lines: form.footer_lines.map((l, k) => (k === i ? e.target.value : l)),
                  })
                }
              />
              <button
                type="button"
                className="sed-btn sed-btn--ghost"
                aria-label={`Выбрать строку подвала ${i + 1}`}
                onClick={() => setFooterTarget(i)}
              >
                Выбрать строку
              </button>
              <button
                type="button"
                className="sed-btn sed-btn--ghost"
                aria-label={`Удалить строку подвала ${i + 1}`}
                onClick={() =>
                  setForm({
                    ...form,
                    footer_lines: form.footer_lines.filter((_, k) => k !== i),
                  })
                }
              >
                Удалить строку
              </button>
            </div>
          ))}
          <div className="sed-toolbar sed-mt-8">
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => {
                setForm({ ...form, footer_lines: [...form.footer_lines, ""] });
                setFooterTarget(form.footer_lines.length);
              }}
            >
              Добавить строку подвала
            </button>
          </div>
          <div className="sed-note">
            {form.footer_lines.length === 0
              ? "Добавьте строку, затем вставьте в неё плейсхолдер."
              : `Плейсхолдер вставится в строку ${footerTarget + 1}.`}
          </div>
          <PlaceholderButtons
            target="подвал"
            onInsert={insertFooterToken}
            disabled={form.footer_lines.length === 0}
          />
        </fieldset>

        <div className="sed-toolbar sed-mt-8">
          <button type="button" className="sed-btn" onClick={handleSaveForm} disabled={busy}>
            {form.id === null ? "Создать бланк" : "Сохранить бланк"}
          </button>
        </div>
      </div>

      {/* Состав СВОИХ шагов выбранного бланка. */}
      {selectedBlank && (
        <div className="sed-editor-card">
          <div className="sed-blockcard__head">
            <span className="sed-blockcard__title">Состав бланка: {selectedBlank.name}</span>
            <span className="sed-blockcard__spacer" />
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setSelectedId(null)}
            >
              Закрыть состав
            </button>
          </div>
          {steps.length === 0 && <div className="sed-note">Шагов нет</div>}
          {steps.map((step, i) => (
            <div key={i} className="sed-editor-card" aria-label={`Шаг ${i + 1}`}>
              <div className="sed-blockcard__head">
                <span className="sed-blockcard__title">Шаг {i + 1}</span>
                <span className="sed-blockcard__spacer" />
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  aria-label={`Поднять шаг ${i + 1}`}
                  disabled={i === 0}
                  onClick={() => moveStep(i, -1)}
                >
                  Вверх
                </button>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  aria-label={`Опустить шаг ${i + 1}`}
                  disabled={i === steps.length - 1}
                  onClick={() => moveStep(i, 1)}
                >
                  Вниз
                </button>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  aria-label={`Удалить шаг ${i + 1}`}
                  onClick={() => setSteps((prev) => prev.filter((_, j) => j !== i))}
                >
                  Удалить
                </button>
              </div>
              <div className="sed-editor-row">
                <label className="sed-field">
                  Название шага
                  <input
                    aria-label={`Название шага ${i + 1}`}
                    value={step.title}
                    onChange={(e) => patchStep(i, { title: e.target.value })}
                  />
                </label>
                <label className="sed-field">
                  Вид исполнителя
                  <select
                    aria-label={`Вид исполнителя шага ${i + 1}`}
                    value={step.executor_kind}
                    onChange={(e) =>
                      patchStep(i, { executor_kind: e.target.value as BlankStepRow["executor_kind"] })
                    }
                  >
                    <option value="people">согласующие</option>
                    <option value="ad_group">группа AD</option>
                    <option value="manager_ad">руководитель сотрудника</option>
                  </select>
                </label>
                <label className="sed-field">
                  Режим
                  <select
                    aria-label={`Режим шага ${i + 1}`}
                    value={step.approval_mode ?? "sequential"}
                    onChange={(e) =>
                      patchStep(i, {
                        approval_mode: e.target.value as "sequential" | "parallel",
                      })
                    }
                  >
                    <option value="sequential">все ответственные</option>
                    <option value="parallel">любой ответственный</option>
                  </select>
                </label>
              </div>

              {/* Исполнитель по списку согласующих: логины AD, ФИО подсказывает
                  поиск AD (getEmployeeCard/поиск сотрудников — /api/ad/search). */}
              {step.executor_kind === "people" && (
                <div className="sed-mt-8">
                  <div className="sed-sub">Согласующие (логины AD):</div>
                  {step.assignees.length === 0 && (
                    <div className="sed-note">
                      Добавьте хотя бы одного согласующего — без согласующих шаг не сохранится.
                    </div>
                  )}
                  {step.assignees.map((sam) => {
                    const hit = adHits.find((c) => c.sam === sam);
                    return (
                      <div key={sam} className="sed-editor-row sed-editor-row--center">
                        <span>
                          {sam}
                          {hit && hit.display_name ? ` — ${hit.display_name}` : ""}
                        </span>
                        <button
                          type="button"
                          className="sed-btn sed-btn--ghost"
                          aria-label={`Удалить согласующего ${sam} из шага ${i + 1}`}
                          onClick={() => removeAssignee(i, sam)}
                        >
                          Удалить согласующего
                        </button>
                      </div>
                    );
                  })}
                  <div className="sed-editor-row sed-editor-row--center sed-mt-8">
                    <input
                      aria-label={`Логин согласующего шага ${i + 1}`}
                      value={adQuery}
                      onChange={(e) => setAdQuery(e.target.value)}
                    />
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      onClick={searchAssignees}
                    >
                      Найти сотрудников
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Добавить согласующего в шаг ${i + 1}`}
                      onClick={() => addAssignee(i, adQuery)}
                    >
                      Добавить по логину
                    </button>
                  </div>
                  {adError && <div role="alert">{adError}</div>}
                  {adHits.length > 0 && (
                    <ul className="sed-list">
                      {adHits.map((cand) => (
                        <li key={cand.sam}>
                          <button
                            type="button"
                            className="sed-btn sed-btn--ghost"
                            aria-label={`Добавить согласующего ${cand.sam} в шаг ${i + 1}`}
                            onClick={() => addAssignee(i, cand.sam)}
                          >
                            {cand.display_name || cand.sam} ({cand.sam})
                            {cand.title ? ` — ${cand.title}` : ""}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {/* Исполнитель — группа AD из справочника групп шагов. */}
              {step.executor_kind === "ad_group" && (
                <label className="sed-field sed-mt-8">
                  Группа AD
                  <select
                    aria-label={`Группа AD шага ${i + 1}`}
                    value={step.owner_group ?? ""}
                    onChange={(e) =>
                      patchStep(i, { owner_group: e.target.value === "" ? null : e.target.value })
                    }
                  >
                    <option value="">— выберите группу —</option>
                    {groups.map((group) => (
                      <option key={group.id} value={group.id}>
                        {group.name}
                      </option>
                    ))}
                  </select>
                  {(step.owner_group ?? "") === "" && (
                    <div className="sed-note">Выберите группу AD — без неё шаг не сохранится.</div>
                  )}
                </label>
              )}

              {/* Исполнитель — руководитель сотрудника в AD: данных в бланке нет,
                  бэкенд находит его при выдаче заявки. */}
              {step.executor_kind === "manager_ad" && (
                <div className="sed-note">
                  Исполнитель — руководитель сотрудника из AD (находит сервер при выдаче
                  заявки; если руководителя нет, заявка не создастся).
                </div>
              )}

              <div className="sed-editor-row">
                <label className="sed-field">
                  <input
                    type="checkbox"
                    aria-label={`Шаг ${i + 1} необязательный`}
                    checked={step.optional}
                    onChange={(e) => patchStep(i, { optional: e.target.checked })}
                  />
                  Необязательный
                </label>
                <label className="sed-field">
                  <input
                    type="checkbox"
                    aria-label={`Комментарий шага ${i + 1} обязателен`}
                    checked={step.require_comment}
                    onChange={(e) => patchStep(i, { require_comment: e.target.checked })}
                  />
                  Комментарий обязателен
                </label>
              </div>

              {/* Текст шага и его пунктов — визуальный редактор (HTML уходит в
                  stage_lines шага бланка; разметка попадёт в печать). */}
              <div className="sed-blanktext">
                {step.stage_lines.map((line, li) => (
                  <div key={li} className="sed-blanktext__line">
                    <RichTextEditor
                      label={`Пункт шага ${i + 1}.${li + 1}`}
                      value={line}
                      onChange={(html) =>
                        patchStep(i, {
                          stage_lines: step.stage_lines.map((l, k) => (k === li ? html : l)),
                        })
                      }
                    />
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Удалить пункт шага ${i + 1}.${li + 1}`}
                      onClick={() =>
                        patchStep(i, { stage_lines: step.stage_lines.filter((_, k) => k !== li) })
                      }
                    >
                      Удалить пункт
                    </button>
                  </div>
                ))}
                <div className="sed-toolbar sed-mt-8">
                  <button
                    type="button"
                    className="sed-btn sed-btn--ghost"
                    aria-label={`Добавить пункт шага ${i + 1}`}
                    onClick={() => patchStep(i, { stage_lines: [...step.stage_lines, "<p></p>"] })}
                  >
                    Добавить пункт
                  </button>
                </div>
              </div>
            </div>
          ))}

          <div className="sed-toolbar sed-mt-8">
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              aria-label="Добавить шаг"
              onClick={addStep}
            >
              Добавить шаг
            </button>
            {/* «Взять из справочника»: раскрывает список активных шагов шаблона;
                выбор копирует шаг заготовкой в конец состава (независимая копия). */}
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              aria-label="Взять из справочника"
              onClick={() => setCatalogOpen((open) => !open)}
            >
              Взять из справочника
            </button>
            <button type="button" className="sed-btn" onClick={handleSaveSteps} disabled={busy}>
              Сохранить состав
            </button>
          </div>
          {catalogOpen && (
            <div className="sed-mt-8">
              <div className="sed-sub">Шаги справочника (копия в состав):</div>
              {stepCatalog.filter((c) => c.active).length === 0 && (
                <div className="sed-note">Активных шагов в справочнике нет</div>
              )}
              <ul className="sed-list">
                {stepCatalog
                  .filter((c) => c.active)
                  .map((c) => (
                    <li key={c.id}>
                      <button
                        type="button"
                        className="sed-btn sed-btn--ghost"
                        aria-label={`Взять из справочника ${c.title}`}
                        onClick={() => copyFromCatalog(c.id)}
                      >
                        {c.title} ({c.code})
                      </button>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {error && <div role="alert">{error}</div>}
      {saved && <div role="status">{saved}</div>}
    </fieldset>
  );
}

// Карточка шага справочника (вкладка «Шаги»): код (неизменен после создания),
// название, вид исполнителя, согласующие/группа AD, режим, флаги, текст.
interface StepCatalogForm {
  // null — новый шаг; число — правка существующего.
  id: number | null;
  code: string;
  title: string;
  stage_lines: string[];
  executor_kind: BlankStepRow["executor_kind"];
  assignees: string[];
  owner_group: string;
  approval_mode: "sequential" | "parallel";
  optional: boolean;
  require_comment: boolean;
  active: boolean;
}

// Пустая карточка нового шага справочника; значения — только из справочников.
const EMPTY_CATALOG_FORM: StepCatalogForm = {
  id: null,
  code: "",
  title: "",
  stage_lines: [],
  executor_kind: "people",
  assignees: [],
  owner_group: "",
  approval_mode: "sequential",
  optional: false,
  require_comment: false,
  active: true,
};

// Вкладка «Шаги» (только админ): справочник шагов (step_catalog) — заготовки для
// состава бланков («Взять из справочника»). Карточка как у шага бланка; код
// неизменен после создания. Согласующие и группы AD — только из справочников.
function StepsCatalogEditor() {
  const [steps, setSteps] = useState<StepCatalogRow[]>([]);
  const [groups, setGroups] = useState<StepGroup[]>([]);
  const [loadError, setLoadError] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [form, setForm] = useState<StepCatalogForm>(EMPTY_CATALOG_FORM);
  const [adQuery, setAdQuery] = useState<string>("");
  const [adHits, setAdHits] = useState<AdCandidate[]>([]);
  const [adError, setAdError] = useState<string>("");

  // Список шагов после любой правки (активность/код/версии из ответа сервера).
  function loadCatalog(): void {
    getStepCatalog()
      .then((items) => {
        setSteps(items);
        setLoadError("");
      })
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "Ошибка загрузки справочника шагов"),
      );
  }

  useEffect(() => {
    loadCatalog();
    // Группы-владельцы шагов — селект группы AD (общий справочник).
    getStepGroups()
      .then((items) => setGroups(items))
      .catch(() => setGroups([]));
  }, []);

  // Создание/правка карточки шага. Код неизменен после создания — при правке он
  // показывается read-only, поэтому в PUT не уходит (частичное обновление).
  async function handleSaveForm(): Promise<void> {
    const title = form.title.trim();
    if (title === "") {
      setError("Укажите название шага");
      return;
    }
    if (form.id === null && form.code.trim() === "") {
      setError("Укажите код шага");
      return;
    }
    if (form.executor_kind === "people" && form.assignees.length === 0) {
      setError("Добавьте хотя бы одного согласующего");
      return;
    }
    if (form.executor_kind === "ad_group" && form.owner_group.trim() === "") {
      setError("Выберите группу AD");
      return;
    }
    setError("");
    setSaved("");
    setBusy(true);
    const payload = {
      title,
      // Пустые пункты текста в запрос не уходят (бэкенд их и не хранит).
      stage_lines: form.stage_lines.filter((line) => richTextToPlain(line) !== ""),
      executor_kind: form.executor_kind,
      assignees: form.assignees,
      owner_group: form.executor_kind === "ad_group" ? form.owner_group : null,
      approval_mode: form.approval_mode,
      optional: form.optional,
      require_comment: form.require_comment,
      active: form.active,
    };
    try {
      if (form.id === null) {
        await createStepCatalog({ code: form.code.trim(), ...payload });
        setSaved("Шаг справочника создан");
        setForm(EMPTY_CATALOG_FORM);
      } else {
        await updateStepCatalog(form.id, payload);
        setSaved("Шаг справочника сохранён");
      }
      loadCatalog();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения шага справочника");
    } finally {
      setBusy(false);
    }
  }

  // Удаление шага справочника с подтверждением.
  async function handleDelete(step: StepCatalogRow): Promise<void> {
    if (!window.confirm(`Удалить шаг «${step.title}» из справочника?`)) return;
    setError("");
    setSaved("");
    setBusy(true);
    try {
      await deleteStepCatalog(step.id);
      setSaved("Шаг справочника удалён");
      loadCatalog();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка удаления шага справочника");
    } finally {
      setBusy(false);
    }
  }

  // Подсказки согласующих по ФИО (GET /api/ad/search, только чтение AD).
  function searchAssignees(): void {
    const q = adQuery.trim();
    if (q === "") {
      setAdHits([]);
      setAdError("");
      return;
    }
    searchAd(q)
      .then((items) => {
        setAdHits(items);
        setAdError("");
      })
      .catch((e: unknown) => setAdError(e instanceof Error ? e.message : "Ошибка поиска сотрудников в AD"));
  }

  function addAssignee(sam: string): void {
    const login = sam.trim();
    if (login === "") return;
    if (form.assignees.includes(login)) return;
    setForm({ ...form, assignees: [...form.assignees, login] });
  }

  function removeAssignee(sam: string): void {
    setForm({ ...form, assignees: form.assignees.filter((a) => a !== sam) });
  }

  function patchLine(index: number, html: string): void {
    setForm({
      ...form,
      stage_lines: form.stage_lines.map((l, k) => (k === index ? html : l)),
    });
  }

  const editing = form.id !== null;

  return (
    <fieldset>
      <legend>Шаги (справочник для бланков)</legend>
      <div className="sed-note">
        Шаг справочника — заготовка для состава бланка: во вкладке «Бланки» шаг
        копируется кнопкой «Взять из справочника» и правится уже в составе. Сам
        справочник бланков не меняет.
      </div>
      {loadError && <div role="alert">Шаги: {loadError}</div>}
      {!loadError && steps.length === 0 && <div className="sed-note">Шагов нет</div>}
      {steps.length > 0 && (
        <table className="sed-table" aria-label="Справочник шагов">
          <thead>
            <tr>
              <th scope="col">Код</th>
              <th scope="col">Название</th>
              <th scope="col">Вид исполнителя</th>
              <th scope="col">Режим</th>
              <th scope="col">Активен</th>
              <th scope="col">Действия</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((step) => (
              <tr key={step.id} className={step.id === form.id ? "sed-table__row--active" : undefined}>
                <td>{step.code}</td>
                <td>{step.title}</td>
                <td>
                  {step.executor_kind === "people"
                    ? "согласующие"
                    : step.executor_kind === "ad_group"
                      ? "группа AD"
                      : "руководитель сотрудника"}
                </td>
                <td>
                  {step.approval_mode === "parallel" ? "любой ответственный" : "все ответственные"}
                </td>
                <td>{step.active ? "да" : "нет"}</td>
                <td>
                  <div className="sed-toolbar sed-mt-0">
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Править шаг ${step.code}`}
                      onClick={() =>
                        setForm({
                          id: step.id,
                          code: step.code,
                          title: step.title,
                          stage_lines: [...step.stage_lines],
                          executor_kind: step.executor_kind,
                          assignees: [...step.assignees],
                          owner_group: step.owner_group ?? "",
                          approval_mode: step.approval_mode ?? "sequential",
                          optional: step.optional,
                          require_comment: step.require_comment,
                          active: step.active,
                        })
                      }
                    >
                      Править
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Удалить шаг ${step.code}`}
                      onClick={() => void handleDelete(step)}
                      disabled={busy}
                    >
                      Удалить
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Карточка шага: создание или правка выбранного. */}
      <div className="sed-editor-card">
        <div className="sed-blockcard__head">
          <span className="sed-blockcard__title">
            {form.id === null ? "Новый шаг справочника" : `Правка шага: ${form.code}`}
          </span>
          <span className="sed-blockcard__spacer" />
          {editing && (
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setForm(EMPTY_CATALOG_FORM)}
            >
              Отменить правку
            </button>
          )}
        </div>
        <div className="sed-editor-row">
          <label className="sed-field">
            Код
            <input
              aria-label="Код шага справочника"
              value={form.code}
              readOnly={editing}
              title={editing ? "Код шага после создания не меняется" : undefined}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Название
            <input
              aria-label="Название шага справочника"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
          </label>
        </div>
        <div className="sed-editor-row">
          <label className="sed-field">
            Вид исполнителя
            <select
              aria-label="Вид исполнителя шага справочника"
              value={form.executor_kind}
              onChange={(e) =>
                setForm({ ...form, executor_kind: e.target.value as StepCatalogForm["executor_kind"] })
              }
            >
              <option value="people">согласующие</option>
              <option value="ad_group">группа AD</option>
              <option value="manager_ad">руководитель сотрудника</option>
            </select>
          </label>
          <label className="sed-field">
            Режим
            <select
              aria-label="Режим шага справочника"
              value={form.approval_mode}
              onChange={(e) =>
                setForm({ ...form, approval_mode: e.target.value as "sequential" | "parallel" })
              }
            >
              <option value="sequential">все ответственные</option>
              <option value="parallel">любой ответственный</option>
            </select>
          </label>
          <label className="sed-field">
            <input
              type="checkbox"
              aria-label="Шаг справочника активен"
              checked={form.active}
              onChange={(e) => setForm({ ...form, active: e.target.checked })}
            />
            Активен
          </label>
        </div>

        {/* Исполнитель по списку согласующих: логины AD, ФИО подсказывает поиск. */}
        {form.executor_kind === "people" && (
          <div className="sed-mt-8">
            <div className="sed-sub">Согласующие (логины AD):</div>
            {form.assignees.length === 0 && (
              <div className="sed-note">
                Добавьте хотя бы одного согласующего — без согласующих шаг не сохранится.
              </div>
            )}
            {form.assignees.map((sam) => {
              const hit = adHits.find((c) => c.sam === sam);
              return (
                <div key={sam} className="sed-editor-row sed-editor-row--center">
                  <span>
                    {sam}
                    {hit && hit.display_name ? ` — ${hit.display_name}` : ""}
                  </span>
                  <button
                    type="button"
                    className="sed-btn sed-btn--ghost"
                    aria-label={`Удалить согласующего ${sam} из шага справочника`}
                    onClick={() => removeAssignee(sam)}
                  >
                    Удалить согласующего
                  </button>
                </div>
              );
            })}
            <div className="sed-editor-row sed-editor-row--center sed-mt-8">
              <input
                aria-label="Логин согласующего шага справочника"
                value={adQuery}
                onChange={(e) => setAdQuery(e.target.value)}
              />
              <button type="button" className="sed-btn sed-btn--ghost" onClick={searchAssignees}>
                Найти сотрудников
              </button>
              <button
                type="button"
                className="sed-btn sed-btn--ghost"
                aria-label="Добавить согласующего в шаг справочника"
                onClick={() => addAssignee(adQuery)}
              >
                Добавить по логину
              </button>
            </div>
            {adError && <div role="alert">{adError}</div>}
            {adHits.length > 0 && (
              <ul className="sed-list">
                {adHits.map((cand) => (
                  <li key={cand.sam}>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Добавить согласующего ${cand.sam} в шаг справочника`}
                      onClick={() => addAssignee(cand.sam)}
                    >
                      {cand.display_name || cand.sam} ({cand.sam})
                      {cand.title ? ` — ${cand.title}` : ""}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Исполнитель — группа AD из справочника групп шагов. */}
        {form.executor_kind === "ad_group" && (
          <label className="sed-field sed-mt-8">
            Группа AD
            <select
              aria-label="Группа AD шага справочника"
              value={form.owner_group}
              onChange={(e) => setForm({ ...form, owner_group: e.target.value })}
            >
              <option value="">— выберите группу —</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
            {form.owner_group === "" && (
              <div className="sed-note">Выберите группу AD — без неё шаг не сохранится.</div>
            )}
          </label>
        )}

        {/* Исполнитель — руководитель сотрудника в AD: данных в шаге нет, бэкенд
            находит его при выдаче заявки. */}
        {form.executor_kind === "manager_ad" && (
          <div className="sed-note">
            Исполнитель — руководитель сотрудника из AD (находит сервер при выдаче
            заявки; если руководителя нет, заявка не создастся).
          </div>
        )}

        <div className="sed-editor-row">
          <label className="sed-field">
            <input
              type="checkbox"
              aria-label="Шаг справочника необязательный"
              checked={form.optional}
              onChange={(e) => setForm({ ...form, optional: e.target.checked })}
            />
            Необязательный
          </label>
          <label className="sed-field">
            <input
              type="checkbox"
              aria-label="Комментарий шага справочника обязателен"
              checked={form.require_comment}
              onChange={(e) => setForm({ ...form, require_comment: e.target.checked })}
            />
            Комментарий обязателен
          </label>
        </div>

        {/* Текст шага и его пунктов — визуальный редактор (HTML уходит в
            stage_lines; разметка попадёт в печать бланка). */}
        <div className="sed-blanktext">
          {form.stage_lines.map((line, li) => (
            <div key={li} className="sed-blanktext__line">
              <RichTextEditor
                label={`Пункт шага справочника ${li + 1}`}
                value={line}
                onChange={(html) => patchLine(li, html)}
              />
              <button
                type="button"
                className="sed-btn sed-btn--ghost"
                aria-label={`Удалить пункт шага справочника ${li + 1}`}
                onClick={() =>
                  setForm({ ...form, stage_lines: form.stage_lines.filter((_, k) => k !== li) })
                }
              >
                Удалить пункт
              </button>
            </div>
          ))}
          <div className="sed-toolbar sed-mt-8">
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              aria-label="Добавить пункт шага справочника"
              onClick={() => setForm({ ...form, stage_lines: [...form.stage_lines, "<p></p>"] })}
            >
              Добавить пункт
            </button>
          </div>
        </div>

        <div className="sed-toolbar sed-mt-8">
          <button type="button" className="sed-btn" onClick={handleSaveForm} disabled={busy}>
            {form.id === null ? "Создать шаг" : "Сохранить шаг"}
          </button>
        </div>
      </div>
      {error && <div role="alert">{error}</div>}
      {saved && <div role="status">{saved}</div>}
    </fieldset>
  );
}

// Формат размера бэкапа: в мегабайтах «X.X МБ» (1 знак после запятой);
// меньше 1 КБ — в килобайтах («X.X КБ»).
function formatBackupSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${(sizeBytes / 1024).toFixed(1)} КБ`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} МБ`;
}

// Формат даты и времени бэкапа: «ДД.ММ.ГГГГ ЧЧ:ММ» по ru-RU (часы:минуты,
// чтобы совпадало с {ts} в имени файла). Дата без времени — только дата.
function formatBackupDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Вкладка «Архивация» (только админ): настройки бэкапов (место хранения,
// шаблон имени, количество копий, расписание — как у регламентов), ручной
// запуск бэкапа и список сохранённых файлов. Значения — из GET/PUT /api/archive
// (settings БД), файлы — GET /api/archive/files; хардкода нет.
function ArchiveTab() {
  const [settings, setSettings] = useState<ArchiveSettingsData | null>(null);
  const [backups, setBackups] = useState<BackupFile[]>([]);
  const [loadError, setLoadError] = useState<string>("");
  const [saveError, setSaveError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [backupStatus, setBackupStatus] = useState<string>("");
  const [backupError, setBackupError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [backupBusy, setBackupBusy] = useState<boolean>(false);

  // Загрузка настроек архивации и списка бэкапов (без перезагрузки вкладки).
  function load(): void {
    setLoadError("");
    getArchiveSettings()
      .then((data) => setSettings(data))
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "Ошибка загрузки настроек архивации"),
      );
    listBackups()
      .then((items) => setBackups(items))
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "Ошибка загрузки списка бэкапов"),
      );
  }

  useEffect(load, []);

  // Сохранение настроек архивации (PUT /api/archive).
  async function handleSave(): Promise<void> {
    if (!settings || busy) return;
    setSaveError("");
    setSaved("");
    setBusy(true);
    try {
      await saveArchiveSettings(settings);
      setSaved("Настройки архивации сохранены");
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Ошибка сохранения настроек архивации");
    } finally {
      setBusy(false);
    }
  }

  // Ручной запуск бэкапа (POST /api/archive/backup); после — обновляем список.
  async function handleBackup(): Promise<void> {
    if (backupBusy) return;
    setBackupError("");
    setBackupStatus("");
    setBackupBusy(true);
    try {
      const result = await runBackup();
      if (result.ok) {
        setBackupStatus("Бэкап создан");
        setBackups(await listBackups());
      } else {
        setBackupError(result.error ?? "Бэкап не создан");
      }
    } catch (e: unknown) {
      setBackupError(e instanceof Error ? e.message : "Ошибка создания бэкапа");
    } finally {
      setBackupBusy(false);
    }
  }

  // Удаление файла бэкапа (DELETE /api/archive/files/{name}) с подтверждением;
  // после успеха список перечитываем.
  async function handleDeleteBackup(name: string): Promise<void> {
    if (!window.confirm(`Удалить бэкап ${name}? Действие необратимо.`)) return;
    setLoadError("");
    try {
      await deleteBackup(name);
      setBackups(await listBackups());
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : "Ошибка удаления бэкапа");
    }
  }

  return (
    <fieldset>
      <legend>Архивация (бэкапы)</legend>
      {loadError && <div className="sed-note">Архивация: {loadError}</div>}
      {!settings && !loadError && <div className="sed-note">Загрузка настроек архивации…</div>}
      {settings && (
        <>
          <label className="sed-field">
            Место хранения
            <input
              aria-label="Место хранения бэкапов"
              type="text"
              value={settings.storage_path}
              onChange={(e) => setSettings({ ...settings, storage_path: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Шаблон имени файла
            <input
              aria-label="Шаблон имени бэкапа"
              type="text"
              value={settings.file_pattern}
              onChange={(e) => setSettings({ ...settings, file_pattern: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Количество хранимых копий
            <input
              aria-label="Количество копий бэкапов"
              type="number"
              min={1}
              value={settings.keep_copies}
              onChange={(e) =>
                setSettings({ ...settings, keep_copies: Number(e.target.value) })
              }
            />
          </label>
          <ScheduleReglamentEditor
            title="бэкапов"
            value={settings.schedule}
            onChange={(schedule) => setSettings({ ...settings, schedule })}
          />
          <div className="sed-toolbar sed-mt-12">
            <button type="button" className="sed-btn" onClick={handleSave} disabled={busy}>
              {busy ? "Сохранение…" : "Сохранить"}
            </button>
            <button
              type="button"
              className="sed-btn"
              onClick={handleBackup}
              disabled={backupBusy}
            >
              {backupBusy ? "Создание…" : "Сделать бэкап сейчас"}
            </button>
          </div>
          {saveError && <div role="alert">{saveError}</div>}
          {saved && <div role="status">{saved}</div>}
          {backupStatus && <div role="status">{backupStatus}</div>}
          {backupError && <div role="alert">{backupError}</div>}
        </>
      )}
      {/* Список сохранённых бэкапов: имя/размер/дата. */}
      <div className="sed-note sed-mt-12">
        Сохранённые бэкапы:
      </div>
      {backups.length === 0 && !loadError && <div className="sed-note">Бэкапов нет</div>}
      {backups.length > 0 && (
        <table className="sed-table" aria-label="Сохранённые бэкапы">
          <thead>
            <tr>
              <th>Имя</th>
              <th>Размер</th>
              <th>Дата и время</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {backups.map((file) => (
              <tr key={file.name}>
                <td>{file.name}</td>
                <td>{formatBackupSize(file.size)}</td>
                <td>{formatBackupDate(file.created_at)}</td>
                <td>
                  <div className="sed-toolbar sed-mt-0">
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Скачать бэкап ${file.name}`}
                      onClick={() => void downloadBackup(file.name).catch(() => undefined)}
                    >
                      Скачать
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Удалить бэкап ${file.name}`}
                      onClick={() => void handleDeleteBackup(file.name)}
                    >
                      Удалить
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </fieldset>
  );
}

// Админка: контент (TTL/флаги, справочники, шаблоны) + инфра (сессия/сканы/SMTP,
// регламенты, доступ и роли) + архивация. Админ видит все вкладки (GET/PUT
// /api/settings), руководитель ОК — только контент (GET/PUT /api/settings/content).
// Всё — из settings БД.
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
  const [adGroups, setAdGroups] = useState<StepGroupRef[]>([]);
  const [mailTemplates, setMailTemplates] = useState<SettingsMailTemplate[]>([]);
  // Базы 1С: поля формы + параллельный признак «пароль задан» для placeholder.
  const [onecBases, setOnecBases] = useState<SettingsOnecBase[]>([]);
  const [onecBasesSet, setOnecBasesSet] = useState<boolean[]>([]);
  // Дата/время последней синхронизации предприятий (read-only).
  const [onecSyncedAt, setOnecSyncedAt] = useState<string | null>(null);
  // Расписания регламентов (null — «не настроено»).
  const [scheduleEnterprises, setScheduleEnterprises] = useState<ScheduleReglament | null>(null);
  const [scheduleAdLinks, setScheduleAdLinks] = useState<ScheduleReglament | null>(null);
  // Статус принудительной синхронизации предприятий из баз 1С.
  const [syncStatus, setSyncStatus] = useState<string>("");
  const [syncError, setSyncError] = useState<string>("");
  const [groupSyncStatus, setGroupSyncStatus] = useState<string>("");
  const [groupSyncError, setGroupSyncError] = useState<string>("");
  // Эскалация в форме не редактируется (отдельная волна), передаём как загружено.
  const [positionEscalation, setPositionEscalation] = useState<Record<string, number> | null>(null);
  // Группы входа и роли (access_groups/admin_groups/hr_groups/hr_admin_groups) —
  // в settings списки, в форме текст через запятую; пусто = «не задано» (env).
  const [accessRoles, setAccessRoles] = useState<AccessRoleGroups>({
    access_groups: "",
    admin_groups: "",
    hr_groups: "",
    hr_admin_groups: "",
  });

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
          // Пустое расписание — null («не настроено»), дефолты не подставляем.
          setScheduleEnterprises(full.schedule_enterprises_sync ?? null);
          setScheduleAdLinks(full.schedule_ad_links_sync ?? null);
          // Группы входа и ролей: список из GET → текст формы (null → пусто).
          setAccessRoles({
            access_groups: groupsToText(full.access_groups),
            admin_groups: groupsToText(full.admin_groups),
            hr_groups: groupsToText(full.hr_groups),
            hr_admin_groups: groupsToText(full.hr_admin_groups),
          });
        }
        setTtl(data.approval_ttl_days);
        setPaperRequired(data.require_paper_signature);
        setRequireComment(data.require_comment);
        setEnterprises(data.enterprises ?? []);
        setAdGroups(toStepGroupRefs(data.allowed_ad_groups ?? []));
        setPositionEscalation(data.position_escalation);
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
      position_escalation: positionEscalation,
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
          // Расписания регламентов: null — «не настроено» (хранится в settings).
          schedule_enterprises_sync: scheduleEnterprises,
          schedule_ad_links_sync: scheduleAdLinks,
          // Группы входа и ролей: текст → список; пустое поле → null (сброс ключа).
          access_groups: groupsFromText(accessRoles.access_groups),
          admin_groups: groupsFromText(accessRoles.admin_groups),
          hr_groups: groupsFromText(accessRoles.hr_groups),
          hr_admin_groups: groupsFromText(accessRoles.hr_admin_groups),
        };
        const result = await saveSettings(full);
        setSaved(
          `Сохранено: TTL=${result.approval_ttl_days} дн., сканы ${result.scan_retention_days} дн./${result.scan_max_mb} МБ, от ${result.smtp_from}, предприятий ${result.enterprises?.length ?? 0}, групп ${result.allowed_ad_groups?.length ?? 0}`,
        );
      } else {
        const result = await saveSettingsContent(content);
        setSaved(
          `Сохранено: TTL=${result.approval_ttl_days} дн., предприятий ${result.enterprises?.length ?? 0}, групп ${result.allowed_ad_groups?.length ?? 0}`,
        );
      }
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Ошибка сохранения настроек");
    } finally {
      setBusy(false);
    }
  }

  // Принудительная синхронизация состава групп AD в локальный кэш
  // (POST /api/ad/groups/sync, только админ): после неё карточка показывает
  // состав без чтения каталога.
  async function handleSyncAdGroups(): Promise<void> {
    setGroupSyncError("");
    setGroupSyncStatus("");
    try {
      const result = await syncAdGroups();
      setGroupSyncStatus(
        `Состав обновлён: групп ${result.synced_groups}, участников ${result.members}, ` +
        `должностей в справочнике ${result.titles}` +
        (result.errors.length > 0 ? `, ошибок ${result.errors.length}` : ""),
      );
    } catch (e: unknown) {
      setGroupSyncError(e instanceof Error ? e.message : "Ошибка синхронизации состава групп");
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
          <label className="sed-field sed-mt-8">
            TTL отметок, дней (approval_ttl_days)
            <input
              aria-label="TTL отметок"
              type="number"
              value={ttl ?? ""}
              onChange={(e) => setTtl(Number(e.target.value))}
            />
          </label>
          <label className="sed-field sed-mt-8">
            <input
              type="checkbox"
              aria-label="Требовать бумажное заявление"
              checked={paperRequired ?? false}
              onChange={(e) => setPaperRequired(e.target.checked)}
            />
            Требовать бумажное заявление (require_paper_signature)
          </label>
          <label className="sed-field sed-mt-8">
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

      {/* Порядок: часто меняемое — сверху; предприятия — последними
          (меняются и используются реже всего). */}
      {activeTab === "Справочники" && (
        <>
          <GroupsEditor value={adGroups} onChange={setAdGroups} />
          {/* Ручной синк состава групп AD в кэш — только админ (эндпоинт 403 остальным). */}
          {isAdmin && (
            <div className="sed-toolbar sed-mt-8">
              <button type="button" className="sed-btn" onClick={handleSyncAdGroups}>
                Обновить состав групп из AD
              </button>
              {groupSyncStatus && <span role="status">{groupSyncStatus}</span>}
              {groupSyncError && <span role="alert">{groupSyncError}</span>}
            </div>
          )}
          {/* Виды документов — таблица doc_types; только админ (не контент-ключ settings). */}
          {isAdmin && <DocTypesEditor />}
          <EnterprisesEditor value={enterprises} onChange={setEnterprises} />
        </>
      )}

      {activeTab === "Письма" && (
        <MailTemplatesEditor value={mailTemplates} onChange={setMailTemplates} />
      )}

      {/* Справочник бланков — только админ (вкладки инфраструктуры, как сейчас
          у остальных настроек): бланки создаются и правятся своими запросами, а
          не общим PUT /settings, поэтому своей кнопки «Сохранить» здесь нет. */}
      {activeTab === "Бланки" && isAdmin && <BlanksEditor />}

      {/* Справочник шагов для бланков — только админ (как «Бланки»). */}
      {activeTab === "Шаги" && isAdmin && <StepsCatalogEditor />}

      {activeTab === "Инфра" && isAdmin && (
        <fieldset>
          <legend>Инфра (сессия, сканы, SMTP)</legend>
          <label className="sed-field sed-mt-8">
            Длительность сессии, минут (session_ttl_minutes; 600 = 10 часов)
            <input
              aria-label="Длительность сессии"
              type="number"
              value={sessionTtl ?? ""}
              onChange={(e) => setSessionTtl(Number(e.target.value))}
            />
          </label>
          <label className="sed-field sed-mt-8">
            Хранение сканов, дней (scan_retention_days)
            <input
              aria-label="Хранение сканов"
              type="number"
              value={retentionDays ?? ""}
              onChange={(e) => setRetentionDays(Number(e.target.value))}
            />
          </label>
          <label className="sed-field sed-mt-8">
            Лимит скана, МБ (scan_max_mb)
            <input
              aria-label="Лимит скана"
              type="number"
              value={maxMb ?? ""}
              onChange={(e) => setMaxMb(Number(e.target.value))}
            />
          </label>
          <label className="sed-field sed-mt-8">
            Хост SMTP-релея (smtp_host)
            <input
              aria-label="Хост SMTP-релея"
              type="text"
              value={smtpHost ?? ""}
              onChange={(e) => setSmtpHost(e.target.value)}
            />
          </label>
          <label className="sed-field sed-mt-8">
            Порт SMTP-релея (smtp_port)
            <input
              aria-label="Порт SMTP-релея"
              type="number"
              value={smtpPort ?? ""}
              onChange={(e) => setSmtpPort(Number(e.target.value))}
            />
          </label>
          <label className="sed-field sed-mt-8">
            Отправитель уведомлений, e-mail (smtp_from)
            <input
              aria-label="Отправитель уведомлений"
              type="email"
              value={smtpFrom ?? ""}
              onChange={(e) => setSmtpFrom(e.target.value)}
            />
          </label>
          <label className="sed-field sed-mt-8">
            Логин SMTP-релея (smtp_user; пусто — отправка без авторизации)
            <input
              aria-label="Логин SMTP-релея"
              type="text"
              value={smtpUser ?? ""}
              onChange={(e) => setSmtpUser(e.target.value)}
            />
          </label>
          <label className="sed-field sed-mt-8">
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
            <div key={i} className="sed-editor-card">
              <label className="sed-field">
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
              <label className="sed-field sed-mt-8">
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
              <label className="sed-field sed-mt-8">
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
              <label className="sed-field sed-mt-8">
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
              <label className="sed-field sed-mt-8">
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
                <label className="sed-field sed-mt-8">
                  Сущность сотрудников (справочник)
                  <input
                    aria-label={`Сущность сотрудников базы 1С ${i + 1}`}
                    type="text"
                    value={base.employee_entity}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, employee_entity: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Поле предприятия (код = Ref_Key организации)
                  <input
                    aria-label={`Поле предприятия базы 1С ${i + 1}`}
                    type="text"
                    value={base.employee_org_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, employee_org_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
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
                <label className="sed-field sed-mt-8">
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
                <label className="sed-field sed-mt-8">
                  Поле ФИО
                  <input
                    aria-label={`Поле ФИО базы 1С ${i + 1}`}
                    type="text"
                    value={base.fio_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, fio_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Поле подразделения (регистр кадровых данных, $expand)
                  <input
                    aria-label={`Поле подразделения базы 1С ${i + 1}`}
                    type="text"
                    value={base.department_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, department_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Поле должности (регистр кадровых данных, $expand)
                  <input
                    aria-label={`Поле должности базы 1С ${i + 1}`}
                    type="text"
                    value={base.position_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, position_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Поле даты приёма (регистр кадровых данных)
                  <input
                    aria-label={`Поле даты приёма базы 1С ${i + 1}`}
                    type="text"
                    value={base.hire_date_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, hire_date_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Поле даты увольнения (регистр кадровых данных)
                  <input
                    aria-label={`Поле даты увольнения базы 1С ${i + 1}`}
                    type="text"
                    value={base.termination_date_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, termination_date_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Регистр кадровых данных (второй запрос карточки)
                  <input
                    aria-label={`Регистр кадровых данных базы 1С ${i + 1}`}
                    type="text"
                    value={base.hr_entity}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, hr_entity: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
                  Поле сотрудника в регистре (Ref_Key)
                  <input
                    aria-label={`Поле сотрудника в регистре базы 1С ${i + 1}`}
                    type="text"
                    value={base.hr_employee_field}
                    onChange={(e) =>
                      setOnecBases(onecBases.map((b, j) => (j === i ? { ...b, hr_employee_field: e.target.value } : b)))
                    }
                  />
                </label>
                <label className="sed-field sed-mt-8">
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
                <label className="sed-field sed-mt-8">
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
              <div className="sed-toolbar sed-mt-8">
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
          <div className="sed-toolbar sed-mt-12">
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
                    employee_entity: "Catalog_Сотрудники",
                    organization_entity: "Catalog_Организации",
                    employee_org_field: "ГоловнаяОрганизация_Key",
                    tab_num_field: "Code",
                    fio_field: "Description",
                    department_field: "ТекущееПодразделение/Description",
                    position_field: "ТекущаяДолжность/Description",
                    hire_date_field: "ДатаПриема",
                    termination_date_field: "ДатаУвольнения",
                    hr_entity: "InformationRegister_ТекущиеКадровыеДанныеСотрудников",
                    hr_employee_field: "Сотрудник_Key",
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
          <div className="sed-toolbar sed-mt-12">
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

      {activeTab === "Регламенты" && isAdmin && (
        <>
          <fieldset>
            <legend>Синхронизация предприятий из 1С</legend>
            <div className="sed-note">
              Регламентная синхронизация справочника предприятий из баз 1С:
              повтор по интервалу или ежедневно (без настройки — раз в 7 дней).
            </div>
            <ScheduleReglamentEditor
              title="предприятий"
              value={scheduleEnterprises}
              onChange={setScheduleEnterprises}
            />
          </fieldset>
          <fieldset>
            <legend>Автосвязка 1С↔AD</legend>
            <div className="sed-note">
              Регламентная автосвязка сотрудников 1С с AD по точному ФИО
              (без настройки — раз в 7 дней).
            </div>
            <ScheduleReglamentEditor
              title="связок"
              value={scheduleAdLinks}
              onChange={setScheduleAdLinks}
            />
          </fieldset>
        </>
      )}

      {activeTab === "Доступ и роли" && isAdmin && (
        <AccessRolesEditor value={accessRoles} onChange={setAccessRoles} />
      )}

      {activeTab === "Архивация" && isAdmin && <ArchiveTab />}

      {/* Общий «Сохранить» — не для вкладок с собственным сохранением:
          «Архивация» (PUT /api/archive), «Бланки» и «Шаги» (свои CRUD-запросы). */}
      {activeTab !== "Архивация" && activeTab !== "Бланки" && activeTab !== "Шаги" && (
        <div className="sed-toolbar sed-mt-12">
          <button type="button" className="sed-btn" onClick={handleSave} disabled={busy}>
            {busy ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
      )}
      {activeTab !== "Архивация" && saveError && <div role="alert">{saveError}</div>}
      {activeTab !== "Архивация" && saved && <div role="status">{saved}</div>}
    </section>
  );
}

// Админка настроек: контент (руководитель ОК + админ) и инфра (только админ).
// Значения — из settings БД (GET/PUT /api/settings для админа, /settings/content
// для руководителя ОК), в коде не хардкодятся. Вкладки: Процесс / Справочники /
// Шаблоны (контент) и Инфра / Регламенты / Доступ и роли (только админ).
import { useEffect, useRef, useState } from "react";
import { base64ToBlob, getDocTypes } from "./requests-client";
import type { DocType } from "./requests-client";
import {
  createDocType,
  deleteBackup,
  deleteDocTemplateFile,
  deleteDocType,
  downloadBackup,
  downloadDocTemplateFile,
  getArchiveSettings,
  getSettings,
  getSettingsContent,
  listBackups,
  previewDocTemplateFile,
  runBackup,
  saveArchiveSettings,
  saveSettings,
  saveSettingsContent,
  syncAdGroups,
  syncEnterprises,
  updateDocType,
  uploadDocTemplateFile,
} from "./settings-client";
import type {
  ArchiveSettingsData,
  BackupFile,
  ContentSettingsData,
  ScheduleReglament,
  SettingsData,
  SettingsDocTemplate,
  SettingsEnterprise,
  SettingsMailTemplate,
  SettingsOnecBase,
  SettingsTemplate,
  SettingsTemplateStep,
  StepGroupRef,
} from "./settings-client";
import type { Role } from "./api-mock";

interface AdminSettingsProps {
  // Роль (админ — все вкладки; руководитель ОК — только контент; проверку
  // доступа делает сервер, 403 для остальных).
  role: Role;
}

// Вкладки админки: контент (Процесс/Справочники/Шаблоны) + Инфра, Регламенты,
// Доступ и роли и Архивация (бэкапы) — только админ.
const CONTENT_TABS = ["Процесс", "Справочники", "Шаблоны"] as const;
const ALL_TABS = [
  "Процесс",
  "Справочники",
  "Шаблоны",
  "Инфра",
  "Регламенты",
  "Доступ и роли",
  "Архивация",
] as const;
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
        <div key={i} className="sed-editor-row">
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
      <div className="sed-toolbar sed-mt-12">
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
        <div key={i} className="sed-editor-card">
          <label className="sed-field">
            Служба
            <input
              aria-label={`Служба шаблона ${i + 1}`}
              value={template.service}
              onChange={(e) => updateTemplate(i, { service: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Категория
            <input
              aria-label={`Категория шаблона ${i + 1}`}
              value={template.category}
              onChange={(e) => updateTemplate(i, { category: e.target.value })}
            />
          </label>
          <div className="sed-mt-8">
            {template.steps.length === 0 && <div className="sed-note">шагов нет</div>}
            {template.steps.map((step, j) => (
              <div key={j} className="sed-editor-row sed-editor-row--center">
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
          <div className="sed-toolbar sed-mt-8">
            <button type="button" className="sed-btn" onClick={() => addStep(i)}>
              Добавить шаг
            </button>
          </div>
          <div className="sed-toolbar sed-mt-8">
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
      <div className="sed-toolbar sed-mt-12">
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

// Редактор бланков бегунков (doc_templates): служба + категория + либо тело DOCX
// (Jinja, текстовый фолбэк, когда file не задан), либо .docx-файл (загрузка/
// скачивание/замена/удаление + предпросмотр рендера на тестовых данных).
// Файл — в FILES_DIR/templates/, в settings хранится только имя (file).
function DocTemplatesEditor(props: { value: SettingsDocTemplate[]; onChange: (v: SettingsDocTemplate[]) => void }) {
  const { value, onChange } = props;
  // Скрытые input-ы загрузки .docx (по индексу карточки): общие для «Загрузить
  // .docx» и «Заменить» (в один момент в карточке видна одна из кнопок).
  const fileInputs = useRef<(HTMLInputElement | null)[]>([]);
  const [fileError, setFileError] = useState<string>("");

  function update(index: number, patch: Partial<SettingsDocTemplate>): void {
    onChange(value.map((doc, i) => (i === index ? { ...doc, ...patch } : doc)));
  }

  // Загрузка .docx: новая (previous нет) либо замена (previous=текущий file).
  // При успехе — имя файла в карточку; текстовый body остаётся фолбэком.
  async function handleUpload(index: number, file: File | null): Promise<void> {
    if (!file) return;
    setFileError("");
    try {
      const previous = value[index]?.file ?? undefined;
      const result = await uploadDocTemplateFile(file, previous);
      update(index, { file: result.name });
    } catch (e: unknown) {
      setFileError(e instanceof Error ? e.message : "Ошибка загрузки файла бланка");
    } finally {
      // Сброс значения input, чтобы повторный выбор того же файла сработал.
      const input = fileInputs.current[index];
      if (input) input.value = "";
    }
  }

  // Скачивание файла бланка (GET .../download).
  async function handleDownload(name: string): Promise<void> {
    setFileError("");
    try {
      await downloadDocTemplateFile(name);
    } catch (e: unknown) {
      setFileError(e instanceof Error ? e.message : "Ошибка скачивания файла бланка");
    }
  }

  // Удаление файла бланка (DELETE) с подтверждением; при успехе file=null —
  // бланк снова редактируется текстом (фолбэк).
  async function handleDelete(index: number, name: string): Promise<void> {
    if (!window.confirm(`Удалить файл бланка ${name}? Действие необратимо.`)) return;
    setFileError("");
    try {
      await deleteDocTemplateFile(name);
      update(index, { file: null });
    } catch (e: unknown) {
      setFileError(e instanceof Error ? e.message : "Ошибка удаления файла бланка");
    }
  }

  // Предпросмотр рендера файла на тестовых данных: PDF открываем в новом окне
  // (base64 → blob), generated=false с reason — текст в редакторе.
  async function handlePreview(name: string): Promise<void> {
    setFileError("");
    try {
      const result = await previewDocTemplateFile(name);
      if (result.generated && result.pdf_b64) {
        const pdfUrl = URL.createObjectURL(base64ToBlob(result.pdf_b64, "application/pdf"));
        window.open(pdfUrl, "_blank");
        setTimeout(() => URL.revokeObjectURL(pdfUrl), 60_000);
      } else {
        setFileError(result.reason ?? "Бланк не сгенерирован");
      }
    } catch (e: unknown) {
      setFileError(e instanceof Error ? e.message : "Ошибка предпросмотра бланка");
    }
  }

  return (
    <fieldset>
      <legend>Бланки бегунков (doc_templates)</legend>
      {value.length === 0 && <div className="sed-note">не задано</div>}
      {value.map((doc, i) => {
        // Имя файла бланка (null/отсутствует — текстовый body-фолбэк).
        const fileName = doc.file ?? null;
        return (
          <div key={i} className="sed-editor-card">
            <label className="sed-field">
              Служба
              <input
                aria-label={`Служба бланка ${i + 1}`}
                value={doc.service}
                onChange={(e) => update(i, { service: e.target.value })}
              />
            </label>
            <label className="sed-field">
              Категория
              <input
                aria-label={`Категория бланка ${i + 1}`}
                value={doc.category}
                onChange={(e) => update(i, { category: e.target.value })}
              />
            </label>
            {fileName ? (
              <div className="sed-mt-8">
                <div className="sed-note sed-mt-0">
                  Файл бланка: <strong>{fileName}</strong>
                </div>
                <div className="sed-toolbar sed-mt-8">
                  <button
                    type="button"
                    className="sed-btn sed-btn--neutral"
                    aria-label={`Скачать файл бланка ${i + 1}`}
                    onClick={() => void handleDownload(fileName)}
                  >
                    Скачать
                  </button>
                  <label className="sed-btn sed-btn--neutral">
                    Заменить
                    <input
                      type="file"
                      accept=".docx"
                      aria-label={`Заменить файл бланка ${i + 1}`}
                      style={{ display: "none" }}
                      ref={(el) => {
                        fileInputs.current[i] = el;
                      }}
                      onChange={(e) => void handleUpload(i, e.target.files?.[0] ?? null)}
                    />
                  </label>
                  <button
                    type="button"
                    className="sed-btn sed-btn--danger"
                    aria-label={`Удалить файл бланка ${i + 1}`}
                    onClick={() => void handleDelete(i, fileName)}
                  >
                    Удалить
                  </button>
                  <button
                    type="button"
                    className="sed-btn sed-btn--neutral"
                    onClick={() => void handlePreview(fileName)}
                  >
                    Предпросмотр
                  </button>
                </div>
              </div>
            ) : (
              <>
                <label className="sed-field">
                  Тело бегунка (Jinja-плейсхолдеры)
                  <textarea
                    aria-label={`Тело бланка ${i + 1}`}
                    rows={4}
                    value={doc.body}
                    onChange={(e) => update(i, { body: e.target.value })}
                  />
                </label>
                <div className="sed-toolbar sed-mt-8">
                  <label className="sed-btn">
                    Загрузить .docx
                    <input
                      type="file"
                      accept=".docx"
                      aria-label={`Загрузить файл бланка ${i + 1}`}
                      style={{ display: "none" }}
                      ref={(el) => {
                        fileInputs.current[i] = el;
                      }}
                      onChange={(e) => void handleUpload(i, e.target.files?.[0] ?? null)}
                    />
                  </label>
                </div>
              </>
            )}
            <div className="sed-toolbar sed-mt-8">
              <button
                type="button"
                className="sed-btn sed-btn--ghost"
                onClick={() => onChange(value.filter((_, j) => j !== i))}
              >
                Удалить бланк
              </button>
            </div>
          </div>
        );
      })}
      {fileError && (
        <div role="alert" className="sed-mt-8">
          {fileError}
        </div>
      )}
      <div className="sed-toolbar sed-mt-12">
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
  const [positionCategory, setPositionCategory] = useState<PositionCategoryPair[]>([]);
  const [templates, setTemplates] = useState<SettingsTemplate[]>([]);
  const [docTemplates, setDocTemplates] = useState<SettingsDocTemplate[]>([]);
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

  // Принудительная синхронизация состава групп AD в локальный кэш
  // (POST /api/ad/groups/sync, только админ): после неё карточка показывает
  // состав без чтения каталога.
  async function handleSyncAdGroups(): Promise<void> {
    setGroupSyncError("");
    setGroupSyncStatus("");
    try {
      const result = await syncAdGroups();
      setGroupSyncStatus(
        `Состав обновлён: групп ${result.synced_groups}, участников ${result.members}` +
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

      {activeTab === "Справочники" && (
        <>
          <EnterprisesEditor value={enterprises} onChange={setEnterprises} />
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
          <PositionCategoryEditor value={positionCategory} onChange={setPositionCategory} />
          {/* Виды документов — таблица doc_types; только админ (не контент-ключ settings). */}
          {isAdmin && <DocTypesEditor />}
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

      {/* Общий «Сохранить» — не для вкладки «Архивация»: у неё свой эндпоинт
          и своя кнопка сохранения (PUT /api/archive). */}
      {activeTab !== "Архивация" && (
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

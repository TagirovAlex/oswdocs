// Сетка скелета: вкладки, дерево папок со счётчиками, тулбар, фильтры, таблица.
// Данные — из реального API (requests-client), тема — из theme.tsx.
// Папки/фильтры — клиентские над загруженными строками (волна 1).
import { useEffect, useMemo, useState } from "react";
import type { ChangeEvent } from "react";
import {
  EMPTY_FILTERS,
  decideStep,
  filterRequests,
  finishRequest,
  getAttachments,
  getDocuments,
  getEnterprises,
  getFolders,
  getRequest,
  getRequests,
  printRequest,
  submitRequest,
  toExecution,
  toRequestRow,
  uploadAttachment,
} from "./requests-client";
import type {
  AttachmentMeta,
  DocumentMeta,
  Enterprise,
  Folder,
  FolderId,
  RequestFilters,
  RequestOut,
  RequestRow,
  StepDecision,
} from "./requests-client";
import type { Role } from "./api-mock";
import { useTheme } from "./theme";
// Экраны волны B4: создание и админка — реальный API.
import { AdminSettings } from "./admin-settings";
import { CreateForm } from "./create-form";

// Вкладки скелета.
const TABS = ["Заявки", "Создание", "Настройки"] as const;
type Tab = (typeof TABS)[number];

// Подписи ролей в шапке (матрица README п.1).
const ROLE_LABELS: Record<Role, string> = {
  hr: "ОК",
  owner: "Владелец",
  admin: "Админ",
  guest: "Гость",
};

interface SedLayoutProps {
  // Роль текущей сессии (из /auth/me); выбора роли на экране нет.
  role: Role;
  // Выход из системы: очистка сессии и возврат на экран логина.
  onLogout: () => void;
}

// Основной каркас экрана.
export function SedLayout(props: SedLayoutProps) {
  const { role, onLogout } = props;
  const { theme, setTheme } = useTheme();
  const [tab, setTab] = useState<Tab>("Заявки");
  const [folder, setFolder] = useState<FolderId>("agreement");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [rows, setRows] = useState<RequestRow[]>([]);
  const [filters, setFilters] = useState<RequestFilters>(EMPTY_FILTERS);
  const [error, setError] = useState<string>("");
  // Печать бегунка и документы (W3b): выбранная заявка + результат печати + список версий.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [printStatus, setPrintStatus] = useState<string>("");
  const [printError, setPrintError] = useState<string>("");
  const [docs, setDocs] = useState<DocumentMeta[]>([]);
  const [docsError, setDocsError] = useState<string>("");
  // Карточка заявки (W5b): шаги, отметки владельца, действия ОК/админа, вложения.
  const [card, setCard] = useState<RequestOut | null>(null);
  const [cardError, setCardError] = useState<string>("");
  const [decisionComment, setDecisionComment] = useState<string>("");
  const [decisionError, setDecisionError] = useState<string>("");
  const [cardActionStatus, setCardActionStatus] = useState<string>("");
  const [cardActionError, setCardActionError] = useState<string>("");
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([]);
  const [attachmentsError, setAttachmentsError] = useState<string>("");
  const [uploadError, setUploadError] = useState<string>("");
  // Принудительная перезагрузка списка после отметки/действия ОК (refetch).
  const [listVersion, setListVersion] = useState<number>(0);

  // Видимые вкладки по роли: «Настройки» — только админу, «Создание» — ОК и админу.
  const visibleTabs = useMemo(() => {
    const tabs: Tab[] = ["Заявки"];
    if (role === "hr" || role === "admin") tabs.push("Создание");
    if (role === "admin") tabs.push("Настройки");
    return tabs;
  }, [role]);

  // Загрузка папок при смене роли; активная папка — первая доступная.
  useEffect(() => {
    let alive = true;
    getFolders()
      .then((data) => {
        if (alive) {
          setFolders(data);
          if (data.length > 0 && !data.some((f) => f.id === folder)) setFolder(data[0].id);
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки папок");
      });
    return () => {
      alive = false;
    };
  }, [role]);

  // Загрузка предприятий для фильтра таблицы (без хардкод-массивов).
  useEffect(() => {
    let alive = true;
    getEnterprises()
      .then((data) => {
        if (alive) setEnterprises(data);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Ошибка загрузки предприятий");
      });
    return () => {
      alive = false;
    };
  }, [role]);

  // Загрузка таблицы: GET /api/requests, папка и фильтры — на клиенте.
  // Переход на вкладку «Заявки» обновляет список (новая заявка видна сразу).
  useEffect(() => {
    let alive = true;
    getRequests()
      .then((data) => {
        if (alive) {
          setRows(filterRequests(data.map(toRequestRow), folder, filters));
          setError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setRows([]);
          setError(e instanceof Error ? e.message : "Ошибка загрузки заявок");
        }
      });
    return () => {
      alive = false;
    };
  }, [folder, filters, role, tab, listVersion]);

  // Загрузка документов выбранной заявки (GET /api/documents/{id}).
  useEffect(() => {
    let alive = true;
    if (!selectedId) {
      setDocs([]);
      setDocsError("");
      return;
    }
    getDocuments(selectedId)
      .then((data) => {
        if (alive) {
          setDocs(data);
          setDocsError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setDocs([]);
          setDocsError(e instanceof Error ? e.message : "Ошибка загрузки документов");
        }
      });
    return () => {
      alive = false;
    };
  }, [selectedId]);

  // Загрузка карточки выбранной заявки (GET /api/requests/{id}).
  useEffect(() => {
    let alive = true;
    setCardError("");
    setCardActionStatus("");
    setCardActionError("");
    setDecisionError("");
    setDecisionComment("");
    if (!selectedId) {
      setCard(null);
      return;
    }
    getRequest(selectedId)
      .then((data) => {
        if (alive) {
          setCard(data);
          setCardError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setCard(null);
          setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
        }
      });
    return () => {
      alive = false;
    };
  }, [selectedId]);

  // Загрузка вложений выбранной заявки (GET /api/requests/{id}/attachments).
  useEffect(() => {
    let alive = true;
    setAttachmentsError("");
    setUploadError("");
    if (!selectedId) {
      setAttachments([]);
      return;
    }
    getAttachments(selectedId)
      .then((data) => {
        if (alive) {
          setAttachments(data);
          setAttachmentsError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setAttachments([]);
          setAttachmentsError(e instanceof Error ? e.message : "Ошибка загрузки вложений");
        }
      });
    return () => {
      alive = false;
    };
  }, [selectedId]);

  // Печать бегунка: POST /api/requests/{id}/print. generated=false с reason —
  // НЕ ошибка: показываем reason как статус, не как сбой.
  async function handlePrint(): Promise<void> {
    if (!selectedId) return;
    setPrintError("");
    setPrintStatus("");
    try {
      const result = await printRequest(selectedId);
      setPrintStatus(result.generated ? `Бегунок ${result.version} сгенерирован` : (result.reason ?? "Бегунок не сгенерирован"));
      // После генерации версии список документов мог измениться.
      getDocuments(selectedId)
        .then((data) => setDocs(data))
        .catch(() => undefined);
    } catch (e: unknown) {
      setPrintError(e instanceof Error ? e.message : "Ошибка печати");
    }
  }

  // Первый ожидающий шаг карточки (отметку ставит владелец строго по порядку).
  const pendingStep = card?.steps.find((s) => s.status === "ожидает") ?? null;

  // Перезагрузка карточки выбранной заявки после отметки/действия.
  async function refreshRequest(): Promise<void> {
    if (!selectedId) return;
    try {
      setCard(await getRequest(selectedId));
      setCardError("");
    } catch (e: unknown) {
      setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
    }
  }

  // Общий запуск действия (отметка/переход), затем — refetch карточки и списка.
  async function runAction(action: () => Promise<unknown>, okMessage: string): Promise<boolean> {
    setCardActionError("");
    setCardActionStatus("");
    try {
      await action();
      setCardActionStatus(okMessage);
      await refreshRequest();
      setListVersion((v) => v + 1);
      return true;
    } catch (e: unknown) {
      setCardActionError(e instanceof Error ? e.message : "Действие не выполнено");
      return false;
    }
  }

  // Отметка владельца: комментарий обязателен при отказе/возврате (клиент + 422 с сервера).
  async function handleDecision(order: number, decision: StepDecision): Promise<void> {
    if (!selectedId) return;
    const text = decisionComment.trim();
    if (decision !== "approve" && !text) {
      setDecisionError("При отказе/возврате комментарий обязателен");
      return;
    }
    setDecisionError("");
    const ok = await runAction(
      () => decideStep(selectedId, order, decision, text || undefined),
      "Отметка сохранена",
    );
    if (ok) setDecisionComment("");
  }

  // Действия ОК/админа по статусу заявки.
  async function handleSubmit(): Promise<void> {
    if (selectedId) await runAction(() => submitRequest(selectedId), "Заявка отправлена на согласование");
  }
  async function handleToExecution(): Promise<void> {
    if (selectedId) await runAction(() => toExecution(selectedId), "Заявка отправлена к исполнению");
  }
  async function handleFinish(): Promise<void> {
    if (selectedId) await runAction(() => finishRequest(selectedId), "Заявка завершена");
  }

  // Перезагрузка вложений после загрузки файла.
  async function refreshAttachments(): Promise<void> {
    if (!selectedId) return;
    try {
      setAttachments(await getAttachments(selectedId));
      setAttachmentsError("");
    } catch (e: unknown) {
      setAttachmentsError(e instanceof Error ? e.message : "Ошибка загрузки вложений");
    }
  }

  // Загрузка скана: multipart через FormData; 413/415/409 — понятный текст из ApiHttpError.
  async function handleUploadFile(e: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = e.target.files?.[0];
    if (!file || !selectedId) return;
    setUploadError("");
    try {
      await uploadAttachment(selectedId, file);
      e.target.value = "";
      await refreshAttachments();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Ошибка загрузки файла");
    }
  }

  // Заголовок таблицы зависит от роли (владельцу — без колонки ПДн).
  const columns = useMemo(() => {
    if (role === "owner") return ["№", "Сотрудник (маска)", "Шаг", "Срок"];
    return ["№", "Сотрудник", "Предприятие", "Статус", "Шаг", "Срок"];
  }, [role]);

  return (
    <div className="sed-shell">
      {/* Шапка: роль из сессии (без выбора), тема и выход. */}
      <header className="sed-header">
        СЭД — Увольнение (скелет) · роль: {ROLE_LABELS[role]}
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
          style={{ marginLeft: 8, borderColor: "#fff", color: "#fff" }}
          onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          title="Задел тёмной темы"
        >
          Тема: {theme === "light" ? "светлая" : "тёмная"}
        </button>
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
          style={{ marginLeft: 8, borderColor: "#fff", color: "#fff" }}
          onClick={onLogout}
          title="Выход из системы"
        >
          Выйти
        </button>
      </header>

      {/* Вкладки скелета (по роли сессии). */}
      <nav className="sed-tabs" aria-label="Вкладки">
        {visibleTabs.map((name) => (
          <button
            key={name}
            type="button"
            className={tab === name ? "sed-tab sed-tab--active" : "sed-tab"}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      <div className="sed-body">
        {/* Дерево папок со счётчиками. */}
        <aside className="sed-folders" aria-label="Папки заявок">
          {folders.length === 0 && <div className="sed-note">Папок нет (гость)</div>}
          {folders.map((item) => (
            <button
              key={item.id}
              type="button"
              className={folder === item.id ? "sed-folder sed-folder--active" : "sed-folder"}
              onClick={() => setFolder(item.id)}
            >
              <span>{item.title}</span>
              <span className="sed-folder__count">{item.count}</span>
            </button>
          ))}
        </aside>

        {/* Контент: вкладка создания/настроек — экраны B4, иначе таблица. */}
        <main className="sed-content">
          {tab === "Создание" && <CreateForm role={role} />}
          {tab === "Настройки" && <AdminSettings role={role} />}
          {tab === "Заявки" && (
          <>
          <div className="sed-toolbar" aria-label="Панель действий">
            <button type="button" className="sed-btn">
              Создать заявку
            </button>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              disabled={!selectedId}
              onClick={handlePrint}
              title={selectedId ? `Печать бегунка ${selectedId}` : "Выберите заявку в таблице"}
            >
              Печать
            </button>
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              Сбросить фильтры
            </button>
          </div>

          {/* Фильтры таблицы (предприятия — из API). */}
          <div className="sed-filters" aria-label="Фильтры">
            <input
              aria-label="Поиск"
              placeholder="Поиск по ФИО/логину"
              value={filters.query}
              onChange={(e) => setFilters({ ...filters, query: e.target.value })}
            />
            <select
              aria-label="Предприятие"
              value={filters.enterprise}
              onChange={(e) => setFilters({ ...filters, enterprise: e.target.value })}
            >
              <option value="">Все предприятия</option>
              {enterprises.map((ent) => (
                <option key={ent.code} value={ent.code}>
                  {ent.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Статус"
              value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            >
              <option value="">Все статусы</option>
              <option value="Черновик">Черновик</option>
              <option value="На согласовании">На согласовании</option>
              <option value="На доработке">На доработке</option>
              <option value="Согласовано">Согласовано</option>
              <option value="К исполнению">К исполнению</option>
              <option value="Завершено">Завершено</option>
              <option value="Отклонено">Отклонено</option>
              <option value="Отозвано">Отозвано</option>
            </select>
          </div>

          {error && <div role="alert">Ошибка: {error}</div>}
          {printStatus && <div role="status">{printStatus}</div>}
          {printError && <div role="alert">{printError}</div>}

          {/* Таблица заявок. */}
          <table className="sed-table" aria-label="Заявки">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => setSelectedId(row.id)}
                  style={
                    selectedId === row.id
                      ? { background: "var(--sed-primary-soft)", cursor: "pointer" }
                      : { cursor: "pointer" }
                  }
                >
                  <td>{row.id}</td>
                  <td>{row.fio}</td>
                  {role !== "owner" && (
                    <>
                      <td>{row.enterprise}</td>
                      <td>{row.status}</td>
                    </>
                  )}
                  <td>{row.step}</td>
                  <td>{row.dueDate}</td>
                </tr>
              ))}
              {rows.length === 0 && !error && (
                <tr>
                  <td colSpan={columns.length}>Заявок нет</td>
                </tr>
              )}
            </tbody>
          </table>

          {/* Блок документов выбранной заявки: версии бегунка и ссылки на PDF. */}
          {selectedId && (
            <section aria-label="Документы">
              <h3>Документы заявки {selectedId}</h3>
              {docsError && <div role="alert">{docsError}</div>}
              {docs.length === 0 && !docsError && <div className="sed-note">Документов нет</div>}
              <ul>
                {docs.map((doc) => (
                  <li key={doc.version}>
                    Бегунок {doc.version} · {doc.created_at} ·{" "}
                    <a href={`/api/documents/${encodeURIComponent(selectedId)}/pdf?version=${encodeURIComponent(doc.version)}`}>
                      PDF
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Карточка выбранной заявки (W5b): шаги, отметка владельца, действия ОК, вложения. */}
          {selectedId && (
            <section aria-label="Карточка заявки">
              <h3>Карточка заявки {selectedId}</h3>
              {cardError && <div role="alert">{cardError}</div>}
              {!card && !cardError && <div className="sed-note">Загрузка карточки…</div>}
              {card && (
                <>
                  {/* ПДн: владельцу fio/tab_num не приходят — маска «Сотрудник № id». */}
                  <div className="sed-note">
                    Статус: {card.status} ·{" "}
                    {role === "owner" ? (
                      <>Сотрудник № {card.id}</>
                    ) : (
                      <>
                        {card.fio ?? `Сотрудник № ${card.id}`}
                        {card.tab_num ? ` · Таб.№ ${card.tab_num}` : ""} · {card.department} · {card.position}
                        {card.enterprise ? ` · ${card.enterprise}` : ""}
                      </>
                    )}
                  </div>

                  {/* Шаги маршрута: группа/статус/срок/комментарий. */}
                  <table className="sed-table" aria-label="Шаги заявки">
                    <thead>
                      <tr>
                        <th>№</th>
                        <th>Группа</th>
                        <th>Статус</th>
                        <th>Срок</th>
                        <th>Комментарий</th>
                      </tr>
                    </thead>
                    <tbody>
                      {card.steps.map((step) => (
                        <tr key={step.order}>
                          <td>{step.order}</td>
                          <td>{step.owner_group}</td>
                          <td>{step.status}</td>
                          <td>{step.expires_at.slice(0, 10)}</td>
                          <td>{step.comment ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {/* Отметка владельца своего ожидающего шага. */}
                  {role === "owner" && pendingStep && (
                    <div aria-label="Решение владельца">
                      <h4>Моё решение</h4>
                      <input
                        aria-label="Комментарий к решению"
                        placeholder="Комментарий (обязателен при отказе/возврате)"
                        value={decisionComment}
                        onChange={(e) => setDecisionComment(e.target.value)}
                      />
                      {decisionError && <div role="alert">{decisionError}</div>}
                      <div className="sed-toolbar" style={{ marginTop: 8 }}>
                        <button
                          type="button"
                          className="sed-btn"
                          onClick={() => handleDecision(pendingStep.order, "approve")}
                        >
                          Согласовать
                        </button>
                        <button
                          type="button"
                          className="sed-btn sed-btn--ghost"
                          onClick={() => handleDecision(pendingStep.order, "reject")}
                        >
                          Отказать
                        </button>
                        <button
                          type="button"
                          className="sed-btn sed-btn--ghost"
                          onClick={() => handleDecision(pendingStep.order, "return")}
                        >
                          Вернуть
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Действия ОК/админа по статусу заявки. */}
                  {role !== "owner" && (
                    <div className="sed-toolbar" aria-label="Действия по заявке">
                      {(card.status === "Черновик" || card.status === "На доработке") && (
                        <button type="button" className="sed-btn" onClick={handleSubmit}>
                          Отправить на согласование
                        </button>
                      )}
                      {card.status === "Согласовано" && (
                        <button type="button" className="sed-btn" onClick={handleToExecution}>
                          К исполнению
                        </button>
                      )}
                      {card.status === "К исполнению" && (
                        <button type="button" className="sed-btn" onClick={handleFinish}>
                          Завершить
                        </button>
                      )}
                    </div>
                  )}

                  {cardActionStatus && <div role="status">{cardActionStatus}</div>}
                  {cardActionError && <div role="alert">{cardActionError}</div>}

                  {/* Скан-вложения: список мета + загрузка файла. */}
                  <section aria-label="Вложения">
                    <h4>Вложения</h4>
                    {attachmentsError && <div role="alert">{attachmentsError}</div>}
                    {uploadError && <div role="alert">{uploadError}</div>}
                    {attachments.length === 0 && !attachmentsError && (
                      <div className="sed-note">Вложений нет</div>
                    )}
                    <ul>
                      {attachments.map((att) => (
                        <li key={att.id}>
                          {att.filename} · {att.size} Б · {att.created_at.slice(0, 10)} ·{" "}
                          <a href={`/api/attachments/${encodeURIComponent(att.id)}/file`}>Скачать</a>
                        </li>
                      ))}
                    </ul>
                    <label>
                      Загрузить скан
                      <input type="file" aria-label="Файл скана" onChange={handleUploadFile} />
                    </label>
                  </section>
                </>
              )}
            </section>
          )}

          <div className="sed-note">Волна 5: карточка заявки и скан-вложения — из реального API.</div>
          </>
          )}
        </main>
      </div>
    </div>
  );
}
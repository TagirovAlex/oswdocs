// Карточка заявки (Задача 3): шаги, отметки владельца, действия ОК/админа,
// документы бегунка (печать) и скан-вложения. Используется в попапе
// «карточка заявки» (?view=request&id=…) и на вкладке «Заявки».
// Данные — из реального API (requests-client); значения — из settings, хардкода нет.
import { useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import {
  decideStep,
  deleteRequest,
  finishRequest,
  getAttachments,
  getDocuments,
  getRequest,
  notifyRequestsChanged,
  printRequest,
  stepLabel,
  submitRequest,
  toExecution,
  uploadAttachment,
} from "./requests-client";
import type { AttachmentMeta, DocumentMeta, RequestOut, RequestStep, StepDecision } from "./requests-client";
import type { Role } from "./api-mock";

interface RequestCardProps {
  // Идентификатор заявки (REQ-XXXX).
  requestId: string;
  // Роль сессии (полная карточка — hr/hr_admin/admin; владельцу — свои шаги без ПДн).
  role: Role;
}

// Исполнитель шага: ФИО (owner_name), если бэкенд его прислал, иначе название
// группы. ФИО приходит для персональных шагов (в т.ч. замена руководителя,
// resolver=ad_direct_manager, исполнитель в assignee) — по признаку resolver
// их не отличить от группового шага, поэтому смотрим owner_name. У by_user
// бэкенд кладёт логин в owner_group — без ФИО показываем нейтральный текст.
// Логика совпадает с stepOwnerLabel в requests-client.
function stepOwnerCell(step: RequestStep): string {
  if (step.owner_name) return step.owner_name;
  if (step.resolver === "by_user") return "Персональный исполнитель";
  return step.owner_group || "—";
}

// Карточка заявки со всеми блоками (W5b + документы + вложения).
export function RequestCard(props: RequestCardProps) {
  const { requestId, role } = props;
  const [card, setCard] = useState<RequestOut | null>(null);
  const [cardError, setCardError] = useState<string>("");
  const [printStatus, setPrintStatus] = useState<string>("");
  const [printError, setPrintError] = useState<string>("");
  const [docs, setDocs] = useState<DocumentMeta[]>([]);
  const [docsError, setDocsError] = useState<string>("");
  const [decisionComment, setDecisionComment] = useState<string>("");
  const [decisionError, setDecisionError] = useState<string>("");
  const [cardActionStatus, setCardActionStatus] = useState<string>("");
  const [cardActionError, setCardActionError] = useState<string>("");
  const [deleteBusy, setDeleteBusy] = useState<boolean>(false);
  const [deleteError, setDeleteError] = useState<string>("");
  const [deleted, setDeleted] = useState<boolean>(false);
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([]);
  const [attachmentsError, setAttachmentsError] = useState<string>("");
  const [uploadError, setUploadError] = useState<string>("");

  // Загрузка документов (версии бегунка и ссылки на PDF).
  useEffect(() => {
    let alive = true;
    getDocuments(requestId)
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
  }, [requestId]);

  // Загрузка карточки (GET /api/requests/{id}).
  useEffect(() => {
    let alive = true;
    setCardError("");
    setCardActionStatus("");
    setCardActionError("");
    setDecisionError("");
    setDecisionComment("");
    setPrintStatus("");
    setPrintError("");
    setDeleteError("");
    setDeleteBusy(false);
    getRequest(requestId)
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
  }, [requestId]);

  // Загрузка вложений (GET /api/requests/{id}/attachments).
  useEffect(() => {
    let alive = true;
    setAttachmentsError("");
    setUploadError("");
    getAttachments(requestId)
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
  }, [requestId]);

  // Печать бегунка: POST /api/requests/{id}/print. generated=false с reason —
  // НЕ ошибка: показываем reason как статус, не как сбой.
  async function handlePrint(): Promise<void> {
    setPrintError("");
    setPrintStatus("");
    try {
      const result = await printRequest(requestId);
      setPrintStatus(
        result.generated
          ? `Бегунок ${result.version} сгенерирован`
          : (result.reason ?? "Бегунок не сгенерирован"),
      );
      getDocuments(requestId)
        .then((data) => setDocs(data))
        .catch(() => undefined);
    } catch (e: unknown) {
      setPrintError(e instanceof Error ? e.message : "Ошибка печати");
    }
  }

  // Шаг, который может отметить ТЕКУЩИЙ пользователь: ожидает И can_act
  // (единственный источник истины от бэкенда, без эвристики по роли).
  const actStep = card?.steps.find((s) => s.status === "ожидает" && s.can_act === true) ?? null;

  async function refreshRequest(): Promise<void> {
    try {
      setCard(await getRequest(requestId));
      setCardError("");
    } catch (e: unknown) {
      setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
    }
  }

  async function runAction(action: () => Promise<unknown>, okMessage: string): Promise<boolean> {
    setCardActionError("");
    setCardActionStatus("");
    try {
      await action();
      setCardActionStatus(okMessage);
      await refreshRequest();
      return true;
    } catch (e: unknown) {
      setCardActionError(e instanceof Error ? e.message : "Действие не выполнено");
      return false;
    }
  }

  async function handleDecision(order: number, decision: StepDecision): Promise<void> {
    const text = decisionComment.trim();
    if (decision !== "approve" && !text) {
      setDecisionError("При отказе/возврате комментарий обязателен");
      return;
    }
    setDecisionError("");
    const ok = await runAction(
      () => decideStep(requestId, order, decision, text || undefined),
      "Отметка сохранена",
    );
    if (ok) setDecisionComment("");
  }

  async function handleSubmit(): Promise<void> {
    await runAction(() => submitRequest(requestId), "Заявка отправлена на согласование");
  }
  async function handleToExecution(): Promise<void> {
    await runAction(() => toExecution(requestId), "Заявка отправлена к исполнению");
  }
  async function handleFinish(): Promise<void> {
    await runAction(() => finishRequest(requestId), "Заявка завершена");
  }

  // Удаление заявки (только админ; для тестового периода). Подтверждение —
  // удаление необратимо. Список заявок уведомляется через localStorage
  // (событие storage в основном окне); при открытой вкладке (?view=request&
  // key= в той же вкладке) window.close() не работает — показываем подтверждение
  // удаления и ссылку «К списку заявок».
  async function handleDelete(): Promise<void> {
    setDeleteError("");
    if (!window.confirm(`Удалить заявку ${requestId}? Действие необратимо.`)) return;
    setDeleteBusy(true);
    try {
      await deleteRequest(requestId);
      notifyRequestsChanged();
      setDeleteBusy(false);
      if (window.opener) {
        window.close();
        return;
      }
      setDeleted(true);
    } catch (e: unknown) {
      setDeleteError(e instanceof Error ? e.message : "Ошибка удаления");
      setDeleteBusy(false);
    }
  }

  async function refreshAttachments(): Promise<void> {
    try {
      setAttachments(await getAttachments(requestId));
      setAttachmentsError("");
    } catch (e: unknown) {
      setAttachmentsError(e instanceof Error ? e.message : "Ошибка загрузки вложений");
    }
  }

  async function handleUploadFile(e: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError("");
    try {
      await uploadAttachment(requestId, file);
      e.target.value = "";
      await refreshAttachments();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Ошибка загрузки файла");
    }
  }

  return (
    <section aria-label="Карточка заявки">
      <h3>Карточка заявки {requestId}</h3>
      {cardError && <div role="alert">{cardError}</div>}
      {!card && !cardError && !deleted && <div className="sed-note">Загрузка карточки…</div>}
      {/* Заявка удалена в этой же вкладке: закрыть окно нельзя — возврат к списку. */}
      {deleted && (
        <div>
          <div role="status">Заявка удалена</div>
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            <a className="sed-btn" href="?">
              К списку заявок
            </a>
          </div>
        </div>
      )}
      {card && !deleted && (
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
                {card.enterprise ? ` · ${card.enterprise_name ?? card.enterprise}` : ""}
              </>
            )}
          </div>

          {/* Печать бегунка (ОК/админ) — в карточке, не в списке. */}
          {role !== "owner" && (
            <div className="sed-toolbar" style={{ marginTop: 8 }}>
              <button type="button" className="sed-btn" onClick={handlePrint}>
                Печать
              </button>
              {printStatus && <span role="status">{printStatus}</span>}
              {printError && <span role="alert">{printError}</span>}
            </div>
          )}

          {/* Документы: версии бегунка и ссылки на PDF. */}
          <section aria-label="Документы">
            <h4>Документы</h4>
            {docsError && <div role="alert">{docsError}</div>}
            {docs.length === 0 && !docsError && <div className="sed-note">Документов нет</div>}
            <ul>
              {docs.map((doc) => (
                <li key={doc.version}>
                  Бегунок {doc.version} · {doc.created_at} ·{" "}
                  <a
                    href={`/api/documents/${encodeURIComponent(requestId)}/pdf?version=${encodeURIComponent(doc.version)}`}
                  >
                    PDF
                  </a>
                </li>
              ))}
            </ul>
          </section>

          {/* Шаги маршрута: исполнитель/статус/срок/комментарий. Логин AD
              согласующего (assignee) в UI не выводится — только ФИО (owner_name)
              для персональных шагов либо название группы. */}
          <table className="sed-table" aria-label="Шаги заявки">
            <thead>
              <tr>
                <th>№</th>
                <th>Исполнитель</th>
                <th>Статус</th>
                <th>Срок</th>
                <th>Комментарий</th>
              </tr>
            </thead>
            <tbody>
              {card.steps.map((step) => (
                <tr key={step.order}>
                  <td>{stepLabel(step.order)}</td>
                  <td>
                    {stepOwnerCell(step)}
                  </td>
                  <td>{step.status}</td>
                  <td>{step.expires_at.slice(0, 10)}</td>
                  <td>{step.comment ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Отметка своего шага — строго по can_act от бэкенда (у согласованных и
              закрытых шагов can_act=false, кнопок нет). */}
          {actStep && (
            <div aria-label="Решение владельца">
              <h4>Моё решение · Шаг {stepLabel(actStep.order)}</h4>
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
                  onClick={() => handleDecision(actStep.order, "approve")}
                >
                  Согласовать
                </button>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => handleDecision(actStep.order, "reject")}
                >
                  Отказать
                </button>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => handleDecision(actStep.order, "return")}
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
              {role === "admin" && (
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={handleDelete}
                  disabled={deleteBusy}
                >
                  Удалить заявку
                </button>
              )}
            </div>
          )}

          {cardActionStatus && <div role="status">{cardActionStatus}</div>}
          {cardActionError && <div role="alert">{cardActionError}</div>}
          {deleteError && <div role="alert">{deleteError}</div>}

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
  );
}

// Карточка заявки (Задача 3): шаги, отметки владельца, действия ОК/админа,
// документы бегунка (печать) и скан-вложения. Используется в попапе
// «карточка заявки» (?view=request&id=…) и на вкладке «Заявки».
// Данные — из реального API (requests-client); значения — из settings, хардкода нет.
import { useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import { me } from "./auth-client";
import {
  addComment,
  base64ToBlob,
  decideStep,
  deleteAttachment,
  deleteRequest,
  finishRequest,
  getAdGroupMembers,
  getAttachments,
  getComments,
  getDocTypes,
  getEmployeeCard,
  getHistory,
  getRequest,
  getStepGroups,
  notifyRequestsChanged,
  printRequest,
  rollbackRequest,
  stepLabel,
  submitRequest,
  toExecution,
  updateRequest,
  uploadAttachment,
  withdrawRequest,
} from "./requests-client";
import type {
  AdGroupMember,
  AttachmentMeta,
  DocType,
  RequestComment,
  RequestHistoryItem,
  RequestOut,
  RequestStep,
  StepDecision,
} from "./requests-client";
import type { Role } from "./api-mock";
import { attachmentUrl, employeeUrl, openPopup } from "./windows";

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

// Дата записи истории: локализованная; при битой строке — как пришла.
function historyWhen(at: string): string {
  const d = new Date(at);
  return isNaN(d.getTime()) ? at : d.toLocaleString("ru-RU");
}

// Статусы, из которых заявку ещё можно отозвать (POST /requests/{id}/withdraw):
// закрытые (Завершено/Отклонено/Отозвано) бэкенд отдаёт 409 — их тут нет.
const WITHDRAW_STATUSES = [
  "Черновик",
  "На согласовании",
  "На доработке",
  "Согласовано",
  "К исполнению",
];

// Карточка заявки со всеми блоками (W5b + документы + вложения).
export function RequestCard(props: RequestCardProps) {
  const { requestId, role } = props;
  const [card, setCard] = useState<RequestOut | null>(null);
  const [cardError, setCardError] = useState<string>("");
  const [printStatus, setPrintStatus] = useState<string>("");
  const [printError, setPrintError] = useState<string>("");
  // Свежий PDF последней печати — ручная ссылка, если вкладку открыть не дали.
  const [printPdf, setPrintPdf] = useState<string | null>(null);
  const [decisionComment, setDecisionComment] = useState<string>("");
  const [decisionError, setDecisionError] = useState<string>("");
  // Форма решения скрыта на время запроса: после отказа закрывается сразу,
  // не дожидаясь ответа бэкенда.
  const [actStepHidden, setActStepHidden] = useState<boolean>(false);
  const [cardActionStatus, setCardActionStatus] = useState<string>("");
  const [cardActionError, setCardActionError] = useState<string>("");
  const [deleteBusy, setDeleteBusy] = useState<boolean>(false);
  const [deleteError, setDeleteError] = useState<string>("");
  const [deleted, setDeleted] = useState<boolean>(false);
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([]);
  const [attachmentsError, setAttachmentsError] = useState<string>("");
  const [uploadError, setUploadError] = useState<string>("");
  const [attachmentDeleteError, setAttachmentDeleteError] = useState<string>("");
  // Логин текущей сессии (GET /auth/me): нужен для кнопки «Удалить» у автора.
  // Не загрузился — кнопок удаления у не-админов просто нет, карточка работает.
  const [mySam, setMySam] = useState<string>("");
  // История изменений (GET /api/requests/{id}/history) и комментарии заявки.
  const [history, setHistory] = useState<RequestHistoryItem[]>([]);
  const [historyError, setHistoryError] = useState<string>("");
  const [comments, setComments] = useState<RequestComment[]>([]);
  const [commentsError, setCommentsError] = useState<string>("");
  const [newComment, setNewComment] = useState<string>("");
  const [commentError, setCommentError] = useState<string>("");
  // Вкладка карточки по образцу (doc.html): «Лист рассмотрения» (панели +
  // таблица шагов) либо «История» (история + комментарии). Только вид.
  const [cardTab, setCardTab] = useState<"sheet" | "history">("sheet");
  // Наименования групп (GET /api/step-groups: id → name) для колонки
  // «Должность / Группа» у групповых шагов; без наименования — код как раньше.
  const [groupNames, setGroupNames] = useState<Record<string, string>>({});
  // Состав групп AD (GET /api/ad/groups/{group}/members) для колонки
  // «Исполнитель» у групповых шагов; показываем всех без сворачивания.
  const [groupMembers, setGroupMembers] = useState<Record<string, AdGroupMember[]>>({});
  // Должность персонального исполнителя из стыковочной таблицы 1С+АД
  // (GET /api/employees/card по employee_key шага): приоритет — title из АД.
  const [stepPositions, setStepPositions] = useState<Record<number, string>>({});
  // Панель администратора СЭД (роль sed_admin от бэкенда): правка полей и откат.
  const isSedAdmin = (role as string) === "sed_admin";
  const [sedDocTypes, setSedDocTypes] = useState<DocType[]>([]);
  const [sedSubject, setSedSubject] = useState<string>("");
  const [sedContent, setSedContent] = useState<string>("");
  const [sedDocType, setSedDocType] = useState<string>("");
  const [rollbackStep, setRollbackStep] = useState<string>("");
  const [sedStatus, setSedStatus] = useState<string>("");
  const [sedError, setSedError] = useState<string>("");

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

  // Логин сессии для кнопки «Удалить» у автора вложения (прецедент — create-form).
  useEffect(() => {
    let alive = true;
    me()
      .then((user) => {
        if (alive) setMySam(user.sam);
      })
      .catch(() => {
        if (alive) setMySam("");
      });
    return () => {
      alive = false;
    };
  }, []);

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

  // История заявки (GET /api/requests/{id}/history; видна участникам). Своя
  // загрузка: ошибка — примечанием, карточку не ломает (alert не используем).
  useEffect(() => {
    let alive = true;
    setHistoryError("");
    getHistory(requestId)
      .then((items) => {
        if (alive) setHistory(items);
      })
      .catch((e: unknown) => {
        if (alive) {
          setHistory([]);
          setHistoryError(e instanceof Error ? e.message : "Ошибка загрузки истории");
        }
      });
    return () => {
      alive = false;
    };
  }, [requestId]);

  // Комментарии заявки (GET /api/requests/{id}/comments; отдельная таблица).
  useEffect(() => {
    let alive = true;
    setCommentsError("");
    getComments(requestId)
      .then((items) => {
        if (alive) setComments(items);
      })
      .catch((e: unknown) => {
        if (alive) {
          setComments([]);
          setCommentsError(e instanceof Error ? e.message : "Ошибка загрузки комментариев");
        }
      });
    return () => {
      alive = false;
    };
  }, [requestId]);

  // Виды документов для панели админа СЭД (все, включая деактивированные —
  // чтобы показать уже выбранный код). Недоступность — пустой селект без текста.
  useEffect(() => {
    if (!isSedAdmin) return;
    let alive = true;
    getDocTypes(false)
      .then((items) => {
        if (alive) setSedDocTypes(items);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [isSedAdmin]);

  // Наименования групп-владельцев (settings step-groups): id → читабельное
  // название; недоступность — fallback на код группы в таблице.
  useEffect(() => {
    let alive = true;
    getStepGroups()
      .then((items) => {
        if (!alive) return;
        const names: Record<string, string> = {};
        items.forEach((g) => {
          if (g.name) names[g.id] = g.name;
        });
        setGroupNames(names);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // Состав групповых шагов и должности персональных — из уже собранной
  // стыковки 1С+АД (без новых эндпоинтов): члены группы — из AD-состава,
  // должность исполнителя — из карточки сотрудника по employee_key шага.
  // Состав запрашиваем для ВСЕХ групповых шагов (критерий — как в isGroupStep):
  // наличие owner_name (наименования из справочника) его не отменяет, иначе
  // в «Сотруднике» остаётся название группы вместо людей.
  useEffect(() => {
    if (!card) return;
    let alive = true;
    const groups = new Set<string>();
    card.steps.forEach((s) => {
      if (!s.assignee && s.resolver !== "by_user" && s.owner_group) groups.add(s.owner_group);
    });
    groups.forEach((group) => {
      getAdGroupMembers(group)
        .then((res) => {
          if (alive) setGroupMembers((prev) => ({ ...prev, [group]: res }));
        })
        .catch(() => undefined);
    });
    card.steps.forEach((step) => {
      if (!step.owner_name) return;
      const enterprise = step.emp_enterprise ?? step.employee_key?.split("|")[0] ?? "";
      const baseCode = step.emp_base_code ?? step.employee_key?.split("|")[1] ?? "";
      const tabNum = step.emp_tab_num ?? step.employee_key?.split("|")[2] ?? "";
      if (!enterprise || !baseCode || !tabNum) return;
      getEmployeeCard(enterprise, baseCode, tabNum)
        .then((data) => {
          if (!alive) return;
          const title =
            data.ad?.title ?? data.snapshot_ad?.title ?? data.position ?? "";
          if (title) setStepPositions((prev) => ({ ...prev, [step.order]: title }));
        })
        .catch(() => undefined);
    });
    return () => {
      alive = false;
    };
  }, [card]);

  // Инициализация полей панели админа СЭД данными карточки (смена заявки/роли).
  useEffect(() => {
    if (!card || !isSedAdmin) return;
    setSedSubject(card.subject ?? "");
    setSedContent(card.content ?? "");
    setSedDocType(card.doc_type_code ?? "");
    setRollbackStep("");
  }, [card, isSedAdmin]);

  // Печать бегунка: POST /api/requests/{id}/print. Вариант 1 — версии не
  // накапливаются: PDF приходит base64 в ответе. generated=false с reason —
  // НЕ ошибка: показываем reason как статус, не как сбой. При успешной генерации
  // декодируем PDF в Blob и открываем диалог печати браузера из скрытого iframe —
  // без новой вкладки.
  async function handlePrint(): Promise<void> {
    setPrintError("");
    setPrintStatus("");
    setPrintPdf(null);
    try {
      const result = await printRequest(requestId);
      setPrintStatus(
        result.generated
          ? "Бегунок сгенерирован"
          : (result.reason ?? "Бегунок не сгенерирован"),
      );
      if (result.generated && result.pdf_b64) {
        // PDF печатаем blob-ом из base64 ответа через скрытый iframe: диалог
        // печати открывается с нужным файлом, без создания новых вкладок.
        try {
          const pdfUrl = URL.createObjectURL(base64ToBlob(result.pdf_b64, "application/pdf"));
          const frame = document.createElement("iframe");
          frame.style.position = "absolute";
          frame.style.width = "1px";
          frame.style.height = "1px";
          frame.style.opacity = "0";
          frame.style.border = "none";
          frame.src = pdfUrl;
          frame.onload = () => {
            try {
              frame.contentWindow?.focus();
              frame.contentWindow?.print();
            } catch {
              // Браузер заблокировал печать: даём ручную ссылку на blob.
              setPrintPdf(pdfUrl);
              setPrintStatus("Браузер заблокировал печать: откройте PDF вручную");
            } finally {
              // Blob-адрес освобождаем с задержкой, чтобы диалог печати успел
              // прочитать документ.
              setTimeout(() => {
                URL.revokeObjectURL(pdfUrl);
                frame.remove();
              }, 60_000);
            }
          };
          document.body?.appendChild(frame);
        } catch (e: unknown) {
          setPrintError(e instanceof Error ? e.message : "Не удалось открыть PDF для печати");
        }
      }
    } catch (e: unknown) {
      setPrintError(e instanceof Error ? e.message : "Ошибка печати");
    }
  }

  // Шаг, который может отметить ТЕКУЩИЙ пользователь: ожидает И can_act
  // (единственный источник истины от бэкенда, без эвристики по роли).
  const actStep = actStepHidden
    ? null
    : (card?.steps.find((s) => s.status === "ожидает" && s.can_act === true) ?? null);

  // Ключ карточки сотрудника (enterprise|base_code|tab_num): employee_key от
  // бэкенда (собран по локальному справочнику, только привилегированным),
  // иначе — как раньше, из полей самой заявки. Пусто — ФИО остаётся текстом.
  const employeeKey =
    card?.employee_key ||
    (card?.enterprise && card?.base_code && card?.tab_num
      ? `${card.enterprise}|${card.base_code}|${card.tab_num}`
      : "");

  // ФИО сотрудника: ссылка на карточку, когда key доступен (иначе текст).
  function employeeCell(fio: string) {
    if (employeeKey === "") return fio;
    const url = employeeUrl(employeeKey);
    return (
      <a
        href={url}
        onClick={(e) => {
          // Окно карточки сотрудника — по клику (иначе браузер блокирует popup).
          e.preventDefault();
          openPopup(url);
        }}
      >
        {fio}
      </a>
    );
  }

  // Исполнитель шага в таблице: при employee_key от бэкенда — ссылка на карточку
  // сотрудника (окно-попа), иначе прежний текст stepOwnerCell.
  function stepOwnerNode(step: RequestStep) {
    const label = stepOwnerCell(step);
    if (!step.employee_key) return label;
    const url = employeeUrl(step.employee_key);
    return (
      <a
        href={url}
        onClick={(e) => {
          // Окно карточки сотрудника — по клику (иначе браузер блокирует popup).
          e.preventDefault();
          openPopup(url);
        }}
      >
        {label}
      </a>
    );
  }

  // Групповой шаг — без персонального исполнителя (assignee пуст);
  // бэкенд может положить читаемое имя группы в owner_name, поэтому признак —
  // только assignee/resolver, а не наличие owner_name. by_user без assignee —
  // персональный без имени, должность прочерком.
  function isGroupStep(step: RequestStep): boolean {
    return !step.assignee && step.resolver !== "by_user";
  }

  // Колонка «Должность»: персональным — должность от бэкенда (из той же
  // AD-карточки, что ФИО), затем из стыковочной таблицы, иначе прочерк;
  // групповым — читабельное название группы из настроек, затем имя от
  // бэкенда, иначе код как раньше.
  function stepDutyCell(step: RequestStep): string {
    if (!isGroupStep(step)) return step.owner_duty ?? stepPositions[step.order] ?? "—";
    if (step.owner_group && groupNames[step.owner_group]) return groupNames[step.owner_group];
    if (step.owner_name) return step.owner_name;
    return step.owner_group || "—";
  }

  // Колонка «Исполнитель»: персональным — ФИО как раньше; групповым — всех
  // участников группы списком без сворачивания, при недоступности — группа.
  function stepExecutorsNode(step: RequestStep) {
    if (!isGroupStep(step)) return stepOwnerNode(step);
    const members: AdGroupMember[] =
      step.owner_group ? (groupMembers[step.owner_group] ?? []) : [];
    if (members.length === 0) return stepOwnerNode(step);
    return (
      <ul aria-label={`Участники группы ${step.owner_group}`} className="sed-list sed-memberlist">
        {members.map((m) => (
          <li key={m.sam}>
            {m.display_name}
            {m.title ? ` · ${m.title}` : ""}
          </li>
        ))}
      </ul>
    );
  }

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
    // Отказ: форму закрываем сразу, не дожидаясь ответа. После запроса карточка
    // приходит обновлённой — показываем то, что в ней есть (can_act бэкенда).
    setActStepHidden(decision === "reject");
    const ok = await runAction(
      () => decideStep(requestId, order, decision, text || undefined),
      "Отметка сохранена",
    );
    setActStepHidden(false);
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
  async function handleWithdraw(): Promise<void> {
    await runAction(() => withdrawRequest(requestId), "Заявка отозвана");
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
      setAttachmentDeleteError("");
    } catch (e: unknown) {
      setAttachmentsError(e instanceof Error ? e.message : "Ошибка загрузки вложений");
    }
  }

  // Удалять вложение могут автор (uploaded_by), admin и администратор СЭД.
  function canDeleteAttachment(att: AttachmentMeta): boolean {
    if (role === "admin" || (role as string) === "sed_admin") return true;
    return mySam !== "" && att.uploaded_by === mySam;
  }

  // Удаление вложения с подтверждением (действие необратимо).
  async function handleDeleteAttachment(att: AttachmentMeta): Promise<void> {
    setAttachmentDeleteError("");
    if (!window.confirm(`Удалить вложение ${att.file_name}? Действие необратимо.`)) return;
    try {
      await deleteAttachment(att.id);
      await refreshAttachments();
    } catch (err: unknown) {
      setAttachmentDeleteError(err instanceof Error ? err.message : "Ошибка удаления файла");
    }
  }

  async function handleUploadFile(e: ChangeEvent<HTMLInputElement>): Promise<void> {
    // Ссылку на input берём до await: после await target может быть недоступен.
    const input = e.target;
    const file = input?.files?.[0];
    if (!file) return;
    setUploadError("");
    try {
      await uploadAttachment(requestId, file);
      await refreshAttachments();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : "Ошибка загрузки файла");
    } finally {
      // Обнуляем input всегда (успех/ошибка), иначе повторный выбор того же
      // файла не даст onChange. Независимо от исхода исключение наружу не уходит.
      if (input) input.value = "";
    }
  }

  // Админ СЭД: сохранение правки Тема/Содержание/Вид (PATCH /requests/{id}).
  async function handleSedSave(): Promise<void> {
    if (!card) return;
    setSedError("");
    setSedStatus("");
    const patch: { subject?: string; content?: string; doc_type_code?: string } = {};
    if (sedSubject !== (card.subject ?? "")) patch.subject = sedSubject;
    if (sedContent !== (card.content ?? "")) patch.content = sedContent;
    if (sedDocType !== (card.doc_type_code ?? "")) patch.doc_type_code = sedDocType;
    if (Object.keys(patch).length === 0) return;
    try {
      await updateRequest(requestId, patch);
      setSedStatus("Изменения сохранены");
      await refreshRequest();
    } catch (e: unknown) {
      setSedError(e instanceof Error ? e.message : "Ошибка сохранения");
    }
  }

  // Админ СЭД: откат заявки к выбранному шагу (POST /requests/{id}/rollback).
  async function handleRollback(): Promise<void> {
    if (rollbackStep === "") return;
    setSedError("");
    setSedStatus("");
    try {
      await rollbackRequest(requestId, Number(rollbackStep));
      setSedStatus(`Заявка откачена к шагу ${rollbackStep}`);
      await refreshRequest();
    } catch (e: unknown) {
      setSedError(e instanceof Error ? e.message : "Ошибка отката");
    }
  }

  // Добавление комментария к заявке (POST /requests/{id}/comments).
  async function handleAddComment(): Promise<void> {
    const body = newComment.trim();
    if (body === "") return;
    setCommentError("");
    try {
      const created = await addComment(requestId, body);
      setComments([...comments, created]);
      setNewComment("");
    } catch (e: unknown) {
      setCommentError(e instanceof Error ? e.message : "Ошибка добавления комментария");
    }
  }

  return (
    <section aria-label="Карточка заявки">
      <h3>Карточка заявки {requestId}</h3>
      {/* Вкладки образца: лист рассмотрения либо история (только вид). */}
      <nav className="sed-tabs sed-tabs--inner" aria-label="Вкладки карточки">
        <button
          type="button"
          className={cardTab === "sheet" ? "sed-tab sed-tab--active" : "sed-tab"}
          onClick={() => setCardTab("sheet")}
        >
          Лист рассмотрения
        </button>
        <button
          type="button"
          className={cardTab === "history" ? "sed-tab sed-tab--active" : "sed-tab"}
          onClick={() => setCardTab("history")}
        >
          История
        </button>
      </nav>
      {cardError && <div role="alert">{cardError}</div>}
      {!card && !cardError && !deleted && <div className="sed-note">Загрузка карточки…</div>}
      {/* Заявка удалена в этой же вкладке: закрыть окно нельзя — возврат к списку. */}
      {deleted && (
          <div>
            <div role="status">Заявка удалена</div>
            <div className="sed-toolbar sed-mt-8">
            <a className="sed-btn" href="?">
              К списку заявок
            </a>
          </div>
        </div>
      )}
      {card && !deleted && cardTab === "sheet" && (
        <>
          {/* Этап/Статус — пояснением (.sed-note), данные заявки — данными (.sed-meta). */}
          <div className="sed-note">
            Этап: карточка заявки · Статус: {card.status}
          </div>
          {/* Две панели образца (doc.html): слева — тема/содержание и вложения,
              справа — инициатор (read-only) и решение по шагу. */}
          <div className="sed-panels">
            <div className="sed-panel">
              <b>Документ</b>
              <div className="sed-block">
                Тема: <strong>{card.subject ?? "—"}</strong>
              </div>
              <div className="sed-block">
                Содержание: <strong>{card.content ?? "—"}</strong>
              </div>
              {/* Скан-вложения: список мета + загрузка файла. */}
              <section aria-label="Вложения" className="sed-mt-8">
                <b>Вложения</b>
                {attachmentsError && <div role="alert">{attachmentsError}</div>}
                {uploadError && <div role="alert">{uploadError}</div>}
                {attachmentDeleteError && <div role="alert">{attachmentDeleteError}</div>}
                {attachments.length === 0 && !attachmentsError && (
                  <div className="sed-note">Вложений нет</div>
                )}
                <ul className="sed-list">
                  {attachments.map((att) => {
                    // Окно просмотра — по клику (иначе браузер блокирует popup).
                    const url = attachmentUrl(att.id, att.file_name, att.mime);
                    return (
                      <li key={att.id}>
                        {/* Фолбэки обязательны: одна битая запись не должна ронять
                            всю карточку (раньше .slice по undefined давал пустой экран). */}
                        {att.file_name ?? "Файл"} · {att.size_bytes ?? "—"} Б ·{" "}
                        {(att.uploaded_at ?? "").slice(0, 10) || "—"} ·{" "}
                        <a
                          href={url}
                          onClick={(e) => {
                            e.preventDefault();
                            openPopup(url, 1000, 800);
                          }}
                        >
                          Открыть
                        </a>
                        {canDeleteAttachment(att) && (
                          <>
                            {" "}
                            <button
                              type="button"
                              className="sed-btn sed-btn--danger"
                              onClick={() => handleDeleteAttachment(att)}
                            >
                              Удалить
                            </button>
                          </>
                        )}
                      </li>
                    );
                  })}
                </ul>
                <label className="sed-field">
                  Загрузить скан
                  <input type="file" aria-label="Файл скана" onChange={handleUploadFile} />
                </label>
              </section>
            </div>
            <div className="sed-panel">
              <b>Инициатор и решение</b>
              {/* ПДн: владельцу fio/tab_num не приходят — маска «Сотрудник № id».
                  Класс .sed-meta — метаданные (контракт теста), .sed-block — отступ. */}
              <div className="sed-block sed-meta">
                {role === "owner" ? (
                  <>Сотрудник № {card.id}</>
                ) : (
                  <>
                    {employeeCell(card.fio ?? `Сотрудник № ${card.id}`)}
                    {card.tab_num ? ` · Таб.№ ${card.tab_num}` : ""} · {card.department} · {card.position}
                    {card.enterprise ? ` · ${card.enterprise_name ?? card.enterprise}` : ""}
                  </>
                )}
              </div>
              {/* Отметка своего шага — строго по can_act от бэкенда. */}
              {actStep && (
                <div aria-label="Решение владельца" className="sed-mt-8">
                  <h4>Моё решение · Шаг {stepLabel(actStep.order)}</h4>
                  <label className="sed-field">
                    Комментарий к решению
                    <textarea
                      aria-label="Комментарий к решению"
                      rows={4}
                      placeholder="Комментарий (обязателен при отказе/возврате)"
                      value={decisionComment}
                      onChange={(e) => setDecisionComment(e.target.value)}
                    />
                  </label>
                  {decisionError && <div role="alert">{decisionError}</div>}
                  <div className="sed-toolbar sed-mt-8">
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
            </div>
          </div>

          {/* Печать бегунка (ОК/админ) — в карточке, не в списке. */}
          {role !== "owner" && (
            <div className="sed-toolbar sed-mt-8">
              <button type="button" className="sed-btn" onClick={handlePrint}>
                Печать
              </button>
              {printStatus && <span role="status">{printStatus}</span>}
              {printPdf && (
                <a href={printPdf} target="_blank" rel="noopener noreferrer">
                  Открыть PDF
                </a>
              )}
              {printError && <span role="alert">{printError}</span>}
            </div>
          )}

          {/* Рассмотрение (низ образца): ВСЕ шаги одной сеткой-таблицей —
              Вид рассмотрения | Должность | Сотрудник | Статус | Срок |
              Комментарий. Вид пишется один раз на весь блок (rowSpan по строкам
              блока, как в образце): код order — блок*1000 + режим + позиция
              (см. stepLabel), order<1000 — общий последовательный блок.
              Должность — читаемое наименование группы из справочника настроек,
              Сотрудник — весь состав группы (для персональных — ФИО).
              Логин AD (assignee) не выводится. */}
          <div className="sed-review">
            <b>Рассмотрение</b>
            {(() => {
              // Блоки в порядке шагов: номер и режим из кода order.
              const blocks: { blockNo: number; parallel: boolean; steps: RequestStep[] }[] = [];
              card.steps.forEach((step) => {
                const coded = step.order >= 1000;
                const blockNo = coded ? Math.floor(step.order / 1000) + 1 : 1;
                const parallel = coded && Math.floor((step.order % 1000) / 100) === 1;
                const last = blocks[blocks.length - 1];
                if (last && last.blockNo === blockNo) {
                  last.steps.push(step);
                } else {
                  blocks.push({ blockNo, parallel, steps: [step] });
                }
              });
              return (
                <table className="sed-table sed-table--review" aria-label="Шаги заявки">
                  <thead>
                    <tr>
                      <th>№</th>
                      <th>Вид рассмотрения</th>
                      <th>Должность</th>
                      <th>Сотрудник</th>
                      <th>Статус</th>
                      <th>Срок</th>
                      <th>Комментарий</th>
                    </tr>
                  </thead>
                  <tbody>
                    {blocks.map((block) =>
                      block.steps.map((step, si) => (
                        <tr key={step.order}>
                          <td>{stepLabel(step.order)}</td>
                          {si === 0 && (
                            <td rowSpan={block.steps.length}>
                              {block.parallel ? "Параллельно" : "Последовательно"}
                            </td>
                          )}
                          <td>{stepDutyCell(step)}</td>
                          <td>{stepExecutorsNode(step)}</td>
                          <td>{step.status}</td>
                          <td>{step.expires_at.slice(0, 10)}</td>
                          <td>{step.comment ?? "—"}</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              );
            })()}
          </div>

          {/* Действия ОК/админа по статусу заявки. */}
          {role !== "owner" && (
            <div className="sed-toolbar sed-mt-8" aria-label="Действия по заявке">
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
              {/* Отзыв (POST /requests/{id}/withdraw, ОК/админ): бэкенд отдаёт 409
                  на закрытых статусах — кнопку там не показываем. */}
              {WITHDRAW_STATUSES.includes(card.status) && (
                <button
                  type="button"
                  className="sed-btn sed-btn--danger"
                  onClick={handleWithdraw}
                >
                  Отозвать заявку
                </button>
              )}
              {role === "admin" && (
                <button
                  type="button"
                  className="sed-btn sed-btn--danger"
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
        </>
      )}
      {card && !deleted && cardTab === "history" && (
        <>
          {/* История заявки: кто / когда / действие / детали (audit_log). */}
          <section aria-label="История">
            <h4>История</h4>
            {historyError && <div className="sed-note">История недоступна: {historyError}</div>}
            {history.length === 0 && !historyError && (
              <div className="sed-note">Записей истории нет</div>
            )}
            {history.length > 0 && (
              <ul>
                {history.map((item, i) => (
                  <li key={i}>
                    <strong>{item.actor}</strong> · {historyWhen(item.at)} · {item.action}
                    {item.details && <div className="sed-sub">{JSON.stringify(item.details)}</div>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Комментарии к заявке: список + поле добавления. */}
          <section aria-label="Комментарии">
            <h4>Комментарии</h4>
            {commentsError && <div className="sed-note">Комментарии недоступны: {commentsError}</div>}
            {comments.length === 0 && !commentsError && (
              <div className="sed-note">Комментариев нет</div>
            )}
            {comments.length > 0 && (
              <ul>
                {comments.map((c) => (
                  <li key={c.id}>
                    <strong>{c.author}</strong> · {c.at.slice(0, 16).replace("T", " ")}: {c.body}
                  </li>
                ))}
              </ul>
            )}
            <input
              aria-label="Новый комментарий"
              placeholder="Комментарий к заявке"
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
            />
            <button
              type="button"
              className="sed-btn"
              onClick={handleAddComment}
              disabled={newComment.trim() === ""}
            >
              Добавить комментарий
            </button>
            {commentError && <div role="alert">{commentError}</div>}
          </section>
        </>
      )}
      {card && !deleted && (
        <>
          {/* Панель администратора СЭД (роль sed_admin): правка Тема/Содержание/Вид
              и откат к шагу. ПДн в макете не встраиваются — только поля карточки. */}
          {isSedAdmin && (
            <section aria-label="Панель администратора СЭД">
              <h4>Администратор СЭД</h4>
              <label className="sed-field">
                Тема
                <input
                  aria-label="Тема (админ СЭД)"
                  value={sedSubject}
                  onChange={(e) => setSedSubject(e.target.value)}
                />
              </label>
              <label className="sed-field">
                Содержание
                <textarea
                  aria-label="Содержание (админ СЭД)"
                  rows={3}
                  value={sedContent}
                  onChange={(e) => setSedContent(e.target.value)}
                />
              </label>
              <label className="sed-field">
                Вид документа
                <select
                  aria-label="Вид документа (админ СЭД)"
                  value={sedDocType}
                  onChange={(e) => setSedDocType(e.target.value)}
                >
                  <option value="">— не выбран —</option>
                  {sedDocTypes.map((dt) => (
                    <option key={dt.code} value={dt.code}>
                      {dt.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="sed-toolbar sed-mt-8">
                <button type="button" className="sed-btn" onClick={handleSedSave}>
                  Сохранить
                </button>
              </div>
              <label className="sed-field">
                Откатить к шагу
                <select
                  aria-label="Откатить к шагу"
                  value={rollbackStep}
                  onChange={(e) => setRollbackStep(e.target.value)}
                >
                  <option value="">— выберите шаг —</option>
                  {card.steps.map((s) => (
                    <option key={s.order} value={String(s.order)}>
                      {stepLabel(s.order)} · {stepOwnerCell(s)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="sed-toolbar sed-mt-8">
                <button
                  type="button"
                  className="sed-btn"
                  onClick={handleRollback}
                  disabled={rollbackStep === ""}
                >
                  Откатить
                </button>
              </div>
              {sedStatus && <div role="status">{sedStatus}</div>}
              {sedError && <div role="alert">{sedError}</div>}
            </section>
          )}
        </>
      )}
    </section>
  );
}

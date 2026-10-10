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
  replaceRequestSteps,
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
  RouteStepSpec,
  StepDecision,
  StepGroup,
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

// Статусы, в которых маршрут ещё можно править (PATCH /requests/{id}/steps).
// Только Черновик и «На доработке»: на «На согласовании» правка сняла бы
// текущий ожидающий шаг из-под исполнителя, который с ним работает, а на
// «Согласовано»/«К исполнению» добавленные шаги уже никто не пройдёт.
const ROUTE_EDIT_STATUSES = ["Черновик", "На доработке"];

// Шаг черновика маршрута в редакторе. kind:
//   group — группа AD (owner_group), user — персональный исполнитель (sam),
//   fixed — несколько ответственных: StepSpec умеет одного, такой шаг править
//   нельзя (см. routeBlocksPayload).
interface RouteStepDraft {
  key: string;
  kind: "group" | "user" | "fixed";
  value: string;
  // Резолвер шага (RESOLVERS в api/app/requests.py) — переносится в PATCH как
  // есть: без него шаг «руководитель сотрудника» стал бы групповым без
  // ответственных, и отметить его было бы некому (can_act вечно false).
  resolver: string;
  // Флаг require_comment шага: без переноса правка маршрута молча отменяла бы
  // требование комментария, заданное бланком/этапом.
  requireComment: boolean;
  // Представление неизменяемого шага (логины его ответственных).
  label: string;
}

// Блок черновика маршрута: последовательный либо параллельный.
interface RouteBlockDraft {
  key: string;
  mode: "sequential" | "parallel";
  steps: RouteStepDraft[];
}

// Роль, которой API разрешает оперировать заявкой (_require_hr в
// api/app/requests.py): hr/hr_admin/admin. У владельца шага и у администратора
// СЭД (_require_hr их не пропускает) такие кнопки давали бы 403, поэтому гейт
// по роли, а не «не владелец».
function canOperateRequest(role: Role | string): boolean {
  return role === "hr" || role === "hr_admin" || role === "admin";
}

// Шаги, по которым заявка ушла инициатору на доработку (отказ/возврат). Их
// наличие — признак, что заявка ждёт решения инициатора: статус заявки
// «Отклонено» в системе не используется (отказ переводит её в «На доработке»).
function REWORKED_STEPS(steps: RequestStep[]): RequestStep[] {
  return steps.filter((s) => s.status === "отклонен" || s.status === "возвращен");
}

// Порядковый номер ключа черновика (React-ключи строк маршрута).
let routeDraftSeq = 0;

function nextRouteKey(): string {
  routeDraftSeq += 1;
  return `rd-${routeDraftSeq}`;
}

// Черновик маршрута из ожидающих шагов заявки. Закрытые шаги («согласован»,
// «отклонён», «возвращён», «просрочен») в правку не входят: бэкенд сохраняет их
// сам и пересобирает маршрут только из ожидающих — иначе согласованные шаги
// задваились бы. Блок и режим восстановлены из кода order (block*1000 + 100
// для параллельного, см. _block_info в api/app/requests.py).
//
// knownGroups — коды групп из справочника (GET /api/step-groups). Шаг-группа с
// кодом вне справочника (этап-реестр, удалённая группа) не выражается через
// owner_group: такой шаг помечается fixed и правку блокирует, иначе в PATCH
// ушёл бы мёртвый код и шаг не отметил бы никто.
function routeDraftFromSteps(steps: RequestStep[], knownGroups: string[]): RouteBlockDraft[] {
  const blocks: RouteBlockDraft[] = [];
  const byKey = new Map<number, RouteBlockDraft>();
  for (const step of steps) {
    if (step.status !== "ожидает") continue;
    const blockIndex = Math.floor(step.order / 1000);
    const mode = Math.floor((step.order % 1000) / 100) === 1 ? "parallel" : "sequential";
    let block = byKey.get(blockIndex);
    if (!block) {
      block = { key: nextRouteKey(), mode, steps: [] };
      byKey.set(blockIndex, block);
      blocks.push(block);
    }
    block.mode = mode;
    const assignees = step.assignees ?? [];
    const login = step.assignee ?? assignees[0] ?? "";
    const groupStep = step.resolver !== "by_user" && login === "";
    // Больше одного ответственного (шаг бланка people, миграция 0013) в StepSpec
    // не помещается, и группа вне справочника не выражается как owner_group —
    // оба случая шаг неизменяемый: иначе правка молча отняла бы у маршрута
    // согласующих либо оставила бы шаг без исполнителя.
    const kind: RouteStepDraft["kind"] =
      assignees.length > 1 || (groupStep && !knownGroups.includes(step.owner_group))
        ? "fixed"
        : groupStep
          ? "group"
          : "user";
    block.steps.push({
      key: nextRouteKey(),
      kind,
      value: kind === "group" ? step.owner_group : login || step.owner_group,
      resolver: step.resolver || "by_group",
      requireComment: step.require_comment === true,
      label:
        assignees.length === 0 && kind === "fixed"
          ? `${step.owner_group} (группы нет в справочнике)`
          : assignees.length > 0
            ? assignees.join(", ")
            : step.owner_group,
    });
  }
  return blocks;
}

// Тело PATCH /steps из черновика. Второе значение — список шагов, которые
// отправить нельзя: правка блокируется понятным сообщением, молча выкидывать
// согласующих или ронять шаг с забытым исполнителем нельзя.
function routeBlocksPayload(blocks: RouteBlockDraft[]): {
  blocks: Array<{ mode: "sequential" | "parallel"; steps: RouteStepSpec[] }>;
  blocked: string[];
} {
  const blocked: string[] = [];
  const payload = blocks.map((block) => ({
    mode: block.mode,
    steps: block.steps.flatMap((step) => {
      if (step.kind === "fixed") {
        blocked.push(step.label);
        return [];
      }
      // Пустой логин/группа — шаг выпал бы из маршрута молча, а ответственным
      // за шагом после этого не остался бы никто.
      if (step.value.trim() === "") {
        blocked.push("шаг без исполнителя");
        return [];
      }
      return [
        step.kind === "user"
          ? { sam: step.value.trim(), require_comment: step.requireComment }
          : {
              owner_group: step.value.trim(),
              resolver: step.resolver,
              require_comment: step.requireComment,
            },
      ];
    }),
  }));
  return { blocks: payload.filter((block) => block.steps.length > 0), blocked };
}

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
  // Запрос отметки в полёте: на это время кнопки решения выключены, форма
  // остаётся на месте — иначе после отказа поле комментария исчезало бы вместе
  // с ней и дописать комментарий к заявке было негде.
  const [deciding, setDeciding] = useState<boolean>(false);
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
  // ФИО своей учётной записи — подпись своей отметки в шаге с несколькими
  // ответственными (бэкенд отдаёт в отметках только логины).
  const [myFio, setMyFio] = useState<string>("");
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
  // Список групп-владельцев для выбора в редакторе маршрута.
  const [stepGroups, setStepGroups] = useState<StepGroup[]>([]);
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
  // Правка маршрута заявки (PATCH /requests/{id}/steps): редактор ожидающих
  // шагов. Открывается кнопкой «Скорректировать маршрут» и подставляет текущий
  // маршрут, чтобы сохранение не выкинуло шаги молча.
  const [routeOpen, setRouteOpen] = useState<boolean>(false);
  const [routeBlocks, setRouteBlocks] = useState<RouteBlockDraft[]>([]);
  const [routeReason, setRouteReason] = useState<string>("");
  // Логины AD, набранные для добавления шага-исполнителя (по блоку, черновик).
  const [routeLogins, setRouteLogins] = useState<Record<string, string>>({});
  const [routeBusy, setRouteBusy] = useState<boolean>(false);
  const [routeError, setRouteError] = useState<string>("");

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
        if (!alive) return;
        setMySam(user.sam);
        setMyFio(user.fio ?? "");
      })
      .catch(() => {
        if (!alive) return;
        setMySam("");
        setMyFio("");
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

  // Справочник групп-владельцев (settings step-groups): список для выбора группы
  // в редакторе маршрута и id → читабельное название для таблицы шагов;
  // недоступность — fallback на код группы в таблице.
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
        setStepGroups(items);
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
  // в «Сотруднике» остаётся название группы вместо людей. Тот же состав —
  // источник ФИО по логинам ответственных (assignees) у шага с несколькими
  // ответственными, если владелец шага — группа (owner_group не логин).
  useEffect(() => {
    if (!card) return;
    let alive = true;
    const groups = new Set<string>();
    card.steps.forEach((s) => {
      const group = s.owner_group;
      if (!group) return;
      if (!s.assignee && s.resolver !== "by_user") {
        groups.add(group);
        return;
      }
      const assignees = s.assignees ?? [];
      if (isMultiAssigneeStep(s) && !assignees.includes(group)) groups.add(group);
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
  const actStep = card?.steps.find((s) => s.status === "ожидает" && s.can_act === true) ?? null;

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
  // бэкенда, иначе код как раньше. Неразрывные пробелы из 1С/AD нормализуем:
  // браузер по ним не переносит, и длинная должность вылезает из колонки.
  function stepDutyCell(step: RequestStep): string {
    let text: string;
    if (!isGroupStep(step)) {
      text = step.owner_duty ?? stepPositions[step.order] ?? "—";
    } else if (step.owner_group && groupNames[step.owner_group]) {
      text = groupNames[step.owner_group];
    } else if (step.owner_name) {
      text = step.owner_name;
    } else {
      text = step.owner_group || "—";
    }
    return text.replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g, " ");
  }

  // Шаг с несколькими ответственными (миграция 0013). Признак и счётчик —
  // от бэкенда (assignee_count считается по полному снимку): непривилегированному
  // в assignees остаётся только собственный логин, поэтому по длине массива
  // режим шага не определяем.
  function stepAssigneeCount(step: RequestStep): number {
    return step.assignee_count ?? step.assignees?.length ?? 0;
  }
  function isMultiAssigneeStep(step: RequestStep): boolean {
    return stepAssigneeCount(step) > 1;
  }

  // ФИО ответственного по логину: бэкенд отдаёт логины, а ФИО каждого — там,
  // где оно в карточке уже есть: у первого ответственного (assignee →
  // owner_name), у участников групп AD (тот же состав, что у группового шага)
  // и у самого пользователя (/auth/me). Нет ФИО — нейтральная подпись:
  // логины чужих ответственных в UI не выводятся (бэкенд их и не отдаёт).
  function assigneeFio(step: RequestStep, sam: string | null): string | null {
    if (!sam) return null;
    if (step.assignee === sam && step.owner_name) return step.owner_name;
    if (mySam !== "" && sam === mySam && myFio !== "") return myFio;
    const groups = Object.keys(groupMembers);
    for (const group of groups) {
      const hit = (groupMembers[group] ?? []).find((m) => m.sam === sam);
      if (hit?.display_name) return hit.display_name;
    }
    return null;
  }

  // Подпись отметки без ФИО: логин в UI не выводится.
  function assigneeLabel(step: RequestStep, sam: string | null): string {
    return assigneeFio(step, sam) ?? "Ответственный";
  }

  // Режим шага словами (для малознакового approval_mode и его отсутствия):
  // parallel — согласование любым из ответственных, sequential — отметки всех.
  function stepModeLabel(step: RequestStep): string {
    return step.approval_mode === "parallel"
      ? "согласование любым из ответственных"
      : "отметки всех ответственных";
  }

  // Решение из отметки — по значениям контракта decide_step; неизвестное
  // показываем как пришло.
  function approvalDecisionLabel(decision: string): string {
    if (decision === "approve") return "согласовал";
    if (decision === "reject") return "отказал";
    if (decision === "return") return "вернул";
    return decision;
  }

  // Колонка «Сотрудник» шага с несколькими ответственными: ФИО по логинам
  // assignees, прогресс «N из M согласовали» и режим шага. Отметки ответственных
  // (последовательный шаг) — в колонке «Комментарий» (см. stepApprovalsNode).
  function stepAssigneesNode(step: RequestStep) {
    const assignees = step.assignees ?? [];
    const total = stepAssigneeCount(step);
    const done = step.approved_count ?? 0;
    const names = assignees.map((sam) => assigneeLabel(step, sam)).join(", ");
    return (
      <>
        <div className="sed-note">Ответственные: {names}</div>
        <div className="sed-note">
          {done} из {total} согласовали
        </div>
        <div className="sed-note">{stepModeLabel(step)}</div>
      </>
    );
  }

  // Отметки ответственных по шагу: кто, когда, решение, комментарий. Чужие
  // отметки приходят с sam=null — решение и комментарий видны, логин нет.
  function stepApprovalsNode(step: RequestStep) {
    const approvals = step.approvals ?? [];
    if (approvals.length === 0) return null;
    return (
      <ul aria-label={`Отметки шага ${stepLabel(step.order)}`} className="sed-list">
        {approvals.map((mark, i) => (
          <li key={`${mark.sam ?? "mark"}-${i}`}>
            <strong>{assigneeLabel(step, mark.sam)}</strong> · {historyWhen(mark.at)} ·{" "}
            {approvalDecisionLabel(mark.decision)}
            {mark.comment ? ` — ${mark.comment}` : ""}
          </li>
        ))}
      </ul>
    );
  }

  // Колонка «Сотрудник»: персональным — ФИО как раньше; шагу с несколькими
  // ответственными — их список и прогресс; групповым — только
  // ФИО участников списком без сворачивания (должность — в колонке
  // «Должность», у участников её не дублируем); при недоступности — группа.
  function stepExecutorsNode(step: RequestStep) {
    if (isMultiAssigneeStep(step)) return stepAssigneesNode(step);
    if (!isGroupStep(step)) return stepOwnerNode(step);
    const members: AdGroupMember[] =
      step.owner_group ? (groupMembers[step.owner_group] ?? []) : [];
    if (members.length === 0) return stepOwnerNode(step);
    return (
      <ul aria-label={`Участники группы ${step.owner_group}`} className="sed-list sed-memberlist">
        {members.map((m) => (
          <li key={m.sam}>{m.display_name}</li>
        ))}
      </ul>
    );
  }

  async function refreshRequest(): Promise<RequestOut | null> {
    try {
      const data = await getRequest(requestId);
      setCard(data);
      setCardError("");
      return data;
    } catch (e: unknown) {
      setCardError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
      return null;
    }
  }

  // Действие с перезагрузкой карточки: возвращает обновлённую карточку (null —
  // ошибка запроса), чтобы вызывающий увидел состояние шагов после действия.
  async function runAction(
    action: () => Promise<unknown>,
    okMessage: string,
  ): Promise<RequestOut | null> {
    setCardActionError("");
    setCardActionStatus("");
    try {
      await action();
      setCardActionStatus(okMessage);
      return await refreshRequest();
    } catch (e: unknown) {
      setCardActionError(e instanceof Error ? e.message : "Действие не выполнено");
      return null;
    }
  }

  // Завершающее действие в окне-попе: оповестить список (обновит счётчики
  // папок в основном окне) и закрыть окно для скорости работы. Встроенная
  // карточка (без opener) — только оповещение, window.close() там не работает.
  function closeIfPopup(): void {
    notifyRequestsChanged();
    if (window.opener) window.close();
  }

  async function handleDecision(order: number, decision: StepDecision): Promise<void> {
    if (deciding) return;
    const text = decisionComment.trim();
    // Комментарий обязателен при отказе/возврате всегда, при согласии — когда
    // шаг помечен require_comment (та же проверка на сервере, _check_comment).
    const step = card?.steps.find((s) => s.order === order);
    const commentRequired = decision !== "approve" || step?.require_comment === true;
    if (commentRequired && !text) {
      setDecisionError(
        step?.require_comment === true && decision === "approve"
          ? "Шаг требует комментарий и при согласовании"
          : "При отказе/возврате комментарий обязателен",
      );
      return;
    }
    setDecisionError("");
    setDeciding(true);
    const updated = await runAction(
      () => decideStep(requestId, order, decision, text || undefined),
      "Отметка сохранена",
    );
    setDeciding(false);
    if (!updated) return;
    setDecisionComment("");
    // Шаг с несколькими ответственными после моей отметки остаётся «На
    // согласовании» (закрывает его любой ответственный — параллельный, либо
    // все — последовательный): окно не закрываем, показываем, что учтено и
    // ждём остальных. Повторную отметку бэкенд не примет (409), поэтому
    // после перезагрузки can_act у шага уже false и кнопок нет.
    const decided = updated.steps.find((s) => s.order === order);
    if (decision === "approve" && decided && isMultiAssigneeStep(decided) && decided.status === "ожидает") {
      setCardActionStatus(
        "Ваша отметка учтена, ожидаются отметки остальных ответственных",
      );
      return;
    }
    // Согласование завершает работу с карточкой — закрываем окно-попу.
    if (decision === "approve") closeIfPopup();
  }

  async function handleSubmit(): Promise<void> {
    await runAction(() => submitRequest(requestId), "Заявка отправлена на согласование");
  }
  async function handleToExecution(): Promise<void> {
    if (await runAction(() => toExecution(requestId), "Заявка отправлена к исполнению")) {
      closeIfPopup();
    }
  }
  async function handleFinish(): Promise<void> {
    if (await runAction(() => finishRequest(requestId), "Заявка завершена")) {
      closeIfPopup();
    }
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

  // Открыть редактор маршрута: подставляем текущие ожидающие шаги, чтобы
  // сохранение сохранило маршрут, а не обнулил его.
  function openRouteEditor(): void {
    if (!card) return;
    setRouteBlocks(routeDraftFromSteps(card.steps, stepGroups.map((g) => g.id)));
    setRouteReason("");
    setRouteError("");
    setRouteOpen(true);
  }

  // Правка блока маршрута: режим, шаги, добавление/удаление и порядок.
  function patchRouteBlock(key: string, patch: Partial<RouteBlockDraft>): void {
    setRouteBlocks((prev) => prev.map((b) => (b.key === key ? { ...b, ...patch } : b)));
  }

  function addRouteBlock(mode: "sequential" | "parallel"): void {
    setRouteBlocks((prev) => [...prev, { key: nextRouteKey(), mode, steps: [] }]);
  }

  function removeRouteBlock(key: string): void {
    const block = routeBlocks.find((b) => b.key === key);
    const fixed = block?.steps.filter((s) => s.kind === "fixed") ?? [];
    // Удаление блока с неизменяемым шагом — тоже потеря согласующих, поэтому
    // спрашиваем явно, а не роняем молча (PATCH пересобирает маршрут целиком).
    if (
      fixed.length > 0 &&
      !window.confirm(
        `В блоке есть шаг с несколькими ответственными (${fixed.map((s) => s.label).join(", ")}). ` +
          "Удаление уберёт его из маршрута вместе с ними. Удалить блок?",
      )
    ) {
      return;
    }
    setRouteBlocks((prev) => prev.filter((b) => b.key !== key));
  }

  function addRouteStep(blockKey: string, kind: "group" | "user", value: string): void {
    const trimmed = value.trim();
    if (trimmed === "") return;
    setRouteBlocks((prev) =>
      prev.map((b) =>
        b.key === blockKey
          ? {
              ...b,
              steps: [
                ...b.steps,
                {
                  key: nextRouteKey(),
                  kind,
                  value: trimmed,
                  resolver: kind === "group" ? "by_group" : "by_user",
                  requireComment: false,
                  label: trimmed,
                },
              ],
            }
          : b,
      ),
    );
  }

  function removeRouteStep(blockKey: string, stepKey: string): void {
    setRouteBlocks((prev) =>
      prev.map((b) =>
        b.key === blockKey ? { ...b, steps: b.steps.filter((s) => s.key !== stepKey) } : b,
      ),
    );
  }

  // Порядок шага: кнопки «вверх/вниз» (доступнее перетаскивания), как в
  // редакторе состава бланка (admin-settings.tsx, moveStep).
  function moveRouteStep(blockKey: string, stepKey: string, delta: number): void {
    setRouteBlocks((prev) =>
      prev.map((b) => {
        if (b.key !== blockKey) return b;
        const index = b.steps.findIndex((s) => s.key === stepKey);
        const target = index + delta;
        if (index < 0 || target < 0 || target >= b.steps.length) return b;
        const steps = [...b.steps];
        const [row] = steps.splice(index, 1);
        steps.splice(target, 0, row);
        return { ...b, steps };
      }),
    );
  }

  function patchRouteStep(blockKey: string, stepKey: string, patch: Partial<RouteStepDraft>): void {
    setRouteBlocks((prev) =>
      prev.map((b) =>
        b.key === blockKey
          ? { ...b, steps: b.steps.map((s) => (s.key === stepKey ? { ...s, ...patch } : s)) }
          : b,
      ),
    );
  }

  // Сохранить маршрут: PATCH /requests/{id}/steps, карточка перечитывается.
  async function handleSaveRoute(): Promise<void> {
    if (!card || routeBusy) return;
    setRouteError("");
    const { blocks, blocked } = routeBlocksPayload(routeBlocks);
    if (blocked.length > 0) {
      setRouteError(
        `Правка недоступна: ${blocked.join(", ")}. Такой шаг нельзя пересобрать — ` +
          "удалите его (или блок) либо правьте состав в справочнике бланков.",
      );
      return;
    }
    if (blocks.length === 0) {
      setRouteError("Маршрут пуст: добавьте блок с исполнителем");
      return;
    }
    setRouteBusy(true);
    try {
      await replaceRequestSteps(requestId, blocks, routeReason);
      setRouteOpen(false);
      // Ответ редактор закрывает, поэтому подтверждение показываем в общей
      // строке статуса карточки — иначе пользователь его не увидит.
      setCardActionStatus("Маршрут сохранён");
      await refreshRequest();
    } catch (e: unknown) {
      setRouteError(e instanceof Error ? e.message : "Ошибка сохранения маршрута");
    } finally {
      setRouteBusy(false);
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
                  {/* Шаг с несколькими ответственными: режим и прогресс отметок —
                      после моей отметки шаг остаётся «На согласовании». */}
                  {isMultiAssigneeStep(actStep) && (
                    <div className="sed-note">
                      Ответственные: {stepAssigneeCount(actStep)} · {stepModeLabel(actStep)} ·{" "}
                      {actStep.approved_count ?? 0} из {stepAssigneeCount(actStep)} согласовали
                    </div>
                  )}
                  <label className="sed-field">
                    Комментарий к решению
                    <textarea
                      aria-label="Комментарий к решению"
                      aria-required={actStep.require_comment === true}
                      rows={4}
                      required={actStep.require_comment === true}
                      placeholder={
                        actStep.require_comment === true
                          ? "Комментарий обязателен (в том числе при согласовании)"
                          : "Комментарий (обязателен при отказе/возврате)"
                      }
                      value={decisionComment}
                      onChange={(e) => setDecisionComment(e.target.value)}
                    />
                  </label>
                  {decisionError && <div role="alert">{decisionError}</div>}
                  <div className="sed-toolbar sed-mt-8">
                    <button
                      type="button"
                      className="sed-btn"
                      disabled={deciding}
                      onClick={() => handleDecision(actStep.order, "approve")}
                    >
                      Согласовать
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      disabled={deciding}
                      onClick={() => handleDecision(actStep.order, "reject")}
                    >
                      Отказать
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      disabled={deciding}
                      onClick={() => handleDecision(actStep.order, "return")}
                    >
                      Вернуть
                    </button>
                  </div>
                </div>
              )}
              {/* Комментарий к заявке доступен сразу на листе рассмотрения:
                  после отказа/возврата форма решения скрывается по can_act
                  бэкенда, и без этого поля дописать заявке комментарий было
                  негде (раньше он был только на вкладке «История»). */}
              <section aria-label="Комментарий к заявке" className="sed-mt-8">
                <label className="sed-field">
                  Комментарий к заявке
                  <input
                    aria-label="Текст комментария"
                    placeholder="Комментарий к заявке"
                    value={newComment}
                    onChange={(e) => setNewComment(e.target.value)}
                  />
                </label>
                <div className="sed-toolbar sed-mt-8">
                  <button
                    type="button"
                    className="sed-btn"
                    onClick={handleAddComment}
                    disabled={newComment.trim() === ""}
                  >
                    Добавить комментарий
                  </button>
                  {commentError && <span role="alert">{commentError}</span>}
                </div>
              </section>
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
              Комментарий. Колонки «№» нет по требованию владельца.
              Вид пишется один раз на весь блок (rowSpan по строкам
              блока, как в образце): код order — блок*1000 + режим + позиция,
              order<1000 — общий последовательный блок.
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
                  {/* Ширины — через colgroup (классы колонок), а не nth-child:
                      в строках 2+ нет ячейки с rowSpan, номера td в строке
                      съезжают и nth-child давит не те колонки. */}
                  <colgroup>
                    <col className="sed-review__col-kind" />
                    <col className="sed-review__col-duty" />
                    <col className="sed-review__col-members" />
                    <col className="sed-review__col-status" />
                    <col className="sed-review__col-deadline" />
                    <col className="sed-review__col-comment" />
                  </colgroup>
                  <thead>
                    <tr>
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
                          {si === 0 && (
                            <td rowSpan={block.steps.length} className="sed-review__kind">
                              {block.parallel ? "Параллельно" : "Последовательно"}
                            </td>
                          )}
                          <td>{stepDutyCell(step)}</td>
                          <td>{stepExecutorsNode(step)}</td>
                          <td>{step.status}</td>
                          <td>{step.expires_at.slice(0, 10)}</td>
                          {/* Отметки ответственных (шаг с несколькими
                              ответственными) — под комментарием шага. */}
                          <td>
                            {step.comment ?? "—"}
                            {stepApprovalsNode(step)}
                          </td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              );
            })()}
          </div>

          {/* Действия ОК/админа по статусу заявки (роль — как у _require_hr). */}
          {canOperateRequest(role) && (
            <div className="sed-toolbar sed-mt-8" aria-label="Действия по заявке">
              {(card.status === "Черновик" ||
                (card.status === "На доработке" && REWORKED_STEPS(card.steps).length === 0)) && (
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
              {/* Правка маршрута — только у Черновика и на доработке: на «На
                  согласовании» она сняла бы текущий ожидающий шаг из-под
                  исполнителя (см. ROUTE_EDIT_STATUSES). */}
              {ROUTE_EDIT_STATUSES.includes(card.status) && (
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={openRouteEditor}
                >
                  Скорректировать маршрут
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

          {/* Отклонённая/возвращённая заявка ждёт инициатора: «Повторить» — увести
              по маршруту дальше (POST /submit), «Перенаправить» — скорректировать
              шаги (PATCH /requests/{id}/steps). Статус заявки «Отклонено» в
              системе не используется: отказ по шагу переводит заявку в
              «На доработке», поэтому признак — закрытые с отказом/возвратом
              шаги, а не статус. Блок только в «На доработке»: в прочих статусах
              POST /submit отдал бы 409. */}
          {canOperateRequest(role) &&
            card.status === "На доработке" &&
            REWORKED_STEPS(card.steps).length > 0 && (
            <div className="sed-toolbar sed-mt-8" aria-label="Действия по доработке">
              <div className="sed-note">
                Заявка возвращена на доработку: шаги{" "}
                {REWORKED_STEPS(card.steps).map((s) => stepLabel(s.order)).join(", ")}. Согласованные
                шаги сохраняются, маршрут можно скорректировать.
              </div>
              <button type="button" className="sed-btn" onClick={handleSubmit}>
                Повторить
              </button>
              <button
                type="button"
                className="sed-btn sed-btn--ghost"
                onClick={openRouteEditor}
              >
                Перенаправить
              </button>
            </div>
          )}

          {/* Правка маршрута заявки (PATCH /requests/{id}/steps). Показывается по
              кнопке «Скорректировать маршрут»: редактор меняет только ожидающие
              шаги, закрытые (согласованные/отклонённые/возвращённые/просроченные)
              бэкенд сохраняет сам. */}
          {canOperateRequest(role) && routeOpen && ROUTE_EDIT_STATUSES.includes(card.status) && (
            <section aria-label="Правка маршрута" className="sed-mt-8">
              <h4>Маршрут заявки</h4>
              <div className="sed-toolbar">
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => addRouteBlock("sequential")}
                >
                  Добавить последовательный блок
                </button>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => addRouteBlock("parallel")}
                >
                  Добавить параллельный блок
                </button>
              </div>
              {routeBlocks.length === 0 && (
                <div className="sed-note">Ожидающих шагов нет — добавьте блок с исполнителем.</div>
              )}
              {routeBlocks.map((block, bi) => (
                <div key={block.key} className="sed-editor-card sed-mt-8">
                  <div className="sed-blockcard__head">
                    <b className="sed-blockcard__title">Блок {bi + 1}</b>
                    <span className="sed-blockcard__type">
                      {block.mode === "parallel" ? "Параллельно" : "Последовательно"}
                    </span>
                    <span className="sed-blockcard__spacer" />
                    <select
                      aria-label={`Режим блока ${bi + 1}`}
                      value={block.mode}
                      onChange={(e) =>
                        patchRouteBlock(block.key, {
                          mode: e.target.value === "parallel" ? "parallel" : "sequential",
                        })
                      }
                    >
                      <option value="sequential">Последовательно</option>
                      <option value="parallel">Параллельно</option>
                    </select>
                    <button
                      type="button"
                      className="sed-btn sed-btn--danger"
                      aria-label={`Удалить блок ${bi + 1}`}
                      onClick={() => removeRouteBlock(block.key)}
                    >
                      Удалить блок
                    </button>
                  </div>
                  {block.steps.length === 0 && (
                    <div className="sed-note">Шагов нет.</div>
                  )}
                  {block.steps.map((step, si) => (
                    <div className="sed-editor-row sed-mt-8" key={step.key}>
                      <span className="sed-meta">
                        {bi + 1}.{si + 1}
                      </span>
                      {step.kind === "fixed" ? (
                        <span className="sed-note">
                          Ответственные: {step.label}. Шаг с несколькими согласующими
                          меняется только в справочнике бланков.
                        </span>
                      ) : step.kind === "user" ? (
                        <label className="sed-field">
                          Логин AD
                          <input
                            aria-label={`Логин исполнителя ${bi + 1}.${si + 1}`}
                            value={step.value}
                            onChange={(e) =>
                              patchRouteStep(block.key, step.key, { value: e.target.value })
                            }
                          />
                        </label>
                      ) : (
                        <label className="sed-field">
                          Группа
                          <select
                            aria-label={`Группа блока ${bi + 1}, шаг ${si + 1}`}
                            value={step.value}
                            onChange={(e) =>
                              patchRouteStep(block.key, step.key, { value: e.target.value })
                            }
                          >
                            <option value="">— выберите группу —</option>
                            {stepGroups.map((g) => (
                              <option key={g.id} value={g.id}>
                                {g.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                      {/* Неизменяемый шаг (несколько ответственных) править и
                          удалять нельзя: PATCH пересобирает маршрут целиком и
                          StepSpec одного ответственного не выразит, поэтому
                          сохранение с таким шагом блокируется. */}
                      {step.kind !== "fixed" && (
                        <>
                          <button
                            type="button"
                            className="sed-btn sed-btn--ghost"
                            aria-label={`Поднять шаг ${bi + 1}.${si + 1}`}
                            disabled={si === 0}
                            onClick={() => moveRouteStep(block.key, step.key, -1)}
                          >
                            Вверх
                          </button>
                          <button
                            type="button"
                            className="sed-btn sed-btn--ghost"
                            aria-label={`Опустить шаг ${bi + 1}.${si + 1}`}
                            disabled={si === block.steps.length - 1}
                            onClick={() => moveRouteStep(block.key, step.key, 1)}
                          >
                            Вниз
                          </button>
                          <button
                            type="button"
                            className="sed-btn sed-btn--danger"
                            aria-label={`Удалить шаг ${bi + 1}.${si + 1}`}
                            onClick={() => removeRouteStep(block.key, step.key)}
                          >
                            Удалить
                          </button>
                        </>
                      )}
                    </div>
                  ))}
                  <div className="sed-toolbar sed-mt-8">
                    <select
                      aria-label={`Группа для блока ${bi + 1}`}
                      value=""
                      onChange={(e) => {
                        addRouteStep(block.key, "group", e.target.value);
                        e.target.value = "";
                      }}
                    >
                      <option value="">— добавить шаг-группу —</option>
                      {stepGroups.map((g) => (
                        <option key={g.id} value={g.id}>
                          {g.name}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label={`Логин исполнителя для блока ${bi + 1}`}
                      placeholder="Логин AD"
                      value={routeLogins[block.key] ?? ""}
                      onChange={(e) =>
                        setRouteLogins((prev) => ({ ...prev, [block.key]: e.target.value }))
                      }
                    />
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Добавить шаг-исполнителя ${bi + 1}`}
                      onClick={() => {
                        addRouteStep(block.key, "user", routeLogins[block.key] ?? "");
                        setRouteLogins((prev) => ({ ...prev, [block.key]: "" }));
                      }}
                    >
                      Добавить шаг-исполнителя
                    </button>
                  </div>
                </div>
              ))}
              <label className="sed-field sed-mt-8">
                Причина правки
                <input
                  aria-label="Причина правки маршрута"
                  value={routeReason}
                  onChange={(e) => setRouteReason(e.target.value)}
                />
              </label>
              <div className="sed-toolbar sed-mt-8">
                <button type="button" className="sed-btn" onClick={handleSaveRoute} disabled={routeBusy}>
                  Сохранить маршрут
                </button>
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => setRouteOpen(false)}
                >
                  Отмена
                </button>
              </div>
              {routeError && <div role="alert">{routeError}</div>}
            </section>
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

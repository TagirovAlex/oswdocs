// Форма создания заявки для ОК (Задача 3.3): единая форма без стадий.
// Блоки появляются/активируются по зависимостям: предприятие → сотрудник →
// маршрут → «Создать»; невалидное — недоступно (кнопка «Создать» disabled).
// Предприятия — только из API (settings БД), хардкода нет (AGENTS.md п.3).
// Сотрудник: живой поиск в 1С (GET /api/employees); без баз (503) — ручной ввод.
// Маршрут: по профилю службы сотрудника (POST /api/requests/route/preview,
// route_mode=auto) либо ручной конструктор блоков с исполнителями из AD
// (route_mode=custom; телом создания идут blocks, не группы steps).
import { useEffect, useRef, useState } from "react";
import { ApiHttpError, me } from "./auth-client";
import {
  linkEmployee,
  createRequest,
  getAdGroupMembers,
  getDocTypes,
  getEmployeeCard,
  getEnterprises,
  getMyLinks,
  getRouteBlanks,
  getRoutingCatalogs,
  getStepGroups,
  previewRoute,
  searchAd,
  searchEmployees,
  submitRequest,
} from "./requests-client";
import type {
  AdCandidate,
  AdGroupMember,
  CreateRequestBody,
  DocType,
  EmployeeHit,
  Enterprise,
  MyLink,
  RouteBlank,
  RouteMode,
  RoutePreview,
  RoutePreviewBlank,
  RoutingCatalogStage,
  StepGroup,
} from "./requests-client";
import type { Role } from "./api-mock";
import { employeeUrl, openPopup } from "./windows";

interface CreateFormProps {
  // Роль (создание — только ОК/админам, гард как в API _is_hr).
  role: Role;
  // Колбэк «грязности» формы: true после первого ввода, false после успешного
  // создания (форма сброшена) — для подтверждения закрытия окна (create-window).
  onDirtyChange?: (dirty: boolean) => void;
  // В окне-попе (?view=create): после успешного создания закрыть окно.
  closeOnCreate?: boolean;
  // Закрытие окна из нижнего тулбара формы (кнопка «Закрыть» в одну строку
  // с Создать/Отмена): обработчик с подтверждением при dirty — из окна.
  onClose?: () => void;
}

// Тип исполнителя шага: конкретный сотрудник AD либо группа-владелец.
type ExecutorKind = "user" | "group";

// Шаг маршрута в конструкторе: сотрудник (sam — контракт API, остальное —
// справочно) либо группа (owner_group + резолвер by_group).
interface RouteStep {
  kind: ExecutorKind;
  sam: string;
  display_name: string;
  owner_group?: string;
  resolver: string;
}

// Блок маршрута: последовательный либо параллельный шаги одного типа исполнителя.
// id стабилен (генерируется при добавлении): индекс блока «съезжает» при удалении,
// а выбранная группа и её состав привязаны к блоку, а не к его позиции.
interface RouteBlock {
  id: string;
  mode: "sequential" | "parallel";
  kind: ExecutorKind;
  steps: RouteStep[];
}

// Состав выбранной группы для одного блока маршрута (счётчик/раскрытие/ошибка AD).
interface GroupMembersState {
  members: AdGroupMember[];
  loading: boolean;
  error: string;
  open: boolean;
}

const EMPTY_GROUP_MEMBERS: GroupMembersState = {
  members: [],
  loading: false,
  error: "",
  open: false,
};

// Размер страницы живого поиска сотрудника в форме создания: совпадает с
// дефолтом сервера (page_size /api/employees); пагинация — по total ответа.
const EMP_SEARCH_PAGE_SIZE = 50;

// Причины подбора маршрута (reason из предпросмотра) — тексты по-русски.
// Базовый признак отделён от пометок («+»), их не показываем.
const ROUTE_REASON_TEXT: Record<string, string> = {
  service_profile: "профиль службы",
  default_profile: "профиль по умолчанию",
  profile_not_found: "профиль не найден",
  service_not_registered: "служба не заведена",
};

// Причины, при которых маршрут НЕ подобрался (профиля нет): показываем
// предупреждение, а не тихо пустой список этапов.
const ROUTE_REASON_MISSED = ["profile_not_found", "service_not_registered"];

// Текст причины подбора из reason предпросмотра («+»-пометки отбрасываем).
function routeReasonText(reason: string): string {
  const base = reason.split("+")[0] ?? "";
  return ROUTE_REASON_TEXT[base] ?? base;
}

// Источник исполнителя этапа (owner_kind) — текстом по-русски.
const OWNER_KIND_TEXT: Record<string, string> = {
  manager_ad: "руководитель из AD",
  ad_group: "группа AD",
  stage_roster: "состав этапа",
  // Шаг бланка со своим списком согласующих (миграция 0014).
  people: "согласующие",
};

// Состояние блока без указанного ключа: удалённый блок не должен оставлять в
// состоянии формы свою группу и её состав.
function omitKey<T>(state: Record<string, T>, key: string): Record<string, T> {
  if (!(key in state)) return state;
  const next = { ...state };
  delete next[key];
  return next;
}

// Номера страниц пейджера для перехода: всегда 1, пять вокруг текущей
// (current-2..current+2) и пять с конца (total-4..total); «…» — разрыв.
// При малом числе страниц (<=10) — все подряд без разрывов.
function pagerPages(current: number, total: number): Array<number | "…"> {
  if (total <= 1) return [1];
  const set = new Set<number>();
  if (total <= 10) {
    for (let i = 1; i <= total; i++) set.add(i);
  } else {
    set.add(1);
    for (let i = current - 2; i <= current + 2; i++) {
      if (i >= 1 && i <= total) set.add(i);
    }
    for (let i = Math.max(1, total - 4); i <= total; i++) set.add(i);
  }
  const sorted = [...set].sort((a, b) => a - b);
  const out: Array<number | "…"> = [];
  let prev = 0;
  for (const page of sorted) {
    if (prev !== 0 && page - prev > 1) out.push("…");
    out.push(page);
    prev = page;
  }
  return out;
}

// Маленькие графические иконки формы (крест очистки/удаления, плюс
// добавления): инлайн-SVG вместо текстовых глифов, под стиль .ibtn макета.
function CrossIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

// Форма создания: единый экран, блоки по зависимостям.
export function CreateForm(props: CreateFormProps) {
  const { role, onDirtyChange, closeOnCreate, onClose } = props;
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [enterprise, setEnterprise] = useState<string>("");
  const [loadError, setLoadError] = useState<string>("");
  const [created, setCreated] = useState<string>("");
  const [createError, setCreateError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  // Вид документа (селект из GET /api/doc-types?active_only=true), по умолчанию
  // — первый активный; недоступность справочника форму не ломает.
  const [docTypes, setDocTypes] = useState<DocType[]>([]);
  const [docTypesError, setDocTypesError] = useState<string>("");
  const [docTypeCode, setDocTypeCode] = useState<string>("");
  // Бланк из справочника (GET /api/requests/route/blanks): выбирает ОК, его
  // шаги становятся маршрутом заявки. Пусто — маршрут подбирается по службе,
  // если это разрешено настройкой blank_autopick (autopick в ответе).
  const [blanks, setBlanks] = useState<RouteBlank[]>([]);
  const [blanksError, setBlanksError] = useState<string>("");
  const [blankId, setBlankId] = useState<string>("");
  // Тема и содержание заявки (обязательные поля макета) и комментарий (правая панель).
  const [subject, setSubject] = useState<string>("");
  const [content, setContent] = useState<string>("");
  const [comment, setComment] = useState<string>("");

  // Сотрудник: живой поиск в 1С (200 — список) либо ручной ввод (503 — базы не настроены).
  const [empQuery, setEmpQuery] = useState<string>("");
  const [empHits, setEmpHits] = useState<EmployeeHit[]>([]);
  const [empListOpen, setEmpListOpen] = useState<boolean>(false);
  const [empSearching, setEmpSearching] = useState<boolean>(false);
  // Пагинация поиска: empTotal — всего совпадений (из ответа; нет — по длине
  // выдачи), empPage — текущая страница (новая строка запроса сбрасывает на 1).
  const [empTotal, setEmpTotal] = useState<number>(0);
  const [empPage, setEmpPage] = useState<number>(1);
  // Сотрудник уже выбран из списка (клик по кандидату): поле показывает ФИО,
  // повторный поиск по empQuery не запускается (иначе список открывался бы заново).
  const [empPicked, setEmpPicked] = useState<boolean>(false);
  // Составной ключ выбранного сотрудника (enterprise|base_code|tab_num):
  // ссылка «карточка сотрудника» (окно ?view=employee) после выбора.
  const [empKey, setEmpKey] = useState<string>("");
  const [manualMode, setManualMode] = useState<boolean>(false);
  const [manualNote, setManualNote] = useState<string>("");
  const [fio, setFio] = useState<string>("");
  const [tabNum, setTabNum] = useState<string>("");
  const [department, setDepartment] = useState<string>("");
  const [position, setPosition] = useState<string>("");
  // Порядковый номер поиска сотрудника: устаревшие ответы отбрасываем.
  const searchSeq = useRef(0);

  // Маршрут по профилю (auto, по умолчанию) либо ручной конструктор (custom).
  const [routeMode, setRouteMode] = useState<RouteMode>("auto");
  // Предпросмотр маршрута: профиль/служба/этапы + текст ошибки (422/503).
  const [preview, setPreview] = useState<RoutePreview | null>(null);
  // Подтверждение связи 1С↔AD прямо в форме: после успеха перезапрашиваем
  // предпросмотр (маршрут соберётся по карточке AD).
  const [linking, setLinking] = useState(false);
  const [linkError, setLinkError] = useState("");
  const [previewError, setPreviewError] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState<boolean>(false);
  // Правки маршрута ОК: снятые и добавленные этапы (коды) — уходят и в
  // предпросмотр, и в создание.
  const [dismissedStages, setDismissedStages] = useState<string[]>([]);
  // Номера шагов бланка (step_order), снятые ОК: у шага бланка нет кода этапа,
  // поэтому его снимают по номеру (dismissed_step_orders), а не по коду.
  const [dismissedStepOrders, setDismissedStepOrders] = useState<number[]>([]);
  const [addedStages, setAddedStages] = useState<string[]>([]);
  // Справочник этапов для «Добавить этап» (админский GET /settings/routing/
  // catalogs). Не-админу 403 — добавление необязательно, кнопки нет.
  const [catalogStages, setCatalogStages] = useState<RoutingCatalogStage[] | null>(null);
  // Раскрыт ли список доступных этапов (кнопка «Добавить этап»).
  const [addStageOpen, setAddStageOpen] = useState<boolean>(false);
  // Замена руководителя, выбранная ОК вручную (этап manager_ad): логин AD и ФИО
  // для показа. Пусто — доверяем руководителю из AD (manager_dn сотрудника).
  const [managerSam, setManagerSam] = useState<string>("");
  const [managerName, setManagerName] = useState<string>("");
  // Раскрыт ли подбор руководителя (чекбокс «выбрать вручную») и состояние поиска.
  const [managerPickOpen, setManagerPickOpen] = useState<boolean>(false);
  const [managerQuery, setManagerQuery] = useState<string>("");
  const [managerHits, setManagerHits] = useState<AdCandidate[]>([]);
  const [managerSearching, setManagerSearching] = useState<boolean>(false);
  const [managerError, setManagerError] = useState<string>("");
  // Порядковый номер поиска руководителя: устаревший ответ по прежнему запросу
  // не должен затирать результаты текущего (как adSeq для панели AD).
  const managerSeq = useRef(0);
  // Порядковый номер предпросмотра: устаревший ответ по прежнему сотруднику
  // не должен затирать текущий.
  const previewSeq = useRef(0);
  // Код базы 1С и логин AD выбранного сотрудника (часть ключа hit.key) —
  // источник карточки для подбора маршрута.
  const [baseCode, setBaseCode] = useState<string>("");
  const [adSam, setAdSam] = useState<string>("");

  // Конструктор маршрута: блоки (последовательный/параллельный) и панель AD.
  const [blocks, setBlocks] = useState<RouteBlock[]>([]);
  // Счётчик идентификаторов блоков (index нестабилен — сдвигается при удалении).
  const blockSeq = useRef(0);
  // Черновик, созданный неудачной попыткой «Отправить на согласование»:
  // {id, signature данных}. Повторная отправка переиспользует его только при
  // неизменных данных; при правках создаётся новый черновик (без потери правок).
  const pendingDraft = useRef<{ id: string; signature: string } | null>(null);
  // id блока, для которого открыта панель выбора исполнителя (null — закрыта).
  const [adPanelBlock, setAdPanelBlock] = useState<string | null>(null);
  const [adQuery, setAdQuery] = useState<string>("");
  const [adCandidates, setAdCandidates] = useState<AdCandidate[]>([]);
  const [adSearching, setAdSearching] = useState<boolean>(false);
  const [adSearchError, setAdSearchError] = useState<string>("");
  // Отмеченные чекбоксами кандидаты модалки (сохраняются между поисками
  // в пределах одной открытой модалки, сбрасываются при открытии/закрытии).
  const [adSelected, setAdSelected] = useState<AdCandidate[]>([]);
  // Порядковый номер поиска AD: устаревшие ответы отбрасываем.
  const adSeq = useRef(0);

  // Тип исполнителя «Группа»: группы из settings (GET /api/step-groups, {id,name})
  // и состав выбранной группы из AD (GET /api/ad/groups/{group}/members).
  const [groups, setGroups] = useState<StepGroup[]>([]);
  const [groupsError, setGroupsError] = useState<string>("");
  // Выбранная группа по id блока.
  const [groupPick, setGroupPick] = useState<Record<string, string>>({});
  // Состав группы хранится ПО id БЛОКА: у каждого группового блока своя
  // выбранная группа, общее состояние показывало бы состав последней выбранной
  // группы во всех блоках сразу.
  const [groupMembers, setGroupMembers] = useState<Record<string, GroupMembersState>>({});
  // Порядковый номер загрузки состава по блокам: устаревшие ответы отбрасываем
  // (как adSeq), иначе медленный ответ по прежней группе перезапишет состав текущей.
  const groupSeq = useRef<Record<string, number>>({});

  // Флаг «грязности» формы: true после первого ввода пользователя — для
  // подтверждения закрытия окна (create-window). Сбрасывается после создания.
  const [touched, setTouched] = useState<boolean>(false);

  // Пометить форму изменённой: onDirtyChange(true) только при первом действии.
  function markTouched(): void {
    if (touched) return;
    setTouched(true);
    onDirtyChange?.(true);
  }

  // Инициатор для правой панели — из сессии (GET /auth/me), только чтение.
  // ФИО у владельца может отсутствовать (урезанная карточка) — тогда логин.
  const [initiator, setInitiator] = useState<string>("");
  // Свои связки АД-1С (GET /link_1c_ad/mine) для ссылки инициатора.
  // Связки — свои данные, как ФИО в me(). Ключ выбирается так: ровно одна
  // связка — она; несколько — та, что на выбранное в форме предприятие
  // (молчаливый выбор предприятия недопустим); иначе — текста без ссылки.
  const [initiatorLinks, setInitiatorLinks] = useState<MyLink[]>([]);
  useEffect(() => {
    let alive = true;
    me()
      .then((user) => {
        if (alive) setInitiator(user.fio ?? user.sam);
        return getMyLinks();
      })
      .then((links) => {
        if (alive) setInitiatorLinks(links);
      })
      .catch(() => {
        // Сессия/связка недоступна — поле инициатора остаётся пустым («—»).
        if (alive) {
          setInitiator("");
          setInitiatorLinks([]);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  // Ключ карточки инициатора: одна связка — она; из нескольких — место
  // текущей работы (is_current, правило задачи K), иначе совпадение
  // с выбранным в форме предприятием; иначе — текста без ссылки.
  // Пересчитывается при смене предприятия в форме.
  function initiatorKeyFor(links: MyLink[], selectedEnterprise: string): string {
    if (links.length === 1) return links[0].key;
    const current = links.filter((l) => l.is_current === true);
    if (current.length === 1) return current[0].key;
    if (selectedEnterprise === "") return "";
    const matched = links.filter((l) => l.enterprise === selectedEnterprise);
    return matched.length === 1 ? matched[0].key : "";
  }
  const initiatorKey = initiatorKeyFor(initiatorLinks, enterprise);

  // Предприятия — только из API.
  useEffect(() => {
    let alive = true;
    getEnterprises()
      .then((ents) => {
        if (alive) setEnterprises(ents);
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(e instanceof Error ? e.message : "Ошибка загрузки данных формы");
      });
    return () => {
      alive = false;
    };
  }, []);

  // Виды документов — из GET /api/doc-types (только активные), по умолчанию первый.
  // Ошибка загрузки — примечание, форма продолжает работать (doc_type_code не уходит).
  useEffect(() => {
    let alive = true;
    getDocTypes(true)
      .then((items) => {
        if (!alive) return;
        setDocTypes(items);
        setDocTypesError("");
        if (items.length > 0) setDocTypeCode(items[0].code);
      })
      .catch((e: unknown) => {
        if (alive) {
          setDocTypesError(e instanceof Error ? e.message : "Ошибка загрузки видов документов");
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  // Доступные бланки для выбора ОК (GET /api/requests/route/blanks): только
  // активные бланки справочника, без ПДн. Ошибка загрузки — примечанием, форма
  // продолжает работать (бланк не уходит, маршрут подбирается по службе).
  useEffect(() => {
    let alive = true;
    getRouteBlanks()
      .then((items) => {
        if (alive) {
          setBlanks(items);
          setBlanksError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) setBlanksError(e instanceof Error ? e.message : "Ошибка загрузки бланков");
      });
    return () => {
      alive = false;
    };
  }, []);

  // Группы-владельцы шагов — из settings (GET /api/step-groups), без хардкода.
  // Недоступность — понятный текст, форма продолжает работать.
  useEffect(() => {
    let alive = true;
    getStepGroups()
      .then((items) => {
        if (alive) {
          setGroups(items);
          setGroupsError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) setGroupsError(e instanceof Error ? e.message : "Ошибка загрузки групп");
      });
    return () => {
      alive = false;
    };
  }, []);

  // Справочник этапов маршрута для добавления (только админ; 403 у остальных —
  // кнопка «Добавить этап» не показывается, деградация без ошибки).
  useEffect(() => {
    let alive = true;
    getRoutingCatalogs()
      .then((catalogs) => {
        if (alive) setCatalogStages((catalogs.stages ?? []).filter((s) => s.active));
      })
      .catch(() => {
        // Справочник недоступен (403/503) — добавление этапов недоступно.
        if (alive) setCatalogStages(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Предпросмотр маршрута по профилю службы: как только известны предприятие и
  // табельный номер (или выбран сотрудник). Перезапрашивается при смене
  // сотрудника и при правке снятых/добавленных этапов. Устаревшие ответы
  // отбрасываем по previewSeq. 422/503 — текст ошибки, создание в custom
  // остаётся доступным (пользователь может переключиться вручную).
  useEffect(() => {
    if (routeMode !== "auto" || enterprise === "" || tabNum.trim() === "") {
      previewSeq.current++;
      setPreview(null);
      setPreviewError("");
      setPreviewLoading(false);
      return;
    }
    const seq = ++previewSeq.current;
    setPreviewLoading(true);
    setPreviewError("");
    previewRoute({
      enterprise,
      tab_num: tabNum.trim(),
      ...(baseCode !== "" ? { base_code: baseCode } : {}),
      ...(adSam !== "" ? { ad_sam: adSam } : {}),
      ...(department !== "" ? { department } : {}),
      ...(position !== "" ? { position } : {}),
      dismissed_stages: dismissedStages,
      dismissed_step_orders: dismissedStepOrders,
      added_stages: addedStages,
      ...(managerSam !== "" ? { manager: managerSam } : {}),
      ...(blankId !== "" ? { blank_id: Number(blankId) } : {}),
    })
      .then((data) => {
        if (previewSeq.current !== seq) return;
        setPreview(data);
        setPreviewLoading(false);
      })
      .catch((e: unknown) => {
        if (previewSeq.current !== seq) return;
        setPreview(null);
        setPreviewLoading(false);
        setPreviewError(e instanceof Error ? e.message : "Ошибка предпросмотра маршрута");
      });
  }, [
    routeMode,
    enterprise,
    tabNum,
    baseCode,
    adSam,
    department,
    position,
    dismissedStages,
    dismissedStepOrders,
    addedStages,
    managerSam,
    blankId,
  ]);

// Подтвердить связь 1С↔AD для выбранного сотрудника: кандидат в AD один (иначе
// бэкенд вернул бы 422 и выбор делает человек). После связи подставляем sam —
// предпросмотр сам перезапустится по зависимостям и соберёт маршрут.
const confirmLink = async () => {
  if (!enterprise || tabNum.trim() === "") return;
  setLinking(true);
  setLinkError("");
  try {
    const result = await linkEmployee({
      enterprise,
      tab_num: tabNum.trim(),
      ...(baseCode !== "" ? { base_code: baseCode } : {}),
      ...(fio.trim() !== "" ? { fio: fio.trim() } : {}),
    });
    setAdSam(result.sam);
  } catch (e: unknown) {
    setLinkError(e instanceof Error ? e.message : "Не удалось подтвердить связь");
  } finally {
    setLinking(false);
  }
};

  // Состав группы из AD: счётчик + раскрываемый список (ФИО/почта). Состав хранится
  // по id блока. Ошибка или недоступность AD — текст, форма не падает.
  function patchGroupMembers(blockId: string, patch: Partial<GroupMembersState>): void {
    setGroupMembers((prev) => ({
      ...prev,
      [blockId]: { ...EMPTY_GROUP_MEMBERS, ...prev[blockId], ...patch },
    }));
  }

  // Загрузка состава группы: номер запроса на блок (groupSeq) — устаревший ответ
  // по прежней группе не перезаписывает состав текущей (как adSeq для поиска AD).
  function loadGroupMembers(blockId: string, group: string): void {
    const seq = (groupSeq.current[blockId] ?? 0) + 1;
    groupSeq.current[blockId] = seq;
    if (group === "") {
      patchGroupMembers(blockId, { members: [], error: "", loading: false, open: false });
      return;
    }
    patchGroupMembers(blockId, { members: [], error: "", loading: true, open: false });
    getAdGroupMembers(group)
      .then((items) => {
        if (groupSeq.current[blockId] !== seq) return;
        patchGroupMembers(blockId, { members: items, loading: false });
      })
      .catch((e: unknown) => {
        if (groupSeq.current[blockId] !== seq) return;
        patchGroupMembers(blockId, {
          loading: false,
          error: e instanceof Error ? e.message : "Не удалось получить состав группы",
        });
      });
  }

  // Живой поиск сотрудника: ввод → debounce 150 мс → searchEmployees; старые
  // ответы отбрасываем по searchSeq. 503 или пустой результат — ручной ввод.
  // При уже выбранном сотруднике (empPicked) поиск не запускаем: поле держит
  // ФИО, иначе после клика по кандидату список открывался бы заново.
  useEffect(() => {
    if (empPicked) {
      searchSeq.current++;
      setEmpHits([]);
      setEmpListOpen(false);
      setEmpSearching(false);
      return;
    }
    const q = empQuery.trim();
    if (q === "") {
      searchSeq.current++;
      setEmpHits([]);
      setEmpListOpen(false);
      setManualMode(false);
      setManualNote("");
      setEmpSearching(false);
      return;
    }
    const seq = ++searchSeq.current;
    setEmpSearching(true);
    const timer = setTimeout(() => {
      searchEmployees(enterprise, q, empPage, EMP_SEARCH_PAGE_SIZE)
        .then((data) => {
          if (seq !== searchSeq.current) return;
          const hits = data.items ?? [];
          setEmpHits(hits);
          // total от серверной пагинации; нет (прежний ответ) — по длине выдачи.
          const total = typeof data.total === "number" ? data.total : hits.length;
          setEmpTotal(total);
          if (hits.length === 0 && total === 0) {
            setManualMode(true);
            setManualNote("ничего не найдено — введите данные вручную");
            setEmpListOpen(false);
          } else {
            setManualMode(false);
            setManualNote("");
            setEmpListOpen(true);
          }
          setEmpSearching(false);
        })
        .catch((e: unknown) => {
          if (seq !== searchSeq.current) return;
          setEmpHits([]);
          setEmpListOpen(false);
          setEmpSearching(false);
          if (e instanceof ApiHttpError && e.status === 503) {
            setManualMode(true);
            setManualNote("данные 1С не настроены, введите вручную");
          } else {
            setManualNote(e instanceof Error ? e.message : "Ошибка поиска сотрудника");
          }
        });
    }, 150);
    return () => clearTimeout(timer);
  }, [empQuery, enterprise, empPicked, empPage]);

  // Живой поиск в AD для панели конструктора: debounce 150 мс → searchAd.
  useEffect(() => {
    const q = adQuery.trim();
    if (q === "" || adPanelBlock === null) {
      adSeq.current++;
      setAdCandidates([]);
      setAdSearchError("");
      setAdSearching(false);
      return;
    }
    const seq = ++adSeq.current;
    setAdSearching(true);
    const timer = setTimeout(() => {
      searchAd(q)
        .then((items) => {
          if (seq !== adSeq.current) return;
          setAdCandidates(items);
          setAdSearchError(items.length === 0 ? "Ничего не найдено в AD" : "");
          setAdSearching(false);
        })
        .catch((e: unknown) => {
          if (seq !== adSeq.current) return;
          setAdCandidates([]);
          setAdSearchError(e instanceof Error ? e.message : "Ошибка поиска в AD");
          setAdSearching(false);
        });
    }, 150);
    return () => clearTimeout(timer);
  }, [adQuery, adPanelBlock]);

  // Создание — ОК, руководителям ОК и админам (роль hr/hr_admin/admin, как в API _is_hr).
  if (role !== "hr" && role !== "hr_admin" && role !== "admin") {
    return <div role="alert">Создание заявок доступно только ОК.</div>;
  }

  // Готовность формы: предприятие → сотрудник → тема/содержание → маршрут.
  // Подразделение/должность в 1С могут быть пустыми (уволен/нет кадровых
  // данных) — для создания они необязательны.
  const empTotalPages = empTotal > 0 ? Math.max(1, Math.ceil(empTotal / EMP_SEARCH_PAGE_SIZE)) : 0;
  const employeeReady = fio.trim() !== "" && tabNum.trim() !== "";
  // Выбранный бланк: подпись селекта — название и число шагов, пояснение бланка
  // (description) идёт подсказкой под селектом.
  const selectedBlank = blanks.find((b) => String(b.id) === blankId) ?? null;
  // Автоподстановка бланка по службе выключена (настройка blank_autopick) — без
  // выбора бланка маршрут собран не будет: предупреждаем до создания.
  const blankAutopick = blanks.length > 0 ? blanks[0].autopick : true;
  // Снимок выбранного бланка в предпросмотре (blank) — по подбору по службе
  // приходит прежнее значение (вид бланка печати, строка office/line).
  const previewBlank: RoutePreviewBlank | null =
    preview && typeof preview.blank === "object" ? preview.blank : null;
  // Готовность маршрута: в auto — этапы подобраны (blocks не отправляются),
  // в custom — заполнены блоки конструктора, как раньше.
  // Этап «Руководитель» (manager_ad) из предпросмотра: кого нашли в AD и почему
  // этап не закрывается, если не нашли.
  const managerStage =
    preview?.stages.find((s) => s.owner_kind === "manager_ad") ?? null;
  // Панель подбора открыта, если её открыл пользователь или этап заблокирован
  // (тогда замену нужно подобрать сразу).
  const managerPanelOpen = managerPickOpen || managerStage?.blocked_reason != null;
  const routeReady =
    routeMode === "auto"
      ? preview !== null && preview.stages.length > 0
      : blocks.length > 0 && blocks.every((b) => b.steps.length > 0);
  const canCreate =
    enterprise !== "" &&
    employeeReady &&
    subject.trim() !== "" &&
    content.trim() !== "" &&
    routeReady &&
    !busy;

  // Выбор сотрудника из списка 1С заполняет справочные поля заявки.
  // Подразделение/должность в списке справочника пустые (они в карточке —
  // второй запрос к регистру кадровых данных): догружаем карточкой.
  function pickEmployee(hit: EmployeeHit): void {
    markTouched();
    setFio(hit.fio);
    setTabNum(hit.tab_num);
    setDepartment("");
    setPosition("");
    setEmpQuery(hit.fio);
    setEmpHits([]);
    setEmpListOpen(false);
    setEmpPicked(true);
    // Ключ карточки сотрудника: enterprise|base_code|tab_num (hit.key).
    setEmpKey(hit.key);
    // Код базы 1С и логин AD — источник карточки для подбора маршрута.
    const parts = hit.key.split("|");
    setBaseCode(parts.length >= 2 ? parts[1] : "");
    setAdSam(hit.ad_sam ?? "");
    setDismissedStages([]);
    // Сотрудник сменился — номера снятых шагов прежнего бланка не подходят.
    setDismissedStepOrders([]);
    setAddedStages([]);
    // Новый сотрудник — прежняя замена руководителя не подходит.
    resetManagerPick();
    if (parts.length < 3) {
      return; // битый ключ — подразделение/должность останутся пустыми («—»)
    }
    void getEmployeeCard(enterprise, parts[1], hit.tab_num)
      .then((card) => {
        setDepartment(card.dept ?? "");
        setPosition(card.position ?? "");
      })
      .catch(() => {
        // Карточка недоступна — подразделение/должность останутся «—».
      });
  }

  // Очистка выбора сотрудника (кнопка ✕ рядом с поиском): сброс полей
  // сотрудника и возврат к поиску в 1С.
  function clearEmployeePick(): void {
    markTouched();
    setEmpPicked(false);
    setEmpKey("");
    setEmpQuery("");
    setEmpHits([]);
    setEmpListOpen(false);
    setEmpSearching(false);
    setEmpTotal(0);
    setEmpPage(1);
    setManualMode(false);
    setManualNote("");
    setFio("");
    setTabNum("");
    setDepartment("");
    setPosition("");
    setBaseCode("");
    setAdSam("");
    setDismissedStages([]);
    setDismissedStepOrders([]);
    setAddedStages([]);
    setPreview(null);
    setPreviewError("");
    setAddStageOpen(false);
    resetManagerPick();
  }

  // Замена руководителя вручную: сброс (смена сотрудника, снятие замены) и
  // подбор по ФИО из AD. Пустой managerSam — доверяем руководителю из AD.
  function resetManagerPick(): void {
    setManagerSam("");
    setManagerName("");
    setManagerPickOpen(false);
    setManagerQuery("");
    setManagerHits([]);
    setManagerSearching(false);
    setManagerError("");
    managerSeq.current++;
  }

  // Живой поиск руководителя в AD: запрос на каждое изменение поля, ответы
  // приходят не по порядку — актуальный отсекается по managerSeq. Пустой
  // запрос — пустой список без обращения к каталогу. Панель подбора может быть
  // открыта пользователем или из-за заблокированного этапа.
  const managerPanel = managerPickOpen || preview?.stages.some(
    (s) => s.owner_kind === "manager_ad" && s.blocked_reason != null,
  ) === true;
  useEffect(() => {
    const q = managerQuery.trim();
    if (!managerPanel || q === "") {
      managerSeq.current++;
      setManagerHits([]);
      setManagerSearching(false);
      setManagerError("");
      return;
    }
    const seq = ++managerSeq.current;
    setManagerSearching(true);
    setManagerError("");
    const timer = setTimeout(() => {
      searchAd(q)
        .then((items) => {
          if (seq !== managerSeq.current) return;
          setManagerHits(items);
          setManagerSearching(false);
        })
        .catch((e: unknown) => {
          if (seq !== managerSeq.current) return;
          setManagerHits([]);
          setManagerSearching(false);
          setManagerError(e instanceof Error ? e.message : "Ошибка поиска в AD");
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [managerQuery, managerPanel]);

  function pickManager(cand: AdCandidate): void {
    markTouched();
    setManagerSam(cand.sam);
    setManagerName(cand.display_name || cand.sam);
    setManagerQuery(cand.display_name || cand.sam);
    setManagerHits([]);
    setManagerError("");
    setManagerPickOpen(false);
  }

  // Снятие/возврат этапа маршрута (auto): optional=false — этап обязательный,
  // снять его нельзя (чекбокс неактивен). У шага бланка кода этапа нет, поэтому
  // его снимают по номеру шага (step_order из предпросмотра).
  function toggleStage(code: string, optional: boolean, stepOrder?: number | null): void {
    if (!optional) return;
    markTouched();
    if (code === "") {
      if (stepOrder == null) return;
      setDismissedStepOrders((prev) =>
        prev.includes(stepOrder) ? prev.filter((n) => n !== stepOrder) : [...prev, stepOrder],
      );
      return;
    }
    setDismissedStages((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code],
    );
  }

  // Добавление этапа в конец маршрута (auto): код уходит в added_stages.
  function addStage(code: string): void {
    if (code === "") return;
    markTouched();
    setAddedStages((prev) => (prev.includes(code) ? prev : [...prev, code]));
    setAddStageOpen(false);
  }

  // Переключение режима маршрута: в custom предпросмотр не нужен.
function changeRouteMode(mode: RouteMode): void {
  markTouched();
  setRouteMode(mode);
  // Переключение между режимами
  if (routeMode === "auto" && mode === "custom") {
    // переход с auto → custom: очистить авто‑состояния, блоки и связанные
    setDismissedStages([]);
    setAddedStages([]);
    setBlankId("");
    setBlocks([]);
    setAddStageOpen(false);
    setPreview(null);
    setPreviewError("");
  } else if (routeMode === "custom" && mode === "auto") {
    // переход с custom → auto: удалить все блоки, группы и очистить preview
    setBlocks([]);
    setGroupPick({});
    setGroupMembers({});
    setPreview(null);
    setPreviewError("");
  }
  // Модалка AD — часть ручного конструктора: в auto её закрываем.
  if (mode === "auto" && adPanelBlock !== null) closeAdPanel();
}


  // Конструктор маршрута: добавление/удаление блоков и шагов (→ dirty).

  function addBlock(mode: "sequential" | "parallel"): void {
    markTouched();
    // Стабильный id блока: состояние группы привязано к нему, а не к индексу.
    const id = `blk-${++blockSeq.current}`;
    setBlocks((prev) => [...prev, { id, mode, kind: "user", steps: [] }]);
  }

  function removeBlock(blockId: string): void {
    markTouched();
    setBlocks((prev) => prev.filter((b) => b.id !== blockId));
    // Состояние удалённого блока не должно достаться оставшимся блокам.
    setGroupPick((prev) => omitKey(prev, blockId));
    setGroupMembers((prev) => omitKey(prev, blockId));
    delete groupSeq.current[blockId];
    if (adPanelBlock === blockId) closeAdPanel();
  }

  function setBlockMode(blockId: string, mode: "sequential" | "parallel"): void {
    markTouched();
    setBlocks((prev) => prev.map((b) => (b.id === blockId ? { ...b, mode } : b)));
  }

  // Тип исполнителя блока: сотрудник (поиск AD) либо группа (список из settings).
  function setBlockKind(blockId: string, kind: ExecutorKind): void {
    markTouched();
    setBlocks((prev) => prev.map((b) => (b.id === blockId ? { ...b, kind } : b)));
    if (adPanelBlock === blockId) closeAdPanel();
  }

  function removeStep(blockId: string, stepIndex: number): void {
    markTouched();
    setBlocks((prev) =>
      prev.map((b) =>
        b.id === blockId ? { ...b, steps: b.steps.filter((_, s) => s !== stepIndex) } : b,
      ),
    );
  }

  function openAdPanel(blockId: string): void {
    setAdPanelBlock(blockId);
    setAdQuery("");
    setAdCandidates([]);
    setAdSearchError("");
    setAdSelected([]);
  }

  function closeAdPanel(): void {
    adSeq.current++;
    setAdPanelBlock(null);
    setAdCandidates([]);
    setAdQuery("");
    setAdSearchError("");
    setAdSelected([]);
  }

  // Переключение чекбокса кандидата в модалке (выбор сохраняется между
  // поисками, пока модалка открыта).
  function toggleAdSelected(cand: AdCandidate): void {
    setAdSelected((prev) =>
      prev.some((c) => c.sam === cand.sam)
        ? prev.filter((c) => c.sam !== cand.sam)
        : [...prev, cand],
    );
  }

  // Добавление отмеченных в модалке исполнителей из AD в текущий блок.
  function confirmAdSelected(): void {
    const blockId = adPanelBlock;
    if (blockId === null || adSelected.length === 0) return;
    markTouched();
    setBlocks((prev) =>
      prev.map((b) =>
        b.id === blockId
          ? {
              ...b,
              steps: [
                ...b.steps,
                ...adSelected.map((cand) => ({
                  kind: "user" as const,
                  sam: cand.sam,
                  display_name: cand.display_name,
                  resolver: "by_user",
                })),
              ],
            }
          : b,
      ),
    );
    closeAdPanel();
  }

  // Выбор группы для блока: подгрузка состава из AD.
  function pickGroup(blockId: string, group: string): void {
    markTouched();
    setGroupPick((prev) => ({ ...prev, [blockId]: group }));
    loadGroupMembers(blockId, group);
  }

  // Добавление выбранной группы в текущий блок: owner_group + резолвер by_group.
  function addGroupToBlock(blockId: string, group: string): void {
    if (group === "") return;
    markTouched();
    setBlocks((prev) =>
      prev.map((b) =>
        b.id === blockId
          ? {
              ...b,
              steps: [
                ...b.steps,
                { kind: "group", sam: "", display_name: group, owner_group: group, resolver: "by_group" },
              ],
            }
          : b,
      ),
    );
  }

  // Сброс формы: после успешного создания и по кнопке «Отмена». Вид документа
  // возвращается к первому активному, бланк сбрасывается (выбирает человек),
  // остальные поля пусты. created НЕ трогаем —
  // статус «Заявка … создана» остаётся видимым.
  function resetForm(): void {
    setEnterprise("");
    setTabNum("");
    setFio("");
    setDepartment("");
    setPosition("");
    setEmpQuery("");
    setEmpHits([]);
    setEmpListOpen(false);
    setEmpPicked(false);
    setEmpKey("");
    setManualMode(false);
    setManualNote("");
    setSubject("");
    setContent("");
    setComment("");
    setDocTypeCode(docTypes.length > 0 ? docTypes[0].code : "");
    setBlankId("");
    setBlocks([]);
    setRouteMode("auto");
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(false);
    setDismissedStages([]);
    setDismissedStepOrders([]);
    setAddedStages([]);
    setAddStageOpen(false);
    setBaseCode("");
    setAdSam("");
    resetManagerPick();
    previewSeq.current++;
    // blockSeq НЕ обнуляем: счётчик монотонно растёт, поэтому id блоков
    // уникальны в пределах жизненного цикла формы. Иначе первый блок новой
    // формы получил бы тот же blk-N, счётчик groupSeq (обнуляемый ниже) сбился
    // бы в ту же единицу, и устаревший in-flight ответ getAdGroupMembers по
    // прежнему блоку прошёл бы guard и записал состав новому блоку.
    groupSeq.current = {};
    setGroupPick({});
    setGroupMembers({});
    closeAdPanel();
    setTouched(false);
    onDirtyChange?.(false);
  }

  // «Отмена» (кнопка макета, серая): в окне-попе — закрыть окно, иначе сброс формы.
  function handleCancel(): void {
    if (closeOnCreate) {
      window.close();
      return;
    }
    resetForm();
  }

  // Создание заявки: POST /api/requests; при submit=true — сразу POST /{id}/submit
  // («Отправить на согласование»). При успехе — статус и сброс формы; при
  // ошибке (в т.ч. submit) — текст ошибки, окно не закрывается, а созданный
  // черновик запоминается: повторная отправка шлёт тот же id, без дубля.
  async function handleCreate(submit: boolean): Promise<void> {
    if (busy) return;
    setCreateError("");
    setCreated("");
    setBusy(true);
    try {
      const payload: CreateRequestBody = {
        enterprise,
        tab_num: tabNum,
        department,
        position,
        fio,
        subject,
        content,
        ...(docTypeCode !== "" ? { doc_type_code: docTypeCode } : {}),
        ...(baseCode !== "" ? { base_code: baseCode } : {}),
        ...(adSam !== "" ? { ad_sam: adSam } : {}),
        ...(managerSam !== "" ? { manager: managerSam } : {}),
        route_mode: routeMode,
        ...(routeMode === "auto"
          ? {
            blank_id: Number(blankId),
            dismissed_stages: dismissedStages,
            dismissed_step_orders: dismissedStepOrders,
            added_stages: addedStages,
          }
          : {
            blocks: blocks.map((b) => ({
              mode: b.mode,
              kind: b.kind,
              steps: b.steps.map((s) =>
                s.kind === "group"
                  ? { owner_group: s.owner_group ?? s.display_name, resolver: s.resolver }
                  : { sam: s.sam },
              ),
            })),
          }),
      };


      // Повтор «Отправить» после сбоя submit переиспользует тот же черновик,
      // ТОЛЬКО если данные формы не изменились. При правках создаём новый
      // черновик: иначе изменения не применились бы и молча потерялись.
      const signature = JSON.stringify(payload);
      let id =
        submit && pendingDraft.current?.signature === signature
          ? pendingDraft.current.id
          : null;
      if (id === null) {
        const result = await createRequest(payload);
        id = result.id;
        // Запоминаем черновик только для отправки — повтор сбоя переиспользует.
        pendingDraft.current = submit ? { id, signature } : null;
      }
      if (submit) await submitRequest(id);
      pendingDraft.current = null;
      setCreated(`Заявка ${id} создана`);
      resetForm();
      // В окне-попе — после создания закрыть окно (список обновится по фокусу).
      // Через onClose не идём: он смотрит «несохранённые данные», а форма уже
      // сброшена, и подтверждение выскакивало бы после каждой отправки.
      if (closeOnCreate) window.close();
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : "Ошибка создания заявки");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Создание заявки">
      <h3>Создание заявки</h3>
      {/* Этап и статус процесса — как в макете (DESIGN.md п.1.6). */}
      <div className="sed-meta">Этап: создание заявки · Статус: черновик</div>

      {loadError && <div role="alert">Ошибка: {loadError}</div>}
      {created && <div role="status">{created}</div>}

      {/* Две панели макета (.panels/.panel): слева — предприятие и сотрудник,
          справа — инициатор (только чтение) и справочные данные из 1С. */}
      <div className="sed-panels">
        <div className="sed-panel">
          {/* Предприятие из настроек (без хардкод-массивов) — шаг 1 формы. */}
          <label className="sed-field">
            Предприятие
            <select
              aria-label="Предприятие"
              value={enterprise}
              onChange={(e) => {
                markTouched();
                setEnterprise(e.target.value);
              }}
            >
              <option value="">— выберите —</option>
              {enterprises.map((ent) => (
                <option key={ent.code} value={ent.code}>
                  {ent.name}
                </option>
              ))}
            </select>
          </label>

          {/* Сотрудник: активируется после выбора предприятия. */}
          {enterprise && (
            <fieldset className="sed-fieldset sed-mt-12">
              <legend>Сотрудник</legend>
              <div className="sed-rel">
                {empPicked && empKey !== "" ? (
                  // Выбранный сотрудник — текст-ссылка на карточку (окно-попап)
                  // и очистка выбора в одну строку; для нового поиска — крестик.
                  <div className="sed-fieldrow">
                    <span>
                      <a
                        href={employeeUrl(empKey)}
                        onClick={(e) => {
                          // Окно карточки сотрудника — по клику (иначе браузер
                          // блокирует popup); default-переход не нужен.
                          e.preventDefault();
                          openPopup(employeeUrl(empKey));
                        }}
                      >
                        {fio || empQuery}
                      </a>
                    </span>
                    <button
                      type="button"
                      className="sed-roundbtn"
                      aria-label="Очистить выбор сотрудника"
                      title="Очистить выбор"
                      onClick={clearEmployeePick}
                    >
                      <CrossIcon />
                    </button>
                  </div>
                ) : (
                <div className="sed-fieldrow">
                  <label className="sed-field">
                    Поиск сотрудника
                    <input
                      aria-label="Поиск сотрудника"
                      placeholder="ФИО / табельный №"
                      value={empQuery}
                      onChange={(e) => {
                        markTouched();
                        // Редактирование после выбора — новый поиск: сбрасываем выбор.
                        if (empPicked) clearEmployeePick();
                        // Новая строка запроса — возврат к первой странице.
                        setEmpPage(1);
                        setEmpQuery(e.target.value);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setEmpListOpen(false);
                      }}
                      onFocus={() => {
                        if (empHits.length > 0) setEmpListOpen(true);
                      }}
                    />
                  </label>
                  {empPicked && (
                    <button
                      type="button"
                      className="sed-roundbtn"
                      aria-label="Очистить выбор сотрудника"
                      title="Очистить выбор"
                      onClick={clearEmployeePick}
                    >
                      <CrossIcon />
                    </button>
                  )}
                </div>
                )}
                {empListOpen && empHits.length > 0 && (
                  <ul className="sed-dropdown">
                    {empHits.map((h) => (
                      <li key={h.key}>
                        <button type="button" className="sed-dropdown__item" onClick={() => pickEmployee(h)}>
                          <strong>{h.fio}</strong> · {h.tab_num}
                          {(h.dept || h.position) && (
                            <div className="sed-sub">
                              {[h.dept, h.position].filter(Boolean).join(" · ")}
                            </div>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {/* Пагинация поиска сотрудника: больше одной страницы совпадений. */}
                {empListOpen && empTotalPages > 1 && (
                  <div className="sed-pager" aria-label="Пагинация поиска сотрудника">
                    <button
                      type="button"
                      className="sed-btn"
                      aria-label="Первая страница"
                      disabled={empPage <= 1}
                      onClick={() => setEmpPage(1)}
                    >
                      Первая
                    </button>
                    <button
                      type="button"
                      className="sed-btn"
                      aria-label="Предыдущая страница сотрудников"
                      disabled={empPage <= 1}
                      onClick={() => setEmpPage(empPage - 1)}
                    >
                      ← Назад
                    </button>
                    {pagerPages(empPage, empTotalPages).map((page, index) =>
                      page === "…" ? (
                        <span key={`ell-${index}`} aria-hidden="true">…</span>
                      ) : (
                        <button
                          key={page}
                          type="button"
                          className="sed-btn"
                          aria-label={`Страница ${page}`}
                          aria-current={page === empPage ? "page" : undefined}
                          disabled={page === empPage}
                          onClick={() => setEmpPage(page)}
                        >
                          {page}
                        </button>
                      ),
                    )}
                    <button
                      type="button"
                      className="sed-btn"
                      aria-label="Следующая страница сотрудников"
                      disabled={empPage >= empTotalPages}
                      onClick={() => setEmpPage(empPage + 1)}
                    >
                      Вперёд →
                    </button>
                    <button
                      type="button"
                      className="sed-btn"
                      aria-label="Последняя страница"
                      disabled={empPage >= empTotalPages}
                      onClick={() => setEmpPage(empTotalPages)}
                    >
                      Последняя
                    </button>
                    <span role="status">стр {empPage} из {empTotalPages}</span>
                  </div>
                )}
              </div>
              {empSearching && <div className="sed-note">Поиск в 1С…</div>}
              {/* Ручной режим (503/ничего не найдено): поля редактируемые. */}
              {manualMode && (
                <fieldset className="sed-fieldset">
                  <legend>Данные сотрудника (вручную)</legend>
                  {manualNote && <div className="sed-note">{manualNote}</div>}
                  <label className="sed-field">
                    ФИО
                    <input aria-label="ФИО" value={fio} onChange={(e) => { markTouched(); setFio(e.target.value); }} />
                  </label>
                  <label className="sed-field">
                    Табельный №
                    <input aria-label="Табельный №" value={tabNum} onChange={(e) => { markTouched(); setTabNum(e.target.value); }} />
                  </label>
                  <label className="sed-field">
                    Подразделение
                    <input aria-label="Подразделение" value={department} onChange={(e) => { markTouched(); setDepartment(e.target.value); }} />
                  </label>
                  <label className="sed-field">
                    Должность
                    <input aria-label="Должность" value={position} onChange={(e) => { markTouched(); setPosition(e.target.value); }} />
                  </label>
                </fieldset>
              )}
              {!manualMode && empHits.length === 0 && !empSearching && tabNum === "" && (
                <div className="sed-note">Введите запрос для поиска в 1С либо укажите данные вручную.</div>
              )}
            </fieldset>
          )}

          {/* Бланк из справочника (GET /api/requests/route/blanks): название и
              число шагов в подписи, описание бланка — подсказкой. Выбор задаёт
              этапы маршрута заявки; без него маршрут подбирается по службе,
              если это разрешено настройкой blank_autopick. */}
          <label className="sed-field">
            Бланк
            <select
              aria-label="Бланк"
              value={blankId}
              onChange={(e) => {
                markTouched();
                setBlankId(e.target.value);
                // Другой бланк — номера снятых шагов прежнего к нему не относятся
                // (сервер отверг бы их как отсутствующие в маршруте).
                setDismissedStepOrders([]);
              }}
            >
              <option value="">— выберите бланк —</option>
              {blanks.map((blank) => (
                <option key={blank.id} value={String(blank.id)} title={blank.description ?? ""}>
                  {blank.name} ({blank.step_count} шаг.)
                </option>
              ))}
            </select>
          </label>
          {blanksError && <div className="sed-note">Бланки: {blanksError}</div>}
          {selectedBlank && selectedBlank.description && (
            <div className="sed-note">{selectedBlank.description}</div>
          )}
          {blanks.length === 0 && !blanksError && (
            <div className="sed-note">
              Активных бланков нет — заведите бланк в настройках (вкладка «Бланки»).
            </div>
          )}
          {!selectedBlank && (
            <div className="sed-note">
              Бланк не выбран — задайте маршрут вручную (режим «Вручную» в блоке
              «Маршрут согласования»).
            </div>
          )}
          {!selectedBlank && !blankAutopick && (
            <div className="sed-note">
              Без выбора бланка маршрут по профилю собран не будет: подстановка бланка по
              службе выключена.
            </div>
          )}

          {/* Вид документа (селект из GET /api/doc-types, только активные),
              тема и содержание — обязательные поля макета (DESIGN.md п.1.12). */}
          <label className="sed-field">
            Вид документа
            <select
              aria-label="Вид документа"
              value={docTypeCode}
              onChange={(e) => {
                markTouched();
                setDocTypeCode(e.target.value);
              }}
            >
              {docTypes.length === 0 && <option value="">— не задан —</option>}
              {docTypes.map((dt) => (
                <option key={dt.code} value={dt.code}>
                  {dt.name}
                </option>
              ))}
            </select>
          </label>
          {docTypesError && <div className="sed-note">Виды документов: {docTypesError}</div>}
          <label className="sed-field">
            Тема
            <input
              aria-label="Тема"
              required
              value={subject}
              onChange={(e) => {
                markTouched();
                setSubject(e.target.value);
              }}
            />
          </label>
          <label className="sed-field">
            Содержание
            <textarea
              aria-label="Содержание"
              rows={3}
              value={content}
              onChange={(e) => {
                markTouched();
                setContent(e.target.value);
              }}
            />
          </label>

          {/* Файлы: до создания заявки загрузки нет (эндпоинт привязан к id
              заявки) — подсказка вместо неработающих кнопок макета. */}
          <div className="sed-note">
            Файлы: скан заявления добавляется в карточке заявки после создания (необязательно).
          </div>
        </div>

        <div className="sed-panel">
          {/* Инициатор — из сессии, только чтение: жирный ярлык + всё ФИО
              ссылкой на свою карточку (есть связка АД-1С), иначе
              readonly-текст. Пояснение «только чтение» убрано — и так видно. */}
          {initiatorKey !== "" ? (
            <div className="sed-field">
              <strong>Инициатор</strong>{" "}
              <a
                href={employeeUrl(initiatorKey)}
                onClick={(e) => {
                  // Окно карточки сотрудника — по клику (иначе браузер
                  // блокирует popup); default-переход не нужен.
                  e.preventDefault();
                  openPopup(employeeUrl(initiatorKey));
                }}
              >
                {initiator || "—"}
              </a>
            </div>
          ) : (
            <label className="sed-field">
              <strong>Инициатор</strong>
              <input aria-label="Инициатор" readOnly value={initiator} placeholder="—" />
            </label>
          )}
          {/* Данные сотрудника из 1С — справочные (read-only); подразделение/
              должность могут быть пустыми («—»), создание допустимо без них. */}
          {!manualMode && enterprise !== "" && tabNum !== "" && (
            <fieldset className="sed-fieldset sed-mt-12">
              <legend>Подразделение / должность (из 1С)</legend>
              <div className="sed-note">Данные из 1С — справочно, изменить нельзя.</div>
              <div className="sed-block">
                ФИО: <strong>{fio}</strong>
                {empKey !== "" && (
                  <a
                    href={employeeUrl(empKey)}
                    onClick={(e) => {
                      // Окно карточки сотрудника открывается по клику (иначе
                      // браузер блокирует popup); default-переход не нужен.
                      e.preventDefault();
                      openPopup(employeeUrl(empKey));
                    }}
                    className="sed-ml-8"
                  >
                    карточка сотрудника
                  </a>
                )}
              </div>
              <div className="sed-block">
                Табельный №: <strong>{tabNum}</strong>
              </div>
              <div className="sed-block">
                Подразделение: <strong>{department || "—"}</strong>
              </div>
              <div className="sed-block">
                Должность: <strong>{position || "—"}</strong>
              </div>
            </fieldset>
          )}

          {/* Комментарий к заявке (правая панель макета): поле повыше,
              на всю ширину панели. */}
          <label className="sed-field">
            Комментарий
            <textarea
              aria-label="Комментарий"
              rows={5}
              value={comment}
              onChange={(e) => {
                markTouched();
                setComment(e.target.value);
              }}
            />
          </label>
        </div>
      </div>

      {/* Маршрут согласования: режим auto — подбор по профилю службы с
          предпросмотром этапов; режим custom — конструктор блоков
          (последовательный/параллельный) с исполнителями из AD или группами. */}
      {enterprise && employeeReady && (
        <fieldset className="sed-fieldset sed-mt-12">
          <legend>Маршрут согласования</legend>
          <div className="sed-fieldrow" role="radiogroup" aria-label="Режим маршрута">
            <label className="sed-field">
              <input
                type="radio"
                name="route-mode"
                aria-label="По профилю (рекомендуется)"
                checked={routeMode === "auto"}
                onChange={() => changeRouteMode("auto")}
              />
              {" "}По профилю (рекомендуется)
            </label>
            <label className="sed-field">
              <input
                type="radio"
                name="route-mode"
                aria-label="Вручную"
                checked={routeMode === "custom"}
                onChange={() => changeRouteMode("custom")}
              />
              {" "}Вручную
            </label>
          </div>

          {/* Автоматический маршрут: предпросмотр (профиль, служба, этапы).
              Ошибка предпросмотра (422/503) — текстом; ручной режим при этом
              остаётся доступен (переключение выше). */}
          {routeMode === "auto" && (
            <div>
              {previewLoading && <div className="sed-note">Подбор маршрута…</div>}
              {previewError && (
                <div role="alert">
                  Маршрут: {previewError}. Переключите режим на «Вручную», чтобы задать маршрут самостоятельно.
                </div>
              )}
              {preview && (
                <>
                  {previewBlank && (
                    <div className="sed-block">
                      Бланк: <strong>{previewBlank.name}</strong>
                      {` · шагов: ${previewBlank.step_count}`}
                      {previewBlank.version != null && ` · версия ${previewBlank.version}`}
                    </div>
                  )}
                  <div className="sed-block">
                    Профиль: <strong>{preview.profile?.name ?? "—"}</strong>
                  </div>
                  <div className="sed-block">
                    Служба: <strong>{preview.service?.dept_name ?? "—"}</strong>
                  </div>
                  <div className="sed-block">
                    Причина подбора: <strong>{routeReasonText(preview.reason)}</strong>
                  </div>
                  {/* Маршрут не подобрался (профиля/службы нет) — предупреждение:
                      заявку в этом режиме создать нельзя, нужен ручной маршрут. */}
                  {ROUTE_REASON_MISSED.includes(preview.reason.split("+")[0] ?? "") && (
                    <div className="sed-note">
                      Маршрут не подобрался: {routeReasonText(preview.reason)}. Задайте маршрут вручную.
                    </div>
                  )}
                </>
              )}
              {/* Руководитель (этап manager_ad). ФИО и причину блокировки не
                  дублируем — они видны в таблице этапов; здесь только ручной
                  подбор замены. Если этап заблокирован, подбор открыт сразу. */}
              {preview && managerStage && (
                <div className="sed-block">
                  <div>
                    Руководитель:{" "}
                    <strong>
                      {managerSam !== ""
                        ? `${managerName} (выбран вручную)`
                        : managerStage.blocked_reason
                          ? "не определён в AD — подберите замену"
                          : "найден в AD"}
                    </strong>
                  </div>
                  {managerSam !== "" ? (
                    <div className="sed-toolbar sed-mt-8">
                      <button
                        type="button"
                        className="sed-btn sed-btn--ghost"
                        aria-label="Сбросить замену руководителя"
                        onClick={resetManagerPick}
                      >
                        Сбросить замену
                      </button>
                    </div>
                  ) : (
                    <div className="sed-toolbar sed-mt-8">
                      <label>
                        <input
                          type="checkbox"
                          aria-label="Выбрать руководителя вручную"
                          checked={managerPickOpen || managerStage.blocked_reason !== null}
                          disabled={managerStage.blocked_reason !== null}
                          onChange={(e) => setManagerPickOpen(e.target.checked)}
                        />{" "}
                        Выбрать руководителя вручную
                      </label>
                    </div>
                  )}
                  {managerSam === "" && (managerPickOpen || managerStage.blocked_reason !== null) && (
                    <div className="sed-toolbar sed-mt-8">
                      <input
                        aria-label="ФИО руководителя"
                        value={managerQuery}
                        placeholder="ФИО руководителя — поиск идёт по мере набора"
                        onChange={(e) => setManagerQuery(e.target.value)}
                      />
                      {managerSearching && <span className="sed-note">Ищем…</span>}
                      {!managerSearching && managerQuery.trim() !== "" && managerHits.length === 0 && (
                        <span className="sed-note">Ничего не найдено в AD</span>
                      )}
                      {managerError && <span className="sed-note">{managerError}</span>}
                    </div>
                  )}
                  {managerSam === "" && managerHits.length > 0 && (
                    <ul className="sed-list">
                      {managerHits.map((cand) => (
                        <li key={cand.sam}>
                          <button
                            type="button"
                            className="sed-btn sed-btn--ghost"
                            aria-label={`Выбрать руководителя ${cand.display_name}`}
                            onClick={() => pickManager(cand)}
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
                  {/* Предупреждение бэкенда: например, сотрудник не найден в AD —
                      маршрут по службе не подобрался, печать пойдёт бланком по
                      умолчанию. Показываем до подробностей подбора. */}
                  {preview && preview.notice && (
                    <div role="alert" className="sed-note">
                      {preview.notice}
                    </div>
                  )}
                  {/* Связь 1С↔AD не оформлена, хотя кандидат в AD один: ОК
                      подтверждает связь здесь — служба и руководитель подтянутся,
                      и маршрут соберётся без похода в другой раздел. */}
                  {preview && preview.link_state === "need_link" && preview.link_candidate && (
                    <div className="sed-note">
                      <div>
                        В AD: {preview.link_candidate.fio || preview.link_candidate.sam}
                        {preview.link_candidate.title_ad ? ` (${preview.link_candidate.title_ad})` : ""}
                        {preview.link_candidate.dept_ad ? `, ${preview.link_candidate.dept_ad}` : ""}
                      </div>
                      <button
                        type="button"
                        onClick={confirmLink}
                        disabled={linking}
                      >
                        {linking ? "Связываем…" : "Подтвердить связь с AD"}
                      </button>
                      {linkError && <div role="alert">{linkError}</div>}
                    </div>
                  )}
                  {/* Этапы: по умолчанию все включены; снятая галочка — код в
                      dismissed_stages, а для шага бланка (кода этапа нет) — его
                      номер в dismissed_step_orders. optional=false — обязательный. */}
              {preview && preview.stages.length === 0 && (
                <div className="sed-note">Этапы не подобраны.</div>
              )}
              {preview && preview.stages.length > 0 && (
                <table className="sed-table" aria-label="Этапы маршрута">
                  <thead>
                    <tr>
                      <th scope="col">
                        <span className="sed-hidden">Включён</span>
                      </th>
                      <th scope="col">Этап</th>
                      <th scope="col">Исполнитель</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.stages.map((stage) => {
                      const code = stage.code ?? "";
                      const title = stage.title || code;
                      // Шаг бланка этапа не имеет (code=null): его снимают по номеру
                      // шага из предпросмотра. Номера нет — снять нечем (объясняем
                      // прямо в строке).
                      const order = stage.step_order ?? null;
                      const blankLocked = code === "" && order === null;
                      // Пока строка в предпросмотре — шаг в маршруте, значит галочка
                      // включена: у шага бланка без номера снять его нечем.
                      const checked =
                        code === ""
                          ? order === null || !dismissedStepOrders.includes(order)
                          : !dismissedStages.includes(code);
                      return (
                        <tr
                          key={
                            code !== ""
                              ? code
                              : `step-${order ?? stage.stage_id ?? title}`
                          }
                        >
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Этап ${title}`}
                              checked={checked}
                              disabled={!stage.optional || blankLocked}
                              title={
                                blankLocked
                                  ? "Шаг задан бланком — номера у него нет, снять вручную нельзя"
                                  : stage.optional
                                    ? code === ""
                                      ? "Снять шаг бланка из маршрута"
                                      : "Снять этап из маршрута"
                                    : code === ""
                                      ? "Шаг бланка обязательный"
                                      : "Этап обязательный"
                              }
                              onChange={() => toggleStage(code, stage.optional, order)}
                            />
                          </td>
                          <td>
                            {title}
                            {!stage.optional && <span className="sed-sub"> (обязательный)</span>}
                            {/* Номер шага бланка показываем, чтобы снятие было
                                однозначным: у него нет кода этапа. Снять нельзя
                                только когда номера нет либо шаг обязательный —
                                это уже видно по подписи «(обязательный)». */}
                            {code === "" && blankLocked && (
                              <span className="sed-sub"> (шаг бланка — снять вручную нельзя)</span>
                            )}
                            {stage.stage_lines.length > 0 && (
                              <div className="sed-sub">{stage.stage_lines.join(" · ")}</div>
                            )}
                            {/* Причина блокировки этапа (важно для ОК: этап
                                нельзя закрыть — например, не найден руководитель). */}
                            {stage.blocked_reason && (
                              <div className="sed-note">{stage.blocked_reason}</div>
                            )}
                          </td>
                          <td>
                            {stage.owner_name ?? stage.owner_group ?? "—"}
                            <div className="sed-sub">
                              {OWNER_KIND_TEXT[stage.owner_kind] ?? stage.owner_kind}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {/* Добавление этапа: справочник админский — при 403 кнопки нет. */}
              {catalogStages !== null && catalogStages.length > 0 && (
                <div className="sed-toolbar sed-mt-8">
                  <button
                    type="button"
                    className="sed-btn sed-btn--ghost"
                    aria-label="Добавить этап"
                    onClick={() => setAddStageOpen(!addStageOpen)}
                  >
                    <PlusIcon />
                    Добавить этап
                  </button>
                  {addStageOpen && (
                    <select
                      aria-label="Этап для добавления"
                      value=""
                      onChange={(e) => addStage(e.target.value)}
                    >
                      <option value="">— выберите этап —</option>
                      {catalogStages
                        .filter((s) => !addedStages.includes(s.code))
                        .map((s) => (
                          <option key={s.code} value={s.code}>
                            {s.title || s.code}
                          </option>
                        ))}
                    </select>
                  )}
                  {addedStages.length > 0 && (
                    <span className="sed-note">Добавленные этапы: {addedStages.length}</span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Ручной конструктор маршрута — только в режиме custom. */}
          {routeMode === "custom" && (
            <>
          <div className="sed-note">
            Конструктор маршрута: блоки с исполнителями — сотрудником из AD или группой.
          </div>
          {groupsError && <div className="sed-note">Группы: {groupsError}</div>}
          {blocks.length === 0 && <div className="sed-note">Добавьте блок и исполнителей.</div>}
          {/* Конструктор блоков — таблица «Рассмотрение» образца: каждый блок
              отдельной карточкой с колонкой типа (последовательно/параллельно). */}
          {blocks.length > 0 && (
            <div className="sed-review">
              <b>Рассмотрение</b>
              {blocks.map((block, bi) => (
                <section
                  key={block.id}
                  aria-label={`Блок ${bi + 1}`}
                  className={
                    block.mode === "parallel"
                      ? "sed-blockcard sed-blockcard--parallel"
                      : "sed-blockcard sed-blockcard--sequential"
                  }
                >
                  <div className="sed-blockcard__head">
                    <span className="sed-blockcard__title">Блок {bi + 1}</span>
                    <span className="sed-blockcard__type">
                      {block.mode === "parallel" ? "Параллельно" : "Последовательно"}
                    </span>
                    <span className="sed-blockcard__spacer" />
                    {/* Вид рассмотрения: последовательный или параллельный. */}
                    <select
                      aria-label={`Режим блока ${bi + 1}`}
                      value={block.mode}
                      onChange={(e) => setBlockMode(block.id, e.target.value as "sequential" | "parallel")}
                    >
                      <option value="sequential">Последовательный</option>
                      <option value="parallel">Параллельный</option>
                    </select>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost sed-delbtn"
                      aria-label="Удалить блок"
                      onClick={() => removeBlock(block.id)}
                    >
                      <CrossIcon />
                      Удалить
                    </button>
                  </div>
                  <table className="sed-table">
                    <thead>
                      <tr>
                        <th scope="col">Должность</th>
                        <th scope="col">Сотрудник</th>
                        <th scope="col">Комментарий</th>
                        <th scope="col">Действия</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        {/* Тип исполнителя и группа-владелец шага. */}
                        <td>
                      <select
                        aria-label={`Тип исполнителя блока ${bi + 1}`}
                        value={block.kind}
                        onChange={(e) => setBlockKind(block.id, e.target.value as ExecutorKind)}
                      >
                        <option value="user">Сотрудник</option>
                        <option value="group">Группа</option>
                      </select>
                      {block.kind === "group" && (
                        <div className="sed-mt-8">
                          <label className="sed-field">
                            Группа
                            <select
                              aria-label={`Группа блока ${bi + 1}`}
                              value={groupPick[block.id] ?? ""}
                              onChange={(e) => pickGroup(block.id, e.target.value)}
                            >
                              <option value="">— выберите —</option>
                              {groups.map((g) => (
                                <option key={g.id} value={g.id}>
                                  {g.name}
                                </option>
                              ))}
                            </select>
                          </label>
                          {groups.length === 0 && !groupsError && (
                            <div className="sed-note">Список групп пуст (задаётся в настройках).</div>
                          )}
                          {(groupPick[block.id] ?? "") !== "" && (() => {
                            // Состав именно этого блока: у каждого группового блока своя группа.
                            const gm = groupMembers[block.id] ?? EMPTY_GROUP_MEMBERS;
                            return (
                              <>
                                {/* Состав группы: счётчик + раскрываемый список (ФИО/почта). */}
                                <div className="sed-note">
                                  Состав группы: {gm.loading ? "загрузка…" : gm.members.length}
                                </div>
                                {gm.error && <div role="alert">{gm.error}</div>}
                                {!gm.loading && gm.error === "" && (
                                  <button
                                    type="button"
                                    className="sed-btn sed-btn--ghost"
                                    onClick={() => patchGroupMembers(block.id, { open: !gm.open })}
                                  >
                                    {gm.open ? "Скрыть состав" : "Показать состав"}
                                  </button>
                                )}
                                {gm.open && gm.members.length > 0 && (
                                  <ul aria-label={`Состав группы ${groupPick[block.id]}`} className="sed-list">
                                    {gm.members.map((m) => (
                                      <li key={m.sam}>
                                        {m.display_name}
                                        {m.mail ? ` · ${m.mail}` : ""}
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </>
                            );
                          })()}
                        </div>
                      )}
                    </td>
                    {/* Исполнители блока и выбор сотрудника из AD. */}
                    <td>
                      {block.steps.length === 0 && <div className="sed-note">Исполнители не добавлены.</div>}
                      <ul className="sed-list">
                        {block.steps.map((s, si) => (
                          <li key={`${s.kind}-${s.sam || s.owner_group}-${si}`}>
                            {s.kind === "group" ? s.display_name : `${s.display_name} (${s.sam})`}
                            <button
                              type="button"
                              aria-label={`Удалить исполнителя ${s.display_name}`}
                              title="Удалить исполнителя"
                              onClick={() => removeStep(block.id, si)}
                              className="sed-roundbtn sed-ml-8"
                            >
                              <CrossIcon />
                            </button>
                          </li>
                        ))}
                      </ul>
                      {block.kind === "user" && (
                        <button
                          type="button"
                          className="sed-btn sed-delbtn"
                          aria-label="Добавить исполнителя"
                          title="Добавить исполнителя"
                          onClick={() => openAdPanel(block.id)}
                        >
                          <PlusIcon />
                          Добавить
                        </button>
                      )}
                    </td>
                    {/* Комментарий к блоку: пока не сохраняется в маршруте (пустая ячейка). */}
                    <td />
                    {/* Действия: добавить выбранную группу-владельца в маршрут. */}
                    <td>
                      {block.kind === "group" && (groupPick[block.id] ?? "") !== "" && (
                        <button
                          type="button"
                          className="sed-btn"
                          onClick={() => addGroupToBlock(block.id, groupPick[block.id] ?? "")}
                        >
                          Добавить группу
                        </button>
                      )}
                    </td>
                  </tr>
                </tbody>
              </table>
            </section>
              ))}
            </div>
          )}
          <div className="sed-toolbar sed-mt-8">
            <button type="button" className="sed-btn sed-btn--ghost" onClick={() => addBlock("sequential")}>
              Добавить последовательный блок
            </button>
            <button type="button" className="sed-btn sed-btn--ghost" onClick={() => addBlock("parallel")}>
              Добавить параллельный блок
            </button>
          </div>
            </>
          )}
        </fieldset>
      )}

      {/* Модальный выбор исполнителей из AD (кнопка «+ Добавить» в блоке):
          поиск + чекбоксы + нижняя панель выбранных (порядок кликов) +
          Очистить/ОК/Отмена. В последовательный блок встанут в порядке выбора,
          в параллельный — все разом в блок. */}
      {adPanelBlock !== null && (() => {
        const blockIndex = blocks.findIndex((b) => b.id === adPanelBlock);
        return (
          <div className="sed-modal-backdrop" onClick={closeAdPanel}>
            <div
              className="sed-modal"
              role="dialog"
              aria-modal="true"
              aria-label={`Выбор исполнителей${blockIndex >= 0 ? ` — Блок ${blockIndex + 1}` : ""}`}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Escape") closeAdPanel();
              }}
            >
              <h4>Выбор исполнителей{blockIndex >= 0 ? ` — Блок ${blockIndex + 1}` : ""}</h4>
              <label className="sed-field">
                Поиск в AD
                <input
                  autoFocus
                  aria-label="Поиск в AD"
                  placeholder="ФИО в AD"
                  value={adQuery}
                  onChange={(e) => setAdQuery(e.target.value)}
                />
              </label>
              {adSearching && <div className="sed-note">Поиск в AD…</div>}
              {adSearchError && <div role="alert">{adSearchError}</div>}
              {adCandidates.length > 0 && (
                <table className="sed-table" aria-label="Найденные сотрудники">
                  <thead>
                    <tr>
                      <th scope="col">
                        <span className="sed-hidden">Выбор</span>
                      </th>
                      <th scope="col">ФИО</th>
                      <th scope="col">Должность</th>
                      <th scope="col">Подразделение</th>
                    </tr>
                  </thead>
                  <tbody>
                    {adCandidates.map((c) => (
                      <tr key={c.sam}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Выбрать ${c.display_name}`}
                            checked={adSelected.some((s) => s.sam === c.sam)}
                            onChange={() => toggleAdSelected(c)}
                          />
                        </td>
                        <td>{c.display_name}</td>
                        <td>{c.title || "—"}</td>
                        <td>{c.department || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {/* Выбранные (низ модалки, в порядке кликов): крестик убирает,
                  Очистить — всех; ОК переносит в блок в этом порядке. */}
              <div className="sed-block" aria-label="Выбранные исполнители">
                {adSelected.length === 0 && <div className="sed-note">Не выбрано</div>}
                <ul className="sed-list">
                  {adSelected.map((c) => (
                    <li key={c.sam}>
                      {c.display_name} ({c.sam})
                      <button
                        type="button"
                        aria-label={`Убрать ${c.display_name}`}
                        title="Убрать из выбранных"
                        onClick={() => toggleAdSelected(c)}
                        className="sed-roundbtn sed-ml-8"
                      >
                        <CrossIcon />
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="sed-toolbar sed-mt-8">
                <button
                  type="button"
                  className="sed-btn sed-btn--ghost"
                  onClick={() => setAdSelected([])}
                  disabled={adSelected.length === 0}
                >
                  Очистить
                </button>
                <span className="sed-toolbar__spacer" />
                <button
                  type="button"
                  className="sed-btn"
                  onClick={confirmAdSelected}
                  disabled={adSelected.length === 0}
                >
                  ОК
                </button>
                <button type="button" className="sed-btn sed-btn--ghost" onClick={closeAdPanel}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      <div className="sed-toolbar sed-mt-12">
        <button
          type="button"
          className="sed-btn sed-btn--neutral"
          disabled={!canCreate}
          onClick={() => handleCreate(false)}
        >
          {busy ? "Создание…" : "Создать"}
        </button>
        <button
          type="button"
          className="sed-btn"
          disabled={!canCreate}
          onClick={() => handleCreate(true)}
        >
          {busy ? "Отправка…" : "Отправить на согласование"}
        </button>
        {/* Отмена (серая, макет): в попапе — закрыть окно, иначе сброс формы. */}
        <button type="button" className="sed-btn sed-btn--neutral" onClick={handleCancel}>
          Отмена
        </button>
        {/* Закрыть — в том же тулбаре (одна строка кнопок); рендерится только
            в окне, где есть обработчик закрытия с подтверждением при dirty. */}
        {onClose && (
          <button type="button" className="sed-btn" onClick={onClose}>
            Закрыть
          </button>
        )}
      </div>
      {createError && <div role="alert">{createError}</div>}
    </section>
  );
}

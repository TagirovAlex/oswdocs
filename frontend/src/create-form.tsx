// Форма создания заявки для ОК (Задача 3.3): единая форма без стадий.
// Блоки появляются/активируются по зависимостям: предприятие → сотрудник →
// маршрут → «Создать»; невалидное — недоступно (кнопка «Создать» disabled).
// Предприятия — только из API (settings БД), хардкода нет (AGENTS.md п.3).
// Сотрудник: живой поиск в 1С (GET /api/employees); без баз (503) — ручной ввод.
// Маршрут: конструктор блоков (последовательный/параллельный) с исполнителями
// из AD (GET /api/ad/search); телом создания идут blocks, не группы (steps).
import { useEffect, useRef, useState } from "react";
import { ApiHttpError, me } from "./auth-client";
import {
  createRequest,
  getAdGroupMembers,
  getDocTypes,
  getEmployeeCard,
  getEnterprises,
  getMyLinks,
  getStepGroups,
  searchAd,
  searchEmployees,
  submitRequest,
} from "./requests-client";
import type { AdCandidate, AdGroupMember, DocType, EmployeeHit, Enterprise, StepGroup } from "./requests-client";
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
  // Ключ своей карточки сотрудника (связка АД-1С, GET /link_1c_ad/mine):
  // ровно одна связка — ФИО инициатора становится ссылкой на карточку,
  // иначе (нет/несколько) — текст без ссылки. Связка — свои данные, как ФИО в me().
  const [initiatorKey, setInitiatorKey] = useState<string>("");
  useEffect(() => {
    let alive = true;
    me()
      .then((user) => {
        if (alive) setInitiator(user.fio ?? user.sam);
        return getMyLinks();
      })
      .then((links) => {
        if (alive && links.length === 1) setInitiatorKey(links[0].key);
      })
      .catch(() => {
        // Сессия/связка недоступна — поле инициатора остаётся пустым («—»).
        if (alive) {
          setInitiator("");
          setInitiatorKey("");
        }
      });
    return () => {
      alive = false;
    };
  }, []);

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
  const canCreate =
    enterprise !== "" &&
    employeeReady &&
    subject.trim() !== "" &&
    content.trim() !== "" &&
    blocks.length > 0 &&
    blocks.every((b) => b.steps.length > 0) &&
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
    const parts = hit.key.split("|");
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
  }

  function closeAdPanel(): void {
    adSeq.current++;
    setAdPanelBlock(null);
    setAdCandidates([]);
    setAdQuery("");
    setAdSearchError("");
  }

  // Добавление выбранного из AD исполнителя в текущий блок.
  function addStepToBlock(blockId: string, cand: AdCandidate): void {
    markTouched();
    setBlocks((prev) =>
      prev.map((b) =>
        b.id === blockId
          ? {
              ...b,
              steps: [
                ...b.steps,
                {
                  kind: "user",
                  sam: cand.sam,
                  display_name: cand.display_name,
                  resolver: "by_user",
                },
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
  // возвращается к первому активному, остальные поля пусты. created НЕ трогаем —
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
    setBlocks([]);
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
      const payload = {
        enterprise,
        tab_num: tabNum,
        department,
        position,
        fio,
        subject,
        content,
        ...(docTypeCode !== "" ? { doc_type_code: docTypeCode } : {}),
        blocks: blocks.map((b) => ({
          mode: b.mode,
          // Шаг-группа уходит owner_group + by_group; шаг-сотрудник — sam.
          steps: b.steps.map((s) =>
            s.kind === "group"
              ? { owner_group: s.owner_group ?? s.display_name, resolver: s.resolver }
              : { sam: s.sam },
          ),
        })),
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
          {/* Инициатор — из сессии, только чтение. Есть однозначная связка
              АД-1С — всё ФИО является ссылкой на свою карточку сотрудника,
              иначе — readonly-текст как раньше. */}
          {initiatorKey !== "" ? (
            <div className="sed-field">
              <span>Инициатор (ОК, только чтение)</span>
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
              Инициатор (ОК, только чтение)
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

          {/* Комментарий к заявке (правая панель макета). */}
          <label className="sed-field">
            Комментарий
            <textarea
              aria-label="Комментарий"
              rows={3}
              value={comment}
              onChange={(e) => {
                markTouched();
                setComment(e.target.value);
              }}
            />
          </label>
        </div>
      </div>

      {/* Маршрут: конструктор блоков (последовательный/параллельный); исполнители
          — сотрудник (поиск AD) либо группа (список групп + состав из AD). */}
      {enterprise && employeeReady && (
        <fieldset className="sed-fieldset sed-mt-12">
          <legend>Маршрут согласования</legend>
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
                        <>
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
                          {adPanelBlock === block.id && (
                            <div className="sed-mt-8 sed-rel">
                              <label className="sed-field">
                                Поиск в AD
                                <input
                                  aria-label="Поиск в AD"
                                  placeholder="ФИО в AD"
                                  value={adQuery}
                                  onChange={(e) => setAdQuery(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === "Escape") closeAdPanel();
                                  }}
                                />
                              </label>
                              {adSearching && <div className="sed-note">Поиск в AD…</div>}
                              {adSearchError && <div className="sed-note">{adSearchError}</div>}
                              {adCandidates.length > 0 && (
                                <ul className="sed-dropdown">
                                  {adCandidates.map((c) => (
                                    <li key={c.sam}>
                                      <button type="button" className="sed-dropdown__item" onClick={() => addStepToBlock(block.id, c)}>
                                        <strong>{c.display_name}</strong> · {c.sam}
                                        {(c.department || c.title) && (
                                          <div className="sed-sub">
                                            {[c.department, c.title].filter(Boolean).join(" · ")}
                                          </div>
                                        )}
                                      </button>
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          )}
                        </>
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
        </fieldset>
      )}

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

// Форма создания заявки для ОК (Задача 3.3): единая форма без стадий.
// Блоки появляются/активируются по зависимостям: предприятие → сотрудник →
// маршрут → «Создать»; невалидное — недоступно (кнопка «Создать» disabled).
// Предприятия — только из API (settings БД), хардкода нет (AGENTS.md п.3).
// Сотрудник: живой поиск в 1С (GET /api/employees); без баз (503) — ручной ввод.
// Маршрут: конструктор блоков (последовательный/параллельный) с исполнителями
// из AD (GET /api/ad/search); телом создания идут blocks, не группы (steps).
import { useEffect, useRef, useState } from "react";
import { ApiHttpError } from "./auth-client";
import {
  createRequest,
  getAdGroupMembers,
  getEmployeeCard,
  getEnterprises,
  getStepGroups,
  searchAd,
  searchEmployees,
  submitRequest,
} from "./requests-client";
import type { AdCandidate, AdGroupMember, EmployeeHit, Enterprise } from "./requests-client";
import type { Role } from "./api-mock";

interface CreateFormProps {
  // Роль (создание — только ОК/админам, гард как в API _is_hr).
  role: Role;
  // Колбэк «грязности» формы: true после первого ввода, false после успешного
  // создания (форма сброшена) — для подтверждения закрытия окна (create-window).
  onDirtyChange?: (dirty: boolean) => void;
  // В окне-попе (?view=create): после успешного создания закрыть окно.
  closeOnCreate?: boolean;
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

// Состояние блока без указанного ключа: удалённый блок не должен оставлять в
// состоянии формы свою группу и её состав.
function omitKey<T>(state: Record<string, T>, key: string): Record<string, T> {
  if (!(key in state)) return state;
  const next = { ...state };
  delete next[key];
  return next;
}

// Форма создания: единый экран, блоки по зависимостям.
export function CreateForm(props: CreateFormProps) {
  const { role, onDirtyChange, closeOnCreate } = props;
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [enterprise, setEnterprise] = useState<string>("");
  const [loadError, setLoadError] = useState<string>("");
  const [created, setCreated] = useState<string>("");
  const [createError, setCreateError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  // Сотрудник: живой поиск в 1С (200 — список) либо ручной ввод (503 — базы не настроены).
  const [empQuery, setEmpQuery] = useState<string>("");
  const [empHits, setEmpHits] = useState<EmployeeHit[]>([]);
  const [empListOpen, setEmpListOpen] = useState<boolean>(false);
  const [empSearching, setEmpSearching] = useState<boolean>(false);
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
  // id блока, для которого открыта панель выбора исполнителя (null — закрыта).
  const [adPanelBlock, setAdPanelBlock] = useState<string | null>(null);
  const [adQuery, setAdQuery] = useState<string>("");
  const [adCandidates, setAdCandidates] = useState<AdCandidate[]>([]);
  const [adSearching, setAdSearching] = useState<boolean>(false);
  const [adSearchError, setAdSearchError] = useState<string>("");
  // Порядковый номер поиска AD: устаревшие ответы отбрасываем.
  const adSeq = useRef(0);

  // Тип исполнителя «Группа»: группы из settings (GET /api/step-groups) и
  // состав выбранной группы из AD (GET /api/ad/groups/{group}/members).
  const [groups, setGroups] = useState<string[]>([]);
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

  // Живой поиск сотрудника: ввод → debounce 300 мс → searchEmployees; старые
  // ответы отбрасываем по searchSeq. 503 или пустой результат — ручной ввод.
  useEffect(() => {
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
      searchEmployees(enterprise, q)
        .then((data) => {
          if (seq !== searchSeq.current) return;
          const hits = data.items ?? [];
          setEmpHits(hits);
          if (hits.length === 0) {
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
    }, 300);
    return () => clearTimeout(timer);
  }, [empQuery, enterprise]);

  // Живой поиск в AD для панели конструктора: debounce 300 мс → searchAd.
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
    }, 300);
    return () => clearTimeout(timer);
  }, [adQuery, adPanelBlock]);

  // Создание — ОК, руководителям ОК и админам (роль hr/hr_admin/admin, как в API _is_hr).
  if (role !== "hr" && role !== "hr_admin" && role !== "admin") {
    return <div role="alert">Создание заявок доступно только ОК.</div>;
  }

  // Готовность формы: предприятие → сотрудник → маршрут (без стадий).
  // Подразделение/должность в 1С могут быть пустыми (уволен/нет кадровых
  // данных) — для создания они необязательны.
  const employeeReady = fio.trim() !== "" && tabNum.trim() !== "";
  const canCreate =
    enterprise !== "" &&
    employeeReady &&
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

  // Конструктор маршрута: добавление/удаление блоков и шагов (→ dirty).

  function addBlock(): void {
    markTouched();
    // Стабильный id блока: состояние группы привязано к нему, а не к индексу.
    const id = `blk-${++blockSeq.current}`;
    setBlocks((prev) => [...prev, { id, mode: "sequential", kind: "user", steps: [] }]);
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

  function setBlockMode(index: number, mode: "sequential" | "parallel"): void {
    markTouched();
    setBlocks((prev) => prev.map((b, i) => (i === index ? { ...b, mode } : b)));
  }

  // Тип исполнителя блока: сотрудник (поиск AD) либо группа (список из settings).
  function setBlockKind(blockId: string, kind: ExecutorKind): void {
    markTouched();
    setBlocks((prev) => prev.map((b) => (b.id === blockId ? { ...b, kind } : b)));
    if (adPanelBlock === blockId) closeAdPanel();
  }

  function removeStep(blockIndex: number, stepIndex: number): void {
    markTouched();
    setBlocks((prev) =>
      prev.map((b, i) =>
        i === blockIndex ? { ...b, steps: b.steps.filter((_, s) => s !== stepIndex) } : b,
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

  // Создание заявки: POST /api/requests; при submit=true — сразу POST /{id}/submit
  // («Отправить на согласование»). При успехе — статус и сброс формы; при
  // ошибке (в т.ч. submit) — текст ошибки, окно не закрывается.
  async function handleCreate(submit: boolean): Promise<void> {
    if (busy) return;
    setCreateError("");
    setCreated("");
    setBusy(true);
    try {
      const result = await createRequest({
        enterprise,
        tab_num: tabNum,
        department,
        position,
        fio,
        blocks: blocks.map((b) => ({
          mode: b.mode,
          // Шаг-группа уходит owner_group + by_group; шаг-сотрудник — sam.
          steps: b.steps.map((s) =>
            s.kind === "group"
              ? { owner_group: s.owner_group ?? s.display_name, resolver: s.resolver }
              : { sam: s.sam },
          ),
        })),
      });
      if (submit) await submitRequest(result.id);
      setCreated(`Заявка ${result.id} создана`);
      setEnterprise("");
      setTabNum("");
      setFio("");
      setDepartment("");
      setPosition("");
      setEmpQuery("");
      setEmpHits([]);
      setEmpListOpen(false);
      setManualMode(false);
      setManualNote("");
      setBlocks([]);
      blockSeq.current = 0;
      groupSeq.current = {};
      setGroupPick({});
      setGroupMembers({});
      closeAdPanel();
      onDirtyChange?.(false);
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

      {loadError && <div role="alert">Ошибка: {loadError}</div>}
      {created && <div role="status">{created}</div>}

      {/* Предприятие из настроек (без хардкод-массивов) — шаг 1 формы. */}
      <label>
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
        <fieldset style={{ marginTop: 12 }}>
          <legend>Сотрудник</legend>
          <div style={{ position: "relative" }}>
            <label>
              Поиск сотрудника
              <input
                aria-label="Поиск сотрудника"
                placeholder="ФИО / табельный №"
                value={empQuery}
                onChange={(e) => {
                  markTouched();
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
            {empListOpen && empHits.length > 0 && (
              <ul
                style={{
                  position: "absolute",
                  zIndex: 10,
                  left: 0,
                  right: 0,
                  margin: 0,
                  padding: 0,
                  listStyle: "none",
                  border: "1px solid var(--sed-border)",
                  background: "var(--sed-surface)",
                  color: "var(--sed-text)",
                  maxHeight: 220,
                  overflowY: "auto",
                }}
              >
                {empHits.map((h) => (
                  <li key={h.key} style={{ margin: 0 }}>
                    <button
                      type="button"
                      onClick={() => pickEmployee(h)}
                      onMouseEnter={(e) => (e.currentTarget as HTMLButtonElement).style.background = "var(--sed-primary-soft)"}
                      onMouseLeave={(e) => (e.currentTarget as HTMLButtonElement).style.background = ""}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        border: "none",
                        background: "transparent",
                        color: "var(--sed-text)",
                        padding: 4,
                        cursor: "pointer",
                      }}
                    >
                      <strong>{h.fio}</strong> · {h.tab_num}
                      {(h.dept || h.position) && (
                        <div style={{ fontSize: "0.85em", opacity: 0.7 }}>
                          {[h.dept, h.position].filter(Boolean).join(" · ")}
                        </div>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {empSearching && <div className="sed-note">Поиск в 1С…</div>}
          {/* Данные сотрудника из 1С — справочные (read-only); подразделение/
              должность могут быть пустыми («—»), создание допустимо без них. */}
          {!manualMode && tabNum !== "" && (
            <fieldset>
              <legend>Данные сотрудника</legend>
              <div className="sed-note">Данные из 1С — справочно, изменить нельзя.</div>
              <div style={{ display: "block", marginTop: 8 }}>
                ФИО: <strong>{fio}</strong>
              </div>
              <div style={{ display: "block", marginTop: 8 }}>
                Табельный №: <strong>{tabNum}</strong>
              </div>
              <div style={{ display: "block", marginTop: 8 }}>
                Подразделение: <strong>{department || "—"}</strong>
              </div>
              <div style={{ display: "block", marginTop: 8 }}>
                Должность: <strong>{position || "—"}</strong>
              </div>
            </fieldset>
          )}
          {/* Ручной режим (503/ничего не найдено): поля редактируемые. */}
          {manualMode && (
            <fieldset>
              <legend>Данные сотрудника (вручную)</legend>
              {manualNote && <div className="sed-note">{manualNote}</div>}
              <label style={{ display: "block", marginTop: 8 }}>
                ФИО
                <input aria-label="ФИО" value={fio} onChange={(e) => { markTouched(); setFio(e.target.value); }} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Табельный №
                <input aria-label="Табельный №" value={tabNum} onChange={(e) => { markTouched(); setTabNum(e.target.value); }} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Подразделение
                <input aria-label="Подразделение" value={department} onChange={(e) => { markTouched(); setDepartment(e.target.value); }} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
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

      {/* Маршрут: конструктор блоков (последовательный/параллельный); исполнители
          — сотрудник (поиск AD) либо группа (список групп + состав из AD). */}
      {enterprise && employeeReady && (
        <fieldset style={{ marginTop: 12 }}>
          <legend>Маршрут согласования</legend>
          <div className="sed-note">
            Конструктор маршрута: блоки с исполнителями — сотрудником из AD или группой.
          </div>
          {groupsError && <div className="sed-note">Группы: {groupsError}</div>}
          {blocks.length === 0 && <div className="sed-note">Добавьте блок и исполнителей.</div>}
          {blocks.map((block, bi) => (
            <div
              key={block.id}
              style={{
                border: "1px solid var(--sed-border)",
                borderRadius: "var(--sed-radius)",
                padding: 8,
                marginTop: 8,
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <strong>Блок {bi + 1}</strong>
                <select
                  aria-label={`Режим блока ${bi + 1}`}
                  value={block.mode}
                  onChange={(e) => setBlockMode(bi, e.target.value as "sequential" | "parallel")}
                >
                  <option value="sequential">Последовательный</option>
                  <option value="parallel">Параллельный</option>
                </select>
                <select
                  aria-label={`Тип исполнителя блока ${bi + 1}`}
                  value={block.kind}
                  onChange={(e) => setBlockKind(block.id, e.target.value as ExecutorKind)}
                >
                  <option value="user">Сотрудник</option>
                  <option value="group">Группа</option>
                </select>
                <button type="button" className="sed-btn" onClick={() => removeBlock(block.id)}>
                  Удалить блок
                </button>
              </div>
              {block.steps.length === 0 && <div className="sed-note">Исполнители не добавлены.</div>}
              <ul style={{ margin: "8px 0", paddingLeft: 20 }}>
                {block.steps.map((s, si) => (
                  <li key={`${s.kind}-${s.sam || s.owner_group}-${si}`}>
                    {s.kind === "group" ? s.display_name : `${s.display_name} (${s.sam})`}
                    <button
                      type="button"
                      aria-label={`Удалить исполнителя ${s.display_name}`}
                      onClick={() => removeStep(bi, si)}
                      style={{ marginLeft: 8 }}
                    >
                      Удалить
                    </button>
                  </li>
                ))}
              </ul>
              {block.kind === "group" ? (
                <div style={{ marginTop: 8 }}>
                  <label>
                    Группа
                    <select
                      aria-label={`Группа блока ${bi + 1}`}
                      value={groupPick[block.id] ?? ""}
                      onChange={(e) => pickGroup(block.id, e.target.value)}
                    >
                      <option value="">— выберите —</option>
                      {groups.map((g) => (
                        <option key={g} value={g}>
                          {g}
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
                          <ul aria-label={`Состав группы ${groupPick[block.id]}`} style={{ margin: "6px 0", paddingLeft: 20 }}>
                            {gm.members.map((m) => (
                              <li key={m.sam}>
                                {m.display_name}
                                {m.mail ? ` · ${m.mail}` : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      <div className="sed-toolbar" style={{ marginTop: 8 }}>
                        <button
                          type="button"
                          className="sed-btn"
                          onClick={() => addGroupToBlock(block.id, groupPick[block.id] ?? "")}
                        >
                          Добавить группу
                        </button>
                      </div>
                    </>
                    );
                  })()}
                </div>
              ) : (
                <>
              <button type="button" className="sed-btn" onClick={() => openAdPanel(block.id)}>
                Добавить исполнителя
              </button>
              {adPanelBlock === block.id && (
                <div style={{ marginTop: 8 }}>
                  <label>
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
                    <ul
                      style={{
                        margin: 0,
                        padding: 0,
                        listStyle: "none",
                        border: "1px solid var(--sed-border)",
                        background: "var(--sed-surface)",
                        color: "var(--sed-text)",
                        maxHeight: 220,
                        overflowY: "auto",
                      }}
                    >
                      {adCandidates.map((c) => (
                        <li key={c.sam} style={{ margin: 0 }}>
                          <button
                            type="button"
                            onClick={() => addStepToBlock(block.id, c)}
                            onMouseEnter={(e) => (e.currentTarget as HTMLButtonElement).style.background = "var(--sed-primary-soft)"}
                            onMouseLeave={(e) => (e.currentTarget as HTMLButtonElement).style.background = ""}
                            style={{
                              display: "block",
                              width: "100%",
                              textAlign: "left",
                              border: "none",
                              background: "transparent",
                              color: "var(--sed-text)",
                              padding: 4,
                              cursor: "pointer",
                            }}
                          >
                            <strong>{c.display_name}</strong> · {c.sam}
                            {(c.department || c.title) && (
                              <div style={{ fontSize: "0.85em", opacity: 0.7 }}>
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
            </div>
          ))}
          <div className="sed-toolbar" style={{ marginTop: 8 }}>
            <button type="button" className="sed-btn" onClick={addBlock}>
              Добавить блок
            </button>
          </div>
        </fieldset>
      )}

      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button
          type="button"
          className="sed-btn sed-btn--ghost"
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
      </div>
      {createError && <div role="alert">{createError}</div>}
    </section>
  );
}

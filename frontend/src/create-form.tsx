// Форма создания заявки для ОК (Задача 3.3): единая форма без стадий.
// Блоки появляются/активируются по зависимостям: предприятие → сотрудник →
// маршрут → «Создать»; невалидное — недоступно (кнопка «Создать» disabled).
// Предприятия — только из API (settings БД), хардкода нет (AGENTS.md п.3).
// Сотрудник: живой поиск в 1С (GET /api/employees); без баз (503) — ручной ввод.
// Маршрут: конструктор блоков (последовательный/параллельный) с исполнителями
// из AD (GET /api/ad/search); телом создания идут blocks, не группы (steps).
import { useEffect, useRef, useState } from "react";
import { ApiHttpError } from "./auth-client";
import { createRequest, getEmployeeCard, getEnterprises, searchAd, searchEmployees } from "./requests-client";
import type { AdCandidate, EmployeeHit, Enterprise } from "./requests-client";
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

// Шаг маршрута в конструкторе: исполнитель из AD (sam — контракт API, остальное — справочно).
interface RouteStep {
  sam: string;
  display_name: string;
  department?: string;
  title?: string;
}

// Блок маршрута: последовательный либо параллельный шаги.
interface RouteBlock {
  mode: "sequential" | "parallel";
  steps: RouteStep[];
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
  // Индекс блока, для которого открыта панель выбора исполнителя (null — закрыта).
  const [adPanelBlock, setAdPanelBlock] = useState<number | null>(null);
  const [adQuery, setAdQuery] = useState<string>("");
  const [adCandidates, setAdCandidates] = useState<AdCandidate[]>([]);
  const [adSearching, setAdSearching] = useState<boolean>(false);
  const [adSearchError, setAdSearchError] = useState<string>("");
  // Порядковый номер поиска AD: устаревшие ответы отбрасываем.
  const adSeq = useRef(0);

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
    setBlocks((prev) => [...prev, { mode: "sequential", steps: [] }]);
  }

  function removeBlock(index: number): void {
    markTouched();
    setBlocks((prev) => prev.filter((_, i) => i !== index));
    if (adPanelBlock === index) closeAdPanel();
  }

  function setBlockMode(index: number, mode: "sequential" | "parallel"): void {
    markTouched();
    setBlocks((prev) => prev.map((b, i) => (i === index ? { ...b, mode } : b)));
  }

  function removeStep(blockIndex: number, stepIndex: number): void {
    markTouched();
    setBlocks((prev) =>
      prev.map((b, i) =>
        i === blockIndex ? { ...b, steps: b.steps.filter((_, s) => s !== stepIndex) } : b,
      ),
    );
  }

  function openAdPanel(index: number): void {
    setAdPanelBlock(index);
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
  function addStepToBlock(blockIndex: number, cand: AdCandidate): void {
    markTouched();
    setBlocks((prev) =>
      prev.map((b, i) =>
        i === blockIndex
          ? {
              ...b,
              steps: [
                ...b.steps,
                { sam: cand.sam, display_name: cand.display_name, department: cand.department, title: cand.title },
              ],
            }
          : b,
      ),
    );
    closeAdPanel();
  }

  // Создание заявки: POST /api/requests; при 201 — статус и сброс формы.
  async function handleCreate(): Promise<void> {
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
          steps: b.steps.map((s) => ({ sam: s.sam })),
        })),
      });
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
                  border: "1px solid var(--sed-border, #999)",
                  background: "var(--sed-surface, #fff)",
                  maxHeight: 220,
                  overflowY: "auto",
                }}
              >
                {empHits.map((h) => (
                  <li key={h.key} style={{ margin: 0 }}>
                    <button
                      type="button"
                      onClick={() => pickEmployee(h)}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        border: "none",
                        background: "transparent",
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

      {/* Маршрут: конструктор блоков (последовательный/параллельный) из AD. */}
      {enterprise && employeeReady && (
        <fieldset style={{ marginTop: 12 }}>
          <legend>Маршрут согласования</legend>
          <div className="sed-note">Конструктор маршрута: блоки с исполнителями из AD.</div>
          {blocks.length === 0 && <div className="sed-note">Добавьте блок и исполнителей.</div>}
          {blocks.map((block, bi) => (
            <div
              key={bi}
              style={{
                border: "1px solid var(--sed-border, #ccc)",
                borderRadius: "var(--sed-radius, 4px)",
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
                <button type="button" className="sed-btn" onClick={() => removeBlock(bi)}>
                  Удалить блок
                </button>
              </div>
              {block.steps.length === 0 && <div className="sed-note">Исполнители не добавлены.</div>}
              <ul style={{ margin: "8px 0", paddingLeft: 20 }}>
                {block.steps.map((s, si) => (
                  <li key={`${s.sam}-${si}`}>
                    {s.display_name} ({s.sam})
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
              <button type="button" className="sed-btn" onClick={() => openAdPanel(bi)}>
                Добавить исполнителя
              </button>
              {adPanelBlock === bi && (
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
                        border: "1px solid var(--sed-border, #999)",
                        background: "var(--sed-surface, #fff)",
                        maxHeight: 220,
                        overflowY: "auto",
                      }}
                    >
                      {adCandidates.map((c) => (
                        <li key={c.sam} style={{ margin: 0 }}>
                          <button
                            type="button"
                            onClick={() => addStepToBlock(bi, c)}
                            style={{
                              display: "block",
                              width: "100%",
                              textAlign: "left",
                              border: "none",
                              background: "transparent",
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
        <button type="button" className="sed-btn" disabled={!canCreate} onClick={handleCreate}>
          {busy ? "Создание…" : "Создать"}
        </button>
      </div>
      {createError && <div role="alert">{createError}</div>}
    </section>
  );
}

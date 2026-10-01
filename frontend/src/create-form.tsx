// Форма создания заявки для ОК (Задача 3.3): единая форма без стадий.
// Блоки появляются/активируются по зависимостям: предприятие → сотрудник →
// маршрут → «Создать»; невалидное — недоступно (кнопка «Создать» disabled).
// Предприятия и группы — только из API (settings БД), хардкода нет (AGENTS.md п.3).
// Сотрудник: поиск в 1С (GET /api/employees); без баз (503) — ручной ввод полей.
import { useEffect, useRef, useState } from "react";
import { ApiHttpError } from "./auth-client";
import { createRequest, getEmployeeCard, getEnterprises, getStepGroups, searchEmployees } from "./requests-client";
import type { EmployeeHit, Enterprise } from "./requests-client";
import type { Role } from "./api-mock";

interface CreateFormProps {
  // Роль (создание — только ОК/админам, гард как в API _is_hr).
  role: Role;
}

// Форма создания: единый экран, блоки по зависимостям.
export function CreateForm(props: CreateFormProps) {
  const { role } = props;
  const [enterprises, setEnterprises] = useState<Enterprise[]>([]);
  const [enterprise, setEnterprise] = useState<string>("");
  const [groups, setGroups] = useState<string[]>([]);
  const [manualGroups, setManualGroups] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string>("");
  const [created, setCreated] = useState<string>("");
  const [createError, setCreateError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  // Сотрудник: поиск в 1С (200 — список) либо ручной ввод (503 — базы не настроены).
  const [empQuery, setEmpQuery] = useState<string>("");
  const [empHits, setEmpHits] = useState<EmployeeHit[]>([]);
  const [empSearching, setEmpSearching] = useState<boolean>(false);
  const [manualMode, setManualMode] = useState<boolean>(false);
  const [manualNote, setManualNote] = useState<string>("");
  const [fio, setFio] = useState<string>("");
  const [tabNum, setTabNum] = useState<string>("");
  const [department, setDepartment] = useState<string>("");
  const [position, setPosition] = useState<string>("");
  // Порядковый номер поиска: устаревшие ответы отбрасываем.
  const searchSeq = useRef(0);

  // Предприятия и группы ручного конструктора — только из API.
  useEffect(() => {
    let alive = true;
    Promise.all([getEnterprises(), getStepGroups()])
      .then(([ents, grps]) => {
        if (!alive) return;
        setEnterprises(ents);
        setGroups(grps);
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(e instanceof Error ? e.message : "Ошибка загрузки данных формы");
      });
    return () => {
      alive = false;
    };
  }, []);

  // Создание — ОК, руководителям ОК и админам (роль hr/hr_admin/admin, как в API _is_hr).
  if (role !== "hr" && role !== "hr_admin" && role !== "admin") {
    return <div role="alert">Создание заявок доступно только ОК.</div>;
  }

  // Название выбранного предприятия (код — в составном ключе сотрудника).
  const enterpriseName = enterprises.find((ent) => ent.code === enterprise)?.name ?? enterprise;

  // Готовность формы: предприятие → сотрудник → маршрут (без стадий).
  const employeeReady =
    fio.trim() !== "" && tabNum.trim() !== "" && department.trim() !== "" && position.trim() !== "";
  const canCreate = enterprise !== "" && employeeReady && manualGroups.length > 0 && !busy;

  // Поиск сотрудника в 1С; без баз (503) или пустой результат — ручной ввод полей.
  function handleSearch(query: string): void {
    setEmpQuery(query);
    const q = query.trim();
    if (q === "") {
      searchSeq.current++;
      setEmpHits([]);
      setManualMode(false);
      setManualNote("");
      return;
    }
    const seq = ++searchSeq.current;
    setEmpSearching(true);
    searchEmployees(enterprise, q)
      .then((data) => {
        if (seq !== searchSeq.current) return;
        const hits = data.items ?? [];
        setEmpHits(hits);
        if (hits.length === 0) {
          setManualMode(true);
          setManualNote("ничего не найдено — введите данные вручную");
        } else {
          setManualMode(false);
          setManualNote("");
        }
        setEmpSearching(false);
      })
      .catch((e: unknown) => {
        if (seq !== searchSeq.current) return;
        setEmpHits([]);
        setEmpSearching(false);
        if (e instanceof ApiHttpError && e.status === 503) {
          setManualMode(true);
          setManualNote("данные 1С не настроены, введите вручную");
        } else {
          setManualNote(e instanceof Error ? e.message : "Ошибка поиска сотрудника");
        }
      });
  }

  // Выбор сотрудника из списка 1С заполняет поля заявки.
  // Подразделение/должность в списке справочника пустые (они в карточке —
  // второй запрос к регистру кадровых данных): догружаем карточкой.
  function pickEmployee(hit: EmployeeHit): void {
    setFio(hit.fio);
    setTabNum(hit.tab_num);
    setDepartment("");
    setPosition("");
    const parts = hit.key.split("|");
    if (parts.length < 3) {
      return; // битый ключ — подразделение/должность ОК введёт вручную
    }
    void getEmployeeCard(enterprise, parts[1], hit.tab_num)
      .then((card) => {
        setDepartment(card.dept ?? "");
        setPosition(card.position ?? "");
      })
      .catch(() => {
        // Карточка недоступна — подразделение/должность ОК введёт вручную.
      });
  }

  // Переключение группы ручного конструктора.
  function toggleGroup(group: string): void {
    setManualGroups((prev) => (prev.includes(group) ? prev.filter((g) => g !== group) : [...prev, group]));
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
        steps: manualGroups.map((g) => ({ owner_group: g })),
      });
      setCreated(`Заявка ${result.id} создана`);
      setEnterprise("");
      setTabNum("");
      setFio("");
      setDepartment("");
      setPosition("");
      setEmpQuery("");
      setEmpHits([]);
      setManualMode(false);
      setManualNote("");
      setManualGroups([]);
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
        <select aria-label="Предприятие" value={enterprise} onChange={(e) => setEnterprise(e.target.value)}>
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
          <label>
            Поиск сотрудника
            <input
              aria-label="Поиск сотрудника"
              placeholder="ФИО / табельный №"
              value={empQuery}
              onChange={(e) => handleSearch(e.target.value)}
            />
          </label>
          {empSearching && <div className="sed-note">Поиск в 1С…</div>}
          {empHits.length > 0 && (
            <label>
              Сотрудник ({enterpriseName})
              <select
                aria-label="Сотрудник"
                value={tabNum}
                onChange={(e) => {
                  const hit = empHits.find((h) => h.tab_num === e.target.value);
                  if (hit) pickEmployee(hit);
                }}
              >
                <option value="">— выберите —</option>
                {empHits.map((h) => (
                  <option key={h.key} value={h.tab_num}>
                    {h.fio} · {h.tab_num}
                  </option>
                ))}
              </select>
            </label>
          )}
          {manualMode && (
            <fieldset>
              <legend>Данные сотрудника (вручную)</legend>
              {manualNote && <div className="sed-note">{manualNote}</div>}
              <label style={{ display: "block", marginTop: 8 }}>
                ФИО
                <input aria-label="ФИО" value={fio} onChange={(e) => setFio(e.target.value)} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Табельный №
                <input aria-label="Табельный №" value={tabNum} onChange={(e) => setTabNum(e.target.value)} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Подразделение
                <input aria-label="Подразделение" value={department} onChange={(e) => setDepartment(e.target.value)} />
              </label>
              <label style={{ display: "block", marginTop: 8 }}>
                Должность
                <input aria-label="Должность" value={position} onChange={(e) => setPosition(e.target.value)} />
              </label>
            </fieldset>
          )}
          {!manualMode && empHits.length === 0 && !empSearching && (
            <div className="sed-note">Введите запрос для поиска в 1С либо укажите данные вручную.</div>
          )}
        </fieldset>
      )}

      {/* Маршрут: активируется, когда сотрудник заполнен. */}
      {enterprise && employeeReady && (
        <fieldset style={{ marginTop: 12 }}>
          <legend>Маршрут согласования</legend>
          <div className="sed-note">Шаблоны маршрутов из настроек не заданы — отметьте группы владельцев вручную.</div>
          {groups.length === 0 && (
            <div className="sed-note">Группы шагов не настроены (GET /api/step-groups пуст).</div>
          )}
          {groups.map((group) => (
            <label key={group} style={{ display: "block" }}>
              <input
                type="checkbox"
                checked={manualGroups.includes(group)}
                onChange={() => toggleGroup(group)}
              />
              {group}
            </label>
          ))}
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
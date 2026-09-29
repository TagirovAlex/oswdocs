// Форма создания заявки для ОК (волна B4).
// Шаги: предприятие → сотрудник → маршрут → печать.
// Все персональные данные ниже — ВЫМЫШЛЕННЫЕ.
import { useMemo, useState } from "react";
import type { Role } from "./api-mock";

interface CreateFormProps {
  // Роль (создание — только ОК).
  role: Role;
}

// Вымышленные предприятия (значения — из настроек, здесь подписи мока).
const ENTERPRISES = ["Завод «Север»", "Филиал «Восток»"] as const;

// Вымышленные сотрудники по предприятиям.
const EMPLOYEES: Record<string, Array<{ tabNum: string; fio: string; position: string }>> = {
  "Завод «Север»": [
    { tabNum: "Т-000201", fio: "Громов Игорь Олегович", position: "Слесарь" },
    { tabNum: "Т-000202", fio: "Лебедева Мария Ивановна", position: "Кладовщик" },
  ],
  "Филиал «Восток»": [
    { tabNum: "Т-000301", fio: "Захаров Николай Павлович", position: "Водитель" },
  ],
};

// Шаблоны маршрута (имена групп — из настроек, здесь подписи мока).
const ROUTE_TEMPLATES = ["Шаблон: линейный персонал", "Шаблон: руководитель", "Ручной конструктор"] as const;

// Форма создания: мастер из четырёх шагов.
export function CreateForm(props: CreateFormProps) {
  const { role } = props;
  const [step, setStep] = useState<number>(0);
  const [enterprise, setEnterprise] = useState<string>("");
  const [tabNum, setTabNum] = useState<string>("");
  const [route, setRoute] = useState<string>(ROUTE_TEMPLATES[0]);
  const [manualGroups, setManualGroups] = useState<string[]>([]);
  const [printVersion, setPrintVersion] = useState<string>("v1");
  const [created, setCreated] = useState<string>("");

  // Сотрудники выбранного предприятия.
  const employees = useMemo(() => (enterprise ? (EMPLOYEES[enterprise] ?? []) : []), [enterprise]);
  // Выбранный сотрудник.
  const selected = useMemo(() => employees.find((e) => e.tabNum === tabNum), [employees, tabNum]);

  // Создание — ОК и админам (роль hr/admin, как в API _is_hr).
  if (role !== "hr" && role !== "admin") {
    return <div role="alert">Создание заявок доступно только ОК.</div>;
  }

  // Переключение группы ручного конструктора.
  function toggleGroup(group: string): void {
    setManualGroups((prev) => (prev.includes(group) ? prev.filter((g) => g !== group) : [...prev, group]));
  }

  // Проверка шага перед переходом дальше.
  function canNext(): boolean {
    if (step === 0) return enterprise !== "";
    if (step === 1) return tabNum !== "";
    if (step === 2) return route !== "Ручной конструктор" || manualGroups.length > 0;
    return true;
  }

  return (
    <section aria-label="Создание заявки">
      <h3>Создание заявки (шаг {step + 1} из 4)</h3>

      {/* Шаг 1: предприятие. */}
      {step === 0 && (
        <label>
          Предприятие
          <select aria-label="Предприятие" value={enterprise} onChange={(e) => setEnterprise(e.target.value)}>
            <option value="">— выберите —</option>
            {ENTERPRISES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* Шаг 2: сотрудник. */}
      {step === 1 && (
        <label>
          Сотрудник ({enterprise})
          <select aria-label="Сотрудник" value={tabNum} onChange={(e) => setTabNum(e.target.value)}>
            <option value="">— выберите —</option>
            {employees.map((e) => (
              <option key={e.tabNum} value={e.tabNum}>
                {e.fio} · {e.tabNum} · {e.position}
              </option>
            ))}
          </select>
        </label>
      )}

      {/* Шаг 3: маршрут. */}
      {step === 2 && (
        <fieldset>
          <legend>Маршрут согласования</legend>
          {ROUTE_TEMPLATES.map((name) => (
            <label key={name} style={{ display: "block" }}>
              <input type="radio" name="route" value={name} checked={route === name} onChange={() => setRoute(name)} />
              {name}
            </label>
          ))}
          {route === "Ручной конструктор" && (
            <div style={{ marginTop: 8 }}>
              <div className="sed-note">Отметьте группы владельцев (замена руководителя — флажком):</div>
              {["SED_STEP_BUH", "SED_STEP_SEC", "SED_STEP_IT"].map((group) => (
                <label key={group} style={{ display: "block" }}>
                  <input
                    type="checkbox"
                    checked={manualGroups.includes(group)}
                    onChange={() => toggleGroup(group)}
                  />
                  {group}
                </label>
              ))}
            </div>
          )}
        </fieldset>
      )}

      {/* Шаг 4: печать. */}
      {step === 3 && (
        <div>
          <p>
            Сотрудник: {selected?.fio} ({selected?.tabNum}), {enterprise}. Маршрут: {route}
            {route === "Ручной конструктор" ? ` (${manualGroups.join(", ")})` : ""}.
          </p>
          <label>
            Версия бегунка
            <select aria-label="Версия бегунка" value={printVersion} onChange={(e) => setPrintVersion(e.target.value)}>
              <option value="v1">v1 (первая печать)</option>
              <option value="v2">v2 (повторная печать)</option>
            </select>
          </label>
          <div style={{ marginTop: 8 }}>
            <button
              type="button"
              className="sed-btn"
              onClick={() => setCreated(`REQ-100 (черновик, печать ${printVersion})`)}
            >
              Создать и отправить на печать
            </button>
            {created && <div role="status">Заявка создана: {created}</div>}
          </div>
        </div>
      )}

      {/* Навигация мастера. */}
      <div className="sed-toolbar" style={{ marginTop: 12 }}>
        <button type="button" className="sed-btn sed-btn--ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>
          Назад
        </button>
        {step < 3 && (
          <button type="button" className="sed-btn" disabled={!canNext()} onClick={() => setStep(step + 1)}>
            Далее
          </button>
        )}
      </div>
    </section>
  );
}

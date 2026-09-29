// Полная карточка сотрудника для ОК/админа (волна B4).
// Блоки: 1С (только ОК) / AD (только для уведомлений) / маршрут / документы / история.
// Все персональные данные ниже — ВЫМЫШЛЕННЫЕ.
import { useEffect, useState } from "react";
import { mockApi } from "./api-mock";
import type { EmployeeBrief, EmployeeFull, Role } from "./api-mock";

interface EmployeeCardProps {
  // Идентификатор заявки.
  requestId: string;
  // Роль (полная карточка — только hr/admin; владельцу — подсказка без ПДн).
  role: Role;
}

// Шаг маршрута (вымышленные данные уровня мока B2).
interface RouteStep {
  order: number;
  ownerGroup: string;
  status: string;
  doneBy: string;
  dueDate: string;
}

// Документ бегунка (версии v1/v2, уровень мока B3).
interface DocItem {
  version: string;
  format: string;
  updatedAt: string;
}

// Запись истории (уровень audit_log, только чтение).
interface HistoryItem {
  at: string;
  author: string;
  action: string;
}

// Вымышленный маршрут по заявкам.
const ROUTE_FIXTURE: Record<string, RouteStep[]> = {
  "REQ-001": [
    { order: 1, ownerGroup: "SED_STEP_BUH", status: "На согласовании", doneBy: "—", dueDate: "2026-10-05" },
    { order: 2, ownerGroup: "SED_STEP_SEC", status: "Ожидает", doneBy: "—", dueDate: "2026-10-07" },
    { order: 3, ownerGroup: "SED_HR", status: "Ожидает", doneBy: "—", dueDate: "2026-10-09" },
  ],
  "REQ-002": [
    { order: 1, ownerGroup: "SED_STEP_SEC", status: "На согласовании", doneBy: "—", dueDate: "2026-10-06" },
    { order: 2, ownerGroup: "SED_HR", status: "Ожидает", doneBy: "—", dueDate: "2026-10-08" },
  ],
  "REQ-003": [
    { order: 1, ownerGroup: "SED_HR", status: "На доработке", doneBy: "ОК", dueDate: "2026-10-03" },
  ],
};

// Вымышленные документы бегунка.
const DOCS_FIXTURE: Record<string, DocItem[]> = {
  "REQ-001": [
    { version: "v1", format: "DOCX→PDF", updatedAt: "2026-09-28" },
    { version: "v2", format: "DOCX→PDF", updatedAt: "2026-09-29" },
  ],
  "REQ-002": [{ version: "v1", format: "DOCX→PDF", updatedAt: "2026-09-29" }],
  "REQ-003": [{ version: "v1", format: "DOCX→PDF", updatedAt: "2026-09-27" }],
};

// Вымышленная история заявки.
const HISTORY_FIXTURE: Record<string, HistoryItem[]> = {
  "REQ-001": [
    { at: "2026-09-28", author: "ОК", action: "Заявка создана" },
    { at: "2026-09-28", author: "Система", action: "Маршрут назначен: Бухгалтерия" },
    { at: "2026-09-29", author: "Система", action: "Бегунок v2 сформирован" },
  ],
  "REQ-002": [{ at: "2026-09-29", author: "ОК", action: "Заявка создана" }],
  "REQ-003": [
    { at: "2026-09-27", author: "ОК", action: "Заявка создана" },
    { at: "2026-09-28", author: "ОК", action: "Возвращена на доработку" },
  ],
};

// Стиль заголовка блока.
const blockTitle: React.CSSProperties = { margin: "12px 0 6px" };

// Полная карточка: пять блоков.
export function EmployeeCard(props: EmployeeCardProps) {
  const { requestId, role } = props;
  const [card, setCard] = useState<EmployeeFull | EmployeeBrief | null>(null);
  const [error, setError] = useState<string>("");

  // Загрузка карточки (ролевая обрезка — уже в моке).
  useEffect(() => {
    let alive = true;
    mockApi
      .getEmployee(requestId, role)
      .then((data) => {
        if (alive) {
          setCard(data);
          setError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setCard(null);
          setError(e instanceof Error ? e.message : "Ошибка загрузки карточки");
        }
      });
    return () => {
      alive = false;
    };
  }, [requestId, role]);

  if (error) return <div role="alert">Ошибка: {error}</div>;
  if (!card) return <div className="sed-note">Загрузка карточки…</div>;

  // Владельцу полная карточка недоступна: только подсказка без ПДн.
  if (card.kind === "brief") {
    return (
      <section aria-label="Карточка заявки">
        <h3>Заявка {card.requestId}</h3>
        <div className="sed-note">
          Полная карточка доступна только ОК. Владельцу показана урезанная карточка без ПДн
          (см. экран владельца).
        </div>
      </section>
    );
  }

  const steps = ROUTE_FIXTURE[card.requestId] ?? [];
  const docs = DOCS_FIXTURE[card.requestId] ?? [];
  const history = HISTORY_FIXTURE[card.requestId] ?? [];

  return (
    <section aria-label="Карточка сотрудника">
      <h3>
        Карточка: {card.fio} ({card.requestId})
      </h3>

      {/* Блок 1С — только ОК (истина при расхождении — 1С). */}
      <h4 style={blockTitle}>Блок 1С (только ОК)</h4>
      <table className="sed-table" aria-label="Данные 1С">
        <tbody>
          <tr>
            <td>Предприятие</td>
            <td>{card.enterprise}</td>
          </tr>
          <tr>
            <td>Табельный №</td>
            <td>{card.tabNum}</td>
          </tr>
          <tr>
            <td>ФИО</td>
            <td>{card.fio}</td>
          </tr>
          <tr>
            <td>Подразделение</td>
            <td>{card.department}</td>
          </tr>
          <tr>
            <td>Должность</td>
            <td>{card.position}</td>
          </tr>
          <tr>
            <td>Дата приёма</td>
            <td>{card.hireDate}</td>
          </tr>
          <tr>
            <td>Остаток отпуска</td>
            <td>{card.vacationBalance} дн. (только ОК)</td>
          </tr>
        </tbody>
      </table>

      {/* Блок AD — только для уведомлений, чтения. */}
      <h4 style={blockTitle}>Блок AD (только для уведомлений)</h4>
      <table className="sed-table" aria-label="Данные AD">
        <tbody>
          <tr>
            <td>Логин (sam)</td>
            <td>{card.sam}</td>
          </tr>
          <tr>
            <td>Руководитель</td>
            <td>{card.manager}</td>
          </tr>
          <tr>
            <td>Почта</td>
            <td>{card.mail}</td>
          </tr>
        </tbody>
      </table>

      {/* Блок маршрута. */}
      <h4 style={blockTitle}>Маршрут согласования</h4>
      <table className="sed-table" aria-label="Маршрут">
        <thead>
          <tr>
            <th>№</th>
            <th>Группа владельцев</th>
            <th>Статус</th>
            <th>Срок</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s) => (
            <tr key={s.order}>
              <td>{s.order}</td>
              <td>{s.ownerGroup}</td>
              <td>{s.status}</td>
              <td>{s.dueDate}</td>
            </tr>
          ))}
          {steps.length === 0 && (
            <tr>
              <td colSpan={4}>Маршрут не назначен</td>
            </tr>
          )}
        </tbody>
      </table>

      {/* Блок документов. */}
      <h4 style={blockTitle}>Документы (бегунок)</h4>
      <ul>
        {docs.map((d) => (
          <li key={d.version}>
            Бегунок {d.version} · {d.format} · {d.updatedAt}
          </li>
        ))}
        {docs.length === 0 && <li>Документов нет</li>}
      </ul>

      {/* Блок истории. */}
      <h4 style={blockTitle}>История</h4>
      <ul>
        {history.map((h, i) => (
          <li key={i}>
            {h.at} · {h.author} · {h.action}
          </li>
        ))}
        {history.length === 0 && <li>Событий нет</li>}
      </ul>
    </section>
  );
}

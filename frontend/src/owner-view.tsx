// Урезанная карточка владельца (волна B4).
// Показывает только обезличенные поля мока: без ФИО, таб.№, почты, руководителя.
// Все персональные данные ниже — ВЫМЫШЛЕННЫЕ (маски вида «Сотрудник № …»).
import { useEffect, useState } from "react";
import { mockApi } from "./api-mock";
import type { EmployeeBrief } from "./api-mock";

interface OwnerViewProps {
  // Идентификатор заявки (только своя задача владельца).
  requestId: string;
}

// Урезанная карточка + решение владельца по своему шагу.
export function OwnerView(props: OwnerViewProps) {
  const { requestId } = props;
  const [card, setCard] = useState<EmployeeBrief | null>(null);
  const [error, setError] = useState<string>("");
  const [comment, setComment] = useState<string>("");
  const [commentError, setCommentError] = useState<string>("");
  const [decision, setDecision] = useState<string>("");

  // Загрузка урезанной карточки (мока отдаёт только brief).
  useEffect(() => {
    let alive = true;
    mockApi
      .getEmployee(requestId, "owner")
      .then((data) => {
        if (alive) {
          // Владелец всегда получает brief; чужое здесь невозможно.
          if (data.kind === "brief") setCard(data);
          setError("");
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setCard(null);
          setError(e instanceof Error ? e.message : "Ошибка загрузки задачи");
        }
      });
    return () => {
      alive = false;
    };
  }, [requestId]);

  if (error) return <div role="alert">Ошибка: {error}</div>;
  if (!card) return <div className="sed-note">Загрузка задачи…</div>;

  // Согласование: комментарий опционален. Отказ/возврат: комментарий обязателен.
  function submit(kind: "approved" | "returned"): void {
    if (kind === "returned" && comment.trim() === "") {
      setCommentError("Для возврата комментарий обязателен");
      return;
    }
    setCommentError("");
    setDecision(kind === "approved" ? "Согласовано" : "Возвращено на доработку");
  }

  return (
    <section aria-label="Задача владельца">
      <h3>
        Моя задача: {card.employeeLabel} ({card.requestId})
      </h3>
      {/* Только обезличенные поля — ПДн сюда не попадают по построению мока. */}
      <table className="sed-table" aria-label="Урезанная карточка">
        <tbody>
          <tr>
            <td>Сотрудник</td>
            <td>{card.employeeLabel}</td>
          </tr>
          <tr>
            <td>Предприятие</td>
            <td>{card.enterprise}</td>
          </tr>
          <tr>
            <td>Статус</td>
            <td>{card.status}</td>
          </tr>
          <tr>
            <td>Мой шаг</td>
            <td>{card.step}</td>
          </tr>
          <tr>
            <td>Срок</td>
            <td>{card.dueDate}</td>
          </tr>
        </tbody>
      </table>

      {/* Решение владельца по своему шагу. */}
      <h4>Моё решение</h4>
      <label>
        Комментарий (обязателен при возврате)
        <input
          aria-label="Комментарий"
          placeholder="Комментарий"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </label>
      {commentError && <div role="alert">{commentError}</div>}
      <div className="sed-toolbar" style={{ marginTop: 8 }}>
        <button type="button" className="sed-btn" onClick={() => submit("approved")}>
          Согласовать
        </button>
        <button type="button" className="sed-btn sed-btn--ghost" onClick={() => submit("returned")}>
          Вернуть
        </button>
      </div>
      {decision && <div role="status">Решение записано: {decision}</div>}
    </section>
  );
}

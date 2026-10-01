// Окно карточки заявки (?view=request&id=…, Задача 3): только карточка без
// шапки/сайдбара. Авторизация — общий токен localStorage (см. windows.tsx).
import { RequestCard } from "./request-card";
import type { Role } from "./api-mock";

interface RequestWindowProps {
  requestId: string;
  role: Role;
}

export function RequestWindow(props: RequestWindowProps) {
  const { requestId, role } = props;
  return (
    <div className="sed-shell">
      {/* Верхняя полоса попапа: закрыть окно. В карточке нет несохранённых
          правок (отметки отправляются сразу) — подтверждение не нужно. */}
      <div className="sed-toolbar">
        <button type="button" className="sed-btn" onClick={() => window.close()}>
          Закрыть
        </button>
      </div>
      <main className="sed-content" style={{ maxWidth: 940, margin: "0 auto", padding: 16 }}>
        <RequestCard requestId={requestId} role={role} />
      </main>
    </div>
  );
}

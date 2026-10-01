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
      <main className="sed-content" style={{ maxWidth: 940, margin: "0 auto", padding: 16 }}>
        <RequestCard requestId={requestId} role={role} />
        {/* Кнопка «Закрыть» — внизу содержимого (как в карточке сотрудника),
            не в верхней липкой полосе. Несохранённых правок в карточке нет:
            отметки отправляются сразу. */}
        <div className="sed-toolbar" style={{ marginTop: 12 }}>
          <button type="button" className="sed-btn" onClick={() => window.close()}>
            Закрыть
          </button>
        </div>
      </main>
    </div>
  );
}

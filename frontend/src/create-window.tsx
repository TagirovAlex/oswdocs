// Окно создания заявки (?view=create, Задача 3): только форма без
// шапки/сайдбара. Авторизация — общий токен localStorage.
import { useState } from "react";
import { CreateForm } from "./create-form";
import type { Role } from "./api-mock";

interface CreateWindowProps {
  role: Role;
}

export function CreateWindow(props: CreateWindowProps) {
  const { role } = props;
  // «Грязность» формы: при несохранённых изменениях закрытие требует подтверждения.
  const [dirty, setDirty] = useState<boolean>(false);

  // Закрыть окно: при dirty — подтверждение (OK → закрыть, отмена — ничего).
  function handleClose(): void {
    if (dirty) {
      if (window.confirm("Есть несохранённые данные. Закрыть без сохранения?")) {
        window.close();
      }
      return;
    }
    window.close();
  }

  return (
    <div className="sed-shell">
      {/* Верхняя полоса попапа: закрыть окно с подтверждением при dirty. */}
      <div className="sed-toolbar">
        <button type="button" className="sed-btn" onClick={handleClose}>
          Закрыть
        </button>
      </div>
      <main className="sed-content" style={{ maxWidth: 940, margin: "0 auto", padding: 16 }}>
        <CreateForm role={role} onDirtyChange={setDirty} />
      </main>
    </div>
  );
}

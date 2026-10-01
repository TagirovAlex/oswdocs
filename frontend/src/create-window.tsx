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
      {/* Верхняя полоса попапа: закрыть окно с подтверждением при dirty.
          Полоса — на всю ширину, кнопка справа, при прокрутке остаётся сверху. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          gap: 8,
          padding: "10px 16px",
          background: "var(--sed-surface)",
          borderBottom: "1px solid var(--sed-border)",
          position: "sticky",
          top: 0,
          zIndex: 5,
        }}
      >
        <button type="button" className="sed-btn" onClick={handleClose}>
          Закрыть
        </button>
      </div>
      <main className="sed-content" style={{ maxWidth: 940, margin: "0 auto", padding: 16 }}>
        <CreateForm role={role} onDirtyChange={setDirty} closeOnCreate />
      </main>
    </div>
  );
}

// Окно создания заявки (?view=create, Задача 3): только форма без
// шапки/сайдбара. Авторизация — общий токен localStorage.
import { useState } from "react";
import { CreateForm } from "./create-form";
import type { Role } from "./api-mock";

interface CreateWindowProps {
  role: Role;
  // Права из /auth/me (блоки). Создание заявки — блок request_create.
  blocks?: string[];
}

export function CreateWindow(props: CreateWindowProps) {
  const { role, blocks } = props;
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
      <main className="sed-content sed-window">
        {/* Кнопка «Закрыть» — в нижнем тулбаре самой формы, в одну строку
            с Создать/Отмена; при несохранённых данных — с подтверждением. */}
        <CreateForm
          role={role}
          blocks={blocks}
          onDirtyChange={setDirty}
          closeOnCreate
          onClose={handleClose}
        />
      </main>
    </div>
  );
}

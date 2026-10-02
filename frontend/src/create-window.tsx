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
      <main className="sed-content sed-window">
        <CreateForm role={role} onDirtyChange={setDirty} closeOnCreate />
        {/* Кнопка «Закрыть» — внизу содержимого (как в карточке сотрудника),
            не в верхней липкой полосе; при несохранённых данных — с подтверждением. */}
        <div className="sed-toolbar sed-mt-12">
          <button type="button" className="sed-btn" onClick={handleClose}>
            Закрыть
          </button>
        </div>
      </main>
    </div>
  );
}

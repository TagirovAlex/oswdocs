// Окно создания заявки (?view=create, Задача 3): только форма без
// шапки/сайдбара. Авторизация — общий токен localStorage.
import { CreateForm } from "./create-form";
import type { Role } from "./api-mock";

interface CreateWindowProps {
  role: Role;
}

export function CreateWindow(props: CreateWindowProps) {
  const { role } = props;
  return (
    <div className="sed-shell">
      <main className="sed-content" style={{ maxWidth: 940, margin: "0 auto", padding: 16 }}>
        <CreateForm role={role} />
      </main>
    </div>
  );
}
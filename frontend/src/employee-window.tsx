// Окно карточки сотрудника (?view=employee&key=enterprise|base_code|tab_num,
// Задача 3): только карточка без шапки/сайдбара. Авторизация — общий токен.
import { EmployeeCardView } from "./employee-card-view";
import type { Role } from "./api-mock";

interface EmployeeWindowProps {
  employeeKey: string;
  role: Role;
}

export function EmployeeWindow(props: EmployeeWindowProps) {
  const { employeeKey, role } = props;
  const parts = employeeKey.split("|");
  if (parts.length < 3) {
    return <div role="alert">Некорректный ключ карточки сотрудника: {employeeKey}</div>;
  }
  return (
    <div className="sed-shell">
      <main className="sed-content" style={{ maxWidth: 940, margin: "0 auto", padding: 16 }}>
        <EmployeeCardView enterprise={parts[0]} baseCode={parts[1]} tabNum={parts[2]} role={role} />
      </main>
    </div>
  );
}
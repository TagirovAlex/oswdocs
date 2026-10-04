// Точка входа: тема + выбор экрана по наличию сессии.
// Нет токена — экран логина; токен есть — каркас с ролью из /auth/me;
// при 401 из /auth/me — снова экран логина (сессия истекла).
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { clearToken, getToken, logout, me } from "./auth-client";
import type { AuthUser } from "./auth-client";
import { SedLayout } from "./layout";
import { LoginScreen } from "./login-screen";
import { ThemeProvider } from "./theme";
import { CreateWindow } from "./create-window";
import { EmployeeWindow } from "./employee-window";
import { RequestWindow } from "./request-window";
import { AttachmentWindow } from "./attachment-view";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Нет корневого элемента #root");

// Лёгкий роутинг окон (Задача 3): query-параметр ?view= без роутера.
// view=request&id=… — карточка заявки; view=employee&key=… — карточка
// сотрудника (enterprise|base_code|tab_num); view=create — создание заявки;
// view=attachment&id=…&name=…&mime=… — просмотр вложения с печатью.
type View =
  | { view: "main" }
  | { view: "request"; id: string }
  | { view: "employee"; key: string }
  | { view: "attachment"; id: string; name: string; mime: string | null }
  | { view: "create" };

function parseView(): View {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view");
  if (view === "request") {
    const id = params.get("id");
    if (id) return { view: "request", id };
  }
  if (view === "employee") {
    const key = params.get("key");
    if (key) return { view: "employee", key };
  }
  if (view === "attachment") {
    const id = params.get("id");
    if (id) {
      return { view: "attachment", id, name: params.get("name") ?? "Файл", mime: params.get("mime") };
    }
  }
  if (view === "create") return { view: "create" };
  return { view: "main" };
}

// Приложение: экран логина или каркас в зависимости от сессии.
function App() {
  const [auth, setAuth] = useState<AuthUser | null>(null);
  // Пока проверяем токен через /auth/me, показываем заглушку.
  const [checking, setChecking] = useState<boolean>(true);

  // Восстановление сессии по сохранённому токену.
  useEffect(() => {
    let alive = true;
    if (!getToken()) {
      setChecking(false);
      return;
    }
    me()
      .then((user) => {
        if (alive) {
          setAuth(user);
          setChecking(false);
        }
      })
      .catch(() => {
        // 401 и прочие ошибки сессии — сброс к экрану логина.
        if (alive) {
          clearToken();
          setAuth(null);
          setChecking(false);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  // Выход: очистка токена и возврат на экран логина.
  function handleLogout(): void {
    logout();
    setAuth(null);
    setChecking(false);
  }

  // Ожидание проверки сессии.
  if (checking) return <div className="sed-note">Проверка сессии…</div>;
  // Нет сессии — экран логина.
  if (!auth) return <LoginScreen onSuccess={setAuth} />;
  // Сессия есть — каркас с ролью из /auth/me; окна-попы — по ?view= (без шапки).
  const route = parseView();
  if (route.view === "request") return <RequestWindow requestId={route.id} role={auth.role} />;
  if (route.view === "employee") return <EmployeeWindow employeeKey={route.key} role={auth.role} />;
  if (route.view === "attachment") {
    return <AttachmentWindow attachmentId={route.id} fileName={route.name} mime={route.mime} />;
  }
  if (route.view === "create") return <CreateWindow role={auth.role} />;
  return <SedLayout role={auth.role} onLogout={handleLogout} />;
}

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
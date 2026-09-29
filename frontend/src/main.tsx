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

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Нет корневого элемента #root");

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
  // Сессия есть — каркас с ролью из /auth/me.
  return <SedLayout role={auth.role} onLogout={handleLogout} />;
}

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
);
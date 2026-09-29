// Экран логина (волна A3): доменная учётка через /auth/login.
// Ошибки: 401 — неверные данные или нет доступа, 503 — AD недоступен.
import { useState } from "react";
import type { FormEvent } from "react";
import { ApiHttpError, login } from "./auth-client";
import type { AuthUser } from "./auth-client";

interface LoginScreenProps {
  // Успешный вход: наверх уходит пользователь из /auth/login.
  onSuccess: (auth: AuthUser) => void;
}

// Форма входа: логин/пароль, кнопка отправки, текст ошибки.
export function LoginScreen(props: LoginScreenProps) {
  const [loginName, setLoginName] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  // Отправка формы: вызов /auth/login, при 401/503 — понятный текст ошибки.
  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const auth = await login(loginName.trim(), password);
      setPassword("");
      props.onSuccess(auth);
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        setError("Неверный логин/пароль или нет доступа");
      } else if (err instanceof ApiHttpError && err.status === 503) {
        setError("AD недоступен");
      } else {
        setError("Не удалось войти. Повторите попытку позже");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="sed-login" onSubmit={submit} aria-label="Вход в систему">
      <h1>СЭД — Увольнение</h1>
      <label>
        Логин (доменная учётка)
        <input
          aria-label="Логин"
          value={loginName}
          onChange={(e) => setLoginName(e.target.value)}
          autoComplete="username"
        />
      </label>
      <label>
        Пароль
        <input
          aria-label="Пароль"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
      </label>
      <button type="submit" className="sed-btn" disabled={busy}>
        {busy ? "Вход…" : "Войти"}
      </button>
      {error && <div role="alert">{error}</div>}
    </form>
  );
}
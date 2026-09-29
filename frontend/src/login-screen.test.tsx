// Тесты экрана логина: форма, обработка 401/503, onSuccess при успехе.
// fetch подменяется фейком — реальный /auth/login не вызывается.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginScreen } from "./login-screen";

// Фейковый ответ fetch (достаточно полей, что использует клиент).
function fakeResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

// Заполнение формы логина.
function fillLogin(login: string, password: string): void {
  fireEvent.change(screen.getByLabelText("Логин"), { target: { value: login } });
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: password } });
}

// Отправка формы.
function submitLogin(): void {
  fireEvent.click(screen.getByRole("button", { name: "Войти" }));
}

describe("LoginScreen", () => {
  // Форма логина рендерится.
  it("показывает форму логина и кнопку входа", () => {
    render(<LoginScreen onSuccess={vi.fn()} />);
    expect(screen.getByLabelText("Логин")).toBeInTheDocument();
    expect(screen.getByLabelText("Пароль")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Войти" })).toBeInTheDocument();
  });

  // Успешный вход: onSuccess с пользователем, токен сохранён.
  it("успешный вход вызывает onSuccess с пользователем", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        fakeResponse(200, {
          token: "token-123",
          user: { sam: "petrov.pp", fio: "Петров Пётр", groups: ["SED_HR"], role: "hr" },
        }),
      ),
    );
    const onSuccess = vi.fn();
    render(<LoginScreen onSuccess={onSuccess} />);
    fillLogin("petrov.pp", "secret");
    submitLogin();

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ sam: "petrov.pp", role: "hr" }));
    expect(localStorage.getItem("sed_token")).toBe("token-123");
  });

  // 401: понятное сообщение, onSuccess не вызывается.
  it("при 401 показывает ошибку «неверный логин/пароль или нет доступа»", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(401, {})));
    const onSuccess = vi.fn();
    render(<LoginScreen onSuccess={onSuccess} />);
    fillLogin("petrov.pp", "wrong");
    submitLogin();

    await waitFor(() =>
      expect(screen.getByText("Неверный логин/пароль или нет доступа")).toBeInTheDocument(),
    );
    expect(onSuccess).not.toHaveBeenCalled();
  });

  // 503: сообщение о недоступности AD.
  it("при 503 показывает «AD недоступен»", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(503, {})));
    render(<LoginScreen onSuccess={vi.fn()} />);
    fillLogin("petrov.pp", "secret");
    submitLogin();

    await waitFor(() => expect(screen.getByText("AD недоступен")).toBeInTheDocument());
  });
});
// Тесты клиента аутентификации: /auth/login, /auth/me, выход.
// Сеть не нужна — fetch подменяется фейком, токен — в localStorage.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError, TOKEN_KEY, clearToken, getToken, login, logout, me } from "./auth-client";

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

describe("authClient", () => {
  // Успешный вход: POST с JSON-телом, токен сохранён, пользователь возвращён.
  it("login сохраняет токен и возвращает пользователя", async () => {
    const fetchMock = vi.fn(async () =>
      fakeResponse(200, {
        token: "token-123",
        user: { sam: "petrov.pp", fio: "Петров Пётр", groups: ["SED_HR"], role: "hr" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = await login("petrov.pp", "secret");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: "petrov.pp", password: "secret" }),
      }),
    );
    expect(user.sam).toBe("petrov.pp");
    expect(user.role).toBe("hr");
    expect(getToken()).toBe("token-123");
  });

  // Неверные данные / нет групп: 401 без записи токена.
  it("login при 401 бросает ApiHttpError и не пишет токен", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(401, {})));
    await expect(login("petrov.pp", "wrong")).rejects.toMatchObject({ status: 401 });
    expect(getToken()).toBeNull();
  });

  // AD недоступен: 503.
  it("login при 503 бросает ApiHttpError со статусом 503", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(503, {})));
    await expect(login("petrov.pp", "secret")).rejects.toMatchObject({ status: 503 });
  });

  // me() шлёт Bearer-токен и возвращает пользователя.
  it("me шлёт Bearer-токен и возвращает пользователя", async () => {
    localStorage.setItem(TOKEN_KEY, "token-123");
    const fetchMock = vi.fn(async () =>
      fakeResponse(200, { sam: "petrov.pp", groups: ["SED_ADMINS"], role: "admin" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = await me();

    expect(user.role).toBe("admin");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/me",
      expect.objectContaining({ headers: { Authorization: "Bearer token-123" } }),
    );
  });

  // me() без токена — 401 без запроса к серверу.
  it("me без токена бросает 401", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(me()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // Истёкшая сессия: 401 из /auth/me.
  it("me при 401 бросает ApiHttpError", async () => {
    localStorage.setItem(TOKEN_KEY, "stale");
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(401, {})));
    await expect(me()).rejects.toBeInstanceOf(ApiHttpError);
  });

  // Выход очищает токен.
  it("logout очищает токен", () => {
    localStorage.setItem(TOKEN_KEY, "token-123");
    logout();
    expect(getToken()).toBeNull();
  });

  // Прямая очистка токена.
  it("clearToken убирает токен", () => {
    localStorage.setItem(TOKEN_KEY, "token-123");
    clearToken();
    expect(getToken()).toBeNull();
  });
});
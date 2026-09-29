// Клиент доменной аутентификации (волна A3): /auth/login, /auth/me, выход.
// Токен сессии — в localStorage (ключ sed_token); пароли нигде не хранятся.
import type { Role } from "./api-mock";

// Ключ хранения токена сессии в браузере.
export const TOKEN_KEY = "sed_token";

// Пользователь из /auth/login и /auth/me (обрезка ПДн — на стороне API).
export interface AuthUser {
  // Доменный логин.
  sam: string;
  // ФИО (у владельца может отсутствовать — урезанная карточка).
  fio?: string;
  // Группы AD пользователя (memberOf).
  groups: string[];
  // Роль по матрице README п.1.
  role: Role;
}

// Ошибка ответа API с HTTP-статусом (для разбора 401/503 на экранах).
export class ApiHttpError extends Error {
  // Код ответа сервера.
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiHttpError";
    this.status = status;
  }
}

// Текущий токен сессии (null, если входа ещё не было).
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

// Сохранить токен после успешного входа.
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

// Очистить токен (выход или истёкшая сессия).
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// POST /auth/login: вход доменной учёткой; токен сохраняется в localStorage.
export async function login(login: string, password: string): Promise<AuthUser> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login, password }),
  });
  if (res.status === 401) {
    throw new ApiHttpError(401, "Неверный логин/пароль или нет доступа");
  }
  if (res.status === 503) {
    throw new ApiHttpError(503, "AD недоступен");
  }
  if (!res.ok) {
    throw new ApiHttpError(res.status, "Ошибка входа");
  }
  const data = (await res.json()) as { token: string; user: AuthUser };
  setToken(data.token);
  return data.user;
}

// GET /auth/me: пользователь текущей сессии (Bearer-токен из localStorage).
export async function me(): Promise<AuthUser> {
  const token = getToken();
  if (!token) {
    throw new ApiHttpError(401, "Нет токена");
  }
  const res = await fetch("/api/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    throw new ApiHttpError(401, "Сессия истекла");
  }
  if (!res.ok) {
    throw new ApiHttpError(res.status, "Ошибка загрузки пользователя");
  }
  return (await res.json()) as AuthUser;
}

// Выход: очистка токена сессии на клиенте (серверный logout — на волне B).
export function logout(): void {
  clearToken();
}
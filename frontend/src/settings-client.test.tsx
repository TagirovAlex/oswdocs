// Тесты клиента справочника заместителей: форма ответа и разбор ошибок.
// Сеть не нужна — fetch подменяется фейком, токен — в localStorage.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "./auth-client";
import { getSubstitutes } from "./settings-client";

// Фейковый ответ fetch (достаточно полей, что использует клиент).
function fakeResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

// Вымышленная запись справочника (логины и даты — не боевые).
const RECORD = {
  id: 1,
  principal_sam: "sotrudnik.osnovnoy",
  deputy_sam: "zameshchitel.vymyshlennyy",
  valid_from: "2026-10-01",
  valid_to: null,
  notify: true,
  note: "Вымышленная причина замещения",
  created_by: "adm.vymyshlennyy",
  created_at: "2026-10-01T09:00:00+00:00",
  updated_at: null,
};

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-123");
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("getSubstitutes", () => {
  // Контракт формы ответа: список приходит МАССИВОМ. Здесь клиент когда-то ждал
  // {items: …}, и вкладка «Заместители» молча показывала пустую таблицу.
  it("читает массив списка (не объект с items)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(200, [RECORD])));

    const items = await getSubstitutes();

    expect(Array.isArray(items)).toBe(true);
    expect(items).toHaveLength(1);
    expect(items[0].principal_sam).toBe(RECORD.principal_sam);
    expect(items[0].valid_to).toBeNull();
  });

  it("пустой справочник — пустой массив, без исключения", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(200, [])));

    await expect(getSubstitutes()).resolves.toEqual([]);
  });

  it("403 разбирается в понятный текст", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(403, {})));

    await expect(getSubstitutes()).rejects.toThrow(/не по вашим правам/);
  });

  it("503 разбирается в понятный текст", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(503, {})));

    await expect(getSubstitutes()).rejects.toThrow(/недоступен/);
  });
});
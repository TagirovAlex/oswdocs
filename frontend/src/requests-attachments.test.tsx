// Тесты методов вложений requests-client: точные URL и методы запросов.
// Регрессия: getAttachmentFile без суффикса /file падал в 405
// (путь совпадал с DELETE-маршрутом, метод — нет).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "./auth-client";
import { deleteAttachment, getAttachmentFile } from "./requests-client";

// Фейковый ответ fetch (достаточно полей, что использует клиент).
function fakeResponse(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
    blob: async () => new Blob(["data"]),
  } as unknown as Response;
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-123");
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("attachmentsClient", () => {
  // Содержимое файла: GET на /api/attachments/{id}/file с Bearer-токеном.
  it("getAttachmentFile идёт GET на .../file и возвращает Blob", async () => {
    const fetchMock = vi.fn(async () => fakeResponse(200, null));
    vi.stubGlobal("fetch", fetchMock);

    const blob = await getAttachmentFile(1);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/attachments/1/file",
      expect.objectContaining({ headers: { Authorization: "Bearer token-123" } }),
    );
    expect(blob).toBeInstanceOf(Blob);
  });

  // Удаление: DELETE на /api/attachments/{id}, 204 без тела — void.
  it("deleteAttachment идёт DELETE и не разбирает тело", async () => {
    const fetchMock = vi.fn(async () => fakeResponse(204, null));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteAttachment(1)).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/attachments/1",
      expect.objectContaining({
        method: "DELETE",
        headers: { Authorization: "Bearer token-123" },
      }),
    );
  });

  // Чужое вложение: 403 с текстом API.
  it("deleteAttachment при 403 бросает текст API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(403, { detail: "Нет доступа" })));
    await expect(deleteAttachment(1)).rejects.toMatchObject({ status: 403 });
  });
});

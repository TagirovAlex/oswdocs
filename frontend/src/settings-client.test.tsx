// Тесты клиента справочника заместителей: форма ответа и разбор ошибок —
// админский CRUD (/api/settings/substitutes), личный раздел (/api/substitutes/my)
// и подбор заместителя для модалки (/api/substitutes/candidates).
// Сеть не нужна — fetch подменяется фейком, токен — в localStorage.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "./auth-client";
import {
  createMySubstitute,
  deleteMySubstitute,
  getMySubstitutes,
  getSubstitutes,
  searchSubstituteCandidates,
  updateMySubstitute,
} from "./settings-client";

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

// Фейковый fetch, запоминающий вызовы: так проверяем путь, метод и тело —
// контракт ручки без сети.
function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => fakeResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

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

describe("личные записи заместителей (/api/substitutes/my)", () => {
  // Путь и метод личного раздела: сервер берёт сотрудника из сессии, поэтому
  // в URL нет ни логина, ни чужого id.
  it("getMySubstitutes читает массив по GET /api/substitutes/my", async () => {
    const fetchMock = stubFetch(200, [RECORD]);

    const items = await getMySubstitutes();

    expect(Array.isArray(items)).toBe(true);
    expect(items).toHaveLength(1);
    expect(items[0].deputy_sam).toBe(RECORD.deputy_sam);
    expect(fetchMock).toHaveBeenCalledWith("/api/substitutes/my", expect.objectContaining({ method: "GET" }));
  });

  it("createMySubstitute шлёт POST с телом без сотрудника", async () => {
    const fetchMock = stubFetch(200, RECORD);

    await createMySubstitute({
      deputy_sam: RECORD.deputy_sam,
      valid_from: "2026-10-01",
      valid_to: null,
      notify: true,
      note: "",
    });

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/substitutes/my");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body)).deputy_sam).toBe(RECORD.deputy_sam);
  });

  it("updateMySubstitute и deleteMySubstitute ходят по /api/substitutes/my/{id}", async () => {
    const fetchMock = stubFetch(200, RECORD);

    await updateMySubstitute(RECORD.id, {
      deputy_sam: RECORD.deputy_sam,
      valid_from: "2026-10-01",
      valid_to: null,
      notify: false,
      note: "",
    });
    await deleteMySubstitute(RECORD.id);

    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      `/api/substitutes/my/${RECORD.id}`,
      `/api/substitutes/my/${RECORD.id}`,
    ]);
    expect(fetchMock.mock.calls[0][1]?.method).toBe("PUT");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("DELETE");
  });

  it("401 разбирается в понятный текст", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(401, {})));

    await expect(getMySubstitutes()).rejects.toThrow(/Сессия истекла/);
  });
});

describe("searchSubstituteCandidates (/api/substitutes/candidates)", () => {
  // Кандидат подбора (вымышленные логин и ФИО).
  const CANDIDATE = {
    sam: "zameshchitel.vymyshlennyy",
    fio: "Вымышленна Вымышленна Вымышленна",
    department: "Цех № 1",
    position: "Мастер участка",
  };

  // Контракт: GET с q (экранированным) и номером страницы. page_size клиент НЕ
  // передаёт — размер страницы остаётся серверным (свой на клиенте продублировал
  // бы дефолт ручки и молча урезал подбор при правке одной стороны).
  it("шлёт GET с экранированным q и page, без своего page_size", async () => {
    const fetchMock = stubFetch(200, { items: [CANDIDATE], total: 1, page: 2, page_size: 50 });

    await searchSubstituteCandidates("Сидорова & Ко", 2);

    const [path, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe("GET");
    expect(path).toBe("/api/substitutes/candidates?q=%D0%A1%D0%B8%D0%B4%D0%BE%D1%80%D0%BE%D0%B2%D0%B0%20%26%20%D0%9A%D0%BE&page=2");
  });

  // Список приходит в items внутри объекта страницы (не МАССИВ, как прочие
  // списки API) — клиент обязан отдать массив items.
  it("читает items страницы как массив кандидатов", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(200, {
      items: [CANDIDATE],
      total: 12,
      page: 1,
      page_size: 50,
    })));

    const page = await searchSubstituteCandidates("Сидорова");

    expect(Array.isArray(page.items)).toBe(true);
    expect(page.items).toHaveLength(1);
    expect(page.items[0].sam).toBe(CANDIDATE.sam);
    expect(page.total).toBe(12);
  });

  it("items не массив — пустой список, без исключения", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(200, { items: "не массив", total: 0 })));

    const page = await searchSubstituteCandidates("Сидорова");

    expect(page.items).toEqual([]);
  });

  it("422 без detail разбирается в подсказку про три символа", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(422, {})));

    await expect(searchSubstituteCandidates("Сид")).rejects.toThrow(/трёх символов/);
  });

  // Точная причина отказа от сервера (detail) показывается дословно: своими
  // словами клиент отказ не пересказывает — сотрудник должен увидеть то, что
  // ответил сервер.
  it("422 с detail показывается дословно", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      fakeResponse(422, { detail: "Введите не менее трёх символов ФИО или логина заместителя" }),
    ));

    await expect(searchSubstituteCandidates("Сид")).rejects.toThrow(
      "Введите не менее трёх символов ФИО или логина заместителя",
    );
  });

  it("422 от валидации (detail — список, не строка) падает на запасной текст", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      fakeResponse(422, { detail: [{ loc: ["body", "valid_to"], msg: "Неверный тип" }] }),
    ));

    await expect(searchSubstituteCandidates("Сид")).rejects.toThrow(/трёх символов/);
  });

  it("503 с detail показывается дословно", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      fakeResponse(503, { detail: "Справочник сотрудников недоступен: обрыв соединения" }),
    ));

    await expect(searchSubstituteCandidates("Сидорова")).rejects.toThrow(
      "Справочник сотрудников недоступен: обрыв соединения",
    );
  });

  it("503 разбирается в текст про справочник сотрудников", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(503, {})));

    await expect(searchSubstituteCandidates("Сидорова")).rejects.toThrow(
      /Справочник сотрудников недоступен/,
    );
  });

  it("401 разбирается в понятный текст", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => fakeResponse(401, {})));

    await expect(searchSubstituteCandidates("Сидорова")).rejects.toThrow(/Сессия истекла/);
  });
});
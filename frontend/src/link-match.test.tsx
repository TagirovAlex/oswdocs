// Экран «Сопоставление 1С↔AD»: ручной запуск, выдача расхождений, массовое
// подтверждение. Персоны вымышленные; сеть замокана.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_KEY } from "./auth-client";
import { LinkMatch, pagerPages } from "./link-match";

interface MockDiscrepancy {
  key: string;
  fio: string;
  tab_num: string;
  reason: string;
  ad_sam: string | null;
  ad_fio: string | null;
  ad_dept: string | null;
  ad_title: string | null;
  one_c_dept: string | null;
  one_c_position: string | null;
  recommended: boolean;
  can_confirm: boolean;
  candidates: string[];
  sibling_tabs: string[];
}

function row(overrides: Partial<MockDiscrepancy>): MockDiscrepancy {
  return {
    key: "ENT|zup|001",
    fio: "Сказочников Иван Тестович",
    tab_num: "001",
    reason: "one_c_duplicate",
    ad_sam: "t.ivan",
    ad_fio: "Сказочников Иван Тестович",
    ad_dept: "Цех Тестовый",
    ad_title: "Тестировщик",
    one_c_dept: "Склад",
    one_c_position: "Кладовщик",
    recommended: false,
    can_confirm: true,
    candidates: [],
    sibling_tabs: [],
    ...overrides,
  };
}

const fetchMock = vi.fn();

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 422,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response);
}

describe("LinkMatch", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    // Клиент API требует сессию: без токена запрос не уходит (401 «нет сессии»).
    window.localStorage.setItem(TOKEN_KEY, "token-test");
  });

  it("показывает расхождения и счётчики по причинам", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes("/link_1c_ad/discrepancies")) {
        return jsonResponse({
          items: [row({})],
          counts: { one_c_duplicate: 2, ad_duplicate: 1, not_in_ad: 5 },
          page: 1,
          page_size: 50,
          total: 8,
        });
      }
      return jsonResponse({ schedule_ad_links_sync: { mode: "interval", interval_hours: 24 } });
    });

    render(<LinkMatch role="admin" />);

    expect((await screen.findAllByText("Сказочников Иван Тестович")).length).toBeGreaterThan(0);
    expect(screen.getByText(/Всего расхождений: 8/)).toBeInTheDocument();
    expect(screen.getByText(/подтверждаемых: 1/)).toBeInTheDocument();
    // Расписание регламентного прохода показано (а не выдумано).
    expect(screen.getByText(/interval_hours/)).toBeInTheDocument();
  });

  it("подтверждает выбранные расхождения пачкой", async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes("/discrepancies/confirm")) {
        return jsonResponse({ linked: 1, skipped: [], errors: [], linked_sams: ["t.ivan"] });
      }
      if (String(url).includes("/link_1c_ad/discrepancies")) {
        return jsonResponse({
          items: [row({})],
          counts: { one_c_duplicate: 1 },
          page: 1,
          page_size: 50,
          total: 1,
        });
      }
      return jsonResponse({});
    });

    render(<LinkMatch role="admin" />);
    await screen.findAllByText("Сказочников Иван Тестович");

    fireEvent.click(screen.getByLabelText("Выбрать Сказочников Иван Тестович"));
    fireEvent.click(screen.getByRole("button", { name: /Подтвердить выбранные/ }));

    await waitFor(() => expect(screen.getByText(/Подтверждено связок: 1/)).toBeInTheDocument());
    const call = fetchMock.mock.calls.find((c) =>
      String(c[0]).includes("/discrepancies/confirm"),
    );
    expect(call).toBeTruthy();
    expect(String((call?.[1] as RequestInit).body)).toContain("ENT|zup|001");
  });

  it("не подтверждает неоднозначные строки и предлагает ручной выбор", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes("/link_1c_ad/discrepancies")) {
        return jsonResponse({
          items: [
            row({
              key: "ENT|zup|002",
              reason: "ad_duplicate",
              can_confirm: false,
              ad_sam: null,
              candidates: ["t.ad1", "t.ad2"],
            }),
          ],
          counts: { ad_duplicate: 1 },
          page: 1,
          page_size: 50,
          total: 1,
        });
      }
      return jsonResponse({});
    });

    render(<LinkMatch role="admin" />);
    expect((await screen.findAllByText(/В AD несколько записей ФИО/)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Выбрать Сказочников Иван Тестович")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Подтвердить все на странице/ })).toBeDisabled();
    expect(screen.getByText(/t\.ad1, t\.ad2/)).toBeInTheDocument();
  });

  it("запускает сопоставление по требованию и показывает итог", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (String(url).includes("/link_1c_ad/sync")) {
        return jsonResponse({
          synced: true,
          scanned: 23538,
          created: 9,
          skipped_linked: 735,
          skipped_1c_duplicates: 7458,
          skipped_ad_no_match: 15334,
          skipped_ad_duplicates: 2,
          errors: [],
          discrepancies_saved: 21669,
          discrepancies: { one_c_duplicate: 1, not_in_ad: 2 },
        });
      }
      if (String(url).includes("/link_1c_ad/discrepancies")) {
        return jsonResponse({
          items: [],
          counts: {},
          page: 1,
          page_size: 50,
          total: 0,
        });
      }
      return jsonResponse({});
    });

    render(<LinkMatch role="admin" />);
    fireEvent.click(await screen.findByRole("button", { name: "Запустить сопоставление" }));

    await waitFor(() =>
      expect(screen.getByText(/Просмотрено 23538, создано связок 9/)).toBeInTheDocument(),
    );
  });

  it("строит пагинацию: первая, последняя и по пять с каждой стороны", () => {
    expect(pagerPages(1, 3)).toEqual([1, 2, 3]);
    const middle = pagerPages(50, 200);
    // Первая и последняя страницы показаны всегда…
    expect(middle[0]).toBe(1);
    expect(middle[middle.length - 1]).toBe(200);
    // …вокруг текущей — до пяти с каждой стороны.
    expect(middle).toContain(45);
    expect(middle).toContain(55);
    expect(middle).not.toContain(44);
    expect(middle).not.toContain(56);
    // Разрывы обозначены многоточием.
    expect(middle).toContain("…");
  });

  it("скрыт от ролей без права подтверждения", () => {
    fetchMock.mockImplementation(() => jsonResponse({ items: [], counts: {}, total: 0 }));
    render(<LinkMatch role="hr" />);
    expect(screen.queryByRole("button", { name: "Запустить сопоставление" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toMatch(/администратору/);
  });
});
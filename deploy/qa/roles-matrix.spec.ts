// Матрица ролей README п.1 для стенда (W5c Волны 5).
//
// Запуск — ТОЛЬКО на ВМ после деплоя, локально не гонять: нужны реальные
// LDAPS/Redis/1С и доменные учетки. Вход — через UI-форму логина доменной
// учеткой. Логины/пароли — ТОЛЬКО переменные окружения, в коде их нет
// (AGENTS.md п.3): учетка не задана в env — тест пропускается (skip).
//
// Переменные окружения:
//   SED_BASE_URL    адрес стенда, по умолчанию https://sed.company.local
//   LOGIN_ADMIN     PASS_ADMIN   учетка роли admin (SED_ADMINS)
//   LOGIN_HR        PASS_HR      учетка роли hr (SED_HR)
//   LOGIN_OWNER     PASS_OWNER   учетка роли owner (SED_STEP_EXEC / группа шага)
//
// Покрытие (README п.1 «Поиск/доступ»): видимость вкладок (в т.ч. личной вкладки
// «Заместители» у всех ролей), создание заявки, настройки (admin-only), правда и
// доступность личного справочника заместителей, карточка заявки владельцу без ПДн,
// кроме ФИО и службы заявки (решение человека 2026-10-14 — без них согласующий
// не видит, о ком заявка).
import { test, expect, Page } from "@playwright/test";

const BASE_URL = process.env.SED_BASE_URL ?? "https://sed.company.local";

// Учетки ролей из env: значения читаются в рантайме, в файле — только имена.
const ROLE_CREDS: Record<string, { login?: string; pass?: string }> = {
  admin: { login: process.env.LOGIN_ADMIN, pass: process.env.PASS_ADMIN },
  hr: { login: process.env.LOGIN_HR, pass: process.env.PASS_HR },
  owner: { login: process.env.LOGIN_OWNER, pass: process.env.PASS_OWNER },
};

type RoleName = "admin" | "hr" | "owner";

// Вкладки по ролям — строго как в layout.tsx (`visibleTabs`): «Заявки» и
// «Заместители» есть у ВСЕХ вошедших без блоков прав («Заместители» — личный
// раздел сотрудника, решение человека 2026-10-15), дальше вкладки по блокам из
// /auth/me: «Справочник» — employee_directory, «Сопоставление» — link_match,
// «Настройки» — settings_content или settings_infra. Отдельной вкладки
// «Создание» в каркасе нет: создание заявки открывает кнопка «Создать заявку».
const EXPECTED_TABS: Record<RoleName, string[]> = {
  admin: ["Заявки", "Заместители", "Справочник", "Сопоставление", "Настройки"],
  hr: ["Заявки", "Заместители", "Справочник"],
  owner: ["Заявки", "Заместители"],
};

// Кнопки в <nav aria-label="Вкладки"> помимо вкладок: переключатель темы и
// «Выйти» (layout.tsx). Их счётчик тоже видит — прибавляем к числу вкладок.
const NAV_EXTRA_BUTTONS = 2;

// Подписи ролей в шапке (layout.tsx, ROLE_LABELS).
const ROLE_LABELS: Record<RoleName, string> = {
  admin: "Админ",
  hr: "ОК",
  owner: "Владелец",
};

// Заголовки колонок таблицы (layout.tsx, columns): владельцу — без предприятия/
// статуса и без текущего согласующего; «Сотрудник» у всех один (ФИО видно всем).
// «Дата» — дата создания заявки, «Срок» — дедлайн шага (разные даты).
const COLUMNS_FULL = [
  "№",
  "Дата",
  "Сотрудник",
  "Предприятие",
  "Статус",
  "Текущий согласующий",
  "Шаг",
  "Срок",
];
const COLUMNS_OWNER = ["№", "Дата", "Сотрудник", "Шаг", "Срок"];

// Учетка роли из env (пустые значения — тест пропускается).
function creds(role: RoleName): { login: string; pass: string } {
  const c = ROLE_CREDS[role];
  return { login: c.login ?? "", pass: c.pass ?? "" };
}

// Пропуск теста, если учетка роли не задана в env.
function skipIfNoCreds(role: RoleName): void {
  const c = creds(role);
  test.skip(
    !c.login || !c.pass,
    `LOGIN_${role.toUpperCase()}/PASS_${role.toUpperCase()} не заданы в env`,
  );
}

// ---------------------------------------------------------------------------
// Хелперы: вход, навигация, ожидания
// ---------------------------------------------------------------------------

// Вход доменной учеткой через UI-форму логина (login-screen.tsx).
async function loginAs(page: Page, role: RoleName): Promise<void> {
  const c = creds(role);
  await page.goto(BASE_URL);
  const form = page.locator('form[aria-label="Вход в систему"]');
  await form.waitFor();
  await form.locator('input[aria-label="Логин"]').fill(c.login);
  await form.locator('input[aria-label="Пароль"]').fill(c.pass);
  await form.getByRole("button", { name: "Войти" }).click();
  // Шапка с ролью сессии — вход завершен (layout.tsx).
  await page.getByText(new RegExp(`роль: ${ROLE_LABELS[role]}`)).waitFor();
}

// Выход из системы: возврат к экрану логина.
async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Выйти" }).click();
  await page.locator('form[aria-label="Вход в систему"]').waitFor();
}

// Навигация вкладок (layout.tsx, nav с aria-label="Вкладки").
function tabsNav(page: Page) {
  return page.locator('nav[aria-label="Вкладки"]');
}

// Проверка состава вкладок против матрицы: нужные есть, лишних нет.
// Первые кнопки навигации — вкладки и идут в порядке visibleTabs; дальше
// переключатель темы и «Выйти», поэтому сверяем первые expected.length штук
// и общий счётчик кнопок в <nav>.
async function expectTabs(page: Page, role: RoleName): Promise<void> {
  const expected = EXPECTED_TABS[role];
  const buttons = tabsNav(page).getByRole("button");
  for (const name of expected) {
    await buttons.getByText(name, { exact: true }).waitFor();
  }
  const shown: string[] = [];
  for (let i = 0; i < expected.length; i += 1) {
    shown.push(((await buttons.nth(i).textContent()) ?? "").trim());
  }
  expect(shown).toEqual(expected);
  expect(await buttons.count()).toBe(expected.length + NAV_EXTRA_BUTTONS);
}

// Токен сессии из localStorage (ключ sed_token, auth-client.tsx).
async function sessionToken(page: Page): Promise<string> {
  return page.evaluate(() => localStorage.getItem("sed_token") ?? "");
}

// HTTP-статус API-запроса от имени текущей сессии (Bearer из localStorage).
async function apiStatus(page: Page, path: string): Promise<number> {
  const token = await sessionToken(page);
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  const res = await page.request.get(`${BASE_URL}${path}`, { headers });
  return res.status();
}

// GET /api/requests от имени сессии: статус + тело.
async function apiRequests(page: Page): Promise<{ status: number; rows: Array<Record<string, unknown>> }> {
  const token = await sessionToken(page);
  const res = await page.request.get(`${BASE_URL}/api/requests`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const rows = res.status() === 200 ? ((await res.json()) as Array<Record<string, unknown>>) : [];
  return { status: res.status(), rows };
}

// Заголовки колонок таблицы заявок.
async function tableColumns(page: Page): Promise<string[]> {
  return page.locator('table[aria-label="Заявки"] thead th').allTextContents();
}

// ---------------------------------------------------------------------------
// Тесты матрицы ролей
// ---------------------------------------------------------------------------

test.describe("Матрица ролей README п.1", () => {
  test("guest: без сессии — только экран логина, API 401", async ({ page }) => {
    // Гость не имеет сессии: SPA показывает только форму входа, каркаса нет.
    await page.goto(BASE_URL);
    await page.locator('form[aria-label="Вход в систему"]').waitFor();
    expect(await tabsNav(page).count()).toBe(0);
    // Прямые запросы API без токена — 401 (данные недоступны).
    for (const path of ["/api/requests", "/api/folders", "/api/settings"]) {
      expect(await apiStatus(page, path)).toBe(401);
    }
  });

  test("admin: вкладки Заявки/Заместители/Справочник/Сопоставление/Настройки, создание и настройки доступны", async ({ page }) => {
    skipIfNoCreds("admin");
    await loginAs(page, "admin");
    await expectTabs(page, "admin");

    // Создание заявки — кнопкой «Создать заявку» на вкладке «Заявки» (окно
    // ?view=create); отдельной вкладки «Создание» в каркасе нет.
    await tabsNav(page).getByRole("button", { name: "Заявки" }).click();
    await page.locator('table[aria-label="Заявки"]').waitFor();
    await page.getByRole("button", { name: "Создать заявку" }).waitFor();
    // Таблица: полные колонки (ПДн виден ОК/админу).
    expect(await tableColumns(page)).toEqual(COLUMNS_FULL);

    // Настройки: форма админки открывается, GET /api/settings → 200 (admin-only).
    await tabsNav(page).getByRole("button", { name: "Настройки" }).click();
    await page.locator('section[aria-label="Настройки СЭД"]').waitFor();
    expect(await apiStatus(page, "/api/settings")).toBe(200);

    // Админский справочник заместителей админу доступен (блок settings_substitutes),
    // личный контур — тоже (это две точки входа в один справочник).
    expect(await apiStatus(page, "/api/settings/substitutes")).toBe(200);
    expect(await apiStatus(page, "/api/substitutes/my")).toBe(200);
  });

  test("hr: вкладки Заявки/Заместители/Справочник, создание доступно, настройки — 403", async ({ page }) => {
    skipIfNoCreds("hr");
    await loginAs(page, "hr");
    await expectTabs(page, "hr");

    // Создание доступно ОК: кнопка «Создать заявку» на вкладке «Заявки».
    await tabsNav(page).getByRole("button", { name: "Заявки" }).click();
    await page.getByRole("button", { name: "Создать заявку" }).waitFor();

    // Настройки: вкладки нет в UI, GET /api/settings → 403 (только админам).
    expect(await apiStatus(page, "/api/settings")).toBe(403);
  });

  test("owner: только Заявки/Заместители, карточка без ПДн (кроме ФИО и службы), настройки — 403", async ({ page }) => {
    skipIfNoCreds("owner");
    await loginAs(page, "owner");
    await expectTabs(page, "owner");

    // Создание/настройки владельцу недоступны: вкладок нет, API — 403.
    expect(await apiStatus(page, "/api/settings")).toBe(403);
    expect(await apiStatus(page, "/api/enterprises")).toBe(403);

    // Личный раздел «Заместители» виден сотруднику БЕЗ блоков прав (решение
    // человека 2026-10-15): раздел открывается, личные записи отдаёт 200, а
    // админский контур справочника (блок settings_substitutes) — 403.
    await tabsNav(page).getByRole("button", { name: "Заместители" }).click();
    await page.getByRole("heading", { name: "Мои заместители" }).waitFor();
    expect(await apiStatus(page, "/api/substitutes/my")).toBe(200);
    expect(await apiStatus(page, "/api/settings/substitutes")).toBe(403);
    // Подбор заместителя открыт всем вошедшим (локальный справочник сотрудников),
    // а поиск в живом AD остался за блоком employee_directory.
    expect(await apiStatus(page, "/api/substitutes/candidates?q=ив")).toBe(200);
    expect(await apiStatus(page, "/api/ad/search?q=ив")).toBe(403);

    // Таблица владельца: без колонок предприятия/статуса (layout.tsx).
    await tabsNav(page).getByRole("button", { name: "Заявки" }).click();
    await page.locator('table[aria-label="Заявки"]').waitFor();
    expect(await tableColumns(page)).toEqual(COLUMNS_OWNER);

    // Карточка заявки: ФИО и служба видны ВСЕМ (решение человека 2026-10-14 —
    // без них согласующий не понимает, о ком заявка), табельный номер и
    // предприятие владельцу по-прежнему не отдаются.
    const { status, rows } = await apiRequests(page);
    expect(status).toBe(200);
    for (const row of rows) {
      expect(row.tab_num).toBeNull();
      expect(row.enterprise).toBeNull();
      expect(row.id).not.toBeNull();
    }
    // В таблице видно ФИО заявки; маска «Сотрудник № …» — только на заявках
    // без ФИО (requests-client.tsx).
    if (rows.length > 0 && rows[0].fio) {
      await page.getByText(new RegExp(rows[0].fio)).first().waitFor();
    }
  });
});
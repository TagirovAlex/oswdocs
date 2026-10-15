// Тесты личного раздела «Заместители» (верхняя вкладка каркаса): сотрудник видит
// и правит только свои записи «я → мой заместитель». Сценарии — загрузка своих
// записей, создание (пустой «по» уходит как null), удаление с подтверждением,
// проверка формы до отправки, пустое состояние и отказы сервера 409/422, а также
// подбор заместителя МОДАЛКОЙ по GET /api/substitutes/candidates (не поиском по
// AD: /api/ad/search закрыт блоком employee_directory, у сотрудника его нет) —
// включая постраничную догрузку выдачи «Показать ещё» и минимум в три символа.
// Сеть не нужна: settings-client и searchAd мокаются. Логины и ФИО вымышленные.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Substitutes } from "./substitutes";
import { ApiHttpError } from "./auth-client";
import { setDisplayTimezone } from "./date-format";
import { searchAd } from "./requests-client";
import {
  createMySubstitute,
  deleteMySubstitute,
  getMySubstitutes,
  searchSubstituteCandidates,
  updateMySubstitute,
} from "./settings-client";
import type { Substitute, SubstituteCandidate } from "./settings-client";

// Мок клиента настроек (fetch не вызывается).
vi.mock("./settings-client", () => ({
  getMySubstitutes: vi.fn(),
  createMySubstitute: vi.fn(),
  updateMySubstitute: vi.fn(),
  deleteMySubstitute: vi.fn(),
  searchSubstituteCandidates: vi.fn(),
}));

// Мок клиента заявок: в личном разделе поиск по AD использоваться НЕ должен
// (блок прав employee_directory у сотрудника нет), поэтому проверяем это отдельно.
vi.mock("./requests-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./requests-client")>();
  return { ...actual, searchAd: vi.fn() };
});

// Кандидат подбора заместителей (как отдаёт GET /api/substitutes/candidates).
const candidate: SubstituteCandidate = {
  sam: "zameshchitel.aa",
  fio: "Сидорова Анна Сергеевна",
  department: "Цех № 1",
  position: "Мастер участка",
};

// Второй кандидат — вторая страница выдачи в проверках догрузки «Показать ещё».
const candidate2: SubstituteCandidate = {
  sam: "petrova.bb",
  fio: "Петрова Ольга Ивановна",
  department: "Цех № 2",
  position: "Инженер-технолог",
};

// Страница подбора кандидатов (обёртка {items, total, page, page_size}); total
// по умолчанию — длина выдачи (выдача вся и есть), для проверки постраничной
// догрузки задаётся явно.
function candidatePage(items: SubstituteCandidate[], total?: number): {
  items: SubstituteCandidate[];
  total: number;
  page: number;
  page_size: number;
} {
  return { items, total: total ?? items.length, page: 1, page_size: 50 };
}

// Свои записи по умолчанию (GET /api/substitutes/my): бессрочная и истёкшая —
// по сроку в далёком прошлом, чтобы «действует сегодня» не зависело от даты
// запуска тестов.
const mySubstitutes: Substitute[] = [
  {
    id: 1,
    principal_sam: "sotrudnik.osnovnoy",
    deputy_sam: "zameshchitel.aa",
    valid_from: "2000-01-01",
    valid_to: null,
    notify: true,
    note: "На время отпуска",
    created_by: "sotrudnik.osnovnoy",
    created_at: "2000-01-01T08:00:00+00:00",
    updated_at: null,
  },
  {
    id: 2,
    principal_sam: "sotrudnik.osnovnoy",
    deputy_sam: "zameshchitel.bb",
    valid_from: "2000-01-01",
    valid_to: "2000-12-31",
    notify: false,
    note: "",
    created_by: "sotrudnik.osnovnoy",
    created_at: "2000-01-01T08:00:00+00:00",
    updated_at: null,
  },
];

beforeEach(() => {
  // Зона показа — состояние модуля: сбрасываем, иначе заданная в одном тесте
  // зона утекла бы в остальные тесты файла.
  setDisplayTimezone(null);
  vi.mocked(getMySubstitutes).mockReset();
  vi.mocked(createMySubstitute).mockReset();
  vi.mocked(updateMySubstitute).mockReset();
  vi.mocked(deleteMySubstitute).mockReset();
  vi.mocked(searchAd).mockReset();
  vi.mocked(searchSubstituteCandidates).mockReset();
  vi.mocked(getMySubstitutes).mockResolvedValue(mySubstitutes);
  vi.mocked(searchSubstituteCandidates).mockResolvedValue(candidatePage([candidate]));
});

// Выбор заместителя модалкой подбора (открытие → поиск с debounce → выбор строки).
async function pickDeputy(): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
  const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
  fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
    target: { value: "Сидорова" },
  });
  await waitFor(() => expect(searchSubstituteCandidates).toHaveBeenCalledWith("Сидорова", 1));
  fireEvent.click(await within(dialog).findByRole("button", { name: `Выбрать ${candidate.fio}` }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}

describe("Substitutes", () => {
  // Свои записи: сотрудник в списке один и тот же (выводим «Я»), заместитель —
  // логин, срок с «бессрочно», признак действия на сегодня, письма и комментарий.
  it("загружает свои записи: сотрудник — «Я», срок и действие на сегодня", async () => {
    render(<Substitutes />);

    const table = await screen.findByRole("table", { name: "Мои заместители" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(getMySubstitutes).toHaveBeenCalled();
    // Сотрудника своих записей не показываем логином — это «Я».
    expect(within(rows[0]).getAllByRole("cell")[0]).toHaveTextContent("Я");
    expect(within(rows[0]).getByText("zameshchitel.aa")).toBeInTheDocument();
    expect(within(rows[0]).getByText("с 2000-01-01 по бессрочно")).toBeInTheDocument();
    expect(within(rows[0]).getByText("На время отпуска")).toBeInTheDocument();
    // Бессрочная запись действует сегодня, запись с прошедшим «по» — нет.
    const first = within(rows[0]).getAllByRole("cell");
    expect(first[3]).toHaveTextContent("да");
    expect(first[4]).toHaveTextContent("да");
    const second = within(rows[1]).getAllByRole("cell");
    expect(within(rows[1]).getByText("с 2000-01-01 по 2000-12-31")).toBeInTheDocument();
    expect(second[3]).toHaveTextContent("нет");
    expect(second[4]).toHaveTextContent("нет");

    cleanup();
  });

  // Записи нет — понятное пустое состояние вместо пустой таблицы.
  it("без записей показывает «Заместителей не назначено»", async () => {
    vi.mocked(getMySubstitutes).mockResolvedValue([]);

    render(<Substitutes />);

    expect(await screen.findByText("Заместителей не назначено")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Мои заместители" })).not.toBeInTheDocument();

    cleanup();
  });

  // Создание: заместитель выбирается поиском в AD, пустой «по» уходит как null,
  // письма по умолчанию включены.
  it("создание записи уходит в createMySubstitute с пустым «по» как null", async () => {
    vi.mocked(createMySubstitute).mockImplementation(async (data) => ({
      ...data,
      id: 3,
      principal_sam: "sotrudnik.osnovnoy",
      created_by: "sotrudnik.osnovnoy",
      created_at: "2026-11-01T08:00:00+00:00",
      updated_at: null,
    }));

    render(<Substitutes />);
    await pickDeputy();
    fireEvent.change(screen.getByLabelText("Заместитель действует с"), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText("Комментарий к записи заместителя"), {
      target: { value: "На время отпуска" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить заместителя" }));

    await waitFor(() =>
      expect(createMySubstitute).toHaveBeenCalledWith({
        deputy_sam: "zameshchitel.aa",
        valid_from: "2026-11-01",
        valid_to: null,
        notify: true,
        note: "На время отпуска",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Заместитель добавлен");

    cleanup();
  });

  // Правка своей записи: форма открывается по кнопке «Править», сохранение идёт
  // в updateMySubstitute с id записи (сотрудника в теле нет — он из сессии).
  it("правка записи уходит в updateMySubstitute", async () => {
    vi.mocked(updateMySubstitute).mockImplementation(async (_id, data) => ({
      ...data,
      id: 1,
      principal_sam: "sotrudnik.osnovnoy",
      created_by: "sotrudnik.osnovnoy",
      created_at: "2000-01-01T08:00:00+00:00",
      updated_at: "2026-10-15T08:00:00+00:00",
    }));

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });
    fireEvent.click(screen.getByRole("button", { name: "Править запись zameshchitel.aa" }));
    fireEvent.change(screen.getByLabelText("Заместитель действует по"), { target: { value: "2026-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(updateMySubstitute).toHaveBeenCalledWith(1, {
        deputy_sam: "zameshchitel.aa",
        valid_from: "2000-01-01",
        valid_to: "2026-12-31",
        notify: true,
        note: "На время отпуска",
      }),
    );
    expect(createMySubstitute).not.toHaveBeenCalled();

    cleanup();
  });

  // Проверки формы до отправки: не выбран заместитель, «по» раньше «с» —
  // понятное сообщение, запрос не уходит.
  it("форма проверяет заместителя и срок до отправки", async () => {
    render(<Substitutes />);

    // Не выбран заместитель — первая проверка.
    fireEvent.click(screen.getByRole("button", { name: "Добавить заместителя" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Выберите заместителя");

    await pickDeputy();
    // «По» раньше «с» — вторая проверка.
    fireEvent.change(screen.getByLabelText("Заместитель действует с"), { target: { value: "2026-11-10" } });
    fireEvent.change(screen.getByLabelText("Заместитель действует по"), { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить заместителя" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Дата «действует по» раньше даты «действует с»",
    );
    expect(createMySubstitute).not.toHaveBeenCalled();

    cleanup();
  });

  // Удаление своей записи — с подтверждением; без согласия запрос не уходит.
  it("удаление записи спрашивает подтверждение", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Удалить запись zameshchitel.bb" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(deleteMySubstitute).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Удалить запись zameshchitel.bb" }));
    await waitFor(() => expect(deleteMySubstitute).toHaveBeenCalledWith(2));
    confirmSpy.mockRestore();

    cleanup();
  });

  // Отказы сервера показываются текстом ошибки в role="alert": дубль пары (409)
  // и неверные данные (422) — как их переводит settings-client.
  it("отказ сервера 409 и 422 показывается в role=\"alert\"", async () => {
    vi.mocked(createMySubstitute).mockRejectedValue(
      new ApiHttpError(409, "Такая пара «сотрудник и заместитель» уже заведена"),
    );

    render(<Substitutes />);
    await pickDeputy();
    fireEvent.click(screen.getByRole("button", { name: "Добавить заместителя" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Такая пара «сотрудник и заместитель» уже заведена",
    );

    cleanup();

    vi.mocked(createMySubstitute).mockRejectedValue(
      new ApiHttpError(422, "Неверные данные заместителя"),
    );
    render(<Substitutes />);
    await pickDeputy();
    fireEvent.click(screen.getByRole("button", { name: "Добавить заместителя" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Неверные данные заместителя");

    cleanup();
  });

  // Ошибка загрузки своих записей — понятный текст в role="alert", пустого
  // состояния при этом не показываем (иначе «ничего не найдено» при ошибке).
  it("ошибка загрузки своих записей показывается в role=\"alert\"", async () => {
    vi.mocked(getMySubstitutes).mockRejectedValue(
      new ApiHttpError(503, "Справочник заместителей недоступен"),
    );

    render(<Substitutes />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Заместители: Справочник заместителей недоступен",
    );
    expect(screen.queryByText("Заместителей не назначено")).not.toBeInTheDocument();

    cleanup();
  });

  // Подбор заместителя МОДАЛКОЙ по своей ручке (решение человека 2026-10-15):
  // открытие → поиск с debounce → выбор строки → в форме логин.
  it("подбор заместителя модалкой: открытие, поиск, выбор кладёт логин в форму", async () => {
    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    // До выбора поле показывает «не выбран», подсказка — про модалку.
    expect(screen.getByLabelText("Заместитель (логин AD)")).toHaveValue("не выбран");
    expect(screen.getByText(/Выберите сотрудника кнопкой выше/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });

    // Короткий запрос до сервера не идёт — подсказка про три символа.
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "С" },
    });
    expect(within(dialog).getByText("Введите не менее трёх символов ФИО или логина.")).toBeInTheDocument();
    expect(searchSubstituteCandidates).not.toHaveBeenCalled();

    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сидорова" },
    });
    // Выдача приходит таблицей с ФИО/должностью/подразделением.
    const found = await screen.findByRole("table", { name: "Найденные сотрудники" });
    const row = within(found).getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell")[0]).toHaveTextContent("Сидорова Анна Сергеевна");
    expect(within(row).getAllByRole("cell")[1]).toHaveTextContent("Мастер участка");
    expect(within(row).getAllByRole("cell")[2]).toHaveTextContent("Цех № 1");

    fireEvent.click(within(found).getByRole("button", { name: `Выбрать ${candidate.fio}` }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByLabelText("Заместитель (логин AD)")).toHaveValue(candidate.sam);

    cleanup();
  });

  // Пустая выдача и отказ подбора (503) показываются в модалке, не ломая форму:
  // текст ошибки — role="alert" внутри модалки.
  it("модалка подбора: пустая выдача и отказ 503 показываются в role=\"alert\"", async () => {
    vi.mocked(searchSubstituteCandidates).mockResolvedValue(candidatePage([]));

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сидорова" },
    });
    expect(await within(dialog).findByText(/Никого не нашли/)).toHaveTextContent(
      "Искать можно по части ФИО или по логину.",
    );

    vi.mocked(searchSubstituteCandidates).mockRejectedValue(
      new ApiHttpError(503, "Справочник сотрудников недоступен"),
    );
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Петрова" },
    });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Справочник сотрудников недоступен",
    );
    // Заместитель в форме не изменился.
    expect(screen.getByLabelText("Заместитель (логин AD)")).toHaveValue("не выбран");

    cleanup();
  });

  // Отказ 422 от сервера показывается в модалке ДОСЛОВНО (текст из detail,
  // разобранный settings-client): сотрудник видит точную причину, а не
  // обезличенную клиентскую подсказку.
  it("модалка подбора: отказ 422 от сервера показывается дословно", async () => {
    vi.mocked(searchSubstituteCandidates).mockRejectedValue(
      new ApiHttpError(422, "Введите не менее трёх символов ФИО или логина заместителя"),
    );

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сид" },
    });

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Введите не менее трёх символов ФИО или логина заместителя",
    );

    cleanup();
  });

  // Постраничная выдача подбора: счётчик total от сервера, кнопка «Показать ещё»
  // дописывает следующую страницу в конец списка, в конце выдачи кнопки нет.
  it("подбор постраничный: «Показать ещё» дописывает страницу, в конце кнопки нет", async () => {
    vi.mocked(searchSubstituteCandidates).mockImplementation(async (_q, page) =>
      (page ?? 1) === 1
        ? candidatePage([candidate], 2)
        : { items: [candidate2], total: 2, page: 2, page_size: 50 },
    );

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сидорова" },
    });
    const found = await screen.findByRole("table", { name: "Найденные сотрудники" });
    // Показано меньше, чем знает сервер (total=2, строк 1) — догружать есть что.
    expect(within(dialog).getByText("Найдено: 2")).toBeInTheDocument();
    expect(within(found).getAllByRole("row")).toHaveLength(2);

    fireEvent.click(within(dialog).getByRole("button", { name: "Показать ещё сотрудников" }));

    // Вторая страница дописана в конец: первая строка на месте, вторая добавилась.
    await waitFor(() => expect(within(found).getAllByRole("row")).toHaveLength(3));
    expect(searchSubstituteCandidates).toHaveBeenLastCalledWith("Сидорова", 2);
    expect(within(found).getAllByRole("row")[1]).toHaveTextContent(candidate.fio);
    expect(within(found).getAllByRole("row")[2]).toHaveTextContent(candidate2.fio);
    // Всё, что знает сервер, показано — догружать больше нечего.
    expect(within(dialog).queryByRole("button", { name: "Показать ещё сотрудников" })).toBeNull();

    cleanup();
  });

  // Минимум подбора синхронизирован с серверным (_CANDIDATES_MIN_QUERY): запрос
  // короче трёх символов на сервер не уходит, вместо него — подсказка.
  it("подбор: запрос короче трёх символов не отправляется", async () => {
    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Си" },
    });
    expect(
      within(dialog).getByText("Введите не менее трёх символов ФИО или логина."),
    ).toBeInTheDocument();
    expect(searchSubstituteCandidates).not.toHaveBeenCalled();

    // Три символа — запрос уходит, и сразу первой страницей.
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сид" },
    });
    await waitFor(() => expect(searchSubstituteCandidates).toHaveBeenCalledTimes(1));
    expect(searchSubstituteCandidates).toHaveBeenCalledWith("Сид", 1);

    cleanup();
  });

  // Новый поисковый запрос сбрасывает догрузку: страница снова первая, строки
  // прежнего запроса в выдаче не остаётся.
  it("подбор: новый запрос сбрасывает догрузку на первую страницу", async () => {
    vi.mocked(searchSubstituteCandidates).mockImplementation(async (q, page) =>
      (page ?? 1) === 1
        ? candidatePage(q === "Петрова" ? [candidate2] : [candidate], 2)
        : { items: [candidate2], total: 2, page: 2, page_size: 50 },
    );

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сидорова" },
    });
    const found = await screen.findByRole("table", { name: "Найденные сотрудники" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Показать ещё сотрудников" }));
    await waitFor(() => expect(within(found).getAllByRole("row")).toHaveLength(3));

    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Петрова" },
    });

    await waitFor(() => expect(searchSubstituteCandidates).toHaveBeenLastCalledWith("Петрова", 1));
    await waitFor(() =>
      expect(within(found).getAllByRole("row")).toHaveLength(2),
    );
    expect(within(found).queryByRole("button", { name: `Выбрать ${candidate.fio}` })).toBeNull();
    expect(within(found).getByRole("button", { name: `Выбрать ${candidate2.fio}` })).toBeInTheDocument();

    cleanup();
  });

  // Гонка догрузки: ответ по уже сменённому поисковому запросу в список не
  // попадает (устаревшая страница игнорируется, а не дописывается в конец).
  it("подбор: устаревшая догрузка не дописывается после смены запроса", async () => {
    let releaseMore: () => void = () => {};
    const more = new Promise<void>((res) => {
      releaseMore = res;
    });
    vi.mocked(searchSubstituteCandidates).mockImplementation(async (_q, page) => {
      if ((page ?? 1) === 1) return candidatePage([candidate], 2);
      // Догрузка «висит» и приходит уже после смены запроса.
      await more;
      return { items: [candidate2], total: 2, page: 2, page_size: 50 };
    });

    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    const dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Сидорова" },
    });
    const found = await screen.findByRole("table", { name: "Найденные сотрудники" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Показать ещё сотрудников" }));

    fireEvent.change(within(dialog).getByLabelText("Поиск заместителя"), {
      target: { value: "Петрова" },
    });
    await waitFor(() => expect(searchSubstituteCandidates).toHaveBeenLastCalledWith("Петрова", 1));

    // Ответ устаревшей догрузки пришёл — в таблице его быть не должно.
    await act(async () => {
      releaseMore();
      await more;
    });
    expect(within(found).getAllByRole("row")).toHaveLength(2);
    expect(within(found).queryByRole("button", { name: `Выбрать ${candidate2.fio}` })).toBeNull();

    cleanup();
  });

  // Escape и «Отмена» закрывают модалку, клик по подложке — нет (решение человека
  // в проекте: подложка закрытия не имеет).
  it("модалка подбора: Escape и «Отмена» закрывают, подложка — нет", async () => {
    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    let dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    // Подложка кликом модалку не закрывает.
    fireEvent.click(document.querySelector(".sed-modal-backdrop")!);
    expect(screen.getByRole("dialog", { name: "Выбрать заместителя" })).toBeInTheDocument();
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Выбрать заместителя" }));
    dialog = await screen.findByRole("dialog", { name: "Выбрать заместителя" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    cleanup();
  });

  // Блокер «вкладка мертва для сотрудника»: личный раздел НЕ ходит в
  // /api/ad/search (он закрыт блоком employee_directory) — подбор только
  // через свою ручку без блоков прав.
  it("личный раздел не использует поиск по AD (searchAd не вызывается)", async () => {
    render(<Substitutes />);
    await screen.findByRole("table", { name: "Мои заместители" });

    // Открытие модалки и сам подбор — запрос только к /api/substitutes/candidates.
    await pickDeputy();
    expect(searchSubstituteCandidates).toHaveBeenCalledWith("Сидорова", 1);
    expect(searchAd).not.toHaveBeenCalled();

    // И после сохранения записи поиск по AD не появляется.
    vi.mocked(createMySubstitute).mockImplementation(async (data) => ({
      ...data,
      id: 3,
      principal_sam: "sotrudnik.osnovnoy",
      created_by: "sotrudnik.osnovnoy",
      created_at: "2026-11-01T08:00:00+00:00",
      updated_at: null,
    }));
    fireEvent.click(screen.getByRole("button", { name: "Добавить заместителя" }));
    await waitFor(() => expect(createMySubstitute).toHaveBeenCalled());
    expect(searchAd).not.toHaveBeenCalled();

    cleanup();
  });
});

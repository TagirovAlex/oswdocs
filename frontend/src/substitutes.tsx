// Личный раздел «Заместители» (верхняя вкладка каркаса, решение человека
// 2026-10-15): сотрудник видит и правит ТОЛЬКО свои записи «я → мой заместитель».
// Данные — GET/POST/PUT/DELETE /api/substitutes/my (settings-client): сервер сам
// берёт сотрудника из сессии, поэтому чужие записи не отдаются даже по id.
// Блоков прав у раздела нет — вкладка доступна всем вошедшим; админский
// справочник заместителей (вкладка «Настройки» → «Заместители») остаётся
// отдельным разделом и здесь не используется.
// Заместителя сотрудник подбирает модалкой по GET /api/substitutes/candidates
// (своя ручка подбора, без блоков прав), а не поиском по AD: /api/ad/search
// закрыт блоком employee_directory, которого у сотрудника нет, — вкладка была
// для него мертвой. Показа срока записи — общий модуль substitutes-shared.
// ПДн в фикстурах/тестах вымышленные; в коде логинов и ФИО нет.
import { useEffect, useRef, useState } from "react";
import { isActiveToday, substitutePeriodLabel, todayIso } from "./substitutes-shared";
import {
  createMySubstitute,
  deleteMySubstitute,
  getMySubstitutes,
  searchSubstituteCandidates,
  updateMySubstitute,
} from "./settings-client";
import type { Substitute, SubstituteCandidate } from "./settings-client";

// Форма записи (создание или правка своей): логин заместителя, срок, письма и
// комментарий. Даты — строки ISO YYYY-MM-DD, пустая конечная = «бессрочно».
interface SubstituteForm {
  // null — новая запись; число — правка своей записи с этим id.
  id: number | null;
  deputy_sam: string;
  valid_from: string;
  valid_to: string;
  notify: boolean;
  note: string;
}

// Новая форма: заместитель не выбран, срок — с сегодняшнего дня (письма
// заместителю по умолчанию шлём, как в админском справочнике).
function emptyForm(): SubstituteForm {
  return {
    id: null,
    deputy_sam: "",
    valid_from: todayIso(),
    valid_to: "",
    notify: true,
    note: "",
  };
}

// Минимум подстроки подбора: короче запрос в сервер не идёт (серверная
// _CANDIDATES_MIN_QUERY в api/app/substitutes.py — тоже 3), подсказку в модалке
// показываем сами, с тем же смыслом.
const CANDIDATES_MIN_QUERY = 3;

// Модалка подбора заместителя (образец — модалка выбора сотрудника AD в
// request-card.tsx): поиск с debounce 300 мс по GET /api/substitutes/candidates,
// таблица выдачи и выбор строки. Выдача постраничная: страница 1 приходит по
// новому запросу, дальше — кнопкой «Показать ещё» (строки дописываются в конец),
// конец выдачи — по total из ответа (свой размер страницы клиент не задаёт).
// Клик по подложке модалку НЕ закрывает (решение человека в проекте) — закрывают
// Отмена и Escape.
function DeputySearchModal(props: {
  onPick: (cand: SubstituteCandidate) => void;
  onClose: () => void;
}) {
  const { onPick, onClose } = props;
  const [query, setQuery] = useState<string>("");
  const [cands, setCands] = useState<SubstituteCandidate[]>([]);
  // Всего подходящих сотрудников (счётчик от сервера, не длина cands).
  const [total, setTotal] = useState<number>(0);
  // Последняя загруженная страница (0 — выдачи ещё нет).
  const [page, setPage] = useState<number>(0);
  const [searching, setSearching] = useState<boolean>(false);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<string>("");
  // Сервер прислал пустую страницу при непустом total (он считает total по
  // фильтру, а себя из выдачи исключает) — дальше грузить нечего.
  const [moreDone, setMoreDone] = useState<boolean>(false);

  // Номер последнего запроса: ответ потерянного по debounce (или устаревшего по
  // смене поискового запроса) запроса не показываем — иначе выдача прыгала бы
  // назад при быстром наборе или при догрузке уже отменённой страницы.
  const seq = useRef<number>(0);

  // Живой подбор заместителей (debounce 300 мс), первая страница. Сервер сам
  // отвечает 422 на запрос короче CANDIDATES_MIN_QUERY символов, но до него лучше
  // не доводить — подсказку показываем сами.
  useEffect(() => {
    const q = query.trim();
    if (q.length < CANDIDATES_MIN_QUERY) {
      seq.current += 1;
      setCands([]);
      setTotal(0);
      setPage(0);
      setMoreDone(false);
      setSearching(false);
      setLoadingMore(false);
      setSearchError("");
      return;
    }
    const mine = seq.current + 1;
    seq.current = mine;
    setSearching(true);
    setLoadingMore(false);
    const timer = setTimeout(() => {
      searchSubstituteCandidates(q, 1)
        .then((res) => {
          if (seq.current !== mine) return;
          setCands(res.items);
          setTotal(res.total);
          setPage(1);
          setMoreDone(false);
          setSearching(false);
          setSearchError("");
        })
        .catch((e: unknown) => {
          if (seq.current !== mine) return;
          setSearching(false);
          setCands([]);
          setTotal(0);
          setPage(0);
          setSearchError(e instanceof Error ? e.message : "Не удалось подобрать заместителя");
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Догрузка следующей страницы к текущему запросу: строки дописываются в конец.
  function loadMore(): void {
    const q = query.trim();
    if (q.length < CANDIDATES_MIN_QUERY || page === 0 || cands.length >= total || moreDone) return;
    const next = page + 1;
    const mine = seq.current + 1;
    seq.current = mine;
    setLoadingMore(true);
    searchSubstituteCandidates(q, next)
      .then((res) => {
        if (seq.current !== mine) return;
        setCands((prev) => [...prev, ...res.items]);
        setTotal(res.total);
        setPage(next);
        setMoreDone(res.items.length === 0);
        setLoadingMore(false);
        setSearchError("");
      })
      .catch((e: unknown) => {
        if (seq.current !== mine) return;
        setLoadingMore(false);
        setSearchError(e instanceof Error ? e.message : "Не удалось подгрузить заместителей");
      });
  }

  // Ещё есть что догружать: сервер знает больше, чем показано, и страница
  // придёт непустой.
  const canLoadMore = page > 0 && cands.length < total && !moreDone;

  return (
    <div className="sed-modal-backdrop">
      <div
        className="sed-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Выбрать заместителя"
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <h4>Выбор заместителя</h4>
        <label className="sed-field">
          Поиск сотрудника
          <input
            autoFocus
            aria-label="Поиск заместителя"
            placeholder="ФИО сотрудника"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        {searching && <div className="sed-note">Поиск сотрудников…</div>}
        {searchError && <div role="alert">{searchError}</div>}
        {cands.length > 0 && <div className="sed-note">Найдено: {total}</div>}
        {cands.length > 0 && (
          <table className="sed-table" aria-label="Найденные сотрудники">
            <thead>
              <tr>
                <th scope="col">ФИО</th>
                <th scope="col">Должность</th>
                <th scope="col">Подразделение</th>
                <th scope="col">
                  <span className="sed-hidden">Выбор</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {cands.map((cand) => (
                <tr key={cand.sam}>
                  <td>{cand.fio || cand.sam}</td>
                  <td>{cand.position || "—"}</td>
                  <td>{cand.department || "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Выбрать ${cand.fio || cand.sam}`}
                      onClick={() => onPick(cand)}
                    >
                      Выбрать
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {/* Догрузка выдачи постранично: во время запроса кнопки нет. */}
        {canLoadMore && !searching && !loadingMore && (
          <div className="sed-toolbar sed-mt-8">
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              aria-label="Показать ещё сотрудников"
              onClick={loadMore}
            >
              Показать ещё
            </button>
          </div>
        )}
        {cands.length === 0 && !searching && searchError === "" && (
          <div className="sed-note">
            {query.trim().length < CANDIDATES_MIN_QUERY
              ? "Введите не менее трёх символов ФИО или логина."
              : "Никого не нашли. Искать можно по части ФИО или по логину."}
          </div>
        )}
        <div className="sed-toolbar sed-mt-8">
          <button type="button" className="sed-btn sed-btn--ghost" onClick={onClose}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

// Поле «Заместитель» формы: выбранный логин в readonly-поле, рядом кнопки
// подбора (модалка) и очистки. Логин вручную не вводится — только выбором из
// подбора, иначе в записи окажется опечатка.
function DeputyField(props: { value: string; onChange: (sam: string) => void }) {
  const { value, onChange } = props;
  const [pickerOpen, setPickerOpen] = useState<boolean>(false);

  return (
    <div className="sed-mt-8">
      <div className="sed-sub">Заместитель (логин AD):</div>
      <div className="sed-editor-row sed-editor-row--center">
        <input
          readOnly
          aria-label="Заместитель (логин AD)"
          value={value === "" ? "не выбран" : value}
        />
        <button
          type="button"
          className="sed-btn"
          aria-label="Выбрать заместителя"
          onClick={() => setPickerOpen(true)}
        >
          Выбрать заместителя
        </button>
        {value !== "" && (
          <button
            type="button"
            className="sed-btn sed-btn--ghost"
            aria-label="Очистить поле «Заместитель»"
            onClick={() => onChange("")}
          >
            Очистить
          </button>
        )}
      </div>
      {value === "" && (
        <div className="sed-note">Выберите сотрудника кнопкой выше — без него запись не сохранится.</div>
      )}
      {pickerOpen && (
        <DeputySearchModal
          onPick={(cand) => {
            onChange(cand.sam);
            setPickerOpen(false);
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}

// Вкладка «Заместители»: список своих записей и форма их правки. Записи
// сохраняются своими запросами сразу (общей кнопки «Сохранить» на вкладке нет —
// как на «Бланках» и «Шагах» в настройках).
export function Substitutes() {
  const [items, setItems] = useState<Substitute[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [form, setForm] = useState<SubstituteForm>(emptyForm);
  // Признак «действует сегодня» — один раз на отрисовку, а не на каждую строку.
  const [today, setToday] = useState<string>(todayIso());

  // Список своих записей после любой правки (срок и флаг писем — как в ответе
  // сервера); ошибка загрузки не стирается успешной правкой, только перезагрузкой.
  function loadList(): void {
    setLoadError("");
    getMySubstitutes()
      .then((data) => {
        setItems(data);
        setToday(todayIso());
      })
      .catch((e: unknown) =>
        setLoadError(e instanceof Error ? e.message : "Ошибка загрузки заместителей"),
      )
      .finally(() => setLoading(false));
  }

  useEffect(loadList, []);

  // Создание/правка своей записи. На клиенте проверяем очевидное (не выбран
  // заместитель, «по» раньше «с»); дубль пары и прочее отвергает сервер.
  async function handleSaveForm(): Promise<void> {
    const deputy = form.deputy_sam.trim();
    if (deputy === "") {
      setError("Выберите заместителя");
      return;
    }
    if (form.valid_from === "") {
      setError("Укажите дату, с которой заместитель действует");
      return;
    }
    // Даты — ISO YYYY-MM-DD, поэтому сравнение строк равно сравнению дат.
    const validTo = form.valid_to === "" ? null : form.valid_to;
    if (validTo !== null && validTo < form.valid_from) {
      setError("Дата «действует по» раньше даты «действует с»");
      return;
    }
    setError("");
    setSaved("");
    setBusy(true);
    const payload = {
      deputy_sam: deputy,
      valid_from: form.valid_from,
      valid_to: validTo,
      notify: form.notify,
      note: form.note.trim(),
    };
    try {
      if (form.id === null) {
        await createMySubstitute(payload);
        setSaved("Заместитель добавлен");
        setForm(emptyForm());
      } else {
        await updateMySubstitute(form.id, payload);
        setSaved("Запись о заместителе сохранена");
      }
      loadList();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка сохранения записи о заместителе");
    } finally {
      setBusy(false);
    }
  }

  // Удаление своей записи с подтверждением; правящаяся запись сбрасывается.
  async function handleDelete(item: Substitute): Promise<void> {
    if (!window.confirm(`Удалить запись «Я → ${item.deputy_sam}»?`)) return;
    setError("");
    setSaved("");
    setBusy(true);
    try {
      await deleteMySubstitute(item.id);
      setSaved("Запись о заместителе удалена");
      if (form.id === item.id) setForm(emptyForm());
      loadList();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка удаления записи о заместителе");
    } finally {
      setBusy(false);
    }
  }

  const editing = form.id !== null;

  return (
    <section className="sed-panel">
      <header className="sed-panel__head">
        <h2>Мои заместители</h2>
      </header>
      <div className="sed-note">
        Здесь ваши записи: видите и правите только их. Если заместителя должен
        назначить руководитель — обратитесь к администратору.
      </div>
      {loading && <div className="sed-note">Загрузка заместителей…</div>}
      {loadError && <div role="alert">Заместители: {loadError}</div>}
      {!loading && !loadError && items.length === 0 && (
        <div className="sed-note">Заместителей не назначено</div>
      )}
      {items.length > 0 && (
        <table className="sed-table" aria-label="Мои заместители">
          <thead>
            <tr>
              <th scope="col">Сотрудник</th>
              <th scope="col">Заместитель</th>
              <th scope="col">Срок</th>
              <th scope="col">Действует</th>
              <th scope="col">Письма</th>
              <th scope="col">Комментарий</th>
              <th scope="col">Действия</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className={item.id === form.id ? "sed-table__row--active" : undefined}>
                {/* Сотрудник в списке один и тот же — он и есть вошедший. */}
                <td>Я</td>
                <td>{item.deputy_sam}</td>
                {/* Срок — датами без времени (ISO YYYY-MM-DD), общим хелпером:
                    formatDate из date-format.ts такую дату увел бы на день. */}
                <td>{substitutePeriodLabel(item)}</td>
                <td>{isActiveToday(item, today) ? "да" : "нет"}</td>
                <td>{item.notify ? "да" : "нет"}</td>
                <td>{item.note}</td>
                <td>
                  <div className="sed-toolbar sed-mt-0">
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Править запись ${item.deputy_sam}`}
                      onClick={() =>
                        setForm({
                          id: item.id,
                          deputy_sam: item.deputy_sam,
                          valid_from: item.valid_from,
                          valid_to: item.valid_to ?? "",
                          notify: item.notify,
                          note: item.note,
                        })
                      }
                    >
                      Править
                    </button>
                    <button
                      type="button"
                      className="sed-btn sed-btn--ghost"
                      aria-label={`Удалить запись ${item.deputy_sam}`}
                      onClick={() => void handleDelete(item)}
                      disabled={busy}
                    >
                      Удалить
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Форма: новая запись или правка выбранной. */}
      <div className="sed-editor-card">
        <div className="sed-blockcard__head">
          <span className="sed-blockcard__title">
            {editing ? `Правка записи: ${form.deputy_sam}` : "Новый заместитель"}
          </span>
          <span className="sed-blockcard__spacer" />
          {editing && (
            <button
              type="button"
              className="sed-btn sed-btn--ghost"
              onClick={() => setForm(emptyForm())}
            >
              Отменить правку
            </button>
          )}
        </div>

        <DeputyField
          value={form.deputy_sam}
          onChange={(sam) => setForm({ ...form, deputy_sam: sam })}
        />

        <div className="sed-editor-row">
          <label className="sed-field">
            Действует с
            <input
              type="date"
              aria-label="Заместитель действует с"
              value={form.valid_from}
              onChange={(e) => setForm({ ...form, valid_from: e.target.value })}
            />
          </label>
          <label className="sed-field">
            Действует по
            <input
              type="date"
              aria-label="Заместитель действует по"
              value={form.valid_to}
              onChange={(e) => setForm({ ...form, valid_to: e.target.value })}
            />
            <div className="sed-note">Пусто — бессрочно.</div>
          </label>
          <label className="sed-field">
            <input
              type="checkbox"
              aria-label="Слать письма заместителю"
              checked={form.notify}
              onChange={(e) => setForm({ ...form, notify: e.target.checked })}
            />
            Слать письма заместителю
          </label>
        </div>

        <label className="sed-field">
          Комментарий
          <input
            aria-label="Комментарий к записи заместителя"
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
        </label>

        <div className="sed-toolbar sed-mt-8">
          <button type="button" className="sed-btn" onClick={handleSaveForm} disabled={busy}>
            {editing ? "Сохранить" : "Добавить заместителя"}
          </button>
        </div>
      </div>
      {error && <div role="alert">{error}</div>}
      {saved && <div role="status">{saved}</div>}
    </section>
  );
}

// Отдельный пункт «Сопоставление 1С↔AD»: ручной запуск автосопоставления,
// выдача расхождений и массовое подтверждение связок.
//
// Расхождения наполняет проход автосвязки (POST /api/link_1c_ad/sync — здесь или
// регламент по schedule_ad_links_sync). Администратор видит, что не связалось,
// почему (несколько карточек в 1С / несколько записей в AD / нет записи в AD),
// и подтверждает связи пачкой там, где кандидат в AD один.
// ПДн в примерах/фикстурах вымышленные; в коде значений нет.
import { useCallback, useEffect, useState } from "react";
import type { Role } from "./api-mock";
import {
  confirmLinkDiscrepancies,
  getLinkDiscrepancies,
  syncLinks,
} from "./requests-client";
import type { LinkDiscrepancy } from "./requests-client";
import { getSettings } from "./settings-client";
import type { SettingsData } from "./settings-client";

// Подписи причин расхождений (backend присылает коды, см. app/ad_sync.py).
const REASON_TITLES: Record<string, string> = {
  one_c_duplicate: "В 1С несколько карточек ФИО",
  ad_duplicate: "В AD несколько записей ФИО",
  not_in_ad: "В AD нет записи с ФИО",
};

// Порядок фильтра-причины: сначала те, где решение можно принять сразу.
const REASON_ORDER = ["one_c_duplicate", "ad_duplicate", "not_in_ad"];

// Размер страницы выдачи расхождений (сервер принимает до 200).
const PAGE_SIZE = 50;

interface LinkMatchProps {
  // Раздел доступен администратору: подтверждение связок пишет проверенные
  // связи от имени пользователя. На уровне API раздел открыт и администратору
  // СЭД (sed_admin), но такая роль в интерфейсе пока не выдаётся.
  role: Role;
}

export function LinkMatch(props: LinkMatchProps) {
  const { role } = props;
  const [reason, setReason] = useState("");
  const [query, setQuery] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<LinkDiscrepancy[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState("");
  const [schedule, setSchedule] = useState<string>("");

  const allowed = role === "admin";

  const load = useCallback(async () => {
    if (!allowed) return;
    setLoading(true);
    setError("");
    try {
      const data = await getLinkDiscrepancies({
        reason: reason || undefined,
        q: query || undefined,
        onlyOpen,
        page,
        pageSize: PAGE_SIZE,
      });
      setItems(data.items);
      setCounts(data.counts);
      setTotal(data.total);
      setSelected((prev) => prev.filter((key) => data.items.some((i) => i.key === key)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить расхождения");
    } finally {
      setLoading(false);
    }
  }, [allowed, reason, query, onlyOpen, page]);

  useEffect(() => {
    load();
  }, [load]);

  // Расписание регламентного прохода: показываем, как настроено (только чтение).
  useEffect(() => {
    if (!allowed) return;
    let alive = true;
    getSettings()
      .then((data) => {
        if (!alive) return;
        const value = (data as SettingsData).schedule_ad_links_sync;
        setSchedule(value ? JSON.stringify(value) : "не задано (раз в 7 дней)");
      })
      .catch(() => {
        if (alive) setSchedule("не удалось прочитать настройки");
      });
    return () => {
      alive = false;
    };
  }, [allowed]);

  if (!allowed) {
    return (
      <section className="sed-panel">
        <div role="alert" className="sed-note">
          Раздел доступен администратору: подтверждать сопоставление 1С↔AD может только он.
        </div>
      </section>
    );
  }

  const run = async () => {
    setRunning(true);
    setError("");
    setReport("");
    try {
      const result = await syncLinks();
      const parts = Object.entries(result.discrepancies || {}).map(
        ([code, count]) => `${REASON_TITLES[code] ?? code}: ${count}`,
      );
      setReport(
        `Просмотрено ${result.scanned}, создано связок ${result.created}, ` +
          `расхождений ${result.discrepancies_saved ?? 0}${
            parts.length ? ` (${parts.join("; ")})` : ""
          }`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска сопоставления");
    } finally {
      setRunning(false);
    }
  };

  const confirm = async (keys: string[]) => {
    if (keys.length === 0) return;
    setBusy(true);
    setError("");
    setReport("");
    try {
      const result = await confirmLinkDiscrepancies(keys);
      const tail =
        result.skipped.length > 0
          ? `; пропущено (выбор за человеком): ${result.skipped.length}`
          : "";
      setReport(
        `Подтверждено связок: ${result.linked}${tail}` +
          (result.errors.length ? `; ошибок: ${result.errors.length}` : ""),
      );
      setSelected([]);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось подтвердить связки");
    } finally {
      setBusy(false);
    }
  };

  const confirmable = items.filter((item) => item.can_confirm);
  const recommended = confirmable.filter((item) => item.recommended);
  const allSelected = confirmable.length > 0 && selected.length === confirmable.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="sed-panel">
      <header className="sed-panel__head">
        <h2>Сопоставление 1С↔AD</h2>
        <button type="button" onClick={run} disabled={running}>
          {running ? "Сопоставляем…" : "Запустить сопоставление"}
        </button>
      </header>

      <p className="sed-note">
        Проход связывает сотрудников по точному ФИО. Расхождения — то, что связать
        автоматически не вышло: их нужно подтвердить или выбрать вручную. Регламент:
        {" "}
        {schedule}. Увольнение определяется датой из 1С, учётку в AD отключают вручную.
      </p>

      {report && <div className="sed-note">{report}</div>}
      {error && (
        <div role="alert" className="sed-note">
          {error}
        </div>
      )}

      <div className="sed-filterbar" aria-label="Фильтры расхождений">
        <input
          aria-label="Поиск расхождений"
          placeholder="ФИО, табельный номер или логин"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setPage(1);
          }}
        />
        <select
          aria-label="Причина"
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            setPage(1);
          }}
        >
          <option value="">Все причины</option>
          {REASON_ORDER.filter((code) => counts[code]).map((code) => (
            <option key={code} value={code}>
              {REASON_TITLES[code]} — {counts[code]}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            aria-label="Только неподтверждённые"
            checked={onlyOpen}
            onChange={(e) => {
              setOnlyOpen(e.target.checked);
              setPage(1);
            }}
          />
          Только неподтверждённые
        </label>
        <button type="button" onClick={load} disabled={loading}>
          Обновить
        </button>
      </div>

      <div className="sed-note" role="status">
        Всего расхождений: {total} · на странице: {items.length} · подтверждаемых:{" "}
        {confirmable.length} · рекомендованных к подтверждению: {recommended.length}
      </div>

      <div className="sed-actions">
        <button
          type="button"
          disabled={busy || selected.length === 0}
          onClick={() => confirm(selected)}
        >
          Подтвердить выбранные ({selected.length})
        </button>
        <button
          type="button"
          disabled={busy || confirmable.length === 0}
          onClick={() => confirm(confirmable.map((item) => item.key))}
        >
          Подтвердить все на странице ({confirmable.length})
        </button>
        <button
          type="button"
          disabled={busy || recommended.length === 0}
          onClick={() => confirm(recommended.map((item) => item.key))}
        >
          Подтвердить рекомендованные ({recommended.length})
        </button>
        <label>
          <input
            type="checkbox"
            aria-label="Выбрать все подтверждаемые"
            checked={allSelected}
            onChange={(e) =>
              setSelected(e.target.checked ? confirmable.map((item) => item.key) : [])
            }
          />
          Выбрать все подтверждаемые на странице
        </label>
      </div>

      <table className="sed-table">
        <thead>
          <tr>
            <th scope="col">Выбрать</th>
            <th scope="col">Сотрудник 1С</th>
            <th scope="col">Причина</th>
            <th scope="col">Кандидат в AD</th>
            <th scope="col">Должность/служба</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.key}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`Выбрать ${item.fio}`}
                  disabled={!item.can_confirm}
                  checked={selected.includes(item.key)}
                  onChange={(e) =>
                    setSelected((prev) =>
                      e.target.checked
                        ? [...prev, item.key]
                        : prev.filter((key) => key !== item.key),
                    )
                  }
                />
              </td>
              <td>
                {item.fio}
                <div className="sed-cell-sub">
                  таб. {item.tab_num}
                  {item.sibling_tabs.length > 1
                    ? ` · в 1С также: ${item.sibling_tabs.filter((t) => t !== item.tab_num).join(", ")}`
                    : ""}
                </div>
              </td>
              <td>
                {REASON_TITLES[item.reason] ?? item.reason}
                {item.recommended && <span className="sed-badge">совпадает</span>}
              </td>
              <td>
                {item.ad_fio || "—"}
                <div className="sed-cell-sub">
                  {item.ad_sam || (item.candidates.length ? item.candidates.join(", ") : "—")}
                </div>
              </td>
              <td>
                {item.ad_title || "—"}
                <div className="sed-cell-sub">
                  1С: {item.one_c_position || "—"}
                  {item.one_c_dept ? `, ${item.one_c_dept}` : ""}
                </div>
              </td>
            </tr>
          ))}
          {items.length === 0 && !loading && (
            <tr>
              <td colSpan={5}>
                Расхождений нет — запустите сопоставление или снимите фильтры.
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="sed-actions">
        <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Назад
        </button>
        <span>
          Страница {page} из {pages}
        </span>
        <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>
          Вперёд
        </button>
      </div>
    </section>
  );
}
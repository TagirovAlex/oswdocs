// Показ дат и времени в одной зоне — настройке админа (settings.timezone,
// её отдаёт /auth/me). Зона ставится один раз при входе в приложение
// (main.tsx), дальше все даты в интерфейсе приводятся к ней.
//
// Зачем так, а не «как у браузера»: сотрудник может быть в другом поясе или
// в браузере выставлена другая зона — тогда одни и те же даты в списке и в
// карточке выглядели бы по-разному. Метки времени приходят с сервера с
// настоящим смещением (Postgres отдаёт UTC), приведение к зоне — только на
// показе: хранимое остаётся в UTC.
//
// Зона не задана (настройка снята или старый API) — берём зону контейнера TZ,
// а если и её нет, зону браузера.
//
// ОСТОРОЖНО, date-only строки сюда приносить не надо: hire_date и
// dismissal_date из 1С — даты без времени («2026-10-01»); new Date() считает
// их полуночью UTC и в зоне с отрицательным смещением уедет на день назад.
// Для таких полей остаётся срез строки, а не форматирование здесь.

// Зона показа; пустая строка — «по умолчанию браузера».
let displayTimezone = "";

// Мусорную зону отвергаем на входе: Intl для неизвестной зоны бросает
// RangeError, и дата просто не показалась бы (вместо того, чтобы показаться
// в зоне браузера). Сервер такое значение и не примет (422), но защита нужна
// и на показе — старый ответ API или правка руками.
export function setDisplayTimezone(timezone: string | null | undefined): void {
  const name = (timezone ?? "").trim();
  if (!name) {
    displayTimezone = "";
    return;
  }
  try {
    new Intl.DateTimeFormat("ru-RU", { timeZone: name });
    displayTimezone = name;
  } catch {
    displayTimezone = "";
  }
}

export function displayTimezoneOrDefault(): string {
  return displayTimezone;
}

// Дата и время: «01.10.2026, 09:30:00». При битой строке — как пришла
// (не показываем пользователю «Invalid Date»).
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", { timeZone: displayTimezone || undefined });
}

// Только дата: «01.10.2026».
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("ru-RU", { timeZone: displayTimezone || undefined });
}

// Дата и время до минут: «01.10.2026, 09:30» — для меток, у которых важны
// часы:минуты (имя файла бэкапа {ts} собирается так же).
export function formatDateTimeHM(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: displayTimezone || undefined,
  });
}
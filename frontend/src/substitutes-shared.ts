// Общие куски разделов «Заместители»: админский справочник («Настройки» →
// «Заместители», блок прав settings_substitutes) и личный раздел сотрудника.
// Здесь только показ срока записи и тип кандидата в заместители — сами логины и
// ФИО приходят из API, в коде их нет (AGENTS.md п.3).
//
// Даты срока приходят БЕЗ времени (ISO YYYY-MM-DD), как hire_date/dismissal_date
// из 1С. Форматировать их через formatDate нельзя: new Date() считает такую
// строку полуночью UTC и в зоне с отрицательным смещением увел бы на день
// назад (осторожно в date-format.ts). Поэтому дата берётся срезом ISO-строки,
// а сегодняшний день считается через formatDate — зона у него одна на всё
// приложение (settings.timezone, см. date-format.ts).
import { formatDate } from "./date-format";

// Срок действия записи о заместителе — часть типа Substitute из
// settings-client (там же логины, письма и комментарий).
export interface SubstitutePeriod {
  // Дата «действует с» без времени (ISO YYYY-MM-DD).
  valid_from: string;
  // Дата «действует по» без времени; null — бессрочно.
  valid_to: string | null;
}

// Кандидат в заместители из GET /api/substitutes/candidates: логин AD, ФИО,
// должность и подразделение для показа в модалке выбора. Отдельная ручка
// подбора, а не /api/ad/search: последний закрыт блоком прав
// employee_directory, которого у рядового сотрудника нет.
export interface SubstituteCandidate {
  // Логин AD (sAMAccountName) — его и хранит запись о заместителе.
  sam: string;
  // ФИО для показа.
  fio: string;
  // Должность из карточки сотрудника.
  position: string;
  // Подразделение из карточки сотрудника.
  department: string;
}

// Дата без времени по срезу ISO-строки: «2026-10-01T00:00:00Z» → «2026-10-01».
// Пустое значение — пустая строка (срок не задан).
export function isoDateOnly(value: string | null | undefined): string {
  if (!value) return "";
  return value.slice(0, 10);
}

// Сегодняшняя дата ISO (YYYY-MM-DD) в зоне показа. Зона выбирается ТОЛЬКО в
// date-format.ts, поэтому берём её оттуда же: formatDate отдаёт «ДД.ММ.ГГГГ»
// в зоне настроек, а срок заместителя приходит без времени — приводим
// показанную дату обратно в ISO, чтобы сравнение строк было сравнением дат.
export function todayIso(): string {
  const parts = formatDate(new Date().toISOString()).split(".");
  if (parts.length !== 3) return "";
  return `${parts[2]}-${parts[1]}-${parts[0]}`;
}

// Запись действует на сегодня: срок «с» не позже сегодняшнего дня, а пустой
// «по» (бессрочно) или «по» не раньше сегодняшнего дня.
export function isActiveToday(period: SubstitutePeriod, today: string): boolean {
  if (today === "") return false;
  if (isoDateOnly(period.valid_from) > today) return false;
  const validTo = isoDateOnly(period.valid_to);
  return validTo === "" || validTo >= today;
}

// Срок записи одной строкой: «с 2026-10-01 по бессрочно».
export function substitutePeriodLabel(period: SubstitutePeriod): string {
  const from = isoDateOnly(period.valid_from);
  const validTo = isoDateOnly(period.valid_to);
  return `с ${from} по ${validTo === "" ? "бессрочно" : validTo}`;
}
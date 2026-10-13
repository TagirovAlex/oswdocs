// Показ дат в зоне настройки админа (settings.timezone): метка времени с
// сервера (UTC) приводится к зоне компании, а не к зоне браузера.
import { afterEach, describe, expect, it } from "vitest";
import { formatDate, formatDateTime, setDisplayTimezone } from "./date-format";

// 2026-10-01T21:30:00Z — в Europe/Moscow это уже 2 октября 00:30: тот самый
// случай, из-за которого даты «убегали»: по UTC дата 1-го, по зоне компании
// дата 2-го.
const CROSS_MIDNIGHT = "2026-10-01T21:30:00+00:00";

describe("date-format: зона показа дат", () => {
  afterEach(() => setDisplayTimezone(null));

  it("зона из настройки применяется, а не зона машины", () => {
    setDisplayTimezone("Europe/Moscow");
    expect(formatDateTime(CROSS_MIDNIGHT)).toContain("02.10.2026");
    expect(formatDateTime(CROSS_MIDNIGHT)).toContain("00:30");
  });

  it("разные зоны дают разные даты для одной метки", () => {
    // Метка 21:30 UTC: в Москве уже 2-е (UTC+3), в Калининграде ещё 1-е (UTC+2),
    // по UTC — 1-е. Ровно тот случай, ради которого зона вынесена в настройку.
    setDisplayTimezone("Europe/Moscow");
    expect(formatDate(CROSS_MIDNIGHT)).toBe("02.10.2026");
    setDisplayTimezone("Europe/Kaliningrad");
    expect(formatDate(CROSS_MIDNIGHT)).toBe("01.10.2026");
    setDisplayTimezone("UTC");
    expect(formatDate(CROSS_MIDNIGHT)).toBe("01.10.2026");
  });

  it("зона не задана — показ как раньше (зона браузера), даты не пропадают", () => {
    setDisplayTimezone(null);
    expect(formatDateTime(CROSS_MIDNIGHT)).not.toBe("");
    expect(formatDate(CROSS_MIDNIGHT)).not.toBe("");
  });

  it("мусорная зона игнорируется — даты всё равно показываются", () => {
    // Сервер такое значение не примет (422), но защита на стороне показа:
    // Intl на неизвестной зоне бросает исключение, и даты бы просто исчезли.
    setDisplayTimezone("Марс/Средний");
    expect(formatDate(CROSS_MIDNIGHT)).not.toBe("");
  });

  it("пустая метка и битая дата не ломают показ", () => {
    setDisplayTimezone("Europe/Moscow");
    expect(formatDateTime("")).toBe("");
    expect(formatDate(null)).toBe("");
    expect(formatDateTime("не дата")).toBe("не дата");
  });
});
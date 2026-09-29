// Тесты мок API: ролевая обрезка ПДн уже на уровне мока.
import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS, MockForbidden, mockApi } from "./api-mock";

describe("mockApi", () => {
  // ОК видит полную карточку с вымышленными ПДн.
  it("отдаёт полную карточку роли ОК", async () => {
    const card = await mockApi.getEmployee("REQ-001", "hr");
    expect(card.kind).toBe("full");
    if (card.kind === "full") {
      expect(card.fio).toContain("Петров");
      expect(card.tabNum).toBeTruthy();
      expect(card.mail).toBeTruthy();
    }
  });

  // Владелец получает урезанную карточку без ПДн.
  it("урезанная карточка владельцу не содержит ПДн", async () => {
    const card = await mockApi.getEmployee("REQ-001", "owner");
    expect(card.kind).toBe("brief");
    // Полных полей нет даже в ключах объекта.
    expect(card).not.toHaveProperty("fio");
    expect(card).not.toHaveProperty("tabNum");
    expect(card).not.toHaveProperty("mail");
    expect(card).not.toHaveProperty("manager");
    if (card.kind === "brief") {
      expect(card.employeeLabel).toContain("Сотрудник №");
    }
  });

  // Владелец не видит чужие задачи.
  it("владелец не открывает чужую заявку", async () => {
    await expect(mockApi.getEmployee("REQ-003", "owner")).rejects.toBeInstanceOf(MockForbidden);
  });

  // Гостю недоступны список и карточка.
  it("гостю всё закрыто", async () => {
    await expect(mockApi.getRequests("agreement", EMPTY_FILTERS, "guest")).rejects.toBeInstanceOf(
      MockForbidden,
    );
    await expect(mockApi.getEmployee("REQ-001", "guest")).rejects.toBeInstanceOf(MockForbidden);
    await expect(mockApi.getFolders("guest")).resolves.toEqual([]);
  });

  // Настройки — только админам.
  it("настройки только для админа", async () => {
    await expect(mockApi.getSettings("admin")).resolves.toBeTruthy();
    await expect(mockApi.getSettings("hr")).rejects.toBeInstanceOf(MockForbidden);
  });
});

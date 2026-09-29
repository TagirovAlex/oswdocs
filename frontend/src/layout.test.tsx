// Тесты сетки скелета: вкладки, дерево, тулбар, фильтры, таблица, выход.
// Роль приходит из сессии (props), селектора ролей на экране нет.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SedLayout } from "./layout";
import { ThemeProvider } from "./theme";
import type { Role } from "./api-mock";

// Обёртка с темой для рендера каркаса.
function renderWithTheme(role: Role = "hr", onLogout: () => void = () => undefined) {
  return render(
    <ThemeProvider initial="light">
      <SedLayout role={role} onLogout={onLogout} />
    </ThemeProvider>,
  );
}

describe("SedLayout", () => {
  // Базовая сетка для роли ОК.
  it("показывает вкладки, папки, тулбар, фильтры и таблицу", async () => {
    renderWithTheme("hr");
    // Вкладки (ОК: без «Настроек»).
    expect(screen.getByText("Заявки")).toBeInTheDocument();
    expect(screen.getByText("Создание")).toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
    // Тулбар.
    expect(screen.getByText("Создать заявку")).toBeInTheDocument();
    expect(screen.getByText("Печать")).toBeInTheDocument();
    // Фильтры.
    expect(screen.getByLabelText("Поиск")).toBeInTheDocument();
    expect(screen.getByLabelText("Предприятие")).toBeInTheDocument();
    // Дерево и таблица подгрузятся из мока.
    await waitFor(() => expect(screen.getByText("На согласовании")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("REQ-001")).toBeInTheDocument());
  });

  // Владельцу не светят ПДн даже в таблице.
  it("владелец видит маски вместо ФИО", async () => {
    renderWithTheme("owner");
    await waitFor(() => expect(screen.getByText("Сотрудник № 101")).toBeInTheDocument());
    expect(screen.queryByText(/Петров Пётр/)).not.toBeInTheDocument();
  });

  // Селектор ролей убран: роль приходит из сессии.
  it("селектора ролей на экране нет", () => {
    renderWithTheme("hr");
    expect(screen.queryByLabelText("Роль пользователя")).not.toBeInTheDocument();
  });

  // Вкладка «Настройки» — только админу.
  it("вкладка «Настройки» видна только админу", () => {
    renderWithTheme("admin");
    expect(screen.getByText("Настройки")).toBeInTheDocument();
    expect(screen.getByText("Создание")).toBeInTheDocument();
  });

  // Вкладка «Создание» — только ОК и админу.
  it("вкладка «Создание» скрыта у владельца", () => {
    renderWithTheme("owner");
    expect(screen.queryByText("Создание")).not.toBeInTheDocument();
    expect(screen.queryByText("Настройки")).not.toBeInTheDocument();
  });

  // Кнопка «Выйти» вызывает сброс сессии.
  it("кнопка «Выйти» вызывает onLogout", () => {
    const onLogout = vi.fn();
    renderWithTheme("hr", onLogout);
    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });
});
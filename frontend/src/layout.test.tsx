// Тесты сетки скелета: вкладки, дерево, тулбар, фильтры, таблица.
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SedLayout } from "./layout";
import { ThemeProvider } from "./theme";

// Обёртка с темой для рендера каркаса.
function renderWithTheme(role: "hr" | "owner" | "guest" = "hr") {
  return render(
    <ThemeProvider initial="light">
      <SedLayout initialRole={role} />
    </ThemeProvider>,
  );
}

describe("SedLayout", () => {
  // Базовая сетка для роли ОК.
  it("показывает вкладки, папки, тулбар, фильтры и таблицу", async () => {
    renderWithTheme("hr");
    // Вкладки.
    expect(screen.getByText("Заявки")).toBeInTheDocument();
    expect(screen.getByText("Создание")).toBeInTheDocument();
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
});

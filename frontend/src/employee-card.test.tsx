// Тесты полной карточки (волна B4): пять блоков для ОК, владельцу ПДн не светят.
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmployeeCard } from "./employee-card";

describe("EmployeeCard", () => {
  // ОК видит все пять блоков с ПДн.
  it("показывает пять блоков роли ОК", async () => {
    render(<EmployeeCard requestId="REQ-001" role="hr" />);
    await waitFor(() => expect(screen.getAllByText(/Петров Пётр/).length).toBeGreaterThan(0));
    expect(screen.getByLabelText("Данные 1С")).toBeInTheDocument();
    expect(screen.getByLabelText("Данные AD")).toBeInTheDocument();
    expect(screen.getByLabelText("Маршрут")).toBeInTheDocument();
    expect(screen.getByText(/Бегунок v1/)).toBeInTheDocument();
    expect(screen.getByText(/Заявка создана/)).toBeInTheDocument();
    // Остаток отпуска — только ОК.
    expect(screen.getAllByText(/только ОК/).length).toBeGreaterThan(0);
  });

  // ОБЯЗАТЕЛЬНЫЙ: владельцу ПДн не светят даже через полную карточку.
  it("владельцу не показывает ПДн", async () => {
    const { container } = render(<EmployeeCard requestId="REQ-001" role="owner" />);
    // Ждать появления заглушки без ПДн, затем проверить отсутствие ПДн.
    await waitFor(() => expect(screen.getByText(/урезанная карточка без ПДн/)).toBeInTheDocument());
    // Блоков полной карточки нет.
    expect(screen.queryByLabelText("Данные 1С")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Данные AD")).not.toBeInTheDocument();
    const html = container.innerHTML;
    expect(html).not.toContain("Петров Пётр");
    expect(html).not.toContain("Т-000101");
    expect(html).not.toContain("petrov.pp@example.local");
    expect(html).not.toContain("Начальник цеха");
    expect(screen.getByText(/урезанная карточка без ПДн/)).toBeInTheDocument();
  });

  // Гостю карточка закрыта.
  it("гостю показывает ошибку доступа", async () => {
    render(<EmployeeCard requestId="REQ-001" role="guest" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});

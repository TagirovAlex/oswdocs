// Тесты формы создания ОК (волна B4): мастер предприятие→сотрудник→маршрут→печать.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CreateForm } from "./create-form";

describe("CreateForm", () => {
  // Полный проход мастера роли ОК.
  it("ведёт ОК по четырём шагам до печати", () => {
    render(<CreateForm role="hr" />);
    // Шаг 1: предприятие.
    fireEvent.change(screen.getByLabelText("Предприятие"), { target: { value: "Завод «Север»" } });
    fireEvent.click(screen.getByText("Далее"));
    // Шаг 2: сотрудник.
    fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "Т-000201" } });
    fireEvent.click(screen.getByText("Далее"));
    // Шаг 3: маршрут.
    expect(screen.getByText("Маршрут согласования")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Далее"));
    // Шаг 4: печать.
    expect(screen.getByLabelText("Версия бегунка")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Создать и отправить на печать"));
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  // Без предприятия дальше не пускает.
  it("не пускает дальше без предприятия", () => {
    render(<CreateForm role="hr" />);
    expect(screen.getByText("Далее")).toBeDisabled();
  });

  // Не-ОК создание закрыто.
  it("владельцу и гостю создание закрыто", () => {
    render(<CreateForm role="owner" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/только ОК/);
  });
});

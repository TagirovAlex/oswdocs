// Тесты урезанной карточки владельца (волна B4).
// ОБЯЗАТЕЛЬНЫЙ: ПДн не светят владельцу — проверка по всему документу.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OwnerView } from "./owner-view";

describe("OwnerView", () => {
  // ОБЯЗАТЕЛЬНЫЙ тест: ни ФИО, ни таб.№, ни почты, ни руководителя.
  it("не показывает ПДн владельцу", async () => {
    const { container } = render(<OwnerView requestId="REQ-001" />);
    await waitFor(() => expect(screen.getByText("Сотрудник № 101")).toBeInTheDocument());
    const html = container.innerHTML;
    expect(html).not.toContain("Петров");
    expect(html).not.toContain("Т-000101");
    expect(html).not.toContain("example.local");
    expect(html).not.toContain("Начальник цеха");
    expect(html).not.toContain("petrov.pp");
    // Маска и шаг — на месте.
    expect(screen.getByText("Бухгалтерия")).toBeInTheDocument();
  });

  // Чужая задача владельцу недоступна.
  it("чужая заявка закрыта", async () => {
    render(<OwnerView requestId="REQ-003" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  // Возврат без комментария запрещён, согласование — можно.
  it("требует комментарий при возврате", async () => {
    render(<OwnerView requestId="REQ-001" />);
    await waitFor(() => expect(screen.getByText("Сотрудник № 101")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Вернуть"));
    expect(screen.getByRole("alert")).toHaveTextContent(/обязателен/);
    fireEvent.change(screen.getByLabelText("Комментарий"), { target: { value: "Нужна доработка" } });
    fireEvent.click(screen.getByText("Вернуть"));
    expect(screen.getByRole("status")).toHaveTextContent(/Возвращено/);
  });
});

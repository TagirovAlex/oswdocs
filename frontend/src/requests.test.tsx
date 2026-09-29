// Тесты экрана списка (волна B4): фильтры, таблица, ролевая обрезка, fetch-перехват.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { installMockFetch } from "./api-mock";
import { RequestsScreen } from "./requests";

describe("RequestsScreen", () => {
  // ОК видит полные подписи.
  it("показывает заявки роли ОК", async () => {
    render(<RequestsScreen role="hr" />);
    await waitFor(() => expect(screen.getByText("REQ-001")).toBeInTheDocument());
    expect(screen.getByText(/Петров Пётр/)).toBeInTheDocument();
  });

  // Владелец видит только маски.
  it("владельцу показывает маски вместо ФИО", async () => {
    render(<RequestsScreen role="owner" />);
    await waitFor(() => expect(screen.getByText("Сотрудник № 101")).toBeInTheDocument());
    expect(screen.queryByText(/Петров Пётр/)).not.toBeInTheDocument();
  });

  // Гостю список закрыт.
  it("гостю показывает ошибку доступа", async () => {
    render(<RequestsScreen role="guest" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  // Фильтр поиска сужает список.
  it("фильтр поиска работает", async () => {
    render(<RequestsScreen role="hr" />);
    await waitFor(() => expect(screen.getByText("REQ-001")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Поиск"), { target: { value: "Сидорова" } });
    await waitFor(() => expect(screen.getByText("REQ-002")).toBeInTheDocument());
    expect(screen.queryByText("REQ-001")).not.toBeInTheDocument();
  });

  // Перехват fetch для /api/* отдаёт список из мока.
  it("installMockFetch отдаёт /api/requests", async () => {
    const restore = installMockFetch("hr");
    try {
      const res = await fetch("/api/requests");
      expect(res.status).toBe(200);
      const data = await res.json();
      expect(Array.isArray(data)).toBe(true);
      expect(data.length).toBeGreaterThan(0);
    } finally {
      restore();
    }
  });
});

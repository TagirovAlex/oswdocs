// Тесты админки настроек (волна B4): только SED_ADMINS.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AdminSettings } from "./admin-settings";

describe("AdminSettings", () => {
  // Админ видит и сохраняет настройки.
  it("админ правит TTL и лимиты", async () => {
    render(<AdminSettings role="admin" />);
    await waitFor(() => expect(screen.getByLabelText("TTL отметок")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("TTL отметок"), { target: { value: "7" } });
    fireEvent.click(screen.getByText("Сохранить"));
    expect(screen.getByRole("status")).toHaveTextContent(/TTL=7/);
  });

  // ОК и владелец в админку не пускаются.
  it("не-админам доступ закрыт", async () => {
    render(<AdminSettings role="hr" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});

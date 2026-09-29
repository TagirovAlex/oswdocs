// Точка входа скелета: тема + каркас.
// Пароли и токены нигде не хранятся (логин — через /auth на волне B1).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { SedLayout } from "./layout";
import { ThemeProvider } from "./theme";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Нет корневого элемента #root");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider>
      <SedLayout initialRole="hr" />
    </ThemeProvider>
  </StrictMode>,
);

// Тема оформления: светлая в синей гамме, задел под тёмную.
// Все цвета — только через CSS-переменные (см. theme.css).
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import "./theme.css";

// Доступные темы (тёмная — задел, по умолчанию светлая).
export type ThemeName = "light" | "dark";

// Ключ хранения выбора темы в браузере.
const STORAGE_KEY = "sed-theme";

interface ThemeContextValue {
  // Текущая тема.
  theme: ThemeName;
  // Переключить тему (светлая/тёмная).
  setTheme: (next: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "light",
  setTheme: () => undefined,
});

// Провайдер темы: выставляет атрибут data-theme на <html>.
export function ThemeProvider(props: { children: ReactNode; initial?: ThemeName }) {
  // Начальная тема: из пропса, иначе из хранилища, иначе светлая.
  const [theme, setThemeState] = useState<ThemeName>(() => {
    if (props.initial) return props.initial;
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      return saved === "dark" ? "dark" : "light";
    } catch {
      return "light";
    }
  });

  // При смене темы обновить атрибут и сохранить выбор.
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Игнорировать: приватный режим и т.п.
    }
  }, [theme]);

  const setTheme = useCallback((next: ThemeName) => {
    setThemeState(next);
  }, []);

  return <ThemeContext.Provider value={{ theme, setTheme }}>{props.children}</ThemeContext.Provider>;
}

// Хук доступа к текущей теме.
export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

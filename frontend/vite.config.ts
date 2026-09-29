// Конфиг сборки скелета SPA (волна A5).
// Статика собирается и отдаётся через proxy (nginx), отдельного рантайма нет.
//vitest/config расширяет defineConfig полем test.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Тестовый сервер — только локальный интерфейс (правила автономности).
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    // Окружение DOM для тестов скелета.
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});

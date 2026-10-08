// Подключение расширений jest-dom для тестов скелета.
import "@testing-library/jest-dom/vitest";

// jsdom не реализует геометрию выделения (Range.getClientRects/getBoundingClientRect),
// а редактор TipTap спрашивает её при возврате фокуса в поле (scrollToSelection).
// Заглушки нулевой геометрии: без них любая кнопка форматирования в тестах падает.
if (typeof Range !== "undefined") {
  if (!Range.prototype.getClientRects) {
    Range.prototype.getClientRects = function getClientRects(): DOMRectList {
      return [] as unknown as DOMRectList;
    };
  }
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = function getBoundingClientRect(): DOMRect {
      return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) } as DOMRect;
    };
  }
}
# СЭД Фаза 0 Задача 3 — сборка статики frontend (скелет).
# Отдельного рантайм-сервиса frontend НЕТ — отдача статики через proxy (nginx).
# Реальные исходники SPA — в Фазе 5; здесь только скелет с заглушкой,
# чтобы контекст сборки не требовал исходников и не тянул лишнее.

# Стадия 1: сборка статики.
FROM node:22-alpine AS build

WORKDIR /app

# Зависимости отдельным слоем; исходников SPA пока нет (Фаза 5).
COPY package*.json ./
RUN if [ -f package.json ]; then npm ci --ignore-scripts; else echo "no SPA sources yet (Phase 5) - stub"; fi

COPY . ./
RUN if [ -f package.json ]; then npm run build; else mkdir -p dist && echo '<!-- stub Phase 0: real SPA in Phase 5 -->' > dist/index.html; fi

# Стадия 2: артефакт статики для proxy.
# Не запускается как отдельный сервис — proxy забирает/монтирует
# содержимое /usr/share/nginx/html.
FROM nginx:stable-alpine AS export
COPY --from=build /app/dist /usr/share/nginx/html

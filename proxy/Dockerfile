# СЭД proxy — образ Nginx. Фаза 0 Задача 2 (devops-proxy).
# База строго по скилу debian-ops / Задаче 1: nginx:stable-alpine.
FROM nginx:stable-alpine

# Конфиг reverse-proxy (server :80 -> 301, :443 ssl, /api/ -> api:8000, / -> статика).
# Шаблон с ${SERVER_NAME}: официальный entrypoint nginx сам делает envsubst
# для /etc/nginx/templates/*.template при старте (SERVER_NAME из compose).
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Статика-заглушка до Фазы 5 (frontend/Dockerfile, Задача 3 / Фаза 5 provision):
# сборка React SPA будет монтироваться/собираться в /usr/share/nginx/html.
COPY html/ /usr/share/nginx/html/

# ЗАПРЕТ: приватный key сюда НЕ копировать.
# Корп. cert/key монтируются только на ВМ read-only:
#   /srv/sed/certs:/etc/nginx/certs:ro  (см. docker-compose.yml Задачи 1).
# SERVER_NAME приходит из окружения сервиса proxy
# (compose: SERVER_NAME=${SERVER_NAME:-sed.company.local});
# дефолт sed.company.local — заглушка, не реальный домен.

EXPOSE 80 443

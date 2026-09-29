---
name: debian-ops
description: Use when editing compose, Nginx certs, systemd on Debian 13 in SED. Triggers on Dockerfile, compose, nginx, certs, volumes, deploy.
---

# Debian 13 Ops

1 ВМ Debian 13 с заделом на разделение app/db без переписывания кода.

## Железные правила

- Сервисы: `proxy (nginx:stable-alpine :80→443 + :443), api, worker, redis:7-alpine, db (postgres:17-trixie)`. Наружу — только 80 (редирект на 443) и 443 (внутри корп. сети). У `db` внешнего порта нет.
- Корп. сертификат монтируется в `proxy` read-only из `/srv/sed/certs`. Приватный ключ в образы не копировать.
- Хост-каталоги: `/srv/sed/{pgdata,files,backups,certs}`. Фронт — статика в `proxy`.
- Разъезд на 2 ВМ — только через `docker-compose.prod-db-external.yml` (вынос `db` + смена `DATABASE_URL`), код не трогать.
- systemd-юнит `sed.service` для `compose up`. Установка чего-либо на локальный ПК агента — только по согласованию (см. AGENTS.md).
- LibreOffice — только в образе `worker` (для PDF), в `api` не тащить.

## Приемка правки

`docker compose config` валиден, стек поднимается на чистой Debian 13, `proxy` отдает 443, данные переживают пересоздание контейнеров (volumes).

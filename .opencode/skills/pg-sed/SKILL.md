---
name: pg-sed
description: Use when writing Postgres migrations, RLS, pg_trgm, backups in SED. Triggers on migrations, Alembic, schema, audit_log, settings seeds.
---

# Postgres SED

Postgres 17 (`postgres:17-trixie`), миграции — Alembic (вверх/вниз).

## Железные правила

- Схема — только из README п.4. Новые колонки с секретами/ПДн — стоп и вопрос.
- `audit_log` — append-only (только INSERT, без UPDATE/DELETE; запретить на уровне прав/триггера).
- Ключ сотрудника составной: `enterprise+base_code+tab_num`.
- Поиск людей — индексы `pg_trgm` на ФИО/`sam`, чувствительность к `ё` учесть в нормализации на стороне API.
- Файлы (сканы, DOCX/PDF) в БД не хранить — только мета + путь в volume.
- Сиды `settings` по умолчанию: группы, `sed_ou`, `approval_ttl_days`, `position_escalation`, `position_to_category`.
- Бэкап: `pg_dump` в `/srv/sed/backups` (путь — в настройках окружения, не в коде).

## Приемка правки

Миграция накатывается и откатывается на чистой БД, сиды идемпотентны, лишний вес индексов обоснован.

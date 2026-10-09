---
name: pg-sed
description: Use when writing Postgres migrations, RLS, pg_trgm, backups in SED. Triggers on migrations, Alembic, schema, audit_log, settings seeds.
---

# Postgres SED

Postgres 17 (`postgres:17-trixie`), миграции — Alembic (вверх/вниз).

## Железные правила

- Схема — только из README п.4. Новые колонки с секретами/ПДн — стоп и вопрос.
- Текущая цепочка миграций: 0001 initial … 0007 ad_position_directory, 0008 справочники
  маршрута, 0009 кадровые данные сотрудника, 0010 link_discrepancies, 0011 detail,
  0012 справочник бланков (blanks/blank_steps + снимок в dismissal_requests), 0013 несколько
  ответственных и режим шага (jsonb `assignees`/`approvals`, `approval_mode`), 0014 самостоятельный
  шаг бланка (`title`/`stage_lines`/`executor_kind`/`assignees`/
  `owner_group`/`optional`/`require_comment`; `blanks.header_html`/`footer_lines`; снимок
  `dismissal_requests.blank_header_html`/`blank_footer_lines`; очистка `blank_steps`/`blanks`;
  удаление ключей `templates`/`position_to_category`), 0015 `request_steps.optional NOT NULL
  DEFAULT TRUE`, 0016 удаление мёртвых ключей `doc_templates`/`position_sets`,
  0017 удаление мёртвых колонок шага бланка (`blank_steps.stage_id`/`optional_override`/
  `require_comment_override`; FK на `approval_stages` и `UNIQUE (blank_id, stage_id)` сняты
  вместе с колонками).
- Откат миграции 0016 ничего не восстанавливает (значения и нерабочие), 0014 при downgrade не
  возвращает прежнее содержимое бланков — состав собирает человек заново; откат 0017 возвращает
  `stage_id` NOT NULL и потому удаляет все текущие шаги бланка (у них `NULL stage_id`) —
  содержимое бланков собирается заново.
- `audit_log` — append-only (только INSERT, без UPDATE/DELETE; запретить на уровне прав/триггера).
- Ключ сотрудника составной: `enterprise+base_code+tab_num`.
- `employees` несёт кадровые данные: `ref_key` (сопоставление с регистром), `dismissal_date`
  (поиск и сопоставление пропускают уволенных), `hr_synced_at`; `link_discrepancies` — выдача
  разбора автосопоставления с `reason` и `resolved_at`; справочник бланков `blanks`/`blank_steps`
  (шаг самостоятельный: порядок + свой текст + свой исполнитель + режим + `optional`; мёртвые
  колонки прежней модели `stage_id`/`optional_override`/`require_comment_override` сняты миграцией
  0017 вместе с FK на `approval_stages` и `UNIQUE (blank_id, stage_id)`; `version` растёт при
  замене состава).
- Поиск людей — индексы `pg_trgm` на ФИО/`sam`, чувствительность к `ё` учесть в нормализации на стороне API.
- Файлы (сканы, DOCX/PDF) в БД не хранить — только мета + путь в volume.
- Сиды `settings` по умолчанию: группы, `sed_ou`, `approval_ttl_days`, `position_escalation`,
  `blank_autopick='off'` (легаси `position_to_category`/`templates`/`doc_templates`/`position_sets`
  из `settings` удалены миграциями 0014/0016 — не возвращать).
- Дефолтов в коде нет: расписания регламентов (`schedule_*_sync`) и метки (`*_synced_at`) —
  ключи settings, читаются `read_setting_value`, пишутся синхронизациями/админом.
- Бэкап: `pg_dump` в `/srv/sed/backups` (путь — в настройках окружения, не в коде).
- Массивы в SQLAlchemy на psycopg3: `= ANY(CAST(:keys AS text[]))` — expanding-bindparam
  даёт «malformed array literal»; JSON-поля — строкой с приведением `::jsonb` (dict psycopg3
  не адаптирует).

## Приемка правки

Миграция накатывается и откатывается на чистой БД, сиды идемпотентны, лишний вес индексов обоснован.

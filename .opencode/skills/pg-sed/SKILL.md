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
  вместе с колонками), 0018 справочник `step_catalog` и единая печать, 0019 числовой код
  вида документа, 0020 справочник блоков/шаблонов прав доступа, 0021 последовательность
  `request_code_seq` + сужение правила триггера `audit_log_no_update_delete`, 0022 настройка
  часового пояса показа дат (`settings.timezone`, сид `Europe/Moscow`), 0023 справочник
  заместителей (`substitutes`: principal_sam/deputy_sam/valid_from/valid_to NULL на
  бессрочное/notify/note/created_by, UNIQUE(principal_sam, deputy_sam)) и 14-й блок прав
  `settings_substitutes` с выдачей `sed_admin`/`admin`, 0024 индексы нечеткого поиска по
  справочнику сотрудников под подбор заместителя (`employees_fio_trgm`/
  `employees_ad_sam_trgm` — GIN по `pg_trgm` под `ILIKE '%…%'` по `fio` и `ad_sam`;
  ручка открыта любому вошедшему, без индекса 23+ тысяч строк читались целиком на
  каждый запрос; `CREATE EXTENSION` не повторяется — его создаёт 0001), 0025 настройка
  ограничения числа заместителей (`settings.substitute_deputies_limit`, сид `3`).
- **Сид ключа настроек делается идемпотентно** (`ON CONFLICT (key) DO NOTHING`) по
  образцу 0022 `timezone`: значение, заведённое админом руками, миграция не
  перетирает. 0025 вниз удаляет ключ — тогда проверка лимита работает по запасному
  дефолту 3 в `substitutes._deputies_limit` (тот же режим, что до миграции). Схему
  0025 не меняет и существующие записи `substitutes` не трогает: у кого заместителей
  больше нового лимита, записи остаются, лимит снижает админ настройкой или разбирает
  записи сам.
- Откат миграции 0016 ничего не восстанавливает (значения и нерабочие), 0014 при downgrade не
  возвращает прежнее содержимое бланков — состав собирает человек заново; откат 0017 возвращает
  `stage_id` NOT NULL и потому удаляет все текущие шаги бланка (у них `NULL stage_id`) —
  содержимое бланков собирается заново.
- `audit_log` — append-only (только INSERT, без UPDATE/DELETE; запретить на уровне прав/триггера). Миграция 0021 сузила правило: DELETE разрешён для истории заявки, которой уже нет в `dismissal_requests` (удаление заявки чистит её след — тестовый стенд, решение человека 2026-10-12); UPDATE запрещён для всех. Номера заявок — из последовательности `request_code_seq`, не `MAX(code)`: по живым заявкам счётчик откатывался после очистки списка, и новая заявка наследовала журнал удалённой.
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
  `blank_autopick='off'`, `timezone='Europe/Moscow'`, `substitute_deputies_limit='3'`
  (лимит заместителей на сотрудника, 0025; правится админом, вкладка «Инфра», ключ в
  `INFRA_KEYS` — контент-настройками он не правится и в `/settings/content` не отдаётся;
  легаси `position_to_category`/`templates`/`doc_templates`/`position_sets`
  из `settings` удалены миграциями 0014/0016 — не возвращать).
- `timezone` — часовая зона СИСТЕМЫ (одна на всё, `api/app/timezone.py`): её отдаёт
  `/auth/me` (обеим карточкам; хранимое остаётся в UTC), она уходит в соединения
  Postgres (`set_config` на выдаче из пула — на `connect` SET откатывался бы вместе
  с транзакцией), в письма/бланки, дату печати, «сегодня» в SQL и в Python,
  расписания и имена бэкапов; правит админ. Часовой пояс контейнеров — `TZ` в env:
  менять `settings.timezone` без смены `TZ` в `.env` нельзя — OS-уровень (метки
  времени в логах, `date` в контейнере) останется в старой зоне.
- Дефолтов в коде нет: расписания регламентов (`schedule_*_sync`) и метки (`*_synced_at`) —
  ключи settings, читаются `read_setting_value`, пишутся синхронизациями/админом.
- Бэкап: `pg_dump` в `/srv/sed/backups` (путь — в настройках окружения, не в коде).
- Массивы в SQLAlchemy на psycopg3: `= ANY(CAST(:keys AS text[]))` — expanding-bindparam
  даёт «malformed array literal»; JSON-поля — строкой с приведением `::jsonb` (dict psycopg3
  не адаптирует).

## Приемка правки

Миграция накатывается и откатывается на чистой БД, сиды идемпотентны, лишний вес индексов обоснован.

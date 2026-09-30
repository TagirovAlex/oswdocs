-- Полнота журнала аудита на стенде (W5c Волны 5, qa-sed).
-- Таблица audit_log(id, at, actor, action, entity, entity_id, details) — 0001_initial_schema.py.
-- append-only гарантирует триггер audit_log_no_update_delete (проверка в п.6).
--
-- Запуск на ВМ (DATABASE_URL из env контейнера api/worker, README п.3):
--   psql "$DATABASE_URL" -f audit-fullness.sql
-- Или из контейнера api: docker compose exec api psql "$DATABASE_URL" -f /app/deploy/qa/audit-fullness.sql
-- (файл предварительно скопировать в образ/каталог по месту развертывания).
--
-- Прогонять ПОСЛЕ прогона ролевых сценариев (roles-matrix.spec.ts): журнал
-- должен содержать события входа, чтений и действий каждой роли.

\pset pager off
\timing on

-- 1. Общие счётчики: всего записей, за текущие сутки (серверное время), за 24 часа.
SELECT count(*)                                                       AS всего,
       count(*) FILTER (WHERE at >= date_trunc('day', now()))         AS за_сегодня,
       count(*) FILTER (WHERE at >= now() - interval '24 hours')      AS за_24ч,
       min(at)                                                        AS первое_событие,
       max(at)                                                        AS последнее_событие
  FROM audit_log;

-- 2. События за день по action: что реально происходило и сколько раз.
SELECT action, count(*) AS количество
  FROM audit_log
 WHERE at >= date_trunc('day', now())
 GROUP BY action
 ORDER BY количество DESC, action;

-- 3. Топ-акторы за день (sam из сессии; пусто — событие без учетки, см. п.4).
SELECT actor, count(*) AS количество
  FROM audit_log
 WHERE at >= date_trunc('day', now())
 GROUP BY actor
 ORDER BY количество DESC
 LIMIT 20;

-- 4. События без актора: быть НЕ должно (каждое значимое действие — от сессии).
-- Если есть — ищем, откуда пишут без actor (системный фоновый поток и т.п.).
SELECT action, entity, count(*) AS количество
  FROM audit_log
 WHERE actor IS NULL OR actor = ''
 GROUP BY action, entity
 ORDER BY количество DESC;

-- 5. Разбивка по сущностям за день (request/user/settings/enterprise/...).
SELECT entity, count(*) AS количество
  FROM audit_log
 WHERE at >= date_trunc('day', now())
 GROUP BY entity
 ORDER BY количество DESC;

-- 6. Проверка append-only: UPDATE и DELETE обязаны упасть (триггер
-- audit_log_no_update_delete). Попытки на несуществующей строке id=-1:
-- BEFORE-триггер срабатывает и при 0 затронутых строках.
DO $$
DECLARE
  update_blocked boolean := false;
  delete_blocked boolean := false;
BEGIN
  BEGIN
    UPDATE audit_log SET details = details WHERE id = -1;
  EXCEPTION WHEN others THEN
    update_blocked := true;  -- триггер сработал, как и задумано
  END;
  BEGIN
    DELETE FROM audit_log WHERE id = -1;
  EXCEPTION WHEN others THEN
    delete_blocked := true;
  END;
  IF update_blocked AND delete_blocked THEN
    RAISE NOTICE 'ОК: audit_log append-only (UPDATE и DELETE запрещены триггером)';
  ELSE
    RAISE EXCEPTION 'ПРОВАЛ: audit_log изменяем — триггер audit_log_no_update_delete не работает';
  END IF;
END $$;

-- 7. Полнота ключевых действий за день: после прогона ролевых сценариев
-- каждая строка ожидаемых action обязана быть с ненулевым количеством.
-- Пустое количество по строке — действие не аудируется (дыра полноты).
SELECT a.action, count(t.action) AS количество
  FROM (VALUES
         ('me.read'), ('settings.read'), ('settings.update'), ('enterprises.read'),
         ('request.create'), ('request.submit'), ('request.decision'), ('request.finish'),
         ('documents.print')
       ) AS a(action)
  LEFT JOIN audit_log t
         ON t.action = a.action
        AND t.at >= date_trunc('day', now())
 GROUP BY a.action
 ORDER BY a.action;
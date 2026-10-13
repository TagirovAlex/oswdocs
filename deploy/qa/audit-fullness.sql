-- Полнота журнала аудита на стенде (W5c Волны 5, qa-sed).
-- Таблица audit_log(id, at, actor, action, entity, entity_id, details) — 0001_initial_schema.py.
-- append-only гарантирует триггер audit_log_no_update_delete (проверка в п.6);
-- миграция 0021 сузила правило: удалять можно историю заявки, которой уже нет
-- в dismissal_requests (удаление заявки чистит её след на тестовом стенде).
--
-- ВНИМАНИЕ: счётчики в п.1–п.5 считают и историю удалённых заявок, пока её не
-- удалили. Полностью зачистить накопленное на стенде — вручную (триггер 0021
-- разрешает DELETE только по бизнес-номерам REQ-XXXX, которых нет среди живых
-- заявок; записи с пустым/NULL entity_id не трогаются).
-- Именно NOT EXISTS, а не NOT IN: dismissal_requests.code nullable (0002), и
-- одна строка с code IS NULL превратила бы NOT IN в UNKNOWN — тихо, без ошибки:
--   DELETE FROM audit_log a
--    WHERE a.entity IN ('request','document','attachment')
--      AND a.entity_id ~ '^REQ-[0-9]+$'
--      AND NOT EXISTS (SELECT 1 FROM dismissal_requests d WHERE d.code = a.entity_id);
--   DELETE FROM request_comments c
--    WHERE NOT EXISTS (SELECT 1 FROM dismissal_requests d WHERE d.code = c.request_id);
-- Удаление заявки пишет событие request.delete ПОСЛЕ зачистки следа, поэтому
-- эти маркеры удаления рецепт тоже снесёт — на стенде это не важно.
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

-- 6. Проверка append-only (триггер audit_log_no_update_delete, миграция 0021).
--    UPDATE запрещён для любой записи. DELETE запрещён, кроме одного случая:
--    след заявки по её бизнес-номеру, если такой заявки в dismissal_requests
--    уже нет (удаление заявки чистит её историю — тестовый стенд, решение
--    человека 2026-10-12). Значит проверять надо на ЖИВОЙ заявке.
--
--    UPDATE берёт реальную строку (min(id)): BEFORE-триггер FOR EACH ROW на
--    UPDATE без совпавших строк не срабатывает и проверка была бы пустой.
--    DELETE берём по номеру живой заявки, у которой след в журнале ЕСТЬ: иначе
--    DELETE не попадёт ни в одну строку, триггер не сработает и проверка дала бы
--    ложный «ПРОВАЛ». Если такого номера нет — это не поломка триггера, а
--    недостаток данных: сообщаем NOTICE и пропускаем часть проверки.
DO $$
DECLARE
  update_blocked boolean := false;
  delete_checked boolean := false;
  live_code text;
  has_rows boolean;
BEGIN
  SELECT EXISTS (SELECT 1 FROM audit_log) INTO has_rows;
  IF NOT has_rows THEN
    RAISE NOTICE 'audit_log пуст — проверять нечего (сначала прогоните ролевые сценарии)';
    RETURN;
  END IF;
  BEGIN
    UPDATE audit_log SET details = details
      WHERE id = (SELECT min(id) FROM audit_log);
  EXCEPTION WHEN others THEN
    update_blocked := true;  -- триггер сработал, как и задумано
  END;
  SELECT d.code INTO live_code
    FROM dismissal_requests d
   WHERE d.code IS NOT NULL
     AND EXISTS (
       SELECT 1 FROM audit_log a
        WHERE a.entity IN ('request', 'document', 'attachment')
          AND a.entity_id = d.code
     )
   ORDER BY d.code
   LIMIT 1;
  IF live_code IS NULL THEN
    RAISE NOTICE 'Нет живой заявки со следом в журнале — проверку DELETE пропускаем';
  ELSE
    BEGIN
      DELETE FROM audit_log
        WHERE entity IN ('request', 'document', 'attachment')
          AND entity_id = live_code;
      RAISE EXCEPTION
        'ПРОВАЛ: след живой заявки % удалился — триггер audit_log_no_update_delete не работает',
        live_code;
    EXCEPTION WHEN others THEN
      IF SQLERRM LIKE 'ПРОВАЛ:%' THEN
        RAISE;  -- наша собственная ошибка, не обработка триггера
      END IF;
      delete_checked := true;  -- триггер запретил удаление — как и должно быть
    END;
  END IF;
  IF update_blocked AND delete_checked THEN
    RAISE NOTICE 'ОК: audit_log append-only (UPDATE любой записи и DELETE истории живой заявки запрещены)';
  ELSIF update_blocked THEN
    RAISE NOTICE 'Частично проверено: UPDATE запрещён, DELETE истории живой заявки не проверялся (нет данных)';
  ELSE
    RAISE EXCEPTION 'ПРОВАЛ: audit_log обновляется — триггер audit_log_no_update_delete не работает';
  END IF;
END $$;

-- 6б. Обратная сторона исключения: след заявки, которой уже нет, УДАЛЯТЬСЯ
--     должен (иначе вернётся «история всех заявок»). Проверка самодостаточная:
--     сами создаём пробную запись с номером, которого заведомо нет среди живых,
--     удаляем её и сверяем число удалённых строк. Роллбак не нужен: INSERT и
--     DELETE в одном DO дают в итоге ноль изменений, а при ошибке psql откатит
--     весь DO сам. Так проверка не зависит от того, чистили ли стенд заранее.
--     ROLLBACK внутри блока с EXCEPTION использовать нельзя: транзакцию нельзя
--     завершить внутри такого блока (PG).
DO $$
DECLARE
  probe_code CONSTANT text := 'REQ-999999999';
  deleted integer;
BEGIN
  INSERT INTO audit_log (actor, action, entity, entity_id, details)
  VALUES ('qa.probe', 'qa.purge_probe', 'request', probe_code, '{}'::jsonb);
  DELETE FROM audit_log
    WHERE entity IN ('request', 'document', 'attachment')
      AND entity_id = probe_code;
  GET DIAGNOSTICS deleted = ROW_COUNT;
  IF deleted <> 1 THEN
    RAISE EXCEPTION
      'ПРОВАЛ: след удалённой заявки удалить нельзя (удалено % строк вместо 1) — триггер работает не по правилу 0021',
      deleted;
  END IF;
  RAISE NOTICE 'ОК: след заявки, которой нет в dismissal_requests, удаляется (правило 0021)';
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
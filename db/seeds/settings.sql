-- Сиды прикладных настроек СЭД (значения по умолчанию, все правки — через роль SED_ADMINS).
-- Скрипт идемпотентный: повторный прогон обновляет значения, дублей не создает.
-- Все имена/группы/предприятия вымышленные; реальные значения вносит ИТ на стенде.
-- Группы, OU, предприятия и TTL живут только здесь, в SQL-логике миграций их нет.

-- Корневой OU учеток СЭД в AD (подтвержден ИТ; DC-компоненты — заглушка, уточнит ИТ).
INSERT INTO settings (key, value) VALUES
  ('sed_ou', '"OU=OSWDOCS,DC=example,DC=com"')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Доменные группы доступа (подтверждены ИТ, заведены в OSWDOCS; проверка memberOf при логине).
INSERT INTO settings (key, value) VALUES
  ('allowed_ad_groups', '["SED_HR", "SED_ADMINS", "SED_STEP_EXEC"]')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Предприятия (вымышленные): код используется в составном ключе сотрудника.
INSERT INTO settings (key, value) VALUES
  ('enterprises',
   '[{"code": "ENT_PRIMER_1", "name": "Предприятие «Пример-1» (вымышленное)"},
      {"code": "ENT_PRIMER_2", "name": "Предприятие «Пример-2» (вымышленное)"}]')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Соответствие должности категории (МОЛ/линейный/руководитель): заглушка, заполняется вручную после запуска.
INSERT INTO settings (key, value) VALUES
  ('position_to_category', '{}')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Включаемая эскалация по должности увольняемого: заглушка, заполняется вручную после запуска.
INSERT INTO settings (key, value) VALUES
  ('position_escalation', '{}')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Срок отметки шага в днях (общего дедлайна заявки нет).
INSERT INTO settings (key, value) VALUES
  ('approval_ttl_days', '3')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Требуется ли бумажная подпись на заявлении.
INSERT INTO settings (key, value) VALUES
  ('require_paper_signature', 'true')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Требовать ли комментарий на шаге всегда (отказ/возврат требуют комментария в любом случае).
INSERT INTO settings (key, value) VALUES
  ('require_comment', 'false')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Хост SMTP-релея (пусто — берётся из env SMTP_HOST; реальное значение вносит ИТ/админ).
INSERT INTO settings (key, value) VALUES
  ('smtp_host', '""')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Порт SMTP-релея (дефолт STARTTLS-релея).
INSERT INTO settings (key, value) VALUES
  ('smtp_port', '587')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Отправитель уведомлений (SMTP FROM). Редактируется через настройки (не env);
-- реальное значение вносит ИТ/админ на стенде.
INSERT INTO settings (key, value) VALUES
  ('smtp_from', '"sed@example.com"')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Срок хранения сканов в днях (глобальный).
INSERT INTO settings (key, value) VALUES
  ('scan_retention_days', '365')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- Максимальный размер скана в мегабайтах (глобальный).
INSERT INTO settings (key, value) VALUES
  ('scan_max_mb', '10')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- MIME-типы, разрешенные для сканов (пустой/нет ключа — загрузка запрещена, 409).
INSERT INTO settings (key, value) VALUES
  ('scan_allowed_types', '["application/pdf", "image/jpeg", "image/png"]')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

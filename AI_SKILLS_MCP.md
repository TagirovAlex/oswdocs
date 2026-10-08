# Скилы и MCP для разработки СЭД (актуально: скилы и opencode.json созданы)

Источник правды по продукту — `README.md`, правила для агентов — `AGENTS.md`.
Все 9 скилов созданы в `.opencode/skills/<имя>/SKILL.md`, MCP заведены в `opencode.json`. Перед стартом фазы сверять файлы скилов с таблицей ниже; новый скил — создавать файлом и строкой таблицы вместе со стартом его фазы.

## 1. Скилы (9, все созданы)

| # | Имя (`name`) | Триггер (description для SKILL.md) | Фаза | Что покрывает |
|---|---|---|---|---|
| 1 | `fastapi-sed` | Use when editing FastAPI, SQLAlchemy, LDAP bind, 1C OData in SED | 2–4 | REST `/auth /employees /requests /steps /documents`, LDAPS bind + группы из настроек, снапшот 1С+AD (истина — 1С), заглушка `AdLifecycle.disable` за `AD_WRITE_ENABLED=false` |
| 2 | `pg-sed` | Use when writing Postgres migrations, RLS, pg_trgm, backups | 1, 6 | Схема README п.4, Alembic вверх/вниз, индексы ФИО/`sam`, `audit_log` append-only, сиды `settings`, `pg_dump` |
| 3 | `ad-reader` | Use when resolving manager, memberOf, LDAPS in SED | 2 | RO-доступ, `manager/memberOf/mail`, кэш 4–8 ч, стыковка по ФИО + ручная проверка, истина — 1С |
| 4 | `onec-multibase` | Use when adding 1C base, mapping enterprise/tabNum to base | 3 | `OneCClient(base_code)` только GET, связка `предприятие→база→сотрудник`, ключ `enterprise+base+tab`, таймаут 5с, одна база down — остальные живы |
| 5 | `approval-templates` | Use when editing dismissal templates, resolvers, deadlines | 4 | бланки `blanks`/`blank_steps` + этапы `approval_stages`/`stage_assignees`, режим шага `parallel`/`sequential`, `position_to_category` (ручное), `position_escalation`, `require_paper_signature`, ручной конструктор только для разрешенной группы |
| 6 | `debian-ops` | Use when editing compose, Nginx certs, systemd on Debian 13 | 0, 7 | `proxy/api/worker/redis/db`, `/srv/sed/*`, корп. cert read-only, `sed.service`, override для выноса `db` |
| 7 | `mail-docs` | Use when editing DOCX/PDF bypass sheets and Exchange mail templates | 4–5 | бланки печати из данных (`python-docx` + LibreOffice PDF, макет `office`/`line` из снимка заявки, QR, без версий — `pdf_b64`), все ответственные шага в колонке и в письмах, `mail_templates` Jinja HTML, очередь SMTP |
| 8 | `qa-sed` | Use when running negative tests, security and rate-limit checks, audit_log verification in SED | 6 | Негативные тесты (403, дубли ФИО, просрочка TTL, лимиты скана, rate-limit), проверка «нет записи в 1С/AD, нет delete», полнота `audit_log`, оптимизация под НФТ (README п.5), чек-лист приемки Фазы 6 |
| 9 | `react-sed` | Use when editing the React SPA, role-based UI, login and OK/owner flows in SED | 5 | Логин через `/auth`, создание ОК (предприятие→сотрудник→маршрут→печать), отметки владельца (только свои), админка настроек (`SED_ADMINS`), матрицы ролей README п.1, прогон UI через MCP `playwright` |

## 2. MCP-серверы (уже заведены в `opencode.json`)

| MCP | Тип | Назначение |
|---|---|---|
| `postgres` | local (`@modelcontextprotocol/server-postgres`, строка — `{env:PG_URL}`) | Миграции, сиды, диагностика пилота. Дежурному — RO-пользователь. `PG_URL` = `DATABASE_URL` приложения (README п.3) |
| `playwright` | local (`@playwright/mcp`) | Прогон UI согласования в intranet |

Дополнительно без отдельных MCP: `fetch/http` — проверка GET 1С и SMTP (через bash/python по согласованию), `docker compose logs/ps` — через bash по согласованию (см. AGENTS.md п.2).

## 3. Что дальше

1. Файлы скилов созданы (папка + `SKILL.md`) — сверять с таблицей перед стартом фазы; новые скилы — файл + строка таблицы.
2. `PG_URL` (= `DATABASE_URL` приложения, README п.3) задать в окружении перед использованием MCP `postgres`.
3. После любых правок `opencode.json`/скилов — перезапустить opencode (конфиг не hot-reload).

# Структура проекта СЭД (для RAG, поддерживать актуальной)

Обновлено: 2026-10-02. Обновлять при добавлении/переносе модулей.

## Обзор
- `api/` — FastAPI (Python 3.12), `app/` — код, `tests/` — pytest, `requirements.txt`.
- `frontend/` — React SPA (Vite): `src/` — код, `public/` — статика, `dist/` — сборка.
- `db/alembic/versions/` — миграции БД (0001..0005).
- `proxy/` — nginx. `deploy/` — компоуз/деплой. `samples/` — DESIGN.md + макет.
- `script_local/`, `script_remote/` — инструментарий разработчика (gitignored).
- `PLAN.md` — план/статус; `TEMPLATES.md` — переменные шаблонов; `task/` — исторические спеки (gitignored).

## API (api/app/)
- `main.py` — точка входа, подключение роутеров.
- `auth.py` — доменный вход/сессия (LDAPS), `/auth/login`, `/auth/me`.
- `deps.py` — зависимости, роли (owner/hr/hr_admin/admin/sed_admin), `_detect_role`.
- `config.py` — настройки/env (Settings), `get_settings`.
- `requests.py` — заявки: создание, шаги, отметки (approve/reject/return), PATCH, rollback, история, комментарии.
- `requests_store.py` — хранилище заявок (InMemory/Db), в т.ч. employees/документы-хелперы.
- `employee_sync.py` — синк локальной таблицы сотрудников (1С+AD, пагинация).
- `employees.py` — поиск сотрудников (локальная таблица + фолбэк 1С), `/employees` (пагинация
  page/page_size/total), `/employees/card`.
- `oneс_client.py` / `onec_cache.py` / `onec_sync.py` — 1С HTTP-клиент, Redis-кэш карточек, синк предприятий.
  Поиск с пагинацией: `search_page` ($skip/$top + total через $inlinecount), `SEARCH_DEFAULT_TOP`.
- `resolver.py` — веерный опрос баз предприятия: `resolve_employee`, `search_enterprise`,
  `search_enterprise_page` (страница + total по базам).
- `employee_sync.py` — локальная таблица сотрудников (миграция 0004), синк постранично.
- `ad_reader.py` / `ad_sync.py` / `link.py` / `link_store.py` — AD (LDAP), связка 1С↔AD.
- `settings_routes.py` — настройки (GET/PUT /settings, /settings/content), doc_types CRUD, step-groups;
  файлы шаблонов бегунков (задача H): `POST/GET/DELETE /settings/doc-templates/files/{upload|download|delete|preview}`,
  хранение `FILES_DIR/templates/`, `DocTemplateItem.file` (имя .docx, необязательно).
- `documents.py` — печать бегунка (вариант 1, pdf_b64), документы; `_bypass_body` возвращает
  `(body, template_file)` — печать использует .docx-файл при наличии.
- `docs.py` — генерация DOCX/PDF/QR (docxtpl, LibreOffice), шаблоны бланков; рендер из .docx-файла
  (`_render_docx_from_file`), `find_doc_template` по `body` ИЛИ `file`, контекст + `steps` + `qr`.
- `archive.py` — бэкапы (ручной, расписание, настройки, список, скачать/удалить).
- `audit.py` — журнал аудита (память + INSERT в БД).
- `mailer.py` — письма (шаблоны, SMTP/файловая очередь).
- `attachments.py` — вложения. `worker.py` — регламенты (просрочка, напоминания, синки, бэкапы).

## Frontend (frontend/src/)
- `main.tsx` — точка входа, роутинг `?view=`.
- `layout.tsx` — скелет: шапка (вкладки+тема+логотип), дерево папок, фильтры, таблица заявок.
- `auth-client.tsx` — токен/вход; `requests-client.tsx` — клиент заявок/сотрудников/документов/истории.
- `settings-client.tsx` — клиент настроек, doc-types, архив.
- `create-form.tsx` — форма создания заявки (панели, маршрут-конструктор, исполнители).
- `request-card.tsx` — карточка заявки (шаги, отметки, история, комментарии, вложения, печать, админ СЭД).
- `admin-settings.tsx` — настройки (вкладки: процесс/инфра/регламенты/доступ/виды документов/архивация).
- `directory.tsx` — справочник сотрудников (поиск, пагинация).
- `theme.tsx` / `theme.css` — тема (light/dark, токены `--sed-*`) и ВСЕ стили.
- `windows.tsx` — окна-попы (?view=), `api-mock.tsx` — тип Role (живой), логин/окна.

## Стили и шаблоны (где что лежит)
- ВСЕ стили — один файл `frontend/src/theme.css` (токены, сетки, кнопки, таблицы, dropdown, панели).
- Иконки меню/темы — инлайн-SVG в `frontend/src/layout.tsx` (каталог `ico/` удалён, в сборку не входит); логотипы — `logo-oswdocs*.svg` + `frontend/public/`.
- Шаблоны бланков — settings `doc_templates` (текст, рендер docxtpl); писем — `mail_templates` (код/тема/тело); маршрутов — `templates` (служба/категория/шаги). Переменные — см. `TEMPLATES.md`.
- `.docx`-файлов в репо нет (бланк = текст шаблона); вопрос «docx как шапка/строки/подвал + импорт/редактор» — PLAN.md блок H (обсуждается).

## БД (db/alembic/versions/)
- 0001 initial (audit_log, requests, documents, settings, links), 0002 persist, 0003 doc_types/request_comments/поля карточки, 0004 employees, 0005 doc_types автонумерация.

## Сценарии ключевых функций
- Создание: create-form → POST /requests → шаги; отметки: request-card → POST .../steps/{order}/decide; печать: POST /print (pdf_b64) → iframe; история: GET /history (audit_log); сотрудники: GET /employees (локальная таблица); бэкапы: /api/archive*; настройки: /api/settings.
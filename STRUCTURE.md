# Структура проекта СЭД (для RAG, поддерживать актуальной)

Обновлено: 2026-10-06. Обновлять при добавлении/переносе модулей.

## Обзор
- `api/` — FastAPI (Python 3.12), `app/` — код, `tests/` — pytest, `requirements.txt`.
- `frontend/` — React SPA (Vite): `src/` — код, `public/` — статика, `dist/` — сборка.
- `db/alembic/versions/` — миграции БД (0001..0011).
- `proxy/` — nginx. `deploy/` — компоуз/деплой. `samples/` — DESIGN.md + макет.
- `script_local/`, `script_remote/` — инструментарий разработчика (gitignored).
- `PLAN.md` — план/статус; `TEMPLATES.md` — переменные шаблонов; `task/` — исторические спеки (gitignored).

## API (api/app/)
- `main.py` — точка входа, подключение роутеров.
- `auth.py` — доменный вход/сессия (LDAPS), `/auth/login`, `/auth/me`.
- `deps.py` — зависимости, роли (owner/hr/hr_admin/admin/sed_admin), `_detect_role`.
- `config.py` — настройки/env (Settings), `get_settings`.
- `requests.py` — заявки: создание, шаги, отметки (approve/reject/return), PATCH, rollback, история,
  комментарии; маршрут по профилю службы (`route_mode=auto|custom`), предпросмотр
  `POST /requests/route/preview` (профиль/этапы/исполнители/`notice`/`link_state`/`link_candidate`),
  подтверждение связи при импорте `POST /requests/route/link-employee` (`_link_state`,
  `_ad_candidates`, `_missing_ad_card_detail`).
- `requests_store.py` — хранилище заявок (InMemory/Db), в т.ч. employees/документы-хелперы.
- `employee_sync.py` — локальный справочник `employees` (миграции 0004/0009): постраничный
  синк 1С+AD, `ref_key`/`dismissal_date`/`hr_synced_at`, проход по регистру кадровых данных
  (`sync_hr_dismissals` — даты увольнения, `list_hr_dismissals` постранично), `mark_linked`
  (подтверждение связи сразу помечает строку), `dismissed_keys` (пропуск уволенных),
  `maybe_sync_employees_weekly` / `maybe_sync_hr_daily` (регламенты).
- `employees.py` — поиск сотрудников (локальная таблица; фолбэк на живой 1С только до первого
  синка), `GET /employees` (пагинация page/page_size/total, уволенные отсекаются по
  `dismissal_date`), `/employees/card`, `POST /employees/sync`, `POST /employees/hr-sync`
  (проход по регистру кадровых данных по требованию, admin/sed_admin).
- `oneс_client.py` / `onec_cache.py` / `onec_sync.py` — 1С HTTP-клиент, Redis-кэш карточек, синк предприятий.
  Поиск с пагинацией: `search_page` ($skip/$top + total через $inlinecount), `SEARCH_DEFAULT_TOP`.
- `resolver.py` — веерный опрос баз предприятия: `resolve_employee`, `search_enterprise`,
  `search_enterprise_page` (страница + total по базам).
- `ad_reader.py` — чтение AD (LDAPS): карточка, поиск по ФИО, группы, титулы (см. скил `ad-reader`).
- `ad_sync.py` — автосопоставление 1С↔AD: уникальное ФИО → verified-связка; дубли ФИО в 1С
  разводятся по должности/службе из регистра кадровых данных (`_enrich_duplicate_cards`,
  `_pick_duplicate_card`); уволенные пропускаются (`dismissed`); всё несвязанное пишется в
  `link_discrepancies` (`one_c_duplicate` / `ad_duplicate` / `not_in_ad`, `REASON_*`).
- `link.py` — связки 1С↔AD вручную (`POST /link_1c_ad`) и автосопоставление
  (`POST /link_1c_ad/sync`), выдача расхождений `GET /link_1c_ad/discrepancies` и массовое
  подтверждение `POST /link_1c_ad/discrepancies/confirm` (только где кандидат AD один).
- `link_store.py` — хранилище связок (InMemory/Db), зеркала `users`/`employee_base_map`/
  `one_c_bases` и таблица расхождений (`replace_discrepancies`, `list_discrepancies`,
  `count_discrepancies`, `counts_by_reason`, `get_discrepancies`, `resolve_discrepancies`).
  Массивы в SQL — через `CAST(:keys AS text[])` (expanding-bindparam psycopg3 не переваривает),
  JSON-поля — строкой с приведением `::jsonb`.
- `routing.py` / `routing_store.py` — справочники маршрута (миграция 0008): `ad_services`
  (службы AD + `blank_kind`), `route_profiles`, `approval_stages` (`owner_kind`:
  `ad_group`/`stage_roster`/`manager_ad`), `stage_assignees`, `route_profile_steps`; чистая
  логика `pick_profile`/`apply_dismissals_and_additions` + хранилище и карточки пользователей
  (`user_card`, `manager_sam_by_dn`).
- `settings_routes.py` — настройки (GET/PUT /settings, /settings/content), doc_types CRUD, step-groups,
  именованные наборы должностей (`position_sets`) и привязка бланков (`DocTemplateItem.position_set`);
  файлы шаблонов бегунков (задача H): `POST/GET/DELETE /settings/doc-templates/files/{upload|download|delete|preview}`,
  хранение `FILES_DIR/templates/`, `DocTemplateItem.file` (имя .docx, необязательно).
- `documents.py` — печать бегунка (вариант 1, pdf_b64), документы; `_bypass_body` возвращает
  `((body, template_file), blank_kind)`, вид бланка — из `blank_kind` службы заявки
  (приоритетнее категории) с фолбэком на `office`; `_fill_step_fio` подставляет ФИО
  персональных исполнителей этапов из зеркала `users` (групповые этапы остаются пустыми —
  их дописывают от руки).
- `docs.py` — генерация DOCX/PDF/QR (docxtpl, LibreOffice), шаблоны бланков; рендер из .docx-файла
  (`_render_docx_from_file`), `find_doc_template` по `body` ИЛИ `file` (служба+категория+
  набор должностей), `build_bypass_context` — контекст с `steps` (order/owner/status/position/
  assignee/fio/stage_lines/done_at) и `qr`; переменные — см. `TEMPLATES.md`.
- `archive.py` — бэкапы (ручной, расписание, настройки, список, скачать/удалить).
- `audit.py` — журнал аудита (память + INSERT в БД).
- `mailer.py` — письма (шаблоны, SMTP/файловая очередь).
- `attachments.py` — вложения. `ad_groups_cache.py` — кэш состава групп AD
  (таблицы ad_group_members/ad_group_sync_state из 0006) и справочник
  должностей (ad_position_directory из 0007, пересборка после каждого синка;
  регламент worker + ручной POST /api/ad/groups/sync; GET состава — из кэша,
  GET /api/ad/titles — справочник + distinct из employees).
  Приоритет должностей AD→1С — только здесь (исключение; везде иначе истина — 1С).
  `worker.py` — регламенты (просрочка, напоминания, синки, бэкапы).

## Frontend (frontend/src/)
- `main.tsx` — точка входа, роутинг `?view=`.
- `layout.tsx` — скелет: шапка (вкладки+тема+логотип), дерево папок, фильтры, таблица заявок.
- `auth-client.tsx` — токен/вход; `requests-client.tsx` — клиент заявок/сотрудников/документов/истории.
- `settings-client.tsx` — клиент настроек, doc-types, архив.
- `create-form.tsx` — форма создания заявки (панели, маршрут-конструктор, исполнители,
  живой поиск руководителя в AD, кнопка «Подтвердить связь с AD» при `link_state=need_link`,
  переход в «Вручную» при блокировке маршрута).
- `request-card.tsx` — карточка заявки (шаги, отметки, история, комментарии, вложения, печать, админ СЭД).
- `admin-settings.tsx` — настройки (вкладки: процесс/инфра/регламенты/доступ/виды документов/архивация).
- `directory.tsx` — справочник сотрудников (поиск, пагинация, автосопоставление).
- `link-match.tsx` — раздел «Сопоставление 1С↔AD» (admin): ручной запуск прохода, счётчики
  расхождений, фильтры, пагинация (первая/последняя + по пять с каждой стороны), массовое
  подтверждение связок (выбранные / все на странице / рекомендованные); пагинация вынесена в
  экспортируемую `pagerPages`.
- `theme.tsx` / `theme.css` — тема (light/dark, токены `--sed-*`) и ВСЕ стили.
- `windows.tsx` — окна-попы (?view=), `api-mock.tsx` — тип Role (живой), логин/окна.

## Стили и шаблоны (где что лежит)
- ВСЕ стили — один файл `frontend/src/theme.css` (токены, сетки, кнопки, таблицы, dropdown, панели).
- Иконки меню/темы — инлайн-SVG в `frontend/src/layout.tsx` (каталог `ico/` удалён, в сборку не входит); логотипы — `logo-oswdocs*.svg` + `frontend/public/`.
- Исходники бланков лежат в репо: `office.docx` (офисный) и `line.docx` (линейный) —
  основа настоящей вёрстки (шапка/строки/подвал, QR слева вверху, данные справа).
  В `FILES_DIR/templates/` — производные `office_base.docx` / `line_base.docx` (тот же макет
  с динамической таблицей шагов `{%tr%}` и строками этапов `{%p%}`), на них ссылается
  `doc_templates.file`. Писем — `mail_templates` (код/тема/тело). Переменные — `TEMPLATES.md`.
- Маршруты строятся по справочникам `route_profiles`/`approval_stages`/`route_profile_steps`
  (миграция 0008), а не по `settings.templates`: старые `templates/template_steps/
  template_filters` из 0001 оставлены пустыми таблицами и не используются; `position_sets`
  и `position_to_category` больше не участвуют в подборе маршрута.

## БД (db/alembic/versions/)
- 0001 initial (audit_log, requests, documents, settings, links), 0002 persist, 0003 doc_types/request_comments/поля карточки,
  0004 employees, 0005 doc_types автонумерация, 0006 ad_group_members/ad_group_sync_state, 0007 ad_position_directory,
  0008 справочники маршрута (ad_services/route_profiles/approval_stages/stage_assignees/route_profile_steps;
  снимок в заявке: profile_id/service_id/service_name, этап/строки/код профиля в request_steps) и снос старых templates*,
  0009 кадровые данные сотрудника (employees.ref_key/dismissal_date/hr_synced_at + индексы),
  0010 link_discrepancies (ключ карточки + reason + кандидат AD + recommended + resolved_at),
  0011 link_discrepancies.detail (JSONB: прочие кандидаты AD и соседние карточки 1С).

## Сценарии ключевых функций
- Создание: create-form → POST /requests → шаги; отметки: request-card → POST .../steps/{order}/decide; печать: POST /print (pdf_b64) → iframe; история: GET /history (audit_log); сотрудники: GET /employees (локальная таблица); бэкапы: /api/archive*; настройки: /api/settings.
- Маршрут по профилю: create-form → POST /requests/route/preview (link_state=need_link? → «Подтвердить связь с AD» → POST /requests/route/link-employee → предпросмотр заново) → POST /requests (auto) — подбор профиля по службе AD, этап руководителя из `manager` AD.
- Сопоставление 1С↔AD: link-match → POST /link_1c_ad/sync (проход) → GET /link_1c_ad/discrepancies → POST /link_1c_ad/discrepancies/confirm (только строки с одним кандидатом AD; строка справочника помечается связанной сразу). Регламент: worker по `schedule_ad_links_sync`/`schedule_hr_dismissals_sync`.
- Кадровые данные: ежедневный проход регистра (worker) → `POST /employees/hr-sync` для запуска по требованию → `employees.dismissal_date` → поиск сотрудников скрывает уволенных, сопоставление их пропускает.
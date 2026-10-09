# Структура проекта СЭД (для RAG, поддерживать актуальной)

Обновлено: 2026-10-06. Обновлять при добавлении/переносе модулей.

## Обзор
- `api/` — FastAPI (Python 3.12), `app/` — код, `tests/` — pytest, `requirements.txt`.
- `frontend/` — React SPA (Vite): `src/` — код, `public/` — статика, `dist/` — сборка.
- `db/alembic/versions/` — миграции БД (0001..0017).
- `proxy/` — nginx. `deploy/` — компоуз/деплой. `samples/` — DESIGN.md + макет.
- `script_local/`, `script_remote/` — инструментарий разработчика (gitignored).
- `PLAN.md` — план/статус; `TEMPLATES.md` — бланки/этапы и печать; `task/` — исторические спеки (gitignored).

## API (api/app/)
- `main.py` — точка входа, подключение роутеров.
- `auth.py` — доменный вход/сессия (LDAPS), `/auth/login`, `/auth/me`.
- `deps.py` — зависимости, роли (owner/hr/hr_admin/admin/sed_admin), `_detect_role`.
- `config.py` — настройки/env (Settings), `get_settings`.
- `requests.py` — заявки: создание, шаги, отметки (approve/reject/return), PATCH, rollback, история,
  комментарии; выбор бланка сотрудником ОК (`blank_id` в `POST /requests`, список для селекта
  `GET /requests/route/blanks`, автоподстановка по службе за `blank_autopick` — `_blank_autopick_enabled`),
  снимок бланка в заявке (`blank_id/blank_name/blank_version/blank_layout/blank_header_html/
  blank_footer_lines`), несколько ответственных шага (`assignees`/`approvals`/`approval_mode`),
  снятие шага бланка по номеру (`dismissed_step_orders`, рядом с `dismissed_stages` по кодам этапов),
  предпросмотр
  `POST /requests/route/preview` (бланк/профиль/этапы/исполнители/`notice`/`link_state`/`link_candidate`),
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
  (службы AD; `blank_kind` — прежний признак вида бланка, в печати не участвует), `route_profiles`,
  `approval_stages` (`owner_kind`:
  `ad_group`/`stage_roster`/`manager_ad`), `stage_assignees`, `route_profile_steps`; справочник
  бланков (миграции 0012/0014): `blanks` (+`header_html`/`footer_lines`), `blank_steps` —
  самостоятельные шаги (`title`/`stage_lines`/`executor_kind`/`assignees`/`owner_group`/`optional`/
  `require_comment`/`approval_mode`) (`list_blanks`/`blank_by_id`/
  `create_blank`/`update_blank`/`list_blank_steps`/`set_blank_steps` — замена состава одной
  транзакцией с ростом `version`); чистая логика `pick_profile`/`pick_blank_steps`/
  `apply_dismissals_and_additions` + хранилище и карточки пользователей (`user_card`,
  `manager_sam_by_dn`).
- `settings_routes.py` — настройки (GET/PUT /settings, /settings/content), doc_types CRUD, step-groups,
  бланки (CRUD справочника и состава шагов):
  `GET/POST /settings/routing/blanks`, `PUT /settings/routing/blanks/{id}`,
  `GET/PUT /settings/routing/blanks/{id}/steps` (все — только admin); ключ `blank_autopick`
  в `INFRA_KEYS`. Ключи `templates`/`position_to_category` (миграция 0014) и мёртвые
  `doc_templates`/`position_sets` (миграция 0016) удалены из БД, ручки файлов-бланков удалены
  вместе с файлами-шаблонами .docx; вкладка «Шаблоны» из админки снята (редактор писем — на
  вкладке «Письма»).
- `documents.py` — печать бланка (вариант 1, `pdf_b64` без записи версий в БД, временные файлы
  удаляются); макет — из снимка `blank_layout` (`resolve_blank_layout`), шапка/подвал/текст шага —
  из снимка `blank_header_html`/`blank_footer_lines`, `_fill_step_fio` подставляет ФИО ВСЕХ
  ответственных шага из зеркала `users` по логинам (`assignees`); групповой шаг (снимок
  ответственных пуст) печатается по `owner_group`; `_fill_hr_placeholders` — `{date}`/
  `{dismissal_date}`/`{manager}` из локальных зеркал (кадровые данные по ключу заявки, AD users).
- `docs.py` — сборка бланка из данных: `build_blank_document` (python-docx) по макету из снимка
  (`LAYOUT_PRESETS` office|line, `build_bypass_context` — снимок бланка, шаги с `assignees`/`assignee_names`
  и `stage_lines`), плейсхолдеры печати (`blank_placeholders`/`render_blank_text`, значения
  экранируются, неизвестные остаются текстом), QR и конвертация в PDF (`generate_bypass`, LibreOffice);
  переменные — см. `TEMPLATES.md`.
- `archive.py` — бэкапы (ручной, расписание, настройки, список, скачать/удалить).
- `audit.py` — журнал аудита (память + INSERT в БД).
- `mailer.py` — письма (шаблоны, SMTP/файловая очередь); адресаты шага — все ответственные из
  снимка `assignees`, кто ещё не отметился (`step_marked_sams`).
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
- `create-form.tsx` — форма создания заявки (панели, выбор бланка из `GET /requests/route/blanks`,
  маршрут-конструктор, исполнители, живой поиск руководителя в AD, кнопка «Подтвердить связь с AD»
  при `link_state=need_link`, переход в «Вручную» при блокировке маршрута).
- `request-card.tsx` — карточка заявки (шаги, отметки, история, комментарии, вложения, печать, админ СЭД);
  у шага видны все ответственные и прогресс отметок по режиму шага.
- `admin-settings.tsx` — настройки (вкладки: процесс/справочники/письма — контент; инфра/регламенты/
  доступ/архивация/бланки — только admin; легаси-вкладка «Шаблоны» удалена) и вкладка «Бланки»
  (только admin): карточка бланка (шапка `header_html`, подвал `footer_lines`) + собственные шаги
  бланка — название/текст, вид исполнителя (`executor_kind`), согласующие/группа AD/руководитель,
  режим шага (`parallel`/`sequential`), флаги `optional`/`require_comment`.
- `rich-text.tsx` — визуальный редактор текста шага и шапки бланка на TipTap
  (`@tiptap/react` + `@tiptap/starter-kit`); HTML уходит в `title`/`stage_lines` шага и
  `blank_header_html` и доходит до печати.
- `directory.tsx` — справочник сотрудников (поиск, пагинация, автосопоставление).
- `link-match.tsx` — раздел «Сопоставление 1С↔AD» (admin): ручной запуск прохода, счётчики
  расхождений, фильтры, пагинация (первая/последняя + по пять с каждой стороны), массовое
  подтверждение связок (выбранные / все на странице / рекомендованные); пагинация вынесена в
  экспортируемую `pagerPages`.
- `theme.tsx` / `theme.css` — тема (light/dark, токены `--sed-*`) и ВСЕ стили.
- `windows.tsx` — окна-попы (?view=), `api-mock.tsx` — тип Role (живой), логин/окна.

## Стили, бланки и печать (где что лежит)
- ВСЕ стили — один файл `frontend/src/theme.css` (токены, сетки, кнопки, таблицы, dropdown, панели).
- Иконки меню/темы — инлайн-SVG в `frontend/src/layout.tsx` (каталог `ico/` удалён, в сборку не входит); логотипы — `logo-oswdocs*.svg` + `frontend/public/`.
- Файлов-шаблонов `.docx` больше нет: оформление печати — два встроенных пресета `office`/`line`
  в `docs.LAYOUT_PRESETS`, выбирает `blank_layout` из снимка заявки. Шапка, таблица шагов и QR
  собираются из данных (`build_blank_document`); в `FILES_DIR` остаются только временные
  docx/pdf/qr, которые удаляются сразу после ответа печати.
- Маршрут задаёт справочник: бланк (`blanks`/`blank_steps`, миграции 0012/0013/0014) — основной
  путь, шаг бланка самостоятельный (свой текст, свой исполнитель, этап справочника не нужен);
  `route_profiles`/`approval_stages`/`route_profile_steps` (миграция 0008) — запасной подбор по
  службе за `blank_autopick` (там снятие/добавление этапов по кодам) и ручной конструктор. Старые
  `templates/template_steps/template_filters` из 0001 — пустые таблицы, в подборе не участвуют;
  мёртвый код маршрута по шаблонам удалён.
- Писем — `mail_templates` (код/тема/тело). Описание бланков, режима шага и печати — `TEMPLATES.md`.

## БД (db/alembic/versions/)
- 0001 initial (audit_log, requests, documents, settings, links), 0002 persist, 0003 doc_types/request_comments/поля карточки,
  0004 employees, 0005 doc_types автонумерация, 0006 ad_group_members/ad_group_sync_state, 0007 ad_position_directory,
  0008 справочники маршрута (ad_services/route_profiles/approval_stages/stage_assignees/route_profile_steps;
  снимок в заявке: profile_id/service_id/service_name, этап/строки/код профиля в request_steps) и снос старых templates*,
  0009 кадровые данные сотрудника (employees.ref_key/dismissal_date/hr_synced_at + индексы),
  0010 link_discrepancies (ключ карточки + reason + кандидат AD + recommended + resolved_at),
  0011 link_discrepancies.detail (JSONB: прочие кандидаты AD и соседние карточки 1С),
  0012 справочник бланков (blanks/blank_steps; снимок в dismissal_requests:
  blank_id/blank_name/blank_version/blank_layout; сид: каждый doc_types → бланк с тем же кодом,
  настройка blank_autopick='off'),
  0013 несколько ответственных и режим шага (blank_steps.approval_mode;
  request_steps.assignees/approval_mode/approvals — jsonb),
  0014 самостоятельный шаг бланка (blank_steps.stage_id → NULLABLE + title/stage_lines/executor_kind/
  assignees/owner_group/optional/require_comment; blanks.header_html/footer_lines; снимок
  dismissal_requests.blank_header_html/blank_footer_lines; очистка blank_steps/blanks;
  удаление ключей templates/position_to_category),
  0015 request_steps.optional NOT NULL DEFAULT TRUE (снимок «шаг можно снять»),
  0016 удаление мёртвых ключей настроек doc_templates/position_sets,
  0017 удаление мёртвых колонок шага бланка (blank_steps.stage_id/optional_override/
  require_comment_override — вместе с FK на approval_stages и UNIQUE (blank_id, stage_id)).

## Сценарии ключевых функций
- Создание: create-form → POST /requests → шаги; отметки: request-card → POST .../steps/{order}/decide; печать: POST /print (pdf_b64) → iframe; история: GET /history (audit_log); сотрудники: GET /employees (локальная таблица); бэкапы: /api/archive*; настройки: /api/settings.
- Маршрут по бланку: create-form → GET /requests/route/blanks (селект бланка ОК) → POST /requests/route/preview (link_state=need_link? → «Подтвердить связь с AD» → POST /requests/route/link-employee → предпросмотр заново; снятие шага бланка — `dismissed_step_orders`) → POST /requests (blank_id; снимок бланка и шагов в заявке). Запасной путь — подбор профиля по службе AD за `blank_autopick`, ручной конструктор — `route_mode=custom`. Бланк не выбран при `blank_autopick=off` — одинаковый 422 в предпросмотре и создании с перечнем вариантов.
- Сопоставление 1С↔AD: link-match → POST /link_1c_ad/sync (проход) → GET /link_1c_ad/discrepancies → POST /link_1c_ad/discrepancies/confirm (только строки с одним кандидатом AD; строка справочника помечается связанной сразу). Регламент: worker по `schedule_ad_links_sync`/`schedule_hr_dismissals_sync`.
- Кадровые данные: ежедневный проход регистра (worker) → `POST /employees/hr-sync` для запуска по требованию → `employees.dismissal_date` → поиск сотрудников скрывает уволенных, сопоставление их пропускает.
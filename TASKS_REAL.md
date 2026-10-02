# План реальной реализации СЭД (без заглушек)

> ## Статус (2026-09-30): Волны 1–5 РЕАЛИЗОВАНЫ и ЗАДЕПЛОЕНЫ на стенд `10.0.70.117`
>
> Залито: `api/app` целиком (включая requests_store/link_store/documents/attachments/worker,
> onec_sync), миграция `0002` (alembic `head`), сиды `settings` (18 ключей на стенде),
> фронт (свежая сборка), `docker-compose.yml` (совпадает с ВМ). api/worker Up и стабильны,
> `/api/health` 200, вход LDAPS+TLS ПОДТВЕРЖДЁН реальным паролем (учётка пользователя),
> IMPORT_OK, `soffice` в образе.
>
> ### Дополнительно (B-фазы 2026-09-30, коммиты 21648c7→25a3771)
> - Роль «руководитель ОК» (`hr_admin`, группа `SED_HR_ADMIN` в AD и в `.env` на ВМ):
>   полные карточки, создание заявок, контент-настройки; рядовой ОК — без настроек.
> - Настройки разделены: `GET/PUT /settings` (admin, всё) и `GET/PUT /settings/content`
>   (admin + hr_admin, контент: предприятия/группы/должности/шаблоны/бланки/письма/процесс);
>   инфра (сессии, сканы, SMTP, 1С-базы, источник) — только admin.
> - 1С-базы (`onec_bases`) и источник предприятий (`onec_enterprises_source`) в settings
>   (пароли маскируются, пишутся при вводе); клиент 1С читает базы из settings (fallback env).
> - Синхронизация предприятий из 1С: `POST /settings/enterprises/sync` (admin) + еженедельно
>   в worker (`onec_sync.maybe_sync_weekly`); до OData-контракта ИТ — ошибка «не настроено».
> - Редакторы бланков (`doc_templates`) и писем (`mail_templates`) на вкладке «Шаблоны».
> - TTL сессии 10 ч (`session_ttl_minutes=600` в settings) и SMTP-учётка (`smtp_user`/`smtp_password`)
>   в settings (пароль маскируется); сессия читается из settings при входе.
>
> ### Сессия 2026-10-01 (коммиты 8ea483c→6b57588; на стенд не задеплоено)
> - **Карточка сотрудника**: дата увольнения (`dismissal_date`, конфиг
>   `termination_date_field` дефолт `ДатаУвольнения`, пустое значение регистра
>   `0001-01-01T00:00:00` нормализуется в `""`); убраны `vacation_balance`/`mol_flag`
>   из кода/фронта (колонки БД `employee_base_map` НЕ тронуты — только по согласованию).
> - **Автосвязка 1С↔AD по точному ФИО**: `AdReader.search_users` (displayName, SUBTREE),
>   `OneCClient.list_employees` (пагинация `$skip/$top`), движок `api/app/ad_sync.py`
>   (уникальные совпадения с обеих сторон → `LinkRecord(verified=True, by=ad_sync)`),
>   запуск еженедельно из worker (`maybe_sync_links_weekly`) + `POST /link_1c_ad/sync`
>   (admin), метка `ad_links_synced_at` (read-only); флаг `ad_status`
>   (linked/match/no_match) в `/employees` и `/employees/card` без записи в БД;
>   `GET /ad/search` (admin) для ручной привязки.
> - **Фронт**: карточки заявки/сотрудника и создание — в отдельных окнах-попах
>   (`?view=request&id=…`, `?view=employee&key=…`, `?view=create`, лёгкий роутинг
>   без роутера); создание заявки — единой формой без стадий (блоки по
>   зависимостям предприятие→сотрудник→маршрут). Компоненты `RequestCard`,
>   `EmployeeCardView` вынесены из layout/directory.
> - Тесты: бэкенд пофайлово зелёный (кроме предсуществующих «висящих» файлов —
>   `test_requests_store/negative_core/requests/requests_contract` без Postgres),
>   фронт 76 passed + typecheck.
>
> ### Волна 4 (2026-10-02, коммиты 6b57588→2901b36; на стенд не задеплоено)
> - **Маршрут блоками**: конструктор в `create-form.tsx` — блоки `blocks`
>   (последовательный/параллельный, `step_order` без миграции), исполнители —
>   живой поиск AD, `can_act`/ФИО согласующего, уведомление согласующему;
>   контракт `blocks` в `requests.py` (`CreateRequestIn`/`RouteBlockSpec`).
> - **Черновики**: папка «Черновики», кнопка «Отправить на согласование»
>   (создать+подать), повтор «Отправить» после сбоя не создаёт второй черновик;
>   удаление заявки админом (`DELETE /requests/{id}`), только активные УЗ AD
>   во всех поисках, ленивый bind RO-учёткой в AD-ридере.
> - **Доступ и роли в settings**: вход — env `ALLOWED_AD_GROUPS` (bootstrap) плюс
>   инфра-ключ `access_groups` из БД (правит только админ; `resolve_allowed_groups`
>   в `settings_routes.py`, вход — `auth.py`, `deps.py`). Контент-ключ
>   `allowed_ad_groups` — только группы ручного конструктора шагов
>   (`resolve_step_groups`/`is_group_allowed_with_settings`, поиск — `employees.py`),
>   входа НЕ даёт. Роли `admin_groups`/`hr_groups`/`hr_admin_groups` (инфра) —
>   из БД с фолбэком на env `ADMIN_GROUPS`/`HR_GROUPS`/`HR_ADMIN_GROUPS`;
>   БД недоступна — фолбэк на env.
> - **Регламенты синхронизации**: `schedule_enterprises_sync`/
>   `schedule_ad_links_sync` (инфра, только админ) + уведомления по расписанию
>   в worker; учётная карточка и справочник переработаны блоками.
> - **Фронт-тема**: цвета W1–W3 по эталону `samples/` (`theme.css`), вкладка
>   «Регламенты» в админке; удалён мёртвый код `requests.tsx`,
>   `owner-view.tsx` и их тесты (2901b36), в этой же сессии — моковый
>   `employee-card.tsx` с тестом (живая карточка — `employee-card-view.tsx`).
>
> ### Открытые пункты (не код, данные/операции)
> - `ONEC_BASES_JSON`/базы в settings не заполнены — 1С ждёт OData-контракт и учётки от ИТ;
>   синхронизация предприятий заработает после настройки `onec_enterprises_source`.
> - SMTP: релей `intsrvmail.fidelio.local:587`, отправитель `tagirovam@yaltaintourist.ru`
>   (в settings); отправка без авторизации работает; `smtp_user`/`smtp_password` — при
>   необходимости стороннего релея (ввод в админке, пароль маскируется).
> - Реальные `doc_templates`/`mail_templates` (образцы корп. бланков/писем) не заведены.
> - QA-прогон на стенде (`deploy/qa/roles-matrix.spec.ts`, `audit-fullness.sql`) не выполнялся
>   (нужны реальные доменные учётки, включая `SED_HR_ADMIN`).
>
> ### Известные пробелы кода (не входят в Волны 1–5)
> - Событие письма «закрыта» (EVENT_CLOSED) не ставится в очередь (нет при finish).
> - Кнопки «Отозвать»/«Повторить шаг» в UI нет (API есть); эскалация не редактируется в админке.
> - `get_ad_reader()` (определение — `employees.py:122`, импортируется в `link.py`) больше не заглушка: создаёт реальный
>   `Ldap3Gateway` из env с ленивым bind, при сбое конфигурации AD — `None` (API не падает).
>   Каталога сотрудников из AD нет — источник истины 1С (README п.1).

База: README.md (весь), AGENTS.md (правила), скилы `fastapi-sed`, `react-sed`,
`pg-sed`, `approval-templates`, `mail-docs`, `qa-sed`. Правило: настройки/данные —
только `settings` (БД) и `env`; хардкод запрещён; 1С/AD — только чтение.

## Текущее состояние (что реально, что заглушка)

Реально: вход (LDAPS+Redis), TLS, кэш 1С, `GET/PUT /settings` (5 ключей), роуты
`/requests*` (хранилище in-memory!), `/employees*` (нужен ONEC_BASES_JSON), `/link_1c_ad`.
Заглушки: фронт на `mockApi` (папки/таблица/создание/карточка), хранилище заявок
in-memory (теряется при рестарте), нет документов/почты/шаблонов в UI, нет
предприятий/групп шагов в API.

## Волна 1 — РЕАЛЬНЫЙ контур «Создание → Заявка → Список → Папки» (без IT-данных)

Цель: создание заявки пишет в API, заявка появляется в таблице, папки работают,
роли соблюдены. Хранилище заявок остаётся in-memory (персистентность в Postgres —
Волна 2). Фронт на видимом пути НЕ использует моки.

### Контракты Волны 1 (согласованы, не менять)

**API (модули: requests.py, settings_routes.py, main.py):**
1. `GET /enterprises` (роли hr/admin, иначе 403) → `[{"code": "...", "name": "..."}]`
   из таблицы settings ключ `enterprises` (JSON-массив сида). Пусто → `[]`. 503 при падении БД.
2. `GET /step-groups` (hr/admin) → `["..."]` — группы для ручного конструктора шагов:
   из settings ключ `allowed_ad_groups` (JSON-массив). Пусто → `[]`.
3. `CreateRequestIn` + поле `fio: str` (ФИО сотрудника: из 1С-карточки при наличии
   баз, иначе вводит ОК). `_Request`/`RequestOut` + поле `fio` (ПДн: в `_public_view`
   урезается для владельца — владельцу fio не отдавать).
4. `GET /folders` (все роли с входом) → `[{"id": "agreement|revision|done|mine",
   "title": "...", "count": n}]`. Счётчики из in-memory хранилища: agreement — статус
   «На согласовании», revision — «На доработке», done — «Завершено»/«Отклонено»/
   «Отозвано», mine — число заявок с шагами на текущего пользователя. Для владельца —
   только mine.
5. Статусы и переходы (submit/to-execution/finish/decision) — уже есть, не менять.

**Фронт (модули: create-form.tsx, layout.tsx, новый requests-client.tsx):**
6. `requests-client.tsx` (новый): `getEnterprises()`, `getStepGroups()`, `createRequest(body)`,
   `getRequests()`, `getFolders()` — fetch `/api/*` с Bearer; 401/403/422/503 → ApiHttpError.
7. `create-form.tsx`: предприятия и группы — только из API (никаких хардкод-массивов).
   Шаг 2 «Сотрудник»: попытка `GET /api/employees` (по предприятию); при 503 «данные 1С
   не настроены» — ручной ввод `fio`, `tab_num`, `department`, `position`. Шаг 3 «Маршрут»:
   ручной конструктор из групп `GET /step-groups` (шаблоны — из settings, пусто — только
   ручной). Кнопка «Создать» → `POST /api/requests` (201) → статус «Заявка REQ-XXXX создана»,
   сброс формы. Ошибки 422/403 — понятный текст.
8. `layout.tsx` / таблица: папки — `GET /api/folders`; строки — `GET /api/requests`,
   маппинг RequestOut → RequestRow: id, fio (employeeLabel), enterprise, status, шаг —
   первый не-завершённый шаг (steps), dueDate — его expires_at. Фильтры (статус/предприятие/
   поиск) — на клиенте по уже загруженным строкам. Папка фильтрует по статусу на клиенте.
   Владельцу — свои (API уже режет), маска вместо ФИО (API не отдаёт fio владельцу).
9. Моки `api-mock.tsx` больше НЕ используются на видимом пути (остаются для тестов
   экранов, не для прод-данных). `create-form.test.tsx`/`layout.test.tsx` обновить на
   мок `requests-client`.

### Приёмка Волны 1
- `pytest api/tests` + `npm test` + `typecheck` зелёные; на стенде: ОК создаёт заявку →
  появляется в «Заявках», папки/счётчики работают, владелец видит только свои (маска).

## Волна 2 — персистентность Postgres + администратор контента
- Заявки/шаги в Postgres (dismissal_requests, request_steps) за тем же интерфейсом.
- Админка: управление `enterprises`, `allowed_ad_groups`, `position_to_category`,
  `templates` (CRUD через таблицу settings), `doc_templates`/`mail_templates`.
- `GET/PUT /settings` расширить: enterprises, allowed_ad_groups, position_to_category.

### Контракты Волны 2 (согласованы, не менять)

**B1 — персистентность заявок (requests.py):**
- Новый модуль `api/app/requests_store.py`: `RequestsStore` (Protocol) +
  `DbRequestsStore` (SQLAlchemy, таблицы `dismissal_requests`/`request_steps` из
  `db/alembic/versions/0001_initial_schema.py`) + `InMemoryRequestsStore` (для
  офлайн-тестов и дефолта без БД). Методы: `create(request)`, `get(request_id)`,
  `list_all()`, `update(request)`, `next_id() -> "REQ-XXXX"`.
- `requests.py`: все обращения к `_REQUESTS`/`_SEQ` перевести на зависимость
  `store: RequestsStore = Depends(get_requests_store)`; `get_requests_store()` —
  лениво создаёт `DbRequestsStore` (как `get_settings_store`); офлайн-тесты
  переопределяют InMemoryRequestsStore. Поведение/контракты эндпоинтов не меняются.
- `reset_state_for_tests` оставить для InMemoryRequestsStore.

**B2 — расширенный /settings (settings_routes.py):**
- `GET /settings` → все ключи: approval_ttl_days, scan_retention_days, scan_max_mb,
  require_paper_signature, smtp_from, require_comment, enterprises
  ([{code,name}]), allowed_ad_groups ([str]), position_to_category ({str:str}),
  position_escalation ({str:int}), templates ([{service,category,steps:[{owner_group,
  resolver?, require_comment?}]}]). Отсутствующий ключ → null.
- `PUT /settings`: тело — те же поля, ВСЕ опциональны (частичное обновление:
  обновляются только присутствующие), ответ — полное текущее состояние.
  Типы валидируются pydantic (422 при неверных).
- Админ-только (403), аудит settings.update с detail по изменённым ключам.
- `GET /enterprises`/`GET /step-groups` (Волна 1) — переиспользовать те же ключи.

**B3 — админка контента (frontend):**
- `settings-client.tsx`: тип `SettingsData` расширить (все ключи B2, nullable);
  `getSettings()`/`saveSettings(data)` — PUT частичное.
- `admin-settings.tsx`: секции: базовые (TTL/сканы/флаг/smtp_from/require_comment),
  предприятия (список code+name: добавить/удалить/переименовать),
  группы доступа (список строк), должность→категория (пары ключ→значение),
  шаблоны маршрутов (список: служба+категория+шаги owner_group). Сохранение —
  PUT одним объектом. Без хардкода значений.
- Тесты на новые секции (мок requests-client/settings-client).

## Волна 3 — документы, почта, worker (Фаза 4)
- Шаблоны бегунков (doc_templates по службе+категории), генерация DOCX→PDF+QR (worker,
  LibreOffice), версии v1/v2, `mail_queue`+`mail_templates`, SMTP Exchange, эскалация.
- `GET /documents/{id}` (печать), POST печать. Worker-модуль `app/worker`.

### Контракты Волны 3 (согласованы, не менять)

**W3a — backend (docs.py, mailer.py, worker.py, эндпоинты; скилы approval-templates, mail-docs):**
1. Настройки из БД (ключи settings, admin управляет через /settings): `doc_templates`
   ([{service, category, body} — шаблон бегунка по службе+категории]),
   `mail_templates` ([{code, subject, body_html}] — события v1: назначена/напоминание/
   эскалация/закрыта/возврат), `position_escalation` ({должность: часы}) — уже есть.
   В коде НЕ хардкодить ни шаблонов, ни подписей, ни TTL.
2. `POST /requests/{id}/print` (ОК/админ): генерация бегунка по doc_templates
   (служба+категория заявки); нет шаблона → ручной конструктор (см. request_steps).
   Версия: v1 — первая, v2 — повторная (документ с тем же номером, version=v2).
   DOCX — через `python-docx-template` (Jinja-подобный), PDF — LibreOffice headless
   (`soffice --convert-to pdf`), QR — `qrcode` с payload = url заявки. Офлайн/нет
   LibreOffice → файл не создаётся, в ответе `{"generated": false, "reason": ...}`
   (не 500!). Запись в таблицу documents (version, docx_path, pdf_path, qr_payload).
   Возврат: {version, pdf_path, qr_payload}.
3. `GET /documents/{request_id}` (ОК/админ/владелец своего шага): мета документов
   заявки (версии, пути, QR). `GET /documents/{request_id}/pdf?version=v1` →
   FileResponse pdf (404 если нет).
4. `mailer.py`: реальный SMTP через `smtplib` (SMTP_HOST/FROM/USER/PASSWORD из env,
   STARTTLS), письма — из `mail_templates` (Jinja-рендер по событию), получатель —
   `mail` из AD (шаг: владелец группы), события: назначена (при submit), напоминание
   (TTL/2), эскалация (position_escalation часов), закрыта/возврат. Офлайн —
   существующий MockMailer. Очередь: `mail_queue` из 0001 (если таблицы нет —
   FileMailQueue как offline, БД — на стенде через 0003? НЕ создавать миграцию без
   нужды: проверь 0001, есть ли mail_queue/mail_templates).
5. `app/worker.py` (новый): цикл (или однократный запуск по команде) — берёт заявки
   «На согласовании» с невыполненными шагами: просроченные (expires_at < now) →
   статус шага «просрочен» + заявка «На доработке» + письмо «возврат»; эскалация —
   письмо руководителю через position_escalation; напоминания за N часов до дедлайна.
   Только чтение/обновление заявок через RequestsStore (никакой записи в 1С/AD).
   Команда в compose у worker уже есть (`python -m app.worker`).
6. `requirements.txt`: раскомментировать `python-docx-template`, `qrcode` (LibreOffice —
   системный пакет на ВМ, пометка в Dockerfile). НЕ ставить софт локально.
7. Тесты: генерация версий v1/v2 (мок subprocess/LibreOffice), маршрут print без
   шаблона → ручной, нет шаблона и нет шагов → 422, mailer MockMailer-очередь,
   worker-логика на InMemoryRequestsStore (просрочка → «На доработке» + письмо).

**W3b — frontend (скил react-sed):**
8. `requests-client.tsx`: `printRequest(id)`, `getDocuments(id)`.
9. `layout.tsx`: кнопка «Печать» в тулбаре/строке → printRequest → статус
   «v1 сгенерирован / v2» или ошибка; блок «Документы» в карточке заявки (версии, ссылка
   на PDF `GET /api/documents/{id}/pdf?version=v1`). Без хардкода текстов/путей.
10. Тесты на печать/документы (мок requests-client).

## Волна 5 — QA/НФТ, отметки владельцев, скан-вложения (Фаза 5–6)
- Матрица ролей (playwright-скрипты в `deploy/qa/`), негативные тесты, rate-limit
  логина через Redis, полнота `audit_log`, «нет записи в 1С/AD».
- Реальная карточка заявки (шаги, отметки владельца approve/reject/return, submit/
  to-execution/finish для ОК), скан-вложения (лимиты из settings).

### Контракты Волны 5 (согласованы, не менять)

**W5a — backend (fastapi-sed, qa-sed):**
1. Rate-limit логина: Redis-счётчик `sed:login:{ip}:{login}` — `LOGIN_RATE_LIMIT`
   попыток за окно `LOGIN_RATE_WINDOW_SECONDS` (env, дефолты 5/60), 429 после лимита
   (до проверки пароля), сброс при успехе. Редкость: падение Redis — пропускать
   лимит (не валить вход), как SessionUnavailable-паттерн.
2. Скан-вложения: `POST /requests/{id}/attachments` (multipart; hr/admin/владелец
   своего шага) — лимит размера `scan_max_mb` из settings (нет ключа → без лимита? НЕТ:
   без ключа → 409 «лимит не задан»), MIME-allowlist из settings `scan_allowed_types`
   (default [] → пусто = запрещено, но если список пуст и в БД — 409). Файл →
   `/app/files/attachments/{request_id}/`, мета в таблицу `attachments` (0001),
   `scan_retention_days` — мета, не применяется на чтении. `GET /requests/{id}/attachments`
   → мета; `GET /attachments/{id}/file` → FileResponse (роли как у заявки).
3. Тесты: rate-limit (фейковый Redis: 429 после N, сброс при успехе, Redis-down →
   вход работает), attachments (мок хранилища файлов: размер>лимит → 413, тип вне
   allowlist → 415, мета в store).

**W5b — frontend (react-sed):**
4. Реальная карточка заявки под таблицей (layout.tsx): `GET /api/requests/{id}` →
   шаги (группа/статус/срок), кнопки владельцу своего шага «Согласовать/Отказать/
   Вернуть» + комментарий (обязателен при отказе/возврате) → `POST /api/requests/{id}/
   steps/{order}/decision`; ОК/админу — submit/to-execution/finish. Отметки пишутся в
   карточку сразу (refetch).
5. Скан-вложения в карточке: список `GET /api/requests/{id}/attachments`, загрузка
   `POST .../attachments` (input file), удаление? (нет delete — только мета). Ошибки
   413/415/409 — понятный текст.
6. Тесты (мок requests-client): карточка, отметка владельца, submit ОК, загрузка скана.

**W5c — QA-скрипты (qa-sed):**
7. `deploy/qa/` — playwright-скрипт матрицы ролей (admin/hr/owner/guest: видимость
   вкладок, создание, настройки, карточка) + чек-лист `deploy/qa/README.md` + проверка
   полноты `audit_log` (скрипт sql-запросов). На ВМ — после деплоя, локально не гонять.

## Запреты
- 1С/AD только чтение; настройки только settings/env; хардкода нет; коммиты — по команде;
  временное — `./temp/`; вне проекта не выходить.
# План реальной реализации СЭД (без заглушек)

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

**Фронт (модули: create-form.tsx, layout.tsx, requests.tsx, новый requests-client.tsx):**
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

## Волна 4 — 1С и связки (данные ИТ)
- ONEC_BASES_JSON на ВМ; `/employees` живой; создание через выбор сотрудника из базы;
  `link_1c_ad` в Postgres; снапшот 1С в заявке.

## Волна 5 — QA/НФТ (Фаза 6)
- Матрица ролей (playwright), негативные тесты, rate-limit логина, HSTS/TLS,
  полнота `audit_log`, «нет записи в 1С/AD».

## Запреты
- 1С/AD только чтение; настройки только settings/env; хардкода нет; коммиты — по команде;
  временное — `./temp/`; вне проекта не выходить.
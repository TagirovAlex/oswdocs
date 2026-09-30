# План реальной реализации СЭД (без заглушек)

> ## Статус (2026-09-30): Волны 1–5 РЕАЛИЗОВАНЫ и ЗАДЕПЛОЕНЫ на стенд `10.0.70.117`
>
> Залито: `api/app` целиком (включая requests_store/link_store/documents/attachments/worker),
> миграция `0002` (alembic `head`), сиды `settings` обновлены (на стенде 15 ключей:
> `scan_allowed_types` + `smtp_host`/`smtp_port` от деплоя, `templates` — добавлен через админку),
> фронт (свежая сборка), `docker-compose.yml` (совпадает с ВМ). api/worker Up и стабильны,
> `/api/health` 200, вход LDAPS+TLS (wrongpass → 401), IMPORT_OK, `soffice` в образе (вариант a).
> Коммиты: `1f9bf99` (fix deploy: LibreOffice/docxtpl/python-multipart/worker-loop), `9be6e6f`
> (SMTP-параметры в settings).
>
> ### Открытые пункты (не код, данные/операции)
> - Вход реальным доменным паролем — ждёт человека (официально не проверялся).
> - `ONEC_BASES_JSON` не задан — карточки/поиск сотрудников из 1С недоступны (1С вне объёма).
> - SMTP: релей `intsrvmail.fidelio.local:587`, отправитель `tagirovam@yaltaintourist.ru`
>   (в settings); `SMTP_USER`/`SMTP_PASSWORD` пусты — если релею нужна авторизация, дать учётку.
> - Реальные `doc_templates`/`mail_templates` (образцы корп. бланков/писем) не заведены.
> - QA-прогон на стенде (`deploy/qa/roles-matrix.spec.ts`, `audit-fullness.sql`) не выполнялся
>   (нужны реальные доменные учётки).
>
> ### Известные пробелы кода (не входят в Волны 1–5)
> - Событие письма «закрыта» (EVENT_CLOSED) не ставится в очередь (нет при finish).
> - Кнопки «Отозвать»/«Повторить шаг» в UI нет (API есть); эскалация не редактируется в админке.
> - Блок AD в `employees.py`/`link.py` отключён: `get_ad_reader()` возвращает `None` (AD читается
>   только в auth и worker). Каталога сотрудников из AD нет — источник истины 1С (README п.1).

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
9. `layout.tsx`/`requests.tsx`: кнопка «Печать» в тулбаре/строке → printRequest → статус
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
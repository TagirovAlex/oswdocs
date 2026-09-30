# Деплой полного кода проекта на стенд (10.0.70.117) — задание агенту

> ## Статус (2026-09-30): ВЫПОЛНЕНО
>
> Результат: код Волн 1–5 залит, миграция `0002` накатана, `settings` — 18 ключей
> (включая `scan_allowed_types`, `smtp_host/smtp_port/smtp_user/smtp_password`,
> `session_ttl_minutes`), фронт собран и в volume `sed_frontend_dist`, api/worker Up без
> рестартов, `/api/health` 200, 401 на settings/me/enterprises/login(wrongpass), IMPORT_OK,
> `soffice=True` (вариант a). Вход реальным паролем подтверждён.
> Дополнительно (B-фазы, коммиты `21648c7`→`25a3771`): роль `hr_admin` (SED_HR_ADMIN),
> разделение настроек контент/инфра (`/settings/content`), 1С-базы и источник предприятий
> в settings (маска паролей), синхронизация предприятий из 1С, редакторы бланков/писем,
> TTL сессии 10 ч, SMTP-учётка в settings. Отклонения и открытое — в `TASKS_REAL.md`.

Запускается ОТДЕЛЬНОЙ сессией. Прочитай перед стартом: `AGENTS.md`, `README.md`
п.2/п.6, `TASKS_REAL.md` (что деплоим), хелперы `temp/ssh_probe.py`,
`temp/sftp_put.py`, `temp/su_exec.py` (пароли сами читают из `./.env`, НЕ выводить).
Рабочий каталог `C:\Github\oswdocs`. Вне проекта не выходить; временное — `./temp/`.

## Контекст: текущее состояние ВМ (на входе)

- Работают: `sed-proxy-1`, `sed-api-1`, `sed-redis-1` (healthy), `sed-db-1` (healthy).
  Worker НЕ запущен.
- На ВМ уже залиты (прошлые волны): ad_reader.py, auth.py, config.py, employees.py,
  onec_cache.py, onec_client.py, settings_routes.py, main.py (версия «настройки»);
  `/srv/sed/certs/fidelio-root-ca.pem` (root CA), `AD_TLS_VALIDATE=true`,
  `AD_CA_CERT=/etc/ssl/certs/sed-ca.pem`; frontend-объём `sed_frontend_dist` со
  старой сборкой; БД: миграция `0001` накатана + сиды (S2a), `0002` НЕ накатана.
- `.env` на ВМ: AD_*, AUTH_MOCK_ENABLED=false, ALLOWED/ADMIN/HR_GROUPS, DATABASE_URL,
  AD_TLS_VALIDATE, AD_CA_CERT. Секреты не выводить, файл не переписывать (только append).

## Что деплоим (HEAD репозитория, коммиты eeed56e→dab8227)

ВСЁ содержимое (код Волн 1–5):
- `api/app/` — целиком (requests, requests_store, link, link_store, settings_routes,
  auth, config, deps, docs, mailer, documents, attachments, worker, employees,
  onec_*, ad_reader, audit, main и т.д.)
- `api/requirements.txt` — уже включает psycopg[binary], python-docx-template, qrcode
- `db/alembic/` — `versions/0002_requests_persist.py` (новое), `env.py`, `alembic.ini`
- `db/seeds/settings.sql` — обновлён (добавлен `scan_allowed_types`)
- `frontend/dist` — пересобрать локально и залить в `sed_frontend_dist`
- `docker-compose.yml` — сверить с ВМ (если на ВМ старая версия — залить)

## ШАГИ (каждый — отдельный вызов хелпера с выводом; RC=0)

### 1. Состояние и доступ
`docker compose --project-directory /srv/sed ps --format '{{.Name}} {{.Status}}'`;
проверить `ls /srv/sed/api/app`, `ls /srv/sed/db/alembic/versions`, версию alembic:
`docker compose --project-directory /srv/sed run --rm -T api python -c "from alembic.config import Config; ..."` —
проще: накатить и проверить `alembic_version` таблицы.

### 2. Заливка кода (sftp_put.py, root)
- Весь `api/app/` → `/srv/sed/api/app/` (пакетом: `tar -czf temp/api-app.tar.gz -C api app`,
  залить, распаковать `tar -xzf ... -C /srv/sed`). Убедиться, что новые файлы на месте:
  `requests_store.py`, `link_store.py`, `documents.py`, `attachments.py`, `worker.py`.
- `db/` целиком → `/srv/sed/db/` (alembic.ini, env.py, versions/, seeds/) тем же способом.
- `docker-compose.yml` → `/srv/sed/docker-compose.yml` (сверка `diff -q` — если отличается).
- `frontend` — см. шаг 5.

### 3. .env на ВМ (только APPEND отсутствующих ключей, значения не выводить)
Для каждого ключа: `grep -q '^KEY=' /srv/sed/.env || echo 'KEY=значение' >> /srv/sed/.env`:
- `LOGIN_RATE_LIMIT=5`, `LOGIN_RATE_WINDOW_SECONDS=60` (дефолты, можно пропустить — кода хватает)
- `APP_BASE_URL=https://oswdocs.fidelio.local` (для QR/писем)
- `FILES_DIR=/app/files` (дефолт)
- `SMTP_PORT=587` (порт релея уточнит ИТ; если пусто — письма в очередь, отправка по настройке)
- `SMTP_USER=`, `SMTP_PASSWORD=` — НЕ заполнять без данных ИТ (пусто — MailQueue копит).
После: `chmod 600 /srv/sed/.env`; проверить только имена ключей.

### 4. Миграции и сиды (live Postgres 17)
- `docker compose --project-directory /srv/sed run --rm -T api alembic upgrade head`
  (рабочая директория `/srv/sed/db`, `-e DATABASE_URL` при необходимости — как в S2a).
- Проверить: `SELECT version_num FROM alembic_version;` → `0002`.
- Сиды: прогнать `db/seeds/settings.sql` (psql или через alembic-скрипт) — идемпотентно,
  ключей `settings` должно стать 12 (включая scan_allowed_types).
- Проверка downgrade/upgrade на живых данных НЕ требуется (данные важнее); достаточно upgrade head.

### 5. Фронт: сборка и заливка
- Локально: `npm run build` (в `frontend/`), `tar -czf temp/frontend-dist-deploy.tar.gz -C frontend/dist .`,
  `sftp_put.py` → `/tmp/`.
- На ВМ: перелить в volume без полного up:
  `docker run --rm -v sed_frontend_dist:/t -v /tmp:/hosttmp:ro nginx:stable-alpine sh -c 'rm -rf /t/* && tar -xzf /hosttmp/frontend-dist-deploy.tar.gz -C /t && ls /t/assets'`.

### 6. LibreOffice для PDF (решение по плану Волны 3)
PDF-генерация (`soffice --convert-to pdf`) выполняется ВНУТРИ контейнера api.
- Проверить: `docker compose --project-directory /srv/sed run --rm -T api python -c "import shutil; print(bool(shutil.which('soffice')))"`.
- Если False: варианты (выбрать и зафиксировать в отчёте):
  a) добавить в `api/Dockerfile` перед USER appuser: `RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer && rm -rf /var/lib/apt/lists/*` — правка репо (локальная, коммит — по команде человека), пересборка api;
  b) оставить как есть — `POST /print` вернёт `generated=false` (не 500), PDF появится после установки.
  Рекомендуется (a) — иначе печать не работает.

### 7. Пересборка и запуск сервисов
- `docker compose --project-directory /srv/sed build api worker` (один образ, две команды; лог /tmp/dep_build.log, tail -5).
- `docker compose --project-directory /srv/sed up -d api worker` (proxy/db/redis не трогать без нужды),
  `sleep 10`, `ps` — api и worker Up, worker БЕЗ падений/рестартов.
- `docker compose --project-directory /srv/sed logs --tail 30 worker 2>&1 | grep -viE 'variable is not set' | tail -30` —
  worker не должен упасть с Traceback (проверить `restart` счётчик: `STATUS Up (N)`, не `Restarting`).

### 8. Проверки (живой стенд)
- `/api/health` → 200 (`curl -sk --resolve oswdocs.fidelio.local:443:127.0.0.1 https://oswdocs.fidelio.local/api/health`).
- `/api/settings` без токена → 401 (маршрут есть); `/api/auth/me` без токена → 401;
  `POST /api/auth/login` wrongpass → 401 (LDAPS+TLS работает).
- `/api/enterprises` без токена → 401 (новый роут).
- Фронт: curl index.html → бандл `index-*.js` (свежий, из шага 5).
- Импорт модулей в контейнере: `docker compose --project-directory /srv/sed run --rm -T api python -c
  "import app.main; import app.worker; from app import documents, attachments, mailer; print('IMPORT_OK')"`.
- БД-сторы: `python -c "from app.settings_routes import DbSettingsStore; from app.config import Settings;
  s=DbSettingsStore(Settings().DATABASE_URL); print(s.get('scan_allowed_types'))"` — непусто.
- Логи api/worker: без Traceback/ModuleNotFoundError/FK-ошибок.
- ВХОД РЕАЛЬНЫМ ПАРОЛЕМ (200 + /auth/me) — НЕ делать (пароль даёт человек), пометить в отчёте.

## ЗАПРЕТЫ
- Не менять `/srv/sed/certs` (700 root:docker); не трогать `sed.service`; не делать полный
  `compose up` без нужды (только build api worker + up -d api worker); worker НЕ запускать до
  миграции 0002 (иначе FK-ошибки на пустой схеме).
- Секреты/пароли не выводить; .env на ВМ только append; git не трогать (кроме разрешённого
  локального диффа Dockerfile из шага 6 — коммитить НЕЛЬЗЯ без команды человека).
- 1С/AD — только чтение (деплой ничего не пишет в них).
- Вне проекта не выходить; временное — только `./temp/` (архивы шага 2/5).

## ОТЧЁТ (финальное сообщение)
1) что залито/изменено на ВМ (файлы, ключи .env по именам); 2) версия БД (0002), число
ключей settings; 3) compose ps (api/worker Up, worker не рестартует); 4) результаты проверок
(health/401/IMPORT_OK/soffice/бандл); 5) LibreOffice: (a) или (b); 6) открытое: вход реальным
паролем, SMTP-данные ИТ, ONEC_BASES_JSON, реальные шаблоны бланков/писем; 7) отклонения дословно.
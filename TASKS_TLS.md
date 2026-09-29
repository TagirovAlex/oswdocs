# TLS-валидация LDAPS корневым CA (доводка боевого стенда, волна C)

Цель: включить проверку цепочки сертификата LDAPS внутренним корневым CA
`FIDELIO-DC2-CA` на боевой ВМ (`10.0.70.117`, `oswdocs.fidelio.local`).
Сейчас `AD_TLS_VALIDATE` не задан (дефолт false — цепочка не проверяется),
вход на стенде уже работает. Внутренний CA — по решению владельца (серт только внутренний).

База: README п.1 (Auth), скил `ad-reader`, коммиты `45d14f2`/`7e8ecd0` (доменный вход),
`deploy/LAYOUT.md` (раскладка certs), хелперы `temp/ssh_probe.py`, `sftp_put.py`, `su_exec.py`,
доступы ВМ — локальный `./.env` (`SED_VM_*`, gitignored).

## Готово (в worktree, НЕ закоммичено)

* `api/app/ad_reader.py` — правки под TLS-валидацию:
  - `AdReaderSettings.ca_certs_file` (env `AD_CA_CERT`), `from_env` читает;
  - `_make_tls()`: при `tls_validate=true` требует `AD_CA_CERT` (иначе
    `AdReaderError`) и отдаёт `ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=...)`;
    при false — как раньше, `ssl.CERT_NONE`.
* `docker-compose.yml` — сервисы `api` и `worker`:
  - env `AD_TLS_VALIDATE: ${AD_TLS_VALIDATE:-false}`, `AD_CA_CERT: ${AD_CA_CERT:-}`;
  - read-only mount `${CERT_PATH:-/srv/sed/certs}:/etc/nginx/certs:ro`.
* На ВМ уже сделано: корневой CA извлечён из цепочки `sed.crt`
  в `/srv/sed/certs/fidelio-root-ca.pem` (self-signed, `CN=FIDELIO-DC2-CA`,
  2023-06-07..2030-08-20); сертификат `DC1.FIDELIO.LOCAL` подписан этим же CA.

## Задачи

### 1. Тесты (локально, офлайн)
* `api/tests/test_ad_gateway.py` — починить падающий
  `test_tls_validate_true_uses_cert_validation` (сейчас 1 failed):
  в `_settings()`/`_gateway()` для этого кейса передать `ca_certs_file`
  (например `/etc/nginx/certs/fidelio-root-ca.pem`) и проверить
  `tls.ca_certs_file == ...` + `tls.validate == ssl.CERT_REQUIRED`.
* Добавить негативный кейс: `tls_validate=true` без `AD_CA_CERT` → `AdReaderError`
  (поведение `_make_tls`, чтобы не откатить).
* Прогнать: `python -m pytest api/tests -q` → ожидается 119 passed.
  Фронт не трогали — `npm test` (39 passed) только для регрессии, при желании.

### 2. ВМ: .env, деплой, пересборка
* `/srv/sed/.env` добавить:
  `AD_TLS_VALIDATE=true`, `AD_CA_CERT=/etc/nginx/certs/fidelio-root-ca.pem`
  (путь ВНУТРИ контейнера; на хосте файл лежит в `/srv/sed/certs/...`).
* Залить `api/app/ad_reader.py` и `docker-compose.yml` на ВМ (`sftp_put.py`).
* Пересобрать и перезапустить только api:
  `docker compose --project-directory /srv/sed build api`, затем `up -d api`.
  **Worker НЕ поднимать** — модуля `app.worker` ещё нет (Фаза 4).

### 3. Проверка вживую (через хелперы)
* `docker compose run --rm api python -c "..."` — bind RO-учеткой
  (`Ldap3Gateway` из `app.ad_reader`) + `search_user_by_sam('tagirovam')`:
  цепочка проверяется корневым CA, запись находится, членство в группах на месте.
* `POST /api/auth/login` доменной учеткой (пароль даст человек) — вход работает,
  как раньше; `/auth/me` по токену.
* Логи api: нет ошибок ssl/TLS/CA (в т.ч. `sslv3 alert handshake failure` — признак
  того, что CA не подошёл, тогда проверить, что в файле именно корневой, не серверный).

### 4. Коммит — только после явной команды человека
* `git add api/app/ad_reader.py docker-compose.yml` (и `api/tests/test_ad_gateway.py`),
  сообщение в стиле репо: `fix(auth): ...` / `feat(auth): ...`.
* Перед коммитом: `git status`, `git diff` — в коммит только эти файлы, секретов нет.

## Запреты
* Worker не поднимать и `app/worker.py` не создавать (Фаза 4 — отдельно).
* Секреты — только `/srv/sed/.env` на ВМ; в чат/команды/коммиты не попадают.
* `/srv/sed/certs` не менять (права `700 root:docker`, серты корпоративные).
* `sed.service` и полный `compose up` без надобности не трогать.
* Вне проекта не выходить; временное — только `./temp/`.
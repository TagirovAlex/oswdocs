# Чек-лист НФТ стенда (W5c Волны 5, README п.5)

> Статус (2026-09-30): НЕ закрыт — ждёт пилота/ИТ. На стенде подтверждено только:
> `/api/health` 200, 401 без токена, wrongpass-вход 401 (LDAPS+TLS), rate-limit-ключи
> в `.env`, `scan_*` в settings (12+ ключей). Остальное — по мере данных ИТ и прогона QA.

Закрывается на стенде после деплоя, перед приемкой Фазы 6. Пункты с отметкой
«руками» — выполняет ИТ/админ вручную; остальные — скриптами/наблюдением.

## 1. TLS 1.2+ / HSTS (proxy, nginx)

- [ ] На `:443` отдается только TLS 1.2 и 1.3 (TLS 1.0/1.1 отключены):
  ```bash
  nmap --script ssl-enum-ciphers -p 443 https://sed.company.local
  ```
  либо `openssl s_client -tls1_1 -connect sed.company.local:443` → ошибка.
- [ ] Ответ содержит заголовок `Strict-Transport-Security` (`max-age` задан):
  ```bash
  curl -sI https://sed.company.local | grep -i strict-transport-security
  ```
- [ ] `:80` только редиректит на `:443` (301), контента по HTTP нет.
- [ ] Наружу не опубликованы порты `8000/5432/6379` (только `443`, возможно `80`).

## 2. Сессии 15–20 минут (Redis)

- [ ] TTL сессии в env `SESSION_TTL_MINUTES` в диапазоне 15–20 (README п.5,
      дефолт 20, `api/app/config.py:session_ttl_seconds`).
- [ ] По истечении сессии `GET /api/requests` с прежним токеном → 401, SPA
      возвращает на экран логина (проверка — смена/удаление ключа
      `sed:session:*` в Redis и повтор запроса).
- [ ] Выход («Выйти») удаляет сессию в Redis (`sed:session:{token}`).

## 3. Rate-limit логина (Redis, W5a)

- [ ] `LOGIN_RATE_LIMIT` попыток за окно `LOGIN_RATE_WINDOW_SECONDS`
      (env, дефолты 5/60): после лимита неверных попыток — `429` ДО проверки
      пароля; сброс счётчика при успешном входе.
  ```bash
  # N неверных паролей подряд → на N+1 ответ 429 (а не 401)
  for i in $(seq 1 8); do curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST https://sed.company.local/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"login":"...","password":"wrong"}'; done
  ```
- [ ] Сбой Redis не валит вход (пропуск лимита по паттерну SessionUnavailable).

## 4. RPO / RTO (ИТ, руками)

- [ ] `pg_dump` по регламенту в `/srv/sed/backups` (периодичность — по
      `deploy/ЧЕК-ЛИСТ_ИТ.md`); дамп покрывает `settings`, `mail_templates`,
      `audit_log` (`deploy/LAYOUT.md`).
- [ ] Снапшот ВМ/тома настроен (физический уровень, `pgdata`).
- [ ] Целевые **RPO 24ч / RTO 4ч** подтверждены тестовым восстановлением
      (восстановление из дампа на чистую ВМ в пределах RTO).
- [ ] Ротация и выгрузка бэкапов наружу — по регламенту ИТ.

## 5. ПДн по ролям (README п.1, скрипт roles-matrix.spec.ts)

- [ ] Владелец видит только свои задачи; `fio/tab_num/enterprise` = null,
      в таблице — маска «Сотрудник № …».
- [ ] ОК (`hr`) и админ видят полную карточку; ОК не имеет доступа к
      настройкам (`/api/settings` → 403).
- [ ] Настройки — только `SED_ADMINS` (`/api/settings` GET/PUT → 403 для
      остальных; аудит `settings.read`/`settings.update`).
- [ ] Гость (без сессии) — только экран логина; все `/api/*` без токена → 401.
- [ ] Поиск сотрудников: ОК/админ — по своим правам, владелец — только свои.

## 6. Аудит (qa, audit-fullness.sql)

- [ ] `audit_log` append-only: UPDATE/DELETE запрещены триггером (п.6 скрипта).
- [ ] Полнота: события входа, заявок, шагов, настроек и печати за день
      присутствуют, событий без `actor` нет.
- [ ] Нет записи в 1С/AD: `AD_WRITE_ENABLED=false`, вызовов записи в коде нет
      (только чтение GET).

## 7. Прочее

- [ ] Ошибки 4xx/5xx не отдают стектрейсы и ПДн в ответах.
- [ ] Заголовки `X-Frame-Options`/`nosniff` на ответах nginx (защита от
      clickjacking/сниффинга MIME).
- [ ] Лимиты сканов в settings (`scan_max_mb`, `scan_retention_days`) заданы,
      пустые/отсутствующие ключи не позволяют загрузить файл (409).
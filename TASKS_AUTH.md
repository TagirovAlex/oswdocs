# Реальная доменная аутентификация (LDAP bind + /auth + экран логина)

> Статус (2026-09-30): волны A (A1–A3) и C ВЫПОЛНЕНЫ. На стенде: `AUTH_MOCK_ENABLED=false`,
> `ALLOWED_AD_GROUPS`/`ADMIN_GROUPS`/`HR_GROUPS` заданы, вход LDAPS+TLS работает
> (`POST /api/auth/login` wrongpass → 401, `/auth/me` без токена → 401, rate-limit в Redis).
> Открыто: вход реальным доменным паролем — ждёт человека.

Цель: заменить мок-роль (`initialRole="hr"` в main.tsx и X-Mock-* заголовки в deps.py)
на реальный вход доменной учеткой через LDAPS. Данные AD уже в `.env` на ВМ:
`AD_URL=ldaps://DC1.FIDELIO.LOCAL:636`, `AD_BASE_DN=DC=FIDELIO,DC=LOCAL`,
`AD_READER_DN=CN=oswdocs,CN=Users,DC=FIDELIO,DC=LOCAL`, `AD_READER_SECRET` (на ВМ).
OU `OSWDOCS`, группы `SED_HR/SED_ADMINS/SED_STEP_EXEC` — подтверждены, в БД `sed_ou`/`allowed_ad_groups`.

База: README п.1 (Auth), п.5 (сессии 15–20 мин), скилы `fastapi-sed`, `ad-reader`, `react-sed`,
существующие `api/app/{config,deps,ad_reader,main}.py`, `frontend/src/{main,layout,api-mock}.tsx`.

## Контракты (согласованы, не менять)

* `POST /auth/login` — JSON `{login, password}` → `200 {token, user}` или `401` (неверные/нет групп) / `503` (AD недоступен).
  `user` — объект `CurrentUser` (sam, fio, department, title, mail, groups, role) — полная обрезка как в `/me`.
* `GET /auth/me` — Bearer token → `CurrentUser` (полный для admin/hr, урезанный для owner — как `/me`).
* Токен — случайный, в Redis `sed:session:{token}` со значением JSON + TTL `SESSION_TTL_MINUTES` (дефолт 20).
* `deps.get_current_user`: если `AUTH_MOCK_ENABLED=true` (env, дефолт TRUE для офлайн-тестов) — старый мок-путь
  по X-Mock-*; иначе — Bearer-токен. На ВМ в `.env` выставить `AUTH_MOCK_ENABLED=false`.
* Роль (`_detect_role`) и разрешенные группы — из `settings.admin_groups/hr_groups/ALLOWED_AD_GROUPS` (env).
  На ВМ дописать: `ALLOWED_AD_GROUPS=SED_HR,SED_ADMINS,SED_STEP_EXEC`, `ADMIN_GROUPS=SED_ADMINS`, `HR_GROUPS=SED_HR`.
* Фронт: экран логина до загрузки каркаса; после логина — `SedLayout` с ролью из сессии, без селектора ролей;
  кнопка «Выйти» (очистка токена). Данные таблиц остаются моковыми (реальные эндпоинты — позже),
  но ПДн-обрезка владельца — как в моке.

## Волна A — параллельно, 3 агента

### A1 — ldap3-шлюз (скил `ad-reader`; владеет `requirements.txt`)
* В `api/app/ad_reader.py`: реализовать `Ldap3Gateway` (по контракту `LdapGateway`): bind RO-учеткой,
  `search_user_by_sam` (фильтр `(sAMAccountName=...)`, base `AD_BASE_DN`, SUBTREE), `search_user_by_dn`,
  `bind_user(dn, password)` для проверки пароля пользователя. ldap3 (`Server`, `Connection`, TLS).
  Флаг `AD_TLS_VALIDATE` (дефолт false, внутренний ЦС — пометка «усилить корневым CA»).
  В `AdReaderSettings` добавить `reader_secret` (из env). Только чтение, `AD_WRITE_ENABLED=false`.
* `api/requirements.txt`: раскомментировать `ldap3>=2.9`.
* Тесты: `api/tests/test_ad_gateway.py` — фейковый ldap3-модуль (инъекция), без сети: bind/search/parse.
* Не трогать: deps.py, main.py, config.py, auth.py (чужое), frontend. ПДн — вымышленные.

### A2 — auth-сервис (скил `fastapi-sed`; владеет `config.py`, `deps.py`, `main.py`)
* `api/app/auth.py`: `AuthService` (Protocol) + `LdapAuthService` (AdReader + SessionStore) + `RedisSessionStore`
  (Redis `from_url(REDIS_URL)`, ключи `sed:session:*`, TTL из настроек) + `login()` / `me(token)` / `logout(token)`.
  При ошибке AD — `503`, при неверных данных/нет групп — `401`. Токен — `secrets.token_urlsafe`.
* `config.py`: `SESSION_TTL_MINUTES` (дефолт 20), `AUTH_MOCK_ENABLED` (дефолт true, пометка «на ВМ false»).
* `deps.py`: реальный путь по Bearer + мок-путь за флагом; `_detect_role` оставить.
* `main.py`: `include_router(auth)` → `POST /auth/login`, `GET /auth/me`.
* Тесты: `api/tests/test_auth_login.py` — мок AuthService (in-memory), 200/401/403/503, /auth/me урезанная.
* Не трогать: ad_reader.py (кроме импорта протоколов), requirements.txt (чужое), frontend.

### A3 — экран логина (скил `react-sed`)
* `frontend/src/auth-client.tsx`: `login(login,password)` → POST `/api/auth/login` (Bearer далее в заголовках),
  `logout()`, `me()` → GET `/api/auth/me`; токен в localStorage (`sed_token`).
* `main.tsx`: если токена нет — `LoginScreen` (форма, ошибка 401/503), иначе `SedLayout` с ролью из `/auth/me`.
* `layout.tsx`: убрать селектор ролей, роль из сессии; кнопка «Выйти»; вкладка «Настройки» — только admin.
* Тесты: `frontend/src/auth-client.test.tsx` + правки `layout.test.tsx` (роль из props, без селектора).
* Не трогать: api/**, db/**, только frontend.

## Волна B — ревью (reviewer, чек-лист `.opencode/agent/reviewer.md`) + доработка.
## Волна C — стенд: `.env` на ВМ (флаги/группы), пересборка образа api, рестарт, проверка
`POST /auth/login` учеткой (логин — доменный, пароль даст человек), `/auth/me` по токену, фронт с экраном логина.

Запреты общие: коммитов/пушей нет (коммитит человек), секреты — только в `.env` на ВМ, вне проекта не выходить,
временное — `./temp/`, полный `compose up` без надобности не делать, `sed.service` не трогать.
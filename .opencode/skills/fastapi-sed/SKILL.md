---
name: fastapi-sed
description: Use when editing FastAPI, SQLAlchemy, LDAP bind, 1C OData in SED. Triggers on api/, backend, auth, employees, requests, steps, documents endpoints.
---

# FastAPI SED Backend

Бэкенд СЭД: Python 3.12 + FastAPI + SQLAlchemy, stateless, за Nginx.

## Железные правила

- 1С и AD — только чтение (GET/bind). Единственное исключение — `AdLifecycle.disableAccount()` за флагом `AD_WRITE_ENABLED=false`; в MVP флаг всегда false, вызов запрещен.
- Группы доступа (`SED_HR/SED_ADMINS/SED_STEP_EXEC` в OU `OSWDOCS`; разбивка исполнителей позже), OU, предприятия/базы, TTL — только из `settings`/env. Хардкод запрещен.
- При расхождении данных истина — 1С. Стыковка 1С↔AD по полному ФИО: автосвязка
  (`ad_sync`), дубли ФИО в 1С разводятся по должности/службе из регистра кадровых данных,
  остальное — расхождения и подтверждение (`link_1c_ad`, `link_1c_ad/discrepancies/confirm`,
  `requests/route/link-employee`). Увольнение определяет 1С (`dismissal_date`), не AD.
- Ролевая обрезка ответов: ОК — полная карточка, владелец шага — только свои задачи без ПДн (без отпуска), остальные — 403/пусто. `fio`/`department` заявки — исключение, видны всем (решение человека 2026-10-14); локальный справочник сотрудников в объёме `sam`/`fio`/`department`/`position` — исключение для подбора заместителя, видны всем вошедшим (решение человека 2026-10-15).
- Минимальный дифф: править только нужный эндпоинт, контракты без согласования не менять.

## Ключевые модули

- `auth.py` — LDAPS bind, проверка `memberOf` из настроек, пароли не хранить.
- `substitutes.py`/`substitute_store.py` — справочник заместителей (миграция 0023).
  Два контура в одном справочнике:
  - **Админский** — `GET/POST/PUT/DELETE /settings/substitutes` под блоком
    `settings_substitutes`, аудит `substitutes.create/update/delete` без логинов и ФИО.
  - **Личный** (решение человека 2026-10-15, БЕЗ блоков прав — любой вошедший):
    `GET/POST /substitutes/my`, `PUT/DELETE /substitutes/my/{id}`. Гейт один —
    принадлежность записи (`_is_my_record`/`_own_record`): **чужая запись отвечает 404**
    наравне с отсутствующей (не 403/409 — иначе по коду ответа видно, что запись с
    таким id существует), проверка ДО `store.update`/`store.delete`. `principal_sam`
    в теле личной ручки не принимается (**422**, `_reject_principal`) — логин оригинала
    всегда из сессии; «заместитель = сам пользователь» — **422** (`_reject_self_deputy`,
    до похода в хранилище); 409 — пара занята; 503 — хранилище недоступно. Аудит личных
    операций — те же события, в `details` только срок и признак писем.
  - **Подбор заместителя** — `GET /substitutes/candidates?q=&page=&page_size=`: любая
    вошедшая сессия, без блока прав, источник — ЛОКАЛЬНАЯ таблица `employees`
    (`employee_sync.search_linked`: непустой `ad_sam`, совпадение по `fio` ИЛИ `ad_sam`
    без регистра, увольненные пропускаются), ответ `{items, total, page, page_size}` с
    `sam`/`fio`/`department`/`position`; себя самого исключает, `q` короче 2 символов —
    **422**, аудита нет. Это **новая видимость ФИО сотрудников всем вошедшим**
    (решение человека 2026-10-15); `/api/ad/search` под блоком `employee_directory`
    остался как был. Расширять видимость ПДн сверх этого решения нельзя.
  Замещение применяется в `requests.py` (`_DeputyResolver`) —
  это НЕ подмена адресата: шаг остаётся у оригинала, отметка пишется от логина заместителя,
  гейт `step_decide` замещение не обходит. Действует на каждом шаге, включая ГРУППОВОЙ
  (оба режима): `_step_deputy_owned` → `_DeputyResolver.covers_member_of`/`_group_sams` по
  составу группы-владельца из кэша `ad_group_members` (один `load` на группу за вызов
  ответа; кэш недоступен/не синхронизирован — пусто, fail-soft), письмо «назначена»
  дополняется `notify_deputies_of_group`. `documents._can_view` и `attachments._can_access`
  зовут тот же `requests._is_step_viewer` с резолвером — бланк и вложения заместителю
  доступны так же, как замещаемому; печать остаётся за блоком `print_blank`.
- `employees.py` — `GET /employees?enterprise&q` (локальная таблица, `page`/`page_size`/`total`,
  уволенные отсекаются), `POST /employees/sync`, `POST /employees/hr-sync` (проход по регистру
  кадровых данных), `GET /employees/card`.
- `routing.py`/`routing_store.py` — справочники маршрута (бланки `blanks`/`blank_steps`, шаг бланка
  самостоятельный — свой текст и свой исполнитель, этап не нужен) и чистый подбор профиля по службе /
  шагов бланка (`pick_blank_steps`).
- `requests.py` — `GET /requests/route/blanks` (селект бланка для ОК), `POST /requests/route/preview`
  (бланк/профиль, этапы, `notice`, `link_state`) и `POST /requests/route/link-employee`
  (подтверждение связи при импорте); создание заявки принимает `blank_id`, снятие шага бланка — по
  номеру (`dismissed_step_orders`), в заявку пишется снимок бланка (в т.ч. `blank_header_html`/
  `blank_footer_lines`), автоподстановка по службе — за настройкой `blank_autopick`; бланк не
  выбран при `blank_autopick=off` — одинаковый 422 в предпросмотре и создании с перечнем вариантов.
- Границы вне роутеров создаём через `dependency_overrides`-совместимые фабрики — прямой
  вызов `get_*_store(get_settings())` в хелпере уводит офлайн-прогоны в боевую БД.
- `documents.py`/`docs.py` — печать бланка из данных заявки и снимка (шапка/подвал/текст шага) с
  плейсхолдерами (`{fio}`…`{manager}`), значения экранируются, неизвестные остаются текстом.
- `requests.py, steps.py` — заявки, `route_origin=template|custom`, отметку ставит любой из `owner_group`.
- `onec_client.py` — клиент per-base (см. скил `onec-multibase`), таймаут 5с.
- `ad_reader.py` — см. скил `ad-reader`.

## Приемка правки

Эндпоинт отвечает по контракту README п.4, нет в группе → 403, лишний SELECT N+1 отсутствует, `audit_log` пишется.

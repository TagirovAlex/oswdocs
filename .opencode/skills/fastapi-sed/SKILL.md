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
- Ролевая обрезка ответов: ОК — полная карточка, владелец шага — только свои задачи без ПДн (без отпуска), остальные — 403/пусто.
- Минимальный дифф: править только нужный эндпоинт, контракты без согласования не менять.

## Ключевые модули

- `auth.py` — LDAPS bind, проверка `memberOf` из настроек, пароли не хранить.
- `employees.py` — `GET /employees?enterprise&q` (локальная таблица, `page`/`page_size`/`total`,
  уволенные отсекаются), `POST /employees/sync`, `POST /employees/hr-sync` (проход по регистру
  кадровых данных), `GET /employees/card`.
- `routing.py`/`routing_store.py` — справочники маршрута и чистый подбор профиля по службе.
- `requests.py` — `POST /requests/route/preview` (профиль/этапы/`notice`/`link_state`) и
  `POST /requests/route/link-employee` (подтверждение связи при импорте).
- Границы вне роутеров создаём через `dependency_overrides`-совместимые фабрики — прямой
  вызов `get_*_store(get_settings())` в хелпере уводит офлайн-прогоны в боевую БД.
- `requests.py, steps.py` — заявки, `route_origin=template|custom`, отметку ставит любой из `owner_group`.
- `onec_client.py` — клиент per-base (см. скил `onec-multibase`), таймаут 5с.
- `ad_reader.py` — см. скил `ad-reader`.

## Приемка правки

Эндпоинт отвечает по контракту README п.4, нет в группе → 403, лишний SELECT N+1 отсутствует, `audit_log` пишется.

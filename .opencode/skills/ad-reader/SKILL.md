---
name: ad-reader
description: Use when resolving AD manager, memberOf, LDAPS in SED. Triggers on manager, memberOf, LDAP bind, link_1c_ad, owner groups.
---

# AD Reader (только чтение)

Доступ — LDAPS `:636`, сервисная RO-учетка ИТ (DN — из настроек). Запись в AD запрещена.

## Железные правила

- Читаем: `sAMAccountName, displayName, manager, department/title, memberOf, mail, userAccountControl`.
- Стыковка 1С↔AD — только по полному ФИО (других ключей в AD нет) + ручное подтверждение ОК в `link_1c_ad`. Дубли/переименования — в очередь ручной сверки, автосклейки нет.
- При расхождении истина — 1С; оба значения показываем в снапшоте.
- Руководитель по умолчанию — `manager` из AD; замена — только в ручном конструкторе разрешенной группой.
- Группы СЭД живут в `OSWDOCS`: `SED_HR` (ОК), `SED_ADMINS` (настройки), `SED_STEP_EXEC` (единая группа исполнителей на старте; разбивка `SED_STEP_*` по ролям — позже, только настройками); отметку шага ставит любой член `owner_group`.
- Кэш `manager/memberOf` в Redis 4–8 ч + кнопка «обновить»; `mail` — только для уведомлений, в бланк не печатать.

## Приемка правки

Нет в группе → 403, manager-цепочка резолвится рекурсивно, падения AD не кладут API (таймаут + понятная ошибка).

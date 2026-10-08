---
name: qa-sed
description: Use when running negative tests, security and rate-limit checks, audit_log verification in SED. Triggers on 403, duplicates, TTL expiry, scan limits, rate-limit, audit_log completeness, 1C/AD read-only checks.
---

# QA / Security SED

Контроль качества и безопасности Фазы 6 (README п.6), оптимизация под НФТ (README п.5).

## Железные правила

- Негативные сценарии: 403 вне групп, дубли ФИО (в т.ч. «связь не подтверждена» — 422 с
  действием, не «нет в AD»), несколько записей AD с одним ФИО (пакетное подтверждение не
  снимает неоднозначность), просрочка TTL (`approval_ttl_days`), лимиты скана
  (`scan_retention_days/scan_max_mb`), rate-limit логина.
- «Нет записи в 1С/AD, нет delete»: вызовов записи нет, `AD_WRITE_ENABLED=false` не тронут,
  `audit_log` полный (append-only, без UPDATE/DELETE). Автосвязка и подтверждение связок пишут
  ТОЛЬКО в локальную БД (`link_1c_ad`, `users`, `employee_base_map`, `employees`,
  `link_discrepancies`) — проверять журналом/снапшотом БД, не обращениями к AD/1С.
- Кадровые данные: уволенные (`dismissal_date <= сегодня`) не выдаются в поиске и не участвуют
  в сопоставлении; метки регламентов (`hr_dismissals_synced_at`, `ad_links_synced_at`) пишутся
  только после успешного прохода.
- ПДн по ролям: отпуск и чувствительные поля — только ОК, владелец — урезанная карточка, поиск — по своим правам (README п.1).
- НФТ: TLS 1.2+, HSTS, сессии 15–20 мин, RPO 24ч/RTO 4ч (уточнить к тесту), `pg_dump` + снапшот.

## Приемка правки

Чек-лист Фазы 6 закрыт, негативные тесты зелёные, `audit_log` полный.
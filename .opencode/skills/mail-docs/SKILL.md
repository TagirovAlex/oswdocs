---
name: mail-docs
description: Use when editing DOCX/PDF bypass sheets and Exchange mail templates in SED. Triggers on blank, docx, PDF, QR, mail_templates, SMTP.
---

# Бланки и письма

Генерация — в `worker`, не в `api`. Печать — обычный принтер в ОК.

## Железные правила

- Бланки `doc_templates` — по службе+МОЛ, в корп. виде (образцы — к тесту). Движок: `python-docx-template (Jinja) → PDF через LibreOffice headless`. QR с `request_id` обязателен. Перепечатка — новая версия (`documents v1/v2`), старые не удаляются.
- Где подпись строго требуется — разметка в шаблоне шага/бланка (`require_paper_signature`), включается без кода.
- Письма `mail_templates` (Jinja HTML, корп. вид): «есть документ» + ссылка на заявку. Отправка через внутренний Exchange SMTP на `mail` из AD. Очередь `mail_queue` с ретраями, события v1: `назначена → напоминание → эскалация → закрыта/возврат`.
- Скан заявления опционален: лимиты `scan_max_mb`, хранение `scan_retention_days` — из настроек. ПДн (отпуск и др.) в бланки/письма не включать.

## Приемка правки

DOCX и PDF собираются из одного шаблона, QR сканируется в заявку, письмо уходит с корп. шапкой, очередь переживает рестарт worker.

# Layout хоста: /srv/sed

Корень развертывания на ВМ — `/srv/sed` (см. `deploy/sed.service`,
`WorkingDirectory`; при установке в другой каталог, например `/opt/sed`,
путь меняется в юните — сами подкаталоги и их назначение сохраняются).
В корне лежат `docker-compose.yml`, `docker-compose.prod-db-external.yml`, `.env`.
Ниже — только данные и сертификаты.

| Каталог хоста        | Назначение | Соответствие volumes в compose |
|----------------------|------------|--------------------------------|
| `/srv/sed/pgdata`    | Хост-конвенция для данных Postgres (PGDATA). В compose Фазы 0 используется именованный volume `pgdata` (`pgdata:/var/lib/postgresql/data` в сервисе `db`, без bind-опций — данными управляет Docker, переживают пересоздание контейнеров). Привязка к этому каталогу через `driver_opts: type=none o=bind device=/srv/sed/pgdata` — опционально на ВМ, не в scaffold. Внешнего порта у `db` нет. | именованный volume `pgdata` |
| `/srv/sed/files`     | Хост-конвенция для прикрепленных файлов: сканы заявлений, DOCX/PDF бегунков (`documents`, `attachments`), бланки-шаблоны в `files/templates/` (`office_base.docx`, `line_base.docx` — производные от исходников `office.docx`/`line.docx` в репозитории; в `doc_templates` хранится только имя файла). В compose — именованный volume `files` (`files:/app/files` в `api`/`worker`). | именованный volume `files` |
| `/srv/sed/backups`   | Локальное место выгрузки `pg_dump` перед отправкой в целевое хранилище бэкапов (см. регламент ниже). В compose — именованный volume `backups` (`backups:/app/backups:ro` в `api`/`worker`; в сервисе `db` НЕ монтируется — дамп делает задача приложения, не сам postgres). | именованный volume `backups` в `api`/`worker` |
| `/srv/sed/certs`     | Корпоративные сертификат и ключ для `proxy` (Nginx, `:443`; `:80` — только редирект на `443`). НЕ именованный volume, а host-mount `${CERT_PATH:-/srv/sed/certs}:/etc/nginx/certs:ro` в сервисе `proxy` **только read-only**. Ключ в образы не копировать. | host-mount `${CERT_PATH}:/etc/nginx/certs:ro`, не volume |

> Примечание: в scaffold Фазы 0 используются именованные volumes
> `pgdata, files, backups` (без bind-опций) + отдельный host-mount certs.
> Каталоги `/srv/sed/{pgdata,files,backups,certs}` — конвенция размещения на ВМ
> (bind через driver_opts — опционально при установке, не в scaffold).
> Смысловое соответствие: volumes `pgdata, files, backups` ↔ данные БД/файлов/бэкапов;
> host-mount certs ↔ `/srv/sed/certs`.
> Разъезд на 2 ВМ — только через `docker-compose.prod-db-external.yml`
> (вынос `db` + смена `DATABASE_URL`), код не трогать.

## Права и владельцы

| Каталог              | Владелец:группа | Права | Комментарий |
|----------------------|-----------------|-------|-------------|
| `/srv/sed`           | `root:docker`   | `750` | читать/писать — root и члены `docker` |
| `/srv/sed/pgdata`    | `root:docker`   | `750` | фактически пишет пользователь postgres внутри контейнера `db` через bind-mount |
| `/srv/sed/files`     | `root:docker`   | `750` | пишут `api`/`worker` |
| `/srv/sed/backups`   | `root:docker`   | `750` | пишет задача дампа (`db`/`api`); чтение для выгрузки наружу |
| `/srv/sed/certs`     | `root:docker`   | `700` | закрытый каталог: сертификат + приватный ключ; mount в `proxy` read-only |

## Что бэкапится

1. **БД** — логический дамп `pg_dump` (каталог `backups` как staging, затем —
   в целевое хранилище по регламенту ИТ). Дамп покрывает все таблицы,
   включая `settings`, `mail_templates`, `audit_log`.
2. **`files`** — целиком (сканы, DOCX/PDF, бланки в `templates/`). Без них дамп БД неполон
   (файлы связаны с `documents`/`attachments`).
3. **`backups`** — сами выгрузки `pg_dump` хранятся/ротируются по регламенту.
4. **`pgdata`** — дополнительно покрывается снапшотом ВМ/тома (физический уровень).
5. **`certs` — не бэкапится** в общем порядке (приватный ключ); восстановление —
   повторной выдачей сертификата через ИТ.

Целевые RPO/RTO: `RPO 24ч / RTO 4ч` (README п.5, уточнить к тесту).
Регламент (периодичность `pg_dump`, ротация, место хранения, снапшоты) —
см. `deploy/ЧЕК-ЛИСТ_ИТ.md`.

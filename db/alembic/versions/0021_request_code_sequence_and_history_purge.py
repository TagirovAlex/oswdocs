# Миграция 0021: последовательность бизнес-номеров заявок и зачистка истории
# удалённой заявки (решение человека 2026-10-12, тестовый стенд).
#
# Проблема, которую чинит миграция (две связанные):
#
# 1. Номер заявки выдавался как MAX(числовая часть code) + 1 по ЖИВЫМ заявкам
#    (DbRequestsStore.next_id, _NEXT_CODE_SQL). Пустой список заявок — счётчик
#    откатывался на REQ-0001, и новый REQ-0001 наследовал журнал аудита от
#    старого, давно удалённого REQ-0001: в карточке показывалась «история всех
#    заявок, которых уже нет». audit_log удалить было нельзя (append-only).
#
# 2. При удалении заявки (DELETE /requests/{id}, только админ) её записи в
#    audit_log оставались навсегда: у журнала нет FK на dismissal_requests, а
#    триггер запрещал DELETE. По решению человека на тестовом стенде удаление
#    заявки чистит и её историю.
#
# Вверх:
#   • последовательность request_code_seq — номера выдаёт nextval(), они не
#     откатываются при удалении заявок и не переиспользуются никогда;
#   • сид последовательности выше максимума по ВСЕМ трём источникам номеров
#     (живые заявки, журнал аудита, комментарии) — иначе номера удалённых
#     заявок, чей след остался в audit_log/request_comments, выдались бы заново;
#   • триггер audit_log_no_update_delete: UPDATE по-прежнему запрещён для всех,
#     DELETE разрешён ТОЛЬКО для истории заявки, которой уже нет в
#     dismissal_requests (сущности request/document/attachment хранят в
#     entity_id бизнес-номер заявки). Записи остальных сущностей (user,
#     settings, employee, ...) по-прежнему неудаляемы.
#
# Вниз: возвращается строгий append-only (UPDATE/DELETE запрещены для всех) и
# последовательность удаляется. Нумерация после отката — снова по MAX(code), но
# только если приложение тоже возвращено на код 0020 (nextval без
# последовательности падает 503).
#
# ГЕЙТ ВЫКЛАДКИ (проверить на стенде до деплоя):
#   • из неприменённых мер НФТ числится `REVOKE UPDATE, DELETE ON audit_log FROM
#     <app_role>` (локальная отметка в temp/BLOCKERS.md, в релиз не входит). Если
#     ревок применён и роль ≠ владелец таблицы, DELETE из DbRequestsStore.delete
#     даст InsufficientPrivilege, и удаление заявки будет отдавать 503 (одна
#     транзакция — откатится и сама заявка). Тогда снять надо ТОЛЬКО DELETE:
#     `GRANT DELETE ON audit_log TO <app_role>` (роль — из DATABASE_URL, не
#     хардкодить), UPDATE оставить отозванным.
#   • файл миграции обязан попасть в тарбол выкладки (DEPLOY.md, п. «Бэкенд»):
#     без него `alembic upgrade head` — no-op, и новый код будет отдавать 503
#     на создании заявки.
"""последовательность номеров заявок и зачистка истории удалённой заявки"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Последовательность номеров заявок. BIGINT + лента без кеша: выдача
    # номера идёт при каждом создании заявки, а MAX(code) по таблице на
    # тестовом стенде ещё и откатывался при очистке списка.
    op.execute("CREATE SEQUENCE IF NOT EXISTS request_code_seq AS BIGINT")
    # Старт выше всего, что уже использовано как номер заявки: живые заявки,
    # события журнала и комментарии (у них нет FK, след переживает удаление).
    # setval(..., m, m > 0): при m = 0 первый nextval даст 1, иначе — m + 1.
    op.execute(
        """
        SELECT setval(
          'request_code_seq',
          GREATEST(1, m),
          m > 0
        )
        FROM (
          SELECT GREATEST(
            COALESCE((SELECT MAX(SUBSTRING(code, 5)::bigint)
                        FROM dismissal_requests
                      WHERE code ~ '^REQ-[0-9]+$'), 0),
            COALESCE((SELECT MAX(SUBSTRING(entity_id, 5)::bigint)
                        FROM audit_log
                      WHERE entity IN ('request', 'document', 'attachment')
                        AND entity_id ~ '^REQ-[0-9]+$'), 0),
            COALESCE((SELECT MAX(SUBSTRING(request_id, 5)::bigint)
                        FROM request_comments
                      WHERE request_id ~ '^REQ-[0-9]+$'), 0)
          ) AS m
        ) AS src
        """
    )

    # Триггер журнала аудита. UPDATE запрещён всем — как и раньше. DELETE
    # пропускаем только для истории заявки, которой в dismissal_requests уже
    # нет: заявку удалили в этой же транзакции, и её след надо убрать.
    # Для живой заявки и для записей других сущностей DELETE по-прежнему падает.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_no_modify()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'audit_log append-only: изменение запрещено';
            RETURN NULL;
          END IF;
          -- Только след ЗАЯВКИ по её бизнес-номеру и только если такой заявки
          -- в dismissal_requests уже нет. Проверка формата обязательна: без неё
          -- под правило попадали бы записи с пустым/NULL/опечаточным
          -- entity_id, а это не след заявки и удалять его нельзя.
          IF OLD.entity IN ('request', 'document', 'attachment')
             AND OLD.entity_id ~ '^REQ-[0-9]+$'
             AND NOT EXISTS (
               SELECT 1 FROM dismissal_requests d WHERE d.code = OLD.entity_id
             )
          THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION
            'audit_log: удаление записи возможно только для истории удалённой заявки';
          RETURN NULL;
        END $$
        """
    )


def downgrade() -> None:
    # Строгий append-only: обновление и удаление запрещены для всех записей.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_no_modify()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log append-only: изменение и удаление запрещены';
          RETURN NULL;
        END $$
        """
    )
    # ВНИМАНИЕ: откат требует и возврата кода 0020. Пока приложение на коде
    # 0021 (DbRequestsStore.next_id зовёт nextval), после DROP SEQUENCE создание
    # заявки будет падать 503. Порядок отката: сначала код 0020, потом миграция.
    op.execute("DROP SEQUENCE IF EXISTS request_code_seq")

# Миграция 0023: справочник заместителей — делегирование отметок по шагам
# согласования (решение человека 2026-10-14; план волны — resume/…_заместители_замещение.md).
#
# Что за таблица:
#   substitutes — «кому кого замещает» на интервал дат. Заместитель НЕ становится
#   адресатом шага: шаг остаётся назначенным оригиналу, но заместитель может
#   поставить согласование или отказ за него, если у него есть блок прав
#   «Отметки по шагам» (step_decide). Отметка пишется от логина заместителя —
#   в истории шага и в аудите видно, кто именно решил.
#   Данные локальные (не AD, не 1С): заводит администратор. Идентификаторы людей
#   — sAMAccountName, тот же ключ, что и в employees/ad_sam, поэтому резолвер
#   заместителей не зависит от ФИО (ФИО меняется, логин — нет).
#
# Ключевые решения по схеме:
#   • UNIQUE (principal_sam, deputy_sam) — одна запись на пару. Несколько
#     сроков подряд одной пары удобнее заводить правкой срока, чем дублями:
#     иначе резолвер «действующих записей» получил бы две строки на одну пару
#     и права на отметку задваивались/неопределённо пересекались.
#   • valid_from обязателен, valid_to NULL — «действует, пока не отменят».
#     Явный срок нужен, чтобы отпуск/замещение не нужно было отменять задним
#     числом иначе, чем при пустой дате.
#   • notify по умолчанию TRUE: копия письма «назначена» заместителю — полезное
#     поведение, молчаливый отказ от уведомления должен быть явным решением админа.
#   • note — свободный текст админа (причина: «на период отпуска»), поэтому
#     NOT NULL DEFAULT '' — пустая строка вместо NULL, чтобы форма и API не
#     различали «не заполнено» и «неизвестно».
#   • created_by/updated_at — журнал правки; сам журнал аудита ведётся в audit_log
#     на уровне API (api/app/substitutes.py), здесь только «кто завёл/когда»,
#     по образцу blanks/link_discrepancies.
#
# Индексы: обычные btree по обоим логинам — резолв идёт в обе стороны
# («у этого сотрудника есть заместители?» для шага и «этот сотрудник замещает
# кого?» для проверки прав), а пара уникальна, поэтому составной индекс по
# principal_sam уже покрывает вторую колонку — отдельный индекс по deputy_sam
# нужен для поиска в обратную сторону.
#
# Права: справочник заместителей — админская функция (решение человека
# 2026-10-14), поэтому заводится 14-й блок функций settings_substitutes
# (sort_order 140 — сразу за sed_admin_panel 130) и выдаётся шаблонам sed_admin
# и admin. Строки берутся по кодам справочников, а не по id: сиды идемпотентны
# и не зависят от порядка выдачи serial. ГЛАВНОЕ — перед ON CONFLICT обязан
# стоять WHERE: иначе PostgreSQL читает «ON CONFLICT» как продолжение
# «JOIN ... ON» и падает на разборе (тот же грабли, что в 0020).
#
# COMMENT ON TABLE/COLUMN не добавляем: в проекте их нет (миграции 0001-0022).
#
# Вниз: сначала снимаем выдачу блока из шаблонов, затем удаляем сам блок
# (блок в использовании удалить нельзя — в perm_template_blocks стоит
# ON DELETE RESTRICT), и только потом DROP TABLE substitutes: так откат идёт
# от «справочников прав» к «данным», и ни на одном шаге не остаётся блока,
# на который кто-то ссылается. Без CASCADE: внешних ссылок на substitutes нет.
"""справочник заместителей и блок прав на него"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE substitutes (
          id SERIAL PRIMARY KEY,
          -- Логины (sAMAccountName), а не ФИО: ФИО меняется, логин — нет.
          principal_sam TEXT NOT NULL,
          deputy_sam TEXT NOT NULL,
          valid_from DATE NOT NULL,
          -- NULL — замещение действует, пока его не отменят.
          valid_to DATE NULL,
          -- Слать заместителю копию письма «назначена» по шагам оригинала.
          notify BOOLEAN NOT NULL DEFAULT TRUE,
          note TEXT NOT NULL DEFAULT '',
          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NULL,
          UNIQUE (principal_sam, deputy_sam)
        )
        """
    )
    # Резолв замещения идёт по обоим логинам (см. шапку), поэтому индексов два.
    op.execute(
        "CREATE INDEX substitutes_principal_idx ON substitutes (principal_sam)"
    )
    op.execute(
        "CREATE INDEX substitutes_deputy_idx ON substitutes (deputy_sam)"
    )

    # 14-й блок прав доступа. sort_order 140 — сразу за sed_admin_panel (130),
    # между соседями оставляем шаг 10 (как в 0020).
    op.execute(
        """
        INSERT INTO perm_blocks (code, title, sort_order)
        VALUES ('settings_substitutes', 'Настройки: заместители', 140)
        ON CONFLICT (code) DO NOTHING
        """
    )

    # Выдача блока шаблонам-администраторам по кодам справочников (не по id) —
    # тот же приём, что в 0020, вместе с обязательным WHERE true перед
    # ON CONFLICT: без него PostgreSQL читает ON CONFLICT как продолжение
    # JOIN ... ON и миграция падает на разборе.
    op.execute(
        """
        INSERT INTO perm_template_blocks (template_id, block_id)
        SELECT template_id, block_id
        FROM (
          SELECT t.id AS template_id, b.id AS block_id
          FROM (VALUES
          ('sed_admin', 'settings_substitutes'),
          ('admin', 'settings_substitutes')
        ) AS m(template_code, block_code)
        JOIN perm_templates t ON t.code = m.template_code
        JOIN perm_blocks b ON b.code = m.block_code
        ) AS src
        WHERE true
        ON CONFLICT (template_id, block_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Порядок: права → таблица. Сначала снимаем выдачу блока шаблонам, иначе
    # удалить блок нельзя (в perm_template_blocks он в использовании —
    # ON DELETE RESTRICT), и только затем убираем блок и таблицу.
    op.execute(
        """
        DELETE FROM perm_template_blocks
        USING perm_blocks b
        WHERE perm_template_blocks.block_id = b.id
          AND b.code = 'settings_substitutes'
        """
    )
    op.execute("DELETE FROM perm_blocks WHERE code = 'settings_substitutes'")
    # Без CASCADE: внешних ссылок на таблицу нет, а каскад здесь скрыл бы
    # ошибку, если такая ссылка появится.
    op.execute("DROP TABLE IF EXISTS substitutes")

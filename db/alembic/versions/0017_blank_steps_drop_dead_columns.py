# Миграция 0017: чистка мёртвых колонок шага бланка (решение человека 2026-10-09).
#
# Шаг бланка стал самостоятельной сущностью (миграция 0014): свой текст
# (title/stage_lines), свой исполнитель (executor_kind + assignees/owner_group),
# свои optional/require_comment. Колонки прежней модели остались в таблице
# мёртвыми — код их не читает и не пишет:
#   stage_id                  — ссылка на этап справочника approval_stages;
#                              новая вставка (DbRoutingStore._INSERT_BLANK_STEP)
#                              его не упоминает, с 0014 колонка nullable и
#                              заполняется NULL. FK на approval_stages и UNIQUE
#                              (blank_id, stage_id) из 0012 снимаются вместе с
#                              колонкой — отдельный DROP CONSTRAINT не нужен
#                              (как FK/CHECK в 0008/0013).
#   optional_override         — переопределение optional этапа прежней модели;
#   require_comment_override  — переопределение require_comment этапа прежней
#                              модели. У шага бланка свои optional и
#                              require_comment (0014); схема BlankStepIn этих
#                              полей не принимает, выборка шагов бланка их не
#                              отдаёт, а запись их не пишет.
#
# Данные: у steps может остаться содержимое, собранное человеком уже после 0014
# (оно живое и не теряет смысла — новые шаги хранятся в своих колонках). Но
# прежних строк со ссылкой на этап быть не может: 0014 очищала содержимое
# blank_steps целиком, а всё, что записано после, писалось без stage_id.
# Удаляются только сами колонки, строки остаются; если в стенде всё же есть
# шаги, заведённые до 0014, их stage_id был наследием прежней модели, которое
# человек пересобирает заново через админку.
"""удаление мёртвых колонок шага бланка (stage_id/optional_override/require_comment_override)"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Порядок не важен: ограничения (FK на approval_stages и UNIQUE
    # (blank_id, stage_id) из 0012) уходят вместе со своими колонками.
    for column in (
        "stage_id",
        "optional_override",
        "require_comment_override",
    ):
        op.execute("ALTER TABLE blank_steps DROP COLUMN IF EXISTS %s" % column)


def downgrade() -> None:
    # Колонки возвращаем в прежнем виде (как в 0014): без этого повторный
    # upgrade head после отката падал бы с «column ... already exists».
    op.execute(
        "ALTER TABLE blank_steps "
        "ADD COLUMN IF NOT EXISTS stage_id INTEGER NULL"
    )
    op.execute(
        "ALTER TABLE blank_steps ADD COLUMN IF NOT EXISTS optional_override BOOLEAN NULL"
    )
    op.execute(
        "ALTER TABLE blank_steps "
        "ADD COLUMN IF NOT EXISTS require_comment_override BOOLEAN NULL"
    )
    # Значения прежних шагов не восстанавливаются: optional_override/
    # require_comment_override всегда писались NULL (переопределений у шага
    # бланка не было), а stage_id у шага, заведённого после 0014, не
    # существует — этап у самостоятельного шага нет.
    # Шаги без ссылки на этап в прежней модели выразить нельзя (stage_id был
    # NOT NULL), поэтому перед возвратом обязательности они удаляются — так же,
    # как в downgrade 0014. Содержимое бланка человек собирает заново.
    op.execute("DELETE FROM blank_steps WHERE stage_id IS NULL")
    op.execute("ALTER TABLE blank_steps ALTER COLUMN stage_id SET NOT NULL")
    # Ссылка на этап прежней модели была RESTRICT (этап из бланков не удаляем).
    op.execute(
        "ALTER TABLE blank_steps ADD CONSTRAINT blank_steps_stage_id_fkey "
        "FOREIGN KEY (stage_id) REFERENCES approval_stages (id) ON DELETE RESTRICT"
    )
    # Один этап в бланке не повторяется (UNIQUE из 0012); явный DROP CONSTRAINT
    # перед ADD — повторный downgrade не должен спотыкаться о «already exists».
    op.execute(
        "ALTER TABLE blank_steps DROP CONSTRAINT IF EXISTS blank_steps_blank_id_stage_id_key"
    )
    op.execute(
        "ALTER TABLE blank_steps ADD CONSTRAINT blank_steps_blank_id_stage_id_key "
        "UNIQUE (blank_id, stage_id)"
    )
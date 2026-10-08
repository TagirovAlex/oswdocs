# Миграция 0013: несколько ответственных у шага (решение человека 2026-10-06).
#
# У шага может быть один ответственный или несколько. Шаг параллельного блока
# («кто-то один») закрывает отметка любого из ответственных, шаг последовательного
# блока — только все: каждый оставляет свою отметку, шаг ждёт остальных.
#
# blank_steps.approval_mode — режим шага в бланке (справочник): выбирает сотрудник
#   ОК, печать/согласование берут режим из этого снимка. По умолчанию
#   sequential: «все ответственные», как было до разделения режимов.
# request_steps.assignees — снимок логинов ответственных на момент выдачи заявки
#   (jsonb-список; один ответственный — список из одного). assignee при этом
#   заполняется первым из списка: прежний UI, выборки и письма продолжают
#   работать. Для группового шага персональных исполнителей нет — список пуст,
#   действует owner_group (группа AD).
# request_steps.approval_mode — снимок режима: из шага бланка, а для ручного
#   маршрута — из блока (параллельный блок = любой, последовательный = все).
#   NULL — шаг выдан до этой миграции: режим тогда sequential (прежнее поведение).
# request_steps.approvals — собранные отметки [{sam, at, decision, comment}] по
#   каждому ответственному. Только логины (без ФИО): sam — участник процесса,
#   он же в assignee/done_by.
"""несколько ответственных у шага (assignees/approval_mode/approvals)"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Режим шага в бланке: parallel — «кто-то один из ответственных»,
    # sequential — «все». CHECK держит справочник в двух значениях.
    op.execute(
        "ALTER TABLE blank_steps "
        "ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'sequential' "
        "CHECK (approval_mode IN ('sequential', 'parallel'))"
    )
    # Снимок ответственных шага в заявке. DEFAULT '[]' — jsonb-массив, чтобы
    # INSERT без списка (групповой шаг) оставался валидным.
    op.execute(
        "ALTER TABLE request_steps "
        "ADD COLUMN assignees JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    # Снимок режима шага; NULL — режим не задан (шаг выдан до миграции).
    op.execute(
        "ALTER TABLE request_steps "
        "ADD COLUMN approval_mode TEXT NULL "
        "CHECK (approval_mode IN ('sequential', 'parallel'))"
    )
    # Отметки ответственных по шагу (jsonb). Пустой список — отметок нет.
    op.execute(
        "ALTER TABLE request_steps "
        "ADD COLUMN approvals JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    # Порядок обратный upgrade; ограничения CHECK уходят вместе с колонками.
    op.execute("ALTER TABLE request_steps DROP COLUMN IF EXISTS approvals")
    op.execute("ALTER TABLE request_steps DROP COLUMN IF EXISTS approval_mode")
    op.execute("ALTER TABLE request_steps DROP COLUMN IF EXISTS assignees")
    op.execute("ALTER TABLE blank_steps DROP COLUMN IF EXISTS approval_mode")

# Миграция 0011: подробности расхождения в link_discrepancies.
#
# В 0010 колонка detail не появилась (её добавление потерялось при правке
# миграции), поэтому прочие кандидаты AD при дубле ФИО и табельные номера
# остальных карточек 1С этой группы не сохранялись, а UI их показывал пустыми.
#
# Вверх — одна колонка JSONB; вниз — удаление. Значения появятся после
# следующего прохода автосопоставления.
"""link discrepancy detail"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE link_discrepancies ADD COLUMN detail JSONB NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE link_discrepancies DROP COLUMN IF EXISTS detail")
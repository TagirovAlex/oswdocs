# Миграция 0007: справочник должностей для наборов бланков.
# Таблица ad_position_directory — титулы AD из локального кэша состава групп
# (пересборка после каждой синхронизации состава, авто и ручной).
# Вниз — таблица падает.
"""ad positions directory for blanks"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Справочник: одна строка на должность; updated_at — момент пересборки.
    op.execute(
        """
        CREATE TABLE ad_position_directory (
          title TEXT PRIMARY KEY,
          updated_at TIMESTAMPTZ
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ad_position_directory")

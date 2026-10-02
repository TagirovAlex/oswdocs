# Миграция 0004: локальная таблица сотрудников (решение человека).
# Справочник сотрудников синхронизируется из 1С (+AD-связка) в таблицу employees,
# поиск /employees переключается на неё; живой поиск 1С остаётся фолбэком до
# первого синка. Ключ — составной (enterprise, base_code, tab_num), как у связок.
# Вверх — таблица; вниз — DROP TABLE (данные восстановит повторный синк).
"""employee directory (local sync target)"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE employees (
          enterprise TEXT NOT NULL,
          base_code TEXT NOT NULL,
          tab_num TEXT NOT NULL,
          fio TEXT NOT NULL,
          department TEXT NULL,
          position TEXT NULL,
          ad_sam TEXT NULL,
          ad_status TEXT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (enterprise, base_code, tab_num)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS employees")
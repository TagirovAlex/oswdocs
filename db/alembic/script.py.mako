# Шаблон новых ревизий Alembic (комментарии по-русски).
"""${message}"""
from alembic import op

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    # Изменения схемы вверх.
    pass


def downgrade() -> None:
    # Полный откат изменений вверх.
    pass

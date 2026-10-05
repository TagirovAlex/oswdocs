# Миграция 0006: кэш состава групп AD (карточка/конструктор без чтения каталога).
# Вверх — таблица ad_group_members (состав: группа + участник) и
# ad_group_sync_state (метка синка группы: отличает «синхронизирована пустой»
# от «не синхронизирована»). Вниз — обе таблицы падают.
"""ad groups members cache"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Состав группы: одна строка на участника; повторный синк группы
    # перезаписывает состав целиком (DELETE + INSERT в одной транзакции).
    op.execute(
        """
        CREATE TABLE ad_group_members (
          group_name TEXT NOT NULL,
          sam TEXT NOT NULL,
          display_name TEXT NOT NULL DEFAULT '',
          department TEXT,
          title TEXT,
          mail TEXT,
          PRIMARY KEY (group_name, sam)
        )
        """
    )
    # Метка синка группы: есть строка — группа синхронизирована (даже пустой
    # состав); нет строки — состав ни разу не снимался, эндпоинт идёт в AD.
    op.execute(
        """
        CREATE TABLE ad_group_sync_state (
          group_name TEXT PRIMARY KEY,
          synced_at TIMESTAMPTZ,
          member_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ad_group_members")
    op.execute("DROP TABLE IF EXISTS ad_group_sync_state")

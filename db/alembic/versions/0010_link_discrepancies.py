# Миграция 0010: расхождения автосопоставления 1С↔AD.
#
# Проход автосвязки (worker или ручной запуск) складывает сюда то, что связать
# автоматически не удалось, чтобы админ видел список и мог подтвердить связь
# массово, не заходя в каждую карточку сотрудника.
#
# reason — причина расхождения:
#   need_link      — ФИО совпало в AD уникально, связки нет (нужно подтверждение);
#   one_c_duplicate— в 1С несколько карточек с этим ФИО (recommended=true — та,
#                    чья должность/служба совпала с AD);
#   ad_duplicate   — в AD несколько записей с этим ФИО (выбирает человек);
#   not_in_ad      — в AD нет записи с таким ФИО;
#   dismissed      — связка есть, но сотрудник уволен (даты из регистра 1С).
#
# Ключ строки — составной ключ карточки (enterprise|base_code|tab_num), поэтому
# повторный проход обновляет карточку, а не плодит дубли.
"""link discrepancy review"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE link_discrepancies (
          key TEXT PRIMARY KEY,
          enterprise TEXT NOT NULL,
          base_code TEXT NOT NULL,
          tab_num TEXT NOT NULL,
          fio TEXT NOT NULL,
          reason TEXT NOT NULL,
          ad_sam TEXT NULL,
          ad_fio TEXT NULL,
          ad_dept TEXT NULL,
          ad_title TEXT NULL,
          one_c_dept TEXT NULL,
          one_c_position TEXT NULL,
          recommended BOOLEAN NOT NULL DEFAULT false,
          detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          resolved_at TIMESTAMPTZ NULL
        )
        """
    )
    # Выдача расхождений фильтруется по причине и открытости (resolved_at IS NULL).
    op.execute(
        "CREATE INDEX link_discrepancies_reason_idx "
        "ON link_discrepancies (reason, resolved_at)"
    )
    op.execute(
        "CREATE INDEX link_discrepancies_enterprise_idx "
        "ON link_discrepancies (enterprise)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS link_discrepancies_enterprise_idx")
    op.execute("DROP INDEX IF EXISTS link_discrepancies_reason_idx")
    op.execute("DROP TABLE IF EXISTS link_discrepancies")
# Миграция 0016: чистка легаси-ключей настроек, которые остались в таблице
# settings на стенде после снятия файлов-бланков и шаблонов маршрута.
#
# Код их больше не читает (GET /settings их не отдаёт, файлы-бланки удалены,
# вкладка «Шаблоны» снята), поэтому строки в БД — просто мусор: они мешают
# разбору «какие настройки живые». Данные не восстанавливаем при откате.
"""удаление мёртвых ключей настроек (doc_templates/position_sets)"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # doc_templates — пути файлов-бланков .docx (файлы и печать по ним удалены);
    # position_sets — справочник должностей для шаблонов маршрута (снят вместе
    # с templates/position_to_category в 0014).
    op.execute("DELETE FROM settings WHERE key IN ('doc_templates', 'position_sets')")


def downgrade() -> None:
    # Ключи не восстанавливаем: код их не читает, а прежние значения всё равно
    # были бы нерабочими (файлов-бланков и шаблонов маршрута больше нет).
    return None
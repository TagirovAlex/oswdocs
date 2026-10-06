# Миграция 0009: кадровые данные сотрудника в локальном справочнике.
#
# По решению человека увольнение определяется ТОЛЬКО по данным 1С: дата
# увольнения берётся из регистра текущих кадровых данных. Учётку в AD после
# увольнения отключают вручную, поэтому состояние AD критерием не является.
#
# ref_key — Ref_Key записи справочника «Сотрудники»: по нему регистр
# (поле «Сотрудник_Key») сопоставляется со строкой справочника; без него
# дата увольнения не к чему привязать. dismissal_date — дата из регистра
# (пусто = работает); hr_synced_at — когда последний раз обновляли регистр.
#
# Вверх — три колонки и два индекса; вниз — удаление колонок и индексов.
"""employee HR data (dismissal date)"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE employees ADD COLUMN ref_key TEXT NULL")
    op.execute("ALTER TABLE employees ADD COLUMN dismissal_date DATE NULL")
    op.execute("ALTER TABLE employees ADD COLUMN hr_synced_at TIMESTAMPTZ NULL")
    # Сопоставление «регистр → строка справочника» по Ref_Key.
    op.execute(
        "CREATE INDEX employees_ref_key_idx "
        "ON employees (enterprise, base_code, ref_key)"
    )
    # Поиск сотрудников: отсечение уволенных (dismissal_date <= текущего дня).
    op.execute(
        "CREATE INDEX employees_dismissal_idx ON employees (dismissal_date)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS employees_dismissal_idx")
    op.execute("DROP INDEX IF EXISTS employees_ref_key_idx")
    op.execute("ALTER TABLE employees DROP COLUMN IF EXISTS hr_synced_at")
    op.execute("ALTER TABLE employees DROP COLUMN IF EXISTS dismissal_date")
    op.execute("ALTER TABLE employees DROP COLUMN IF EXISTS ref_key")
# Миграция 0005: автонумерация кодов видов документов (блок C).
# Вверх — числовой идентификатор num (BIGSERIAL) в doc_types; код новых видов
# назначается автоматически из той же последовательности (числовая строка).
# Существующие строки и FK dismissal_requests.doc_type_code -> doc_types (code)
# сохраняются: code остаётся текстовым ключом, автонумерация — DEFAULT-значением.
# Вниз — DEFAULT снимается, колонка num падает (вместе с последовательностью).
"""auto-numbering doc types codes (wave C)"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Числовой идентификатор вида: BIGSERIAL создаёт последовательность
    # doc_types_num_seq, существующим строкам num присваивается автоматически.
    op.execute("ALTER TABLE doc_types ADD COLUMN num BIGSERIAL NOT NULL UNIQUE")
    # Код новых видов — из той же последовательности (автонумерация, числовая
    # строка): при INSERT без code значение подставляется дефолтом.
    op.execute(
        "ALTER TABLE doc_types ALTER COLUMN code "
        "SET DEFAULT nextval('doc_types_num_seq')::text"
    )
    # Последовательность на максимум num: строки уже заняли значения, новые
    # INSERT не должны пересекаться с ними.
    op.execute(
        "SELECT setval(pg_get_serial_sequence('doc_types', 'num'), "
        "COALESCE((SELECT MAX(num) FROM doc_types), 1))"
    )


def downgrade() -> None:
    # DEFAULT снимаем до DROP COLUMN: последовательность живёт с колонкой num.
    op.execute("ALTER TABLE doc_types ALTER COLUMN code DROP DEFAULT")
    op.execute("ALTER TABLE doc_types DROP COLUMN num")
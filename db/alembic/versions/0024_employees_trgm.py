# Миграция 0024: индексы нечеткого поиска по локальному справочнику сотрудников
# (подбор заместителя, GET /api/substitutes/candidates).
#
# Зачем. Подбор идёт условием fio ILIKE '%…%' ИЛИ ad_sam ILIKE '%…%' по таблице
# employees, а в ней 23+ тысячи строк; без индекса Postgres читает её целиком на
# каждый запрос. Ручка открыта ЛЮБОМУ вошедшему (блока прав у подбора нет, см.
# api/app/substitutes.py), поэтому полное сканирование справочника сотрудников
# (ФИО — персональные данные) дёргается часто и всеми подряд. GIN по pg_trgm
# обслуживает именно подстроку с wildcard — это то, что делает запрос.
# Имена индексов — по образцу 0001 (users_fio_full_trgm/users_sam_trgm) и
# employees_*, пересечений с уже существующими индексами employees (employees_
# ref_key_idx, employees_dismissal_idx из 0009) нет.
#
# Про pg_trgm: расширение создаёт миграция 0001 (там же первые GIN-индексы),
# БД всегда проходит через неё, поэтому CREATE EXTENSION здесь НЕ повторяется —
# повторный CREATE IF NOT EXISTS только раздул бы миграцию.
#
# Вверх — два GIN-индекса; вниз — их удаление (данные и сама таблица остаются).
"""нечеткий поиск по справочнику сотрудников (подбор заместителя)"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Поиск подбора идёт по ФИО и по логину — два индекса, gin_trgm_ops.
    op.execute(
        "CREATE INDEX IF NOT EXISTS employees_fio_trgm "
        "ON employees USING gin (fio gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS employees_ad_sam_trgm "
        "ON employees USING gin (ad_sam gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS employees_ad_sam_trgm")
    op.execute("DROP INDEX IF EXISTS employees_fio_trgm")

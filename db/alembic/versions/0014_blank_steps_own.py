# Миграция 0014: самостоятельный бланк (решение человека 2026-10-08).
#
# Шаг бланка больше не ссылается на этап справочника: у него свой текст
# (title/stage_lines) и свой исполнитель (executor_kind + assignees/owner_group).
# Состав бланка, шапка и подвал задаёт сотрудник ОК через админку.
#
# blank_steps            — stage_id становится NULLABLE (наследие, новая вставка
#                         пишет NULL) и появляются собственные поля шага:
#                         title/stage_lines/executor_kind/assignees/owner_group/
#                         optional/require_comment. approval_mode (миграция 0013)
#                         остаётся как есть.
#                         optional_override/require_comment_override остаются в БД
#                         как наследие прежней модели (nullable, не используются).
# blanks                 — header_html (шапка, TipTap HTML) и footer_lines (подвал).
# dismissal_requests     — снимок blank_header_html/blank_footer_lines: печать идёт
#                         по снимку выданной заявки, а не по текущему состоянию
#                         справочника (как blank_name/blank_version, миграция 0012).
#
# Перенос данных: содержимое blank_steps и blanks очищается — прежние шаги
# ссылались на этапы и в новой модели смысла не имеют, бланки человек собирает
# заново. Одновременно удаляются легаси-ключи настроек templates и
# position_to_category (вкладка «Шаблоны» удалена целиком).
"""blank steps own text/executors; blank header/footer snapshot in request"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Шаг бланка — самостоятельная сущность: ссылка на этап больше не нужна.
    op.execute("ALTER TABLE blank_steps ALTER COLUMN stage_id DROP NOT NULL")
    op.execute("ALTER TABLE blank_steps ADD COLUMN title TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE blank_steps ADD COLUMN stage_lines JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE blank_steps ADD COLUMN executor_kind TEXT NOT NULL DEFAULT 'people' "
        "CHECK (executor_kind IN ('people', 'ad_group', 'manager_ad'))"
    )
    op.execute(
        "ALTER TABLE blank_steps ADD COLUMN assignees JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute("ALTER TABLE blank_steps ADD COLUMN owner_group TEXT NULL")
    op.execute(
        "ALTER TABLE blank_steps ADD COLUMN optional BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE blank_steps ADD COLUMN require_comment BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Шапка и подвал бланка — текст печати, собираемый из данных.
    op.execute("ALTER TABLE blanks ADD COLUMN header_html TEXT NULL")
    op.execute(
        "ALTER TABLE blanks ADD COLUMN footer_lines JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    # Снимок шапки/подвала в заявке (печать — по снимку, как blank_name/version).
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN blank_header_html TEXT NULL")
    op.execute(
        "ALTER TABLE dismissal_requests ADD COLUMN blank_footer_lines JSONB NOT NULL "
        "DEFAULT '[]'::jsonb"
    )
    # Перенос данных: прежний состав бланков ссылался на этапы и в новой модели
    # смысла не имеет — бланки собирает человек заново через админку.
    op.execute("DELETE FROM blank_steps")
    op.execute("DELETE FROM blanks")
    # Легаси-ключи настроек вместе с вкладкой «Шаблоны».
    op.execute("DELETE FROM settings WHERE key IN ('templates', 'position_to_category')")


def downgrade() -> None:
    # Содержимое бланков прежним не восстанавливаем: старые шаги ссылались на
    # этапы, которых в новой модели нет (решение человека 2026-10-08).
    op.execute("DELETE FROM blank_steps")
    op.execute("DELETE FROM blanks")
    op.execute("DELETE FROM settings WHERE key IN ('templates', 'position_to_category')")
    # Колонки возвращаем в прежний вид: без этого повторный upgrade head после
    # отката падал бы с «column ... already exists» (как 0013).
    for column in (
        "title",
        "stage_lines",
        "executor_kind",
        "assignees",
        "owner_group",
        "optional",
        "require_comment",
    ):
        op.execute("ALTER TABLE blank_steps DROP COLUMN IF EXISTS %s" % column)
    op.execute("ALTER TABLE blanks DROP COLUMN IF EXISTS header_html")
    op.execute("ALTER TABLE blanks DROP COLUMN IF EXISTS footer_lines")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_header_html")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_footer_lines")
    # stage_id снова обязателен — сначала убираем пустые ссылки на этапы.
    op.execute("DELETE FROM blank_steps WHERE stage_id IS NULL")
    op.execute("ALTER TABLE blank_steps ALTER COLUMN stage_id SET NOT NULL")

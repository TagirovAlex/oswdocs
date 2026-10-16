# Миграция 0026: свой срок шага (ttl_hours) — справочник и снимок заявки.
#
# Зачем. Срок отметки шага задавался одной настройкой approval_ttl_days на ВСЕ
# шаги (постановка человека 2026-10-16: «срок для каждого шага в часах/днях,
# некоторые шаги должны делаться быстро»). Одной настройки не хватает: у шага
# «непосредственный руководитель» и у шага «бухгалтерия» разные сроки.
#
# blank_steps.ttl_hours — срок шага бланка в ЧАСАХ (единица хранения одна:
# дни в UI пересчитываются в часы, чтобы не держать две колонки и не
# разъезжаться на округлении). NULL = срок не задан, берётся общий
# approval_ttl_days из settings (прежнее поведение).
#
# request_steps.ttl_hours — снимок срока шага на момент выдачи заявки, по
# образцу optional (миграция 0015): правка справочника уже выданную заявку не
# меняет. Нужен пересчёту срока при переоткрытии/повторе шага — без снимка
# там пришлось бы снова читать справочник и срок шага «поехал» бы вместе с
# правкой бланка.
#
# ДЕФОЛТ ДЛЯ РАНЕЕ ВЫДАННЫХ ЗАЯВОК — NULL (срок не задан): их сроки стоят в
# expires_at и выданы по общей настройке, пересчитываются при переоткрытии так
# же, как сейчас (общий approval_ttl_days).
"""свой срок шага в часах: blank_steps.ttl_hours и снимок request_steps.ttl_hours"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL, а не 0: «срок не задан» и «срок ноль» — разные вещи, ноль шагу
    # запрещён проверкой на границе (BlankStepIn.ttl_hours: ge=1).
    op.execute("ALTER TABLE blank_steps ADD COLUMN IF NOT EXISTS ttl_hours INTEGER")
    op.execute("ALTER TABLE request_steps ADD COLUMN IF NOT EXISTS ttl_hours INTEGER")


def downgrade() -> None:
    # Сносятся оба столбца: снимок и справочник. Выданные заявки продолжают
    # работать — их сроки лежат в expires_at (уже выданные сроки не меняются).
    op.execute("ALTER TABLE blank_steps DROP COLUMN IF EXISTS ttl_hours")
    op.execute("ALTER TABLE request_steps DROP COLUMN IF EXISTS ttl_hours")
# Миграция 0018: справочник шагов step_catalog и снятие «Макета печати»
# (решение человека 2026-10-09).
#
# step_catalog — переиспользуемые шаги-заготовки: бланк набирается копией шага
# справочника в blank_steps. Поля повторяют самостоятельный шаг бланка (0014):
# свой текст (title/stage_lines), свой исполнитель (executor_kind +
# assignees/owner_group), свои optional/require_comment/approval_mode. Код шага
# уникален и неизменен (как code бланка/этапа); active=false — шаг недоступен
# для набора в бланки, но остаётся в справочнике.
#
# Одновременно убираются остатки прежних файлов-шаблонов печати: blanks.layout
# и снимок dismissal_requests.blank_layout (пресеты office|line). Печать теперь
# единая: документ собирается из данных и снимков заявки, макет ни на что не
# влияет (миграция 0014 сняла файлы-шаблоны, 0018 — сам пресет).
"""справочник шагов step_catalog; снятие макета печати (layout)"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Справочник шагов-заготовок: те же поля, что у самостоятельного шага бланка
    # (0014/0013), плюс уникальный неизменный code и признак активности.
    op.execute(
        """
        CREATE TABLE step_catalog (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          stage_lines JSONB NOT NULL DEFAULT '[]'::jsonb,
          executor_kind TEXT NOT NULL DEFAULT 'people'
            CHECK (executor_kind IN ('people', 'ad_group', 'manager_ad')),
          assignees JSONB NOT NULL DEFAULT '[]'::jsonb,
          owner_group TEXT NULL,
          approval_mode TEXT NULL
            CHECK (approval_mode IN ('sequential', 'parallel')),
          optional BOOLEAN NOT NULL DEFAULT FALSE,
          require_comment BOOLEAN NOT NULL DEFAULT FALSE,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          updated_by TEXT NOT NULL DEFAULT '',
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Макет печати — остатки прежних файлов-шаблонов: печать единая, пресет
    # office|line больше ни на что не влияет (код его не читает с этой миграции).
    op.execute("ALTER TABLE blanks DROP COLUMN IF EXISTS layout")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_layout")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS step_catalog")
    # Колонки макета возвращаем в прежнем виде (как в 0012): без этого повторный
    # upgrade head после отката падал бы с «column ... already exists», а CHECK —
    # с «constraint already exists» (поэтому сначала явный DROP CONSTRAINT).
    op.execute("ALTER TABLE blanks DROP CONSTRAINT IF EXISTS blanks_layout_check")
    op.execute(
        "ALTER TABLE blanks ADD COLUMN IF NOT EXISTS layout TEXT NOT NULL DEFAULT 'office'"
    )
    op.execute(
        "ALTER TABLE blanks ADD CONSTRAINT blanks_layout_check "
        "CHECK (layout IN ('office', 'line'))"
    )
    op.execute(
        "ALTER TABLE dismissal_requests ADD COLUMN IF NOT EXISTS blank_layout TEXT NULL"
    )
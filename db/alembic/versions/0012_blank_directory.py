# Миграция 0012: справочник бланков (решение человека 2026-10-06).
#
# Новая модель: БЛАНК — набор шагов из справочника этапов; ШАГ — текст описания
# (title + stage_lines) и список ответственных (owner_kind/stage_assignees). Сотрудник
# ОК выбирает бланк из списка при создании заявки; система фиксирует шаги и
# ответственных в заявке (снимок). Файлы .docx как источник оформления уходят —
# документ собирается из данных.
#
# blanks            — собственно бланки (код/название/вид документа/макет/активность).
# blank_steps       — порядок шагов бланка со ссылкой на этап (как route_profile_steps,
#                     переопределения optional/require_comment = NULL — из этапа).
# dismissal_requests — blank_id + снимок blank_name/blank_version (чтобы правка
#                     справочника не меняла выданные заявки).
#
# Перенос данных: вид документа из doc_types становится бланком (увольнение — первый),
# doc_types остаётся как справочник видов (не удаляется, чтобы не рвать старые
# заявки и карточку); новая выборка бланков его не использует.
"""blank directory (blanks composed of stages)"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE blanks (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          -- Вид документа (doc_types.code) — классификация, не влияет на печать.
          doc_type_code TEXT NULL,
          description TEXT NULL,
          -- Макет печати: встроенные пресеты office|line, файлов-шаблонов нет.
          layout TEXT NOT NULL DEFAULT 'office'
            CHECK (layout IN ('office', 'line')),
          active BOOLEAN NOT NULL DEFAULT TRUE,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT
        )
        """
    )
    # Шаги бланка: порядок + переопределения как у профиля маршрута.
    op.execute(
        """
        CREATE TABLE blank_steps (
          blank_id INTEGER NOT NULL REFERENCES blanks (id) ON DELETE CASCADE,
          stage_id INTEGER NOT NULL REFERENCES approval_stages (id) ON DELETE RESTRICT,
          step_order INTEGER NOT NULL CHECK (step_order >= 1),
          optional_override BOOLEAN NULL,
          require_comment_override BOOLEAN NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (blank_id, step_order),
          UNIQUE (blank_id, stage_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX blank_steps_blank_order_idx ON blank_steps (blank_id, step_order)"
    )
    # Заявка: выбранный бланк и его снимок (название/версия на момент выдачи).
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN blank_id INTEGER NULL")
    op.execute(
        "ALTER TABLE dismissal_requests ADD COLUMN blank_name TEXT NULL"
    )
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN blank_version INTEGER NULL")
    # Макет бланка — тоже снимок: переключение бланка в справочнике не должно
    # менять вид уже выданной заявки (та же логика, что у шагов).
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN blank_layout TEXT NULL")
    op.execute(
        "ALTER TABLE dismissal_requests ADD CONSTRAINT dismissal_requests_blank_id_fkey "
        "FOREIGN KEY (blank_id) REFERENCES blanks (id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX dismissal_requests_blank_idx ON dismissal_requests (blank_id)"
    )

    # Перенос: каждый вид документа doc_types становится бланком с тем же кодом и
    # названием. Пустой справочник — бланков не будет, их заводит человек.
    op.execute(
        """
        INSERT INTO blanks (code, name, doc_type_code, layout, active)
        SELECT code, name, code, 'office', is_active
        FROM doc_types
        ON CONFLICT (code) DO NOTHING
        """
    )

    # Перенос состава НЕ выполняется автоматически: справочник route_profiles
    # привязан к службам AD, а бланк — к виду документа, и однозначного
    # соответствия между ними нет. Состав бланка собирает админ в разделе
    # «Бланки»; пустой бланк без шагов нельзя выбрать при создании заявки.

    # Автоподстановка бланка по службе выключена: бланк выбирает сотрудник ОК
    # (решение человека 2026-10-06). Сид идемпотентен, значение — в settings.
    op.execute(
        """
        INSERT INTO settings (key, value)
        VALUES ('blank_autopick', '"off"')
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS dismissal_requests_blank_idx")
    op.execute("ALTER TABLE dismissal_requests DROP CONSTRAINT IF EXISTS dismissal_requests_blank_id_fkey")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_version")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_layout")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_name")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN IF EXISTS blank_id")
    op.execute("DROP INDEX IF EXISTS blank_steps_blank_order_idx")
    op.execute("DROP TABLE IF EXISTS blank_steps")
    op.execute("DROP TABLE IF EXISTS blanks")
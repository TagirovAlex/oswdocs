# Миграция 0003: карточка заявки (тема/содержание/вид документа), виды документов
# и комментарии (решения пользователя 2026-10-02).
# Вверх — таблица doc_types с сидом из контентного ключа settings.doc_types
# (массив строк, sort_order по индексу; ключа нет/пусто — дефолт «Увольнение»),
# поля subject/content/doc_type_code в dismissal_requests (nullable, старые заявки
# остаются NULL) и таблица request_comments с индексом (request_id, at).
# Вниз — обратные операции в порядке зависимостей.
"""request card, doc types and comments (wave card)"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Виды документов: код — короткое имя вида (значения settings.doc_types).
    op.execute(
        """
        CREATE TABLE doc_types (
          code TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          is_active BOOLEAN NOT NULL DEFAULT true,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Сид из контентного ключа settings.doc_types (массив строк) — по одной записи
    # с sort_order по индексу (WITH ORDINALITY с 1, поэтому ord - 1).
    op.execute(
        """
        INSERT INTO doc_types (code, name, sort_order)
        SELECT elem, elem, (ord - 1)::int
        FROM settings,
             jsonb_array_elements_text(settings.value) WITH ORDINALITY AS t(elem, ord)
        WHERE settings.key = 'doc_types'
          AND jsonb_typeof(settings.value) = 'array'
        """
    )
    # Сида не было (нет ключа/не массив/пустой массив) — дефолтный вид. Значение
    # в БД, а не в коде API: дальше вид правится в настройках/таблице.
    op.execute(
        """
        INSERT INTO doc_types (code, name)
        SELECT 'Увольнение', 'Увольнение'
        WHERE NOT EXISTS (SELECT 1 FROM doc_types)
        """
    )

    # Поля карточки заявки (nullable: существующие заявки — NULL, показ пустым).
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN subject TEXT")
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN content TEXT")
    op.execute(
        "ALTER TABLE dismissal_requests "
        "ADD COLUMN doc_type_code TEXT REFERENCES doc_types (code)"
    )

    # Комментарии заявки (отдельная таблица по решению пользователя).
    op.execute(
        """
        CREATE TABLE request_comments (
          id BIGSERIAL PRIMARY KEY,
          request_id TEXT NOT NULL,
          author TEXT,
          body TEXT NOT NULL,
          at TIMESTAMPTZ NOT NULL DEFAULT now(),
          kind TEXT NOT NULL DEFAULT 'request'
            CHECK (kind IN ('request', 'step', 'rollback')),
          step_id TEXT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX request_comments_request_at_idx "
        "ON request_comments (request_id, at)"
    )


def downgrade() -> None:
    # Обратные операции: комментарии и поля карточки, затем doc_types.
    op.execute("DROP INDEX IF EXISTS request_comments_request_at_idx")
    op.execute("DROP TABLE IF EXISTS request_comments")
    # DROP COLUMN снимает и FK doc_type_code -> doc_types.
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN doc_type_code")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN content")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN subject")
    op.execute("DROP TABLE IF EXISTS doc_types")
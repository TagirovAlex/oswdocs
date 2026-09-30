# Миграция 0002: персистентность заявок (Волна 2, B1).
# Вверх — поля модели заявки, которых не хватало в 0001: бизнес-номер REQ-XXXX
# (колонка code), fio/department/position/category/escalation_hours; base_code
# без FK (в модели _Request его нет); у шагов — resolver/assignee/require_comment.
# Вниз — откат к схеме 0001: колонки падают, NOT NULL/FK на base_code
# возвращаются «по возможности» (с данными с NULL base_code откат остановится).
"""persist requests (wave 2 B1)"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Бизнес-номер заявки REQ-XXXX (в модели — id). Уникальный индекс: старые
    # строки (до Волны 2 заявки жили в памяти) допускают NULL.
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN code TEXT")
    op.execute(
        "CREATE UNIQUE INDEX dismissal_requests_code_key "
        "ON dismissal_requests (code)"
    )

    # Снапшот карточки 1С на момент создания (ПДн; обрезка — на уровне API).
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN fio TEXT")
    op.execute(
        "ALTER TABLE dismissal_requests ADD COLUMN department TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE dismissal_requests ADD COLUMN position TEXT NOT NULL DEFAULT ''"
    )
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN category TEXT")
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN escalation_hours INTEGER")

    # В модели _Request нет base_code: убираем FK на employee_base_map и делаем
    # колонку nullable (составной FK с NULL-компонентом невозможен).
    op.execute(
        "ALTER TABLE dismissal_requests "
        "DROP CONSTRAINT dismissal_requests_enterprise_base_code_tab_num_fkey"
    )
    op.execute("ALTER TABLE dismissal_requests ALTER COLUMN base_code DROP NOT NULL")

    # Новые поля шагов (модель _Step).
    op.execute(
        "ALTER TABLE request_steps "
        "ADD COLUMN resolver TEXT NOT NULL DEFAULT 'by_group'"
    )
    op.execute("ALTER TABLE request_steps ADD COLUMN assignee TEXT")
    op.execute(
        "ALTER TABLE request_steps "
        "ADD COLUMN require_comment BOOLEAN NOT NULL DEFAULT false"
    )

    # FK на users блокируют INSERT при пустой таблице users (AD ещё не
    # синхронизирован): снимаем, колонки остаются TEXT с sam-логином
    # (ПДн-обрезка — на уровне API, см. _public_view).
    op.execute(
        "ALTER TABLE dismissal_requests "
        "DROP CONSTRAINT IF EXISTS dismissal_requests_initiated_by_hr_fkey"
    )
    op.execute(
        "ALTER TABLE request_steps DROP CONSTRAINT IF EXISTS request_steps_done_by_fkey"
    )

    # Связки 1С-AD: поля модели LinkRecord (diverged/needs_manual_review/
    # truth_source), которых не было в 0001 (Волна 4).
    op.execute(
        "ALTER TABLE link_1c_ad "
        "ADD COLUMN diverged BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE link_1c_ad "
        "ADD COLUMN needs_manual_review BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE link_1c_ad "
        "ADD COLUMN truth_source TEXT NOT NULL DEFAULT '1c'"
    )

    # mail_queue.template_code ссылается на mail_templates(code), но прикладные
    # шаблоны писем читаются из settings (JSONB-ключ mail_templates), таблица
    # не сидируется — FK ронял бы постановку писем в очередь (ревью Волны 3).
    op.execute(
        "ALTER TABLE mail_queue DROP CONSTRAINT IF EXISTS mail_queue_template_code_fkey"
    )


def downgrade() -> None:
    # Сначала индекс на code, затем колонки.
    op.execute("DROP INDEX IF EXISTS dismissal_requests_code_key")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN code")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN fio")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN department")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN position")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN category")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN escalation_hours")
    op.execute("ALTER TABLE request_steps DROP COLUMN resolver")
    op.execute("ALTER TABLE request_steps DROP COLUMN assignee")
    op.execute("ALTER TABLE request_steps DROP COLUMN require_comment")
    op.execute("ALTER TABLE link_1c_ad DROP COLUMN truth_source")
    op.execute("ALTER TABLE link_1c_ad DROP COLUMN needs_manual_review")
    op.execute("ALTER TABLE link_1c_ad DROP COLUMN diverged")

    # Возврат FK на users (пустая таблица — данные не нарушают).
    op.execute(
        "ALTER TABLE request_steps "
        "ADD CONSTRAINT request_steps_done_by_fkey FOREIGN KEY (done_by) REFERENCES users (sam)"
    )
    op.execute(
        "ALTER TABLE dismissal_requests "
        "ADD CONSTRAINT dismissal_requests_initiated_by_hr_fkey "
        "FOREIGN KEY (initiated_by_hr) REFERENCES users (sam)"
    )
    op.execute(
        "ALTER TABLE mail_queue "
        "ADD CONSTRAINT mail_queue_template_code_fkey "
        "FOREIGN KEY (template_code) REFERENCES mail_templates (code)"
    )

    # Возврат к схеме 0001 «по возможности»: если остался NULL base_code,
    # SET NOT NULL не взведётся — откат остановится (данные важнее отката).
    op.execute("ALTER TABLE dismissal_requests ALTER COLUMN base_code SET NOT NULL")
    op.execute(
        "ALTER TABLE dismissal_requests "
        "ADD CONSTRAINT dismissal_requests_enterprise_base_code_tab_num_fkey "
        "FOREIGN KEY (enterprise, base_code, tab_num) "
        "REFERENCES employee_base_map (enterprise, base_code, tab_num)"
    )
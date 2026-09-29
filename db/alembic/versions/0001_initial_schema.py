# Миграция 0001: начальная схема СЭД по README п.4.
# Вверх — все таблицы, индексы pg_trgm на ФИО/sam, защита audit_log от изменений.
# Вниз — полный откат: триггер, функция и все таблицы удаляются.
"""initial schema"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Расширение для нечеткого поиска по ФИО/sam (индексы GIN создаются ниже).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Пользователи из AD (зеркало учеток, только чтение первоисточника).
    op.execute(
        """
        CREATE TABLE users (
          sam TEXT PRIMARY KEY,
          fio_full TEXT NOT NULL,
          dept_ad TEXT,
          title_ad TEXT,
          manager_dn TEXT,
          mail TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Нечеткий поиск по ФИО и логину.
    op.execute("CREATE INDEX users_fio_full_trgm ON users USING gin (fio_full gin_trgm_ops)")
    op.execute("CREATE INDEX users_sam_trgm ON users USING gin (sam gin_trgm_ops)")

    # Базы 1С ЗУП (пути/учетки — только в env ONEC_BASES_JSON, здесь лишь коды и имена).
    op.execute(
        """
        CREATE TABLE one_c_bases (
          code TEXT PRIMARY KEY,
          enterprise TEXT NOT NULL,
          name TEXT NOT NULL,
          odata_url TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX one_c_bases_enterprise_idx ON one_c_bases (enterprise)")

    # Сотрудники 1С. Составной ключ: предприятие + код базы + табельный номер.
    op.execute(
        """
        CREATE TABLE employee_base_map (
          enterprise TEXT NOT NULL,
          base_code TEXT NOT NULL REFERENCES one_c_bases (code),
          tab_num TEXT NOT NULL,
          fio TEXT NOT NULL,
          dept_1c TEXT,
          position_1c TEXT,
          employment_type TEXT,
          hire_date DATE,
          vacation_balance NUMERIC(6, 2),
          mol_flag BOOLEAN NOT NULL DEFAULT FALSE,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (enterprise, base_code, tab_num)
        )
        """
    )
    # Нечеткий поиск по ФИО из 1С.
    op.execute(
        "CREATE INDEX employee_base_map_fio_trgm "
        "ON employee_base_map USING gin (fio gin_trgm_ops)"
    )

    # Ручная стыковка 1С<->AD по полному ФИО (истина при расхождении — 1С).
    op.execute(
        """
        CREATE TABLE link_1c_ad (
          id SERIAL PRIMARY KEY,
          enterprise TEXT NOT NULL,
          base_code TEXT NOT NULL,
          tab_num TEXT NOT NULL,
          sam TEXT REFERENCES users (sam),
          linked_by TEXT,
          linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          verified BOOLEAN NOT NULL DEFAULT FALSE,
          UNIQUE (enterprise, base_code, tab_num),
          FOREIGN KEY (enterprise, base_code, tab_num)
            REFERENCES employee_base_map (enterprise, base_code, tab_num)
        )
        """
    )
    op.execute("CREATE INDEX link_1c_ad_sam_idx ON link_1c_ad (sam)")

    # Заявки на увольнение со снапшотами карточки 1С/AD на момент создания.
    # Статусы (код -> смысл README п.1):
    # draft -> Черновик, in_approval -> На согласовании, rework -> На доработке,
    # approved -> Согласовано, to_execute -> К исполнению, done -> Завершено,
    # rejected -> Отклонено, withdrawn -> Отозвано.
    op.execute(
        """
        CREATE TABLE dismissal_requests (
          id SERIAL PRIMARY KEY,
          enterprise TEXT NOT NULL,
          base_code TEXT NOT NULL,
          tab_num TEXT NOT NULL,
          initiated_by_hr TEXT REFERENCES users (sam),
          route_origin TEXT NOT NULL DEFAULT 'manual'
            CHECK (route_origin IN ('template', 'manual')),
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'in_approval', 'rework', 'approved',
                              'to_execute', 'done', 'rejected', 'withdrawn')),
          snapshot_1c JSONB,
          snapshot_ad JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (enterprise, base_code, tab_num)
            REFERENCES employee_base_map (enterprise, base_code, tab_num)
        )
        """
    )
    op.execute(
        "CREATE INDEX dismissal_requests_emp_idx "
        "ON dismissal_requests (enterprise, base_code, tab_num)"
    )
    op.execute("CREATE INDEX dismissal_requests_status_idx ON dismissal_requests (status)")

    # Шаги согласования: владелец — имя группы из настроек (в коде групп нет).
    # Статусы шага: pending -> ожидает, approved -> согласовано,
    # rejected -> отказано, returned -> возвращено, expired -> просрочено.
    op.execute(
        """
        CREATE TABLE request_steps (
          id SERIAL PRIMARY KEY,
          request_id INTEGER NOT NULL REFERENCES dismissal_requests (id) ON DELETE CASCADE,
          step_order INTEGER NOT NULL,
          owner_group TEXT NOT NULL,
          done_by TEXT REFERENCES users (sam),
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected', 'returned', 'expired')),
          done_at TIMESTAMPTZ,
          expires_at TIMESTAMPTZ,
          comment TEXT,
          UNIQUE (request_id, step_order)
        )
        """
    )
    op.execute("CREATE INDEX request_steps_owner_group_idx ON request_steps (owner_group)")

    # Документы заявки: только мета и пути в volume, файлов в БД нет.
    op.execute(
        """
        CREATE TABLE documents (
          id SERIAL PRIMARY KEY,
          request_id INTEGER NOT NULL REFERENCES dismissal_requests (id) ON DELETE CASCADE,
          version TEXT NOT NULL,
          docx_path TEXT,
          pdf_path TEXT,
          qr_payload TEXT,
          created_by TEXT REFERENCES users (sam),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (request_id, version)
        )
        """
    )

    # Вложения (сканы заявлений): только мета и путь, лимиты — в settings.
    op.execute(
        """
        CREATE TABLE attachments (
          id SERIAL PRIMARY KEY,
          request_id INTEGER NOT NULL REFERENCES dismissal_requests (id) ON DELETE CASCADE,
          file_path TEXT NOT NULL,
          file_name TEXT NOT NULL,
          mime TEXT,
          size_bytes BIGINT,
          uploaded_by TEXT REFERENCES users (sam),
          uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX attachments_request_idx ON attachments (request_id)")

    # Шаблоны маршрутов согласования.
    op.execute(
        """
        CREATE TABLE templates (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Шаги шаблона (ttl_days пуст -> брать approval_ttl_days из settings).
    op.execute(
        """
        CREATE TABLE template_steps (
          id SERIAL PRIMARY KEY,
          template_id INTEGER NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
          step_order INTEGER NOT NULL,
          owner_group TEXT NOT NULL,
          require_comment BOOLEAN NOT NULL DEFAULT FALSE,
          ttl_days INTEGER,
          UNIQUE (template_id, step_order)
        )
        """
    )

    # Фильтры подбора шаблона по службе и категории сотрудника.
    op.execute(
        """
        CREATE TABLE template_filters (
          id SERIAL PRIMARY KEY,
          template_id INTEGER NOT NULL REFERENCES templates (id) ON DELETE CASCADE,
          service TEXT,
          category TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX template_filters_lookup_idx ON template_filters (service, category)"
    )

    # Шаблоны бланков по службе и категории (пути к файлам, не сами файлы).
    op.execute(
        """
        CREATE TABLE doc_templates (
          id SERIAL PRIMARY KEY,
          service TEXT NOT NULL,
          category TEXT NOT NULL,
          version TEXT NOT NULL DEFAULT 'v1',
          storage_path TEXT NOT NULL,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (service, category, version)
        )
        """
    )

    # Прикладные настройки (правит роль SED_ADMINS, значения — в сидах).
    op.execute(
        """
        CREATE TABLE settings (
          key TEXT PRIMARY KEY,
          value JSONB NOT NULL,
          updated_by TEXT REFERENCES users (sam),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Шаблоны писем (тема и Jinja-HTML тело).
    op.execute(
        """
        CREATE TABLE mail_templates (
          code TEXT PRIMARY KEY,
          subject TEXT NOT NULL,
          body_html TEXT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # Очередь писем: статусы queued -> в очереди, sent -> отправлено, failed -> ошибка.
    op.execute(
        """
        CREATE TABLE mail_queue (
          id SERIAL PRIMARY KEY,
          template_code TEXT REFERENCES mail_templates (code),
          to_mail TEXT NOT NULL,
          payload JSONB,
          status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'sent', 'failed')),
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          sent_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX mail_queue_status_idx ON mail_queue (status)")

    # Журнал аудита: только добавление, правки и удаления запрещены триггером.
    op.execute(
        """
        CREATE TABLE audit_log (
          id BIGSERIAL PRIMARY KEY,
          at TIMESTAMPTZ NOT NULL DEFAULT now(),
          actor TEXT,
          action TEXT NOT NULL,
          entity TEXT NOT NULL,
          entity_id TEXT,
          details JSONB
        )
        """
    )
    op.execute("CREATE INDEX audit_log_entity_idx ON audit_log (entity, entity_id)")
    op.execute("CREATE INDEX audit_log_at_idx ON audit_log (at)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_no_modify()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log append-only: изменение и удаление запрещено';
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_no_modify()
        """
    )


def downgrade() -> None:
    # Откат в обратном порядке зависимостей: сначала триггер и функция аудита.
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_no_modify()")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS mail_queue")
    op.execute("DROP TABLE IF EXISTS mail_templates")
    op.execute("DROP TABLE IF EXISTS settings")
    op.execute("DROP TABLE IF EXISTS doc_templates")
    op.execute("DROP TABLE IF EXISTS template_filters")
    op.execute("DROP TABLE IF EXISTS template_steps")
    op.execute("DROP TABLE IF EXISTS templates")
    op.execute("DROP TABLE IF EXISTS attachments")
    op.execute("DROP TABLE IF EXISTS documents")
    op.execute("DROP TABLE IF EXISTS request_steps")
    op.execute("DROP TABLE IF EXISTS dismissal_requests")
    op.execute("DROP TABLE IF EXISTS link_1c_ad")
    op.execute("DROP TABLE IF EXISTS employee_base_map")
    op.execute("DROP TABLE IF EXISTS one_c_bases")
    op.execute("DROP TABLE IF EXISTS users")
    # Расширение pg_trgm оставляем: оно общее для базы, не часть схемы.

# Миграция 0008: справочники маршрута согласования (службы, профили, этапы).
# Маршрут и печатный бланк строятся из справочников, а не из ручного ввода:
# ad_services (службы) -> route_profiles (профили маршрута) ->
# approval_stages (этапы) -> stage_assignees (состав этапа) /
# route_profile_steps (порядок этапов профиля).
# Мёртвые таблицы templates/template_steps/template_filters из 0001 приложение
# не использует — удаляем. Снимок этапа на момент создания заявки кладётся в
# request_steps (stage_id/stage_code/stage_title/stage_lines/profile_step_id),
# профиль и служба заявки — в dismissal_requests (profile_id/service_id/
# service_name), чтобы маршрут не пересобирался при правке справочников.
"""route catalogs (services, profiles, stages)"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Службы (подразделения из AD/1С): blank_kind задаёт вид бланка печати
    # (office — построчный по подразделениям, line — линейный маршрут).
    # route_profile_id заполняется FK ниже: профили создаются после служб.
    op.execute(
        """
        CREATE TABLE ad_services (
          id SERIAL PRIMARY KEY,
          dept_name TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'inactive')),
          active_count INTEGER NOT NULL DEFAULT 0,
          last_seen_at TIMESTAMPTZ,
          blank_kind TEXT
            CHECK (blank_kind IN ('office', 'line')),
          route_profile_id INTEGER,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT
        )
        """
    )
    op.execute("CREATE INDEX ad_services_status_idx ON ad_services (status)")

    # Профили маршрута: service_id NULL — профиль по умолчанию (на все службы),
    # иначе профиль закреплён за конкретной службой.
    op.execute(
        """
        CREATE TABLE route_profiles (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          service_id INTEGER REFERENCES ad_services (id) ON DELETE SET NULL,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX route_profiles_service_active_idx "
        "ON route_profiles (service_id, active)"
    )
    # Ссылка службы на профиль по умолчанию — после создания route_profiles.
    op.execute(
        "ALTER TABLE ad_services ADD CONSTRAINT ad_services_route_profile_id_fkey "
        "FOREIGN KEY (route_profile_id) REFERENCES route_profiles (id) "
        "ON DELETE SET NULL"
    )

    # Этапы маршрута: title идёт в колонку 2 бланка, stage_lines (jsonb) —
    # в колонку 3. owner_kind задаёт источник исполнителя этапа:
    # ad_group (группа AD), stage_roster (состав этапа), manager_ad (руководитель).
    # require_comment — комментарий обязателен даже при согласии.
    op.execute(
        """
        CREATE TABLE approval_stages (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          stage_lines JSONB NOT NULL DEFAULT '[]'::jsonb,
          owner_kind TEXT NOT NULL DEFAULT 'ad_group'
            CHECK (owner_kind IN ('ad_group', 'stage_roster', 'manager_ad')),
          owner_group TEXT,
          optional BOOLEAN NOT NULL DEFAULT TRUE,
          print_assignee BOOLEAN NOT NULL DEFAULT FALSE,
          require_comment BOOLEAN NOT NULL DEFAULT FALSE,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT
        )
        """
    )

    # Состав этапа (owner_kind = stage_roster): логины AD без ФИО (ПДн).
    op.execute(
        """
        CREATE TABLE stage_assignees (
          id SERIAL PRIMARY KEY,
          stage_id INTEGER NOT NULL REFERENCES approval_stages (id) ON DELETE CASCADE,
          sam TEXT NOT NULL,
          position_title TEXT,
          active BOOLEAN NOT NULL DEFAULT TRUE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by TEXT,
          UNIQUE (stage_id, sam)
        )
        """
    )
    op.execute(
        "CREATE INDEX stage_assignees_stage_active_idx "
        "ON stage_assignees (stage_id, active)"
    )

    # Порядок этапов профиля: шаг RESTRICT (этап из маршрутов не удаляется),
    # переопределения optional/require_comment — NULL = брать из этапа.
    op.execute(
        """
        CREATE TABLE route_profile_steps (
          id SERIAL PRIMARY KEY,
          profile_id INTEGER NOT NULL REFERENCES route_profiles (id) ON DELETE CASCADE,
          stage_id INTEGER NOT NULL REFERENCES approval_stages (id) ON DELETE RESTRICT,
          step_order INTEGER NOT NULL,
          optional_override BOOLEAN,
          require_comment_override BOOLEAN,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by TEXT,
          UNIQUE (profile_id, step_order)
        )
        """
    )
    op.execute(
        "CREATE INDEX route_profile_steps_profile_order_idx "
        "ON route_profile_steps (profile_id, step_order)"
    )

    # Снимок этапа в шаге заявки: маршрут не пересобирается при правке
    # справочника (обнуление этапа — SET NULL, снимок остаётся).
    op.execute(
        "ALTER TABLE request_steps "
        "ADD COLUMN stage_id INTEGER REFERENCES approval_stages (id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE request_steps ADD COLUMN stage_code TEXT")
    op.execute("ALTER TABLE request_steps ADD COLUMN stage_title TEXT")
    op.execute("ALTER TABLE request_steps ADD COLUMN stage_lines JSONB")
    op.execute("ALTER TABLE request_steps ADD COLUMN profile_step_id INTEGER")

    # Профиль и служба заявки: снимок подбора на момент создания.
    op.execute(
        "ALTER TABLE dismissal_requests "
        "ADD COLUMN profile_id INTEGER REFERENCES route_profiles (id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE dismissal_requests "
        "ADD COLUMN service_id INTEGER REFERENCES ad_services (id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE dismissal_requests ADD COLUMN service_name TEXT")

    op.execute(
        "CREATE INDEX request_steps_request_stage_idx "
        "ON request_steps (request_id, stage_id)"
    )

    # Мёртвые справочники ручных шаблонов из 0001 (приложение их не читало):
    # порядок дропа — от зависимых к независимым.
    op.execute("DROP TABLE IF EXISTS template_filters")
    op.execute("DROP TABLE IF EXISTS template_steps")
    op.execute("DROP TABLE IF EXISTS templates")


def downgrade() -> None:
    # Сначала индекс на новую пару шага, затем добавленные колонки
    # (FK уезжают вместе с колонками).
    op.execute("DROP INDEX IF EXISTS request_steps_request_stage_idx")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN service_name")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN service_id")
    op.execute("ALTER TABLE dismissal_requests DROP COLUMN profile_id")
    op.execute("ALTER TABLE request_steps DROP COLUMN profile_step_id")
    op.execute("ALTER TABLE request_steps DROP COLUMN stage_lines")
    op.execute("ALTER TABLE request_steps DROP COLUMN stage_title")
    op.execute("ALTER TABLE request_steps DROP COLUMN stage_code")
    op.execute("ALTER TABLE request_steps DROP COLUMN stage_id")

    # Справочники маршрута — от зависимых к независимым.
    op.execute("DROP TABLE IF EXISTS route_profile_steps")
    op.execute("DROP TABLE IF EXISTS stage_assignees")
    op.execute("DROP TABLE IF EXISTS approval_stages")
    # ad_services ссылается на route_profiles, поэтому FK снимаем ДО дропа
    # профилей — иначе PostgreSQL откатывает весь downgrade.
    op.execute(
        "ALTER TABLE ad_services DROP CONSTRAINT IF EXISTS ad_services_route_profile_id_fkey"
    )
    op.execute("DROP TABLE IF EXISTS route_profiles")
    op.execute("DROP TABLE IF EXISTS ad_services")

    # Возврат справочников ручных шаблонов в структуре 0001 (пустыми).
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
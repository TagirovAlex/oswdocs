# Миграция 0020: справочник блоков функций и шаблонов прав доступа
# (решение человека 2026-10-11, спека — PLAN.md «МОДЕЛЬ ПРАВ ДОСТУПА»).
#
# Единица прав — БЛОК ФУНКЦИИ, а не роль (решение человека). Хранение — по образцу
# справочника бланков (0012: blanks/blank_steps), только без привязки к заявкам:
#   perm_blocks          — справочник блоков (код/название/активность/порядок);
#   perm_templates       — справочник шаблонов (код/название/protected/порядок);
#   perm_template_blocks — состав «шаблон → блок»;
#   perm_template_groups — группы AD шаблона.
# Несколько шаблонов у человека — ОБЪЕДИНЕНИЕ прав (резолв — волна F2).
#
# Вверх: создаются четыре таблицы; сид 13 блоков, 5 шаблонов и состава
# «шаблон → блок» (перенос текущих прав один в один: роль → шаблон того же кода);
# группы шаблонов sed_admin/admin/hr_admin/hr переносятся из ключей settings
# sed_admin_groups/admin_groups/hr_admin_groups/hr_groups (по одному ключу на
# шаблон; ниже — как именно переносится значение).
#
# Ключ access_groups НЕ переносится — он остаётся ключом ВХОДА в систему
# (api/app/settings_routes.py, resolve_allowed_groups), и резолв прав появится в
# волне F2. Сами ключи settings не меняются и не удаляются: до F2 код работает
# как раньше, миграция ни на что в приложении не влияет.
#
# Перенос групп — fail-soft: ключа нет, значение не массив или элемент не строка —
# у шаблона просто не будет групп, ошибки не будет. Переносим ТОЛЬКО то, что
# реально есть в БД: никаких имён групп по умолчанию в миграции не зашито
# (AGENTS.md §3). Формат значения повторяет чтение в коде (`_groups_with_names`
# в settings_routes.py): строки и объекты {id, name} — берётся id. Таблицы без
# created_at/updated_at — в отличие от blanks: состав правится целиком и по
# журналу аудита, отдельные метки времени ему не нужны.
#
# Сиды идемпотентны (ON CONFLICT DO NOTHING) — повторный прогон без ошибок.
#
# Вниз: честный откат схемы — DROP TABLE четырёх новых таблиц в обратном порядке
# (cascade). Данные settings не трогаем: миграция их не меняла.
"""справочник прав доступа (блоки функций, шаблоны, состав, группы AD)"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Блоки функций — единица прав. Код латинский snake_case и не меняется: по
    # нему резолв (F2) и админка показывают название (title).
    op.execute(
        """
        CREATE TABLE perm_blocks (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          description TEXT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    # Шаблоны прав. protected=TRUE — шаблон нельзя лишить блоков/групп: это
    # основа доступа к настройкам прав, правило «админ не может снять права
    # у себя» (волна F2) опирается на такие шаблоны.
    op.execute(
        """
        CREATE TABLE perm_templates (
          id SERIAL PRIMARY KEY,
          code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          description TEXT NULL,
          protected BOOLEAN NOT NULL DEFAULT FALSE,
          sort_order INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Состав «шаблон → блок». Шаблон удаляется каскадом, блок — RESTRICT:
    # используемый в шаблонах блок из справочника не удаляется.
    op.execute(
        """
        CREATE TABLE perm_template_blocks (
          template_id INTEGER NOT NULL REFERENCES perm_templates (id) ON DELETE CASCADE,
          block_id INTEGER NOT NULL REFERENCES perm_blocks (id) ON DELETE RESTRICT,
          PRIMARY KEY (template_id, block_id)
        )
        """
    )
    # Группы AD шаблона. Ключ входа в систему (access_groups)
    # сюда НЕ переносится.
    op.execute(
        """
        CREATE TABLE perm_template_groups (
          template_id INTEGER NOT NULL REFERENCES perm_templates (id) ON DELETE CASCADE,
          group_name TEXT NOT NULL,
          PRIMARY KEY (template_id, group_name)
        )
        """
    )

    # Сид блоков (13, согласованный состав). Порядок в таблице — это и есть
    # sort_order: шаг 10 оставляет место для вставки блока между соседями.
    op.execute(
        """
        INSERT INTO perm_blocks (code, title, sort_order)
        VALUES
          ('requests_list', 'Просмотр списка заявок', 10),
          ('request_card', 'Просмотр карточки заявки', 20),
          ('request_create', 'Создание заявки', 30),
          ('step_decide', 'Отметки по шагам (согласовать, отказать, вернуть)', 40),
          ('print_blank', 'Печать бланка', 50),
          ('request_delete', 'Удаление заявки', 60),
          ('employee_directory', 'Справочник сотрудников', 70),
          ('link_match', 'Сопоставление 1С↔AD', 80),
          ('settings_content', 'Настройки: контент', 90),
          ('settings_infra', 'Настройки: инфраструктура и регламенты', 100),
          ('settings_blanks', 'Настройки: бланки и справочник шагов', 110),
          ('archive_backups', 'Архивация (бэкапы)', 120),
          ('sed_admin_panel', 'Панель администратора СЭД в карточке', 130)
        ON CONFLICT (code) DO NOTHING
        """
    )

    # Сид шаблонов (5). У sed_admin и admin protected=TRUE: иначе можно было бы
    # вычистить состав и запереть доступ к настройкам прав (волна F2).
    op.execute(
        """
        INSERT INTO perm_templates (code, title, description, protected, sort_order)
        VALUES
          ('sed_admin', 'Администратор СЭД',
           'Перенос текущих прав администратора СЭД один в один', TRUE, 10),
          ('admin', 'Администратор',
           'Перенос текущих прав администратора один в один', TRUE, 20),
          ('hr_admin', 'Руководитель ОК',
           'Перенос текущих прав руководителя ОК один в один', FALSE, 30),
          ('hr', 'Сотрудник ОК',
           'Перенос текущих прав сотрудника ОК один в один', FALSE, 40),
          ('owner', 'Владелец шага',
           'Владение шагом определяется назначением шага, а не членством в группе '
           'AD, поэтому группы шаблона пустые намеренно; блоки «свои заявки/свой '
           'шаг» уточняются внутри самих блоков при резолве (волна F2)',
           FALSE, 50)
        ON CONFLICT (code) DO NOTHING
        """
    )

    # Состав «шаблон → блок»: перенос текущих прав один в один (решение человека
    # 2026-10-11). Строки берутся по кодам справочников, а не по id: сиды
    # идемпотентны и не зависят от порядка выдачи serial.
    # ГЛАВНОЕ: перед ON CONFLICT обязан стоять WHERE. Иначе PostgreSQL читает
    # «ON CONFLICT» как продолжение «JOIN ... ON» и падает на разборе (проверено
    # на стенде 2026-10-11: миграция откатилась целиком). «WHERE true» — пустой
    # предикат, который снимает неоднозначность, а не фильтр.
    op.execute(
        """
        INSERT INTO perm_template_blocks (template_id, block_id)
        SELECT template_id, block_id
        FROM (
          SELECT t.id AS template_id, b.id AS block_id
          FROM (VALUES
          ('sed_admin', 'requests_list'),
          ('sed_admin', 'request_card'),
          ('sed_admin', 'request_create'),
          ('sed_admin', 'step_decide'),
          ('sed_admin', 'print_blank'),
          ('sed_admin', 'request_delete'),
          ('sed_admin', 'employee_directory'),
          ('sed_admin', 'link_match'),
          ('sed_admin', 'settings_content'),
          ('sed_admin', 'settings_infra'),
          ('sed_admin', 'settings_blanks'),
          ('sed_admin', 'archive_backups'),
          ('sed_admin', 'sed_admin_panel'),
          ('admin', 'requests_list'),
          ('admin', 'request_card'),
          ('admin', 'request_create'),
          ('admin', 'step_decide'),
          ('admin', 'print_blank'),
          ('admin', 'request_delete'),
          ('admin', 'employee_directory'),
          ('admin', 'link_match'),
          ('admin', 'settings_content'),
          ('admin', 'settings_infra'),
          ('admin', 'settings_blanks'),
          ('admin', 'archive_backups'),
          ('hr_admin', 'requests_list'),
          ('hr_admin', 'request_card'),
          ('hr_admin', 'request_create'),
          ('hr_admin', 'step_decide'),
          ('hr_admin', 'print_blank'),
          ('hr_admin', 'employee_directory'),
          ('hr_admin', 'settings_content'),
          ('hr', 'requests_list'),
          ('hr', 'request_card'),
          ('hr', 'request_create'),
          ('hr', 'step_decide'),
          ('hr', 'print_blank'),
          ('hr', 'employee_directory'),
          ('owner', 'requests_list'),
          ('owner', 'request_card'),
          ('owner', 'step_decide')
        ) AS m(template_code, block_code)
        JOIN perm_templates t ON t.code = m.template_code
        JOIN perm_blocks b ON b.code = m.block_code
        ) AS src
        WHERE true
        ON CONFLICT (template_id, block_id) DO NOTHING
        """
    )

    # Группы шаблонов — из ключей settings ролевых групп.
    # Ключа нет / значение не массив / элемент не строка и не {id,name} — у
    # шаблона просто не будет групп (fail-soft, ошибки не возникает): группы
    # заводит админ в разделе прав. Ключи settings остаются на месте — резолв
    # читает их до волны F2.
    #
    # ВАЖНО про порядок вычисления: jsonb_array_elements — set-returning
    # функция в FROM, её аргумент считается ДО WHERE верхнего уровня, поэтому
    # проверка «значение — массив» должна быть ВНУТРИ вызова. Иначе на
    # скалярах settings (например blank_autopick = "off", ключи со строками и
    # числами) миграция упала бы с «cannot extract elements from a scalar».
    # Не-массив подставляем пустым массивом — вкладка просто пустая.
    op.execute(
        """
        INSERT INTO perm_template_groups (template_id, group_name)
        SELECT t.id, g.group_name
        FROM settings s
        JOIN perm_templates t
          ON t.code = CASE s.key
                        WHEN 'sed_admin_groups' THEN 'sed_admin'
                        WHEN 'admin_groups' THEN 'admin'
                        WHEN 'hr_admin_groups' THEN 'hr_admin'
                        WHEN 'hr_groups' THEN 'hr'
                      END
        CROSS JOIN LATERAL (
          SELECT btrim(
                   CASE jsonb_typeof(e.elem)
                     WHEN 'string' THEN e.elem #>> '{}'
                     WHEN 'object' THEN e.elem ->> 'id'
                   END
                 ) AS group_name
          FROM jsonb_array_elements(
                 CASE WHEN jsonb_typeof(s.value) = 'array'
                      THEN s.value ELSE '[]'::jsonb END
               ) AS e(elem)
          WHERE jsonb_typeof(e.elem) IN ('string', 'object')
        ) g
        WHERE s.key IN ('sed_admin_groups', 'admin_groups', 'hr_admin_groups', 'hr_groups')
          AND g.group_name <> ''
        ON CONFLICT (template_id, group_name) DO NOTHING
        """
    )


def downgrade() -> None:
    # Откат схемы: четыре новые таблицы в обратном порядке. CASCADE на всякий
    # случай (внешних ссылок на них в БД нет — резолв появится в волне F2).
    # Данные settings не восстанавливаем: миграция их не меняла.
    op.execute("DROP TABLE IF EXISTS perm_template_groups CASCADE")
    op.execute("DROP TABLE IF EXISTS perm_template_blocks CASCADE")
    op.execute("DROP TABLE IF EXISTS perm_templates CASCADE")
    op.execute("DROP TABLE IF EXISTS perm_blocks CASCADE")
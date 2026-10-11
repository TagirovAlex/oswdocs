# Миграция 0019: код вида документа — числовой, служебный (решение человека
# 2026-10-11).
#
# Смесь кодов сложилась из двух источников:
#   * текстовые коды — сид миграции 0003 (значения контентного ключа
#     settings.doc_types, который с тех пор удалён);
#   * числовые коды — автонумерация миграции 0005: при INSERT код и num брались
#     из одной последовательности doc_types_num_seq ДВУМЯ вызовами
#     (code DEFAULT nextval(...)::text, num DEFAULT nextval(...)), поэтому у
#     такой строки code <> num (например code «2» при num 3).
#
# Вверх — приводим код к числовому по внутреннему идентификатору:
# doc_types.code = num::text для всех строк, а ссылающиеся колонки
# (dismissal_requests.doc_type_code, blanks.doc_type_code) переписываются по той
# же карте «прежний код → num::text». Название вида («Увольнение») живёт в
# колонке name и не меняется; колонка num (BIGSERIAL, 0005) остаётся
# внутренним идентификатором.
#
# Порядок операций. FK в PostgreSQL NOT DEFERRABLE, поэтому значение PK нельзя
# менять, пока на него ссылаются строки с другим значением. Кроме того, если у
# строки с текстовым кодом num совпадёт с чужим уже занятым кодом (а такой
# сценарий не исключён — коды заводились и вручную, и автонумерацией), UPDATE в
# один проход упал бы на UNIQUE, даже если бы порядок строк его спас. Поэтому
# переезд делаем так, чтобы коллизия была невозможна по построению:
#   1) снимаем FK dismissal_requests.doc_type_code → doc_types (code) из 0003
#      (ON UPDATE/ON DELETE там не задавались — NO ACTION; это же возвращаем);
#   2) переписываем ссылки по карте «прежний код → num::text», пока коды
#      справочника ещё прежние; строки без пары (NULL или осиротевшее значение)
#      не трогаем — ничего не теряем;
#   3) снимаем PRIMARY KEY doc_types_pkey (code) — на время переезда кодов
#      уникальность не проверяется, целевые значения всё равно попарно различны
#      (num UNIQUE);
#   4) одним UPDATE переводим все коды в num::text — коллизия невозможна: и
#      цели различны, и прежние коды никто не держит (FK снят, PK снят);
#   5) возвращаем PRIMARY KEY с тем же именем doc_types_pkey, затем FK с теми
#      же правилами, что были в 0003 (без ON UPDATE/ON DELETE);
#   6) doc_types_num_seq — на максимум num (как в 0005 через
#      pg_get_serial_sequence + setval), чтобы новый вид не получил занятый
#      номер.
# Всё выполняется в одной транзакции миграции: промежуточного состояния без
# целостности «наружу» не видно.
#
# Вниз — отката данных НЕТ: прежних текстовых кодов в базе больше не существует,
# вернуть их можно только выдуманными значениями. Схема (FK, PK, колонки,
# последовательность) миграцией не меняется — PK и FK снимаются и возвращаются в
# том же виде, поэтому downgrade — no-op с явным объяснением, как в 0016.
# Повторный upgrade после отката идемпотентен: UPDATE WHERE code <> num::text
# не заденет уже числовые коды.
"""числовой код вида документа (doc_types.code приводится к num::text)"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. FK смотрит на doc_types.code и не даёт сменить значение PK (NOT
    #    DEFERRABLE) — снимаем на время переезда кодов. Имя автосозданное в 0003.
    op.execute(
        "ALTER TABLE dismissal_requests "
        "DROP CONSTRAINT IF EXISTS dismissal_requests_doc_type_code_fkey"
    )
    # 2. Ссылки приводим к новым кодам, пока коды справочника ещё прежние.
    #    NULL и значения без пары в doc_types (осиротевшие) остаются как есть.
    op.execute(
        """
        UPDATE dismissal_requests r
        SET doc_type_code = d.num::text
        FROM doc_types d
        WHERE d.code = r.doc_type_code
        """
    )
    # blanks.doc_type_code (миграция 0012) — логическая ссылка без FK, но она
    # показывается в справочнике бланков, поэтому переезжает вместе с видами.
    op.execute(
        """
        UPDATE blanks b
        SET doc_type_code = d.num::text
        FROM doc_types d
        WHERE d.code = b.doc_type_code
        """
    )
    # 3. На время переезда снимаем PK: без него UPDATE не может упасть на
    #    UNIQUE, даже если чей-то прежний код совпадёт с чужим целевым num.
    #    Ограничение возвращается ниже с тем же именем doc_types_pkey.
    op.execute("ALTER TABLE doc_types DROP CONSTRAINT doc_types_pkey")
    # 4. Коды справочника — все строки получают числовой код по num.
    #    Условие WHERE делает шаг идемпотентным при повторном прогоне.
    op.execute("UPDATE doc_types SET code = num::text WHERE code <> num::text")
    # 5. PK возвращаем (то же имя, что автосозданное в 0003), затем FK — без
    #    явных ON UPDATE/ON DELETE, то есть NO ACTION, как в 0003.
    op.execute("ALTER TABLE doc_types ADD CONSTRAINT doc_types_pkey PRIMARY KEY (code)")
    op.execute(
        "ALTER TABLE dismissal_requests "
        "ADD CONSTRAINT dismissal_requests_doc_type_code_fkey "
        "FOREIGN KEY (doc_type_code) REFERENCES doc_types (code)"
    )
    # 6. Последовательность на максимум num — новые виды не получают занятый код.
    op.execute(
        "SELECT setval(pg_get_serial_sequence('doc_types', 'num'), "
        "COALESCE((SELECT MAX(num) FROM doc_types), 1))"
    )


def downgrade() -> None:
    # Данные не восстанавливаем: прежних текстовых кодов («увольнение» и подобных
    # значений сида 0003) в базе больше нет, вернуть их можно только выдуманными
    # значениями. Схема (PK, FK, колонки, последовательность) миграцией не
    # меняется, поэтому откат — no-op; повторный upgrade после него идемпотентен.
    return None
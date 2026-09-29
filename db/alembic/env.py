# Окружение миграций Alembic: URL БД только из переменной окружения, секретов в коде нет.
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

# Конфигурация из alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Метаданные моделей не используются: схема задана явным SQL в ревизиях.
target_metadata = None


def get_url() -> str:
    # Подключение на стенде берется из DATABASE_URL (см. .env.example).
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL не задан в окружении")
    return url


def run_migrations_offline() -> None:
    # Режим генерации SQL-текста без живого подключения.
    context.configure(url=get_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Штатный накат на стенде через живое подключение.
    engine = create_engine(get_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

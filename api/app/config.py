# Конфигурация API СЭД: только env-переменные (инфра/секреты).
# Прикладные настройки (группы-члены, OU, предприятия, TTL, шаблоны) живут
# в таблице settings в БД (сиды — волна A1); здесь — только чтение env.
# Полный каталог — README п.3, пример значений — .env.example.

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Инфра-конфиг API. Значения по умолчанию — безопасные заглушки."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Postgres / приложение ---
    DATABASE_URL: str = Field(default="postgresql://localhost:5432/sed")

    # --- Redis (брокер + кэш) ---
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # --- Active Directory (только чтение через LDAPS bind, проверка memberOf) ---
    AD_URL: str = Field(default="ldaps://localhost:636")
    AD_BASE_DN: str = Field(default="DC=example,DC=com")
    AD_READER_DN: str = Field(default="")
    AD_READER_SECRET: str = Field(default="")
    LDAP_CACHE_TTL: int = Field(default=300)

    # --- 1С ЗУП (только чтение, мультибаза; разбор JSON — в волне A3) ---
    ONEC_BASES_JSON: str = Field(default="{}")

    # --- AD write-заглушка: всегда False в MVP (запись запрещена) ---
    AD_WRITE_ENABLED: bool = Field(default=False)

    # --- Группы доступа: только из env (на стенде — из settings БД, волна A1).
    # Дефолты нейтральные (пусто/пример): реальные имена — только через env/БД.
    ALLOWED_AD_GROUPS: str = Field(default="")
    ADMIN_GROUPS: str = Field(default="")
    HR_GROUPS: str = Field(default="")
    # Префикс групп владельцев шагов: любая группа с таким префиксом тоже
    # считается разрешающей (конкретные имена — в settings, волна A1).
    STEP_GROUP_PREFIX: str = Field(default="EXAMPLE_STEP_")

    # --- SMTP Exchange (уведомления; отправка — в волне B3) ---
    SMTP_HOST: str = Field(default="localhost")
    SMTP_FROM: str = Field(default="sed@example.com")

    # --- TLS-сертификат (read-only mount в proxy) ---
    CERT_PATH: str = Field(default="/srv/sed/certs")

    @property
    def admin_groups(self) -> set[str]:
        """Множество групп администраторов из env (без пустых, без пробелов)."""
        return {g.strip() for g in self.ADMIN_GROUPS.split(",") if g.strip()}

    @property
    def hr_groups(self) -> set[str]:
        """Множество групп ОК из env (без пустых, без пробелов)."""
        return {g.strip() for g in self.HR_GROUPS.split(",") if g.strip()}

    @property
    def allowed_groups(self) -> set[str]:
        """Множество разрешенных групп из env: явный список + админы + ОК."""
        explicit = {g.strip() for g in self.ALLOWED_AD_GROUPS.split(",") if g.strip()}
        return explicit | self.admin_groups | self.hr_groups

    def is_group_allowed(self, group: str) -> bool:
        """Группа разрешает вход: явный список или префикс владельцев шагов."""
        if group in self.allowed_groups:
            return True
        return bool(self.STEP_GROUP_PREFIX) and group.startswith(self.STEP_GROUP_PREFIX)

    def onec_bases(self) -> dict:
        """Разбор ONEC_BASES_JSON в словарь (пустой при битой строке)."""
        try:
            data = json.loads(self.ONEC_BASES_JSON or "{}")
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def ensure_read_only(self) -> None:
        """Защита: запись в AD запрещена, флаг обязан быть False."""
        if self.AD_WRITE_ENABLED:
            raise RuntimeError("AD_WRITE_ENABLED=true запрещен в MVP (только чтение AD)")


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам (синглтон на процесс)."""
    return Settings()

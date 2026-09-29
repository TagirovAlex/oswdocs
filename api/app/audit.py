# Append-only журнал аудита (скелет волны A2).
# Контракт: события только добавляются (append), обновления и удаления
# запрещены — так же будет и в таблице audit_log (волна A1, только INSERT).
# Сейчас — хранение в памяти процесса; персистентность — в волнах B1/B2.

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Одно событие аудита (минимум полей, расширяется по месту)."""

    at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Момент события (UTC)",
    )
    actor: str = Field(default="", description="Кто совершил действие (sam)")
    action: str = Field(default="", description="Что сделано (например me.read)")
    entity: str = Field(default="", description="Сущность (например user)")
    entity_id: str = Field(default="", description="Идентификатор сущности")
    detail: str = Field(default="", description="Пояснение без ПДн")


class AuditLogger:
    """Только добавление событий. Методов изменения/удаления нет специально."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        """Добавить событие в конец журнала (аналог будущего INSERT)."""
        self._events.append(event)
        return event

    def all(self) -> list[AuditEvent]:
        """Снимок журнала (только чтение, для проверок и тестов)."""
        return list(self._events)

    def clear_for_tests(self) -> None:
        """Сброс журнала. Только для изоляции pytest; в прод-коде не вызывать."""
        self._events.clear()


# Общий логгер процесса (в волнах B персистентность подменится без смены вызовов).
audit_log = AuditLogger()

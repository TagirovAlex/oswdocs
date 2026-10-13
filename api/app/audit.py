# Append-only журнал аудита (волна A2): память процесса + INSERT в audit_log.
# Контракт: события только добавляются (append), обновления запрещены — так же и
# в таблице audit_log (волна A1, триггер audit_log_no_update_delete). Исключение
# одно: DELETE разрешён для истории уже удалённой заявки — тестовый стенд,
# решение человека 2026-10-12 (миграция 0021, purge_request_history/Db
# delete). Запись в БД — best-effort: любой сбой БД не роняет запрос, журнал в
# памяти пополняется всегда (офлайн-тесты без Postgres).

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Сущности, которыми пишется история одной заявки: в entity_id у них бизнес-номер
# заявки. Этот список — эталон для зачистки следа удалённой заявки: триггер в БД
# (миграция 0021) и удаление в requests_store.py должны знать ровно тот же набор,
# иначе след заявки молча перестанет вычищаться, а вручную дочистить его уже
# не даст триггер.
REQUEST_HISTORY_ENTITIES = ("request", "document", "attachment")


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


class DbAuditStore:
    """Персистентное хранилище аудита: в этот класс — только INSERT в audit_log.

    Схема таблицы — миграция 0001 (id BIGSERIAL, at, actor, action, entity,
    entity_id, details JSONB); UPDATE запрещён триггером, DELETE — кроме следа
    уже удалённой заявки (миграция 0021, правило в шапке этого файла).
    Ошибки БД не пробрасываются наружу: аудит — best-effort, сбой журнала не
    должен ронять запрос приложения."""

    _INSERT_SQL = text(
        """
        INSERT INTO audit_log (at, actor, action, entity, entity_id, details)
        VALUES (:at, :actor, :action, :entity, :entity_id, CAST(:details AS jsonb))
        """
    )

    def __init__(self, database_url: str) -> None:
        # connect_timeout — best-effort: недоступная БД не должна надолго
        # задерживать запрос (по умолчанию драйвер может ждать десятки секунд).
        self._engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        self._session_factory = sessionmaker(
            bind=self._engine, expire_on_commit=False
        )

    def append(self, event: AuditEvent) -> None:
        """INSERT события (best-effort: сбой БД проглатывается)."""
        try:
            with self._session_factory() as session:
                session.execute(
                    self._INSERT_SQL,
                    {
                        "at": event.at,
                        "actor": event.actor,
                        "action": event.action,
                        "entity": event.entity,
                        "entity_id": event.entity_id,
                        "details": json.dumps(
                            {"detail": event.detail}, ensure_ascii=False
                        ),
                    },
                )
                session.commit()
        except Exception:
            # Журнал в памяти уже пополнен вызывающим кодом — молча пропускаем.
            pass


class AuditLogger:
    """Только добавление событий; изменить или удалить отдельное событие нельзя.

    Единственное исключение — purge_request_history: зачистка следа заявки,
    которой больше нет (тестовый стенд, решение человека 2026-10-12)."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> AuditEvent:
        """Добавить событие в конец журнала (память + best-effort INSERT)."""
        self._events.append(event)
        _persist(event)
        return event

    def all(self) -> list[AuditEvent]:
        """Снимок журнала (только чтение, для проверок и тестов)."""
        return list(self._events)

    def purge_request_history(self, entity_id: str) -> int:
        """Убрать из памяти события истории одной заявки (тестовый стенд).

        В БД историю удалённой заявки чистит DbRequestsStore.delete — там это
        DELETE в audit_log, разрешённый триггером. Здесь только список процесса:
        офлайн-хранилище заявок читает историю именно из него, и без зачистки
        удалённая заявка продолжала бы отдавать свой журнал."""
        before = len(self._events)
        self._events = [
            e
            for e in self._events
            if not (e.entity in REQUEST_HISTORY_ENTITIES and e.entity_id == entity_id)
        ]
        return before - len(self._events)

    def clear_for_tests(self) -> None:
        """Сброс журнала. Только для изоляции pytest; в прод-коде не вызывать."""
        self._events.clear()


_db_audit_store: DbAuditStore | None = None


def _get_db_store() -> DbAuditStore | None:
    """Боевой персистентный логгер: один движок на процесс (ленивый импорт).

    Импорт get_settings внутри функции — чтобы не зациклить модули (audit.py
    подключается одним из первых). При отсутствии настроек возвращает None."""
    global _db_audit_store
    if _db_audit_store is None:
        try:
            from .config import get_settings

            _db_audit_store = DbAuditStore(get_settings().DATABASE_URL)
        except Exception:
            return None
    return _db_audit_store


def _persist(event: AuditEvent) -> None:
    """Best-effort INSERT в audit_log: любой сбой БД не роняет вызов append."""
    try:
        store = _get_db_store()
        if store is not None:
            store.append(event)
    except Exception:
        pass


# Общий логгер процесса: память + персистентность в audit_log (без смены вызовов).
audit_log = AuditLogger()
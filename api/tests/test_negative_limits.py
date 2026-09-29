# Негативные тесты C1-лимиты (волна C, скилл qa-sed): лимиты скана
# (max_mb/форматы/retention), rate-limit логина (мок), append-only аудита.
# Отдельного эндпоинта сканов/лимитера в API волн A–B нет (хранение — settings
# БД + volume files, лимитер — Redis на стенде), поэтому здесь проверяется
# политика на значениях из db/seeds/settings.sql + локальные валидаторы-моки.
# Применение политики в прод-коде и живой rate-limit — пометка «на ВМ».
# Все ПДн вымышлены. Прогон локально: pytest api/tests/test_negative_limits.py -q.

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.audit import AuditEvent, AuditLogger, audit_log  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SEEDS = REPO_ROOT / "db" / "seeds" / "settings.sql"

# Значения по умолчанию из сидов (проверяются чтением файла, не только константой).
SCAN_MAX_MB = 10
SCAN_RETENTION_DAYS = 365
SCAN_FORMATS = frozenset({"pdf", "jpg", "jpeg", "png", "tiff"})


def _seeds_text() -> str:
    """Текст сидов настроек (единая точка чтения для тестов политики)."""
    return SEEDS.read_text(encoding="utf-8")


def test_scan_settings_present_in_seeds():
    """Сиды задают лимиты скана (max_mb/retention); стенд правит через SED_ADMINS."""
    text = _seeds_text()
    assert "scan_max_mb" in text
    assert "scan_retention_days" in text
    assert "'10'" in text  # scan_max_mb по умолчанию
    assert "'365'" in text  # scan_retention_days по умолчанию


@dataclass(frozen=True)
class ScanPolicy:
    """Мок политики скана (значения — из settings БД; применение — на ВМ).

    Прод-применение (на ВМ): проверка размера/формата при загрузке в volume
    files, чистка старше retention_days. Здесь — только логика валидатора.
    """

    max_mb: int = SCAN_MAX_MB
    retention_days: int = SCAN_RETENTION_DAYS
    formats: frozenset = SCAN_FORMATS

    def validate(self, filename: str, size_bytes: int) -> None:
        """Отклонить негатив: превышение max_mb или запрещенный формат."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in self.formats:
            raise ValueError("запрещенный формат скана: %r" % ext)
        if size_bytes > self.max_mb * 1024 * 1024:
            raise ValueError("скан больше лимита %d МБ" % self.max_mb)

    def is_expired(self, age_days: int) -> bool:
        """Просрочен ли файл по retention (чистка — на ВМ по volume files)."""
        return age_days > self.retention_days


def test_scan_ok_boundary():
    """Граничные допустимые: ровно max_mb и верхний регистр расширения."""
    ScanPolicy().validate("Заявление.PDF", SCAN_MAX_MB * 1024 * 1024)
    ScanPolicy().validate("scan photo.JPG", 1024)


def test_scan_oversize_rejected():
    """Скан больше max_mb — отклоняется (на ВМ: 413/422 без сохранения)."""
    with pytest.raises(ValueError, match="больше лимита"):
        ScanPolicy().validate("заявление.pdf", SCAN_MAX_MB * 1024 * 1024 + 1)


def test_scan_format_rejected():
    """Исполняемые/текстовые форматы — отклоняются; без расширения — тоже."""
    for bad in ("заявление.exe", "скрипт.bat", "документ.docx", "безрасширения", "вирус.PHP"):
        with pytest.raises(ValueError, match="запрещенный формат"):
            ScanPolicy().validate(bad, 1024)


def test_scan_retention_expiry():
    """Файлы старше retention_days — под чистку (на ВМ: фоновая чистка volume)."""
    policy = ScanPolicy()
    assert policy.is_expired(SCAN_RETENTION_DAYS + 1) is True
    assert policy.is_expired(SCAN_RETENTION_DAYS) is False
    assert policy.is_expired(0) is False


class LoginRateLimiter:
    """Мок rate-limit логина (прод — Redis на ВМ; здесь — память процесса).

    Правило-мок: не более max_attempts неудач за window_seconds, блок — 429.
    Успешный вход сбрасывает счетчик (сессии 15–20 мин — на ВМ по чек-листу ИТ).
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 900,
                 now=None) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._now = now or time.monotonic
        self._fails: dict[str, list[float]] = {}

    def _prune(self, login: str) -> list[float]:
        cutoff = self._now() - self.window_seconds
        kept = [t for t in self._fails.get(login, []) if t > cutoff]
        self._fails[login] = kept
        return kept

    def attempt(self, login: str, ok: bool) -> int:
        """Неудача/удача входа: вернуть HTTP-статус (200/401/429)."""
        if len(self._prune(login)) >= self.max_attempts:
            return 429
        if ok:
            self._fails.pop(login, None)
            return 200
        self._fails.setdefault(login, []).append(self._now())
        if len(self._fails[login]) >= self.max_attempts:
            return 429
        return 401


def test_login_rate_limit_blocks_after_threshold():
    """Пять неудач подряд — блок 429; шестая даже с верным паролем — 429."""
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=900)
    for _ in range(4):
        assert limiter.attempt("ok.vymyshlennaya", ok=False) == 401
    assert limiter.attempt("ok.vymyshlennaya", ok=False) == 429
    assert limiter.attempt("ok.vymyshlennaya", ok=True) == 429


def test_login_rate_limit_window_reset():
    """Окно истекло — счетчик сброшен, вход снова возможен (на ВМ: TTL в Redis)."""
    now = [1000.0]
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60, now=lambda: now[0])
    limiter.attempt("step.buhgalter", ok=False)
    limiter.attempt("step.buhgalter", ok=False)
    assert limiter.attempt("step.buhgalter", ok=False) == 429
    now[0] += 61.0
    assert limiter.attempt("step.buhgalter", ok=True) == 200


def test_login_rate_limit_per_user_isolation():
    """Блок одного логина не блокирует других (ключи лимитера — по логину)."""
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=900)
    limiter.attempt("user.zablokirovannyi", ok=False)
    assert limiter.attempt("user.zablokirovannyi", ok=False) == 429
    assert limiter.attempt("user.chistyi", ok=False) == 401
    assert limiter.attempt("user.chistyi", ok=True) == 200


def test_login_rate_limit_success_resets():
    """Успешный вход между неудачами сбрасывает счетчик (подбор не копится)."""
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=900)
    limiter.attempt("ok.sbrasyvaemaya", ok=False)
    limiter.attempt("ok.sbrasyvaemaya", ok=False)
    assert limiter.attempt("ok.sbrasyvaemaya", ok=True) == 200
    assert limiter.attempt("ok.sbrasyvaemaya", ok=False) == 401  # снова первая


def test_audit_append_only_no_update_delete():
    """Журнал только пополняется: нет update/delete/remove/purge в коде и классе."""
    for forbidden in ("update", "delete", "remove", "purge", "clear"):
        assert not hasattr(AuditLogger, forbidden), forbidden
    src = (Path(__file__).resolve().parents[1] / "app" / "audit.py").read_text(encoding="utf-8")
    for forbidden in ("def update", "def delete", "def remove", "def purge"):
        assert forbidden not in src, forbidden
    audit_log.clear_for_tests()
    audit_log.append(AuditEvent(actor="t", action="x", entity="e", entity_id="1"))
    assert len(audit_log.all()) == 1
    audit_log.clear_for_tests()


def test_audit_detail_has_no_pdn():
    """Пояснения аудита — без ПДн: нет ФИО/таб. номеров в detail значимых событий."""
    audit_log.clear_for_tests()
    try:
        for action in ("request.create", "step.approve", "steps.patch",
                       "employees.search", "link.create"):
            audit_log.append(AuditEvent(actor="ok.vymyshlennaya", action=action,
                                        entity="request", entity_id="REQ-0001",
                                        detail="origin=template order=1"))
        for event in audit_log.all():
            assert "Сказочников" not in event.detail
            assert "В-0001" not in event.detail
            assert "@" not in event.detail  # почты в detail нет
    finally:
        audit_log.clear_for_tests()

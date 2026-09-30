# Синхронизация справочника предприятий из 1С (Фаза 4).
# Источник — отдельный OData-эндпоинт, контракт уточнит ИТ; механизм готов,
# до контракта — понятная ошибка «не настроено». Синхронизация: еженедельно
# (worker, maybe_sync_weekly) и принудительно (эндпоинт POST /settings/
# enterprises/sync, кнопка в админке). Только чтение из 1С (HTTP GET),
# запись — только в settings (enterprises + onec_enterprises_synced_at).

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx

from .settings_routes import read_setting_value


class OnecSyncUnavailable(Exception):
    """Источник предприятий 1С не настроен или недоступен (503/409, не 500)."""


def http_get(url: str, user: str, password: str, timeout: float = 15.0) -> str:
    """GET источника предприятий: basic auth при заданном user, тело как текст."""
    headers = {}
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _parse_enterprises(payload: object) -> list[dict]:
    """Нормализация ответа источника: список [{"code","name"},...].

    Допускается и конверт {"enterprises": [...]}; записи без code пропускаются;
    битый формат — OnecSyncUnavailable (не 500)."""
    data = payload
    if isinstance(data, dict):
        data = data.get("enterprises")
    if not isinstance(data, list):
        raise OnecSyncUnavailable("Неожиданный формат ответа источника предприятий 1С")
    items = []
    for item in data:
        if not isinstance(item, dict) or not item.get("code"):
            continue
        items.append({"code": str(item["code"]), "name": str(item.get("name") or "")})
    return items


def sync_enterprises(store) -> list[dict]:
    """Прочитать предприятия из источника 1С и записать в settings.

    Источник — settings.onec_enterprises_source (JSON: {url, user, password});
    без url — OnecSyncUnavailable. Результат пишется в settings.enterprises
    и settings.onec_enterprises_synced_at (now ISO). Возвращает список.
    """
    source = read_setting_value(store, "onec_enterprises_source")
    url = ""
    user = ""
    password = ""
    if isinstance(source, dict):
        url = str(source.get("url") or "").strip()
        user = str(source.get("user") or "")
        password = str(source.get("password") or "")
    if not url:
        raise OnecSyncUnavailable("Источник предприятий 1С не настроен")
    try:
        text = http_get(url, user, password)
        parsed = json.loads(text)
    except OnecSyncUnavailable:
        raise
    except Exception as exc:
        raise OnecSyncUnavailable(str(exc)) from exc
    items = _parse_enterprises(parsed)
    store.set("enterprises", json.dumps(items, ensure_ascii=False))
    store.set(
        "onec_enterprises_synced_at",
        json.dumps(datetime.now(timezone.utc).isoformat()),
    )
    return items


def maybe_sync_weekly(store) -> bool:
    """Еженедельная синхронизация предприятий (worker): тихо, без сбоев.

    Источник не настроен — False; последняя синхронизация свежая (< 7 дней) —
    False; иначе sync_enterprises (сбой не валит worker — False), успех — True."""
    source = read_setting_value(store, "onec_enterprises_source")
    if not (isinstance(source, dict) and str(source.get("url") or "").strip()):
        return False
    last_raw = read_setting_value(store, "onec_enterprises_synced_at")
    if isinstance(last_raw, str) and last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if datetime.now(timezone.utc) - last < timedelta(days=7):
                return False
        except ValueError:
            pass  # битое значение — считаем, что синхронизации не было
    try:
        sync_enterprises(store)
        return True
    except Exception:
        return False
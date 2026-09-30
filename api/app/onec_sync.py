# Синхронизация справочника предприятий из 1С (Фаза C1).
# Список предприятий берётся ИЗ КАЖДОЙ БАЗЫ 1С (сущность организаций базы,
# в базе может быть несколько предприятий — ЗУП 3.х, справочник «Организации»).
# Синхронизация: еженедельно (worker, maybe_sync_weekly) и принудительно
# (POST /settings/enterprises/sync, кнопка в админке). Только чтение из 1С
# (HTTP GET), запись — только в settings (enterprises + onec_enterprise_bases +
# onec_enterprises_synced_at).

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import httpx

from .onec_client import build_entity_url, parse_collection
from .settings_routes import read_setting_value


class OnecSyncUnavailable(Exception):
    """Базы 1С не настроены или недоступны (503, не 500)."""


def http_get(url: str, user: str, password: str, timeout: float = 15.0) -> str:
    """GET источника предприятий: basic auth при заданном user, тело как текст."""
    headers = {}
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _organization(item: dict) -> tuple[str, str]:
    """Код/название организации из записи OData (Code/Description)."""
    code = str(item.get("Code") or item.get("code") or "").strip()
    name = str(item.get("Description") or item.get("name") or "").strip()
    return code, name


def sync_enterprises(store) -> list[dict]:
    """Опрос всех баз 1С: собрать предприятия (union) и маппинг предприятие→базы.

    Базы — settings.onec_bases (в каждой — OData-URL и сущность организаций).
    Результат пишется в settings.enterprises, settings.onec_enterprise_bases
    и settings.onec_enterprises_synced_at. Падение одной базы не валит остальные;
    если не ответила НИ ОДНА — OnecSyncUnavailable.
    """
    bases = read_setting_value(store, "onec_bases")
    if not isinstance(bases, list) or not bases:
        raise OnecSyncUnavailable("Базы 1С не настроены")
    enterprises: dict[str, dict] = {}
    mapping: dict[str, list[str]] = {}
    errors: list[str] = []
    ok = False
    for base in bases:
        if not isinstance(base, dict) or not base.get("url") or not base.get("code"):
            continue
        base_code = str(base["code"])
        entity = str(base.get("organization_entity") or "Catalog_Организации")
        url = build_entity_url(str(base["url"]), entity)
        try:
            text = http_get(url, str(base.get("user") or ""), str(base.get("password") or ""))
            items = parse_collection(text)
        except Exception as exc:  # падение одной базы — не валит остальные
            errors.append("%s: %s" % (base_code, exc))
            continue
        ok = True
        for item in items:
            code, name = _organization(item)
            if not code:
                continue
            if code not in enterprises:
                enterprises[code] = {"code": code, "name": name}
            if base_code not in mapping.setdefault(code, []):
                mapping[code].append(base_code)
    if not ok:
        raise OnecSyncUnavailable(
            "Ни одна база 1С не ответила: " + "; ".join(errors or ["баз нет"])
        )
    store.set("enterprises", json.dumps(list(enterprises.values()), ensure_ascii=False))
    store.set("onec_enterprise_bases", json.dumps(mapping, ensure_ascii=False))
    store.set(
        "onec_enterprises_synced_at",
        json.dumps(datetime.now(timezone.utc).isoformat()),
    )
    return list(enterprises.values())


def maybe_sync_weekly(store) -> bool:
    """Еженедельная синхронизация предприятий (worker): тихо, без сбоев.

    Базы не настроены — False; последняя синхронизация свежая (< 7 дней) —
    False; иначе sync_enterprises (сбой не валит worker — False), успех — True."""
    bases = read_setting_value(store, "onec_bases")
    if not isinstance(bases, list) or not bases:
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
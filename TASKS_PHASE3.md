# Фаза 3 — backend-1c: кэш Redis карточек 1С + боевая сборка клиента

> Статус (2026-09-30): ВЫПОЛНЕНА — `api/app/onec_cache.py` (CardCache/RedisCardCache/
> CachingOneCClient), `config.ONEC_CACHE_TTL=300`, `get_onec_client` с кэшем Redis.
> На стенде: данные 1С (`ONEC_BASES_JSON`) НЕ заданы — карточки/поиск из 1С ждут ИТ.

Цель (README п.6 Фаза 3): закрыть недостающий пункт приёмки — «кэш Redis».
Готово офлайн-волной A3: `OneCClient` (per-base GET, таймаут 5с, circuit-breaker),
`resolver` (предприятие→база→сотрудник, изоляция падения баз), составной ключ
`enterprise+base_code+tab_num`, снапшот. `get_onec_client` в `employees.py` —
заглушка 503 (офлайн подмена мок-транспортом).

База: README п.2/п.3/п.6, скил `onec-multibase`, `temp/OFFLINE_GAPS.md` GAP-03.
Запись в 1С запрещена (только GET).

## Контракт (согласован, не менять)

### Модуль `api/app/onec_cache.py` (новый)
- `class CardCache(Protocol)`:
  - `get(key: str) -> Optional[EmployeeCard]`
  - `set(key: str, card: EmployeeCard, ttl_seconds: int) -> None`
  - Обе операции СТРАХУЮТСЯ: любое исключение (RedisError и пр.) — промах/нет-оп
    (`get`→None, `set`→no-op). Кэш никогда не должен валить чтение 1С.
- `class RedisCardCache`:
  - `__init__(self, redis_url: str, ttl_seconds: int, redis_client=None, prefix: str = "sed:onec:card:")`;
    `redis_client` — инъекция для тестов (по умолчанию `redis.from_url(redis_url, decode_responses=True)`).
  - `get(key)`: ключ = `prefix + key`; JSON → `EmployeeCard(**data)`; ошибки/None → None.
  - `set(key, card, ttl_seconds=None)`: `dataclasses.asdict(card)` → `json.dumps(ensure_ascii=False)`;
    TTL — аргумент либо `self._ttl_seconds`.
- `class CachingOneCClient` (обёртка, публичный интерфейс как у `OneCClient`):
  - `__init__(self, client: OneCClient, cache: CardCache, ttl_seconds: int | None = None)`
  - Методы: `base_codes`, `bases_for_enterprise`, `circuit_is_open`, `get_employee(base_code, tab_num)`, `search(base_code, query)`.
  - `get_employee`: кэш-ключ = `f"{base_code}|{tab_num}"` (коды баз уникальны в
    конфигурации, составной ключ карточки enterprise|base|tab хранится в значении
    и возвращается как `card.key()`); попадание → вернуть карточку без сети;
    промах → `client.get_employee` → успех записать в кэш.
  - НЕ кэшируются: `search` (поиск не кэшируем), ошибки (сеть/таймаут/circuit/404).
  - `OneCClient` НЕ менять (офлайн-тесты не трогаем).

### Настройки (env)
- `api/app/config.py`: поле `ONEC_CACHE_TTL: int = Field(default=300, ...)` (сек).
- `.env.example`: `ONEC_CACHE_TTL=300` (в секцию «1С ЗУП»).

### Боевая сборка клиента — `api/app/employees.py`
- `get_onec_client(settings: Settings = Depends(get_settings)) -> OneCClient`:
  - если `load_bases_from_env()` (читает `os.environ` `ONEC_BASES_JSON`) пуст →
    прежний `HTTPException 503` (офлайн-тесты не ломаются);
  - иначе: `client = OneCClient(bases)`, `cache = RedisCardCache(settings.REDIS_URL, settings.ONEC_CACHE_TTL)`,
    вернуть `CachingOneCClient(client, cache, ttl_seconds=settings.ONEC_CACHE_TTL)`.
  - Сигнатура вызова зависимостью не меняется (`Depends(get_onec_client)`).

### Тесты — `api/tests/test_onec_cache.py` (новый, по образцу `test_onec_resolver.py`)
- `CachingOneCClient` (FakeTransport из `test_onec_resolver`-стиля): первый
  `get_employee` — сеть + запись в кэш; второй — попадание, сеть НЕ вызывается;
  404/ошибка сети НЕ кэшируются (повторный вызов снова идёт в сеть);
  `search` дважды — сеть дважды; `circuit_is_open` пробрасывается.
- `RedisCardCache` с фейковым redis-клиентом (без сети): round-trip
  сериализации `EmployeeCard`; TTL передаётся в `set`; при исключении redis —
  `get`→None, `set`→no-op.
- `get_onec_client` (monkeypatch env `ONEC_BASES_JSON`): пустой env → 503;
  непустой → возвращает `CachingOneCClient` (сеть не вызываем — только сборка).

## Запреты
- Только чтение 1С (GET). Хардкода предприятий/баз/URL нет (всё из env).
- Не трогать: `onec_client.py` (кроме импортов), `ad_reader.py`, `auth.py`,
  `deps.py`, контракты эндпоинтов, `resolver.py`.
- Коммиты — только по команде человека. Временное — только `./temp/`.
- Прогон: `python -m pytest api/tests -q` → все зелёные (121 + новые).

## На стенд (данные ИТ — вопросы человеку)
- `ONEC_BASES_JSON` на ВМ (предприятие→база→OData url+user+secret), живой
  OData-контракт (точные пути сущностей), таймаут 5с и падение одной базы не
  валит остальные — на живых базах; дубли ФИО — ручная сверка ОК (ASK-05).
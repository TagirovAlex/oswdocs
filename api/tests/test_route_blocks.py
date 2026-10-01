# Чистые юнит-тесты маршрута блоками (последовательный/параллельный) — БЕЗ БД.
# Кодирование блоков в step_order (колонка уже есть), параллельность персистентна
# без смены схемы. Все ПДн вымышленные; БД/HTTP не подключаются.

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.requests import (  # noqa: E402
    STEP_APPROVED,
    STEP_PENDING,
    RouteBlockSpec,
    StepSpec,
    _Request,
    _Step,
    _block_info,
    _build_steps,
    _current_pending_steps,
    _order_for,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _spec(owner_group="SED_STEP_BUH", **kw) -> StepSpec:
    return StepSpec(owner_group=owner_group, **kw)


def _block(mode="sequential", owners=("SED_STEP_BUH", "SED_STEP_HR")) -> RouteBlockSpec:
    return RouteBlockSpec(mode=mode, steps=[_spec(g) for g in owners])


def _request_with(*blocks: RouteBlockSpec) -> _Request:
    steps = _build_steps([], 3, None, NOW, blocks=list(blocks))
    return _Request(
        id="r1",
        enterprise="Предприятие-Тест",
        fio="Иванов Иван Иванович",
        tab_num="001",
        department="Служба тестовая",
        position="Тестировщик",
        created_by="ok.ivnova",
        steps=steps,
    )


def _approve(request: _Request, order: int) -> None:
    step = next(s for s in request.steps if s.order == order)
    step.status = STEP_APPROVED


# --- Кодирование блока/режима в step_order ---

def test_order_for_sequential():
    assert _order_for(0, 1, False) == 1
    assert _order_for(0, 3, False) == 3


def test_order_for_parallel():
    assert _order_for(0, 1, True) == 101
    assert _order_for(0, 2, True) == 102


def test_order_for_second_block():
    assert _order_for(1, 1, True) == 1101
    assert _order_for(1, 2, False) == 1002


def test_block_info_roundtrip():
    # Последовательный блок 0.
    assert _block_info(1) == (0, False, 1)
    assert _block_info(3) == (0, False, 3)
    # Параллельный блок 0.
    assert _block_info(101) == (0, True, 1)
    assert _block_info(102) == (0, True, 2)
    # Второй блок (последовательный и параллельный).
    assert _block_info(1002) == (1, False, 2)
    assert _block_info(1101) == (1, True, 1)


# --- Сборка шагов из блоков ---

def test_build_steps_sequential_block_orders():
    request = _build_steps([], 3, None, NOW, blocks=[_block("sequential", owners=("A", "B", "C"))])
    assert [s.order for s in request] == [1, 2, 3]


def test_build_steps_parallel_block_orders():
    request = _build_steps([], 3, None, NOW, blocks=[_block("parallel", owners=("A", "B"))])
    assert [s.order for s in request] == [101, 102]


def test_build_steps_two_blocks_orders():
    request = _build_steps(
        [],
        3,
        None,
        NOW,
        blocks=[
            _block("sequential", owners=("A", "B", "C")),
            _block("parallel", owners=("D", "E")),
        ],
    )
    assert [s.order for s in request] == [1, 2, 3, 1101, 1102]
    # Режим/позиция восстанавливаются из order.
    assert _block_info(request[3].order) == (1, True, 1)


def test_build_steps_sam_resolves_by_user():
    request = _build_steps(
        [],
        3,
        None,
        NOW,
        blocks=[
            RouteBlockSpec(
                mode="sequential",
                steps=[_spec("SED_STEP_BUH", sam="t.ivan", require_comment=True)],
            )
        ],
    )
    step = request[0]
    assert step.resolver == "by_user"
    assert step.assignee == "t.ivan"
    assert step.owner_group == "t.ivan"
    assert step.require_comment is True


def test_build_steps_sam_overrides_manager():
    # sam (выбор ОК) важнее подстановки ad_direct_manager из manager.
    request = _build_steps(
        [],
        3,
        "t.zam",
        NOW,
        blocks=[
            RouteBlockSpec(
                mode="sequential",
                steps=[_spec("SED_STEP_BUH", sam="t.ivan", resolver="ad_direct_manager")],
            )
        ],
    )
    step = request[0]
    assert step.resolver == "by_user"
    assert step.assignee == "t.ivan"
    assert step.owner_group == "t.ivan"


def test_build_steps_without_blocks_flat_orders():
    request = _build_steps(
        [_spec("A"), _spec("B"), _spec("C", resolver="ad_direct_manager")],
        3,
        "t.zam",
        NOW,
    )
    assert [s.order for s in request] == [1, 2, 3]
    assert request[2].assignee == "t.zam"
    assert request[2].resolver == "ad_direct_manager"


# --- Текущий блок: строгий порядок / параллельность ---

def test_pending_sequential_only_first():
    request = _request_with(_block("sequential", owners=("A", "B")))
    assert [s.order for s in _current_pending_steps(request)] == [1]


def test_pending_sequential_after_first_approved():
    request = _request_with(_block("sequential", owners=("A", "B")))
    _approve(request, 1)
    assert [s.order for s in _current_pending_steps(request)] == [2]


def test_pending_parallel_both():
    request = _request_with(_block("parallel", owners=("A", "B")))
    assert [s.order for s in _current_pending_steps(request)] == [101, 102]


def test_pending_parallel_after_one_approved():
    request = _request_with(_block("parallel", owners=("A", "B")))
    _approve(request, 101)
    # Второй шаг параллельного блока всё ещё в текущем блоке.
    assert [s.order for s in _current_pending_steps(request)] == [102]


def test_pending_block_done_next_block():
    request = _request_with(
        _block("sequential", owners=("A", "B")),
        _block("sequential", owners=("C",)),
    )
    _approve(request, 1)
    _approve(request, 2)
    assert [s.order for s in _current_pending_steps(request)] == [1001]


def test_pending_all_done_empty():
    request = _request_with(_block("sequential", owners=("A",)))
    _approve(request, 1)
    assert _current_pending_steps(request) == []


def test_pending_mixed_blocks_parallel_locked_to_block():
    # Параллельный блок не должен перескакивать на следующий, пока есть его шаги.
    request = _request_with(
        _block("parallel", owners=("A", "B")),
        _block("sequential", owners=("C",)),
    )
    _approve(request, 101)
    assert [s.order for s in _current_pending_steps(request)] == [102]
    _approve(request, 102)
    assert [s.order for s in _current_pending_steps(request)] == [1001]
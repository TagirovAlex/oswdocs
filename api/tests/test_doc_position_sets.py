# Подбор бланка по набору должностей: find_doc_template с position/position_sets.
# Чистые функции docs.py — без БД/40x, данные вымышленные.
from __future__ import annotations

from app.docs import _set_positions, find_doc_template

SETS = [
    {"name": "Руководители", "positions": ["Директор", "Главный бухгалтер"]},
    {"name": "Линейные", "positions": ["Слесарь", "грузчик"]},
]

TPL_OFFICE = {"service": "Цех", "category": "линейный", "body": "офис",
              "position_set": "Руководители"}
TPL_LINE = {"service": "Цех", "category": "линейный", "body": "линия",
            "position_set": "Линейные"}
TPL_DEFAULT = {"service": "Цех", "category": "линейный", "body": "дефолт"}
TPL_OTHER = {"service": "Офис", "category": "линейный", "body": "чужой"}


def test_set_positions_normalizes():
    """Должности набора нормализуются (регистр/пробелы), пустые отбрасываются."""
    assert _set_positions(SETS, "Руководители") == ["директор", "главный бухгалтер"]
    assert _set_positions(SETS, "  линейные ") == ["слесарь", "грузчик"]
    assert _set_positions(SETS, "Нет такого") == []
    assert _set_positions("не список", "Руководители") == []
    assert _set_positions(SETS, "") == []


def test_match_by_position_set():
    """Должность из набора — бланк набора (в любом регистре)."""
    templates = [TPL_DEFAULT, TPL_LINE, TPL_OFFICE]
    found = find_doc_template(templates, "Цех", "линейный", "ГРУЗЧИК", SETS)
    assert found is not None and found["body"] == "линия"
    found = find_doc_template(templates, "Цех", "линейный", "директор", SETS)
    assert found is not None and found["body"] == "офис"


def test_fallback_to_default_blank():
    """Должность ни в одном наборе — бланк без набора (по умолчанию)."""
    templates = [TPL_LINE, TPL_DEFAULT]
    found = find_doc_template(templates, "Цех", "линейный", "Сторож", SETS)
    assert found is not None and found["body"] == "дефолт"


def test_no_default_no_match():
    """Все бланки с наборами, должность нигде — None (ручной конструктор)."""
    assert find_doc_template([TPL_LINE, TPL_OFFICE], "Цех", "линейный", "Сторож", SETS) is None


def test_legacy_behavior_without_positions():
    """Без должности/наборов — прежнее поведение (первый подходящий)."""
    assert find_doc_template([TPL_LINE, TPL_DEFAULT], "Цех", "линейный")["body"] == "линия"
    assert find_doc_template([TPL_OTHER], "Цех", "линейный") is None
    assert find_doc_template("не список", "Цех", "линейный") is None


def test_wildcard_empty_service_category():
    """Пустые служба/категория в записи — wildcard (бланк только по набору)."""
    tpl_set_only = {"service": "", "category": "", "body": "по набору",
                    "position_set": "Линейные"}
    tpl_default = {"service": "", "category": "", "body": "дефолт"}
    # Ручная заявка без категории: подбор идёт по набору.
    found = find_doc_template([tpl_set_only, tpl_default], "Цех № 1", "",
                              "Грузчик", SETS)
    assert found is not None and found["body"] == "по набору"
    # Должность вне наборов — дефолтный.
    found = find_doc_template([tpl_set_only, tpl_default], "Цех № 1", "",
                              "Сторож", SETS)
    assert found is not None and found["body"] == "дефолт"
    # Запись с заполненной службой чужому цеху не подходит.
    tpl_other = {"service": "Офис", "category": "", "body": "чужой"}
    assert find_doc_template([tpl_other], "Цех № 1", "", "Грузчик", SETS) is None

from decimal import Decimal
from pathlib import Path

import pytest

from src.infrastructure.excel_loader import (
    load_pantry,
    parse_package_unit,
    to_decimal,
)


XLSX_PATH = Path("data/despensa_dona_maria.xlsx")


def test_to_decimal_from_int() -> None:
    assert to_decimal(10) == Decimal("10")


def test_to_decimal_from_float() -> None:
    assert to_decimal(1.5) == Decimal("1.5")


def test_to_decimal_from_decimal() -> None:
    assert to_decimal(Decimal("3.14")) == Decimal("3.14")


def test_to_decimal_from_brazilian_string() -> None:
    assert to_decimal("1.234,56") == Decimal("1234.56")


def test_to_decimal_from_currency_string() -> None:
    assert to_decimal("R$ 24,90") == Decimal("24.90")


def test_to_decimal_none_raises() -> None:
    with pytest.raises(ValueError, match="Expected numeric"):
        to_decimal(None)


def test_parse_package_unit_simple() -> None:
    assert parse_package_unit("kg") is None
    assert parse_package_unit("L") is None
    assert parse_package_unit("un") is None


def test_parse_package_unit_compound() -> None:
    package = parse_package_unit("un 500g")
    assert package is not None
    assert package.container == "un"
    assert package.content_quantity == Decimal("500")
    assert package.content_unit == "g"


def test_parse_package_unit_liter() -> None:
    package = parse_package_unit("un 500ml")
    assert package is not None
    assert package.content_unit == "ml"


def test_parse_package_unit_balde() -> None:
    package = parse_package_unit("balde 2kg")
    assert package is not None
    assert package.container == "balde"
    assert package.content_quantity == Decimal("2")
    assert package.content_unit == "kg"


@pytest.mark.skipif(
    not XLSX_PATH.exists(),
    reason="Excel file not present",
)
def test_load_pantry_from_real_file() -> None:
    pantry = load_pantry(XLSX_PATH)
    assert len(pantry.items) > 0
    for item in pantry.items:
        assert item.ingredient.strip() != ""
        assert item.stock.value >= 0


@pytest.mark.skipif(
    not XLSX_PATH.exists(),
    reason="Excel file not present",
)
def test_load_pantry_has_purchase_info() -> None:
    pantry = load_pantry(XLSX_PATH)
    items_with_purchase = [
        item for item in pantry.items if item.purchase is not None
    ]
    assert len(items_with_purchase) > 0
    for item in items_with_purchase:
        assert item.purchase.total_paid >= 0
        assert item.purchase.quantity.value > 0


def test_load_pantry_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_pantry("nonexistent.xlsx")

from decimal import Decimal

import pytest

from src.services.pricing_service import (
    InvalidCMV,
    generate_price_scenarios,
)


def test_minimum_price_accounts_for_platform_fee() -> None:
    result = generate_price_scenarios(Decimal("9.80"))

    assert result.minimum_price == Decimal("10.89")
    assert result.minimum_price * result.net_rate >= Decimal("9.80")


def test_minimum_price_rounds_up_to_avoid_a_loss() -> None:
    result = generate_price_scenarios(Decimal("10.00"))

    assert result.minimum_price == Decimal("11.12")
    assert result.minimum_price * result.net_rate >= Decimal("10.00")


def test_generates_selected_profit_over_cmv_scenarios() -> None:
    result = generate_price_scenarios(Decimal("12.00"))

    assert [
        (scenario.label, scenario.markup_rate)
        for scenario in result.scenarios
    ] == [
        ("entrada", Decimal("0.20")),
        ("equilibrado", Decimal("0.40")),
        ("maior margem", Decimal("0.60")),
    ]
    assert [scenario.price for scenario in result.scenarios] == [
        Decimal("16.00"),
        Decimal("18.67"),
        Decimal("21.33"),
    ]
    assert [scenario.profit for scenario in result.scenarios] == [
        Decimal("2.40"),
        Decimal("4.80"),
        Decimal("7.20"),
    ]


def test_fee_net_revenue_and_profit_reconcile() -> None:
    cmv = Decimal("12.00")
    result = generate_price_scenarios(cmv)

    for scenario in result.scenarios:
        assert scenario.platform_fee + scenario.net_revenue == scenario.price
        assert scenario.profit == scenario.net_revenue - cmv


def test_zero_cmv_produces_zero_prices() -> None:
    result = generate_price_scenarios(Decimal("0"))

    assert result.minimum_price == Decimal("0.00")
    assert all(
        scenario.price == Decimal("0.00")
        and scenario.profit == Decimal("0.00")
        for scenario in result.scenarios
    )


def test_negative_cmv_fails() -> None:
    with pytest.raises(InvalidCMV, match="must not be negative"):
        generate_price_scenarios(Decimal("-0.01"))


def test_money_serializes_with_two_decimal_places() -> None:
    result = generate_price_scenarios(Decimal("10.005"))
    serialized = result.model_dump(mode="json")

    assert serialized["cmv_per_serving"] == "10.01"
    assert serialized["minimum_price"] == "11.12"
    assert all(
        len(scenario["price"].split(".")[1]) == 2
        for scenario in serialized["scenarios"]
    )


def test_all_financial_values_remain_decimal() -> None:
    result = generate_price_scenarios(Decimal("10.01"))

    assert isinstance(result.minimum_price, Decimal)
    for scenario in result.scenarios:
        assert isinstance(scenario.price, Decimal)
        assert isinstance(scenario.platform_fee, Decimal)
        assert isinstance(scenario.net_revenue, Decimal)
        assert isinstance(scenario.profit, Decimal)

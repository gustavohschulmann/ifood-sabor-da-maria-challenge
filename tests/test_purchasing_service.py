from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.feasibility import MissingIngredient
from src.domain.pantry import Pantry
from src.domain.purchasing import MarketQuote
from src.domain.recipe import Recipe, RecipeIngredient
from src.services.costing_service import calculate_recipe_cmv
from src.services.purchasing_service import (
    DuplicateMarketQuote,
    IncompatiblePurchaseUnits,
    InsufficientBudget,
    InvalidPurchaseRequirement,
    MissingMarketQuote,
    plan_complementary_purchases,
)


def missing(
    ingredient: str,
    quantity: str,
    unit: str,
) -> MissingIngredient:
    return MissingIngredient(
        ingredient=ingredient,
        quantity=Decimal(quantity),
        unit=unit,
    )


def quote(
    ingredient: str,
    package_quantity: str,
    unit: str,
    package_price: str,
    *,
    quote_id: str | None = None,
) -> MarketQuote:
    return MarketQuote(
        quote_id=quote_id or f"{ingredient}-quote",
        ingredient=ingredient,
        package_quantity=Decimal(package_quantity),
        unit=unit,
        package_price=Decimal(package_price),
        source_url=f"https://example.com/{ingredient}",
    )


def test_cash_outlay_is_distinct_from_allocated_recipe_cost() -> None:
    plan = plan_complementary_purchases(
        [missing("Champignon", "100", "g")],
        [quote("Champignon", "200", "g", "10.00")],
    )

    requirement = plan.requirements[0]
    assert requirement.packages_required == 1
    assert requirement.cash_outlay == Decimal("10.00")
    assert requirement.allocated_recipe_cost == Decimal("5.00")
    assert plan.cash_outlay == Decimal("10.00")
    assert plan.remaining_budget == Decimal("70.00")


def test_exact_package_requirement_uses_one_package() -> None:
    plan = plan_complementary_purchases(
        [missing("Champignon", "200", "g")],
        [quote("Champignon", "200", "g", "10.00")],
    )

    requirement = plan.requirements[0]
    assert requirement.packages_required == 1
    assert requirement.cash_outlay == Decimal("10.00")
    assert requirement.allocated_recipe_cost == Decimal("10.00")


def test_partial_second_package_is_purchased_but_only_usage_is_costed() -> None:
    plan = plan_complementary_purchases(
        [missing("Champignon", "300", "g")],
        [quote("Champignon", "200", "g", "10.00")],
    )

    requirement = plan.requirements[0]
    purchase = plan.complementary_purchases[0]
    assert requirement.packages_required == 2
    assert requirement.cash_outlay == Decimal("20.00")
    assert requirement.allocated_recipe_cost == Decimal("15.00")
    assert purchase.purchased_quantity == Decimal("400")
    assert purchase.package_price == Decimal("20.00")


@pytest.mark.parametrize(
    (
        "required_quantity",
        "required_unit",
        "package_quantity",
        "package_unit",
        "expected_packages",
    ),
    [
        ("0.5", "kg", "200", "g", 3),
        ("500", "g", "1", "kg", 1),
        ("1.5", "L", "500", "ml", 3),
        ("750", "ml", "1", "L", 1),
    ],
)
def test_purchase_planning_converts_supported_units(
    required_quantity: str,
    required_unit: str,
    package_quantity: str,
    package_unit: str,
    expected_packages: int,
) -> None:
    plan = plan_complementary_purchases(
        [missing("Ingredient", required_quantity, required_unit)],
        [
            quote(
                "Ingredient",
                package_quantity,
                package_unit,
                "10.00",
            )
        ],
    )

    assert plan.requirements[0].packages_required == expected_packages


def test_multiple_ingredients_share_the_same_budget() -> None:
    plan = plan_complementary_purchases(
        [
            missing("Champignon", "100", "g"),
            missing("Cream", "250", "ml"),
        ],
        [
            quote("Champignon", "200", "g", "10.00"),
            quote("Cream", "200", "ml", "4.00"),
        ],
    )

    assert plan.cash_outlay == Decimal("18.00")
    assert plan.remaining_budget == Decimal("62.00")
    assert len(plan.requirements) == 2


def test_committed_purchases_reduce_available_budget() -> None:
    plan = plan_complementary_purchases(
        [missing("Champignon", "100", "g")],
        [quote("Champignon", "200", "g", "10.00")],
        committed_purchases=Decimal("60.00"),
    )

    assert plan.available_budget == Decimal("20.00")
    assert plan.remaining_budget == Decimal("10.00")


def test_purchase_can_use_exact_remaining_budget() -> None:
    plan = plan_complementary_purchases(
        [missing("Ingredient", "1", "un")],
        [quote("Ingredient", "1", "un", "80.00")],
    )

    assert plan.cash_outlay == Decimal("80.00")
    assert plan.remaining_budget == Decimal("0.00")


def test_purchase_over_remaining_budget_fails() -> None:
    with pytest.raises(InsufficientBudget, match="only R\\$5.00 remains"):
        plan_complementary_purchases(
            [missing("Champignon", "100", "g")],
            [quote("Champignon", "200", "g", "10.00")],
            committed_purchases=Decimal("75.00"),
        )


def test_committed_purchases_cannot_exceed_initial_budget() -> None:
    with pytest.raises(InsufficientBudget, match="initial budget"):
        plan_complementary_purchases(
            [],
            [],
            committed_purchases=Decimal("80.01"),
        )


def test_missing_market_quote_fails() -> None:
    with pytest.raises(MissingMarketQuote, match="Champignon"):
        plan_complementary_purchases(
            [missing("Champignon", "100", "g")],
            [],
        )


def test_duplicate_quotes_for_an_ingredient_fail() -> None:
    with pytest.raises(DuplicateMarketQuote, match="Multiple"):
        plan_complementary_purchases(
            [missing("Champignon", "100", "g")],
            [
                quote(
                    "Champignon",
                    "200",
                    "g",
                    "10.00",
                    quote_id="quote-a",
                ),
                quote(
                    "Champignon",
                    "300",
                    "g",
                    "12.00",
                    quote_id="quote-b",
                ),
            ],
        )


def test_non_positive_missing_quantity_fails() -> None:
    with pytest.raises(InvalidPurchaseRequirement, match="greater than zero"):
        plan_complementary_purchases(
            [missing("Champignon", "0", "g")],
            [quote("Champignon", "200", "g", "10.00")],
        )


def test_negative_committed_purchases_fail() -> None:
    with pytest.raises(InvalidPurchaseRequirement, match="must not be negative"):
        plan_complementary_purchases(
            [],
            [],
            committed_purchases=Decimal("-0.01"),
        )


def test_invalid_market_package_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="greater than zero"):
        quote("Champignon", "0", "g", "10.00")


def test_contextual_unit_conversion_fails() -> None:
    with pytest.raises(IncompatiblePurchaseUnits, match="Cannot convert"):
        plan_complementary_purchases(
            [missing("Flour", "1", "xícara")],
            [quote("Flour", "500", "g", "5.00")],
        )


def test_decimal_precision_is_preserved_until_json_output() -> None:
    plan = plan_complementary_purchases(
        [missing("Ingredient", "100", "g")],
        [quote("Ingredient", "300", "g", "10.00")],
    )

    assert plan.requirements[0].allocated_recipe_cost == (
        Decimal("100") * Decimal("10.00") / Decimal("300")
    )
    serialized = plan.model_dump(mode="json")
    assert serialized["requirements"][0]["allocated_recipe_cost"] == "3.33"
    assert serialized["cash_outlay"] == "10.00"


def test_purchase_plan_outputs_are_compatible_with_cmv_service() -> None:
    plan = plan_complementary_purchases(
        [missing("Champignon", "100", "g")],
        [quote("Champignon", "200", "g", "10.00")],
    )
    recipe = Recipe(
        id="mushrooms",
        name="Mushrooms",
        source_url="https://example.com/recipe",
        servings=1,
        ingredients=[
            RecipeIngredient(
                ingredient="Champignon",
                quantity=Decimal("100"),
                unit="g",
            )
        ],
        required_equipment=[],
        required_skills=[],
        operational_requirements=[],
    )

    cmv = calculate_recipe_cmv(
        recipe=recipe,
        pantry=Pantry(items=[]),
        ingredient_allocations=plan.ingredient_allocations,
        complementary_purchases=plan.complementary_purchases,
    )

    assert cmv.batch_cmv == Decimal("5.00")

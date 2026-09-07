from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.domain.costing import (
    ComplementaryPurchase,
    IngredientAllocation,
    IngredientCostSource,
)
from src.domain.pantry import (
    PackageInfo,
    Pantry,
    PantryItem,
    PurchaseInfo,
    Quantity,
)
from src.domain.recipe import Recipe, RecipeIngredient
from src.services.costing_service import (
    InvalidCostRecord,
    InvalidIngredientAllocation,
    InvalidRecipeYield,
    MissingCostInformation,
    calculate_recipe_cmv,
)
from src.utils.unit_conversion import convert_quantity


def make_recipe(
    ingredients: list[RecipeIngredient],
    *,
    servings: int = 1,
    name: str = "Test recipe",
) -> Recipe:
    return Recipe(
        id="test-recipe",
        name=name,
        source_url="https://example.com/recipe",
        servings=servings,
        ingredients=ingredients,
        required_equipment=[],
        required_skills=[],
        operational_requirements=[],
    )


def make_pantry_item(
    ingredient: str,
    *,
    purchased_quantity: str = "1",
    purchased_unit: str = "kg",
    total_paid: str = "1.00",
    package: PackageInfo | None = None,
    include_purchase: bool = True,
) -> PantryItem:
    purchase = (
        PurchaseInfo(
            quantity=Quantity(
                value=Decimal(purchased_quantity),
                unit=purchased_unit,
            ),
            total_paid=Decimal(total_paid),
            package=package,
        )
        if include_purchase
        else None
    )
    return PantryItem(
        ingredient=ingredient,
        stock=Quantity(
            value=Decimal(purchased_quantity),
            unit=purchased_unit,
        ),
        purchase=purchase,
        package=package,
    )


def pantry_allocation(
    recipe_ingredient: str,
    quantity: str,
    unit: str,
    pantry_ingredient: str | None = None,
) -> IngredientAllocation:
    return IngredientAllocation(
        recipe_ingredient=recipe_ingredient,
        source=IngredientCostSource.PANTRY,
        quantity=Decimal(quantity),
        unit=unit,
        pantry_ingredient=pantry_ingredient or recipe_ingredient,
    )


def purchase_allocation(
    recipe_ingredient: str,
    quantity: str,
    unit: str,
    purchase_id: str,
) -> IngredientAllocation:
    return IngredientAllocation(
        recipe_ingredient=recipe_ingredient,
        source=IngredientCostSource.COMPLEMENTARY_PURCHASE,
        quantity=Decimal(quantity),
        unit=unit,
        complementary_purchase_id=purchase_id,
    )


def test_historical_pantry_cost_converts_kg_to_g() -> None:
    recipe = make_recipe(
        [RecipeIngredient(ingredient="Chicken", quantity=Decimal("500"), unit="g")],
        servings=4,
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Chicken",
                purchased_quantity="2",
                purchased_unit="kg",
                total_paid="28.00",
            )
        ]
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [pantry_allocation("Chicken", "500", "g")],
        [],
    )

    component = result.ingredients[0].components[0]
    assert component.quantity_used == Decimal("0.500")
    assert component.unit == "kg"
    assert component.unit_cost == Decimal("14.00")
    assert component.total_cost == Decimal("7.00000")
    assert result.batch_cmv == Decimal("7.00000")
    assert result.cmv_per_serving == Decimal("1.75000")


@pytest.mark.parametrize(
    ("value", "from_unit", "to_unit", "expected"),
    [
        ("1", "kg", "g", "1000"),
        ("500", "g", "kg", "0.500"),
        ("1.5", "L", "ml", "1500.0"),
        ("250", "ml", "L", "0.250"),
        ("3", "un", "un", "3"),
    ],
)
def test_supported_unit_conversions(
    value: str,
    from_unit: str,
    to_unit: str,
    expected: str,
) -> None:
    assert convert_quantity(
        Decimal(value),
        from_unit,
        to_unit,
    ) == Decimal(expected)


def test_same_unit_countable_items() -> None:
    recipe = make_recipe(
        [RecipeIngredient(ingredient="Eggs", quantity=Decimal("3"), unit="un")]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Eggs",
                purchased_quantity="30",
                purchased_unit="un",
                total_paid="24.00",
            )
        ]
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [pantry_allocation("Eggs", "3", "un")],
        [],
    )

    assert result.ingredients[0].total_cost == Decimal("2.40")


def test_package_content_cost_is_generic() -> None:
    package = PackageInfo(
        container="un",
        content_quantity=Decimal("500"),
        content_unit="g",
    )
    recipe = make_recipe(
        [
            RecipeIngredient(
                ingredient="Whipped cream",
                quantity=Decimal("250"),
                unit="g",
            )
        ]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Whipped cream",
                purchased_quantity="1",
                purchased_unit="un 500g",
                total_paid="20.00",
                package=package,
            )
        ]
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [pantry_allocation("Whipped cream", "250", "g")],
        [],
    )

    component = result.ingredients[0].components[0]
    assert component.unit_cost == Decimal("0.04")
    assert component.total_cost == Decimal("10.00")


def test_complementary_purchase_costs_only_consumed_quantity() -> None:
    recipe = make_recipe(
        [
            RecipeIngredient(
                ingredient="Mushrooms",
                quantity=Decimal("100"),
                unit="g",
            )
        ]
    )
    purchase = ComplementaryPurchase(
        purchase_id="mushrooms-quote",
        ingredient="Mushrooms",
        purchased_quantity=Decimal("200"),
        purchased_unit="g",
        package_price=Decimal("10.00"),
    )

    result = calculate_recipe_cmv(
        recipe,
        Pantry(items=[]),
        [purchase_allocation("Mushrooms", "100", "g", "mushrooms-quote")],
        [purchase],
    )

    component = result.ingredients[0].components[0]
    assert purchase.package_price == Decimal("10.00")
    assert component.unit_cost == Decimal("0.05")
    assert component.total_cost == Decimal("5.00")


def test_mixed_pantry_and_complementary_purchase_allocations() -> None:
    recipe = make_recipe(
        [
            RecipeIngredient(
                ingredient="Chicken",
                quantity=Decimal("800"),
                unit="g",
            )
        ]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Chicken",
                purchased_quantity="2",
                purchased_unit="kg",
                total_paid="28.00",
            )
        ]
    )
    purchase = ComplementaryPurchase(
        purchase_id="extra-chicken",
        ingredient="Chicken",
        purchased_quantity=Decimal("1"),
        purchased_unit="kg",
        package_price=Decimal("20.00"),
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [
            pantry_allocation("Chicken", "500", "g"),
            purchase_allocation("Chicken", "300", "g", "extra-chicken"),
        ],
        [purchase],
    )

    components = result.ingredients[0].components
    assert [component.source for component in components] == [
        IngredientCostSource.PANTRY,
        IngredientCostSource.COMPLEMENTARY_PURCHASE,
    ]
    assert components[0].total_cost == Decimal("7.00000")
    assert components[1].total_cost == Decimal("6.00000")
    assert result.batch_cmv == Decimal("13.00000")


def test_batch_and_per_serving_cmv() -> None:
    recipe = make_recipe(
        [
            RecipeIngredient(
                ingredient="Chicken",
                quantity=Decimal("500"),
                unit="g",
            ),
            RecipeIngredient(
                ingredient="Onion",
                quantity=Decimal("200"),
                unit="g",
            ),
            RecipeIngredient(
                ingredient="Butter",
                quantity=Decimal("50"),
                unit="g",
            ),
        ],
        servings=4,
        name="Chicken stroganoff",
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Chicken",
                purchased_quantity="2",
                purchased_unit="kg",
                total_paid="28.00",
            ),
            make_pantry_item(
                "Onion",
                purchased_quantity="1",
                purchased_unit="kg",
                total_paid="4.00",
            ),
            make_pantry_item(
                "Butter",
                purchased_quantity="0.5",
                purchased_unit="kg",
                total_paid="20.00",
            ),
        ]
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [
            pantry_allocation("Chicken", "500", "g"),
            pantry_allocation("Onion", "200", "g"),
            pantry_allocation("Butter", "50", "g"),
        ],
        [],
    )

    assert [item.total_cost for item in result.ingredients] == [
        Decimal("7.00000"),
        Decimal("0.8000"),
        Decimal("2.0000"),
    ]
    assert result.batch_cmv == Decimal("9.80000")
    assert result.cmv_per_serving == Decimal("2.45000")


def test_missing_historical_cost_fails() -> None:
    recipe = make_recipe(
        [RecipeIngredient(ingredient="Salt", quantity=Decimal("10"), unit="g")]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Salt",
                purchased_unit="g",
                include_purchase=False,
            )
        ]
    )

    with pytest.raises(MissingCostInformation, match="historical cost"):
        calculate_recipe_cmv(
            recipe,
            pantry,
            [pantry_allocation("Salt", "10", "g")],
            [],
        )


def test_invalid_servings_fails() -> None:
    with pytest.raises(ValidationError, match="greater than zero"):
        make_recipe([], servings=0)


def test_negative_recipe_quantity_fails() -> None:
    with pytest.raises(ValidationError, match="greater than zero"):
        make_recipe(
            [
                RecipeIngredient(
                    ingredient="Chicken",
                    quantity=Decimal("-1"),
                    unit="g",
                )
            ]
        )


def test_negative_allocation_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be negative"):
        pantry_allocation("Chicken", "-1", "g")


def test_zero_purchased_quantity_fails_before_division() -> None:
    recipe = make_recipe(
        [RecipeIngredient(ingredient="Chicken", quantity=Decimal("1"), unit="g")]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Chicken",
                purchased_quantity="0",
                purchased_unit="g",
                total_paid="10.00",
            )
        ]
    )

    with pytest.raises(InvalidCostRecord, match="greater than zero"):
        calculate_recipe_cmv(
            recipe,
            pantry,
            [pantry_allocation("Chicken", "1", "g")],
            [],
        )


def test_unresolved_contextual_unit_skips_cost_gracefully() -> None:
    """When units are unconvertible, the ingredient contributes zero cost
    rather than crashing, so the workflow can proceed."""
    recipe = make_recipe(
        [
            RecipeIngredient(
                ingredient="Flour",
                quantity=Decimal("1"),
                unit="xícara",
            )
        ]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Flour",
                purchased_quantity="1",
                purchased_unit="kg",
                total_paid="5.00",
            )
        ]
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [pantry_allocation("Flour", "1", "xícara")],
        [],
    )
    assert result.batch_cmv == Decimal("0")
    assert len(result.ingredients) == 1


def test_allocations_must_exactly_cover_recipe_quantity() -> None:
    recipe = make_recipe(
        [RecipeIngredient(ingredient="Chicken", quantity=Decimal("800"), unit="g")]
    )

    with pytest.raises(InvalidIngredientAllocation, match="recipe requires"):
        calculate_recipe_cmv(
            recipe,
            Pantry(items=[]),
            [purchase_allocation("Chicken", "300", "g", "extra-chicken")],
            [],
        )


def test_decimal_precision_is_preserved_until_json_output() -> None:
    package = PackageInfo(
        container="un",
        content_quantity=Decimal("500"),
        content_unit="g",
    )
    recipe = make_recipe(
        [
            RecipeIngredient(
                ingredient="Whipped cream",
                quantity=Decimal("250"),
                unit="g",
            )
        ]
    )
    pantry = Pantry(
        items=[
            make_pantry_item(
                "Whipped cream",
                purchased_quantity="1",
                purchased_unit="un 500g",
                total_paid="23.67",
                package=package,
            )
        ]
    )

    result = calculate_recipe_cmv(
        recipe,
        pantry,
        [pantry_allocation("Whipped cream", "250", "g")],
        [],
    )

    component = result.ingredients[0].components[0]
    assert component.unit_cost == Decimal("0.04734")
    assert component.total_cost == Decimal("11.83500")
    assert result.batch_cmv == Decimal("11.83500")

    serialized = result.model_dump(mode="json")
    assert serialized["ingredients"][0]["components"][0]["unit_cost"] == "0.05"
    assert serialized["ingredients"][0]["components"][0]["total_cost"] == "11.84"
    assert serialized["ingredients"][0]["total_cost"] == "11.84"
    assert serialized["batch_cmv"] == "11.84"
    assert serialized["cmv_per_serving"] == "11.84"

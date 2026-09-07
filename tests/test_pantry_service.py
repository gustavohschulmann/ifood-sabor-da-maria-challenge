from decimal import Decimal

from src.domain.pantry import (
    PackageInfo,
    Pantry,
    PantryItem,
    PurchaseInfo,
    Quantity,
)
from src.domain.recipe import Recipe, RecipeIngredient
from src.services.pantry_service import compare_recipe_with_pantry


def _recipe(ingredients: list[RecipeIngredient]) -> Recipe:
    return Recipe(
        id="test",
        name="Test",
        source_url="https://example.com",
        servings=1,
        ingredients=ingredients,
        required_equipment=[],
        required_skills=[],
        operational_requirements=[],
    )


def _item(
    ingredient: str,
    stock_value: str,
    stock_unit: str,
    package: PackageInfo | None = None,
) -> PantryItem:
    return PantryItem(
        ingredient=ingredient,
        stock=Quantity(value=Decimal(stock_value), unit=stock_unit),
        package=package,
    )


def test_fully_available_ingredient() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="Chicken", quantity=Decimal("500"), unit="g"),
    ])
    pantry = Pantry(items=[_item("Chicken", "2", "kg")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.available) == 1
    assert result.available[0].ingredient == "Chicken"
    assert result.partial == []
    assert result.missing == []


def test_missing_ingredient() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="Mushrooms", quantity=Decimal("100"), unit="g"),
    ])
    pantry = Pantry(items=[])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert result.available == []
    assert len(result.missing) == 1
    assert result.missing[0].ingredient == "Mushrooms"
    assert result.missing[0].quantity == Decimal("100")


def test_partial_availability() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="Chicken", quantity=Decimal("3"), unit="kg"),
    ])
    pantry = Pantry(items=[_item("Chicken", "2", "kg")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert result.available == []
    assert len(result.partial) == 1
    assert result.partial[0].ingredient == "Chicken"
    assert result.partial[0].available == Decimal("2")
    assert result.partial[0].missing == Decimal("1")


def test_case_insensitive_matching() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="tomate", quantity=Decimal("200"), unit="g"),
    ])
    pantry = Pantry(items=[_item("Tomate", "1", "kg")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.available) == 1


def test_ingredient_map_overrides_name_matching() -> None:
    recipe = _recipe([
        RecipeIngredient(
            ingredient="tomates maduros",
            quantity=Decimal("200"),
            unit="g",
        ),
    ])
    pantry = Pantry(items=[_item("Tomate", "1", "kg")])
    result = compare_recipe_with_pantry(
        recipe, pantry,
        ingredient_map={"tomates maduros": "Tomate"},
    )
    assert len(result.available) == 1
    assert result.available[0].ingredient == "tomates maduros"


def test_incompatible_units_with_pantry_item_are_incompatible() -> None:
    """When conversion fails but the item IS in the pantry, block the unit."""
    recipe = _recipe([
        RecipeIngredient(
            ingredient="Flour",
            quantity=Decimal("1"),
            unit="bushel",
        ),
    ])
    pantry = Pantry(items=[_item("Flour", "1", "kg")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert result.available == []
    assert result.missing == []
    assert len(result.incompatible) == 1
    assert result.incompatible[0].ingredient == "Flour"
    assert result.incompatible[0].recipe_unit == "bushel"
    assert result.incompatible[0].pantry_unit == "kg"


def test_incompatible_units_without_pantry_item_is_missing() -> None:
    """When conversion fails and the item is NOT in the pantry, it's missing."""
    recipe = _recipe([
        RecipeIngredient(
            ingredient="Saffron",
            quantity=Decimal("1"),
            unit="pinch",
        ),
    ])
    pantry = Pantry(items=[_item("Flour", "1", "kg")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.missing) == 1


def test_package_item_stock_conversion() -> None:
    package = PackageInfo(
        container="un",
        content_quantity=Decimal("500"),
        content_unit="g",
    )
    recipe = _recipe([
        RecipeIngredient(
            ingredient="Cream",
            quantity=Decimal("250"),
            unit="g",
        ),
    ])
    pantry = Pantry(items=[
        _item("Cream", "1", "un 500g", package=package),
    ])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.available) == 1
    assert result.available[0].available == Decimal("500")


def test_effective_stock_reduces_availability() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="Chicken", quantity=Decimal("1.5"), unit="kg"),
    ])
    pantry = Pantry(items=[_item("Chicken", "2", "kg")])
    effective = {"Chicken": (Decimal("0.5"), "kg")}
    result = compare_recipe_with_pantry(
        recipe, pantry, effective_stock=effective,
    )
    assert result.available == []
    assert len(result.partial) == 1
    assert result.partial[0].available == Decimal("0.5")
    assert result.partial[0].missing == Decimal("1.0")


def test_effective_stock_zero_reports_as_missing() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="Chicken", quantity=Decimal("500"), unit="g"),
    ])
    pantry = Pantry(items=[_item("Chicken", "2", "kg")])
    effective = {"Chicken": (Decimal("0"), "kg")}
    result = compare_recipe_with_pantry(
        recipe, pantry, effective_stock=effective,
    )
    assert len(result.missing) == 1


def test_multiple_ingredients_categorized_correctly() -> None:
    recipe = _recipe([
        RecipeIngredient(ingredient="Chicken", quantity=Decimal("500"), unit="g"),
        RecipeIngredient(ingredient="Onion", quantity=Decimal("200"), unit="g"),
        RecipeIngredient(ingredient="Mushrooms", quantity=Decimal("100"), unit="g"),
    ])
    pantry = Pantry(items=[
        _item("Chicken", "2", "kg"),
        _item("Onion", "0.1", "kg"),
    ])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.available) == 1
    assert result.available[0].ingredient == "Chicken"
    assert len(result.partial) == 1
    assert result.partial[0].ingredient == "Onion"
    assert len(result.missing) == 1
    assert result.missing[0].ingredient == "Mushrooms"


def test_cooking_unit_xicara_converts_to_ml() -> None:
    """1 xícara = 240ml, pantry has 2L = 2000ml → available."""
    recipe = _recipe([
        RecipeIngredient(ingredient="Milk", quantity=Decimal("1"), unit="xícara"),
    ])
    pantry = Pantry(items=[_item("Milk", "2", "L")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.available) == 1


def test_unit_alias_unidade_matches_un() -> None:
    """'unidade' normalizes to 'un', matching pantry unit."""
    recipe = _recipe([
        RecipeIngredient(ingredient="Eggs", quantity=Decimal("3"), unit="unidade"),
    ])
    pantry = Pantry(items=[_item("Eggs", "30", "un")])
    result = compare_recipe_with_pantry(recipe, pantry)
    assert len(result.available) == 1

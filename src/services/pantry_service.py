from decimal import Decimal

from src.domain.recipe import Recipe
from src.domain.pantry import Pantry, PantryItem
from src.domain.feasibility import (
    AvailableIngredient,
    IncompatibleIngredient,
    MissingIngredient,
    PartialIngredient,
    PantryComparison,
)
from src.utils.unit_conversion import convert_quantity


def _normalize_ingredient(name: str) -> str:
    return " ".join(name.casefold().split())


def _available_quantity(
    item: PantryItem,
    target_unit: str,
) -> Decimal | None:
    converted_stock = convert_quantity(
        item.stock.value,
        item.stock.unit,
        target_unit,
    )

    if converted_stock is not None:
        return converted_stock

    if item.package is None:
        return None

    package_contents = (
        item.stock.value
        * item.package.content_quantity
    )

    return convert_quantity(
        package_contents,
        item.package.content_unit,
        target_unit,
    )


def compare_recipe_with_pantry(
    recipe: Recipe,
    pantry: Pantry,
    ingredient_map: dict[str, str] | None = None,
    effective_stock: dict[str, tuple[Decimal, str]] | None = None,
) -> PantryComparison:
    """Compare recipe ingredients against pantry.

    ingredient_map maps recipe ingredient names to pantry ingredient names
    (from confirmed semantic matches).  If None, uses normalized name matching.

    effective_stock maps pantry ingredient names to (remaining_quantity, unit)
    after deducting reservations from accepted recipes.  When provided, overrides
    the raw pantry stock value for availability calculations.
    """
    pantry_by_name = {
        item.ingredient: item for item in pantry.items
    }
    pantry_by_normalized = {
        _normalize_ingredient(item.ingredient): item
        for item in pantry.items
    }

    available: list[AvailableIngredient] = []
    partial: list[PartialIngredient] = []
    missing: list[MissingIngredient] = []
    incompatible: list[IncompatibleIngredient] = []

    for required_item in recipe.ingredients:
        pantry_item: PantryItem | None = None

        if ingredient_map and required_item.ingredient in ingredient_map:
            pantry_name = ingredient_map[required_item.ingredient]
            pantry_item = pantry_by_name.get(pantry_name)
        else:
            pantry_item = pantry_by_normalized.get(
                _normalize_ingredient(required_item.ingredient)
            )

        if pantry_item is None:
            missing.append(
                MissingIngredient(
                    ingredient=required_item.ingredient,
                    quantity=required_item.quantity,
                    unit=required_item.unit,
                )
            )
            continue

        if effective_stock and pantry_item.ingredient in effective_stock:
            remaining_qty, remaining_unit = effective_stock[pantry_item.ingredient]
            quantity_available = convert_quantity(
                remaining_qty, remaining_unit, required_item.unit
            )
        else:
            quantity_available = _available_quantity(
                pantry_item, required_item.unit
            )

        if quantity_available is None:
            incompatible.append(
                IncompatibleIngredient(
                    ingredient=required_item.ingredient,
                    required=required_item.quantity,
                    recipe_unit=required_item.unit,
                    pantry_unit=pantry_item.stock.unit,
                )
            )
            continue

        if quantity_available >= required_item.quantity:
            available.append(
                AvailableIngredient(
                    ingredient=required_item.ingredient,
                    required=required_item.quantity,
                    available=quantity_available,
                    unit=required_item.unit,
                )
            )
        elif quantity_available > 0:
            missing_qty = required_item.quantity - quantity_available
            partial.append(
                PartialIngredient(
                    ingredient=required_item.ingredient,
                    required=required_item.quantity,
                    available=quantity_available,
                    missing=missing_qty,
                    unit=required_item.unit,
                )
            )
        else:
            missing.append(
                MissingIngredient(
                    ingredient=required_item.ingredient,
                    quantity=required_item.quantity,
                    unit=required_item.unit,
                )
            )

    return PantryComparison(
        available=available,
        partial=partial,
        missing=missing,
        incompatible=incompatible,
    )

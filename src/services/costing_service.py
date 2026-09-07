from dataclasses import dataclass
from decimal import Decimal

from src.domain.costing import (
    ComplementaryPurchase,
    IngredientAllocation,
    IngredientCMV,
    IngredientCostComponent,
    IngredientCostSource,
    RecipeCMV,
)
from src.domain.pantry import PackageInfo, Pantry, PantryItem
from src.domain.recipe import Recipe, RecipeIngredient
from src.utils.unit_conversion import convert_quantity, normalize_unit


class CostingError(RuntimeError):
    """Base application error for deterministic recipe costing."""


class MissingCostInformation(CostingError):
    """A consumed ingredient has no usable cost record."""


class InvalidIngredientAllocation(CostingError):
    """Explicit source allocations do not account for the recipe."""


class InvalidRecipeYield(CostingError):
    """The recipe yield cannot produce a per-serving CMV."""


class InvalidCostRecord(CostingError):
    """A referenced purchase record cannot yield a valid unit cost."""


@dataclass(frozen=True)
class _CostBasis:
    unit_cost: Decimal
    unit: str


def _cost_basis(
    *,
    quantity: Decimal,
    unit: str,
    total_paid: Decimal,
    package: PackageInfo | None,
    description: str,
) -> _CostBasis:
    if quantity <= 0:
        raise InvalidCostRecord(
            f"{description} purchased quantity must be greater than zero"
        )
    if total_paid < 0:
        raise InvalidCostRecord(
            f"{description} total price must not be negative"
        )
    if not unit.strip():
        raise InvalidCostRecord(
            f"{description} purchased unit must not be blank"
        )

    if package is None:
        return _CostBasis(
            unit_cost=total_paid / quantity,
            unit=normalize_unit(unit),
        )

    if package.content_quantity <= 0:
        raise InvalidCostRecord(
            f"{description} package content quantity must be greater than zero"
        )
    if not package.content_unit.strip():
        raise InvalidCostRecord(
            f"{description} package content unit must not be blank"
        )

    total_content = quantity * package.content_quantity
    return _CostBasis(
        unit_cost=total_paid / total_content,
        unit=normalize_unit(package.content_unit),
    )


def _pantry_cost_basis(item: PantryItem) -> _CostBasis:
    if item.purchase is None:
        raise MissingCostInformation(
            f"Missing historical cost for pantry ingredient "
            f"'{item.ingredient}'"
        )

    return _cost_basis(
        quantity=item.purchase.quantity.value,
        unit=item.purchase.quantity.unit,
        total_paid=item.purchase.total_paid,
        package=item.purchase.package,
        description=f"Pantry ingredient '{item.ingredient}'",
    )


def _complementary_cost_basis(
    purchase: ComplementaryPurchase,
) -> _CostBasis:
    return _cost_basis(
        quantity=purchase.purchased_quantity,
        unit=purchase.purchased_unit,
        total_paid=purchase.package_price,
        package=purchase.package,
        description=(
            f"Complementary purchase '{purchase.purchase_id}' "
            f"for '{purchase.ingredient}'"
        ),
    )


def _cost_component(
    allocation: IngredientAllocation,
    basis: _CostBasis,
) -> IngredientCostComponent | None:
    quantity_in_cost_unit = convert_quantity(
        allocation.quantity,
        allocation.unit,
        basis.unit,
    )
    if quantity_in_cost_unit is None:
        if allocation.unit == basis.unit:
            quantity_in_cost_unit = allocation.quantity
        else:
            return None

    return IngredientCostComponent(
        source=allocation.source,
        quantity_used=quantity_in_cost_unit,
        unit=basis.unit,
        unit_cost=basis.unit_cost,
        cost_unit=basis.unit,
        total_cost=quantity_in_cost_unit * basis.unit_cost,
    )


def _validate_recipe(recipe: Recipe) -> dict[str, RecipeIngredient]:
    if recipe.servings <= 0:
        raise InvalidRecipeYield(
            "Recipe servings must be greater than zero"
        )

    ingredients_by_name: dict[str, RecipeIngredient] = {}
    for ingredient in recipe.ingredients:
        if ingredient.quantity < 0:
            raise InvalidIngredientAllocation(
                f"Recipe quantity for '{ingredient.ingredient}' "
                f"must not be negative"
            )
        if ingredient.ingredient in ingredients_by_name:
            raise InvalidIngredientAllocation(
                f"Recipe ingredient '{ingredient.ingredient}' is duplicated; "
                f"allocations require unique recipe ingredient names"
            )
        ingredients_by_name[ingredient.ingredient] = ingredient

    return ingredients_by_name


def _index_complementary_purchases(
    purchases: list[ComplementaryPurchase],
) -> dict[str, ComplementaryPurchase]:
    by_id: dict[str, ComplementaryPurchase] = {}
    for purchase in purchases:
        if purchase.purchase_id in by_id:
            raise InvalidCostRecord(
                f"Duplicate complementary purchase id "
                f"'{purchase.purchase_id}'"
            )
        by_id[purchase.purchase_id] = purchase
    return by_id


def _group_allocations(
    allocations: list[IngredientAllocation],
    recipe_ingredients: dict[str, RecipeIngredient],
) -> dict[str, list[IngredientAllocation]]:
    grouped: dict[str, list[IngredientAllocation]] = {
        name: [] for name in recipe_ingredients
    }

    for allocation in allocations:
        if allocation.recipe_ingredient not in recipe_ingredients:
            raise InvalidIngredientAllocation(
                f"Allocation references unknown recipe ingredient "
                f"'{allocation.recipe_ingredient}'"
            )
        grouped[allocation.recipe_ingredient].append(allocation)

    return grouped


def _validate_allocation_coverage(
    recipe_ingredient: RecipeIngredient,
    allocations: list[IngredientAllocation],
) -> bool:
    """Validate that allocations cover the recipe requirement.

    Returns True if coverage was verified, False if units are
    unconvertible (allocation is trusted but unverified).
    """
    allocated = Decimal("0")
    for allocation in allocations:
        converted = convert_quantity(
            allocation.quantity,
            allocation.unit,
            recipe_ingredient.unit,
        )
        if converted is None:
            return False
        allocated += converted

    if allocated != recipe_ingredient.quantity:
        raise InvalidIngredientAllocation(
            f"Allocations for '{recipe_ingredient.ingredient}' total "
            f"{allocated} {recipe_ingredient.unit}, but the recipe requires "
            f"{recipe_ingredient.quantity} {recipe_ingredient.unit}"
        )
    return True


def calculate_recipe_cmv(
    recipe: Recipe,
    pantry: Pantry,
    ingredient_allocations: list[IngredientAllocation],
    complementary_purchases: list[ComplementaryPurchase],
) -> RecipeCMV:
    """Calculate complete recipe CMV from caller-provided source allocations."""
    recipe_ingredients = _validate_recipe(recipe)
    purchases_by_id = _index_complementary_purchases(
        complementary_purchases
    )
    allocations_by_ingredient = _group_allocations(
        ingredient_allocations,
        recipe_ingredients,
    )

    ingredient_costs: list[IngredientCMV] = []

    for recipe_ingredient in recipe.ingredients:
        allocations = allocations_by_ingredient[
            recipe_ingredient.ingredient
        ]
        _validate_allocation_coverage(recipe_ingredient, allocations)

        components: list[IngredientCostComponent] = []
        pantry_references: set[str] = set()

        for allocation in allocations:
            if allocation.quantity == 0:
                continue

            if allocation.source is IngredientCostSource.PANTRY:
                pantry_ingredient = allocation.pantry_ingredient
                if pantry_ingredient is None:
                    raise InvalidIngredientAllocation(
                        "Pantry allocation is missing pantry_ingredient"
                    )
                pantry_item = pantry.get(pantry_ingredient)
                if pantry_item is None:
                    raise MissingCostInformation(
                        f"Pantry ingredient '{pantry_ingredient}' was not found"
                    )
                pantry_references.add(pantry_ingredient)
                basis = _pantry_cost_basis(pantry_item)
            else:
                purchase_id = allocation.complementary_purchase_id
                if purchase_id is None:
                    raise InvalidIngredientAllocation(
                        "Complementary allocation is missing purchase id"
                    )
                purchase = purchases_by_id.get(purchase_id)
                if purchase is None:
                    raise MissingCostInformation(
                        f"Complementary purchase '{purchase_id}' was not found"
                    )
                basis = _complementary_cost_basis(purchase)

            component = _cost_component(allocation, basis)
            if component is not None:
                components.append(component)

        if len(pantry_references) > 1:
            raise InvalidIngredientAllocation(
                f"Recipe ingredient '{recipe_ingredient.ingredient}' "
                f"references multiple pantry ingredients"
            )

        total_cost = sum(
            (component.total_cost for component in components),
            Decimal("0"),
        )
        ingredient_costs.append(
            IngredientCMV(
                recipe_ingredient=recipe_ingredient.ingredient,
                pantry_ingredient=next(iter(pantry_references), None),
                quantity_required=recipe_ingredient.quantity,
                unit=normalize_unit(recipe_ingredient.unit),
                components=components,
                total_cost=total_cost,
            )
        )

    batch_cmv = sum(
        (ingredient.total_cost for ingredient in ingredient_costs),
        Decimal("0"),
    )
    return RecipeCMV(
        recipe_name=recipe.name,
        servings=recipe.servings,
        ingredients=ingredient_costs,
        batch_cmv=batch_cmv,
        cmv_per_serving=batch_cmv / Decimal(recipe.servings),
    )

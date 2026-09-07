from decimal import Decimal, ROUND_CEILING

from src.domain.costing import (
    ComplementaryPurchase,
    IngredientAllocation,
    IngredientCostSource,
)
from src.domain.feasibility import MissingIngredient
from src.domain.purchasing import (
    MarketQuote,
    PurchasePlan,
    PurchaseRequirement,
)
from src.utils.unit_conversion import convert_quantity, normalize_unit


INITIAL_BUDGET = Decimal("80.00")


class PurchasingError(RuntimeError):
    """Base application error for complementary purchase planning."""


class InsufficientBudget(PurchasingError):
    """Complementary purchases exceed the remaining cash budget."""


class MissingMarketQuote(PurchasingError):
    """A required ingredient has no exact market quote."""


class DuplicateMarketQuote(PurchasingError):
    """Market quotes cannot be indexed without ambiguity."""


class InvalidPurchaseRequirement(PurchasingError):
    """A shortage or committed amount is invalid."""


class IncompatiblePurchaseUnits(PurchasingError):
    """A market package cannot be compared to the required quantity."""


def _index_quotes(quotes: list[MarketQuote]) -> dict[str, MarketQuote]:
    by_ingredient: dict[str, MarketQuote] = {}
    quote_ids: set[str] = set()

    for quote in quotes:
        if quote.quote_id in quote_ids:
            raise DuplicateMarketQuote(
                f"Duplicate market quote id '{quote.quote_id}'"
            )
        if quote.ingredient in by_ingredient:
            raise DuplicateMarketQuote(
                f"Multiple market quotes supplied for "
                f"'{quote.ingredient}'"
            )
        quote_ids.add(quote.quote_id)
        by_ingredient[quote.ingredient] = quote

    return by_ingredient


def _validate_requirements(
    missing_ingredients: list[MissingIngredient],
) -> None:
    seen: set[str] = set()
    for missing in missing_ingredients:
        if not missing.ingredient.strip():
            raise InvalidPurchaseRequirement(
                "Missing ingredient name must not be blank"
            )
        if missing.quantity <= 0:
            raise InvalidPurchaseRequirement(
                f"Required quantity for '{missing.ingredient}' "
                f"must be greater than zero"
            )
        if not missing.unit.strip():
            raise InvalidPurchaseRequirement(
                f"Required unit for '{missing.ingredient}' must not be blank"
            )
        if missing.ingredient in seen:
            raise InvalidPurchaseRequirement(
                f"Duplicate purchase requirement for "
                f"'{missing.ingredient}'"
            )
        seen.add(missing.ingredient)


def plan_complementary_purchases(
    missing_ingredients: list[MissingIngredient],
    market_quotes: list[MarketQuote],
    committed_purchases: Decimal = Decimal("0"),
) -> PurchasePlan:
    """Plan whole-package purchases and validate the remaining R$80 budget."""
    if committed_purchases < 0:
        raise InvalidPurchaseRequirement(
            "committed_purchases must not be negative"
        )
    if committed_purchases > INITIAL_BUDGET:
        raise InsufficientBudget(
            f"Committed purchases of R${committed_purchases} exceed "
            f"the initial budget of R${INITIAL_BUDGET}"
        )

    _validate_requirements(missing_ingredients)
    quotes_by_ingredient = _index_quotes(market_quotes)

    requirements: list[PurchaseRequirement] = []
    complementary_purchases: list[ComplementaryPurchase] = []
    allocations: list[IngredientAllocation] = []

    for missing in missing_ingredients:
        quote = quotes_by_ingredient.get(missing.ingredient)
        if quote is None:
            raise MissingMarketQuote(
                f"Missing market quote for '{missing.ingredient}'"
            )

        required_in_package_unit = convert_quantity(
            missing.quantity,
            missing.unit,
            quote.unit,
        )
        if required_in_package_unit is None:
            raise IncompatiblePurchaseUnits(
                f"Cannot convert required quantity for "
                f"'{missing.ingredient}' from '{missing.unit}' "
                f"to market unit '{quote.unit}'"
            )

        packages_required = int(
            (
                required_in_package_unit
                / quote.package_quantity
            ).to_integral_value(rounding=ROUND_CEILING)
        )
        cash_outlay = (
            Decimal(packages_required)
            * quote.package_price
        )
        allocated_recipe_cost = (
            required_in_package_unit
            * quote.package_price
            / quote.package_quantity
        )

        requirements.append(
            PurchaseRequirement(
                purchase_id=quote.quote_id,
                ingredient=missing.ingredient,
                required_quantity=missing.quantity,
                required_unit=normalize_unit(missing.unit),
                package_quantity=quote.package_quantity,
                package_unit=normalize_unit(quote.unit),
                packages_required=packages_required,
                cash_outlay=cash_outlay,
                allocated_recipe_cost=allocated_recipe_cost,
                source_url=quote.source_url,
            )
        )
        complementary_purchases.append(
            ComplementaryPurchase(
                purchase_id=quote.quote_id,
                ingredient=missing.ingredient,
                purchased_quantity=(
                    Decimal(packages_required)
                    * quote.package_quantity
                ),
                purchased_unit=normalize_unit(quote.unit),
                package_price=cash_outlay,
                source_url=quote.source_url,
            )
        )
        allocations.append(
            IngredientAllocation(
                recipe_ingredient=missing.ingredient,
                source=IngredientCostSource.COMPLEMENTARY_PURCHASE,
                quantity=missing.quantity,
                unit=normalize_unit(missing.unit),
                complementary_purchase_id=quote.quote_id,
            )
        )

    available_budget = INITIAL_BUDGET - committed_purchases
    cash_outlay = sum(
        (requirement.cash_outlay for requirement in requirements),
        Decimal("0"),
    )
    if cash_outlay > available_budget:
        raise InsufficientBudget(
            f"Complementary purchases cost R${cash_outlay}, but only "
            f"R${available_budget} remains"
        )

    return PurchasePlan(
        initial_budget=INITIAL_BUDGET,
        committed_purchases=committed_purchases,
        available_budget=available_budget,
        requirements=requirements,
        cash_outlay=cash_outlay,
        remaining_budget=available_budget - cash_outlay,
        complementary_purchases=complementary_purchases,
        ingredient_allocations=allocations,
    )

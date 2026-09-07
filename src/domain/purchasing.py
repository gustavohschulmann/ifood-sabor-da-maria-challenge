from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from src.domain.costing import ComplementaryPurchase, IngredientAllocation
from src.domain.money import serialize_money as format_money


class PurchasingModel(BaseModel):
    # PurchasingModel rejects fields outside the purchasing contracts.
    model_config = ConfigDict(extra="forbid")


class MarketQuote(PurchasingModel):
    # MarketQuote records one sourced market package and its price.
    quote_id: str
    ingredient: str
    package_quantity: Decimal
    unit: str
    package_price: Decimal
    source_url: str

    @field_validator("quote_id", "ingredient", "unit", "source_url")
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("package_quantity")
    @classmethod
    def require_positive_package_quantity(
        cls,
        value: Decimal,
    ) -> Decimal:
        if value <= 0:
            raise ValueError("package_quantity must be greater than zero")
        return value

    @field_validator("package_price")
    @classmethod
    def require_non_negative_package_price(
        cls,
        value: Decimal,
    ) -> Decimal:
        if value < 0:
            raise ValueError("package_price must not be negative")
        return value


class PurchaseRequirement(PurchasingModel):
    # PurchaseRequirement separates package cash outlay from consumed CMV.
    purchase_id: str
    ingredient: str
    required_quantity: Decimal
    required_unit: str
    package_quantity: Decimal
    package_unit: str
    packages_required: int
    cash_outlay: Decimal
    allocated_recipe_cost: Decimal
    source_url: str

    @field_serializer(
        "cash_outlay",
        "allocated_recipe_cost",
        when_used="json",
    )
    def serialize_money(self, value: Decimal) -> str:
        return format_money(value)


class PurchasePlan(PurchasingModel):
    # PurchasePlan contains budget totals and CMV-ready purchase records.
    initial_budget: Decimal
    committed_purchases: Decimal
    available_budget: Decimal
    requirements: list[PurchaseRequirement]
    cash_outlay: Decimal
    remaining_budget: Decimal
    complementary_purchases: list[ComplementaryPurchase]
    ingredient_allocations: list[IngredientAllocation]

    @field_serializer(
        "initial_budget",
        "committed_purchases",
        "available_budget",
        "cash_outlay",
        "remaining_budget",
        when_used="json",
    )
    def serialize_money(self, value: Decimal) -> str:
        return format_money(value)

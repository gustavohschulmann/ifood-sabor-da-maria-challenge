from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

from src.domain.money import serialize_money as format_money


class PricingModel(BaseModel):
    # PricingModel rejects fields outside the pricing contracts.
    model_config = ConfigDict(extra="forbid")


class PriceScenario(PricingModel):
    # PriceScenario records one selling price and its financial breakdown.
    label: str
    markup_rate: Decimal
    price: Decimal
    platform_fee: Decimal
    net_revenue: Decimal
    profit: Decimal

    @field_serializer(
        "price",
        "platform_fee",
        "net_revenue",
        "profit",
        when_used="json",
    )
    def serialize_money(self, value: Decimal) -> str:
        return format_money(value)


class RecipePricing(PricingModel):
    # RecipePricing contains break-even and suggested per-serving prices.
    cmv_per_serving: Decimal
    platform_fee_rate: Decimal
    net_rate: Decimal
    minimum_price: Decimal
    scenarios: list[PriceScenario]

    @field_serializer(
        "cmv_per_serving",
        "minimum_price",
        when_used="json",
    )
    def serialize_money(self, value: Decimal) -> str:
        return format_money(value)

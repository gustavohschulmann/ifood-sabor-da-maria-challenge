from decimal import Decimal
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)

from src.domain.money import serialize_money as format_money
from src.domain.pantry import PackageInfo


class CostingModel(BaseModel):
    # CostingModel applies strict field validation to all costing models.
    model_config = ConfigDict(extra="forbid")


class IngredientCostSource(StrEnum):
    # IngredientCostSource identifies the price record used by an allocation.
    PANTRY = "pantry"
    COMPLEMENTARY_PURCHASE = "complementary_purchase"


class ComplementaryPurchase(CostingModel):
    # ComplementaryPurchase stores the quoted cost of an additional purchase.
    purchase_id: str
    ingredient: str
    purchased_quantity: Decimal
    purchased_unit: str
    package_price: Decimal
    package: PackageInfo | None = None
    source_url: str | None = None

    @field_validator(
        "purchase_id",
        "ingredient",
        "purchased_unit",
    )
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("purchased_quantity")
    @classmethod
    def require_positive_quantity(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("purchased_quantity must be greater than zero")
        return value

    @field_validator("package_price")
    @classmethod
    def require_non_negative_price(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("package_price must not be negative")
        return value

    @model_validator(mode="after")
    def validate_package_content(self) -> "ComplementaryPurchase":
        if self.package is not None and self.package.content_quantity <= 0:
            raise ValueError("package content quantity must be greater than zero")
        return self


class IngredientAllocation(CostingModel):
    # IngredientAllocation assigns an explicit recipe quantity to one cost source.
    recipe_ingredient: str
    source: IngredientCostSource
    quantity: Decimal
    unit: str
    pantry_ingredient: str | None = None
    complementary_purchase_id: str | None = None

    @field_validator("recipe_ingredient", "unit")
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("quantity")
    @classmethod
    def reject_negative_quantity(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("quantity must not be negative")
        return value

    @model_validator(mode="after")
    def validate_source_reference(self) -> "IngredientAllocation":
        if self.source is IngredientCostSource.PANTRY:
            if not self.pantry_ingredient or not self.pantry_ingredient.strip():
                raise ValueError(
                    "pantry allocations require pantry_ingredient"
                )
            if self.complementary_purchase_id is not None:
                raise ValueError(
                    "pantry allocations cannot reference a complementary purchase"
                )
            return self

        if (
            not self.complementary_purchase_id
            or not self.complementary_purchase_id.strip()
        ):
            raise ValueError(
                "complementary purchase allocations require "
                "complementary_purchase_id"
            )
        if self.pantry_ingredient is not None:
            raise ValueError(
                "complementary purchase allocations cannot reference pantry"
            )
        return self


class IngredientCostComponent(CostingModel):
    # IngredientCostComponent records one source's exact CMV contribution.
    source: IngredientCostSource
    quantity_used: Decimal
    unit: str
    unit_cost: Decimal
    cost_unit: str
    total_cost: Decimal

    @field_serializer("total_cost", "unit_cost", when_used="json")
    def serialize_money(self, value: Decimal) -> str:
        return format_money(value)

    @field_serializer("quantity_used", when_used="json")
    def serialize_quantity(self, value: Decimal) -> str:
        return format(value.normalize(), "f")


class IngredientCMV(CostingModel):
    # IngredientCMV groups all cost components for one recipe ingredient.
    recipe_ingredient: str
    pantry_ingredient: str | None
    quantity_required: Decimal
    unit: str
    components: list[IngredientCostComponent]
    total_cost: Decimal

    @field_serializer("total_cost", when_used="json")
    def serialize_total_cost(self, value: Decimal) -> str:
        return format_money(value)


class RecipeCMV(CostingModel):
    # RecipeCMV contains the complete batch and per-serving costing result.
    recipe_name: str
    servings: int
    ingredients: list[IngredientCMV]
    batch_cmv: Decimal
    cmv_per_serving: Decimal

    @field_serializer(
        "batch_cmv",
        "cmv_per_serving",
        when_used="json",
    )
    def serialize_money(self, value: Decimal) -> str:
        return format_money(value)

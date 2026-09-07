from decimal import Decimal
from pydantic import BaseModel, field_serializer
from typing import Literal


FeasibilityStatus = Literal[
    "feasible",
    "missing_information",
    "not_feasible",
]


class FeasibilityAssessment(BaseModel):
    """Structured result used to drive feasibility conversations."""

    status: FeasibilityStatus
    missing_information: list[str]
    blocking_reasons: list[str]


class AvailableIngredient(BaseModel):
    ingredient: str
    required: Decimal
    available: Decimal
    unit: str

    @field_serializer(
        "required",
        "available",
        when_used="json",
    )
    def serialize_quantities(
        self,
        value: Decimal,
    ) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class PartialIngredient(BaseModel):
    """Ingredient partially available in pantry — the rest must be purchased."""

    ingredient: str
    required: Decimal
    available: Decimal
    missing: Decimal
    unit: str

    @field_serializer(
        "required",
        "available",
        "missing",
        when_used="json",
    )
    def serialize_quantities(
        self,
        value: Decimal,
    ) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class IncompatibleIngredient(BaseModel):
    """Matched pantry item whose unit cannot be converted to the recipe unit."""

    ingredient: str
    required: Decimal
    recipe_unit: str
    pantry_unit: str

    @field_serializer("required", when_used="json")
    def serialize_required(
        self,
        value: Decimal,
    ) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class MissingIngredient(BaseModel):
    ingredient: str
    quantity: Decimal
    unit: str

    @field_serializer("quantity", when_used="json")
    def serialize_quantity(
        self,
        value: Decimal,
    ) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)


class PantryComparison(BaseModel):
    available: list[AvailableIngredient] = []
    partial: list[PartialIngredient] = []
    missing: list[MissingIngredient] = []
    incompatible: list[IncompatibleIngredient] = []

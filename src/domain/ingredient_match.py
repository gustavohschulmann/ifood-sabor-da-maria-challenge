from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class IngredientMatchStatus(StrEnum):
    MATCHED = "matched"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NOT_AVAILABLE = "not_available"


class IngredientMatch(BaseModel):
    """Validated result of mapping a recipe ingredient to the pantry."""

    model_config = ConfigDict(extra="forbid")

    recipe_ingredient: str
    pantry_ingredient: str | None
    status: IngredientMatchStatus
    reasoning: str

    @field_validator("recipe_ingredient", "reasoning")
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_status_and_pantry_ingredient(self) -> "IngredientMatch":
        if self.status is IngredientMatchStatus.NOT_AVAILABLE:
            if self.pantry_ingredient is not None:
                raise ValueError(
                    "not_available results cannot include a pantry ingredient"
                )
            return self

        if self.pantry_ingredient is None:
            raise ValueError(
                f"{self.status.value} results must include a pantry ingredient"
            )

        return self

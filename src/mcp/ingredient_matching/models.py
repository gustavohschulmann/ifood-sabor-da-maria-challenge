from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from src.domain.ingredient_match import IngredientMatchStatus


class HermesIngredientDecision(BaseModel):
    """Minimal structured decision expected from Hermes."""

    model_config = ConfigDict(extra="forbid")

    pantry_ingredient: str | None
    status: IngredientMatchStatus
    reasoning: str

    @field_validator("reasoning")
    @classmethod
    def require_reasoning(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reasoning must not be blank")
        return value

    @model_validator(mode="after")
    def validate_status_and_candidate(self) -> "HermesIngredientDecision":
        if self.status is IngredientMatchStatus.NOT_AVAILABLE:
            if self.pantry_ingredient is not None:
                raise ValueError(
                    "not_available decisions cannot include a pantry ingredient"
                )
            return self

        if self.pantry_ingredient is None:
            raise ValueError(
                f"{self.status.value} decisions must include a pantry ingredient"
            )

        return self

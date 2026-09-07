from pydantic import BaseModel, ConfigDict, field_validator


class HermesRequirementDecision(BaseModel):
    """Structured requirement extraction expected from Hermes."""

    model_config = ConfigDict(extra="forbid")

    required_equipment: list[str]
    required_skills: list[str]
    operational_requirements: list[str]
    reasoning: str

    @field_validator("reasoning")
    @classmethod
    def require_reasoning(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reasoning must not be blank")
        return value

from dataclasses import dataclass
import json

from pydantic import ValidationError

from src.domain.recipe import Recipe
from src.mcp.requirements.models import HermesRequirementDecision
from src.mcp.requirements.prompts import (
    REQUIREMENTS_SYSTEM_PROMPT,
    build_requirements_prompt,
)
from src.services.requirements_service import enrich_recipe_requirements
from src.utils.capability_normalization import (
    UnknownCapabilityName,
    normalize_capability_name,
)
from src.utils.enums import Equipment


class InvalidHermesResponseError(RuntimeError):
    """Hermes returned malformed or unsafe structured output."""


class RequirementExtractionUnavailableError(RuntimeError):
    """Hermes could not extract recipe requirements."""


@dataclass(frozen=True)
class RequirementExtractionRequest:
    system_prompt: str
    prompt: str
    max_tokens: int = 400


def prepare_requirement_extraction(
    recipe_name: str,
    steps: list[str],
) -> RequirementExtractionRequest | None:
    cleaned = [step.strip() for step in steps if step.strip()]
    if not cleaned:
        return None
    return RequirementExtractionRequest(
        system_prompt=REQUIREMENTS_SYSTEM_PROMPT,
        prompt=build_requirements_prompt(recipe_name, cleaned),
    )


def complete_requirement_extraction(
    recipe: Recipe,
    raw_response: str,
) -> Recipe:
    if not raw_response.strip():
        raise InvalidHermesResponseError("Hermes returned an empty response")

    try:
        payload = json.loads(raw_response)
        decision = HermesRequirementDecision.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise InvalidHermesResponseError(
            "Hermes returned an invalid requirement extraction response"
        ) from error

    equipment: list[Equipment] = []
    seen: set[str] = set()
    for item in decision.required_equipment:
        try:
            key = normalize_capability_name("equipment", item)
        except UnknownCapabilityName:
            continue
        if key in seen:
            continue
        try:
            equipment.append(Equipment(key))
        except ValueError:
            continue
        seen.add(key)

    skills = [skill.strip() for skill in decision.required_skills if skill.strip()]
    operational: list[str] = []
    for item in decision.operational_requirements:
        if not item.strip():
            continue
        try:
            operational.append(
                normalize_capability_name("operational", item)
            )
        except UnknownCapabilityName:
            continue

    extracted = recipe.model_copy(
        update={
            "required_equipment": equipment or recipe.required_equipment,
            "required_skills": skills or recipe.required_skills,
            "operational_requirements": (
                operational or recipe.operational_requirements
            ),
        }
    )
    return ensure_actionable_requirements(extracted)


def ensure_actionable_requirements(recipe: Recipe) -> Recipe:
    """Fail closed when a sourced recipe cannot support elicitation."""
    enriched = enrich_recipe_requirements(recipe)
    missing: list[str] = []
    if not enriched.steps:
        missing.append("preparation steps")
    if not enriched.required_equipment:
        missing.append("equipment requirements")
    if not enriched.required_skills:
        missing.append("skill requirements")
    if missing:
        raise RequirementExtractionUnavailableError(
            "Cannot register recipe without verified " + ", ".join(missing)
        )
    return enriched

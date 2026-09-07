from src.domain.recipe import Recipe
from src.domain.kitchen import KitchenProfile
from src.domain.feasibility import FeasibilityAssessment
from src.services.requirements_service import (
    enrich_recipe_requirements,
    minimum_operational_for,
)
from src.utils.capability_normalization import normalize_capability_name


def assess_recipe_feasibility(
    recipe: Recipe,
    kitchen: KitchenProfile,
) -> FeasibilityAssessment:
    """Check whether Dona Maria can actually produce this recipe."""

    enriched = enrich_recipe_requirements(recipe)
    missing_information: list[str] = []
    blocking_reasons: list[str] = []

    equipment_lookup = {
        normalize_capability_name("equipment", key): value
        for key, value in kitchen.equipment.items()
    }
    skill_lookup = {key: value for key, value in kitchen.skills.items()}
    operational_lookup = {
        normalize_capability_name("operational", key): value
        for key, value in kitchen.operational.items()
    }

    for equipment in enriched.required_equipment:
        key = normalize_capability_name("equipment", str(equipment))
        capability = equipment_lookup.get(key)
        if capability is False:
            blocking_reasons.append(f"{key} unavailable")
        elif capability is None:
            missing_information.append(key)

    for skill in enriched.required_skills:
        capability = skill_lookup.get(skill)
        if capability is False:
            blocking_reasons.append(f"{skill} unavailable")
        elif capability is None:
            missing_information.append(skill)

    required_ops = list(dict.fromkeys(
        list(enriched.operational_requirements)
        + minimum_operational_for(enriched)
    ))
    for requirement in required_ops:
        key = normalize_capability_name("operational", requirement)
        capability = operational_lookup.get(key)
        if capability is False:
            blocking_reasons.append(f"{key} unavailable")
        elif capability is None:
            missing_information.append(key)

    if blocking_reasons:
        status = "not_feasible"
    elif missing_information:
        status = "missing_information"
    else:
        status = "feasible"

    return FeasibilityAssessment(
        status=status,
        missing_information=missing_information,
        blocking_reasons=blocking_reasons,
    )

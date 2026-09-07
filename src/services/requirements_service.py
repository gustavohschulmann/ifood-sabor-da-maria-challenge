"""Validate and enrich recipe equipment, skills, and operational needs."""

from __future__ import annotations

import re
import unicodedata

from src.domain.recipe import Recipe
from src.utils.capability_normalization import (
    UnknownCapabilityName,
    is_equipment_name,
    normalize_capability_name,
)
from src.utils.enums import Equipment, OperationalRequirement

_STOVE_IMPLIERS: frozenset[str] = frozenset({
    Equipment.STOVE.value,
    Equipment.FRYING_PAN.value,
    Equipment.SAUCEPAN.value,
    Equipment.WOK.value,
})


MINIMUM_OPERATIONAL: tuple[str, ...] = (
    OperationalRequirement.GAS_OR_ELECTRIC.value,
    OperationalRequirement.FRIDGE_SPACE.value,
    OperationalRequirement.PREP_TIME.value,
    OperationalRequirement.COUNTER_SPACE.value,
)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", ascii_text.casefold()).strip()


_EQUIPMENT_STEP_RULES: tuple[tuple[str, tuple[Equipment, ...]], ...] = (
    (r"\b(empan|pass[ea].*farinha)", (Equipment.MIXING_BOWL,)),
    (r"\b(mistur|bat[ae])", (Equipment.MIXING_BOWL,)),
    (r"\b(frit|dour)", (Equipment.FRYING_PAN, Equipment.STOVE)),
    (r"\b(ass|forno|gratin)", (Equipment.OVEN,)),
    (r"\b(refratario|assadeira|forma)", (Equipment.CASSEROLE_DISH,)),
    (r"\b(cort|pic|fati)", (Equipment.CHEF_KNIFE, Equipment.CUTTING_BOARD)),
    (r"\b(liquidific)", (Equipment.BLENDER,)),
    (r"\b(panela de press)", (Equipment.PRESSURE_COOKER,)),
    (r"\b(cozinh|refog|ferv)", (Equipment.SAUCEPAN, Equipment.STOVE)),
)

_SKILL_STEP_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (r"\btemper", "temperar os alimentos", ("temper",)),
    (r"\bempan", "empanar", ("empan",)),
    (r"\bfrit", "fritar com segurança", ("frit",)),
    (
        r"\b(ponto|dour)",
        "identificar o ponto do alimento",
        ("ponto", "dour"),
    ),
    (r"\bgratin", "gratinar", ("gratin",)),
    (r"\b(mistur|bat[ae])", "misturar ingredientes", ("mistur", "bat")),
    (
        r"\b(bechamel|molho branco)",
        "preparar molho béchamel",
        ("bechamel", "molho branco"),
    ),
    (
        r"\b(sov|massa fresca|abrir a massa)",
        "preparar massa fresca",
        ("massa", "sov"),
    ),
    (r"\b(refog)", "refogar", ("refog",)),
)


def _merge_step_requirements(
    recipe: Recipe,
) -> tuple[list[Equipment], list[str]]:
    """Conservative fallback so sampling cannot silently erase elicitation."""
    step_text = _fold(" ".join(recipe.steps))
    equipment = list(recipe.required_equipment)
    equipment_keys = {str(item) for item in equipment}
    skills = list(recipe.required_skills)
    folded_skills = [_fold(skill) for skill in skills]

    for pattern, inferred in _EQUIPMENT_STEP_RULES:
        if not re.search(pattern, step_text):
            continue
        for item in inferred:
            if item.value not in equipment_keys:
                equipment.append(item)
                equipment_keys.add(item.value)

    for pattern, label, semantic_stems in _SKILL_STEP_RULES:
        if not re.search(pattern, step_text):
            continue
        if any(
            any(stem in existing for stem in semantic_stems)
            for existing in folded_skills
        ):
            continue
        skills.append(label)
        folded_skills.append(_fold(label))

    return equipment, skills


def minimum_operational_for(recipe: Recipe) -> list[str]:
    """Operational keys every candidate must resolve before buying."""
    required = list(MINIMUM_OPERATIONAL)
    equipment = set()
    for item in recipe.required_equipment:
        try:
            equipment.add(
                normalize_capability_name("equipment", str(item))
            )
        except UnknownCapabilityName:
            continue
    if equipment & _STOVE_IMPLIERS:
        if OperationalRequirement.STOVE_BURNERS.value not in required:
            required.append(OperationalRequirement.STOVE_BURNERS.value)
    return required


def enrich_recipe_requirements(recipe: Recipe) -> Recipe:
    """Normalize aliases, strip equipment from operational, inject minimums."""
    inferred_equipment, inferred_skills = _merge_step_requirements(recipe)
    equipment: list[Equipment] = []
    seen_equipment: set[str] = set()
    for item in inferred_equipment:
        try:
            key = normalize_capability_name("equipment", str(item))
        except UnknownCapabilityName:
            continue
        if key in seen_equipment:
            continue
        try:
            equipment.append(Equipment(key))
        except ValueError:
            continue
        seen_equipment.add(key)

    if seen_equipment & _STOVE_IMPLIERS:
        if Equipment.STOVE.value not in seen_equipment:
            equipment.append(Equipment.STOVE)
            seen_equipment.add(Equipment.STOVE.value)

    skills: list[str] = []
    seen_skills: set[str] = set()
    for skill in inferred_skills:
        stripped = skill.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        if lowered in seen_skills:
            continue
        seen_skills.add(lowered)
        skills.append(stripped)

    operational: list[str] = []
    seen_ops: set[str] = set()
    for requirement in recipe.operational_requirements:
        if is_equipment_name(requirement):
            continue
        try:
            key = normalize_capability_name("operational", requirement)
        except UnknownCapabilityName:
            continue
        if key in seen_ops:
            continue
        seen_ops.add(key)
        operational.append(key)

    enriched = recipe.model_copy(
        update={
            "required_equipment": equipment,
            "required_skills": skills,
            "operational_requirements": operational,
        }
    )
    for key in minimum_operational_for(enriched):
        if key not in seen_ops:
            operational.append(key)
            seen_ops.add(key)

    return enriched.model_copy(
        update={"operational_requirements": operational}
    )

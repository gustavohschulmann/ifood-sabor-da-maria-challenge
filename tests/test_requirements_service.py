from src.domain.recipe import Recipe
import pytest

from src.mcp.requirements.service import (
    RequirementExtractionUnavailableError,
    complete_requirement_extraction,
    ensure_actionable_requirements,
)
from src.services.requirements_service import (
    enrich_recipe_requirements,
    minimum_operational_for,
)
from src.utils.enums import Equipment


def _recipe(**overrides) -> Recipe:
    defaults = dict(
        id="test",
        name="Test",
        source_url="https://example.com",
        servings=1,
        ingredients=[],
        required_equipment=[],
        required_skills=[],
        operational_requirements=[],
    )
    defaults.update(overrides)
    return Recipe(**defaults)


def test_minimum_operational_always_present() -> None:
    recipe = _recipe()
    required = minimum_operational_for(recipe)
    assert required == [
        "gas_or_electric",
        "fridge_space",
        "prep_time",
        "counter_space",
    ]


def test_stove_adds_burners() -> None:
    recipe = _recipe(required_equipment=[Equipment.STOVE])
    assert "stove_burners" in minimum_operational_for(recipe)


def test_frying_pan_injects_stove_and_burners() -> None:
    recipe = _recipe(required_equipment=[Equipment.FRYING_PAN])
    assert "stove_burners" in minimum_operational_for(recipe)
    enriched = enrich_recipe_requirements(recipe)
    assert Equipment.STOVE in enriched.required_equipment
    assert Equipment.FRYING_PAN in enriched.required_equipment
    assert "stove_burners" in enriched.operational_requirements


def test_enrich_strips_equipment_and_injects_minimum() -> None:
    recipe = _recipe(
        required_equipment=[Equipment.STOVE],
        operational_requirements=["stove", "energia"],
    )
    enriched = enrich_recipe_requirements(recipe)
    assert "stove" not in enriched.operational_requirements
    assert "gas_or_electric" in enriched.operational_requirements
    assert "stove_burners" in enriched.operational_requirements


def test_complete_extraction_uses_current_steps_only() -> None:
    recipe = _recipe(
        required_skills=["Mexer o molho sem queimar"],
        operational_requirements=["Água para cozinhar o arroz"],
        steps=["Empane o frango", "Frite", "Gratine no forno"],
    )
    raw = """
    {
      "required_equipment": ["stove", "oven", "frying_pan"],
      "required_skills": ["empanar e fritar", "gratinar"],
      "operational_requirements": ["gas_or_electric"],
      "reasoning": "Derived from breading, frying, and oven steps."
    }
    """
    extracted = complete_requirement_extraction(recipe, raw)
    assert "Mexer o molho sem queimar" not in extracted.required_skills
    assert "Água para cozinhar o arroz" not in extracted.operational_requirements
    assert "empanar e fritar" in extracted.required_skills
    assert "gratinar" in extracted.required_skills
    assert Equipment.OVEN in extracted.required_equipment


def test_parmegiana_steps_cannot_finish_without_skills() -> None:
    recipe = _recipe(
        steps=[
            "Tempere o frango.",
            "Empane em farinha, ovos e farinha de rosca.",
            "Frite até dourar e gratine no forno.",
        ]
    )

    enriched = ensure_actionable_requirements(recipe)

    assert Equipment.FRYING_PAN in enriched.required_equipment
    assert Equipment.OVEN in enriched.required_equipment
    assert "temperar os alimentos" in enriched.required_skills
    assert "empanar" in enriched.required_skills
    assert "fritar com segurança" in enriched.required_skills
    assert "identificar o ponto do alimento" in enriched.required_skills
    assert "gratinar" in enriched.required_skills


def test_sampling_cannot_erase_skills_evidenced_by_steps() -> None:
    recipe = _recipe(
        steps=[
            "Tempere e empane o frango.",
            "Frite até dourar e gratine no forno.",
        ]
    )
    incomplete_sampling = """
    {
      "required_equipment": ["oven"],
      "required_skills": [],
      "operational_requirements": [],
      "reasoning": "Only the oven was identified."
    }
    """

    extracted = complete_requirement_extraction(recipe, incomplete_sampling)

    assert "temperar os alimentos" in extracted.required_skills
    assert "empanar" in extracted.required_skills
    assert "fritar com segurança" in extracted.required_skills
    assert "gratinar" in extracted.required_skills


def test_incomplete_requirement_extraction_fails_closed() -> None:
    recipe = _recipe(steps=["Sirva imediatamente."])

    with pytest.raises(
        RequirementExtractionUnavailableError,
        match="equipment requirements, skill requirements",
    ):
        ensure_actionable_requirements(recipe)

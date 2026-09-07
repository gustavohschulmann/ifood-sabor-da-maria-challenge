from src.domain.kitchen import KitchenProfile
from src.domain.recipe import Recipe
from src.services.feasibility_service import assess_recipe_feasibility
from src.services.requirements_service import MINIMUM_OPERATIONAL
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


def _kitchen_with_minimum(**overrides) -> KitchenProfile:
    operational = {key: True for key in MINIMUM_OPERATIONAL}
    operational.update(overrides.pop("operational", {}))
    return KitchenProfile(
        equipment=overrides.pop("equipment", {}),
        skills=overrides.pop("skills", {}),
        operational=operational,
        details=overrides.pop("details", {}),
    )


def test_feasible_when_all_capabilities_available() -> None:
    recipe = _recipe(
        required_equipment=[Equipment.OVEN, Equipment.BLENDER],
        required_skills=["bechamel"],
        operational_requirements=["gas_or_electric"],
    )
    kitchen = _kitchen_with_minimum(
        equipment={"oven": True, "blender": True},
        skills={"bechamel": True},
    )
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "feasible"
    assert result.missing_information == []
    assert result.blocking_reasons == []


def test_missing_information_for_unknown_capabilities() -> None:
    recipe = _recipe(
        required_equipment=[Equipment.PRESSURE_COOKER],
        required_skills=["meat_doneness"],
    )
    kitchen = KitchenProfile()
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "missing_information"
    assert "pressure_cooker" in result.missing_information
    assert "meat_doneness" in result.missing_information
    assert "gas_or_electric" in result.missing_information


def test_not_feasible_when_equipment_unavailable() -> None:
    recipe = _recipe(required_equipment=[Equipment.OVEN])
    kitchen = _kitchen_with_minimum(equipment={"oven": False})
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "not_feasible"
    assert "oven unavailable" in result.blocking_reasons


def test_not_feasible_when_skill_unavailable() -> None:
    recipe = _recipe(required_skills=["bechamel"])
    kitchen = _kitchen_with_minimum(skills={"bechamel": False})
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "not_feasible"
    assert "bechamel unavailable" in result.blocking_reasons


def test_not_feasible_when_operational_unavailable() -> None:
    recipe = _recipe(operational_requirements=["gas_or_electric"])
    kitchen = KitchenProfile(
        operational={
            "gas_or_electric": False,
            "fridge_space": True,
            "prep_time": True,
            "counter_space": True,
        }
    )
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "not_feasible"
    assert "gas_or_electric unavailable" in result.blocking_reasons


def test_blocking_takes_precedence_over_missing() -> None:
    recipe = _recipe(
        required_equipment=[Equipment.OVEN, Equipment.BLENDER],
    )
    kitchen = KitchenProfile(equipment={"oven": False})
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "not_feasible"
    assert "blender" in result.missing_information
    assert "oven unavailable" in result.blocking_reasons


def test_empty_recipe_still_requires_minimum_operational() -> None:
    recipe = _recipe()
    kitchen = KitchenProfile()
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "missing_information"
    for key in MINIMUM_OPERATIONAL:
        assert key in result.missing_information


def test_stove_requires_burner_count() -> None:
    recipe = _recipe(required_equipment=[Equipment.STOVE])
    kitchen = _kitchen_with_minimum(equipment={"stove": True})
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "missing_information"
    assert "stove_burners" in result.missing_information


def test_forno_alias_matches_oven() -> None:
    recipe = _recipe(required_equipment=[Equipment.OVEN])
    kitchen = _kitchen_with_minimum(equipment={"forno": True})
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "feasible"


def test_all_three_requirement_types() -> None:
    recipe = _recipe(
        required_equipment=[Equipment.OVEN],
        required_skills=["pasta_al_dente"],
        operational_requirements=["fridge_space"],
    )
    kitchen = KitchenProfile(
        equipment={"oven": True},
        skills={"pasta_al_dente": None},
        operational={"fridge_space": None},
    )
    result = assess_recipe_feasibility(recipe, kitchen)
    assert result.status == "missing_information"
    assert "pasta_al_dente" in result.missing_information
    assert "fridge_space" in result.missing_information

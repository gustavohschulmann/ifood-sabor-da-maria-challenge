import pytest
from decimal import Decimal
from pydantic import ValidationError

from src.domain.recipe import Recipe, RecipeIngredient
from src.utils.enums import Equipment


def test_blank_id_is_filled_from_name() -> None:
    recipe = Recipe(
        id="  ",
        name="Filé de frango à parmegiana",
        source_url="https://example.com",
        servings=1,
        ingredients=[],
    )
    assert recipe.id == "file-de-frango-a-parmegiana"


def test_missing_requirement_fields_default_to_empty() -> None:
    recipe = Recipe(
        name="Frango à Parmegiana",
        source_url="https://example.com",
        servings=4,
        ingredients=[
            RecipeIngredient(ingredient="Frango", quantity=Decimal("1"), unit="kg"),
        ],
        steps=["Frite na frigideira", "Gratine no forno"],
    )
    assert recipe.id == "frango-a-parmegiana"
    assert recipe.required_equipment == []
    assert recipe.required_skills == []
    assert recipe.operational_requirements == []


def test_portuguese_equipment_and_string_operational_are_coerced() -> None:
    recipe = Recipe(
        name="Parmegiana",
        source_url="https://example.com",
        servings=4,
        ingredients=[],
        required_equipment=["frigideira", "forno", "tigela"],
        operational_requirements="gás",
    )
    assert Equipment.FRYING_PAN in recipe.required_equipment
    assert Equipment.OVEN in recipe.required_equipment
    assert recipe.operational_requirements == ["gas_or_electric"]


def test_duplicate_ingredients_are_merged() -> None:
    recipe = Recipe(
        id="bad",
        name="Bad",
        source_url="https://example.com",
        servings=1,
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("1"), unit="kg"),
            RecipeIngredient(ingredient="chicken", quantity=Decimal("2"), unit="kg"),
        ],
    )
    assert len(recipe.ingredients) == 1
    assert recipe.ingredients[0].quantity == Decimal("3")


def test_ingredient_name_alias_is_accepted() -> None:
    recipe = Recipe.model_validate(
        {
            "name": "Frango à Parmegiana",
            "source_url": "https://example.com",
            "servings": 6,
            "ingredients": [
                {"name": "Peito de frango", "quantity": 1000, "unit": "g"},
                {"ingredient": "Ovos", "qty": 2, "unit": "un"},
            ],
        }
    )
    assert recipe.ingredients[0].ingredient == "Peito de frango"
    assert recipe.ingredients[0].quantity == Decimal("1000")
    assert recipe.ingredients[1].ingredient == "Ovos"
    assert recipe.ingredients[1].quantity == Decimal("2")


def test_unit_aliases_are_normalized() -> None:
    item = RecipeIngredient(
        ingredient="Farinha",
        quantity=Decimal("200"),
        unit="gramas",
    )
    assert item.unit == "g"


def test_zero_servings_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Recipe(
            id="test",
            name="Test",
            source_url="https://example.com",
            servings=0,
            ingredients=[],
        )


def test_non_positive_quantity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RecipeIngredient(ingredient="Chicken", quantity=Decimal("0"), unit="kg")


def test_source_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        Recipe(
            id="test",
            name="Test",
            source_url="tudogostoso.com.br/parmegiana",
            servings=1,
            ingredients=[],
        )

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from src.domain.ingredient_match import IngredientMatchStatus
from src.mcp.ingredient_matching.service import (
    HermesMatchingUnavailableError,
    InvalidHermesResponseError,
    match_recipe_ingredient,
)


def hermes_response(
    pantry_ingredient: str | None,
    status: str,
    reasoning: str = "Hermes semantic decision.",
) -> str:
    return json.dumps(
        {
            "pantry_ingredient": pantry_ingredient,
            "status": status,
            "reasoning": reasoning,
        }
    )


def run_match(
    recipe_ingredient: str,
    pantry_ingredients: list[str],
    response: str,
):
    invoke_hermes = AsyncMock(return_value=response)
    result = asyncio.run(
        match_recipe_ingredient(
            recipe_ingredient,
            pantry_ingredients,
            invoke_hermes,
        )
    )
    return result, invoke_hermes


def test_exact_match_does_not_call_hermes() -> None:
    result, invoke_hermes = run_match(
        "Tomate",
        ["Tomate", "Cebola"],
        response="unused",
    )

    assert result.status is IngredientMatchStatus.MATCHED
    assert result.pantry_ingredient == "Tomate"
    assert result.reasoning == "Exact pantry ingredient match."
    invoke_hermes.assert_not_awaited()


def test_exact_match_is_case_insensitive() -> None:
    result, invoke_hermes = run_match(
        "peito de frango",
        ["Peito de frango", "Alho"],
        response="unused",
    )

    assert result.status is IngredientMatchStatus.MATCHED
    assert result.pantry_ingredient == "Peito de frango"
    invoke_hermes.assert_not_awaited()


def test_empty_pantry_does_not_call_hermes() -> None:
    result, invoke_hermes = run_match(
        "tomates maduros",
        [],
        response="unused",
    )

    assert result.status is IngredientMatchStatus.NOT_AVAILABLE
    assert result.pantry_ingredient is None
    invoke_hermes.assert_not_awaited()


@pytest.mark.parametrize(
    ("recipe_ingredient", "pantry", "candidate", "status"),
    [
        (
            "tomates maduros",
            ["Arroz branco tipo 1", "Tomate", "Cebola"],
            "Tomate",
            "matched",
        ),
        (
            "filé de peito de frango",
            ["Peito de frango", "Alho"],
            "Peito de frango",
            "matched",
        ),
        (
            "creme de leite",
            ["Leite integral", "Manteiga"],
            None,
            "not_available",
        ),
        (
            "nozes",
            ["Amêndoa fatiada"],
            None,
            "not_available",
        ),
        (
            "óleo vegetal",
            ["Óleo de soja"],
            "Óleo de soja",
            "needs_confirmation",
        ),
    ],
)
def test_semantic_match_uses_mocked_hermes_decision(
    recipe_ingredient: str,
    pantry: list[str],
    candidate: str | None,
    status: str,
) -> None:
    result, invoke_hermes = run_match(
        recipe_ingredient,
        pantry,
        hermes_response(candidate, status),
    )

    assert result.recipe_ingredient == recipe_ingredient
    assert result.pantry_ingredient == candidate
    assert result.status.value == status
    invoke_hermes.assert_awaited_once()

    request = invoke_hermes.await_args.args[0]
    assert recipe_ingredient in request.prompt
    assert all(item in request.prompt for item in pantry)


def test_rejects_blank_recipe_ingredient() -> None:
    invoke_hermes = AsyncMock()

    with pytest.raises(ValueError, match="must not be blank"):
        asyncio.run(match_recipe_ingredient("   ", ["Tomate"], invoke_hermes))

    invoke_hermes.assert_not_awaited()


def test_rejects_malformed_hermes_response() -> None:
    with pytest.raises(InvalidHermesResponseError):
        run_match("tomates maduros", ["Tomate"], "not json")


@pytest.mark.parametrize(
    "response",
    [
        hermes_response("Tomate", "not_available"),
        hermes_response(None, "matched"),
    ],
)
def test_rejects_inconsistent_hermes_response(response: str) -> None:
    with pytest.raises(InvalidHermesResponseError):
        run_match("tomates maduros", ["Tomate"], response)


def test_rejects_invented_pantry_ingredient() -> None:
    response = hermes_response("Creme de leite", "matched")

    with pytest.raises(
        InvalidHermesResponseError,
        match="was not provided",
    ):
        run_match(
            "creme de leite",
            ["Leite integral", "Manteiga"],
            response,
        )


def test_surfaces_hermes_failure() -> None:
    invoke_hermes = AsyncMock(side_effect=TimeoutError("model timeout"))

    with pytest.raises(
        HermesMatchingUnavailableError,
        match="Hermes ingredient matching failed",
    ):
        asyncio.run(
            match_recipe_ingredient(
                "tomates maduros",
                ["Tomate"],
                invoke_hermes,
            )
        )

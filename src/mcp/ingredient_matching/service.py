from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json

from pydantic import ValidationError

from src.domain.ingredient_match import IngredientMatch, IngredientMatchStatus
from src.mcp.ingredient_matching.models import HermesIngredientDecision
from src.mcp.ingredient_matching.prompts import (
    INGREDIENT_MATCHING_SYSTEM_PROMPT,
    build_matching_prompt,
)


class IngredientMatchingError(RuntimeError):
    """Base application error for ingredient semantic matching."""


class InvalidHermesResponseError(IngredientMatchingError):
    """Hermes returned malformed or unsafe structured output."""


class HermesMatchingUnavailableError(IngredientMatchingError):
    """Hermes could not provide a semantic decision."""


@dataclass(frozen=True)
class SemanticMatchRequest:
    system_prompt: str
    prompt: str
    max_tokens: int = 300


def prepare_ingredient_match(
    recipe_ingredient: str,
    pantry_ingredients: list[str],
) -> IngredientMatch | SemanticMatchRequest:
    """Apply deterministic exits or describe the semantic decision needed."""
    if not recipe_ingredient.strip():
        raise ValueError("recipe_ingredient must not be blank")

    pantry_by_fold = {
        name.casefold(): name for name in pantry_ingredients
    }
    exact = pantry_by_fold.get(recipe_ingredient.strip().casefold())
    if exact is not None:
        return IngredientMatch(
            recipe_ingredient=recipe_ingredient,
            pantry_ingredient=exact,
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact pantry ingredient match.",
        )

    if not pantry_ingredients:
        return IngredientMatch(
            recipe_ingredient=recipe_ingredient,
            pantry_ingredient=None,
            status=IngredientMatchStatus.NOT_AVAILABLE,
            reasoning="The pantry is empty.",
        )

    return SemanticMatchRequest(
        system_prompt=INGREDIENT_MATCHING_SYSTEM_PROMPT,
        prompt=build_matching_prompt(recipe_ingredient, pantry_ingredients),
    )


def complete_ingredient_match(
    recipe_ingredient: str,
    pantry_ingredients: list[str],
    raw_response: str,
) -> IngredientMatch:
    """Parse Hermes output and enforce request-specific invariants."""
    if not raw_response.strip():
        raise InvalidHermesResponseError("Hermes returned an empty response")

    try:
        payload = json.loads(raw_response)
        decision = HermesIngredientDecision.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise InvalidHermesResponseError(
            "Hermes returned an invalid ingredient match response"
        ) from error

    if (
        decision.pantry_ingredient is not None
        and decision.pantry_ingredient not in pantry_ingredients
    ):
        raise InvalidHermesResponseError(
            "Hermes selected a pantry ingredient that was not provided"
        )

    return IngredientMatch(
        recipe_ingredient=recipe_ingredient,
        pantry_ingredient=decision.pantry_ingredient,
        status=decision.status,
        reasoning=decision.reasoning,
    )


async def match_recipe_ingredient(
    recipe_ingredient: str,
    pantry_ingredients: list[str],
    invoke_hermes: Callable[[SemanticMatchRequest], Awaitable[str]],
) -> IngredientMatch:
    """Framework-neutral use case with an injected Hermes invocation."""
    prepared = prepare_ingredient_match(recipe_ingredient, pantry_ingredients)

    if isinstance(prepared, IngredientMatch):
        return prepared

    try:
        raw_response = await invoke_hermes(prepared)
    except IngredientMatchingError:
        raise
    except Exception as error:
        raise HermesMatchingUnavailableError(
            "Hermes ingredient matching failed"
        ) from error

    return complete_ingredient_match(
        recipe_ingredient,
        pantry_ingredients,
        raw_response,
    )

"""Stateful workflow MCP tools for the Sabor da Maria agent."""

from __future__ import annotations

from pathlib import Path

from fastmcp import Context

from src.domain.ingredient_match import IngredientMatch, IngredientMatchStatus
from src.domain.purchasing import MarketQuote
from src.domain.recipe import Recipe
from src.domain.workflow import (
    AcceptanceResult,
    CapabilityUpdate,
    ConfirmationResult,
    FeedbackResult,
    PricingResult,
    QuoteSubmission,
    RecipeRegistration,
    WorkspaceStatus,
)
from src.infrastructure.excel_loader import load_pantry
from src.mcp.ingredient_matching.service import (
    SemanticMatchRequest,
    match_recipe_ingredient,
)
from src.mcp.requirements.service import (
    RequirementExtractionUnavailableError,
    complete_requirement_extraction,
    ensure_actionable_requirements,
    prepare_requirement_extraction,
)
from src.mcp.runtime import workflow
from src.services.workflow_service import InvalidWorkflowState


async def initialize_workspace(
    xlsx_path: str = "data/despensa_dona_maria.xlsx",
) -> WorkspaceStatus:
    """Load Dona Maria's pantry from the Excel spreadsheet and initialize
    the workspace.  Only works on a fresh (never-initialized) workspace.
    Use reset_workspace to start over from scratch.
    """
    pantry = load_pantry(Path(xlsx_path))
    return workflow.initialize_workspace(pantry)


async def reset_workspace(
    confirm: bool = False,
    xlsx_path: str = "data/despensa_dona_maria.xlsx",
) -> WorkspaceStatus:
    """Transactionally wipe ALL state (recipes, kitchen capabilities,
    purchases, reservations, budget) and reload the pantry from the
    spreadsheet.  Requires confirm=True to prevent accidents.
    """
    if not confirm:
        raise InvalidWorkflowState(
            "Pass confirm=True to reset all workspace state."
        )
    pantry = load_pantry(Path(xlsx_path))
    return workflow.reset_workspace(pantry)


async def get_workspace_status(
    xlsx_path: str = "",
    confirm: bool = False,
) -> WorkspaceStatus:
    """Return the current state of the workspace: pantry, kitchen, recipes,
    budget, and accepted menu items with next actions.

    Extra arguments are ignored so echoing initialize_workspace fields
    does not fail validation.
    """
    del xlsx_path, confirm
    return workflow.get_workspace_status()


async def register_recipe(
    recipe: Recipe,
    ctx: Context,
    interested: bool | None = None,
    feedback: str = "",
) -> RecipeRegistration:
    """Register a sourced web recipe, perform ingredient matching against
    the pantry, and return the analysis with next actions.

    Pass name, source_url, servings, ingredients and steps. Ingredient
    rows use `ingredient` (or `name`), `quantity` and `unit`. Leave
    required_equipment, required_skills and operational_requirements
    empty — the server fills them from the steps. Portuguese names and
    a missing id are fine.

    If she already said she wants to test, pass interested=true and preserve
    any concern or perceived impediment in feedback. The
    response exposes ingredient availability plus required, unknown, and
    unavailable capabilities grouped as equipment, skills, and operational
    constraints. Use those facts to conduct a concise, adaptive conversation.
    When infrastructure and skills are both unknown, ask equipment plus
    operational conditions first and wait. Ask skills in a separate next
    interaction; never combine both groups into one user-facing question.
    Do not research prices while any unknown_capabilities category is non-empty.

    Example:
    {
      "name": "Frango à Parmegiana",
      "source_url": "https://example.com/parmegiana",
      "servings": 4,
      "steps": ["Empane o frango", "Frite", "Gratine no forno"],
      "ingredients": [
        {"ingredient": "Peito de frango", "quantity": "0.6", "unit": "kg"}
      ]
    }
    """
    extraction = prepare_requirement_extraction(recipe.name, recipe.steps)
    if extraction is not None:
        try:
            response = await ctx.sample(
                messages=extraction.prompt,
                system_prompt=extraction.system_prompt,
                temperature=0.0,
                max_tokens=extraction.max_tokens,
                tools=None,
            )
            if response.text:
                recipe = complete_requirement_extraction(recipe, response.text)
            else:
                recipe = ensure_actionable_requirements(recipe)
        except Exception as error:
            if isinstance(error, RequirementExtractionUnavailableError):
                raise
            recipe = ensure_actionable_requirements(recipe)
    else:
        recipe = ensure_actionable_requirements(recipe)

    existing_matches: dict[str, IngredientMatch] = {}
    try:
        old_infos = workflow._get_match_infos(recipe.id)
        for info in old_infos:
            existing_matches[info.recipe_ingredient] = IngredientMatch(
                recipe_ingredient=info.recipe_ingredient,
                pantry_ingredient=info.pantry_ingredient,
                status=IngredientMatchStatus(info.status),
                reasoning=info.reasoning,
            )
    except Exception:
        pass

    workflow.store_recipe(recipe)

    pantry = workflow.get_pantry()
    pantry_names = [item.ingredient for item in pantry.items]

    async def invoke_hermes(request: SemanticMatchRequest) -> str:
        response = await ctx.sample(
            messages=request.prompt,
            system_prompt=request.system_prompt,
            temperature=0.0,
            max_tokens=request.max_tokens,
            tools=None,
        )
        if not response.text:
            raise ValueError("empty ingredient matching completion")
        return response.text

    matches: list[IngredientMatch] = []
    for ingredient in recipe.ingredients:
        if ingredient.ingredient in existing_matches:
            matches.append(existing_matches[ingredient.ingredient])
            continue

        try:
            match = await match_recipe_ingredient(
                ingredient.ingredient,
                pantry_names,
                invoke_hermes,
            )
        except Exception:
            match = IngredientMatch(
                recipe_ingredient=ingredient.ingredient,
                pantry_ingredient=None,
                status=IngredientMatchStatus.NOT_AVAILABLE,
                reasoning=(
                    "Ingredient matching was unavailable; "
                    "treated as missing from the pantry."
                ),
            )
        matches.append(match)

    workflow.store_ingredient_matches(recipe.id, matches)
    if interested is True:
        workflow.record_feedback(recipe.id, True, feedback)
    elif interested is False:
        workflow.record_feedback(recipe.id, False, feedback)
    return workflow.get_recipe_registration(recipe.id)


async def record_feedback(
    recipe_id: str,
    interested: bool,
    feedback: str = "",
) -> FeedbackResult:
    """Record whether Dona Maria is interested in a recipe."""
    return workflow.record_feedback(recipe_id, interested, feedback)


async def record_kitchen_capability(
    capability_type: str,
    name: str,
    available: bool,
    detail: str = "",
) -> CapabilityUpdate:
    """Record a kitchen capability by canonical name.

    Use the grouped unknown_capabilities returned by register_recipe or
    get_workspace_status. Record every fact Dona Maria provides, including
    spontaneous answers; multiple calls may be made after one grouped reply.
    capability_type must be equipment, skill, or operational. Equipment and
    operational names accept canonical keys and common Portuguese aliases.
    Follow remaining_unknowns in two stages: finish equipment/operational
    questions first, then ask unresolved skills in a separate interaction.
    """
    return workflow.record_kitchen_capability(
        capability_type, name, available, detail
    )


async def confirm_ingredient_match(
    recipe_id: str,
    recipe_ingredient: str,
    confirmed: bool,
) -> ConfirmationResult:
    """Confirm or reject an ambiguous ingredient match (needs_confirmation)."""
    return workflow.confirm_ingredient_match(
        recipe_id, recipe_ingredient, confirmed
    )


async def submit_market_quotes(
    recipe_id: str,
    quotes: list[MarketQuote] = [],
) -> QuoteSubmission:
    """Submit sourced market quotes for missing ingredients and calculate
    the purchase plan within the R$80 budget.

    This transition is blocked until positive feedback, every equipment,
    skill, and operational requirement, and every ambiguous match are resolved.
    Pass an empty list if all ingredients are from the pantry and no purchases
    are needed.
    """
    return workflow.submit_market_quotes(recipe_id, quotes)


async def prepare_recipe_pricing(
    recipe_id: str,
) -> PricingResult:
    """Calculate authoritative CMV and generate three price scenarios.

    Present every returned cmv_breakdown.calculation and every returned formula
    to Dona Maria before asking her to choose. Never replace the tool result
    with model arithmetic or a shortened list of totals.
    """
    return workflow.prepare_recipe_pricing(recipe_id)


async def accept_recipe(
    recipe_id: str,
    selected_label: str,
) -> AcceptanceResult:
    """Accept a recipe with the selected price scenario.  Transactionally
    validates feasibility, ingredient matches, stock, and budget before
    committing.
    """
    return workflow.accept_recipe(recipe_id, selected_label)

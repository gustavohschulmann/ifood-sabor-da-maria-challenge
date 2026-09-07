from decimal import Decimal

import pytest

from src.domain.ingredient_match import IngredientMatch, IngredientMatchStatus
from src.domain.pantry import Pantry, PantryItem, PurchaseInfo, Quantity
from src.domain.purchasing import MarketQuote
from src.domain.recipe import Recipe, RecipeIngredient
from src.infrastructure.database import Database
from src.services.workflow_service import (
    InvalidWorkflowState,
    RecipeNotFound,
    WorkflowService,
    WorkspaceNotInitialized,
)
from src.utils.enums import Equipment


def _pantry() -> Pantry:
    return Pantry(
        items=[
            PantryItem(
                ingredient="Chicken",
                stock=Quantity(value=Decimal("2"), unit="kg"),
                purchase=PurchaseInfo(
                    quantity=Quantity(value=Decimal("2"), unit="kg"),
                    total_paid=Decimal("28.00"),
                ),
            ),
            PantryItem(
                ingredient="Onion",
                stock=Quantity(value=Decimal("1"), unit="kg"),
                purchase=PurchaseInfo(
                    quantity=Quantity(value=Decimal("1"), unit="kg"),
                    total_paid=Decimal("4.00"),
                ),
            ),
            PantryItem(
                ingredient="Butter",
                stock=Quantity(value=Decimal("0.5"), unit="kg"),
                purchase=PurchaseInfo(
                    quantity=Quantity(value=Decimal("0.5"), unit="kg"),
                    total_paid=Decimal("20.00"),
                ),
            ),
        ]
    )


def _recipe(
    recipe_id: str = "stroganoff",
    name: str = "Chicken Stroganoff",
    ingredients: list[RecipeIngredient] | None = None,
    equipment: list[Equipment] | None = None,
    skills: list[str] | None = None,
    operational: list[str] | None = None,
) -> Recipe:
    return Recipe(
        id=recipe_id,
        name=name,
        source_url="https://example.com/stroganoff",
        servings=4,
        ingredients=ingredients if ingredients is not None else [
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("500"), unit="g"),
            RecipeIngredient(ingredient="Onion", quantity=Decimal("200"), unit="g"),
            RecipeIngredient(ingredient="Mushrooms", quantity=Decimal("100"), unit="g"),
        ],
        required_equipment=equipment if equipment is not None else [Equipment.STOVE],
        required_skills=skills if skills is not None else [],
        operational_requirements=operational if operational is not None else [],
    )


def _service() -> WorkflowService:
    db = Database(":memory:")
    return WorkflowService(db)


def _initialized_service() -> WorkflowService:
    svc = _service()
    svc.initialize_workspace(_pantry())
    return svc


def _resolve_kitchen(
    svc: WorkflowService,
    extra_equipment: list[str] | None = None,
    skills: list[str] | None = None,
) -> None:
    svc.record_kitchen_capability("equipment", "stove", True)
    for name in extra_equipment or []:
        svc.record_kitchen_capability("equipment", name, True)
    for skill in skills or []:
        svc.record_kitchen_capability("skill", skill, True)
    svc.record_kitchen_capability("operational", "stove_burners", True, "4")
    svc.record_kitchen_capability("operational", "gas_or_electric", True, "gas")
    svc.record_kitchen_capability("operational", "fridge_space", True, "enough")
    svc.record_kitchen_capability("operational", "prep_time", True, "60 min")
    svc.record_kitchen_capability("operational", "counter_space", True, "enough")


def _matches_for_recipe() -> list[IngredientMatch]:
    return [
        IngredientMatch(
            recipe_ingredient="Chicken",
            pantry_ingredient="Chicken",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact match",
        ),
        IngredientMatch(
            recipe_ingredient="Onion",
            pantry_ingredient="Onion",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact match",
        ),
        IngredientMatch(
            recipe_ingredient="Mushrooms",
            pantry_ingredient=None,
            status=IngredientMatchStatus.NOT_AVAILABLE,
            reasoning="Not in pantry",
        ),
    ]


# ── workspace ──────────────────────────────────────────────


def test_workspace_not_initialized_raises() -> None:
    svc = _service()
    with pytest.raises(WorkspaceNotInitialized):
        svc.get_pantry()


def test_initialize_workspace() -> None:
    svc = _service()
    status = svc.initialize_workspace(_pantry())
    assert status.initialized is True
    assert status.pantry_items_count == 3
    assert "Chicken" in status.pantry_ingredient_names


def test_initialize_workspace_rejects_second_call() -> None:
    svc = _initialized_service()
    with pytest.raises(InvalidWorkflowState, match="already initialized"):
        svc.initialize_workspace(_pantry())


def test_reset_workspace_clears_all_state() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)
    svc.submit_market_quotes(recipe.id, [
        MarketQuote(
            quote_id="mushrooms-quote",
            ingredient="Mushrooms",
            package_quantity=Decimal("200"),
            unit="g",
            package_price=Decimal("10.00"),
            source_url="https://example.com/mushrooms",
        ),
    ])
    svc.prepare_recipe_pricing(recipe.id)
    svc.accept_recipe(recipe.id, "equilibrado")

    status = svc.reset_workspace(_pantry())
    assert status.initialized is True
    assert status.recipes == []
    assert status.accepted_menu == []
    assert status.committed_budget == "0.00"
    assert status.remaining_budget == "80.00"
    assert status.kitchen_equipment == {}
    assert svc._db.fetchall("SELECT * FROM ingredient_matches") == []
    assert svc._db.fetchall("SELECT * FROM market_quotes") == []
    assert svc._db.fetchall("SELECT * FROM inventory_reservations") == []
    assert svc._db.fetchall("SELECT * FROM committed_purchases") == []


def test_reset_workspace_without_prior_init() -> None:
    svc = _service()
    status = svc.reset_workspace(_pantry())
    assert status.initialized is True
    assert status.pantry_items_count == 3


def test_workspace_status_shows_budget() -> None:
    svc = _initialized_service()
    status = svc.get_workspace_status()
    assert status.initial_budget == "80.00"
    assert status.remaining_budget == "80.00"


# ── recipe registration ───────────────────────────────────


def test_store_and_load_recipe() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    status = svc.get_workspace_status()
    assert len(status.recipes) == 1
    assert status.recipes[0].recipe_name == "Chicken Stroganoff"


def test_recipe_not_found_raises() -> None:
    svc = _initialized_service()
    with pytest.raises(RecipeNotFound):
        svc.record_feedback("nonexistent", True)


def test_ingredient_matches_stored() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    reg = svc.get_recipe_registration(recipe.id)
    assert len(reg.ingredient_matches) == 3
    assert "Chicken" in reg.available_from_pantry
    assert "Mushrooms" in reg.missing_ingredients


# ── feedback ───────────────────────────────────────────────


def test_record_interest() -> None:
    svc = _initialized_service()
    svc.store_recipe(_recipe())
    result = svc.record_feedback("stroganoff", True, "Looks good!")
    assert result.interested is True


def test_record_rejection() -> None:
    svc = _initialized_service()
    svc.store_recipe(_recipe())
    result = svc.record_feedback("stroganoff", False, "Too complex")
    assert result.interested is False


# ── kitchen capabilities ───────────────────────────────────


def test_record_kitchen_capability() -> None:
    svc = _initialized_service()
    result = svc.record_kitchen_capability("equipment", "stove", True)
    assert result.available is True
    kitchen = svc.get_kitchen_profile()
    assert kitchen.equipment["stove"] is True


def test_unknown_capability_type_raises() -> None:
    svc = _initialized_service()
    with pytest.raises(InvalidWorkflowState):
        svc.record_kitchen_capability("invalid", "x", True)


# ── ingredient confirmation ────────────────────────────────


def test_confirm_ambiguous_match() -> None:
    svc = _initialized_service()
    recipe = _recipe(ingredients=[
        RecipeIngredient(ingredient="óleo vegetal", quantity=Decimal("50"), unit="ml"),
    ])
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="óleo vegetal",
            pantry_ingredient="Óleo de soja",
            status=IngredientMatchStatus.NEEDS_CONFIRMATION,
            reasoning="Likely equivalent but ambiguous",
        ),
    ])
    result = svc.confirm_ingredient_match(recipe.id, "óleo vegetal", True)
    assert result.confirmed is True


def test_reject_ambiguous_match() -> None:
    svc = _initialized_service()
    recipe = _recipe(ingredients=[
        RecipeIngredient(ingredient="óleo vegetal", quantity=Decimal("50"), unit="ml"),
    ])
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="óleo vegetal",
            pantry_ingredient="Óleo de soja",
            status=IngredientMatchStatus.NEEDS_CONFIRMATION,
            reasoning="Likely equivalent but ambiguous",
        ),
    ])
    result = svc.confirm_ingredient_match(recipe.id, "óleo vegetal", False)
    assert result.confirmed is False


# ── market quotes and pricing ──────────────────────────────


def test_submit_quotes_and_prepare_pricing() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)

    quotes = [
        MarketQuote(
            quote_id="mushrooms-quote",
            ingredient="Mushrooms",
            package_quantity=Decimal("200"),
            unit="g",
            package_price=Decimal("10.00"),
            source_url="https://example.com/mushrooms",
        ),
    ]
    quote_result = svc.submit_market_quotes(recipe.id, quotes)
    assert Decimal(quote_result.cash_outlay) == Decimal("10.00")

    pricing = svc.prepare_recipe_pricing(recipe.id)
    assert pricing.recipe_name == "Chicken Stroganoff"
    assert Decimal(pricing.cmv_per_serving) > 0
    assert len(pricing.scenarios) == 3


def test_submit_quotes_over_budget_returns_feedback() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)

    quotes = [
        MarketQuote(
            quote_id="expensive-item",
            ingredient="Mushrooms",
            package_quantity=Decimal("200"),
            unit="g",
            package_price=Decimal("90.00"),
            source_url="https://example.com/mushrooms",
        ),
    ]
    result = svc.submit_market_quotes(recipe.id, quotes)
    assert result.budget_ok is False
    assert result.over_budget_by is not None
    assert Decimal(result.over_budget_by) > 0
    assert "OVER BUDGET" in result.next_actions[0]


# ── acceptance ─────────────────────────────────────────────


def _fully_prepared_service() -> tuple[WorkflowService, str]:
    """Set up a service with a recipe ready for acceptance."""
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)
    svc.submit_market_quotes(recipe.id, [
        MarketQuote(
            quote_id="mushrooms-quote",
            ingredient="Mushrooms",
            package_quantity=Decimal("200"),
            unit="g",
            package_price=Decimal("10.00"),
            source_url="https://example.com/mushrooms",
        ),
    ])
    svc.prepare_recipe_pricing(recipe.id)
    return svc, recipe.id


def test_accept_recipe() -> None:
    svc, recipe_id = _fully_prepared_service()
    result = svc.accept_recipe(recipe_id, "equilibrado")
    assert result.recipe_name == "Chicken Stroganoff"
    assert result.selected_label == "equilibrado"
    assert Decimal(result.remaining_budget) == Decimal("70.00")


def test_accepted_recipe_appears_in_menu() -> None:
    svc, recipe_id = _fully_prepared_service()
    svc.accept_recipe(recipe_id, "entrada")
    status = svc.get_workspace_status()
    assert len(status.accepted_menu) == 1
    assert status.accepted_menu[0].recipe_name == "Chicken Stroganoff"


def test_accept_without_pricing_fails() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)
    with pytest.raises(InvalidWorkflowState, match="Pricing"):
        svc.accept_recipe(recipe.id, "entrada")


def test_submit_quotes_blocked_without_feasibility() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    with pytest.raises(InvalidWorkflowState, match="Resolve kitchen"):
        svc.submit_market_quotes(recipe.id, [
            MarketQuote(
                quote_id="mushrooms-quote",
                ingredient="Mushrooms",
                package_quantity=Decimal("200"),
                unit="g",
                package_price=Decimal("10.00"),
                source_url="https://example.com/mushrooms",
            ),
        ])


def test_prepare_pricing_blocked_without_feasibility() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("500"), unit="g"),
        ],
        equipment=[Equipment.STOVE],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="Chicken",
            pantry_ingredient="Chicken",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
    ])
    svc.record_feedback(recipe.id, True)
    with pytest.raises(InvalidWorkflowState, match="Resolve kitchen"):
        svc.prepare_recipe_pricing(recipe.id)


def test_quotes_blocked_without_positive_feedback() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    _resolve_kitchen(svc)
    with pytest.raises(InvalidWorkflowState, match="positive feedback"):
        svc.submit_market_quotes(recipe.id, [])


def test_accept_with_unconfirmed_match_fails() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        ingredients=[
            RecipeIngredient(ingredient="óleo vegetal", quantity=Decimal("50"), unit="ml"),
        ],
        equipment=[],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="óleo vegetal",
            pantry_ingredient="Óleo de soja",
            status=IngredientMatchStatus.NEEDS_CONFIRMATION,
            reasoning="Ambiguous",
        ),
    ])
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)
    with pytest.raises(InvalidWorkflowState, match="Unconfirmed"):
        svc.submit_market_quotes(recipe.id, [
            MarketQuote(
                quote_id="oleo-quote",
                ingredient="óleo vegetal",
                package_quantity=Decimal("500"),
                unit="ml",
                package_price=Decimal("5.00"),
                source_url="https://example.com/oleo",
            ),
        ])


def test_invalid_price_label_fails() -> None:
    svc, recipe_id = _fully_prepared_service()
    with pytest.raises(InvalidWorkflowState, match="Unknown price label"):
        svc.accept_recipe(recipe_id, "nonexistent")


# ── state persistence ─────────────────────────────────────


def test_state_survives_service_recreation() -> None:
    db = Database(":memory:")
    svc1 = WorkflowService(db)
    svc1.initialize_workspace(_pantry())
    svc1.store_recipe(_recipe())
    svc1.store_ingredient_matches("stroganoff", _matches_for_recipe())

    svc2 = WorkflowService(db)
    status = svc2.get_workspace_status()
    assert status.initialized is True
    assert len(status.recipes) == 1
    assert status.recipes[0].recipe_name == "Chicken Stroganoff"


# ── cross-recipe budget ───────────────────────────────────


def test_cross_recipe_budget_accounting() -> None:
    svc, recipe_id = _fully_prepared_service()
    svc.accept_recipe(recipe_id, "entrada")

    status = svc.get_workspace_status()
    assert Decimal(status.remaining_budget) == Decimal("70.00")

    recipe2 = _recipe(
        recipe_id="pasta",
        name="Pasta",
        ingredients=[
            RecipeIngredient(ingredient="Onion", quantity=Decimal("100"), unit="g"),
            RecipeIngredient(ingredient="Cream", quantity=Decimal("200"), unit="ml"),
        ],
        equipment=[Equipment.STOVE],
    )
    svc.store_recipe(recipe2)
    svc.store_ingredient_matches(recipe2.id, [
        IngredientMatch(
            recipe_ingredient="Onion",
            pantry_ingredient="Onion",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact match",
        ),
        IngredientMatch(
            recipe_ingredient="Cream",
            pantry_ingredient=None,
            status=IngredientMatchStatus.NOT_AVAILABLE,
            reasoning="Not in pantry",
        ),
    ])
    svc.record_feedback(recipe2.id, True)
    _resolve_kitchen(svc)
    svc.submit_market_quotes(recipe2.id, [
        MarketQuote(
            quote_id="cream-quote",
            ingredient="Cream",
            package_quantity=Decimal("200"),
            unit="ml",
            package_price=Decimal("4.00"),
            source_url="https://example.com/cream",
        ),
    ])
    svc.prepare_recipe_pricing(recipe2.id)
    result = svc.accept_recipe(recipe2.id, "entrada")
    assert Decimal(result.remaining_budget) == Decimal("66.00")


def test_all_pantry_recipe_with_unconvertible_units() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        recipe_id="garlic_bread",
        name="Garlic Bread",
        ingredients=[
            RecipeIngredient(
                ingredient="Butter",
                quantity=Decimal("2"),
                unit="colher de sopa",
            ),
        ],
        equipment=[],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="Butter",
            pantry_ingredient="Butter",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact match",
        ),
    ])
    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)
    with pytest.raises(InvalidWorkflowState, match="Incompatible units"):
        svc.prepare_recipe_pricing(recipe.id)


# ── pantry depletion across recipes ────────────────────────


def test_inventory_reservation_reduces_available_stock() -> None:
    svc = _initialized_service()
    recipe1 = _recipe(
        recipe_id="r1",
        name="Recipe 1",
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("1500"), unit="g"),
        ],
        equipment=[],
    )
    svc.store_recipe(recipe1)
    svc.store_ingredient_matches(recipe1.id, [
        IngredientMatch(
            recipe_ingredient="Chicken",
            pantry_ingredient="Chicken",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
    ])
    svc.record_feedback(recipe1.id, True)
    _resolve_kitchen(svc)
    svc.submit_market_quotes(recipe1.id, [])
    svc.prepare_recipe_pricing(recipe1.id)
    svc.accept_recipe(recipe1.id, "entrada")

    recipe2 = _recipe(
        recipe_id="r2",
        name="Recipe 2",
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("800"), unit="g"),
        ],
        equipment=[],
    )
    svc.store_recipe(recipe2)
    svc.store_ingredient_matches(recipe2.id, [
        IngredientMatch(
            recipe_ingredient="Chicken",
            pantry_ingredient="Chicken",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
    ])
    svc.record_feedback(recipe2.id, True)

    reg = svc.get_recipe_registration(recipe2.id)
    assert "Chicken" in reg.partially_available or "Chicken" in reg.missing_ingredients


# ── end-to-end service flow ────────────────────────────────


def test_complete_flow_spreadsheet_to_menu() -> None:
    """Full journey: pantry → recipe → matching → feasibility → quotes →
    CMV → pricing → accepted menu item."""
    svc = _initialized_service()

    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())

    reg = svc.get_recipe_registration(recipe.id)
    assert reg.feasibility_status == "missing_information"
    assert "stove" in reg.feasibility_missing
    assert "gas_or_electric" in reg.feasibility_missing

    svc.record_feedback(recipe.id, True)
    _resolve_kitchen(svc)

    svc.submit_market_quotes(recipe.id, [
        MarketQuote(
            quote_id="mushrooms-quote",
            ingredient="Mushrooms",
            package_quantity=Decimal("200"),
            unit="g",
            package_price=Decimal("10.00"),
            source_url="https://example.com/mushrooms",
        ),
    ])

    pricing = svc.prepare_recipe_pricing(recipe.id)
    assert len(pricing.scenarios) == 3
    assert Decimal(pricing.cmv_per_serving) > 0

    result = svc.accept_recipe(recipe.id, "equilibrado")
    assert result.selected_label == "equilibrado"
    assert Decimal(result.remaining_budget) == Decimal("70.00")

    status = svc.get_workspace_status()
    assert len(status.accepted_menu) == 1
    assert status.accepted_menu[0].recipe_name == "Chicken Stroganoff"
    assert Decimal(status.remaining_budget) == Decimal("70.00")


def test_capability_name_normalization() -> None:
    svc = _initialized_service()
    result = svc.record_kitchen_capability("equipment", "forno", True)
    assert result.name == "oven"
    kitchen = svc.get_kitchen_profile()
    assert kitchen.equipment["oven"] is True
    assert "forno" not in kitchen.equipment


def test_duplicate_capability_record_is_noop() -> None:
    svc = _initialized_service()
    first = svc.record_kitchen_capability("equipment", "forno", True)
    second = svc.record_kitchen_capability("equipment", "oven", True)
    assert first.already_recorded is False
    assert second.already_recorded is True
    rows = svc._db.fetchall("SELECT name FROM kitchen_capabilities")
    assert [row["name"] for row in rows] == ["oven"]


def test_reregister_preserves_feedback() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.record_feedback(recipe.id, True, "Gosto")
    svc.store_recipe(recipe)
    row = svc._db.fetchone(
        "SELECT interested, feedback, purchase_plan_json, cmv_json "
        "FROM recipes WHERE id = ?",
        (recipe.id,),
    )
    assert bool(row["interested"]) is True
    assert row["feedback"] == "Gosto"
    assert row["purchase_plan_json"] is None
    assert row["cmv_json"] is None


def test_double_accept_fails() -> None:
    svc, recipe_id = _fully_prepared_service()
    svc.accept_recipe(recipe_id, "equilibrado")
    with pytest.raises(InvalidWorkflowState, match="already accepted"):
        svc.accept_recipe(recipe_id, "equilibrado")


def test_parmegiana_does_not_inherit_stroganoff_skills() -> None:
    svc = _initialized_service()
    stroganoff = _recipe(
        skills=["Mexer o molho sem queimar"],
        operational=["Água para cozinhar o arroz"],
    )
    svc.store_recipe(stroganoff)
    parmegiana = _recipe(
        recipe_id="parmegiana",
        name="Frango à Parmegiana",
        equipment=[Equipment.STOVE, Equipment.OVEN, Equipment.FRYING_PAN],
        skills=["empanar e fritar", "gratinar"],
        operational=[],
    )
    svc.store_recipe(parmegiana)
    loaded = svc._load_recipe("parmegiana")
    assert "Mexer o molho sem queimar" not in loaded.required_skills
    assert "Água para cozinhar o arroz" not in loaded.operational_requirements
    assert "empanar e fritar" in loaded.required_skills
    assert "gratinar" in loaded.required_skills
    assessment = svc.get_recipe_registration("parmegiana")
    assert "empanar e fritar" in assessment.feasibility_missing
    assert "gratinar" in assessment.feasibility_missing


def test_registration_exposes_quantity_comparison() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("1500"), unit="g"),
        ],
        equipment=[],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="Chicken",
            pantry_ingredient="Chicken",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
    ])
    reg = svc.get_recipe_registration(recipe.id)
    chicken = next(
        line for line in reg.ingredient_availability
        if line.ingredient == "Chicken"
    )
    assert chicken.status == "available"
    assert Decimal(chicken.required) == Decimal("1500.00")
    assert Decimal(chicken.available) == Decimal("2000.00")


def test_pricing_exposes_cmv_breakdown() -> None:
    svc, recipe_id = _fully_prepared_service()
    pricing = svc.prepare_recipe_pricing(recipe_id)
    assert pricing.cmv_breakdown
    chicken = next(
        line for line in pricing.cmv_breakdown
        if line.recipe_ingredient == "Chicken"
    )
    assert chicken.source == "pantry"
    assert chicken.unit_cost == "14.00"
    assert chicken.line_total == "7.00"
    assert chicken.calculation == "0.5 kg × R$ 14.00/kg = R$ 7.00"
    assert pricing.platform_fee_rate == "10%"
    assert pricing.net_revenue_formula == (
        "receita líquida = 0,90 × preço de venda"
    )
    assert pricing.minimum_price_calculation.startswith(
        f"R$ {pricing.cmv_per_serving} / 0,90 = R$ "
    )
    assert pricing.batch_cmv_calculation.endswith(
        f"= R$ {pricing.batch_cmv}"
    )
    assert pricing.cmv_per_serving_calculation == (
        f"R$ {pricing.batch_cmv} / {pricing.servings} porções = "
        f"R$ {pricing.cmv_per_serving} por porção"
    )
    assert "CMV por porção" in pricing.profit_formula
    assert "EVERY" in pricing.next_actions[0]


def test_demo_flow_reset_then_accept_all_pantry_recipe() -> None:
    svc = _initialized_service()
    leftover = _recipe()
    svc.store_recipe(leftover)
    svc.reset_workspace(_pantry())

    recipe = _recipe(
        recipe_id="parmegiana",
        name="Frango à Parmegiana (Versão Simplificada)",
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("0.6"), unit="kg"),
            RecipeIngredient(ingredient="Onion", quantity=Decimal("0.075"), unit="kg"),
            RecipeIngredient(ingredient="Butter", quantity=Decimal("0.03"), unit="kg"),
        ],
        equipment=[Equipment.STOVE, Equipment.OVEN, Equipment.FRYING_PAN],
        skills=["empanar e fritar", "gratinar"],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, [
        IngredientMatch(
            recipe_ingredient="Chicken",
            pantry_ingredient="Chicken",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
        IngredientMatch(
            recipe_ingredient="Onion",
            pantry_ingredient="Onion",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
        IngredientMatch(
            recipe_ingredient="Butter",
            pantry_ingredient="Butter",
            status=IngredientMatchStatus.MATCHED,
            reasoning="Exact",
        ),
    ])
    svc.record_feedback(recipe.id, True, "Quero testar")
    _resolve_kitchen(
        svc,
        extra_equipment=["oven", "frying_pan"],
        skills=["empanar e fritar", "gratinar"],
    )
    quotes = svc.submit_market_quotes(recipe.id, [])
    assert quotes.budget_ok is True
    assert Decimal(quotes.cash_outlay) == Decimal("0.00")

    pricing = svc.prepare_recipe_pricing(recipe.id)
    assert len(pricing.scenarios) == 3
    assert pricing.cmv_breakdown
    assert Decimal(pricing.batch_cmv) > 0

    result = svc.accept_recipe(recipe.id, "equilibrado")
    status = svc.get_workspace_status()
    assert result.selected_label == "equilibrado"
    assert Decimal(status.remaining_budget) == Decimal("80.00")
    assert len(status.accepted_menu) == 1
    assert status.accepted_menu[0].recipe_name == recipe.name
    assert leftover.id not in {item.recipe_id for item in status.accepted_menu}


def test_recipe_validation_merges_duplicates() -> None:
    recipe = Recipe(
        id="bad",
        name="Bad",
        source_url="https://example.com",
        servings=1,
        ingredients=[
            RecipeIngredient(ingredient="Chicken", quantity=Decimal("1"), unit="kg"),
            RecipeIngredient(ingredient="chicken", quantity=Decimal("2"), unit="kg"),
        ],
        required_equipment=[],
        required_skills=[],
        operational_requirements=[],
    )
    assert len(recipe.ingredients) == 1
    assert recipe.ingredients[0].quantity == Decimal("3")


def test_parmegiana_exposes_grouped_requirements() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        recipe_id="parmegiana",
        name="Frango à Parmegiana",
        equipment=[Equipment.FRYING_PAN],
        skills=[],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    loaded = svc._load_recipe(recipe.id)
    assert Equipment.STOVE in loaded.required_equipment
    assert "stove_burners" in loaded.operational_requirements

    svc.record_feedback(recipe.id, True)
    registration = svc.get_recipe_registration(recipe.id)
    assert registration.unknown_capabilities.equipment == [
        "frying_pan",
        "stove",
    ]
    assert "stove_burners" in registration.unknown_capabilities.operational

    # Answers can be recorded in any conversationally useful order. There is
    # no server-owned interview cursor.
    stove_burners_update = svc.record_kitchen_capability(
        "operational", "stove_burners", True, "4"
    )
    assert stove_burners_update.name == "stove_burners"
    assert (
        "stove_burners"
        not in stove_burners_update.remaining_unknowns.operational
    )
    kitchen = svc.get_kitchen_profile()
    assert "stove_burners 4" not in kitchen.operational
    assert kitchen.operational["stove_burners"] is True
    assert kitchen.details["stove_burners"] == "4"


def test_capability_detail_does_not_belong_in_name() -> None:
    svc = _initialized_service()
    with pytest.raises(InvalidWorkflowState, match="Unknown"):
        svc.record_kitchen_capability(
            "operational", "counter_space medium", True
        )


def test_unavailable_capability_is_returned_as_a_blocker() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)

    update = svc.record_kitchen_capability("equipment", "stove", False)

    assert update.unavailable_capabilities.equipment == ["stove"]
    assert "blocker" in update.next_actions[0]
    registration = svc.get_recipe_registration(recipe.id)
    assert registration.feasibility_status == "not_feasible"
    assert registration.unavailable_capabilities.equipment == ["stove"]


def test_registration_returns_facts_without_dialogue_script() -> None:
    svc = _initialized_service()
    recipe = _recipe(name="Frango à Parmegiana")
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    reg = svc.get_recipe_registration(recipe.id)
    payload = reg.model_dump()
    assert "next_turn" not in payload
    assert "speak" not in payload
    assert reg.ingredient_availability
    assert reg.required_capabilities.equipment == ["stove"]


def test_registration_groups_unknowns_instead_of_selecting_one() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        name="Frango à Parmegiana",
        equipment=[Equipment.FRYING_PAN, Equipment.OVEN],
        skills=["empanar", "reconhecer o ponto da carne"],
    )
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    reg = svc.get_recipe_registration(recipe.id)
    assert reg.unknown_capabilities.equipment == [
        "frying_pan",
        "oven",
        "stove",
    ]
    assert reg.unknown_capabilities.skills == [
        "empanar",
        "reconhecer o ponto da carne",
    ]
    assert "fridge_space" in reg.unknown_capabilities.operational
    assert "DO NOT include" in reg.next_actions[0]
    assert "separate" in reg.next_actions[0]


def test_pricing_returns_structured_facts_without_dialogue_script() -> None:
    svc, recipe_id = _fully_prepared_service()
    pricing = svc.prepare_recipe_pricing(recipe_id)
    assert len(pricing.scenarios) == 3
    assert pricing.cmv_breakdown
    assert "next_turn" not in pricing.model_dump()


def test_quotes_and_pricing_blocked_while_pending() -> None:
    svc = _initialized_service()
    recipe = _recipe()
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)
    registration = svc.get_recipe_registration(recipe.id)
    assert registration.unknown_capabilities.equipment == ["stove"]
    with pytest.raises(InvalidWorkflowState):
        svc.submit_market_quotes(recipe.id, [])
    with pytest.raises(InvalidWorkflowState):
        svc.prepare_recipe_pricing(recipe.id)


def test_parmegiana_cannot_reach_quotes_without_skill_answers() -> None:
    svc = _initialized_service()
    recipe = _recipe(
        recipe_id="parmegiana-skills-gate",
        name="Frango à Parmegiana",
        equipment=[],
        skills=[],
    ).model_copy(update={
        "steps": [
            "Tempere o frango.",
            "Empane em farinha, ovos e farinha de rosca.",
            "Frite até dourar e gratine no forno.",
        ]
    })
    svc.store_recipe(recipe)
    svc.store_ingredient_matches(recipe.id, _matches_for_recipe())
    svc.record_feedback(recipe.id, True)

    registration = svc.get_recipe_registration(recipe.id)
    assert "empanar" in registration.unknown_capabilities.skills
    assert "gratinar" in registration.unknown_capabilities.skills

    for name in registration.unknown_capabilities.equipment:
        svc.record_kitchen_capability("equipment", name, True)
    for name in registration.unknown_capabilities.operational:
        svc.record_kitchen_capability("operational", name, True, "adequado")

    still_pending = svc.get_recipe_registration(recipe.id)
    assert still_pending.unknown_capabilities.equipment == []
    assert still_pending.unknown_capabilities.operational == []
    assert still_pending.unknown_capabilities.skills
    assert "skills" in still_pending.next_actions[0]
    assert "Infrastructure is resolved" in still_pending.next_actions[0]

    with pytest.raises(InvalidWorkflowState, match="Resolve kitchen"):
        svc.submit_market_quotes(recipe.id, [])


def test_accept_recipe_maps_portuguese_price_choice() -> None:
    svc, recipe_id = _fully_prepared_service()
    svc.prepare_recipe_pricing(recipe_id)
    result = svc.accept_recipe(recipe_id, "no meio")
    assert result.selected_label == "equilibrado"
    assert Decimal(result.price) > 0

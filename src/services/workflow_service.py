from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.domain.costing import (
    ComplementaryPurchase,
    IngredientAllocation,
    IngredientCostSource,
    RecipeCMV,
)
from src.domain.feasibility import MissingIngredient
from src.domain.ingredient_match import IngredientMatch
from src.domain.kitchen import KitchenProfile
from src.domain.pantry import Pantry
from src.domain.pricing import RecipePricing
from src.domain.purchasing import MarketQuote, PurchasePlan
from src.domain.recipe import Recipe
from src.domain.workflow import (
    AcceptanceResult,
    CMVBreakdownLine,
    CapabilityGroups,
    CapabilityUpdate,
    ConfirmationResult,
    FeedbackResult,
    IngredientAvailabilityLine,
    IngredientMatchInfo,
    MenuItemInfo,
    PricingResult,
    QuoteSubmission,
    RecipeCandidate,
    RecipeRegistration,
    WorkspaceStatus,
)
from src.infrastructure.database import WORKSPACE_ID, Database
from src.services.costing_service import calculate_recipe_cmv
from src.services.feasibility_service import assess_recipe_feasibility
from src.services.pantry_service import compare_recipe_with_pantry
from src.services.requirements_service import (
    enrich_recipe_requirements,
    minimum_operational_for,
)
from src.services.pricing_service import generate_price_scenarios
from src.services.purchasing_service import (
    InsufficientBudget,
    plan_complementary_purchases,
)
from src.utils.unit_conversion import convert_quantity
from src.utils.capability_normalization import (
    UnknownCapabilityName,
    require_canonical_name,
)


INITIAL_BUDGET = Decimal("80.00")


class WorkflowError(RuntimeError):
    pass


class WorkspaceNotInitialized(WorkflowError):
    pass


class RecipeNotFound(WorkflowError):
    pass


class InvalidWorkflowState(WorkflowError):
    pass


def _fmt(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), ".2f")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_PRICE_LABELS = {
    "entrada": "entrada",
    "mais barato": "entrada",
    "barato": "entrada",
    "equilibrado": "equilibrado",
    "no meio": "equilibrado",
    "meio": "equilibrado",
    "maior margem": "maior margem",
    "mais caro": "maior margem",
    "caro": "maior margem",
}


def _resolve_price_label(label: str) -> str:
    return _PRICE_LABELS.get(label.strip().casefold(), label.strip())


class WorkflowService:
    def __init__(self, db: Database):
        self._db = db

    # ── workspace ──────────────────────────────────────────────

    def initialize_workspace(self, pantry: Pantry) -> WorkspaceStatus:
        row = self._db.fetchone(
            "SELECT id FROM workspace WHERE id = ?",
            (WORKSPACE_ID,),
        )
        if row:
            raise InvalidWorkflowState(
                "Workspace already initialized. "
                "Call reset_workspace to start over."
            )
        pantry_json = self._db.dumps(
            pantry.model_dump(mode="python"),
        )
        self._db.execute(
            "INSERT INTO workspace (id, initial_budget, committed_budget, "
            "pantry_json, initialized_at) VALUES (?, ?, '0.00', ?, ?)",
            (WORKSPACE_ID, str(INITIAL_BUDGET), pantry_json, _now()),
        )
        self._db.commit()
        return self.get_workspace_status()

    def reset_workspace(self, pantry: Pantry) -> WorkspaceStatus:
        """Transactionally wipe all state and reload the pantry."""
        pantry_json = self._db.dumps(
            pantry.model_dump(mode="python"),
        )
        with self._db.transaction() as cur:
            cur.execute(
                "DELETE FROM ingredient_matches WHERE recipe_id IN "
                "(SELECT id FROM recipes WHERE workspace_id = ?)",
                (WORKSPACE_ID,),
            )
            cur.execute(
                "DELETE FROM market_quotes WHERE recipe_id IN "
                "(SELECT id FROM recipes WHERE workspace_id = ?)",
                (WORKSPACE_ID,),
            )
            cur.execute(
                "DELETE FROM inventory_reservations WHERE workspace_id = ?",
                (WORKSPACE_ID,),
            )
            cur.execute(
                "DELETE FROM committed_purchases WHERE workspace_id = ?",
                (WORKSPACE_ID,),
            )
            cur.execute(
                "DELETE FROM recipes WHERE workspace_id = ?",
                (WORKSPACE_ID,),
            )
            cur.execute(
                "DELETE FROM kitchen_capabilities WHERE workspace_id = ?",
                (WORKSPACE_ID,),
            )
            row = cur.execute(
                "SELECT id FROM workspace WHERE id = ?",
                (WORKSPACE_ID,),
            ).fetchone()
            if row:
                cur.execute(
                    "UPDATE workspace SET pantry_json = ?, "
                    "committed_budget = '0.00', initialized_at = ? "
                    "WHERE id = ?",
                    (pantry_json, _now(), WORKSPACE_ID),
                )
            else:
                cur.execute(
                    "INSERT INTO workspace (id, initial_budget, "
                    "committed_budget, pantry_json, initialized_at) "
                    "VALUES (?, ?, '0.00', ?, ?)",
                    (WORKSPACE_ID, str(INITIAL_BUDGET), pantry_json, _now()),
                )
        return self.get_workspace_status()

    def _require_workspace(self) -> None:
        row = self._db.fetchone(
            "SELECT pantry_json FROM workspace WHERE id = ?",
            (WORKSPACE_ID,),
        )
        if row is None or row["pantry_json"] is None:
            raise WorkspaceNotInitialized(
                "Call initialize_workspace first."
            )

    def get_pantry(self) -> Pantry:
        self._require_workspace()
        row = self._db.fetchone(
            "SELECT pantry_json FROM workspace WHERE id = ?",
            (WORKSPACE_ID,),
        )
        return Pantry.model_validate(
            self._db.loads(row["pantry_json"])
        )

    def _get_committed_budget(self) -> Decimal:
        row = self._db.fetchone(
            "SELECT committed_budget FROM workspace WHERE id = ?",
            (WORKSPACE_ID,),
        )
        return Decimal(row["committed_budget"]) if row else Decimal("0")

    def _get_remaining_budget(self) -> Decimal:
        return INITIAL_BUDGET - self._get_committed_budget()

    # ── kitchen capabilities ───────────────────────────────────

    def get_kitchen_profile(self) -> KitchenProfile:
        rows = self._db.fetchall(
            "SELECT capability_type, name, available, detail "
            "FROM kitchen_capabilities WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
        equipment: dict[str, bool | None] = {}
        skills: dict[str, bool | None] = {}
        operational: dict[str, bool | None] = {}
        details: dict[str, str] = {}
        for r in rows:
            val = None if r["available"] is None else bool(r["available"])
            target = {
                "equipment": equipment,
                "skill": skills,
                "operational": operational,
            }[r["capability_type"]]
            target[r["name"]] = val
            if r["detail"]:
                details[r["name"]] = r["detail"]
        return KitchenProfile(
            equipment=equipment,
            skills=skills,
            operational=operational,
            details=details,
        )

    def record_kitchen_capability(
        self,
        capability_type: str,
        name: str,
        available: bool,
        detail: str = "",
    ) -> CapabilityUpdate:
        self._require_workspace()
        if capability_type not in ("equipment", "skill", "operational"):
            raise InvalidWorkflowState(
                "capability_type must be equipment, skill, or operational"
            )
        try:
            canonical = require_canonical_name(capability_type, name)
        except UnknownCapabilityName as error:
            raise InvalidWorkflowState(str(error)) from error
        stored_detail = detail.strip() or None
        existing = self._db.fetchone(
            "SELECT available, detail FROM kitchen_capabilities "
            "WHERE workspace_id = ? AND capability_type = ? AND name = ?",
            (WORKSPACE_ID, capability_type, canonical),
        )
        already = (
            existing is not None
            and bool(existing["available"]) == available
            and (existing["detail"] or None) == stored_detail
        )
        if not already:
            self._db.execute(
                "INSERT OR REPLACE INTO kitchen_capabilities "
                "(workspace_id, capability_type, name, available, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    WORKSPACE_ID,
                    capability_type,
                    canonical,
                    int(available),
                    stored_detail,
                ),
            )
            self._db.commit()
        unknowns, unavailable = self._get_all_capability_state()
        actions = self._capability_next_actions(unknowns, unavailable)
        if already:
            actions = ["Already recorded."] + actions
        return CapabilityUpdate(
            capability_type=capability_type,
            name=canonical,
            available=available,
            detail=stored_detail,
            already_recorded=already,
            remaining_unknowns=unknowns,
            unavailable_capabilities=unavailable,
            next_actions=actions,
        )

    def _get_all_capability_state(
        self,
    ) -> tuple[CapabilityGroups, CapabilityGroups]:
        recipes = self._db.fetchall(
            "SELECT recipe_json FROM recipes WHERE workspace_id = ? "
            "AND accepted = 0 AND interested IS NOT 0",
            (WORKSPACE_ID,),
        )
        kitchen = self.get_kitchen_profile()
        equipment: set[str] = set()
        skills: set[str] = set()
        operational: set[str] = set()
        unavailable_equipment: set[str] = set()
        unavailable_skills: set[str] = set()
        unavailable_operational: set[str] = set()
        for r in recipes:
            recipe = Recipe.model_validate(self._db.loads(r["recipe_json"]))
            unknown, unavailable = self._capability_state(recipe, kitchen)
            equipment.update(unknown.equipment)
            skills.update(unknown.skills)
            operational.update(unknown.operational)
            unavailable_equipment.update(unavailable.equipment)
            unavailable_skills.update(unavailable.skills)
            unavailable_operational.update(unavailable.operational)
        return (
            CapabilityGroups(
                equipment=sorted(equipment),
                skills=sorted(skills),
                operational=sorted(operational),
            ),
            CapabilityGroups(
                equipment=sorted(unavailable_equipment),
                skills=sorted(unavailable_skills),
                operational=sorted(unavailable_operational),
            ),
        )

    def _capability_next_actions(
        self,
        unknowns: CapabilityGroups,
        unavailable: CapabilityGroups,
    ) -> list[str]:
        if (
            unavailable.equipment
            or unavailable.skills
            or unavailable.operational
        ):
            return [
                "An active recipe has unavailable requirements. Explain the "
                "blocker and adapt the recipe or choose another candidate."
            ]
        if unknowns.equipment or unknowns.skills or unknowns.operational:
            if unknowns.equipment or unknowns.operational:
                skill_boundary = (
                    " Skills are also unresolved, but DO NOT include them in "
                    "this message. Wait for Dona Maria's reply, record the "
                    "infrastructure facts, then ask skills in a separate "
                    "interaction."
                    if unknowns.skills
                    else ""
                )
                return [
                    "BLOCKED before market quotes and pricing. Ask one "
                    "concise grouped question about only the unresolved "
                    "equipment and operational conditions, then wait."
                    f"{skill_boundary}"
                ]
            return [
                "BLOCKED before market quotes and pricing. Infrastructure is "
                "resolved. Ask one concise grouped question about only the "
                "unresolved skills, then wait and record the explicit answer."
            ]
        return ["All kitchen capabilities are known."]

    @staticmethod
    def _required_capabilities(recipe: Recipe) -> CapabilityGroups:
        equipment = list(dict.fromkeys(
            str(item) for item in recipe.required_equipment
        ))
        skills = list(dict.fromkeys(recipe.required_skills))
        operational = list(dict.fromkeys(
            list(recipe.operational_requirements)
            + minimum_operational_for(recipe)
        ))
        return CapabilityGroups(
            equipment=equipment,
            skills=skills,
            operational=operational,
        )

    def _capability_state(
        self,
        recipe: Recipe,
        kitchen: KitchenProfile,
    ) -> tuple[CapabilityGroups, CapabilityGroups]:
        required = self._required_capabilities(recipe)
        unknown = CapabilityGroups(
            equipment=[
                key for key in required.equipment
                if kitchen.equipment.get(key) is None
            ],
            skills=[
                key for key in required.skills
                if kitchen.skills.get(key) is None
            ],
            operational=[
                key for key in required.operational
                if kitchen.operational.get(key) is None
            ],
        )
        unavailable = CapabilityGroups(
            equipment=[
                key for key in required.equipment
                if kitchen.equipment.get(key) is False
            ],
            skills=[
                key for key in required.skills
                if kitchen.skills.get(key) is False
            ],
            operational=[
                key for key in required.operational
                if kitchen.operational.get(key) is False
            ],
        )
        return unknown, unavailable

    def _require_positive_feedback(self, recipe_id: str) -> None:
        row = self._db.fetchone(
            "SELECT interested FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        if row is None or row["interested"] is None or not bool(row["interested"]):
            raise InvalidWorkflowState(
                "Record positive feedback before continuing."
            )

    def _compare_recipe(self, recipe_id: str, recipe: Recipe):
        pantry = self.get_pantry()
        matches = self._get_match_infos(recipe_id)
        ingredient_map = {
            m.recipe_ingredient: m.pantry_ingredient
            for m in matches
            if m.pantry_ingredient and (m.confirmed or m.status == "matched")
        }
        return compare_recipe_with_pantry(
            recipe, pantry, ingredient_map, self._build_effective_stock()
        )

    def _require_compatible_units(
        self,
        recipe_id: str,
        recipe: Recipe,
    ) -> None:
        comparison = self._compare_recipe(recipe_id, recipe)
        if comparison.incompatible:
            names = [item.ingredient for item in comparison.incompatible]
            raise InvalidWorkflowState(
                f"Incompatible units for: {', '.join(names)}. "
                "Re-register the recipe using pantry units."
            )

    def _require_feasibility_resolved(
        self,
        recipe_id: str,
        recipe: Recipe,
    ) -> None:
        """Raise if feasibility has not been fully resolved for this recipe."""
        self._require_positive_feedback(recipe_id)
        self._require_compatible_units(recipe_id, recipe)
        kitchen = self.get_kitchen_profile()
        assessment = assess_recipe_feasibility(recipe, kitchen)
        if assessment.status == "not_feasible":
            raise InvalidWorkflowState(
                f"Recipe is not feasible: "
                f"{', '.join(assessment.blocking_reasons)}"
            )
        if assessment.status == "missing_information":
            raise InvalidWorkflowState(
                f"Resolve kitchen capabilities first: "
                f"{', '.join(assessment.missing_information)}"
            )
        unconfirmed = self._db.fetchall(
            "SELECT recipe_ingredient FROM ingredient_matches "
            "WHERE recipe_id = ? AND status = 'needs_confirmation' "
            "AND confirmed = 0",
            (recipe_id,),
        )
        if unconfirmed:
            names = [r["recipe_ingredient"] for r in unconfirmed]
            raise InvalidWorkflowState(
                f"Unconfirmed ingredient matches: {', '.join(names)}"
            )

    # ── recipes ────────────────────────────────────────────────

    def store_recipe(self, recipe: Recipe) -> None:
        self._require_workspace()
        recipe = enrich_recipe_requirements(recipe)
        recipe_json = self._db.dumps(
            recipe.model_dump(mode="python")
        )
        existing = self._db.fetchone(
            "SELECT interested, feedback FROM recipes WHERE id = ?",
            (recipe.id,),
        )
        if existing:
            self._db.execute(
                "UPDATE recipes SET name = ?, source_url = ?, servings = ?, "
                "recipe_json = ?, purchase_plan_json = NULL, cmv_json = NULL, "
                "pricing_json = NULL, selected_price_label = NULL, "
                "accepted = 0 WHERE id = ?",
                (
                    recipe.name,
                    recipe.source_url,
                    recipe.servings,
                    recipe_json,
                    recipe.id,
                ),
            )
        else:
            self._db.execute(
                "INSERT INTO recipes "
                "(id, workspace_id, name, source_url, servings, recipe_json, "
                "interested, feedback, accepted, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?)",
                (
                    recipe.id,
                    WORKSPACE_ID,
                    recipe.name,
                    recipe.source_url,
                    recipe.servings,
                    recipe_json,
                    _now(),
                ),
            )
        self._db.commit()

    def store_ingredient_matches(
        self,
        recipe_id: str,
        matches: list[IngredientMatch],
    ) -> None:
        self._db.execute(
            "DELETE FROM ingredient_matches WHERE recipe_id = ?",
            (recipe_id,),
        )
        for m in matches:
            confirmed = 1 if m.status.value == "matched" else 0
            self._db.execute(
                "INSERT INTO ingredient_matches "
                "(recipe_id, recipe_ingredient, pantry_ingredient, status, "
                "confirmed, reasoning) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    recipe_id,
                    m.recipe_ingredient,
                    m.pantry_ingredient,
                    m.status.value,
                    confirmed,
                    m.reasoning,
                ),
            )
        self._db.commit()

    def get_recipe_registration(
        self,
        recipe_id: str,
    ) -> RecipeRegistration:
        self._require_workspace()
        recipe = self._load_recipe(recipe_id)
        matches = self._get_match_infos(recipe_id)
        pantry = self.get_pantry()
        kitchen = self.get_kitchen_profile()
        assessment = assess_recipe_feasibility(recipe, kitchen)
        unknown, unavailable = self._capability_state(recipe, kitchen)

        ingredient_map = {
            m.recipe_ingredient: m.pantry_ingredient
            for m in matches
            if m.pantry_ingredient and m.confirmed
        }
        effective = self._build_effective_stock()
        comparison = compare_recipe_with_pantry(
            recipe, pantry, ingredient_map, effective
        )
        availability = self._build_availability_lines(comparison)
        interested = self._recipe_interest(recipe_id)
        next_actions = self._derive_recipe_next_actions(
            recipe_id, recipe, matches, assessment,
            comparison, has_plan=False, has_cmv=False,
            has_pricing=False, selected_label=None,
            accepted=False, interested=interested,
        )
        return RecipeRegistration(
            recipe_id=recipe_id,
            recipe_name=recipe.name,
            ingredient_matches=matches,
            ingredient_availability=availability,
            available_from_pantry=[
                a.ingredient for a in comparison.available
            ],
            partially_available=[
                p.ingredient for p in comparison.partial
            ],
            missing_ingredients=[
                m.ingredient for m in comparison.missing
            ],
            feasibility_status=assessment.status,
            feasibility_missing=assessment.missing_information,
            feasibility_blocking=assessment.blocking_reasons,
            required_capabilities=self._required_capabilities(recipe),
            unknown_capabilities=unknown,
            unavailable_capabilities=unavailable,
            next_actions=next_actions,
        )

    def _load_recipe(self, recipe_id: str) -> Recipe:
        row = self._db.fetchone(
            "SELECT recipe_json FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        if row is None:
            raise RecipeNotFound(f"Recipe '{recipe_id}' not found")
        return Recipe.model_validate(self._db.loads(row["recipe_json"]))

    def _get_match_infos(self, recipe_id: str) -> list[IngredientMatchInfo]:
        rows = self._db.fetchall(
            "SELECT recipe_ingredient, pantry_ingredient, status, "
            "confirmed, reasoning FROM ingredient_matches "
            "WHERE recipe_id = ?",
            (recipe_id,),
        )
        return [
            IngredientMatchInfo(
                recipe_ingredient=r["recipe_ingredient"],
                pantry_ingredient=r["pantry_ingredient"],
                status=r["status"],
                confirmed=bool(r["confirmed"]),
                reasoning=r["reasoning"] or "",
            )
            for r in rows
        ]

    # ── feedback ───────────────────────────────────────────────

    def record_feedback(
        self,
        recipe_id: str,
        interested: bool,
        feedback: str = "",
    ) -> FeedbackResult:
        self._require_workspace()
        self._load_recipe(recipe_id)
        self._db.execute(
            "UPDATE recipes SET interested = ?, feedback = ? WHERE id = ?",
            (int(interested), feedback, recipe_id),
        )
        self._db.commit()
        if not interested:
            return FeedbackResult(
                recipe_id=recipe_id,
                interested=interested,
                next_actions=["Search for another recipe."],
            )
        return FeedbackResult(
            recipe_id=recipe_id,
            interested=interested,
            next_actions=[
                "STOP before market research. Resolve every item in "
                "unknown_capabilities (including skills), every item in "
                "unavailable_capabilities, and every ambiguous ingredient "
                "match before submitting market quotes."
            ],
        )

    # ── ingredient confirmation ────────────────────────────────

    def confirm_ingredient_match(
        self,
        recipe_id: str,
        recipe_ingredient: str,
        confirmed: bool,
    ) -> ConfirmationResult:
        self._require_workspace()
        self._load_recipe(recipe_id)
        if confirmed:
            self._db.execute(
                "UPDATE ingredient_matches SET confirmed = 1 "
                "WHERE recipe_id = ? AND recipe_ingredient = ?",
                (recipe_id, recipe_ingredient),
            )
        else:
            self._db.execute(
                "UPDATE ingredient_matches SET status = 'not_available', "
                "pantry_ingredient = NULL, confirmed = 0 "
                "WHERE recipe_id = ? AND recipe_ingredient = ?",
                (recipe_id, recipe_ingredient),
            )
        self._db.commit()
        return ConfirmationResult(
            recipe_id=recipe_id,
            recipe_ingredient=recipe_ingredient,
            confirmed=confirmed,
            next_actions=self._pending_actions_for_recipe(recipe_id),
        )

    # ── market quotes ──────────────────────────────────────────

    def submit_market_quotes(
        self,
        recipe_id: str,
        quotes: list[MarketQuote],
    ) -> QuoteSubmission:
        self._require_workspace()
        recipe = self._load_recipe(recipe_id)
        self._require_feasibility_resolved(recipe_id, recipe)

        self._db.execute(
            "DELETE FROM market_quotes WHERE recipe_id = ?",
            (recipe_id,),
        )
        for q in quotes:
            self._db.execute(
                "INSERT INTO market_quotes "
                "(quote_id, recipe_id, ingredient, package_quantity, "
                "unit, package_price, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    q.quote_id,
                    recipe_id,
                    q.ingredient,
                    str(q.package_quantity),
                    q.unit,
                    str(q.package_price),
                    q.source_url,
                ),
            )

        missing = self._missing_for_recipe(recipe_id, recipe)
        committed = self._get_committed_budget()

        try:
            plan = plan_complementary_purchases(missing, quotes, committed)
        except InsufficientBudget:
            available = INITIAL_BUDGET - committed
            total_cost = sum(q.package_price for q in quotes)
            over_by = total_cost - available
            self._db.commit()
            per_item = ", ".join(
                f"{q.ingredient} R${_fmt(q.package_price)}" for q in quotes
            )
            return QuoteSubmission(
                recipe_id=recipe_id,
                cash_outlay=_fmt(total_cost),
                remaining_budget=_fmt(available),
                budget_ok=False,
                over_budget_by=_fmt(over_by),
                next_actions=[
                    f"OVER BUDGET by R${_fmt(over_by)}. "
                    f"Available: R${_fmt(available)}. "
                    f"Quotes: {per_item}. "
                    "Ask Dona Maria which ingredients to remove or "
                    "find cheaper alternatives, then resubmit quotes."
                ],
            )

        self._db.execute(
            "UPDATE recipes SET purchase_plan_json = ? WHERE id = ?",
            (self._db.dumps(plan.model_dump(mode="python")), recipe_id),
        )
        self._db.commit()

        return QuoteSubmission(
            recipe_id=recipe_id,
            cash_outlay=_fmt(plan.cash_outlay),
            remaining_budget=_fmt(plan.remaining_budget),
            budget_ok=True,
            next_actions=[
                "Call prepare_recipe_pricing to calculate CMV and "
                "generate price scenarios."
            ],
        )

    # ── pricing ────────────────────────────────────────────────

    def prepare_recipe_pricing(
        self,
        recipe_id: str,
    ) -> PricingResult:
        self._require_workspace()
        recipe = self._load_recipe(recipe_id)
        self._require_feasibility_resolved(recipe_id, recipe)
        pantry = self.get_pantry()

        plan_row = self._db.fetchone(
            "SELECT purchase_plan_json FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        if plan_row and plan_row["purchase_plan_json"]:
            plan = PurchasePlan.model_validate(
                self._db.loads(plan_row["purchase_plan_json"])
            )
        else:
            plan = None

        allocations = self._build_allocations(recipe_id, recipe, plan)
        comp_purchases: list[ComplementaryPurchase] = (
            plan.complementary_purchases if plan else []
        )
        cmv = calculate_recipe_cmv(
            recipe, pantry, allocations, comp_purchases
        )
        pricing = generate_price_scenarios(cmv.cmv_per_serving)

        self._db.execute(
            "UPDATE recipes SET cmv_json = ?, pricing_json = ? WHERE id = ?",
            (
                self._db.dumps(cmv.model_dump(mode="python")),
                self._db.dumps(pricing.model_dump(mode="python")),
                recipe_id,
            ),
        )
        self._db.commit()

        breakdown = [
            CMVBreakdownLine(
                recipe_ingredient=ing.recipe_ingredient,
                source=comp.source.value,
                quantity_used=format(comp.quantity_used.normalize(), "f"),
                unit=comp.unit,
                unit_cost=_fmt(comp.unit_cost),
                cost_unit=comp.cost_unit,
                line_total=_fmt(comp.total_cost),
                calculation=(
                    f"{format(comp.quantity_used.normalize(), 'f')} "
                    f"{comp.unit} × R$ {_fmt(comp.unit_cost)}/"
                    f"{comp.cost_unit} = R$ {_fmt(comp.total_cost)}"
                ),
            )
            for ing in cmv.ingredients
            for comp in ing.components
        ]
        scenarios = [
            {
                "label": s.label,
                "price": _fmt(s.price),
                "platform_fee": _fmt(s.platform_fee),
                "net_revenue": _fmt(s.net_revenue),
                "profit": _fmt(s.profit),
            }
            for s in pricing.scenarios
        ]
        return PricingResult(
            recipe_id=recipe_id,
            recipe_name=recipe.name,
            servings=recipe.servings,
            batch_cmv=_fmt(cmv.batch_cmv),
            cmv_per_serving=_fmt(cmv.cmv_per_serving),
            batch_cmv_calculation=(
                " + ".join(
                    f"R$ {line.line_total}" for line in breakdown
                )
                + f" = R$ {_fmt(cmv.batch_cmv)}"
            ),
            cmv_per_serving_calculation=(
                f"R$ {_fmt(cmv.batch_cmv)} / {recipe.servings} porções = "
                f"R$ {_fmt(cmv.cmv_per_serving)} por porção"
            ),
            minimum_price=_fmt(pricing.minimum_price),
            platform_fee_rate="10%",
            net_revenue_formula="receita líquida = 0,90 × preço de venda",
            minimum_price_calculation=(
                f"R$ {_fmt(cmv.cmv_per_serving)} / 0,90 = "
                f"R$ {_fmt(pricing.minimum_price)}"
            ),
            profit_formula="lucro = 0,90 × preço de venda − CMV por porção",
            cmv_breakdown=breakdown,
            scenarios=scenarios,
            next_actions=[
                "Before asking Dona Maria to choose, present EVERY "
                "cmv_breakdown.calculation, batch_cmv_calculation, "
                "cmv_per_serving_calculation, the 10% fee, "
                "minimum_price_calculation, profit_formula, and all three "
                "scenarios. Do not recompute or omit line items."
            ],
        )

    # ── acceptance ─────────────────────────────────────────────

    def accept_recipe(
        self,
        recipe_id: str,
        selected_label: str,
    ) -> AcceptanceResult:
        self._require_workspace()
        recipe = self._load_recipe(recipe_id)

        already = self._db.fetchone(
            "SELECT accepted FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        if already and bool(already["accepted"]):
            raise InvalidWorkflowState(
                "Recipe already accepted. Cannot accept again."
            )

        kitchen = self.get_kitchen_profile()

        assessment = assess_recipe_feasibility(recipe, kitchen)
        if assessment.status == "not_feasible":
            raise InvalidWorkflowState(
                f"Recipe is not feasible: "
                f"{', '.join(assessment.blocking_reasons)}"
            )
        if assessment.status == "missing_information":
            raise InvalidWorkflowState(
                f"Kitchen capabilities still unknown: "
                f"{', '.join(assessment.missing_information)}"
            )

        unconfirmed = self._db.fetchall(
            "SELECT recipe_ingredient FROM ingredient_matches "
            "WHERE recipe_id = ? AND status = 'needs_confirmation' "
            "AND confirmed = 0",
            (recipe_id,),
        )
        if unconfirmed:
            names = [r["recipe_ingredient"] for r in unconfirmed]
            raise InvalidWorkflowState(
                f"Unconfirmed ingredient matches: {', '.join(names)}"
            )

        pricing_row = self._db.fetchone(
            "SELECT pricing_json, purchase_plan_json FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        if not pricing_row or not pricing_row["pricing_json"]:
            raise InvalidWorkflowState(
                "Pricing has not been calculated yet."
            )

        pricing = RecipePricing.model_validate(
            self._db.loads(pricing_row["pricing_json"])
        )
        selected_label = _resolve_price_label(selected_label)
        scenario = next(
            (s for s in pricing.scenarios if s.label == selected_label),
            None,
        )
        if scenario is None:
            labels = [s.label for s in pricing.scenarios]
            raise InvalidWorkflowState(
                f"Unknown price label '{selected_label}'. "
                f"Available: {', '.join(labels)}"
            )

        plan: PurchasePlan | None = None
        if pricing_row["purchase_plan_json"]:
            plan = PurchasePlan.model_validate(
                self._db.loads(pricing_row["purchase_plan_json"])
            )

        with self._db.transaction() as cur:
            matches = self._get_match_infos(recipe_id)
            pantry = self.get_pantry()

            for ingredient in recipe.ingredients:
                match_info = next(
                    (m for m in matches
                     if m.recipe_ingredient == ingredient.ingredient),
                    None,
                )
                if (
                    match_info
                    and match_info.pantry_ingredient
                    and match_info.status in ("matched", "needs_confirmation")
                    and match_info.confirmed
                ):
                    pantry_item = pantry.get(match_info.pantry_ingredient)
                    if pantry_item is None:
                        continue
                    eff_qty, eff_unit = self._effective_stock_for(
                        match_info.pantry_ingredient, pantry_item
                    )
                    needed = convert_quantity(
                        ingredient.quantity, ingredient.unit, eff_unit
                    )
                    if needed is None:
                        continue
                    reserve = min(needed, eff_qty)
                    if reserve > 0:
                        cur.execute(
                            "INSERT INTO inventory_reservations "
                            "(workspace_id, recipe_id, pantry_ingredient, "
                            "quantity, unit) VALUES (?, ?, ?, ?, ?)",
                            (
                                WORKSPACE_ID,
                                recipe_id,
                                match_info.pantry_ingredient,
                                str(reserve),
                                eff_unit,
                            ),
                        )

            if plan:
                for req in plan.requirements:
                    cur.execute(
                        "INSERT INTO committed_purchases "
                        "(workspace_id, recipe_id, purchase_id, ingredient, "
                        "purchased_quantity, unit, cash_outlay, "
                        "quantity_used, quantity_remaining) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            WORKSPACE_ID,
                            recipe_id,
                            req.purchase_id,
                            req.ingredient,
                            str(
                                Decimal(req.packages_required)
                                * req.package_quantity
                            ),
                            req.package_unit,
                            str(req.cash_outlay),
                            str(req.required_quantity),
                            str(
                                Decimal(req.packages_required)
                                * req.package_quantity
                                - convert_quantity(
                                    req.required_quantity,
                                    req.required_unit,
                                    req.package_unit,
                                )
                            ),
                        ),
                    )
                new_committed = (
                    self._get_committed_budget() + plan.cash_outlay
                )
                cur.execute(
                    "UPDATE workspace SET committed_budget = ? "
                    "WHERE id = ?",
                    (str(new_committed), WORKSPACE_ID),
                )

            cur.execute(
                "UPDATE recipes SET selected_price_label = ?, accepted = 1 "
                "WHERE id = ?",
                (selected_label, recipe_id),
            )

        remaining = _fmt(self._get_remaining_budget())
        return AcceptanceResult(
            recipe_id=recipe_id,
            recipe_name=recipe.name,
            selected_label=selected_label,
            price=_fmt(scenario.price),
            profit=_fmt(scenario.profit),
            remaining_budget=remaining,
            next_actions=[
                "Recipe accepted! Search for more recipes or "
                "call get_workspace_status to review the menu."
            ],
        )

    # ── workspace status ───────────────────────────────────────

    def get_workspace_status(self) -> WorkspaceStatus:
        ws = self._db.fetchone(
            "SELECT * FROM workspace WHERE id = ?", (WORKSPACE_ID,)
        )
        if ws is None or ws["pantry_json"] is None:
            return WorkspaceStatus(
                initialized=False,
                initial_budget=_fmt(INITIAL_BUDGET),
                committed_budget="0.00",
                remaining_budget=_fmt(INITIAL_BUDGET),
                pantry_items_count=0,
                pantry_ingredient_names=[],
                kitchen_equipment={},
                kitchen_skills={},
                kitchen_operational={},
                kitchen_details={},
                recipes=[],
                accepted_menu=[],
                next_actions=[
                    "Call initialize_workspace to load the pantry "
                    "from despensa_dona_maria.xlsx."
                ],
            )

        pantry = Pantry.model_validate(self._db.loads(ws["pantry_json"]))
        kitchen = self.get_kitchen_profile()
        committed = Decimal(ws["committed_budget"])
        remaining = INITIAL_BUDGET - committed

        recipe_rows = self._db.fetchall(
            "SELECT * FROM recipes WHERE workspace_id = ?",
            (WORKSPACE_ID,),
        )
        recipes: list[RecipeCandidate] = []
        accepted_menu: list[MenuItemInfo] = []

        for rr in recipe_rows:
            recipe = Recipe.model_validate(
                self._db.loads(rr["recipe_json"])
            )
            matches = self._get_match_infos(rr["id"])
            assessment = assess_recipe_feasibility(recipe, kitchen)
            unknown, unavailable = self._capability_state(recipe, kitchen)
            has_plan = rr["purchase_plan_json"] is not None
            has_cmv = rr["cmv_json"] is not None
            has_pricing = rr["pricing_json"] is not None
            cmv_per_serving = None
            if has_cmv:
                cmv = RecipeCMV.model_validate(
                    self._db.loads(rr["cmv_json"])
                )
                cmv_per_serving = _fmt(cmv.cmv_per_serving)

            interested = (
                None if rr["interested"] is None
                else bool(rr["interested"])
            )
            accepted = bool(rr["accepted"])
            comparison = (
                None if accepted
                else self._compare_recipe(rr["id"], recipe)
            )
            next_actions = self._derive_recipe_next_actions(
                rr["id"], recipe, matches, assessment,
                comparison, has_plan, has_cmv, has_pricing,
                rr["selected_price_label"], accepted, interested,
            )
            recipes.append(
                RecipeCandidate(
                    recipe_id=rr["id"],
                    recipe_name=rr["name"],
                    source_url=rr["source_url"],
                    servings=rr["servings"],
                    interested=interested,
                    feedback=rr["feedback"],
                    accepted=accepted,
                    ingredient_matches=matches,
                    feasibility_status=assessment.status,
                    feasibility_missing=assessment.missing_information,
                    feasibility_blocking=assessment.blocking_reasons,
                    required_capabilities=self._required_capabilities(recipe),
                    unknown_capabilities=unknown,
                    unavailable_capabilities=unavailable,
                    has_purchase_plan=has_plan,
                    has_cmv=has_cmv,
                    cmv_per_serving=cmv_per_serving,
                    has_pricing=has_pricing,
                    selected_price_label=rr["selected_price_label"],
                    next_actions=next_actions,
                )
            )

            if accepted and has_pricing:
                pricing = RecipePricing.model_validate(
                    self._db.loads(rr["pricing_json"])
                )
                label = rr["selected_price_label"]
                sc = next(
                    (s for s in pricing.scenarios if s.label == label),
                    None,
                )
                if sc:
                    accepted_menu.append(
                        MenuItemInfo(
                            recipe_id=rr["id"],
                            recipe_name=rr["name"],
                            cmv_per_serving=cmv_per_serving or "0.00",
                            price=_fmt(sc.price),
                            profit=_fmt(sc.profit),
                            label=label,
                        )
                    )

        global_actions: list[str] = []
        if not recipes:
            global_actions.append(
                "Pantry is already loaded. Do not call initialize_workspace. "
                "Call web_search now in this same turn with a short query, "
                "present the sourced recipe, and wait. Do not call "
                "register_recipe until Dona Maria says she wants to test it."
            )
        else:
            pending = [
                recipe
                for recipe in recipes
                if not recipe.accepted and recipe.interested is not False
            ]
            if pending:
                global_actions.append(
                    "Continue resolving pending recipes."
                )
            global_actions.append(
                "Search for more recipe candidates or finalize the menu."
            )

        return WorkspaceStatus(
            initialized=True,
            initial_budget=_fmt(INITIAL_BUDGET),
            committed_budget=_fmt(committed),
            remaining_budget=_fmt(remaining),
            pantry_items_count=len(pantry.items),
            pantry_ingredient_names=[
                item.ingredient for item in pantry.items
            ],
            kitchen_equipment=kitchen.equipment,
            kitchen_skills=kitchen.skills,
            kitchen_operational=kitchen.operational,
            kitchen_details=kitchen.details,
            recipes=recipes,
            accepted_menu=accepted_menu,
            next_actions=global_actions,
        )

    # ── internal helpers ───────────────────────────────────────

    def _recipe_interest(self, recipe_id: str) -> bool | None:
        row = self._db.fetchone(
            "SELECT interested FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        if row is None or row["interested"] is None:
            return None
        return bool(row["interested"])

    @staticmethod
    def _build_availability_lines(
        comparison: object,
    ) -> list[IngredientAvailabilityLine]:
        from src.domain.feasibility import PantryComparison

        if not isinstance(comparison, PantryComparison):
            return []
        lines: list[IngredientAvailabilityLine] = []
        for a in comparison.available:
            lines.append(IngredientAvailabilityLine(
                ingredient=a.ingredient,
                required=_fmt(a.required),
                available=_fmt(a.available),
                missing="0.00",
                unit=a.unit,
                status="available",
            ))
        for p in comparison.partial:
            lines.append(IngredientAvailabilityLine(
                ingredient=p.ingredient,
                required=_fmt(p.required),
                available=_fmt(p.available),
                missing=_fmt(p.missing),
                unit=p.unit,
                status="partial",
            ))
        for m in comparison.missing:
            lines.append(IngredientAvailabilityLine(
                ingredient=m.ingredient,
                required=_fmt(m.quantity),
                available="0.00",
                missing=_fmt(m.quantity),
                unit=m.unit,
                status="missing",
            ))
        for item in comparison.incompatible:
            lines.append(IngredientAvailabilityLine(
                ingredient=item.ingredient,
                required=_fmt(item.required),
                available="?",
                missing=_fmt(item.required),
                unit=item.recipe_unit,
                status="incompatible",
            ))
        return lines

    def _missing_for_recipe(
        self,
        recipe_id: str,
        recipe: Recipe,
    ) -> list[MissingIngredient]:
        pantry = self.get_pantry()
        matches = self._get_match_infos(recipe_id)
        ingredient_map = {
            m.recipe_ingredient: m.pantry_ingredient
            for m in matches
            if m.pantry_ingredient and (m.confirmed or m.status == "matched")
        }
        effective = self._build_effective_stock()
        comparison = compare_recipe_with_pantry(
            recipe, pantry, ingredient_map, effective
        )
        result: list[MissingIngredient] = list(comparison.missing)
        for p in comparison.partial:
            result.append(
                MissingIngredient(
                    ingredient=p.ingredient,
                    quantity=p.missing,
                    unit=p.unit,
                )
            )
        return result

    def _build_allocations(
        self,
        recipe_id: str,
        recipe: Recipe,
        plan: PurchasePlan | None,
    ) -> list[IngredientAllocation]:
        pantry = self.get_pantry()
        matches = self._get_match_infos(recipe_id)
        match_map = {m.recipe_ingredient: m for m in matches}
        effective = self._build_effective_stock()
        purchase_allocs = {
            a.recipe_ingredient: a
            for a in (plan.ingredient_allocations if plan else [])
        }

        allocations: list[IngredientAllocation] = []

        for ingredient in recipe.ingredients:
            m = match_map.get(ingredient.ingredient)
            pantry_ingredient = (
                m.pantry_ingredient
                if m and m.pantry_ingredient
                and (m.confirmed or m.status == "matched")
                else None
            )

            if pantry_ingredient:
                pantry_item = pantry.get(pantry_ingredient)
                if pantry_item:
                    eff_qty, eff_unit = (
                        effective[pantry_ingredient]
                        if pantry_ingredient in effective
                        else self._effective_stock_for(
                            pantry_ingredient, pantry_item
                        )
                    )
                    needed = convert_quantity(
                        ingredient.quantity, ingredient.unit, eff_unit
                    )
                    if needed is not None and eff_qty > 0:
                        from_pantry = min(needed, eff_qty)
                        from_pantry_recipe_unit = convert_quantity(
                            from_pantry, eff_unit, ingredient.unit
                        )
                        if (
                            from_pantry_recipe_unit
                            and from_pantry_recipe_unit > 0
                        ):
                            allocations.append(
                                IngredientAllocation(
                                    recipe_ingredient=ingredient.ingredient,
                                    source=IngredientCostSource.PANTRY,
                                    quantity=from_pantry_recipe_unit,
                                    unit=ingredient.unit,
                                    pantry_ingredient=pantry_ingredient,
                                )
                            )
                            if from_pantry_recipe_unit >= ingredient.quantity:
                                continue
                    elif needed is None and eff_qty > 0:
                        allocations.append(
                            IngredientAllocation(
                                recipe_ingredient=ingredient.ingredient,
                                source=IngredientCostSource.PANTRY,
                                quantity=ingredient.quantity,
                                unit=eff_unit,
                                pantry_ingredient=pantry_ingredient,
                            )
                        )
                        continue

            pa = purchase_allocs.get(ingredient.ingredient)
            if pa:
                allocations.append(pa)

        return allocations

    def _build_effective_stock(
        self,
    ) -> dict[str, tuple[Decimal, str]]:
        pantry = self.get_pantry()
        result: dict[str, tuple[Decimal, str]] = {}
        for item in pantry.items:
            eff_qty, eff_unit = self._effective_stock_for(
                item.ingredient, item
            )
            result[item.ingredient] = (eff_qty, eff_unit)
        return result

    def _effective_stock_for(
        self,
        ingredient_name: str,
        pantry_item: object,
    ) -> tuple[Decimal, str]:
        from src.domain.pantry import PantryItem

        if not isinstance(pantry_item, PantryItem):
            return (Decimal("0"), "un")

        if pantry_item.package:
            total = (
                pantry_item.stock.value
                * pantry_item.package.content_quantity
            )
            unit = pantry_item.package.content_unit
        else:
            total = pantry_item.stock.value
            unit = pantry_item.stock.unit

        reservations = self._db.fetchall(
            "SELECT quantity, unit FROM inventory_reservations "
            "WHERE workspace_id = ? AND pantry_ingredient = ?",
            (WORKSPACE_ID, ingredient_name),
        )
        for r in reservations:
            reserved_qty = Decimal(r["quantity"])
            converted = convert_quantity(reserved_qty, r["unit"], unit)
            if converted is not None:
                total -= converted

        purchased = self._db.fetchall(
            "SELECT quantity_remaining, unit FROM committed_purchases "
            "WHERE workspace_id = ? AND ingredient = ?",
            (WORKSPACE_ID, ingredient_name),
        )
        for p in purchased:
            remaining = Decimal(p["quantity_remaining"])
            converted = convert_quantity(remaining, p["unit"], unit)
            if converted is not None:
                total += converted

        return (max(total, Decimal("0")), unit)

    def _derive_recipe_next_actions(
        self,
        recipe_id: str,
        recipe: Recipe,
        matches: list[IngredientMatchInfo],
        assessment: object,
        comparison: object | None,
        has_plan: bool,
        has_cmv: bool,
        has_pricing: bool,
        selected_label: str | None,
        accepted: bool,
        interested: bool | None,
    ) -> list[str]:
        from src.domain.feasibility import FeasibilityAssessment

        if accepted:
            return ["Recipe accepted and on the menu."]

        if interested is None:
            return [
                "Present this recipe to Dona Maria and "
                "call record_feedback with her response."
            ]

        if interested is False:
            return ["Recipe rejected. Search for alternatives."]

        from src.domain.feasibility import PantryComparison

        if isinstance(comparison, PantryComparison) and comparison.incompatible:
            names = [item.ingredient for item in comparison.incompatible]
            return [
                "Re-register the recipe using pantry units for: "
                f"{', '.join(names)}"
            ]

        actions: list[str] = []

        if isinstance(assessment, FeasibilityAssessment):
            if assessment.status == "not_feasible":
                return [
                    f"Recipe is not feasible: "
                    f"{', '.join(assessment.blocking_reasons)}"
                ]
            if assessment.missing_information:
                unknowns, unavailable = self._capability_state(
                    recipe, self.get_kitchen_profile()
                )
                actions.extend(
                    self._capability_next_actions(unknowns, unavailable)
                )

        unconfirmed = [
            m for m in matches
            if m.status == "needs_confirmation" and not m.confirmed
        ]
        if unconfirmed:
            names = [m.recipe_ingredient for m in unconfirmed]
            actions.append(
                f"Confirm ingredient matches: {', '.join(names)}"
            )

        if actions:
            return actions

        if not has_plan:
            missing = self._missing_for_recipe(recipe_id, recipe)
            if missing:
                names = [m.ingredient for m in missing]
                actions.append(
                    f"Search for market quotes for: "
                    f"{', '.join(names)}. "
                    f"Then call submit_market_quotes."
                )
            else:
                actions.append(
                    "All ingredients are from the pantry — no "
                    "purchases needed. Call prepare_recipe_pricing."
                )
        elif not has_cmv:
            actions.append("Call prepare_recipe_pricing.")
        elif not has_pricing:
            actions.append("Call prepare_recipe_pricing.")
        elif not selected_label:
            actions.append(
                "Present pricing scenarios to Dona Maria and "
                "call accept_recipe with her chosen label."
            )

        return actions or ["Ready for next step."]

    def _pending_actions_for_recipe(
        self,
        recipe_id: str,
    ) -> list[str]:
        recipe = self._load_recipe(recipe_id)
        matches = self._get_match_infos(recipe_id)
        kitchen = self.get_kitchen_profile()
        assessment = assess_recipe_feasibility(recipe, kitchen)
        row = self._db.fetchone(
            "SELECT purchase_plan_json, cmv_json, pricing_json, "
            "selected_price_label, accepted FROM recipes WHERE id = ?",
            (recipe_id,),
        )
        return self._derive_recipe_next_actions(
            recipe_id,
            recipe,
            matches,
            assessment,
            None,
            row["purchase_plan_json"] is not None if row else False,
            row["cmv_json"] is not None if row else False,
            row["pricing_json"] is not None if row else False,
            row["selected_price_label"] if row else None,
            bool(row["accepted"]) if row else False,
            True,
        )

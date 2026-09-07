from pydantic import BaseModel, Field


class CapabilityGroups(BaseModel):
    """Kitchen capabilities grouped for agent-led elicitation."""

    equipment: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    operational: list[str] = Field(default_factory=list)


class IngredientMatchInfo(BaseModel):
    recipe_ingredient: str
    pantry_ingredient: str | None
    status: str
    confirmed: bool
    reasoning: str


class IngredientAvailabilityLine(BaseModel):
    """Structured availability info for one recipe ingredient."""
    ingredient: str
    required: str
    available: str
    missing: str
    unit: str
    status: str


class RecipeCandidate(BaseModel):
    recipe_id: str
    recipe_name: str
    source_url: str
    servings: int
    interested: bool | None
    feedback: str | None
    accepted: bool
    ingredient_matches: list[IngredientMatchInfo]
    feasibility_status: str | None
    feasibility_missing: list[str]
    feasibility_blocking: list[str]
    required_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    unknown_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    unavailable_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    has_purchase_plan: bool
    has_cmv: bool
    cmv_per_serving: str | None
    has_pricing: bool
    selected_price_label: str | None
    next_actions: list[str]


class MenuItemInfo(BaseModel):
    recipe_id: str
    recipe_name: str
    cmv_per_serving: str
    price: str
    profit: str
    label: str


class WorkspaceStatus(BaseModel):
    initialized: bool
    initial_budget: str
    committed_budget: str
    remaining_budget: str
    pantry_items_count: int
    pantry_ingredient_names: list[str]
    kitchen_equipment: dict[str, bool | None]
    kitchen_skills: dict[str, bool | None]
    kitchen_operational: dict[str, bool | None]
    kitchen_details: dict[str, str] = {}
    recipes: list[RecipeCandidate]
    accepted_menu: list[MenuItemInfo]
    next_actions: list[str]


class RecipeRegistration(BaseModel):
    recipe_id: str
    recipe_name: str
    ingredient_matches: list[IngredientMatchInfo]
    ingredient_availability: list[IngredientAvailabilityLine]
    available_from_pantry: list[str]
    partially_available: list[str]
    missing_ingredients: list[str]
    feasibility_status: str
    feasibility_missing: list[str]
    feasibility_blocking: list[str]
    required_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    unknown_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    unavailable_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    next_actions: list[str]


class FeedbackResult(BaseModel):
    recipe_id: str
    interested: bool
    next_actions: list[str]


class CapabilityUpdate(BaseModel):
    capability_type: str
    name: str
    available: bool
    detail: str | None = None
    already_recorded: bool = False
    remaining_unknowns: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    unavailable_capabilities: CapabilityGroups = Field(
        default_factory=CapabilityGroups
    )
    next_actions: list[str]


class ConfirmationResult(BaseModel):
    recipe_id: str
    recipe_ingredient: str
    confirmed: bool
    next_actions: list[str]


class QuoteSubmission(BaseModel):
    recipe_id: str
    cash_outlay: str
    remaining_budget: str
    budget_ok: bool = True
    over_budget_by: str | None = None
    next_actions: list[str]


class CMVBreakdownLine(BaseModel):
    """One ingredient's contribution to the recipe CMV."""
    recipe_ingredient: str
    source: str
    quantity_used: str
    unit: str
    unit_cost: str
    cost_unit: str
    line_total: str
    calculation: str


class PricingResult(BaseModel):
    recipe_id: str
    recipe_name: str
    servings: int
    batch_cmv: str
    cmv_per_serving: str
    batch_cmv_calculation: str
    cmv_per_serving_calculation: str
    minimum_price: str
    platform_fee_rate: str
    net_revenue_formula: str
    minimum_price_calculation: str
    profit_formula: str
    cmv_breakdown: list[CMVBreakdownLine]
    scenarios: list[dict]
    next_actions: list[str]


class AcceptanceResult(BaseModel):
    recipe_id: str
    recipe_name: str
    selected_label: str
    price: str
    profit: str
    remaining_budget: str
    next_actions: list[str]

from fastmcp import FastMCP

from src.mcp.workflow.tools import (
    accept_recipe,
    confirm_ingredient_match,
    get_workspace_status,
    initialize_workspace,
    prepare_recipe_pricing,
    record_feedback,
    record_kitchen_capability,
    register_recipe,
    reset_workspace,
    submit_market_quotes,
)


mcp = FastMCP(
    name="Sabor da Maria",
    instructions=(
        "Stateful menu-planning assistant for Dona Maria.  Use the "
        "workflow tools (initialize_workspace, reset_workspace, "
        "register_recipe, etc.) for guarded state transitions. Tool "
        "results expose facts and grouped unknown capabilities; the agent "
        "owns conversational wording and question grouping."
    ),
)

mcp.tool(initialize_workspace)
mcp.tool(reset_workspace)
mcp.tool(get_workspace_status)
mcp.tool(register_recipe)
mcp.tool(record_feedback)
mcp.tool(record_kitchen_capability)
mcp.tool(confirm_ingredient_match)
mcp.tool(submit_market_quotes)
mcp.tool(prepare_recipe_pricing)
mcp.tool(accept_recipe)

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)

import asyncio

from src.mcp.server import mcp


def test_existing_server_exposes_all_tool_schemas() -> None:
    tools = {
        tool.name: tool
        for tool in asyncio.run(mcp.list_tools())
    }

    workflow_tools = {
        "initialize_workspace",
        "reset_workspace",
        "get_workspace_status",
        "register_recipe",
        "record_feedback",
        "record_kitchen_capability",
        "confirm_ingredient_match",
        "submit_market_quotes",
        "prepare_recipe_pricing",
        "accept_recipe",
    }
    assert set(tools) == workflow_tools
    assert len(tools) == 10

    assert set(tools["register_recipe"].parameters["properties"]) == {
        "recipe",
        "interested",
        "feedback",
    }

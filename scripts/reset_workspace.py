"""Wipe runtime SQLite and reload Dona Maria's pantry from the spreadsheet."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infrastructure.database import DEFAULT_DB_PATH, Database
from src.infrastructure.excel_loader import load_pantry
from src.services.workflow_service import WorkflowService

XLSX = ROOT / "data" / "despensa_dona_maria.xlsx"


def reset_workspace() -> None:
    runtime = ROOT / ".runtime"
    if runtime.exists():
        shutil.rmtree(runtime)

    pantry = load_pantry(XLSX)
    workflow = WorkflowService(Database(ROOT / DEFAULT_DB_PATH))
    status = workflow.reset_workspace(pantry)
    print(
        f"Workspace reset. pantry={status.pantry_items_count} "
        f"budget=R${status.remaining_budget} recipes={len(status.recipes)}"
    )


if __name__ == "__main__":
    reset_workspace()

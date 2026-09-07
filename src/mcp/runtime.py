import os
from pathlib import Path

from src.infrastructure.database import DEFAULT_DB_PATH, Database
from src.services.workflow_service import WorkflowService

_ROOT = Path(__file__).resolve().parents[2]
_db = Database(_ROOT / os.environ.get("SABOR_DB_PATH", DEFAULT_DB_PATH))
workflow = WorkflowService(_db)

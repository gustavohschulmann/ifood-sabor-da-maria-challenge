from pydantic import BaseModel


class KitchenProfile(BaseModel):
    """Tri-state kitchen capabilities: True=has, False=doesn't have, None=unknown."""

    equipment: dict[str, bool | None] = {}
    skills: dict[str, bool | None] = {}
    operational: dict[str, bool | None] = {}
    details: dict[str, str] = {}

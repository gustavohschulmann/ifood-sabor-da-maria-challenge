import re
import unicodedata
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.utils.capability_normalization import (
    UnknownCapabilityName,
    is_equipment_name,
    normalize_capability_name,
)
from src.utils.enums import Equipment

_UNIT_ALIASES = {
    "quilograma": "kg",
    "quilogramas": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "grama": "g",
    "gramas": "g",
    "litro": "L",
    "litros": "L",
    "l": "L",
    "mililitro": "ml",
    "mililitros": "ml",
    "unidade": "un",
    "unidades": "un",
    "un.": "un",
    "und": "un",
}


def recipe_slug(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(
        char for char in stripped if not unicodedata.combining(char)
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _coerce_equipment(value: Any) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        try:
            key = normalize_capability_name("equipment", str(item))
        except UnknownCapabilityName:
            continue
        if key in seen:
            continue
        seen.add(key)
        items.append(key)
    return items


def _coerce_operational(value: Any) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        text = str(item).strip()
        if not text:
            continue
        if is_equipment_name(text):
            try:
                key = normalize_capability_name("equipment", text)
            except UnknownCapabilityName:
                key = text
        else:
            try:
                key = normalize_capability_name("operational", text)
            except UnknownCapabilityName:
                continue
        if key in seen:
            continue
        seen.add(key)
        items.append(key)
    return items


def _coerce_ingredient_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    data = dict(item)
    if not str(data.get("ingredient") or "").strip():
        data["ingredient"] = (
            data.get("name")
            or data.get("item")
            or data.get("product")
            or ""
        )
    if data.get("quantity") in (None, ""):
        qty = data.get("qty") or data.get("amount")
        if qty is not None:
            data["quantity"] = qty
    if not str(data.get("unit") or "").strip():
        data["unit"] = data.get("measure") or data.get("unidade") or ""
    return data


class RecipeIngredient(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ingredient: str
    quantity: Decimal
    unit: str

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        return _coerce_ingredient_item(data)

    @field_validator("ingredient")
    @classmethod
    def require_non_blank_ingredient(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ingredient must not be blank")
        return v

    @field_validator("quantity")
    @classmethod
    def require_positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("quantity must be greater than zero")
        return v

    @field_validator("unit")
    @classmethod
    def require_non_blank_unit(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("unit must not be blank")
        canonical = _UNIT_ALIASES.get(v.strip().lower(), v.strip())
        return canonical


class Recipe(BaseModel):
    id: str = ""
    name: str
    source_url: str

    servings: int

    ingredients: list[RecipeIngredient]
    steps: list[str] = Field(default_factory=list)

    required_equipment: list[Equipment] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    operational_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def coerce_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if not str(payload.get("id") or "").strip():
            payload["id"] = recipe_slug(str(payload.get("name") or ""))
        payload["steps"] = [
            str(step).strip()
            for step in _as_list(payload.get("steps"))
            if str(step).strip()
        ]
        payload["required_equipment"] = _coerce_equipment(
            payload.get("required_equipment")
        )
        payload["required_skills"] = [
            str(skill).strip()
            for skill in _as_list(payload.get("required_skills"))
            if str(skill).strip()
        ]
        payload["operational_requirements"] = _coerce_operational(
            payload.get("operational_requirements")
        )
        payload["ingredients"] = [
            _coerce_ingredient_item(item)
            for item in _as_list(payload.get("ingredients"))
        ]
        return payload

    @field_validator("id")
    @classmethod
    def require_non_blank_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id must not be blank")
        return v

    @field_validator("name")
    @classmethod
    def require_non_blank_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v

    @field_validator("source_url")
    @classmethod
    def require_http_url(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_url must not be blank")
        if not (
            v.startswith("http://") or v.startswith("https://")
        ):
            raise ValueError(
                "source_url must start with http:// or https://"
            )
        return v

    @field_validator("servings")
    @classmethod
    def require_positive_servings(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("servings must be greater than zero")
        return v

    @model_validator(mode="after")
    def merge_duplicate_ingredients(self) -> "Recipe":
        merged: list[RecipeIngredient] = []
        index: dict[str, int] = {}
        for ing in self.ingredients:
            key = ing.ingredient.strip().casefold()
            existing_at = index.get(key)
            if existing_at is None:
                index[key] = len(merged)
                merged.append(ing)
                continue
            existing = merged[existing_at]
            if existing.unit.strip().casefold() == ing.unit.strip().casefold():
                merged[existing_at] = existing.model_copy(
                    update={"quantity": existing.quantity + ing.quantity}
                )
        self.ingredients = merged
        return self

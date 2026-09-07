"""Normalize capability names so aliases resolve to canonical keys."""

from src.utils.enums import Equipment, OperationalRequirement

_EQUIPMENT_ALIASES: dict[str, str] = {
    "forno": "oven",
    "fogão": "stove",
    "fogao": "stove",
    "microondas": "microwave",
    "liquidificador": "blender",
    "panela de pressão": "pressure_cooker",
    "panela de pressao": "pressure_cooker",
    "airfryer": "air_fryer",
    "air fryer": "air_fryer",
    "fritadeira": "air_fryer",
    "grelha": "grill",
    "churrasqueira": "barbecue_grill",
    "batedeira": "stand_mixer",
    "processador": "food_processor",
    "wok": "wok",
    "frigideira": "frying_pan",
    "panela": "saucepan",
    "caçarola": "dutch_oven",
    "assadeira": "roasting_pan",
    "forma": "baking_sheet",
    "tábua de corte": "cutting_board",
    "tabua de corte": "cutting_board",
    "faca": "chef_knife",
    "espátula": "spatula",
    "colher de pau": "wooden_spoon",
    "concha": "ladle",
    "escorredor": "colander",
    "peneira": "sieve",
    "rolo": "rolling_pin",
    "ralador": "grater",
    "descascador": "peeler",
    "fouet": "whisk",
    "batedor": "whisk",
}

_OPERATIONAL_ALIASES: dict[str, str] = {
    "gás": OperationalRequirement.GAS_OR_ELECTRIC.value,
    "gas": OperationalRequirement.GAS_OR_ELECTRIC.value,
    "energia": OperationalRequirement.GAS_OR_ELECTRIC.value,
    "energia/gás": OperationalRequirement.GAS_OR_ELECTRIC.value,
    "gas_or_electric": OperationalRequirement.GAS_OR_ELECTRIC.value,
    "geladeira": OperationalRequirement.FRIDGE_SPACE.value,
    "espaço na geladeira": OperationalRequirement.FRIDGE_SPACE.value,
    "espaco na geladeira": OperationalRequirement.FRIDGE_SPACE.value,
    "fridge_space": OperationalRequirement.FRIDGE_SPACE.value,
    "large_fridge": OperationalRequirement.FRIDGE_SPACE.value,
    "tempo": OperationalRequirement.PREP_TIME.value,
    "tempo de preparo": OperationalRequirement.PREP_TIME.value,
    "prep_time": OperationalRequirement.PREP_TIME.value,
    "prep_time_minutes": OperationalRequirement.PREP_TIME.value,
    "bancada": OperationalRequirement.COUNTER_SPACE.value,
    "espaço": OperationalRequirement.COUNTER_SPACE.value,
    "espaco": OperationalRequirement.COUNTER_SPACE.value,
    "counter_space": OperationalRequirement.COUNTER_SPACE.value,
    "bocas": OperationalRequirement.STOVE_BURNERS.value,
    "bocas do fogão": OperationalRequirement.STOVE_BURNERS.value,
    "bocas do fogao": OperationalRequirement.STOVE_BURNERS.value,
    "stove_burners": OperationalRequirement.STOVE_BURNERS.value,
}

_VALID_EQUIPMENT_VALUES = set(e.value for e in Equipment)
_VALID_OPERATIONAL_VALUES = set(o.value for o in OperationalRequirement)


class UnknownCapabilityName(ValueError):
    pass


def is_equipment_name(name: str) -> bool:
    lower = name.lower().strip()
    if lower in _EQUIPMENT_ALIASES:
        return True
    if lower in _VALID_EQUIPMENT_VALUES:
        return True
    return any(member.name.lower() == lower for member in Equipment)


def normalize_capability_name(capability_type: str, name: str) -> str:
    """Return the canonical key for a capability name."""
    lower = name.lower().strip()
    if capability_type == "equipment":
        if lower in _EQUIPMENT_ALIASES:
            return _EQUIPMENT_ALIASES[lower]
        if lower in _VALID_EQUIPMENT_VALUES:
            return lower
        for member in Equipment:
            if member.name.lower() == lower:
                return member.value
        raise UnknownCapabilityName(
            f"Unknown equipment name '{name}'"
        )
    if capability_type == "operational":
        if lower in _OPERATIONAL_ALIASES:
            return _OPERATIONAL_ALIASES[lower]
        if lower in _VALID_OPERATIONAL_VALUES:
            return lower
        raise UnknownCapabilityName(
            f"Unknown operational name '{name}'"
        )
    return name.strip()


def require_canonical_name(capability_type: str, name: str) -> str:
    if capability_type == "skill":
        stripped = name.strip()
        if not stripped:
            raise UnknownCapabilityName("skill must not be blank")
        return stripped
    if capability_type == "equipment":
        key = normalize_capability_name("equipment", name)
        if key not in _VALID_EQUIPMENT_VALUES:
            raise UnknownCapabilityName(
                f"Unknown equipment name '{name}'"
            )
        return key
    if capability_type == "operational":
        return normalize_capability_name("operational", name)
    raise UnknownCapabilityName(
        f"Unknown capability type '{capability_type}'"
    )

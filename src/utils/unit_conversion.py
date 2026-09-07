from decimal import Decimal


UNIT_ALIASES: dict[str, str] = {
    "unidade": "un",
    "unidades": "un",
    "und": "un",
    "dente": "un",
    "dentes": "un",
    "litro": "L",
    "litros": "L",
    "grama": "g",
    "gramas": "g",
    "quilo": "kg",
    "quilos": "kg",
    "mililitro": "ml",
    "mililitros": "ml",
    "caixa": "L",
    "caixas": "L",
    "pacote": "un",
    "pacotes": "un",
    "lata": "un",
    "latas": "un",
}

COOKING_CONVERSIONS: dict[str, tuple[Decimal, str]] = {
    "colher de sopa": (Decimal("15"), "ml"),
    "colheres de sopa": (Decimal("15"), "ml"),
    "colher de cha": (Decimal("5"), "ml"),
    "colheres de cha": (Decimal("5"), "ml"),
    "colher de chá": (Decimal("5"), "ml"),
    "colheres de chá": (Decimal("5"), "ml"),
    "xicara": (Decimal("240"), "ml"),
    "xicaras": (Decimal("240"), "ml"),
    "xícara": (Decimal("240"), "ml"),
    "xícaras": (Decimal("240"), "ml"),
    "pitada": (Decimal("0.5"), "g"),
    "pitadas": (Decimal("0.5"), "g"),
    "a gosto": (Decimal("5"), "g"),
}

CONVERSIONS = {
    ("kg", "g"): Decimal("1000"),
    ("g", "kg"): Decimal("0.001"),
    ("L", "ml"): Decimal("1000"),
    ("ml", "L"): Decimal("0.001"),
}


def normalize_unit(unit: str) -> str:
    normalized = unit.strip()
    lowered = normalized.lower()
    if lowered == "l":
        return "L"
    if lowered in UNIT_ALIASES:
        return UNIT_ALIASES[lowered]
    return lowered


def convert_quantity(
    value: Decimal,
    from_unit: str,
    to_unit: str,
) -> Decimal | None:
    source_unit = normalize_unit(from_unit)
    target_unit = normalize_unit(to_unit)

    if source_unit == target_unit:
        return value

    factor = CONVERSIONS.get((source_unit, target_unit))
    if factor is not None:
        return value * factor

    source_lower = from_unit.strip().lower()
    if source_lower in COOKING_CONVERSIONS:
        equiv_value, equiv_unit = COOKING_CONVERSIONS[source_lower]
        intermediate = value * equiv_value
        return convert_quantity(intermediate, equiv_unit, to_unit)

    target_lower = to_unit.strip().lower()
    if target_lower in COOKING_CONVERSIONS:
        equiv_value, equiv_unit = COOKING_CONVERSIONS[target_lower]
        intermediate = convert_quantity(value, from_unit, equiv_unit)
        if intermediate is not None:
            return intermediate / equiv_value

    return None

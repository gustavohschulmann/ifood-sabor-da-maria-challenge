from decimal import Decimal, ROUND_HALF_UP

MONEY_QUANTUM = Decimal("0.01")


def serialize_money(value: Decimal) -> str:
    return format(
        value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
        ".2f",
    )

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from src.domain.pricing import PriceScenario, RecipePricing


MONEY_QUANTUM = Decimal("0.01")
PLATFORM_FEE_RATE = Decimal("0.10")
NET_RATE = Decimal("0.90")
PRICE_SCENARIOS = (
    ("entrada", Decimal("0.20")),
    ("equilibrado", Decimal("0.40")),
    ("maior margem", Decimal("0.60")),
)


class PricingError(RuntimeError):
    """Base application error for deterministic delivery pricing."""


class InvalidCMV(PricingError):
    """A CMV value cannot be used for pricing."""


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _minimum_price(cmv_per_serving: Decimal) -> Decimal:
    return (cmv_per_serving / NET_RATE).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_CEILING,
    )


def generate_price_scenarios(
    cmv_per_serving: Decimal,
) -> RecipePricing:
    """Generate break-even and 20/40/60% profit-over-CMV prices."""
    if cmv_per_serving < 0:
        raise InvalidCMV("cmv_per_serving must not be negative")

    scenarios: list[PriceScenario] = []
    for label, markup_rate in PRICE_SCENARIOS:
        unrounded_price = (
            cmv_per_serving
            * (Decimal("1") + markup_rate)
            / NET_RATE
        )
        price = _round_money(unrounded_price)
        net_revenue = _round_money(price * NET_RATE)
        platform_fee = price - net_revenue
        profit = _round_money(net_revenue - cmv_per_serving)

        scenarios.append(
            PriceScenario(
                label=label,
                markup_rate=markup_rate,
                price=price,
                platform_fee=platform_fee,
                net_revenue=net_revenue,
                profit=profit,
            )
        )

    return RecipePricing(
        cmv_per_serving=cmv_per_serving,
        platform_fee_rate=PLATFORM_FEE_RATE,
        net_rate=NET_RATE,
        minimum_price=_minimum_price(cmv_per_serving),
        scenarios=scenarios,
    )

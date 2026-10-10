"""Business-rule validation of simulated candidates (FLOW.md: eligibility rules)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import RiskLevel, StrategyConstraints
from .simulator import SimulationResult

RISK_RANK: dict[RiskLevel, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class ActivePromotion:
    draft_id: str
    product_id: str
    valid_from: date
    valid_to: date


def validate(
    result: SimulationResult,
    constraints: StrategyConstraints,
    price: float,
    unit_cost: float,
    valid_from: date,
    valid_to: date,
    active: list[ActivePromotion],
    product_id: str,
    objective: str = "maximize_margin",
    budget_mxn: float = 0.0,
) -> list[str]:
    violations: list[str] = []
    c = result.candidate
    if c.discount_pct > constraints.max_discount_pct + 1e-9:
        violations.append(f"Discount {c.discount_pct:g}% exceeds the {constraints.max_discount_pct:g}% maximum.")
    discounted = price * (1 - c.discount_pct / 100)
    floor = unit_cost * (1 + constraints.min_unit_margin_pct / 100)
    if discounted < floor:
        violations.append(
            f"Discounted price ${discounted:.2f} is below cost + {constraints.min_unit_margin_pct:g}% minimum "
            f"margin (${floor:.2f})."
        )
    if RISK_RANK[result.stockout_risk] > RISK_RANK[constraints.max_stockout_risk]:
        violations.append(
            f"Run-out risk is {result.stockout_risk}: the extra demand would empty the shelf "
            f"(~{result.extra_lost_units:.0f} more units of demand lost)."
        )
    if objective == "increase_sales" and budget_mxn > 0:
        # "Explicit objective justification" (FLOW.md): the manager accepts investing margin in volume.
        if result.net_impact < -budget_mxn:
            violations.append(
                f"Costs ${-result.net_impact:,.2f} MXN of margin, above the ${budget_mxn:,.2f} MXN budget."
            )
        if result.sales_increment <= 0:
            violations.append("Does not increase sales.")
    elif result.net_impact <= 0:
        violations.append(f"Expected net impact is negative (${result.net_impact:,.2f} MXN).")
    for promo in active:
        touches = {product_id, c.anchor_product_id}
        if promo.product_id in touches and promo.valid_from <= valid_to and valid_from <= promo.valid_to:
            violations.append(
                f"Overlaps promotion {promo.draft_id} on the same product ({promo.valid_from} to {promo.valid_to})."
            )
    return violations

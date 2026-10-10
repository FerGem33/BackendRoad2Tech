"""Strategy templates and the business-rule assumptions they rely on.

The v2 dataset has no promotion history and no ticket/basket table, so:
* price response uses an assumed elasticity per category;
* combos use business-rule category pairs ("synthetic affinity"), never observed co-purchases.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Objective, StrategyType

# Assumed own-price elasticity per category: units multiply by (1 - discount) ** -elasticity.
ELASTICITY: dict[str, float] = {
    "C01": 1.6,
    "C02": 1.4,
    "C03": 1.8,
    "C04": 1.8,
    "C05": 2.2,
    "C06": 1.5,
    "C07": 1.6,
    "C08": 2.0,
    "C09": 1.2,
    "C10": 1.7,
    "C11": 1.3,
    "C12": 0.4,
    "C13": 1.1,
    "C14": 1.1,
    "C15": 0.8,
    "C16": 1.0,
    "C17": 0.9,
    "C18": 2.2,
    "C19": 1.2,
    "C20": 2.0,
}

# Business-rule complements: a shopper buying the key category is offered the listed ones.
COMPLEMENTS: dict[str, tuple[str, ...]] = {
    "C01": ("C03", "C05", "C04"),
    "C02": ("C03", "C11", "C19"),
    "C03": ("C02", "C01"),
    "C04": ("C01", "C06"),
    "C05": ("C01", "C06"),
    "C06": ("C08", "C07"),
    "C07": ("C08", "C06"),
    "C08": ("C06", "C07"),
    "C09": ("C01", "C07"),
    "C10": ("C01", "C04"),
    "C11": ("C02", "C01"),
    "C12": ("C06", "C01"),
    "C13": ("C14",),
    "C14": ("C13",),
    "C15": ("C01", "C13"),
    "C16": ("C09",),
    "C17": ("C13", "C07"),
    "C18": ("C07", "C01"),
    "C19": ("C02", "C11"),
    "C20": ("C04", "C19"),
}

# Share of combo buyers that add the discounted item: 3% + 0.6 pp per discount point.
ATTACH_BASE = 0.03
ATTACH_PER_POINT = 0.006
# Share of the item's own baseline buyers who would have bought anyway and now get the combo price.
COMBO_CANNIBALIZATION = 0.3

DISCOUNT_GRID = (5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    strategy_type: StrategyType
    title: str
    discount_pct: float
    anchor_product_id: str | None = None
    anchor_product_name: str | None = None

    @property
    def is_combo(self) -> bool:
        return self.strategy_type != "contextual_discount"


def attach_rate(discount_pct: float) -> float:
    return ATTACH_BASE + ATTACH_PER_POINT * discount_pct


def elasticity(category_id: str) -> float:
    return ELASTICITY.get(category_id, 1.2)


def generate_candidates(
    product_id: str,
    product_name: str,
    max_discount_pct: float,
    objective: Objective,
    has_waste_risk: bool,
    affinity_anchor: tuple[str, str] | None,
    rescue_anchor: tuple[str, str] | None,
) -> list[Candidate]:
    """Every allowed template x discount level within the user's max discount."""
    grid = [d for d in DISCOUNT_GRID if d <= max_discount_pct + 1e-9]
    if not grid and max_discount_pct > 0:
        grid = [round(max_discount_pct, 1)]
    candidates: list[Candidate] = []
    for d in grid:
        candidates.append(
            Candidate(
                candidate_id=f"{product_id}-disc-{int(d)}",
                strategy_type="contextual_discount",
                title=f"{d:g}% off {product_name}",
                discount_pct=d,
            )
        )
    rescue = has_waste_risk and rescue_anchor is not None
    # When the same anchor would serve both combos, keep only the rescue version (same mechanics).
    if affinity_anchor and objective != "reduce_waste" and not (rescue and affinity_anchor == rescue_anchor):
        anchor_id, anchor_name = affinity_anchor
        for d in [x for x in grid if x >= 10]:
            candidates.append(
                Candidate(
                    candidate_id=f"{product_id}-aff-{anchor_id}-{int(d)}",
                    strategy_type="affinity_combo",
                    title=f"Buy {anchor_name}, get {d:g}% off {product_name}",
                    discount_pct=d,
                    anchor_product_id=anchor_id,
                    anchor_product_name=anchor_name,
                )
            )
    if rescue and rescue_anchor:
        anchor_id, anchor_name = rescue_anchor
        for d in [x for x in grid if x >= 10]:
            candidates.append(
                Candidate(
                    candidate_id=f"{product_id}-rescue-{anchor_id}-{int(d)}",
                    strategy_type="rescue_combo",
                    title=f"Rescue combo: {anchor_name} + {product_name} at {d:g}% off",
                    discount_pct=d,
                    anchor_product_id=anchor_id,
                    anchor_product_name=anchor_name,
                )
            )
    return candidates

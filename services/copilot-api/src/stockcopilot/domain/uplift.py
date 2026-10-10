"""Promotion uplift measured from historical campaigns (difference-in-differences).

`promotion_campaigns.csv` + `promotion_store_days.csv` assign each campaign's stores to
treatment or control. For every campaign that ended on or before the demo date:

    ratio(group)  = units/day during the campaign / units/day in the 28 days before
    uplift        = ratio(treatment) / ratio(control) - 1

measured for the promoted product and for the anchor. Uplift is then expressed per unit of
discount (uplift / discount) and summarised per strategy type with its median and
interquartile range, which the simulator uses as the central estimate and confidence range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median, quantiles

import numpy as np

from ..data.repository import Campaign, DataRepository
from .models import StrategyType

PRE_DAYS = 28

# Our strategy templates -> the campaign types recorded in the dataset.
CAMPAIGN_TYPE: dict[StrategyType, str] = {
    "contextual_discount": "contextual_offer",
    "rescue_combo": "rescue_bundle",
    "affinity_combo": "affinity_combo",
}


@dataclass(frozen=True)
class UpliftEstimate:
    """Uplift per unit of discount (0.10 discount x 2.0 per unit = +20% units)."""

    campaign_type: str
    campaigns: int
    promoted_per_unit: float
    promoted_low: float
    promoted_high: float
    anchor_per_unit: float
    typical_discount_pct: float
    typical_uplift_pct: float

    def effect_bounds(self) -> tuple[float, float]:
        """Low/high multipliers of the central effect (interquartile range of the campaigns)."""
        if self.promoted_per_unit <= 0:
            return 0.0, 0.0
        return (
            max(0.0, self.promoted_low / self.promoted_per_unit),
            max(0.0, self.promoted_high / self.promoted_per_unit),
        )


def _did(
    repo: DataRepository, product_id: str, campaign: Campaign, treated: set[str], control: set[str]
) -> float | None:
    days = (campaign.end_date - campaign.start_date).days + 1
    series = repo.sales_by_store(product_id, campaign.start_date - timedelta(days=PRE_DAYS), campaign.end_date)
    if not series or len(next(iter(series.values()))) < PRE_DAYS + days:
        return None

    def ratio(stores: set[str]) -> float | None:
        during = sum(float(series[s][-days:].sum()) for s in stores if s in series)
        before = sum(float(series[s][:-days].sum()) for s in stores if s in series)
        if before <= 0:
            return None
        return (during / days) / (before / PRE_DAYS)

    rt, rc = ratio(treated), ratio(control)
    if rt is None or rc is None or rc <= 0:
        return None
    return rt / rc - 1


def measure_uplift(repo: DataRepository, as_of: date) -> dict[StrategyType, UpliftEstimate]:
    first, _ = repo.data_bounds()
    assignments = repo.campaign_assignments()
    promoted: dict[str, list[float]] = {}
    anchor: dict[str, list[float]] = {}
    discounts: dict[str, list[float]] = {}
    uplifts: dict[str, list[float]] = {}
    for campaign in repo.campaigns():
        if campaign.end_date > as_of or campaign.start_date - timedelta(days=PRE_DAYS) < first:
            continue  # never learn from the future; skip campaigns without a full pre-period
        stores = assignments.get(campaign.campaign_id, {})
        treated = {s for s, flag in stores.items() if flag}
        control = {s for s, flag in stores.items() if not flag}
        if not treated or not control or campaign.discount_pct <= 0:
            continue
        p = _did(repo, campaign.promoted_product_id, campaign, treated, control)
        if p is None:
            continue
        a = _did(repo, campaign.anchor_product_id, campaign, treated, control)
        kind = campaign.strategy_type
        promoted.setdefault(kind, []).append(p / campaign.discount_pct)
        anchor.setdefault(kind, []).append((a or 0.0) / campaign.discount_pct)
        discounts.setdefault(kind, []).append(campaign.discount_pct)
        uplifts.setdefault(kind, []).append(p)

    estimates: dict[StrategyType, UpliftEstimate] = {}
    for ours, theirs in CAMPAIGN_TYPE.items():
        values = promoted.get(theirs, [])
        if len(values) < 3:
            continue
        q1, _, q3 = quantiles(values, n=4)
        estimates[ours] = UpliftEstimate(
            campaign_type=theirs,
            campaigns=len(values),
            promoted_per_unit=max(0.0, median(values)),
            promoted_low=max(0.0, q1),
            promoted_high=max(0.0, q3),
            anchor_per_unit=max(0.0, median(anchor[theirs])),
            typical_discount_pct=round(float(np.median(discounts[theirs])) * 100, 1),
            typical_uplift_pct=round(median(uplifts[theirs]) * 100, 1),
        )
    return estimates

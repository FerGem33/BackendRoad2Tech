"""Generate observed promotion experiments with explicit treatment and control stores.

The campaign files are historical *facts* for model training, not recommendations.
Only the treatment group receives a price reduction and an induced demand uplift; the
control group keeps the same eligibility window without the intervention.  This makes
uplift evaluation possible without leaking future campaign outcomes into a forecast.
"""
import numpy as np
import pandas as pd

from common import DATES, rng, write_csv


CAMPAIGN_SPECS = (
    # strategy, objective, anchor, promoted/rescue, discount, anchor uplift, promoted uplift, reason
    ("affinity_combo", "increase_ticket", "P069", "P085", 0.10, 0.06, 0.28,
     "Coffee and sweet-bread affinity observed in morning baskets"),
    ("rescue_bundle", "reduce_waste", "P001", "P117", 0.20, 0.04, 0.34,
     "Move short-life fruit cups with a high-velocity cold drink"),
    ("contextual_offer", "increase_volume", "P010", "P097", 0.12, 0.03, 0.24,
     "Warm-weather hydration and ice-cream opportunity"),
)
CAMPAIGN_LENGTH_DAYS = 4
CAMPAIGN_CADENCE_DAYS = 14


def _treatment_by_zone(stores: pd.DataFrame, campaign_number: int) -> dict[str, int]:
    """60/40-ish deterministic split inside every zone for comparable controls."""
    out = {}
    for zone, group in stores.groupby("zone_type", sort=True):
        ids = group.store_id.to_numpy().copy()
        rng(60, campaign_number, sum(map(ord, zone))).shuffle(ids)
        n_treated = max(1, int(round(len(ids) * 0.60)))
        if len(ids) > 1:
            n_treated = min(n_treated, len(ids) - 1)  # retain a control in each multi-store zone
        out.update({store_id: int(i < n_treated) for i, store_id in enumerate(ids)})
    return out


def build(stores: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    campaigns, assignments = [], []
    start = DATES[0]
    campaign_number = 0
    for start_idx in range(0, len(DATES) - CAMPAIGN_LENGTH_DAYS + 1, CAMPAIGN_CADENCE_DAYS):
        spec = CAMPAIGN_SPECS[campaign_number % len(CAMPAIGN_SPECS)]
        strategy, objective, anchor, promoted, discount, anchor_uplift, promoted_uplift, reason = spec
        start_date = DATES[start_idx]
        end_date = DATES[start_idx + CAMPAIGN_LENGTH_DAYS - 1]
        campaign_id = f"CMP{campaign_number + 1:04d}"
        campaigns.append({
            "campaign_id": campaign_id,
            "strategy_type": strategy,
            "objective": objective,
            "anchor_product_id": anchor,
            "promoted_product_id": promoted,
            "discount_pct": discount,
            "uplift_anchor_pct": anchor_uplift,
            "uplift_promoted_pct": promoted_uplift,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "trigger_reason": reason,
        })
        treatment = _treatment_by_zone(stores, campaign_number)
        for day in DATES[start_idx:start_idx + CAMPAIGN_LENGTH_DAYS]:
            for store in stores.itertuples(index=False):
                is_treatment = treatment[store.store_id]
                assignments.append({
                    "campaign_id": campaign_id,
                    "date": day.strftime("%Y-%m-%d"),
                    "store_id": store.store_id,
                    "assignment_group": store.zone_type,
                    "is_eligible": 1,
                    "is_treatment": is_treatment,
                    "expected_uplift_pct": promoted_uplift if is_treatment else 0.0,
                })
        campaign_number += 1
    return pd.DataFrame(campaigns), pd.DataFrame(assignments)


def main() -> None:
    print("Building controlled promotion experiments...")
    from common import read_csv
    campaigns, assignments = build(read_csv("stores"))
    write_csv(campaigns, "promotion_campaigns", float_format="%.3f")
    write_csv(assignments, "promotion_store_days", float_format="%.3f")


if __name__ == "__main__":
    main()

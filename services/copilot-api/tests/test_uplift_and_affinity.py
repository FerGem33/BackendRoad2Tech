from __future__ import annotations

from datetime import date

import pandas as pd

from stockcopilot.data.build_affinity import compute_affinity
from stockcopilot.domain.uplift import measure_uplift


def test_uplift_is_measured_per_strategy_type(repo, svc):
    estimates = svc.uplift()
    assert set(estimates) == {"contextual_discount", "rescue_combo", "affinity_combo"}
    for estimate in estimates.values():
        assert estimate.campaigns >= 3
        assert estimate.promoted_low <= estimate.promoted_per_unit <= estimate.promoted_high
        low, high = estimate.effect_bounds()
        assert 0 <= low <= 1 <= high


def test_uplift_never_uses_campaigns_after_as_of(repo):
    first_campaign_end = min(c.end_date for c in repo.campaigns())
    assert measure_uplift(repo, first_campaign_end - pd.Timedelta(days=1).to_pytimedelta()) == {}
    early = measure_uplift(repo, date(2024, 12, 31))
    late = measure_uplift(repo, date(2026, 9, 16))
    assert sum(e.campaigns for e in early.values()) < sum(e.campaigns for e in late.values())


def test_affinity_excludes_campaign_lines_and_scores_lift():
    lines = pd.DataFrame(
        {
            "ticket_id": ["t1", "t1", "t2", "t2", "t3", "t4", "t5", "t5"],
            "product_id": ["A", "B", "A", "B", "A", "C", "A", "C"],
            "line_type": ["regular"] * 6 + ["campaign_bundle"] * 2,
        }
    )
    pairs = compute_affinity(lines, min_co_tickets=1)
    ab = pairs[(pairs.product_id == "A") & (pairs.other_product_id == "B")].iloc[0]
    assert ab.co_tickets == 2
    assert ab.confidence == round(2 / 3, 5)  # A is in 3 non-campaign tickets
    assert ab.lift == round(2 * 4 / (3 * 2), 3)  # 4 tickets in total, B in 2
    assert not ((pairs.product_id == "A") & (pairs.other_product_id == "C")).any()


def test_observed_affinity_is_loaded(svc):
    partners = svc.affinity_partners("P085")
    assert partners and partners[0].other_product_id == "P069"

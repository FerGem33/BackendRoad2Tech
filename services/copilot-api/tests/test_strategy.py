from __future__ import annotations

import pytest

from stockcopilot.domain.catalog import NotFoundError
from stockcopilot.domain.strategy.portfolio import generate_plan


def test_waste_strategy_creates_a_draft_that_needs_approval(engine, drafts):
    out = engine.run({"store": "S016", "product": "P059", "objective": "reduce_waste", "max_discount_pct": 30})
    assert out.strategy_type is not None
    assert out.draft_id and out.draft_status == "draft"
    assert out.requires_approval is True
    assert drafts.get(out.draft_id).status == "draft"
    assert len(out.baseline_series) == len(out.strategy_series)
    assert out.confidence_interval.low <= out.expected_net_impact <= out.confidence_interval.high
    assert out.discount_pct <= 30
    viable = [c for c in out.candidates if c.viable]
    assert viable and all(not c.violations for c in viable)
    assert all(c.violations for c in out.candidates if not c.viable)
    assert out.uplift_source == "campaigns"
    if out.cross_sell_product:
        # Sandwich + Coca-Cola is a pair observed in the ticket data.
        assert out.cross_sell_product.product_id == "P001"
        assert (out.cross_sell_basis or "").startswith("Observed in")


def test_discount_never_exceeds_the_maximum(engine):
    out = engine.run({"store": "S016", "product": "P059", "objective": "reduce_waste", "max_discount_pct": 10})
    assert all(c.discount_pct <= 10 for c in out.candidates)


def test_no_action_when_every_candidate_breaks_a_rule(engine, drafts):
    # Coca-Cola: discounts cost more margin than they bring (assumed elasticity) and nothing expires.
    out = engine.run({"store": "S001", "product": "P001", "objective": "maximize_margin", "max_discount_pct": 20})
    if out.strategy_type is None:
        assert out.draft_id is None
        assert out.candidates and all(not c.viable for c in out.candidates)
        assert drafts.list() == []


def test_overlapping_active_promotion_is_rejected(engine, drafts):
    first = engine.run({"store": "S016", "product": "P059", "objective": "reduce_waste", "max_discount_pct": 30})
    drafts.transition(first.draft_id, "approved", actor="test")
    second = engine.run({"store": "S016", "product": "P059", "objective": "reduce_waste", "max_discount_pct": 30})
    assert second.strategy_type is None
    assert any("Overlaps promotion" in v for c in second.candidates for v in c.violations)


def test_unknown_product_raises(engine):
    with pytest.raises(NotFoundError):
        engine.run({"store": "S016", "product": "zzzz unknown thing"})


def test_plan_strategies_never_share_products(engine):
    plan = generate_plan(engine, "S016")
    assert len(plan.strategies) <= 5
    touched = []
    for s in plan.strategies:
        touched.append(s.product.product_id)
        if s.cross_sell_product:
            touched.append(s.cross_sell_product.product_id)
        assert s.draft_id
    assert len(touched) == len(set(touched))
    assert plan.net_expected_impact == pytest.approx(sum(s.expected_net_impact for s in plan.strategies), abs=0.01)


def test_without_campaign_data_falls_back_to_assumed_elasticity(repo, svc, drafts):
    from stockcopilot.domain.services import Services
    from stockcopilot.domain.strategy.pipeline import StrategyEngine

    plain = Services.create(repo, svc.as_of)
    plain._uplift = {}  # as if the dataset had no promotion campaigns
    out = StrategyEngine(plain, drafts).run({"store": "S016", "product": "P059", "objective": "reduce_waste"})
    assert out.uplift_source == "assumed"
    assert any("assumed" in a for a in out.assumptions)

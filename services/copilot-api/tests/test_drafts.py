from __future__ import annotations

import pytest

from stockcopilot.domain.drafts import DraftNotFoundError, InvalidTransitionError


@pytest.fixture
def draft_id(engine):
    out = engine.run({"store": "S016", "product": "P059", "objective": "reduce_waste", "max_discount_pct": 30})
    return out.draft_id


def test_happy_path_keeps_history(drafts, draft_id):
    drafts.transition(draft_id, "selected", actor="manager")
    approved = drafts.transition(draft_id, "approved", actor="manager", note="ok")
    assert approved.status == "approved"
    assert [e.status for e in approved.history] == ["draft", "selected", "approved"]
    assert approved.strategy.draft_status == "approved"


def test_repeating_a_status_is_idempotent(drafts, draft_id):
    once = drafts.transition(draft_id, "rejected", actor="manager")
    twice = drafts.transition(draft_id, "rejected", actor="manager")
    assert len(once.history) == len(twice.history) == 2


def test_invalid_transitions(drafts, draft_id):
    drafts.transition(draft_id, "approved", actor="manager")
    with pytest.raises(InvalidTransitionError):
        drafts.transition(draft_id, "rejected", actor="manager")
    with pytest.raises(DraftNotFoundError):
        drafts.get("DR-missing")

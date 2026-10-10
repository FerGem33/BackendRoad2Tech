from __future__ import annotations

import pandas as pd
import pytest

from scripts.forecast.modeling import select_training_rows


def test_training_sample_is_reproducible_and_respects_cutoff() -> None:
    features = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=100),
        "demand_target": range(100),
    })
    cutoff = "2026-03-15"
    first = select_training_rows(features, cutoff, 20, random_seed=42)
    second = select_training_rows(features, cutoff, 20, random_seed=42)

    assert len(first) == 20
    assert first.index.tolist() == second.index.tolist()
    assert first["date"].max() <= pd.Timestamp(cutoff)
    assert features.loc[features["date"] > cutoff].index.intersection(first.index).empty


def test_training_sample_rejects_invalid_size() -> None:
    features = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"])})
    with pytest.raises(ValueError, match="positive integer"):
        select_training_rows(features, "2026-01-01", 0, random_seed=42)

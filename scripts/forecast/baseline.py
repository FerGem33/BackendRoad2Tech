"""Seasonal-naive recursive baseline used to validate CatBoost improvement."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .features import build_static_grid, initial_history


def predict_seasonal_naive(
    observed: pd.DataFrame,
    stores: pd.DataFrame,
    products: pd.DataFrame,
    context: pd.DataFrame,
    as_of: str | pd.Timestamp,
) -> pd.DataFrame:
    """Predict each day from the preceding four same-weekday values, recursively."""
    cutoff = pd.Timestamp(as_of)
    grid = build_static_grid(stores, products)
    history = initial_history(observed, cutoff, grid)
    rows: list[pd.DataFrame] = []
    for _, context_row in context.loc[context["date"] > cutoff].sort_values("date").iterrows():
        prediction = np.nanmean(history[[-7, -14, -21, -28]], axis=0)
        prediction = np.nan_to_num(prediction, nan=0.0, posinf=0.0, neginf=0.0).clip(min=0.0)
        day = grid[["store_id", "product_id"]].copy()
        day["date"] = pd.Timestamp(context_row["date"])
        day["baseline_p50"] = prediction
        rows.append(day)
        history = np.vstack([history[1:], prediction])
    return pd.concat(rows, ignore_index=True)

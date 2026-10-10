"""Deterministic forecast metrics used by rolling backtests."""
from __future__ import annotations

import numpy as np
import pandas as pd


def wape(actual: pd.Series | np.ndarray, prediction: pd.Series | np.ndarray) -> float:
    actual_array = np.asarray(actual, dtype=float)
    prediction_array = np.asarray(prediction, dtype=float)
    denominator = np.abs(actual_array).sum()
    if denominator == 0:
        return 0.0
    return float(np.abs(actual_array - prediction_array).sum() / denominator)


def pinball_loss(actual: pd.Series | np.ndarray, prediction: pd.Series | np.ndarray, quantile: float) -> float:
    error = np.asarray(actual, dtype=float) - np.asarray(prediction, dtype=float)
    return float(np.maximum(quantile * error, (quantile - 1) * error).mean())


def empirical_coverage(actual: pd.Series | np.ndarray, upper_quantile: pd.Series | np.ndarray) -> float:
    return float((np.asarray(actual, dtype=float) <= np.asarray(upper_quantile, dtype=float)).mean())


def metric_summary(frame: pd.DataFrame) -> dict[str, float]:
    """Summarize a scored frame containing demand_target and P50/P80/P90 columns."""
    return {
        "wape_p50": wape(frame["demand_target"], frame["p50"]),
        "pinball_p80": pinball_loss(frame["demand_target"], frame["p80"], 0.8),
        "pinball_p90": pinball_loss(frame["demand_target"], frame["p90"], 0.9),
        "coverage_p80": empirical_coverage(frame["demand_target"], frame["p80"]),
        "coverage_p90": empirical_coverage(frame["demand_target"], frame["p90"]),
    }

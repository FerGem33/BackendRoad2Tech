from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.forecast.features import build_training_features


def _observed(days: int = 40) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    frame = pd.DataFrame({"date": dates})
    frame["store_id"] = "S001"
    frame["product_id"] = "P001"
    frame["category_id"] = "C01"
    frame["brand"] = "Demo"
    frame["unit"] = "piece"
    frame["weather_sensitivity"] = "neutral"
    frame["zone_type"] = "residential"
    frame["secondary_zone_type"] = None
    frame["size_category"] = "medium"
    frame["weather_label"] = "mild"
    frame["holiday_name"] = None
    frame["season_name"] = "winter"
    frame["event_name"] = None
    frame["event_scope"] = None
    frame["retail_price"] = 10.0
    frame["shelf_life_days"] = 30
    frame["is_seasonal"] = 0
    frame["base_daily_demand"] = 5.0
    frame["is_24h"] = 1
    frame["is_weekend"] = (dates.dayofweek >= 5).astype(int)
    frame["is_holiday"] = 0
    frame["is_payday"] = 0
    frame["is_school_vacation"] = 0
    frame["is_college_vacation"] = 0
    frame["temp_max_c"] = 25.0
    frame["temp_min_c"] = 12.0
    frame["precipitation_mm"] = 0.0
    frame["demand_target"] = np.arange(days, dtype=float)
    frame["is_treated_campaign"] = False
    return frame


def test_treated_rows_are_not_training_labels() -> None:
    observed = _observed()
    treated_date = observed.loc[35, "date"]
    observed.loc[35, "is_treated_campaign"] = True
    features = build_training_features(observed)
    assert treated_date not in set(features["date"])


def test_same_day_target_is_not_a_feature() -> None:
    observed = _observed()
    target_date = observed.loc[35, "date"]
    original = build_training_features(observed)
    observed.loc[observed["date"] == target_date, "demand_target"] = 9999.0
    changed = build_training_features(observed)
    original_row = original.loc[original["date"] == target_date].iloc[0]
    changed_row = changed.loc[changed["date"] == target_date].iloc[0]
    assert original_row["lag_1"] == changed_row["lag_1"]
    assert original_row["rolling_mean_7"] == changed_row["rolling_mean_7"]

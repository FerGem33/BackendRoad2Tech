"""Build leakage-safe training and recursive inference features."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CATEGORICAL_FEATURES, FEATURE_COLUMNS, LAG_DAYS


def _date_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    dates = pd.to_datetime(result["date"])
    result["day_of_week"] = dates.dt.dayofweek.astype("int8")
    result["month"] = dates.dt.month.astype("int8")
    result["week_of_year"] = dates.dt.isocalendar().week.astype("int16")
    angle = 2 * np.pi * dates.dt.dayofyear / 365.25
    result["day_of_year_sin"] = np.sin(angle)
    result["day_of_year_cos"] = np.cos(angle)
    return result


def normalize_categoricals(frame: pd.DataFrame) -> pd.DataFrame:
    """CatBoost requires categorical columns to be non-null strings."""
    result = frame.copy()
    for column in CATEGORICAL_FEATURES:
        result[column] = result[column].fillna("none").astype(str)
    return result


def build_training_features(observed: pd.DataFrame) -> pd.DataFrame:
    """Create target-date features using only demand strictly before that date.

    Treated promotion observations are intentionally absent both as labels and history;
    the baseline model must not learn promotional uplift as ordinary demand.
    """
    keys = ["store_id", "product_id", "date"]
    result = observed.sort_values(keys).copy()
    result["baseline_history"] = result["demand_target"].where(~result["is_treated_campaign"])
    group = result.groupby(["store_id", "product_id"], observed=True)["baseline_history"]
    for lag in LAG_DAYS:
        result[f"lag_{lag}"] = group.shift(lag)
    shifted = group.shift(1)
    result["rolling_mean_7"] = shifted.groupby(
        [result["store_id"], result["product_id"]], observed=True
    ).transform(lambda values: values.rolling(7, min_periods=7).mean())
    result["rolling_mean_28"] = shifted.groupby(
        [result["store_id"], result["product_id"]], observed=True
    ).transform(lambda values: values.rolling(28, min_periods=14).mean())
    result["recent_trend_7"] = result["rolling_mean_7"] - result.groupby(
        ["store_id", "product_id"], observed=True
    )["rolling_mean_7"].shift(7)
    result = _date_features(result)
    result = normalize_categoricals(result)
    required = ["demand_target", "lag_28", *FEATURE_COLUMNS]
    baseline_rows = result.loc[~result["is_treated_campaign"]].dropna(subset=required).copy()
    return baseline_rows.sort_values(keys).reset_index(drop=True)


def build_static_grid(stores: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Create a stable store-product order shared by history and batch predictions."""
    store_columns = ["store_id", "zone_type", "secondary_zone_type", "size_category", "is_24h"]
    product_columns = [
        "product_id", "category_id", "brand", "unit", "shelf_life_days", "retail_price",
        "is_seasonal", "weather_sensitivity", "base_daily_demand",
    ]
    grid = stores[store_columns].merge(products[product_columns], how="cross")
    grid["entity_id"] = grid["store_id"].astype(str) + "::" + grid["product_id"].astype(str)
    return grid.sort_values(["store_id", "product_id"]).reset_index(drop=True)


def initial_history(observed: pd.DataFrame, as_of: pd.Timestamp, grid: pd.DataFrame) -> np.ndarray:
    """Return the last 28 baseline-demand observations in the grid's fixed order."""
    history = observed.loc[observed["date"] <= as_of, ["date", "store_id", "product_id", "demand_target", "is_treated_campaign"]].copy()
    history["baseline_history"] = history["demand_target"].where(~history["is_treated_campaign"])
    history["entity_id"] = history["store_id"].astype(str) + "::" + history["product_id"].astype(str)
    dates = pd.date_range(as_of - pd.Timedelta(days=27), as_of, freq="D")
    matrix = history.pivot(index="date", columns="entity_id", values="baseline_history")
    matrix = matrix.reindex(index=dates, columns=grid["entity_id"])
    return matrix.to_numpy(dtype=float)


def build_future_step_features(grid: pd.DataFrame, context_row: pd.Series, history: np.ndarray) -> pd.DataFrame:
    """Build one feature row per store-product for a future date from known context."""
    if history.shape[0] < 28:
        raise ValueError("Recursive inference requires 28 historical days")
    result = grid.drop(columns="entity_id").copy()
    context = context_row.to_dict()
    result["date"] = pd.Timestamp(context["date"])
    for column in (
        "is_weekend", "is_holiday", "holiday_name", "season_name", "event_name", "event_scope",
        "is_payday", "is_school_vacation", "is_college_vacation", "temp_max_c", "temp_min_c",
        "precipitation_mm", "weather_label",
    ):
        result[column] = context.get(column)
    result["lag_1"] = history[-1]
    result["lag_7"] = history[-7]
    result["lag_14"] = history[-14]
    result["lag_28"] = history[-28]
    result["rolling_mean_7"] = np.nanmean(history[-7:], axis=0)
    result["rolling_mean_28"] = np.nanmean(history[-28:], axis=0)
    result["recent_trend_7"] = np.nanmean(history[-7:], axis=0) - np.nanmean(history[-14:-7], axis=0)
    result = _date_features(result)
    return normalize_categoricals(result)

"""Shared, deterministic configuration for the local CatBoost forecast pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "public" / "softtek" / "dataset" / "dataset-hackathon-road2tech" / "v2"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "forecast"
DEFAULT_DATA_AS_OF = "2026-09-30"
FORECAST_HORIZON_DAYS = 14
RANDOM_SEED = 42

LAG_DAYS = (1, 7, 14, 28)
ROLLING_WINDOWS = (7, 28)
QUANTILES = (0.5, 0.8, 0.9)
BACKTEST_AS_OF_DATES = ("2025-03-31", "2025-09-30", "2026-03-31", "2026-09-16")

CATEGORICAL_FEATURES = (
    "store_id", "product_id", "category_id", "brand", "unit", "weather_sensitivity",
    "zone_type", "secondary_zone_type", "size_category", "weather_label", "holiday_name",
    "season_name", "event_name", "event_scope",
)

NUMERIC_FEATURES = (
    "retail_price", "shelf_life_days", "is_seasonal", "base_daily_demand", "is_24h",
    "day_of_week", "month", "week_of_year", "day_of_year_sin", "day_of_year_cos",
    "is_weekend", "is_holiday", "is_payday", "is_school_vacation", "is_college_vacation",
    "temp_max_c", "temp_min_c", "precipitation_mm", "lag_1", "lag_7", "lag_14", "lag_28",
    "rolling_mean_7", "rolling_mean_28", "recent_trend_7",
)

FEATURE_COLUMNS = CATEGORICAL_FEATURES + NUMERIC_FEATURES


@dataclass(frozen=True)
class CatBoostConfig:
    """Conservative CPU settings that fit the full synthetic grid sequentially."""

    iterations: int = 750
    depth: int = 8
    learning_rate: float = 0.05
    l2_leaf_reg: float = 5.0
    thread_count: int = 6
    random_seed: int = RANDOM_SEED

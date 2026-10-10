"""CatBoost quantile training, artifact persistence, and recursive batch inference."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import CATEGORICAL_FEATURES, FEATURE_COLUMNS, CatBoostConfig, QUANTILES
from .features import build_future_step_features, initial_history


def _catboost():
    try:
        from catboost import CatBoostRegressor
    except ImportError as error:
        raise RuntimeError("Install ML dependencies with: pip install -r requirements-ml.txt") from error
    return CatBoostRegressor


def cat_feature_indices() -> list[int]:
    return [FEATURE_COLUMNS.index(column) for column in CATEGORICAL_FEATURES]


def _model_params(quantile: float, config: CatBoostConfig) -> dict[str, Any]:
    return {
        "loss_function": f"Quantile:alpha={quantile}",
        "iterations": config.iterations,
        "depth": config.depth,
        "learning_rate": config.learning_rate,
        "l2_leaf_reg": config.l2_leaf_reg,
        "random_seed": config.random_seed,
        "thread_count": config.thread_count,
        "allow_writing_files": False,
        "verbose": 100,
        "random_strength": 0.5,
    }


def select_training_rows(features: pd.DataFrame, as_of: str | pd.Timestamp, max_train_rows: int | None, random_seed: int) -> pd.DataFrame:
    """Apply the temporal cutoff before reproducibly sampling completed feature rows."""
    cutoff = pd.Timestamp(as_of)
    eligible_index = features.index[features["date"] <= cutoff]
    if len(eligible_index) == 0:
        raise ValueError(f"No eligible training rows exist at or before {cutoff.date()}")
    if max_train_rows is not None:
        if max_train_rows < 1:
            raise ValueError("max_train_rows must be a positive integer")
        if len(eligible_index) > max_train_rows:
            rng = np.random.default_rng(random_seed)
            eligible_index = pd.Index(rng.choice(eligible_index.to_numpy(), size=max_train_rows, replace=False)).sort_values()
    return features.loc[eligible_index].copy()


def fit_quantile_models(
    features: pd.DataFrame,
    as_of: str | pd.Timestamp,
    config: CatBoostConfig = CatBoostConfig(),
    max_train_rows: int | None = None,
) -> dict[float, Any]:
    """Fit P50, P80, and P90 on eligible baseline rows through as_of."""
    train = select_training_rows(features, as_of, max_train_rows, config.random_seed)
    if max_train_rows is not None:
        print(f"Training sample: {len(train):,} of {int((features['date'] <= pd.Timestamp(as_of)).sum()):,} eligible rows")
    model_input = train.loc[:, FEATURE_COLUMNS]
    target = train["demand_target"].astype(float)
    CatBoostRegressor = _catboost()
    models: dict[float, Any] = {}
    for quantile in QUANTILES:
        print(f"Training CatBoost P{int(quantile * 100)} on {len(train):,} rows...")
        model = CatBoostRegressor(**_model_params(quantile, config))
        model.fit(model_input, target, cat_features=cat_feature_indices())
        models[quantile] = model
    return models


def enforce_quantile_order(predictions: pd.DataFrame) -> pd.DataFrame:
    """Keep quantile forecasts non-negative and monotonically ordered by construction."""
    result = predictions.copy()
    result["p50"] = result["p50"].clip(lower=0.0)
    result["p80"] = np.maximum(result["p50"], result["p80"].clip(lower=0.0))
    result["p90"] = np.maximum(result["p80"], result["p90"].clip(lower=0.0))
    return result


def predict_batch(
    models: dict[float, Any],
    observed: pd.DataFrame,
    stores: pd.DataFrame,
    products: pd.DataFrame,
    context: pd.DataFrame,
    as_of: str | pd.Timestamp,
) -> pd.DataFrame:
    """Recursively predict every requested day and every store-product pair."""
    from .features import build_static_grid

    cutoff = pd.Timestamp(as_of)
    grid = build_static_grid(stores, products)
    context = context.loc[context["date"] > cutoff].sort_values("date").reset_index(drop=True)
    if context.empty:
        raise ValueError("Forecast context must contain at least one date after data_as_of")
    histories = {quantile: initial_history(observed, cutoff, grid) for quantile in QUANTILES}
    rows: list[pd.DataFrame] = []

    for _, context_row in context.iterrows():
        day = grid[["store_id", "product_id"]].copy()
        day["date"] = pd.Timestamp(context_row["date"])
        for quantile in QUANTILES:
            step_features = build_future_step_features(grid, context_row, histories[quantile])
            day[f"p{int(quantile * 100)}"] = models[quantile].predict(step_features.loc[:, FEATURE_COLUMNS])
        day = enforce_quantile_order(day)
        rows.append(day)
        for quantile in QUANTILES:
            column = f"p{int(quantile * 100)}"
            histories[quantile] = np.vstack([histories[quantile][1:], day[column].to_numpy(dtype=float)])
    return pd.concat(rows, ignore_index=True)


def save_bundle(
    models: dict[float, Any],
    output_dir: Path,
    data_as_of: str | pd.Timestamp,
    config: CatBoostConfig,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Persist CatBoost models and the exact feature contract needed for inference."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for quantile, model in models.items():
        model.save_model(output_dir / f"model_p{int(quantile * 100)}.cbm")
    metadata = {
        "data_as_of": str(pd.Timestamp(data_as_of).date()),
        "feature_columns": list(FEATURE_COLUMNS),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "quantiles": list(QUANTILES),
        "config": asdict(config),
        "target": "units_sold + lost_demand",
        "promotion_policy": "baseline_only_excluding_treated_campaign_rows",
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    if metrics is not None:
        (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")


def load_bundle(model_dir: Path) -> tuple[dict[float, Any], dict[str, Any]]:
    """Load local models and reject artifacts with an incompatible feature contract."""
    model_dir = Path(model_dir)
    metadata = json.loads((model_dir / "metadata.json").read_text())
    if metadata["feature_columns"] != list(FEATURE_COLUMNS):
        raise ValueError("Model feature schema does not match this code version")
    CatBoostRegressor = _catboost()
    models: dict[float, Any] = {}
    for quantile in QUANTILES:
        model = CatBoostRegressor()
        model.load_model(model_dir / f"model_p{int(quantile * 100)}.cbm")
        models[quantile] = model
    return models, metadata


def forecast_contract(batch: pd.DataFrame, store_id: str, product_id: str, data_as_of: str) -> dict[str, Any]:
    """Return the M0 Forecast-compatible response for a store-product forecast."""
    selected = batch.loc[(batch["store_id"] == store_id) & (batch["product_id"] == product_id)].sort_values("date")
    if selected.empty:
        raise ValueError(f"No forecast found for {store_id}/{product_id}")
    return {
        "store_id": store_id,
        "product_id": product_id,
        "data_as_of": data_as_of,
        "horizon": selected["date"].dt.strftime("%Y-%m-%d").tolist(),
        "p50": selected["p50"].round(3).tolist(),
        "p80": selected["p80"].round(3).tolist(),
        "p90": selected["p90"].round(3).tolist(),
        "drivers": ["historical_demand", "calendar", "weather", "store_and_product_attributes"],
    }

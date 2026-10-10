"""Run fixed rolling 14-day backtests against the seasonal-naive benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .baseline import predict_seasonal_naive
from .config import BACKTEST_AS_OF_DATES, CatBoostConfig, DEFAULT_ARTIFACT_DIR, DEFAULT_DATA_DIR, FORECAST_HORIZON_DAYS
from .data import load_forecast_data
from .features import build_training_features
from .metrics import metric_summary, wape
from .modeling import fit_quantile_models, predict_batch


def _score_fold(predictions: pd.DataFrame, observed: pd.DataFrame, baseline: pd.DataFrame, as_of: str) -> dict:
    start = pd.Timestamp(as_of) + pd.Timedelta(days=1)
    end = start + pd.Timedelta(days=FORECAST_HORIZON_DAYS - 1)
    actual = observed.loc[
        (observed["date"] >= start) & (observed["date"] <= end) & ~observed["is_treated_campaign"],
        ["date", "store_id", "product_id", "demand_target", "category_id", "zone_type"],
    ]
    scored = actual.merge(predictions, on=["date", "store_id", "product_id"], validate="one_to_one")
    scored = scored.merge(baseline, on=["date", "store_id", "product_id"], validate="one_to_one")
    metrics = metric_summary(scored)
    metrics["wape_seasonal_naive"] = wape(scored["demand_target"], scored["baseline_p50"])
    metrics["rows_scored"] = int(len(scored))
    metrics["catboost_beats_baseline"] = metrics["wape_p50"] < metrics["wape_seasonal_naive"]
    metrics["wape_by_category"] = {
        str(category): wape(group["demand_target"], group["p50"])
        for category, group in scored.groupby("category_id", observed=True)
    }
    metrics["wape_by_zone"] = {
        str(zone): wape(group["demand_target"], group["p50"])
        for zone, group in scored.groupby("zone_type", observed=True)
    }
    return metrics


def run_backtest(data_dir: Path, iterations: int) -> dict:
    data = load_forecast_data(data_dir)
    features = build_training_features(data.observed)
    reports = []
    for as_of in BACKTEST_AS_OF_DATES:
        print(f"Backtesting horizon after {as_of}...")
        models = fit_quantile_models(features, as_of, CatBoostConfig(iterations=iterations))
        start = pd.Timestamp(as_of) + pd.Timedelta(days=1)
        end = start + pd.Timedelta(days=FORECAST_HORIZON_DAYS - 1)
        context = data.context.loc[(data.context["date"] >= start) & (data.context["date"] <= end)]
        predictions = predict_batch(models, data.observed, data.stores, data.products, context, as_of)
        baseline = predict_seasonal_naive(data.observed, data.stores, data.products, context, as_of)
        reports.append({"data_as_of": as_of, **_score_fold(predictions, data.observed, baseline, as_of)})
    report = pd.DataFrame(reports)
    return {
        "folds": reports,
        "mean_wape_p50": float(report["wape_p50"].mean()),
        "mean_wape_seasonal_naive": float(report["wape_seasonal_naive"].mean()),
        "catboost_beats_baseline": bool(report["wape_p50"].mean() < report["wape_seasonal_naive"].mean()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--iterations", type=int, default=CatBoostConfig.iterations)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT_DIR / "backtest_metrics.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_backtest(args.data_dir, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

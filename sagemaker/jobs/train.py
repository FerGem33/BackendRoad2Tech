"""Train and quality-gate StockCopilot's CatBoost forecast bundle in SageMaker."""
from __future__ import annotations

import os
from pathlib import Path

from scripts.forecast.backtest import run_backtest
from scripts.forecast.config import CatBoostConfig, DEFAULT_DATA_AS_OF
from scripts.forecast.data import load_forecast_data
from scripts.forecast.features import build_training_features
from scripts.forecast.modeling import fit_quantile_models, save_bundle

from .common import environment_int, environment_path, write_json


def main() -> None:
    data_dir = environment_path("SM_CHANNEL_TRAINING", "/opt/ml/input/data/training")
    model_dir = environment_path("SM_MODEL_DIR", "/opt/ml/model")
    output_dir = environment_path("SM_OUTPUT_DATA_DIR", "/opt/ml/output/data")
    data_as_of = os.environ.get("SM_HP_DATA_AS_OF", os.environ.get("DATA_AS_OF", DEFAULT_DATA_AS_OF))
    iterations = environment_int("SM_HP_MODEL_ITERATIONS", environment_int("MODEL_ITERATIONS", CatBoostConfig.iterations))

    report = run_backtest(data_dir, iterations)
    report.update(
        {
            "data_as_of": data_as_of,
            "iterations": iterations,
            "quality_gate": "catboost_mean_wape_p50_lt_seasonal_naive",
        }
    )
    write_json(output_dir / "evaluation.json", report)
    if not report["catboost_beats_baseline"]:
        raise RuntimeError("CatBoost did not beat the seasonal-naive baseline; model promotion is blocked")

    data = load_forecast_data(data_dir)
    features = build_training_features(data.observed)
    config = CatBoostConfig(iterations=iterations)
    models = fit_quantile_models(features, data_as_of, config)
    save_bundle(models, model_dir, data_as_of, config, metrics=report)
    print(f"Saved approved CatBoost forecast bundle to {model_dir}")


if __name__ == "__main__":
    main()

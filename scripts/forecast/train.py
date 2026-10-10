"""Train and persist the local P50/P80/P90 baseline demand model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import CatBoostConfig, DEFAULT_ARTIFACT_DIR, DEFAULT_DATA_AS_OF, DEFAULT_DATA_DIR
from .data import load_forecast_data
from .features import build_training_features
from .modeling import fit_quantile_models, save_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--data-as-of", default=DEFAULT_DATA_AS_OF)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--iterations", type=int, default=CatBoostConfig.iterations)
    parser.add_argument(
        "--require-backtest-pass",
        action="store_true",
        help="Require artifacts/forecast/backtest_metrics.json to show CatBoost beat the baseline.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir or DEFAULT_ARTIFACT_DIR / args.data_as_of
    metrics_path = DEFAULT_ARTIFACT_DIR / "backtest_metrics.json"
    metrics = None
    if args.require_backtest_pass:
        if not metrics_path.exists():
            raise FileNotFoundError(f"Run backtest first; metrics are missing at {metrics_path}")
        metrics = json.loads(metrics_path.read_text())
        if not metrics.get("catboost_beats_baseline", False):
            raise RuntimeError("CatBoost did not beat the seasonal-naive baseline; final training is blocked")
    data = load_forecast_data(args.data_dir)
    features = build_training_features(data.observed)
    config = CatBoostConfig(iterations=args.iterations)
    models = fit_quantile_models(features, args.data_as_of, config)
    save_bundle(models, output_dir, args.data_as_of, config, metrics=metrics)
    print(f"Saved forecast bundle to {output_dir}")


if __name__ == "__main__":
    main()

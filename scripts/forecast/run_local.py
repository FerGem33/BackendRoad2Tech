"""Train, evaluate, and forecast locally using the same pipeline as SageMaker."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import DEFAULT_ARTIFACT_DIR, DEFAULT_DATA_AS_OF, DEFAULT_DATA_DIR, CatBoostConfig


def run_pipeline(data_dir: Path, output_dir: Path, data_as_of: str, iterations: int) -> Path:
    """Run the shared training quality gate and generate the forecast JSONL locally."""
    model_dir = output_dir / "model"
    evaluation_dir = output_dir / "evaluation"
    forecast_dir = output_dir / "forecasts"
    for directory in (model_dir, evaluation_dir, forecast_dir):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ.update(
        {
            "SM_CHANNEL_TRAINING": str(data_dir.resolve()),
            "SM_MODEL_DIR": str(model_dir.resolve()),
            "SM_OUTPUT_DATA_DIR": str(evaluation_dir.resolve()),
            "SM_HP_DATA_AS_OF": data_as_of,
            "SM_HP_MODEL_ITERATIONS": str(iterations),
        }
    )

    from sagemaker.jobs.train import main as train

    train()

    os.environ.update(
        {
            "PROCESSING_DATA_DIR": str(data_dir.resolve()),
            "PROCESSING_MODEL_DIR": str(model_dir.resolve()),
            "PROCESSING_OUTPUT_DIR": str(forecast_dir.resolve()),
            "MODEL_PACKAGE_ARN": "local",
            "MODEL_VERSION": f"local-{data_as_of}",
        }
    )

    from sagemaker.jobs.batch_predict import main as predict

    predict()
    return forecast_dir / "forecasts.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--data-as-of", default=DEFAULT_DATA_AS_OF)
    parser.add_argument("--iterations", type=int, default=CatBoostConfig.iterations)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.iterations < 1:
        raise SystemExit("--iterations must be a positive integer")
    if not args.data_dir.is_dir():
        raise SystemExit(f"Forecast data directory does not exist: {args.data_dir}")
    destination = run_pipeline(args.data_dir, args.output_dir, args.data_as_of, args.iterations)
    print(f"Local model bundle: {args.output_dir / 'model'}")
    print(f"Backtest report: {args.output_dir / 'evaluation' / 'evaluation.json'}")
    print(f"Forecasts: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

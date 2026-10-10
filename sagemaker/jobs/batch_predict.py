"""Create every store-SKU 14-day forecast for publishing to DynamoDB."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from scripts.forecast.data import load_forecast_data, load_future_context
from scripts.forecast.modeling import load_bundle, predict_batch

from .common import environment_path, extract_model_archive


def _records(batch: pd.DataFrame, metadata: dict, model_package_arn: str, model_version: str) -> Iterator[dict]:
    for (store_id, product_id), frame in batch.groupby(["store_id", "product_id"], observed=True, sort=True):
        points = [
            {
                "date": row.date.strftime("%Y-%m-%d"),
                "p50": round(float(row.p50), 3),
                "p80": round(float(row.p80), 3),
                "p90": round(float(row.p90), 3),
            }
            for row in frame.sort_values("date").itertuples(index=False)
        ]
        yield {
            "store_id": str(store_id),
            "product_id": str(product_id),
            "data_as_of": metadata["data_as_of"],
            "model_package_arn": model_package_arn,
            "model_version": model_version,
            "backtest_wape_p50": metadata.get("metrics", {}).get("mean_wape_p50"),
            "forecast": points,
        }


def main() -> None:
    data_dir = environment_path("PROCESSING_DATA_DIR", "/opt/ml/processing/input/data")
    archive_dir = environment_path("PROCESSING_MODEL_DIR", "/opt/ml/processing/input/model")
    output_dir = environment_path("PROCESSING_OUTPUT_DIR", "/opt/ml/processing/output")
    model_dir = Path("/tmp/stockcopilot-model")
    archives = sorted(archive_dir.glob("*.tar.gz"))
    if len(archives) == 1:
        extract_model_archive(archives[0], model_dir)
    elif not archives and (archive_dir / "metadata.json").is_file():
        model_dir = archive_dir
    else:
        raise ValueError(f"Expected one model.tar.gz or a local model bundle, found {archives}")
    models, metadata = load_bundle(model_dir)
    metrics_path = model_dir / "metrics.json"
    if metrics_path.exists():
        metadata["metrics"] = json.loads(metrics_path.read_text())
    data = load_forecast_data(data_dir)
    as_of = pd.Timestamp(metadata["data_as_of"])
    known_calendar_context = data.context.loc[data.context["date"] > as_of].copy()
    future_context = load_future_context(data_dir)
    context = (
        pd.concat([known_calendar_context, future_context], ignore_index=True)
        .drop_duplicates(subset=["date"], keep="first")
        .sort_values("date")
        .head(14)
        .reset_index(drop=True)
    )
    if len(context) != 14:
        raise ValueError("Exactly 14 future calendar/weather context rows are required")
    batch = predict_batch(models, data.observed, data.stores, data.products, context, metadata["data_as_of"])
    package_arn = os.environ.get("MODEL_PACKAGE_ARN", "unregistered")
    version = os.environ.get("MODEL_VERSION", "unknown")

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "forecasts.jsonl"
    with destination.open("w", encoding="utf-8") as stream:
        for record in _records(batch, metadata, package_arn, version):
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"Wrote {len(batch) // len(context):,} store-SKU forecast records to {destination}")


if __name__ == "__main__":
    main()

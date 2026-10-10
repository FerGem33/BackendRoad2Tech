"""Generate a contract-compatible 14-day forecast from local CatBoost artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DEFAULT_ARTIFACT_DIR, DEFAULT_DATA_AS_OF, DEFAULT_DATA_DIR
from .data import load_forecast_data, load_future_context
from .modeling import forecast_contract, load_bundle, predict_batch


def get_demand_forecast(
    store_id: str,
    product_id: str,
    model_dir: Path,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> dict:
    """Adapter for the architecture's `get_demand_forecast` tool contract."""
    models, metadata = load_bundle(model_dir)
    data = load_forecast_data(data_dir)
    context = load_future_context(data_dir)
    batch = predict_batch(models, data.observed, data.stores, data.products, context, metadata["data_as_of"])
    return forecast_contract(batch, store_id, product_id, metadata["data_as_of"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_ARTIFACT_DIR / DEFAULT_DATA_AS_OF)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    response = get_demand_forecast(args.store_id, args.product_id, args.model_dir, args.data_dir)
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    main()

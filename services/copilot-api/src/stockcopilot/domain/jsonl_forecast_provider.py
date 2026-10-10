"""Read a validated local CatBoost batch forecast without loading model dependencies."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .forecast import ForecastResult
from .forecast_provider import ForecastUnavailableError

HORIZON_DAYS = 14


class JsonlForecastProvider:
    """In-memory index of the forecast JSONL emitted by the local training pipeline."""

    def __init__(self, path: Path, expected_as_of: date):
        self.path = Path(path)
        self.as_of = expected_as_of
        self._records: dict[tuple[str, str], dict] = {}
        if not self.path.is_file():
            raise FileNotFoundError(f"CatBoost forecast file not found: {self.path}")

        with self.path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    key = (str(record["store_id"]), str(record["product_id"]))
                    if not all(key):
                        raise ValueError("store_id and product_id must be non-empty")
                    if key in self._records:
                        raise ValueError(f"duplicate store/product pair {key}")
                    if date.fromisoformat(str(record["data_as_of"])) != expected_as_of:
                        raise ValueError(f"data_as_of must match DEMO_AS_OF={expected_as_of}")
                    self._validate_points(record["forecast"], expected_as_of)
                    self._records[key] = record
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Invalid CatBoost forecast at {self.path}:{line_number}: {exc}") from exc
        if not self._records:
            raise ValueError(f"CatBoost forecast file is empty: {self.path}")

    @staticmethod
    def _validate_points(points: list[dict], as_of: date) -> None:
        if not isinstance(points, list) or len(points) != HORIZON_DAYS:
            raise ValueError(f"forecast must have exactly {HORIZON_DAYS} points")
        for offset, point in enumerate(points, start=1):
            if date.fromisoformat(str(point["date"])) != as_of + timedelta(days=offset):
                raise ValueError("forecast dates must be consecutive days after data_as_of")
            quantiles = [float(point[name]) for name in ("p50", "p80", "p90")]
            if not all(math.isfinite(value) for value in quantiles):
                raise ValueError("forecast quantiles must be finite")
            if not 0 <= quantiles[0] <= quantiles[1] <= quantiles[2]:
                raise ValueError("forecast quantiles must be non-negative and ordered")

    def forecast(self, store_id: str, product_id: str, as_of: date, horizon: int) -> ForecastResult:
        if not 1 <= horizon <= HORIZON_DAYS:
            raise ValueError(f"CatBoost batch forecasts support horizons from 1 through {HORIZON_DAYS} days")
        if as_of != self.as_of:
            raise ForecastUnavailableError(f"CatBoost forecast is for {self.as_of}, not {as_of}")
        record = self._records.get((store_id, product_id))
        if record is None:
            raise ForecastUnavailableError(f"No CatBoost forecast exists for {store_id}/{product_id}")
        points = record["forecast"][:horizon]
        return ForecastResult(
            store_id=store_id,
            product_id=product_id,
            as_of=as_of,
            dates=[date.fromisoformat(point["date"]) for point in points],
            p50=np.array([float(point["p50"]) for point in points]),
            p80=np.array([float(point["p80"]) for point in points]),
            p90=np.array([float(point["p90"]) for point in points]),
            model=f"catboost-local-{str(record.get('model_version', 'unknown')).removeprefix('local-')}",
            backtest_wape=(
                float(record["backtest_wape_p50"])
                if record.get("backtest_wape_p50") is not None
                else None
            ),
        )

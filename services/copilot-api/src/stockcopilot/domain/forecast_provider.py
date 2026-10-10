"""Forecast providers that preserve the domain contract across local and AWS runtimes."""

from __future__ import annotations

from datetime import date
from typing import Protocol

import numpy as np

from .forecast import ForecastResult


class ForecastProvider(Protocol):
    """Read a previously computed baseline-demand forecast for one store and product."""

    def forecast(self, store_id: str, product_id: str, as_of: date, horizon: int) -> ForecastResult: ...


class ForecastUnavailableError(RuntimeError):
    """The active forecast table does not contain a requested valid forecast."""


class DynamoForecastProvider:
    """Low-latency provider for validated SageMaker batch forecasts stored in DynamoDB."""

    def __init__(self, table_name: str, region_name: str | None = None):
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region_name).Table(table_name)

    def forecast(self, store_id: str, product_id: str, as_of: date, horizon: int) -> ForecastResult:
        if not 1 <= horizon <= 14:
            raise ValueError("SageMaker batch forecasts support horizons from 1 through 14 days")
        item = self._table.get_item(
            Key={"pk": f"FORECAST#{store_id}#{product_id}", "sk": f"AS_OF#{as_of.isoformat()}"}
        ).get("Item")
        if item is None:
            raise ForecastUnavailableError(
                f"No SageMaker forecast exists for {store_id}/{product_id} as of {as_of.isoformat()}"
            )
        points = item.get("forecast", [])[:horizon]
        if len(points) != horizon:
            raise ForecastUnavailableError(f"Forecast has {len(points)} points but {horizon} were requested")
        dates = [date.fromisoformat(str(point["date"])) for point in points]
        if any(day <= as_of for day in dates):
            raise ForecastUnavailableError("Forecast contains a non-future date")
        p50 = np.array([float(point["p50"]) for point in points])
        p80 = np.array([float(point["p80"]) for point in points])
        p90 = np.array([float(point["p90"]) for point in points])
        if np.any(p50 < 0) or np.any(p80 < p50) or np.any(p90 < p80):
            raise ForecastUnavailableError("Forecast quantiles violate their contract")
        return ForecastResult(
            store_id=store_id,
            product_id=product_id,
            as_of=as_of,
            dates=dates,
            p50=p50,
            p80=p80,
            p90=p90,
            model=f"sagemaker-catboost-{item['model_version']}",
            backtest_wape=float(item["backtest_wape_p50"]) if item.get("backtest_wape_p50") is not None else None,
        )

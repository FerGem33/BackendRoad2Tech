from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stockcopilot.api.app import create_app
from stockcopilot.config import Settings
from stockcopilot.domain.forecast_provider import ForecastUnavailableError
from stockcopilot.domain.jsonl_forecast_provider import JsonlForecastProvider

AS_OF = date(2026, 9, 16)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "v2"


def record(store_id: str = "S001", product_id: str = "P001") -> dict:
    return {
        "store_id": store_id,
        "product_id": product_id,
        "data_as_of": AS_OF.isoformat(),
        "model_version": "local-2026-09-16",
        "backtest_wape_p50": 0.12,
        "forecast": [
            {
                "date": (AS_OF + timedelta(days=day)).isoformat(),
                "p50": 10.0,
                "p80": 12.0,
                "p90": 15.0,
            }
            for day in range(1, 15)
        ],
    }


def write_forecasts(path, *records: dict) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def test_jsonl_forecast_reaches_tendency_api(tmp_path) -> None:
    path = tmp_path / "forecasts.jsonl"
    write_forecasts(path, record())
    settings = Settings(
        dataset_dir=FIXTURE_DIR,
        demo_as_of=AS_OF,
        forecast_provider="jsonl",
        forecast_file=path,
        llm_mode="offline",
        _env_file=None,
    )
    with TestClient(create_app(settings, include_agent=False)) as client:
        response = client.get("/stores/S001/tendency", params={"product_id": "P001", "forecast_days": 7})
        missing = client.get("/stores/S001/tendency", params={"product_id": "P010", "forecast_days": 7})
    assert response.status_code == 200
    result = response.json()["data"]
    assert len(result["forecast_series"]) == 7
    assert result["forecast_series"][0]["p50"] == 10.0
    assert result["model"]["source"] == "local CatBoost batch forecast"
    assert result["model"]["backtest_wape_14d"] == 0.12
    assert missing.status_code == 503
    assert missing.json()["code"] == "forecast_unavailable"


def test_jsonl_provider_rejects_wrong_cutoff_duplicates_and_bad_quantiles(tmp_path) -> None:
    path = tmp_path / "forecasts.jsonl"
    write_forecasts(path, record())
    with pytest.raises(ValueError, match="data_as_of must match"):
        JsonlForecastProvider(path, date(2026, 9, 15))

    write_forecasts(path, record(), record())
    with pytest.raises(ValueError, match="duplicate store/product pair"):
        JsonlForecastProvider(path, AS_OF)

    bad = record()
    bad["forecast"][0]["p80"] = 9.0
    write_forecasts(path, bad)
    with pytest.raises(ValueError, match="quantiles must be non-negative and ordered"):
        JsonlForecastProvider(path, AS_OF)


def test_jsonl_provider_rejects_missing_product_and_invalid_horizon(tmp_path) -> None:
    path = tmp_path / "forecasts.jsonl"
    write_forecasts(path, record())
    provider = JsonlForecastProvider(path, AS_OF)
    with pytest.raises(ForecastUnavailableError, match="No CatBoost forecast"):
        provider.forecast("S001", "P002", AS_OF, 7)
    with pytest.raises(ValueError, match="1 through 14"):
        provider.forecast("S001", "P001", AS_OF, 15)

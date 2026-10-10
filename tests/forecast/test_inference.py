from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.forecast.modeling import forecast_contract, predict_batch


class FixedModel:
    def __init__(self, value: float):
        self.value = value

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value)


def _inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    observed = pd.DataFrame({"date": dates, "store_id": "S001", "product_id": "P001"})
    observed["demand_target"] = 4.0
    observed["is_treated_campaign"] = False
    stores = pd.DataFrame({
        "store_id": ["S001"], "zone_type": ["residential"], "secondary_zone_type": [None],
        "size_category": ["medium"], "is_24h": [1],
    })
    products = pd.DataFrame({
        "product_id": ["P001"], "category_id": ["C01"], "brand": ["Demo"], "unit": ["piece"],
        "shelf_life_days": [30], "retail_price": [10.0], "is_seasonal": [0],
        "weather_sensitivity": ["neutral"], "base_daily_demand": [4.0],
    })
    future_dates = pd.date_range("2026-01-31", periods=14, freq="D")
    context = pd.DataFrame({
        "date": future_dates, "is_weekend": (future_dates.dayofweek >= 5).astype(int), "is_holiday": 0,
        "holiday_name": None, "season_name": "winter", "event_name": None, "event_scope": None,
        "is_payday": 0, "is_school_vacation": 0, "is_college_vacation": 0,
        "temp_max_c": 25.0, "temp_min_c": 12.0, "precipitation_mm": 0.0, "weather_label": "mild",
    })
    return observed, stores, products, context


def test_inference_returns_ordered_quantiles_and_contract() -> None:
    observed, stores, products, context = _inputs()
    models = {0.5: FixedModel(10.0), 0.8: FixedModel(5.0), 0.9: FixedModel(8.0)}
    batch = predict_batch(models, observed, stores, products, context, "2026-01-30")
    assert len(batch) == 14
    assert (batch["p50"] <= batch["p80"]).all()
    assert (batch["p80"] <= batch["p90"]).all()
    response = forecast_contract(batch, "S001", "P001", "2026-01-30")
    assert response["data_as_of"] == "2026-01-30"
    assert len(response["horizon"]) == 14
    assert response["p50"] == [10.0] * 14

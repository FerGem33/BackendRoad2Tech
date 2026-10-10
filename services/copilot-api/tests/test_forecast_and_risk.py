from __future__ import annotations

import numpy as np

from stockcopilot.domain.tendency import get_product_tendency


def test_forecast_quantiles_are_ordered_and_non_negative(svc):
    forecast = svc.forecast("S001", "P001", 14)
    assert len(forecast.dates) == 14
    assert forecast.dates[0] > svc.as_of
    assert np.all(forecast.p50 >= 0)
    assert np.all(forecast.p80 >= forecast.p50)
    assert np.all(forecast.p90 >= forecast.p80)
    assert forecast.backtest_wape is not None


def test_tendency_contract(svc):
    t = get_product_tendency(svc, "S016", "sandwich de jamon", history_days=60, forecast_days=7)
    assert t.product.product_id == "P059"
    assert len(t.historical_series) == 60
    assert t.historical_series[-1].date == svc.as_of
    assert len(t.forecast_series) == 7
    assert t.trend in ("increasing", "stable", "decreasing")
    assert 0 <= t.trend_strength <= 1
    assert t.stockout_risk.level in ("low", "medium", "high")
    assert t.waste_risk.expected_waste_units >= 0
    assert t.ai_summary


def test_tendency_clamps_windows(svc):
    t = get_product_tendency(svc, "S001", "P001", history_days=5, forecast_days=90)
    assert len(t.historical_series) == 14
    assert len(t.forecast_series) == 28

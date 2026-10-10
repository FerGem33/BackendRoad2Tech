"""`get_product_tendency`: history + forecast + trend + risks for one store x product."""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from .models import (
    DriverOut,
    ForecastPoint,
    InventoryOut,
    ModelInfo,
    ProductTendency,
    SeriesPoint,
    StockLotOut,
)
from .risk import stockout_risk, waste_risk
from .services import Services

TREND_THRESHOLD_PCT_PER_WEEK = 1.5


def _trend(y: np.ndarray) -> tuple[str, float, float]:
    """Slope of the 7-day rolling mean, as % of the mean per week, plus a 0..1 strength."""
    if len(y) < 14 or y.mean() <= 0:
        return "stable", 0.0, 0.0
    rolling = np.convolve(y, np.ones(7) / 7, mode="valid")
    x = np.arange(len(rolling), dtype=float)
    slope, intercept = np.polyfit(x, rolling, 1)
    fitted = slope * x + intercept
    ss_res = float(((rolling - fitted) ** 2).sum())
    ss_tot = float(((rolling - rolling.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    pct = float(slope * 7 / rolling.mean() * 100)
    if pct >= TREND_THRESHOLD_PCT_PER_WEEK:
        label = "increasing"
    elif pct <= -TREND_THRESHOLD_PCT_PER_WEEK:
        label = "decreasing"
    else:
        label = "stable"
    strength = min(1.0, abs(pct) / 10) * (0.5 + 0.5 * max(r2, 0.0))
    return label, round(pct, 2), round(float(strength), 2)


def template_summary(t: ProductTendency) -> str:
    top = t.main_drivers[0].detail if t.main_drivers else "No strong calendar, weather or weekday effect."
    return (
        f"{t.product.product_name} at {t.store.store_name}: demand is {t.trend} "
        f"({t.trend_pct_per_week:+.1f}% per week over the last {t.history_days} days). "
        f"Next {t.forecast_days} days: ~{t.forecast_avg_daily_units:.1f} units/day "
        f"({t.forecast_vs_recent_pct:+.0f}% vs the last 28 days). {top} "
        f"Run-out risk is {t.stockout_risk.level} and waste risk is {t.waste_risk.level}."
    )


def get_product_tendency(
    svc: Services, store: str, product: str, history_days: int = 90, forecast_days: int = 14
) -> ProductTendency:
    s = svc.catalog.resolve_store(store)
    p = svc.catalog.resolve_product(product)
    history_days = int(np.clip(history_days, 14, 365))
    max_horizon = 14 if svc.forecast_provider is not None else 28
    forecast_days = int(np.clip(forecast_days, 1, max_horizon))

    full_history = svc.history(s.store_id)
    y_all = full_history.row(p.product_id)
    y = y_all[-history_days:]
    dates = full_history.dates[-history_days:]
    forecast = svc.forecast(s.store_id, p.product_id, forecast_days)

    position = svc.position(s.store_id).products[p.product_id]
    unit_cost = svc.catalog.unit_cost(p.product_id, position.last_supplier_id)
    trend, pct, strength = _trend(y)
    recent = float(y_all[-28:].mean())
    next_delivery = None
    if position.in_transit:
        # Late orders whose expected date already passed are assumed to arrive tomorrow.
        next_delivery = max(position.in_transit[0].expected_delivery_date, svc.as_of + timedelta(days=1))
    upcoming = float(forecast.p50.mean())

    result = ProductTendency(
        store=svc.store_ref(s),
        product=svc.product_ref(p),
        data_as_of=svc.as_of,
        history_days=history_days,
        forecast_days=forecast_days,
        historical_series=[SeriesPoint(date=d, value=float(v)) for d, v in zip(dates, y, strict=True)],
        forecast_series=[
            ForecastPoint(date=d, p50=round(float(a), 2), p80=round(float(b), 2), p90=round(float(c), 2))
            for d, a, b, c in zip(forecast.dates, forecast.p50, forecast.p80, forecast.p90, strict=True)
        ],
        trend=trend,
        trend_strength=strength,
        trend_pct_per_week=pct,
        recent_avg_daily_units=round(recent, 2),
        forecast_avg_daily_units=round(upcoming, 2),
        forecast_vs_recent_pct=round((upcoming / recent - 1) * 100, 1) if recent > 0 else 0.0,
        main_drivers=[DriverOut(**d.__dict__) for d in forecast.drivers[:5]],
        stockout_risk=stockout_risk(svc.catalog, position, forecast, svc.as_of),
        waste_risk=waste_risk(svc.catalog, position, forecast, svc.as_of, unit_cost),
        inventory=InventoryOut(
            on_hand=position.on_hand,
            in_transit=position.in_transit_units,
            next_delivery_date=next_delivery,
            lots=[StockLotOut(expiration_date=lot.expiration_date, quantity=lot.quantity) for lot in position.lots],
        ),
        model=ModelInfo(
            name=forecast.model,
            backtest_wape_14d=round(forecast.backtest_wape, 3) if forecast.backtest_wape is not None else None,
            source=(
                "SageMaker batch forecast" if forecast.model.startswith("sagemaker-")
                else "local CatBoost batch forecast" if forecast.model.startswith("catboost-local-")
                else "deterministic baseline"
            ),
        ),
        ai_summary="",
    )
    result.ai_summary = template_summary(result)
    return result

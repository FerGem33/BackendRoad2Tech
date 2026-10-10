"""Stock-out and waste risk from the current position plus the forecast (deterministic)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .catalog import Catalog
from .forecast import ForecastResult
from .inventory import ProductPosition, project_inventory
from .models import StockoutRisk, WasteRisk


def _extend(values: np.ndarray, length: int) -> np.ndarray:
    if len(values) >= length:
        return values[:length]
    fill = float(values.mean()) if len(values) else 0.0
    return np.concatenate([values, np.full(length - len(values), fill)])


def fastest_lead_time(catalog: Catalog, product_id: str) -> int:
    offers = catalog.offers_by_product.get(product_id, [])
    return min((o.lead_time_days for o in offers), default=3)


def stockout_risk(catalog: Catalog, position: ProductPosition, forecast: ForecastResult, as_of: date) -> StockoutRisk:
    """Can current stock (plus orders in transit) last until a new order placed today arrives?"""
    product = catalog.products[position.product_id]
    lead = fastest_lead_time(catalog, product.product_id)
    window = lead + 1
    start = as_of + timedelta(days=1)
    mean_demand = float(forecast.p50.mean()) if len(forecast.p50) else 0.0

    p50 = project_inventory(position, start, _extend(forecast.p50, window), product.shelf_life_days)
    p90 = project_inventory(position, start, _extend(forecast.p90, window), product.shelf_life_days)
    full = project_inventory(position, start, forecast.p50, product.shelf_life_days)
    window_demand = float(_extend(forecast.p50, window).sum())

    days_of_cover = round(position.on_hand / mean_demand, 1) if mean_demand > 0 else None
    first = full.first_stockout_index()
    if p50.total_lost > 0.5:
        level, score = "high", 0.7 + 0.3 * min(1.0, p50.total_lost / max(window_demand, 1.0))
        detail = (
            f"Expected demand runs out the shelf before a new order can arrive ({lead}-day lead time); "
            f"~{p50.total_lost:.0f} units of demand would be lost."
        )
    elif p90.total_lost > 0.5:
        level, score = "medium", 0.35 + 0.3 * min(1.0, p90.total_lost / max(window_demand, 1.0))
        detail = (
            f"Stock covers expected demand, but a high-demand day (P90) would cause a run-out within {window} days."
        )
    else:
        cover = days_of_cover if days_of_cover is not None else 99.0
        level, score = "low", max(0.0, min(0.3, 0.3 * window / max(cover, 1.0)))
        days = "day" if lead == 1 else "days"
        detail = f"Stock covers ~{cover:.0f} days of expected demand; the fastest supplier delivers in {lead} {days}."
    return StockoutRisk(
        level=level,
        score=round(float(score), 2),
        days_of_cover=days_of_cover,
        first_stockout_date=start + timedelta(days=first) if first is not None else None,
        expected_lost_units=round(full.total_lost, 1),
        detail=detail,
    )


def waste_risk(
    catalog: Catalog, position: ProductPosition, forecast: ForecastResult, as_of: date, unit_cost: float
) -> WasteRisk:
    """Units expected to expire unsold within the forecast horizon if nothing changes."""
    product = catalog.products[position.product_id]
    horizon = len(forecast.p50)
    start = as_of + timedelta(days=1)
    end = as_of + timedelta(days=horizon)
    expiring = sum(lot.quantity for lot in position.lots if lot.expiration_date <= end)
    projection = project_inventory(position, start, forecast.p50, product.shelf_life_days)
    waste = projection.total_waste
    exposure = max(position.on_hand + position.in_transit_units, 1)
    ratio = waste / exposure
    if ratio >= 0.15 and waste >= 2:
        level = "high"
    elif ratio >= 0.03 or waste >= 3:
        level = "medium"
    else:
        level = "low"
    if waste >= 0.5:
        detail = (
            f"~{waste:.0f} of {exposure} units (on hand + in transit) are expected to expire unsold "
            f"by {end.strftime('%b %d')} (shelf life {product.shelf_life_days} days)."
        )
    elif expiring:
        detail = f"{expiring:.0f} units expire by {end.strftime('%b %d')}, but expected demand sells them in time."
    else:
        detail = f"No lot expires before {end.strftime('%b %d')}."
    return WasteRisk(
        level=level,
        score=round(float(min(1.0, ratio * 3)), 2),
        expiring_units=round(float(expiring), 1),
        expected_waste_units=round(waste, 1),
        expected_waste_value=round(waste * unit_cost, 2),
        detail=detail,
    )

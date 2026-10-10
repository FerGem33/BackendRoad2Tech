"""Store overview (KPIs, movers, alerts) and demand of one product across all branches."""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from .models import (
    AlertOut,
    DemandByStore,
    MoverOut,
    SeriesPoint,
    StoreDemandOut,
    StoreKpis,
    StoreOverview,
)
from .risk import stockout_risk, waste_risk
from .services import Services

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _pct(new: float, old: float) -> float:
    return round((new / old - 1) * 100, 1) if old > 0 else 0.0


def get_store_overview(svc: Services, store: str, period_days: int = 7) -> StoreOverview:
    s = svc.catalog.resolve_store(store)
    period_days = max(1, min(int(period_days), 90))
    history = svc.history(s.store_id)
    prices = np.array([svc.catalog.products[pid].retail_price for pid in history.product_ids])
    units = history.units
    revenue = units * prices[:, None]

    current = revenue[:, -period_days:].sum()
    previous = revenue[:, -2 * period_days : -period_days].sum()
    daily = revenue[:, -28:].sum(axis=0)

    position = svc.position(s.store_id)
    alerts: list[AlertOut] = []
    run_outs = 0
    waste_products = 0
    for i, product_id in enumerate(history.product_ids):
        product = svc.catalog.products[product_id]
        forecast = svc.forecast(s.store_id, product_id, 14)
        pos = position.products[product_id]
        stock = stockout_risk(svc.catalog, pos, forecast, svc.as_of)
        unit_cost = svc.catalog.unit_cost(product_id, pos.last_supplier_id)
        waste = waste_risk(svc.catalog, pos, forecast, svc.as_of, unit_cost)
        if stock.level == "high":
            run_outs += 1
            alerts.append(
                AlertOut(
                    kind="run_out",
                    severity="high",
                    product_id=product_id,
                    product_name=product.product_name,
                    date=stock.first_stockout_date,
                    message=f"{product.product_name}: {stock.detail}",
                )
            )
        if waste.level != "low":
            waste_products += 1
            first_expiry = next((lot.expiration_date for lot in pos.lots), None)
            alerts.append(
                AlertOut(
                    kind="expiration",
                    severity=waste.level,
                    product_id=product_id,
                    product_name=product.product_name,
                    date=first_expiry,
                    message=f"{product.product_name}: {waste.detail}",
                )
            )
        recent = units[i, -28:].mean()
        upcoming = forecast.p50[:7].mean()
        if recent >= 2 and upcoming / recent - 1 >= 0.25:
            reason = next((d for d in forecast.drivers if d.impact_pct > 0), None)
            alerts.append(
                AlertOut(
                    kind="demand_spike",
                    severity="medium",
                    product_id=product_id,
                    product_name=product.product_name,
                    date=svc.as_of + timedelta(days=1),
                    message=f"{product.product_name}: demand expected {_pct(upcoming, recent):+.0f}% next week"
                    + (f" ({reason.name})." if reason else "."),
                )
            )

    no_demand_mask = units[:, -14:].sum(axis=1) == 0
    for i in np.nonzero(no_demand_mask)[0]:
        product = svc.catalog.products[history.product_ids[i]]
        alerts.append(
            AlertOut(
                kind="no_demand",
                severity="medium",
                product_id=product.product_id,
                product_name=product.product_name,
                date=None,
                message=f"{product.product_name}: no units sold in the last 14 days.",
            )
        )

    recent_units = units[:, -28:].sum(axis=1)
    previous_units = units[:, -56:-28].sum(axis=1)
    movers = [
        MoverOut(
            product=svc.product_ref(svc.catalog.products[pid]),
            recent_units=float(recent_units[i]),
            previous_units=float(previous_units[i]),
            change_pct=_pct(recent_units[i], previous_units[i]),
        )
        for i, pid in enumerate(history.product_ids)
        if previous_units[i] >= 28
    ]
    movers.sort(key=lambda m: m.change_pct, reverse=True)
    alerts.sort(key=lambda a: (SEVERITY_ORDER[a.severity], a.kind))

    return StoreOverview(
        store=svc.store_ref(s),
        data_as_of=svc.as_of,
        period_from=svc.as_of - timedelta(days=period_days - 1),
        period_to=svc.as_of,
        kpis=StoreKpis(
            total_sales=round(float(current), 2),
            total_sales_previous=round(float(previous), 2),
            sales_change_pct=_pct(float(current), float(previous)),
            units_sold=int(units[:, -period_days:].sum()),
            run_outs=run_outs,
            no_demand=int(no_demand_mask.sum()),
            waste_risk_products=waste_products,
        ),
        daily_sales=[
            SeriesPoint(date=d, value=round(float(v), 2)) for d, v in zip(history.dates[-28:], daily, strict=True)
        ],
        top_movers_up=[m for m in movers[:5] if m.change_pct > 0],
        top_movers_down=[m for m in reversed(movers[-5:]) if m.change_pct < 0],
        alerts=alerts[:15],
    )


def get_demand_by_store(svc: Services, product: str, days: int = 28) -> DemandByStore:
    p = svc.catalog.resolve_product(product)
    days = max(7, min(int(days), 180))
    start = svc.as_of - timedelta(days=2 * days - 1)
    series = svc.repo.sales_by_store(p.product_id, start, svc.as_of)
    rows: list[StoreDemandOut] = []
    for store_id, values in series.items():
        store = svc.catalog.stores[store_id]
        recent = float(values[-days:].sum())
        previous = float(values[:-days].sum())
        forecast = svc.forecast(store_id, p.product_id, 14)
        rows.append(
            StoreDemandOut(
                store=svc.store_ref(store),
                recent_units=recent,
                previous_units=previous,
                change_pct=_pct(recent, previous),
                forecast_next_7d_units=round(float(forecast.p50[:7].sum()), 1),
            )
        )
    rows.sort(key=lambda r: r.recent_units, reverse=True)
    return DemandByStore(
        product=svc.product_ref(p),
        data_as_of=svc.as_of,
        days=days,
        network_recent_units=round(sum(r.recent_units for r in rows), 1),
        stores=rows,
    )

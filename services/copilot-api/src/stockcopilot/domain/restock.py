"""Restock suggestions using the dataset's best-supplier rule (`scripts/dataset/supplier_selection.py`).

1. days_coverage = current_stock / predicted_daily_demand
2. Keep suppliers with lead_time_days <= days_coverage; if none, take the fastest and flag urgent.
3. qty = max(min_order_qty, ceil(predicted_daily_demand x (lead_time_days + cycle_days))),
   cycle_days = min(7, shelf_life_days)
4. waste = max(0, qty - predicted_daily_demand x shelf_life_days)
5. score = ((retail_price - unit_cost) x (qty - waste) - unit_cost x waste) x reliability_score
6. Highest score wins; ties -> lowest lead time, then lowest supplier_id.

An order is suggested when stock on hand + on order <= reorder point, where
reorder_point = predicted_daily_demand x (median supplier lead time + safety_days).
"""

from __future__ import annotations

import math
from datetime import timedelta
from statistics import median

from ..data.repository import SupplierOffer
from .inventory import ReorderPolicy
from .models import RestockPlan, RestockReason, RestockSuggestionOut, SupplierEmail
from .services import Services

CYCLE_DAYS = 7


def _safety_days(shelf_life_days: int) -> int:
    return 1 if shelf_life_days <= 30 else 3


def _choose_supplier(
    svc: Services, offers: list[SupplierOffer], pdd: float, current_stock: float, price: float, shelf: int
) -> tuple[SupplierOffer, int, bool]:
    coverage = current_stock / pdd if pdd > 0 else math.inf
    eligible = [o for o in offers if o.lead_time_days <= coverage]
    urgent = not eligible
    if urgent:
        fastest = min(o.lead_time_days for o in offers)
        eligible = [o for o in offers if o.lead_time_days == fastest]
    cycle = min(CYCLE_DAYS, shelf)
    best: tuple[float, int, str] | None = None
    chosen: tuple[SupplierOffer, int] | None = None
    for offer in eligible:
        qty = max(offer.min_order_qty, math.ceil(pdd * (offer.lead_time_days + cycle)))
        waste = max(0.0, qty - pdd * shelf)
        profit = (price - offer.unit_cost) * (qty - waste) - offer.unit_cost * waste
        score = profit * svc.catalog.suppliers[offer.supplier_id].reliability_score
        key = (-score, offer.lead_time_days, offer.supplier_id)
        if best is None or key < best:
            best, chosen = key, (offer, qty)
    assert chosen is not None
    return chosen[0], chosen[1], urgent


def reorder_policy(svc: Services, store_id: str, product_id: str) -> ReorderPolicy | None:
    """Standing reorder rule for a store x product, from the baseline forecast of the next 7 days."""
    product = svc.catalog.products[product_id]
    offers = svc.catalog.offers_by_product.get(product_id, [])
    pdd = float(svc.forecast(store_id, product_id, 14).p50[:7].mean())
    if not offers or pdd <= 0:
        return None
    pos = svc.position(store_id).products[product_id]
    offer, qty, _ = _choose_supplier(
        svc, offers, pdd, pos.on_hand + pos.in_transit_units, product.retail_price, product.shelf_life_days
    )
    reorder_point = pdd * (median(o.lead_time_days for o in offers) + _safety_days(product.shelf_life_days))
    return ReorderPolicy(reorder_point=reorder_point, quantity=qty, lead_time_days=offer.lead_time_days)


def get_restock_suggestions(svc: Services, store: str, product: str | None = None, limit: int = 5) -> RestockPlan:
    s = svc.catalog.resolve_store(store)
    position = svc.position(s.store_id)
    product_ids = [svc.catalog.resolve_product(product).product_id] if product else list(svc.catalog.products)
    limit = max(1, min(int(limit), 20))

    suggestions: list[RestockSuggestionOut] = []
    notes: list[str] = []
    for product_id in product_ids:
        p = svc.catalog.products[product_id]
        offers = svc.catalog.offers_by_product.get(product_id, [])
        if not offers:
            continue
        forecast = svc.forecast(s.store_id, product_id, 14)
        pdd = float(forecast.p50[:7].mean())
        if pdd <= 0:
            continue
        pos = position.products[product_id]
        current = pos.on_hand + pos.in_transit_units
        reorder_point = pdd * (median(o.lead_time_days for o in offers) + _safety_days(p.shelf_life_days))
        if current > reorder_point:
            if product:
                notes.append(
                    f"{p.product_name} does not need an order yet: {current} units on hand/on order cover "
                    f"~{current / pdd:.0f} days (reorder point {reorder_point:.0f} units)."
                )
            continue

        offer, qty, urgent = _choose_supplier(svc, offers, pdd, current, p.retail_price, p.shelf_life_days)
        supplier = svc.supplier_ref(offer.supplier_id)
        reasons: list[RestockReason] = ["stock_limit"]
        details = [f"Stock + on order ({current} units) is at or below the reorder point ({reorder_point:.0f})."]
        recent = float(svc.history(s.store_id).row(product_id)[-28:].mean())
        if recent > 0 and pdd / recent - 1 >= 0.1:
            reasons.append("demand")
            details.append(f"Demand for the next 7 days is {round((pdd / recent - 1) * 100)}% above the last 28 days.")
        seasonal = [d for d in forecast.drivers if d.kind in ("event", "weather", "season") and d.impact_pct > 0]
        if p.is_seasonal or seasonal:
            reasons.append("season")
            details.append(seasonal[0].detail if seasonal else "Seasonal product.")
        arrival = svc.as_of + timedelta(days=offer.lead_time_days)
        cost = round(qty * offer.unit_cost, 2)
        suggestions.append(
            RestockSuggestionOut(
                id=f"RS-{s.store_id}-{product_id}-{svc.as_of.isoformat()}",
                store_id=s.store_id,
                product=svc.product_ref(p),
                supplier=supplier,
                suggested_qty=qty,
                order_date=svc.as_of,
                expected_arrival_date=arrival,
                lead_time_days=offer.lead_time_days,
                unit_cost=offer.unit_cost,
                order_cost=cost,
                is_urgent=urgent,
                on_hand=pos.on_hand,
                in_transit=pos.in_transit_units,
                predicted_daily_demand=round(pdd, 2),
                days_of_cover=round(pos.on_hand / pdd, 1),
                reorder_point=round(reorder_point, 1),
                reasons=reasons,
                reason_details=details,
                email_draft=SupplierEmail(
                    to=supplier.email,
                    subject=f"Purchase order: {qty} x {p.product_name} for {s.store_name}",
                    body=(
                        f"Hello {supplier.supplier_name},\n\n"
                        f"Please deliver {qty} units of {p.product_name} ({p.product_id}) to {s.store_name} "
                        f"({s.colonia}, Saltillo) by {arrival.isoformat()}"
                        f"{' - this order is urgent' if urgent else ''}.\n"
                        f"Agreed unit cost: ${offer.unit_cost:.2f} MXN. Order total: ${cost:,.2f} MXN.\n\n"
                        "Thank you,\nStore manager - StockCopilot"
                    ),
                ),
            )
        )

    suggestions.sort(key=lambda r: (not r.is_urgent, r.days_of_cover if r.days_of_cover is not None else 999))
    return RestockPlan(
        store=svc.store_ref(s),
        data_as_of=svc.as_of,
        checked_products=len(product_ids),
        suggestions=suggestions[:limit],
        notes=notes,
    )

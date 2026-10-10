"""Best-supplier rule shared by the dataset generator and the dashboard.

For an order of `product_id` at `store_id` on `order_date`:

1. days_coverage = current_stock / predicted_daily_demand
2. Keep suppliers with lead_time_days <= days_coverage. If none qualify, take the supplier with the
   lowest lead_time_days and flag the order as urgent.
3. qty = max(min_order_qty, ceil(predicted_daily_demand * (lead_time_days + cycle_days))),
   cycle_days = min(7, shelf_life_days)
4. sellable = predicted_daily_demand * shelf_life_days; waste = max(0, qty - sellable)
5. expected_profit = (retail_price - unit_cost) * (qty - waste) - unit_cost * waste
   score = expected_profit * reliability_score
6. Highest score wins; ties -> lowest lead_time_days, then lowest supplier_id.

Prices are fixed over the whole date range, so `store_id` and `order_date` do not change the
result today; they are part of the signature so the dashboard can pass them through unchanged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from common import CYCLE_DAYS, OUT_DIR


@dataclass(frozen=True)
class Offer:
    supplier_id: str
    unit_cost: float
    min_order_qty: int
    lead_time_days: int
    reliability_score: float


@dataclass(frozen=True)
class Decision:
    supplier_id: str
    quantity: int
    lead_time_days: int
    unit_cost: float
    is_urgent: bool
    score: float


@dataclass(frozen=True)
class ProductInfo:
    retail_price: float
    shelf_life_days: int
    offers: tuple[Offer, ...]


@lru_cache(maxsize=4)
def load_catalog(data_dir: str | Path = OUT_DIR) -> dict[str, ProductInfo]:
    """Read products, suppliers and supplier_products from a v2 folder into a lookup."""
    data_dir = Path(data_dir)
    products = pd.read_csv(data_dir / "products.csv")
    suppliers = pd.read_csv(data_dir / "suppliers.csv").set_index("supplier_id")["reliability_score"]
    offers = pd.read_csv(data_dir / "supplier_products.csv")
    catalog = {}
    for prod in products.itertuples(index=False):
        rows = offers[offers.product_id == prod.product_id]
        catalog[prod.product_id] = ProductInfo(
            retail_price=float(prod.retail_price),
            shelf_life_days=int(prod.shelf_life_days),
            offers=tuple(Offer(r.supplier_id, float(r.unit_cost), int(r.min_order_qty), int(r.lead_time_days),
                               float(suppliers[r.supplier_id]))
                         for r in rows.itertuples(index=False)),
        )
    return catalog


def cycle_days_for(shelf_life_days: int) -> int:
    return min(CYCLE_DAYS, shelf_life_days)


def order_quantity(offer: Offer, predicted_daily_demand: float, shelf_life_days: int) -> int:
    need = predicted_daily_demand * (offer.lead_time_days + cycle_days_for(shelf_life_days))
    return max(offer.min_order_qty, math.ceil(need - 1e-9))


def decide(info: ProductInfo, predicted_daily_demand: float, current_stock: float) -> Decision:
    """Apply the rule to one product's offers and return the full decision."""
    demand = max(predicted_daily_demand, 1e-9)
    days_coverage = current_stock / demand
    eligible = [o for o in info.offers if o.lead_time_days <= days_coverage]
    urgent = not eligible
    if urgent:
        fastest = min(o.lead_time_days for o in info.offers)
        eligible = [o for o in info.offers if o.lead_time_days == fastest]

    sellable = demand * info.shelf_life_days
    best = None
    for o in eligible:
        qty = order_quantity(o, demand, info.shelf_life_days)
        waste = max(0.0, qty - sellable)
        profit = (info.retail_price - o.unit_cost) * (qty - waste) - o.unit_cost * waste
        score = profit * o.reliability_score
        key = (-score, o.lead_time_days, o.supplier_id)
        if best is None or key < best[0]:
            best = (key, Decision(o.supplier_id, qty, o.lead_time_days, o.unit_cost, urgent, score))
    return best[1]


def select_supplier(store_id: str, product_id: str, order_date, predicted_daily_demand: float,
                    current_stock: float, data_dir: str | Path = OUT_DIR) -> str:
    """Return the supplier_id to order `product_id` from (see module docstring for the rule)."""
    return decide(load_catalog(str(data_dir))[product_id], predicted_daily_demand, current_stock).supplier_id


if __name__ == "__main__":
    import sys
    store, product, date, demand, stock = sys.argv[1:6]
    print(select_supplier(store, product, date, float(demand), float(stock)))

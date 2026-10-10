from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np

from stockcopilot.data.repository import OpenOrder
from stockcopilot.domain.inventory import ProductPosition, ReorderPolicy, StockLot, project_inventory, store_position


def test_reconstructed_stock_matches_lots_left_on_last_day(repo):
    _, last = repo.data_bounds()
    for store_id in ("S001", "S016"):
        position = store_position(repo, store_id, last)
        remaining: dict[str, int] = defaultdict(int)
        for lot in repo.lots(store_id, last):
            if lot.expiration_date > last:
                remaining[lot.product_id] += lot.quantity_remaining
        for product_id, pos in position.products.items():
            assert pos.on_hand == remaining[product_id], (store_id, product_id)
            assert sum(lot.quantity for lot in pos.lots) == pos.on_hand


def _position(lots, in_transit=()):
    return ProductPosition(
        product_id="P",
        on_hand=int(sum(q for _, q in lots)),
        lots=[StockLot(d, q) for d, q in lots],
        in_transit=list(in_transit),
        last_supplier_id=None,
    )


def test_projection_sells_first_expiring_lot_and_counts_waste():
    start = date(2026, 1, 1)
    position = _position([(start + timedelta(days=2), 5.0), (start + timedelta(days=10), 5.0)])
    result = project_inventory(position, start, np.array([3.0, 3.0, 3.0, 3.0]), shelf_life_days=7)
    # FEFO: the early lot is sold out (3 + 2) before it expires; the late lot covers the rest until it runs out.
    assert result.units_sold.tolist() == [3.0, 3.0, 3.0, 1.0]
    assert result.total_waste == 0.0
    assert result.total_lost == 2.0


def test_projection_waste_when_demand_is_too_low():
    start = date(2026, 1, 1)
    position = _position([(start + timedelta(days=1), 10.0)])
    result = project_inventory(position, start, np.array([2.0, 2.0, 2.0]), shelf_life_days=7)
    assert result.total_sold == 2.0
    assert result.total_waste == 8.0


def test_reorder_policy_and_late_orders_arrive():
    start = date(2026, 1, 1)
    late = OpenOrder("PO1", "P", "SUP01", start - timedelta(days=5), 4, 1.0, start - timedelta(days=1))
    position = _position([(start + timedelta(days=30), 2.0)], in_transit=[late])
    no_policy = project_inventory(position, start, np.full(6, 2.0), shelf_life_days=30)
    with_policy = project_inventory(
        position,
        start,
        np.full(6, 2.0),
        shelf_life_days=30,
        policy=ReorderPolicy(reorder_point=2, quantity=10, lead_time_days=1),
    )
    assert no_policy.total_sold == 6.0  # 2 on hand + 4 late units arriving on day 1
    assert with_policy.total_lost < no_policy.total_lost

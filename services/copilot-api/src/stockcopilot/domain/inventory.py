"""Inventory position and day-by-day projection (deterministic).

The dataset's replenishment simulation consumes stock first-expiring-first-out (FEFO) and removes
lots on the morning of their expiration date, writing what was left to `quantity_remaining`.
That lets us rebuild the exact stock on hand at the end of any day:

    on_hand(as_of) = received(<= as_of) - sold(<= as_of) - expired_waste(expiration <= as_of)

Because FEFO always sells the oldest lot first, the units still on the shelf belong to the
latest-expiring lots, which is how they are assigned back to lots below.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from ..data.repository import DataRepository, Lot, OpenOrder


@dataclass
class StockLot:
    expiration_date: date
    quantity: float


@dataclass
class ProductPosition:
    product_id: str
    on_hand: int
    lots: list[StockLot]
    in_transit: list[OpenOrder]
    last_supplier_id: str | None

    @property
    def in_transit_units(self) -> int:
        return sum(o.quantity for o in self.in_transit)


@dataclass
class StorePosition:
    store_id: str
    as_of: date
    products: dict[str, ProductPosition] = field(default_factory=dict)


def _assign_to_lots(on_hand: int, live_lots: list[Lot]) -> list[StockLot]:
    """Put the remaining units in the latest-expiring lots (FEFO sells the earliest first)."""
    remaining = on_hand
    assigned: list[StockLot] = []
    for lot in sorted(live_lots, key=lambda x: (x.expiration_date, x.received_date), reverse=True):
        if remaining <= 0:
            break
        take = min(remaining, lot.quantity_received)
        assigned.append(StockLot(expiration_date=lot.expiration_date, quantity=float(take)))
        remaining -= take
    assigned.sort(key=lambda x: x.expiration_date)
    return assigned


def store_position(repo: DataRepository, store_id: str, as_of: date) -> StorePosition:
    sold = repo.cumulative_units(store_id, as_of)
    received: dict[str, int] = defaultdict(int)
    expired: dict[str, int] = defaultdict(int)
    live: dict[str, list[Lot]] = defaultdict(list)
    latest_lot: dict[str, Lot] = {}
    for lot in repo.lots(store_id, as_of):
        received[lot.product_id] += lot.quantity_received
        if lot.expiration_date <= as_of:
            expired[lot.product_id] += lot.quantity_remaining
        else:
            live[lot.product_id].append(lot)
        current = latest_lot.get(lot.product_id)
        if current is None or lot.received_date >= current.received_date:
            latest_lot[lot.product_id] = lot

    transit: dict[str, list[OpenOrder]] = defaultdict(list)
    for order in repo.open_orders(store_id, as_of):
        transit[order.product_id].append(order)

    position = StorePosition(store_id=store_id, as_of=as_of)
    for product_id, units_sold in sold.items():
        on_hand = max(0, received[product_id] - units_sold - expired[product_id])
        last = latest_lot.get(product_id)
        position.products[product_id] = ProductPosition(
            product_id=product_id,
            on_hand=on_hand,
            lots=_assign_to_lots(on_hand, live[product_id]),
            in_transit=sorted(transit[product_id], key=lambda o: o.expected_delivery_date),
            last_supplier_id=last.supplier_id if last else None,
        )
    return position


@dataclass(frozen=True)
class ReorderPolicy:
    """The store's standing replenishment rule (same as the dataset generator).

    Each evening, if stock on hand + on order <= reorder_point, order `quantity` units that
    arrive `lead_time_days` later.
    """

    reorder_point: float
    quantity: int
    lead_time_days: int


@dataclass
class ProjectionResult:
    units_sold: np.ndarray
    lost_units: np.ndarray
    waste_units: np.ndarray
    end_stock: np.ndarray

    @property
    def total_sold(self) -> float:
        return float(self.units_sold.sum())

    @property
    def total_lost(self) -> float:
        return float(self.lost_units.sum())

    @property
    def total_waste(self) -> float:
        return float(self.waste_units.sum())

    def first_stockout_index(self) -> int | None:
        hits = np.nonzero(self.lost_units > 1e-9)[0]
        return int(hits[0]) if hits.size else None


def project_inventory(
    position: ProductPosition,
    start: date,
    demand: np.ndarray,
    shelf_life_days: int,
    include_in_transit: bool = True,
    policy: ReorderPolicy | None = None,
) -> ProjectionResult:
    """Simulate FEFO stock day by day from `start` for `len(demand)` days.

    Morning: expected deliveries arrive as new lots, expired lots become waste.
    Day: sales consume the earliest-expiring stock first, capped by what is on the shelf.
    Evening: with a `policy`, place an order when stock + on order falls to the reorder point.
    Without one, no new orders are placed: "what happens if we do nothing".
    """
    horizon = len(demand)
    lots = [StockLot(lot.expiration_date, lot.quantity) for lot in position.lots]
    arrivals: dict[int, float] = defaultdict(float)
    if include_in_transit:
        for order in position.in_transit:
            # Late orders (expected date already passed) are assumed to arrive tomorrow.
            offset = max(0, (order.expected_delivery_date - start).days)
            if offset < horizon:
                arrivals[offset] += order.quantity

    sold = np.zeros(horizon)
    lost = np.zeros(horizon)
    waste = np.zeros(horizon)
    end_stock = np.zeros(horizon)
    for t in range(horizon):
        today = start + timedelta(days=t)
        if arrivals.get(t):
            lots.append(StockLot(today + timedelta(days=shelf_life_days), arrivals[t]))
            lots.sort(key=lambda x: x.expiration_date)
        kept: list[StockLot] = []
        for lot in lots:
            if lot.expiration_date <= today:
                waste[t] += lot.quantity
            else:
                kept.append(lot)
        lots = kept
        need = float(max(demand[t], 0.0))
        for lot in lots:
            if need <= 0:
                break
            take = min(need, lot.quantity)
            lot.quantity -= take
            need -= take
            sold[t] += take
        lots = [lot for lot in lots if lot.quantity > 1e-9]
        lost[t] = need
        end_stock[t] = sum(lot.quantity for lot in lots)
        if policy is not None:
            on_order = sum(qty for day, qty in arrivals.items() if day > t)
            if end_stock[t] + on_order <= policy.reorder_point:
                arrivals[t + max(1, policy.lead_time_days)] += policy.quantity
    return ProjectionResult(units_sold=sold, lost_units=lost, waste_units=waste, end_stock=end_stock)

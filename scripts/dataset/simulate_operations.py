"""Simulate daily store operations and write sales.csv, inventory_lots.csv and purchase_orders.csv.

Per (store, product), day by day:
  morning   receive orders whose actual_delivery_date is today (one lot each); drop expired lots
  day       units_sold = min(realized demand, stock on hand), consumed first-expiring-first-out
  evening   if stock on hand + stock on order <= reorder point, order via supplier_selection.decide()

reorder_point = predicted_daily_demand x (median supplier lead time + safety_days)
predicted_daily_demand = mean expected (noise-free) demand over the next 7 days, rounded to 4 decimals
"""
import bisect
import math
import time

import numpy as np
import pandas as pd

import build_demand
from catalog_data import PRODUCTS
from common import (DATES, MIN_PREDICTED_DEMAND_TO_ORDER, PERISHABLE_MAX_SHELF_LIFE, SAFETY_DAYS_PERISHABLE,
                    SAFETY_DAYS_STABLE, read_csv, rng, write_csv)
from supplier_selection import cycle_days_for, decide, load_catalog


def forward_mean(expected: np.ndarray, horizon: int = 7) -> np.ndarray:
    n = expected.shape[0]
    cs = np.concatenate([np.zeros((1,) + expected.shape[1:]), np.cumsum(expected, axis=0)])
    t = np.arange(n)
    hi = np.minimum(t + 1 + horizon, n)
    lo = np.minimum(t + 1, n - 1)
    cnt = np.maximum(hi - lo, 1)
    out = (cs[hi] - cs[lo]) / cnt[:, None, None]
    out[-1] = expected[-1]
    return np.round(out, 4)


def expiration_offset(shelf: int, r: np.random.Generator) -> int:
    if shelf <= 3:
        return shelf
    if shelf <= PERISHABLE_MAX_SHELF_LIFE:
        return shelf + int(r.integers(-1, 2))
    return max(1, shelf + int(round(shelf * r.uniform(-0.03, 0.03))))


def simulate_pair(s, p, store_id, product_id, info, demand, pred, r, lots_out, orders_out):
    n = len(demand)
    shelf = info.shelf_life_days
    lead_times = sorted(o.lead_time_days for o in info.offers)
    median_lt = lead_times[len(lead_times) // 2]
    safety = SAFETY_DAYS_PERISHABLE if shelf <= PERISHABLE_MAX_SHELF_LIFE else SAFETY_DAYS_STABLE
    reliability = {o.supplier_id: o.reliability_score for o in info.offers}
    sold = np.zeros(n, dtype=np.int32)

    lots = []          # sorted by expiry: [exp_idx, qty, lot_record_index]
    arrivals = {}      # day index -> list of (qty, supplier_id)
    on_order = 0

    def receive(day, qty, sup):
        exp = day + expiration_offset(shelf, r)
        rec = [store_id, product_id, sup, day, qty, exp, 0]
        lots_out.append(rec)
        bisect.insort(lots, [exp, qty, rec], key=lambda x: x[0])

    first = float(pred[0])
    if first >= MIN_PREDICTED_DEMAND_TO_ORDER:
        qty0 = math.ceil(first * (median_lt + safety + cycle_days_for(shelf)))
        receive(0, qty0, decide(info, first, qty0).supplier_id)

    for t in range(n):
        for qty, sup in arrivals.pop(t, ()):
            on_order -= qty
            receive(t, qty, sup)
        while lots and lots[0][0] <= t:  # expired: remaining units are waste
            _, q, rec = lots.pop(0)
            rec[6] = q

        want = int(demand[t])
        i = 0
        while want and i < len(lots):
            take = min(want, lots[i][1])
            lots[i][1] -= take
            want -= take
            sold[t] += take
            i += 1
        while lots and lots[0][1] == 0:  # depleted lots leave the shelf (remaining stays 0)
            lots.pop(0)
        on_hand = sum(l[1] for l in lots)

        d = float(pred[t])
        if d >= MIN_PREDICTED_DEMAND_TO_ORDER and on_hand + on_order <= d * (median_lt + safety):
            dec = decide(info, d, on_hand)
            expected = t + dec.lead_time_days
            actual = expected + (int(r.integers(1, 4)) if r.random() > reliability[dec.supplier_id] else 0)
            orders_out.append([store_id, product_id, dec.supplier_id, t, dec.quantity, dec.unit_cost,
                               expected, actual if actual < n else None, int(dec.is_urgent), d, on_hand])
            on_order += dec.quantity
            if actual < n:
                arrivals.setdefault(actual, []).append((dec.quantity, dec.supplier_id))

    for exp, q, rec in lots:  # still on the shelf at the end of the range
        rec[6] = q
    return sold


def main() -> None:
    print("Simulating demand and operations...")
    t0 = time.time()
    stores = read_csv("stores")
    products = read_csv("products")
    weather = read_csv("weather")
    catalog = load_catalog()
    model = build_demand.build(stores, weather)
    pred = forward_mean(model.expected)
    print(f"  demand model ready ({time.time() - t0:.0f}s); expected units/day/store = "
          f"{model.expected.sum() / len(DATES) / len(stores):.0f}")

    n_d, n_s, n_p = model.realized.shape
    sold = np.zeros_like(model.realized)
    lots_out, orders_out = [], []
    for s, store_id in enumerate(stores.store_id):
        for p, product_id in enumerate(products.product_id):
            r = rng(8, s, p)
            sold[:, s, p] = simulate_pair(s, p, store_id, product_id, catalog[product_id],
                                          model.realized[:, s, p], pred[:, s, p], r, lots_out, orders_out)
    print(f"  simulation done ({time.time() - t0:.0f}s)")

    day = DATES.strftime("%Y-%m-%d").to_numpy()
    fmt = lambda idx: day[int(idx)]
    fmt_any = lambda idx: (DATES[0] + pd.Timedelta(days=int(idx))).strftime("%Y-%m-%d")

    # sales.csv (full grid: date x store x product)
    price = products.retail_price.to_numpy()
    units = sold.reshape(-1)
    sales = pd.DataFrame({
        "date": np.repeat(day, n_s * n_p),
        "store_id": np.tile(np.repeat(stores.store_id.to_numpy(), n_p), n_d),
        "product_id": np.tile(products.product_id.to_numpy(), n_d * n_s),
        "units_sold": units,
        "revenue": np.round(units * np.tile(price, n_d * n_s), 2),
    })
    write_csv(sales, "sales", float_format="%.2f")

    # purchase_orders.csv
    po = pd.DataFrame(orders_out, columns=["store_id", "product_id", "supplier_id", "order_idx", "quantity",
                                           "unit_cost", "expected_idx", "actual_idx", "is_urgent",
                                           "predicted_daily_demand", "stock_at_order"])
    po = po.sort_values(["order_idx", "store_id", "product_id"], kind="stable").reset_index(drop=True)
    po.insert(0, "order_id", [f"PO{i + 1:07d}" for i in range(len(po))])
    po["order_date"] = day[po.order_idx]
    po["expected_delivery_date"] = [fmt_any(i) for i in po.expected_idx]
    po["actual_delivery_date"] = [fmt(i) if pd.notna(i) else None for i in po.actual_idx]
    po = po[["order_id", "store_id", "product_id", "supplier_id", "order_date", "quantity", "unit_cost",
             "expected_delivery_date", "actual_delivery_date", "is_urgent", "predicted_daily_demand",
             "stock_at_order"]]
    write_csv(po, "purchase_orders")

    # inventory_lots.csv
    lots = pd.DataFrame(lots_out, columns=["store_id", "product_id", "supplier_id", "recv_idx",
                                           "quantity_received", "exp_idx", "quantity_remaining"])
    lots = lots.sort_values(["recv_idx", "store_id", "product_id"], kind="stable").reset_index(drop=True)
    lots.insert(0, "lot_id", [f"L{i + 1:07d}" for i in range(len(lots))])
    lots["received_date"] = day[lots.recv_idx]
    lots["expiration_date"] = [fmt_any(i) for i in lots.exp_idx]
    lots = lots[["lot_id", "store_id", "product_id", "supplier_id", "received_date", "quantity_received",
                 "expiration_date", "quantity_remaining"]]
    write_csv(lots, "inventory_lots")

    cal = read_csv("calendar")
    cal["demand_multiplier_hint"] = np.round(model.calendar_hint, 3)
    write_csv(cal, "calendar")

    demand_total = model.realized.sum()
    expired = lots.loc[pd.to_datetime(lots.expiration_date) <= DATES[-1], "quantity_remaining"].sum()
    print(f"  lost sales (stockouts) = {1 - units.sum() / demand_total:.2%} of demand; "
          f"expired = {expired / lots.quantity_received.sum():.2%} of received units; "
          f"urgent orders = {po.is_urgent.mean():.2%}; total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

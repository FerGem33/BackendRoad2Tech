"""Build the DynamoDB import files for the `road2tech-stores` table from the v2 dataset.

Output: DynamoDB JSON lines, gzip, ready for *DynamoDB Import from S3* (see docs/DYNAMO.md §6.1).

    python3 scripts/dynamo/build_import.py [--out api/dist/dynamo-import]

Forecast: the trained model is not published yet, so the 14-day forecast is a seasonal-naive baseline per
store x product (last 28 days level x weekday factor of the last 12 weeks x payday factor of the store), with
p80/p90 from the residual standard deviation. `model_version` says so.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import math
import os
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
V2 = REPO / "public" / "softtek" / "dataset" / "dataset-hackathon-road2tech" / "v2"
AS_OF = pd.Timestamp("2026-09-30")
HORIZON = 14
SALES_WINDOW_DAYS = 90
CYCLE_DAYS = 7
SAFETY_DAYS_PERISHABLE = 1
SAFETY_DAYS_STABLE = 3
PERISHABLE_MAX_SHELF_LIFE = 30
MODEL_VERSION = "seasonal-naive-2026-09-30"
GENERATED_AT = "2026-09-30T23:30:00Z"
DEMO_USER = {"id": "u_01", "name": "Demo", "email": "demo@stockcopilot.com", "password": "demo1234"}


# --- DynamoDB JSON ---------------------------------------------------------------------------------------------

def av(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return {"NULL": True}
    if isinstance(v, (bool, np.bool_)):
        return {"BOOL": bool(v)}
    if isinstance(v, (int, np.integer)):
        return {"N": str(int(v))}
    if isinstance(v, (float, np.floating, Decimal)):
        return {"N": format(Decimal(str(round(float(v), 6))).normalize(), "f")}
    if isinstance(v, str):
        return {"S": v}
    if isinstance(v, (list, tuple)):
        return {"L": [av(x) for x in v]}
    if isinstance(v, dict):
        return {"M": {k: av(x) for k, x in v.items()}}
    raise TypeError(f"unsupported {type(v)}: {v!r}")


class Writer:
    def __init__(self, out: Path, per_file: int = 200_000):
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.json.gz"):
            old.unlink()
        self.out, self.per_file, self.n, self.fh, self.part = out, per_file, 0, None, 0
        self.counts: dict[str, int] = {}

    def put(self, item: dict) -> None:
        if self.fh is None or self.n % self.per_file == 0:
            if self.fh:
                self.fh.close()
            self.part += 1
            self.fh = gzip.open(self.out / f"items-{self.part:03d}.json.gz", "wt", encoding="utf-8")
        clean = {k: v for k, v in item.items() if v is not None}
        self.fh.write(json.dumps({"Item": {k: av(v) for k, v in clean.items()}}, ensure_ascii=False) + "\n")
        self.n += 1
        self.counts[item["entity_type"]] = self.counts.get(item["entity_type"], 0) + 1

    def close(self) -> None:
        if self.fh:
            self.fh.close()


def r2(x) -> float:
    return round(float(x), 2)


def ds(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def hash_password(password: str, iterations: int = 210_000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, 32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"pbkdf2_sha256${iterations}${b64(salt)}${b64(dk)}"


def weather_label(tmax: float, tmin: float, precip: float) -> str:
    if precip >= 5.0:
        return "rainy"
    if tmax >= 30.0:
        return "hot"
    if tmax < 17.0 or tmin <= 1.0:
        return "cold"
    return "mild"


# --- supplier_selection.decide() (same rule as scripts/dataset/supplier_selection.py) ---------------------------

def decide(offers: list[dict], retail_price: float, shelf: int, demand: float, stock: float) -> dict:
    demand = max(demand, 1e-9)
    coverage = stock / demand
    eligible = [o for o in offers if o["lead_time_days"] <= coverage]
    urgent = not eligible
    if urgent:
        fastest = min(o["lead_time_days"] for o in offers)
        eligible = [o for o in offers if o["lead_time_days"] == fastest]
    sellable = demand * shelf
    best = None
    for o in eligible:
        qty = max(o["min_order_qty"], math.ceil(demand * (o["lead_time_days"] + min(CYCLE_DAYS, shelf)) - 1e-9))
        waste = max(0.0, qty - sellable)
        profit = (retail_price - o["unit_cost"]) * (qty - waste) - o["unit_cost"] * waste
        key = (-profit * o["reliability_score"], o["lead_time_days"], o["supplier_id"])
        if best is None or key < best[0]:
            best = (key, {**o, "quantity": qty, "is_urgent": urgent})
    return best[1]


# --- build -----------------------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "api" / "dist" / "dynamo-import"))
    out = Path(ap.parse_args().out)
    w = Writer(out)

    read = lambda name, **kw: pd.read_csv(V2 / f"{name}.csv", keep_default_na=False, na_values=[""], **kw)
    zones, categories, stores = read("zones"), read("categories"), read("stores")
    products, suppliers, offers = read("products"), read("suppliers"), read("supplier_products")
    calendar, weather = read("calendar"), read("weather")

    cat_name = dict(zip(categories.category_id, categories.category_name))
    sup = suppliers.set_index("supplier_id")
    prod = products.set_index("product_id")

    for z in zones.itertuples(index=False):
        w.put({"PK": "CATALOG", "SK": f"ZONE#{z.zone_type}", "entity_type": "ZONE", "zone_type": z.zone_type,
               "description": z.description, "peak_hours": z.peak_hours.split("|"),
               "weekend_multiplier": float(z.weekend_multiplier), "notes": z.notes})
    for c in categories.itertuples(index=False):
        w.put({"PK": "CATALOG", "SK": f"CATEGORY#{c.category_id}", "entity_type": "CATEGORY",
               "category_id": c.category_id, "category_name": c.category_name,
               "is_perishable": bool(c.is_perishable), "typical_shelf_life_days": int(c.typical_shelf_life_days),
               "requires_refrigeration": bool(c.requires_refrigeration)})
    store_meta = {}
    for s in stores.itertuples(index=False):
        sec = s.secondary_zone_type if isinstance(s.secondary_zone_type, str) else None
        item = {"PK": f"STORE#{s.store_id}", "SK": "#META", "entity_type": "STORE",
                "GSI1PK": "STORE", "GSI1SK": f"ZONE#{s.zone_type}#{s.store_id}",
                "store_id": s.store_id, "store_name": s.store_name, "colonia": s.colonia,
                "latitude": float(s.latitude), "longitude": float(s.longitude), "zone_type": s.zone_type,
                "secondary_zone_type": sec, "size_category": s.size_category, "opening_hours": s.opening_hours,
                "is_24h": bool(s.is_24h), "opened_date": s.opened_date}
        store_meta[s.store_id] = item
        w.put(item)

    # offers per product, with supplier and product data denormalized
    offers_by_product: dict[str, list[dict]] = {}
    for o in offers.itertuples(index=False):
        p, sp = prod.loc[o.product_id], sup.loc[o.supplier_id]
        rec = {"supplier_id": o.supplier_id, "unit_cost": float(o.unit_cost), "min_order_qty": int(o.min_order_qty),
               "lead_time_days": int(o.lead_time_days), "reliability_score": float(sp.reliability_score)}
        offers_by_product.setdefault(o.product_id, []).append(rec)
        w.put({"PK": f"PRODUCT#{o.product_id}", "SK": f"OFFER#{o.supplier_id}", "entity_type": "OFFER",
               "GSI1PK": f"SUPPLIER#{o.supplier_id}", "GSI1SK": f"OFFER#{o.product_id}",
               "supplier_id": o.supplier_id, "product_id": o.product_id,
               "unit_cost": float(o.unit_cost), "min_order_qty": int(o.min_order_qty),
               "lead_time_days": int(o.lead_time_days), "delivery_days": o.delivery_days.split("|"),
               "supplier_name": sp.supplier_name, "supplier_email": sp.email,
               "reliability_score": float(sp.reliability_score),
               "product_name": p.product_name, "category_id": p.category_id, "retail_price": float(p.retail_price),
               "margin_pct": round(1 - float(o.unit_cost) / float(p.retail_price), 4)})

    offers_count = offers.groupby("supplier_id").size().to_dict()
    for s in suppliers.itertuples(index=False):
        w.put({"PK": f"SUPPLIER#{s.supplier_id}", "SK": "#META", "entity_type": "SUPPLIER",
               "GSI1PK": "SUPPLIER", "GSI1SK": s.supplier_id, "supplier_id": s.supplier_id,
               "supplier_name": s.supplier_name, "email": s.email,
               "categories_served": s.categories_served.split("|"), "city": s.city,
               "reliability_score": float(s.reliability_score), "offers_count": int(offers_count.get(s.supplier_id, 0))})

    for p in products.itertuples(index=False):
        d = decide(offers_by_product[p.product_id], float(p.retail_price), int(p.shelf_life_days),
                   float(p.base_daily_demand), float("inf"))
        w.put({"PK": f"PRODUCT#{p.product_id}", "SK": "#META", "entity_type": "PRODUCT",
               "GSI1PK": "PRODUCT", "GSI1SK": f"CATEGORY#{p.category_id}#{p.product_id}",
               "product_id": p.product_id, "product_name": p.product_name, "category_id": p.category_id,
               "category_name": cat_name[p.category_id], "brand": p.brand, "unit": p.unit,
               "shelf_life_days": int(p.shelf_life_days), "retail_price": float(p.retail_price),
               "is_seasonal": bool(p.is_seasonal), "weather_sensitivity": p.weather_sensitivity,
               "base_daily_demand": float(p.base_daily_demand),
               "default_supplier": {"supplier_id": d["supplier_id"], "unit_cost": d["unit_cost"],
                                    "min_order_qty": d["min_order_qty"]}})

    # calendar + weather (history) and forecast days (climatology of the same day in previous years)
    cw = calendar.merge(weather, on="date")
    def day_item(r: dict, is_forecast: bool) -> dict:
        names = r["event_name"].split("|") if isinstance(r.get("event_name"), str) else []
        scopes = r["event_scope"].split("|") if isinstance(r.get("event_scope"), str) else []
        return {"PK": "CALENDAR", "SK": f"DATE#{r['date']}", "entity_type": "DAY", "date": r["date"],
                "day_of_week": r["day_of_week"], "is_weekend": bool(r["is_weekend"]), "is_holiday": bool(r["is_holiday"]),
                "holiday_name": r["holiday_name"] if isinstance(r.get("holiday_name"), str) else None,
                "season_name": r["season_name"],
                "events": [{"event_name": n, "event_scope": scopes[i] if i < len(scopes) else None} for i, n in enumerate(names)],
                "is_payday": bool(r["is_payday"]), "is_school_vacation": bool(r["is_school_vacation"]),
                "is_college_vacation": bool(r["is_college_vacation"]),
                "demand_multiplier_hint": None if is_forecast else float(r["demand_multiplier_hint"]),
                "temp_max_c": float(r["temp_max_c"]), "temp_min_c": float(r["temp_min_c"]),
                "precipitation_mm": float(r["precipitation_mm"]), "weather_label": r["weather_label"],
                "is_forecast": is_forecast}
    for r in cw.to_dict("records"):
        w.put(day_item(r, False))
    cw["md"] = cw.date.str[5:]
    fdates = pd.date_range(AS_OF + pd.Timedelta(days=1), periods=HORIZON, freq="D")
    for d in fdates:
        prev = cw[cw.md == d.strftime("%m-%d")]
        tmax, tmin, rain = prev.temp_max_c.mean(), prev.temp_min_c.mean(), prev.precipitation_mm.mean()
        month_end = (d + pd.Timedelta(days=1)).month != d.month
        season = prev.season_name.iloc[-1]
        w.put(day_item({"date": ds(d), "day_of_week": d.day_name(), "is_weekend": d.dayofweek >= 5, "is_holiday": False,
                        "holiday_name": None, "season_name": season, "event_name": None, "event_scope": None,
                        "is_payday": d.day == 15 or month_end, "is_school_vacation": False, "is_college_vacation": False,
                        "temp_max_c": round(tmax, 1), "temp_min_c": round(tmin, 1), "precipitation_mm": round(rain, 1),
                        "weather_label": weather_label(tmax, tmin, rain)}, True))
    payday = set(calendar.loc[calendar.is_payday == 1, "date"])

    # sales: 90-day window (lines + store-day summaries) and the forecast baseline
    print("reading sales.csv ...")
    sales = read("sales", dtype={"store_id": str, "product_id": str})
    sales["date"] = pd.to_datetime(sales["date"])
    window_start = AS_OF - pd.Timedelta(days=SALES_WINDOW_DAYS - 1)
    recent = sales[sales.date >= window_start].copy()
    recent["category_id"] = recent.product_id.map(prod.category_id)
    for r in recent.itertuples(index=False):
        d = ds(r.date)
        w.put({"PK": f"STORE#{r.store_id}#SALES", "SK": f"LINE#{d}#{r.product_id}", "entity_type": "SALE",
               "GSI2PK": f"STORE#{r.store_id}#PRODUCT#{r.product_id}", "GSI2SK": f"SALE#{d}",
               "date": d, "store_id": r.store_id, "product_id": r.product_id,
               "units_sold": int(r.units_sold), "revenue": r2(r.revenue)})
    for (store_id, date), g in recent.groupby(["store_id", "date"]):
        w.put({"PK": f"STORE#{store_id}#SALES", "SK": f"DAY#{ds(date)}", "entity_type": "SALE_DAY",
               "date": ds(date), "store_id": store_id, "units_sold": int(g.units_sold.sum()),
               "revenue": r2(g.revenue.sum()),
               "revenue_by_category": {k: r2(v) for k, v in g.groupby("category_id").revenue.sum().items()},
               "zero_sale_products": int((g.units_sold == 0).sum())})

    # ticket day aggregates (from ticket_lines.csv) for the ticket KPIs of ENDPOINTS.md §3.2/§3.4
    # (average_ticket, items_per_ticket). One item per store x day in the same 90-day window.
    print("reading ticket_lines.csv ...")
    tickets = read("ticket_lines", dtype={"store_id": str})
    tickets["date"] = pd.to_datetime(tickets["date"])
    tickets = tickets[tickets.date >= window_start]
    for (store_id, date), g in tickets.groupby(["store_id", "date"], sort=False):
        w.put({"PK": f"STORE#{store_id}#SALES", "SK": f"TICKET#{ds(date)}", "entity_type": "TICKET_DAY",
               "date": ds(date), "store_id": store_id, "tickets": int(g.ticket_id.nunique()),
               "units": int(g.quantity.sum()), "net_amount": r2(g.net_amount.sum())})
    del tickets

    hist = sales[sales.date > AS_OF - pd.Timedelta(days=84)].copy()
    del sales
    hist["dow"] = hist.date.dt.dayofweek
    hist["payday"] = hist.date.dt.strftime("%Y-%m-%d").isin(payday)
    last28 = hist[hist.date > AS_OF - pd.Timedelta(days=28)]
    level = last28.groupby(["store_id", "product_id"]).units_sold.mean()
    sd = last28.groupby(["store_id", "product_id"]).units_sold.std().fillna(0)
    pair_mean = hist.groupby(["store_id", "product_id"]).units_sold.mean()
    dow_mean = hist.groupby(["store_id", "product_id", "dow"]).units_sold.mean()
    store_day = hist.groupby(["store_id", "date", "payday"]).units_sold.sum().reset_index()
    pay_factor = (store_day[store_day.payday].groupby("store_id").units_sold.mean()
                  / store_day[~store_day.payday].groupby("store_id").units_sold.mean()).fillna(1.0)

    forecast: dict[tuple[str, str], list[float]] = {}
    for (store_id, product_id), lv in level.items():
        pm = pair_mean[(store_id, product_id)]
        sigma = float(sd[(store_id, product_id)])
        series = []
        for d in fdates:
            wf = float(dow_mean.get((store_id, product_id, d.dayofweek), pm) / pm) if pm > 0 else 1.0
            is_pay = d.day == 15 or (d + pd.Timedelta(days=1)).month != d.month
            pf = float(pay_factor.get(store_id, 1.0)) if is_pay else 1.0
            p50 = max(0.0, lv * wf * pf)
            drivers = []
            if wf >= 1.1:
                drivers.append({"type": "DEMAND", "name": "weekday", "detail": d.day_name()})
            if is_pay and pf > 1.02:
                drivers.append({"type": "EVENT", "name": "Payday", "detail": None})
            uplift = round((p50 / lv - 1) * 100, 1) if lv > 0 else 0.0
            series.append(p50)
            dd = ds(d)
            w.put({"PK": f"STORE#{store_id}#FCST", "SK": f"{dd}#{product_id}", "entity_type": "FORECAST",
                   "GSI2PK": f"STORE#{store_id}#PRODUCT#{product_id}", "GSI2SK": f"FCST#{dd}",
                   "date": dd, "store_id": store_id, "product_id": product_id,
                   "category_id": prod.loc[product_id].category_id,
                   "predicted_units": round(p50, 4), "p50": round(p50, 4),
                   "p80": round(p50 + 0.8416 * sigma, 4), "p90": round(p50 + 1.2816 * sigma, 4),
                   "baseline_units": round(float(lv), 4), "uplift_pct": uplift, "drivers": drivers,
                   "model_version": MODEL_VERSION, "generated_at": GENERATED_AT})
        forecast[(store_id, product_id)] = series

    # lots (active at AS_OF), purchase orders (in transit + last 90 days), stock
    lots = read("inventory_lots", dtype={"store_id": str, "product_id": str})
    lots = lots[(lots.expiration_date > ds(AS_OF)) & (lots.quantity_remaining > 0)]
    offer_cost = {(o.supplier_id, o.product_id): float(o.unit_cost) for o in offers.itertuples(index=False)}
    for l in lots.itertuples(index=False):
        cost = offer_cost[(l.supplier_id, l.product_id)]
        w.put({"PK": f"STORE#{l.store_id}", "SK": f"LOT#{l.expiration_date}#{l.product_id}#{l.lot_id}",
               "entity_type": "LOT", "GSI2PK": f"STORE#{l.store_id}#PRODUCT#{l.product_id}",
               "GSI2SK": f"LOT#{l.expiration_date}#{l.lot_id}", "lot_id": l.lot_id, "store_id": l.store_id,
               "product_id": l.product_id, "product_name": prod.loc[l.product_id].product_name,
               "supplier_id": l.supplier_id, "received_date": l.received_date,
               "quantity_received": int(l.quantity_received), "expiration_date": l.expiration_date,
               "quantity_remaining": int(l.quantity_remaining), "unit_cost": cost,
               "value_at_risk": r2(cost * l.quantity_remaining), "status": "ACTIVE"})

    pos = read("purchase_orders", dtype={"store_id": str, "product_id": str})
    in_transit = pos.actual_delivery_date.isna()
    pos = pos[in_transit | (pos.order_date >= ds(AS_OF - pd.Timedelta(days=SALES_WINDOW_DAYS)))]
    for o in pos.itertuples(index=False):
        p, sp = prod.loc[o.product_id], sup.loc[o.supplier_id]
        open_ = not isinstance(o.actual_delivery_date, str)
        status = ("LATE" if o.expected_delivery_date <= ds(AS_OF) else "IN_TRANSIT") if open_ else "DELIVERED"
        lead = (pd.Timestamp(o.expected_delivery_date) - pd.Timestamp(o.order_date)).days
        w.put({"PK": f"STORE#{o.store_id}", "SK": f"PO#{o.order_date}#{o.order_id}", "entity_type": "PO",
               "GSI1PK": f"SUPPLIER#{o.supplier_id}", "GSI1SK": f"PO#{o.order_date}#{o.order_id}",
               "GSI2PK": f"DELIVERY#{o.expected_delivery_date}" if open_ else None,
               "GSI2SK": f"STORE#{o.store_id}#{o.order_id}" if open_ else None,
               "order_id": o.order_id, "store_id": o.store_id, "product_id": o.product_id,
               "product_name": p.product_name, "supplier_id": o.supplier_id, "supplier_name": sp.supplier_name,
               "supplier_email": sp.email, "order_date": o.order_date, "quantity": int(o.quantity),
               "unit_cost": float(o.unit_cost), "total_cost": r2(o.quantity * o.unit_cost), "lead_time_days": lead,
               "expected_delivery_date": o.expected_delivery_date,
               "actual_delivery_date": o.actual_delivery_date if not open_ else None,
               "is_urgent": bool(o.is_urgent), "predicted_daily_demand": float(o.predicted_daily_demand),
               "stock_at_order": int(o.stock_at_order), "status": status})

    on_hand = lots.groupby(["store_id", "product_id"]).quantity_remaining.sum()
    next_exp = lots.groupby(["store_id", "product_id"]).expiration_date.min()
    n_lots = lots.groupby(["store_id", "product_id"]).size()
    open_pos = pos[pos.actual_delivery_date.isna()]
    on_order = open_pos.groupby(["store_id", "product_id"]).quantity.sum()
    open_ids = open_pos.groupby(["store_id", "product_id"]).order_id.apply(list)
    sold = recent[recent.units_sold > 0].groupby(["store_id", "product_id"]).date.max()

    # lost demand over the last 7 days (inventory_daily.lost_demand), exposed as §3.7 lost_demand_7d
    print("reading inventory_daily.csv ...")
    inv = read("inventory_daily", dtype={"store_id": str, "product_id": str})
    inv["date"] = pd.to_datetime(inv["date"])
    lost = (inv[(inv.date > AS_OF - pd.Timedelta(days=7)) & (inv.date <= AS_OF)]
            .groupby(["store_id", "product_id"]).lost_demand.sum())
    del inv

    for s in stores.store_id:
        for p in products.itertuples(index=False):
            key = (s, p.product_id)
            lts = sorted(o["lead_time_days"] for o in offers_by_product[p.product_id])
            median_lt = lts[len(lts) // 2]
            shelf = int(p.shelf_life_days)
            safety = SAFETY_DAYS_PERISHABLE if shelf <= PERISHABLE_MAX_SHELF_LIFE else SAFETY_DAYS_STABLE
            pred = float(np.mean(forecast.get(key, [0.0])[:7]))
            oh = int(on_hand.get(key, 0))
            rp = int(math.ceil(pred * (median_lt + safety)))
            mx = max(rp + 1, int(math.ceil(pred * (median_lt + safety + min(CYCLE_DAYS, shelf)))))
            w.put({"PK": f"STORE#{s}", "SK": f"STOCK#{p.product_id}", "entity_type": "STOCK",
                   "GSI2PK": f"STORE#{s}#PRODUCT#{p.product_id}", "GSI2SK": "STOCK",
                   "store_id": s, "product_id": p.product_id, "product_name": p.product_name,
                   "category_id": p.category_id, "retail_price": float(p.retail_price), "shelf_life_days": shelf,
                   "on_hand": oh, "on_order": int(on_order.get(key, 0)), "open_order_ids": open_ids.get(key, []),
                   "reorder_point": rp, "max_stock": mx, "predicted_daily_demand": round(pred, 4),
                   "days_coverage": round(oh / pred, 2) if pred > 0 else None,
                   "next_expiration_date": next_exp.get(key), "active_lots": int(n_lots.get(key, 0)),
                   "last_sale_date": ds(sold[key]) if key in sold.index else None,
                   "lost_demand_7d": int(lost.get(key, 0)),
                   "as_of_date": ds(AS_OF), "updated_at": "2026-09-30T23:59:00Z", "version": 1})

    # auth and counters
    u = DEMO_USER
    w.put({"PK": f"USER#{u['email']}", "SK": "#META", "entity_type": "USER", "user_id": u["id"], "name": u["name"],
           "email": u["email"], "password_hash": hash_password(u["password"]), "created_at": "2026-09-30T00:00:00Z"})
    w.put({"PK": "COUNTER", "SK": "PO", "entity_type": "COUNTER", "value": 91800})
    w.close()
    print(json.dumps({"items": w.n, "files": w.part, "by_entity": w.counts}, indent=2))


if __name__ == "__main__":
    main()

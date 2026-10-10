"""Build dimension tables: zones, categories, products, stores, suppliers, supplier_products."""
import math
import re
import unicodedata

import numpy as np
import pandas as pd

from catalog_data import (CATEGORIES, PRODUCTS, STORE_ANCHORS, SUPPLIERS, WEEKDAY_MULT, ZONES,
                          on_hill)
from common import LAT_RANGE, LON_RANGE, rng, write_csv

DELIVERY_DAYS_BY_LEAD = {1: "Mon|Tue|Wed|Thu|Fri|Sat", 2: "Mon|Wed|Fri", 3: "Tue|Fri"}
PERISHABLE_KEYS = {k for k, v in CATEGORIES.items() if v[2] == 1}


def product_id(i: int) -> str:
    return f"P{i + 1:03d}"


def supplier_id(i: int) -> str:
    return f"SUP{i + 1:02d}"


def build_zones() -> pd.DataFrame:
    rows = []
    for zone, (desc, peaks, notes) in ZONES.items():
        wk = WEEKDAY_MULT[zone]
        rows.append({"zone_type": zone, "description": desc, "peak_hours": peaks,
                     "weekend_multiplier": round((wk[5] + wk[6]) / 2, 2), "notes": notes})
    return pd.DataFrame(rows)


def build_categories() -> pd.DataFrame:
    return pd.DataFrame([{"category_id": cid, "category_name": name, "is_perishable": per,
                          "typical_shelf_life_days": life, "requires_refrigeration": fridge}
                         for cid, name, per, life, fridge in CATEGORIES.values()])


def build_products() -> pd.DataFrame:
    assert len(PRODUCTS) == 120, f"expected 120 products, got {len(PRODUCTS)}"
    rows = []
    for i, (name, cat, brand, unit, life, price, sens, base, _tags, seasonal) in enumerate(PRODUCTS):
        rows.append({"product_id": product_id(i), "product_name": name, "category_id": CATEGORIES[cat][0],
                     "brand": brand, "unit": unit, "shelf_life_days": life, "retail_price": round(price, 2),
                     "is_seasonal": seasonal, "weather_sensitivity": sens, "base_daily_demand": base})
    return pd.DataFrame(rows)


def build_stores() -> pd.DataFrame:
    r = rng(1)
    rows = []
    for i, (colonia, lat0, lon0, zone, zone2, size, is_24h) in enumerate(STORE_ANCHORS):
        while True:
            lat = round(lat0 + r.uniform(-0.0015, 0.0015), 6)
            lon = round(lon0 + r.uniform(-0.0015, 0.0015), 6)
            if (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]
                    and not on_hill(lat, lon)):
                break
        hours = "00:00-24:00" if is_24h else r.choice(["06:00-23:00", "07:00-23:00", "06:00-24:00"])
        opened = pd.Timestamp("2008-01-01") + pd.Timedelta(days=int(r.integers(0, 5650)))
        rows.append({"store_id": f"S{i + 1:03d}", "store_name": f"OXXO {colonia}", "colonia": colonia,
                     "latitude": lat, "longitude": lon, "zone_type": zone, "secondary_zone_type": zone2,
                     "size_category": size, "opening_hours": hours, "is_24h": is_24h,
                     "opened_date": opened.strftime("%Y-%m-%d")})
    return pd.DataFrame(rows)


def supplier_email(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.split(r"[^a-z0-9]+", ascii_name) if w]
    return ".".join(words[:3]) + "@gmail.com"


def build_suppliers() -> pd.DataFrame:
    r = rng(2)
    rows = []
    for i, (name, city, _lt, cats) in enumerate(SUPPLIERS):
        rows.append({"supplier_id": supplier_id(i), "supplier_name": name, "email": supplier_email(name),
                     "categories_served": "|".join(CATEGORIES[c][0] for c in cats), "city": city,
                     "reliability_score": round(float(r.uniform(0.70, 0.99)), 2)})
    return pd.DataFrame(rows)


def _min_order_qty(base: float, unit: str, days: float) -> int:
    qty = max(1, math.ceil(base * days))
    if unit in ("bottle", "can") and base >= 3:
        qty = int(math.ceil(qty / 6) * 6)
    return qty


def build_supplier_products() -> pd.DataFrame:
    r = rng(3)
    by_cat = {k: [i for i, s in enumerate(SUPPLIERS) if k in s[3]] for k in CATEGORIES}
    for k, sups in by_cat.items():
        assert len(sups) >= 3, f"category {k} has only {len(sups)} suppliers"
    rows = []
    for p, (_name, cat, _brand, unit, life, price, _sens, base, _tags, _seas) in enumerate(PRODUCTS):
        cands = by_cat[cat]
        n = int(r.integers(3, min(5, len(cands)) + 1))
        chosen = r.choice(cands, size=n, replace=False)
        # "cheapness" in [0, 1]: cheaper offers tend to have longer lead times and larger MOQs,
        # with noise so the cheapest is not always the slowest.
        cheap = np.sort(r.uniform(0, 1, n))[r.permutation(n)]
        costs = set()
        perishable = cat in PERISHABLE_KEYS and life <= 30
        for s, c in zip(chosen, cheap):
            margin = 0.10 + 0.35 * c
            cost = round(price * (1 - margin), 2)
            while cost in costs or not (0.10 <= 1 - cost / price <= 0.45):
                margin = float(np.clip(margin + r.uniform(-0.01, 0.01), 0.101, 0.449))
                cost = round(price * (1 - margin), 2)
            costs.add(cost)
            base_lt = SUPPLIERS[s][2]
            if perishable:
                lt = 1 + int(c + r.normal(0, 0.3) > 0.7)
                lt = int(min(lt, max(1, life // 5)))  # fresh items (<= 9 days) come next day
                moq_days = 0.8 + 1.5 * c + r.uniform(0, 0.5)
            else:
                lt = int(np.clip(round(base_lt + 1.5 * c + r.normal(0, 0.6)), 1, 7))
                moq_days = 1.5 + 4.0 * c + r.uniform(0, 1.0)
            rows.append({"supplier_id": supplier_id(int(s)), "product_id": product_id(p), "unit_cost": cost,
                         "min_order_qty": _min_order_qty(base, unit, moq_days), "lead_time_days": lt,
                         "delivery_days": DELIVERY_DAYS_BY_LEAD.get(lt, ["Mon", "Tue", "Wed", "Thu"][s % 4])})
    return pd.DataFrame(rows).sort_values(["product_id", "supplier_id"]).reset_index(drop=True)


def main() -> None:
    print("Building catalogs...")
    write_csv(build_zones(), "zones")
    write_csv(build_categories(), "categories")
    write_csv(build_products(), "products", float_format="%.2f")
    write_csv(build_stores(), "stores")
    write_csv(build_suppliers(), "suppliers")
    write_csv(build_supplier_products(), "supplier_products", float_format="%.2f")


if __name__ == "__main__":
    main()

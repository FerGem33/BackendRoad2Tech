"""Phase 3 acceptance checks and sanity checks for the v2 dataset. Exits non-zero on failure."""
import sys

import numpy as np
import pandas as pd

from catalog_data import on_hill
from common import DATES, LAT_RANGE, LON_RANGE, OUT_DIR, SEED, read_csv
from supplier_selection import select_supplier

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def orphans(child: pd.Series, parent: pd.Series) -> int:
    return int((~child.isin(set(parent))).sum())


def main() -> int:
    t = {n: read_csv(n) for n in ["stores", "zones", "categories", "products", "suppliers", "supplier_products",
                                  "calendar", "weather", "inventory_lots", "purchase_orders"]}
    sales = read_csv("sales")
    st, pr, su, sp = t["stores"], t["products"], t["suppliers"], t["supplier_products"]
    po, lots, cal, wx = t["purchase_orders"], t["inventory_lots"], t["calendar"], t["weather"]

    print("\nACCEPTANCE CRITERIA")
    fk = {
        "sales.store_id": orphans(sales.store_id, st.store_id),
        "sales.product_id": orphans(sales.product_id, pr.product_id),
        "products.category_id": orphans(pr.category_id, t["categories"].category_id),
        "stores.zone_type": orphans(st.zone_type, t["zones"].zone_type),
        "stores.secondary_zone_type": orphans(st.secondary_zone_type.dropna(), t["zones"].zone_type),
        "supplier_products.supplier_id": orphans(sp.supplier_id, su.supplier_id),
        "supplier_products.product_id": orphans(sp.product_id, pr.product_id),
        "purchase_orders.store/product/supplier": orphans(po.store_id, st.store_id) + orphans(po.product_id, pr.product_id)
        + orphans(po.supplier_id, su.supplier_id),
        "inventory_lots.store/product/supplier": orphans(lots.store_id, st.store_id)
        + orphans(lots.product_id, pr.product_id) + orphans(lots.supplier_id, su.supplier_id),
        "purchase_orders (supplier, product) offered": orphans(po.supplier_id + "|" + po.product_id,
                                                               sp.supplier_id + "|" + sp.product_id),
        "inventory_lots (supplier, product) offered": orphans(lots.supplier_id + "|" + lots.product_id,
                                                              sp.supplier_id + "|" + sp.product_id),
    }
    bad = {k: v for k, v in fk.items() if v}
    check("All foreign keys resolve", not bad, f"{len(fk)} relationships checked" + (f"; orphans: {bad}" if bad else ""))

    inside = st.latitude.between(*LAT_RANGE) & st.longitude.between(*LON_RANGE)
    hills = [on_hill(a, b) for a, b in zip(st.latitude, st.longitude)]
    check("All stores inside Saltillo urban area", bool(inside.all()) and not any(hills),
          f"lat {st.latitude.min():.4f}..{st.latitude.max():.4f}, lon {st.longitude.min():.4f}..{st.longitude.max():.4f}")

    g = sp.merge(pr[["product_id", "retail_price"]]).groupby("product_id")
    n_sup = g.size()
    distinct = g.unit_cost.nunique() == n_sup
    below = (sp.merge(pr[["product_id", "retail_price"]]).eval("unit_cost < retail_price")).all()
    margin = 1 - sp.unit_cost / sp.merge(pr[["product_id", "retail_price"]]).retail_price
    check("Every product has 3-5 suppliers, distinct prices, unit_cost < retail_price",
          n_sup.between(3, 5).all() and distinct.all() and below and len(n_sup) == 120,
          f"suppliers/product {n_sup.min()}-{n_sup.max()}, margin {margin.min():.1%}-{margin.max():.1%}")

    served = su.set_index("supplier_id").categories_served.str.split("|").to_dict()
    cat_of = pr.set_index("product_id").category_id.to_dict()
    outside = sum(cat_of[p] not in served[s] for s, p in zip(sp.supplier_id, sp.product_id))
    per_cat = pd.Series([c for cs in served.values() for c in cs]).value_counts()
    check("15-20 suppliers; none sells outside categories_served", 15 <= len(su) <= 20 and outside == 0,
          f"{len(su)} suppliers, {outside} out-of-category offers, suppliers/category {per_cat.min()}-{per_cat.max()}")

    price = sales.product_id.map(pr.set_index("product_id").retail_price)
    rev_ok = np.allclose(sales.revenue, np.round(sales.units_sold * price, 2), atol=0.005)
    po_cost = po.merge(sp[["supplier_id", "product_id", "unit_cost"]], on=["supplier_id", "product_id"],
                       suffixes=("", "_catalog"))
    check("unit_cost and retail_price constant over the whole range", rev_ok and
          bool((po_cost.unit_cost == po_cost.unit_cost_catalog).all()),
          f"revenue = units x retail_price on {len(sales):,} rows; PO unit_cost = catalog on {len(po_cost):,} orders")

    sample = po.sample(100, random_state=SEED)
    same = sum(select_supplier(r.store_id, r.product_id, r.order_date, r.predicted_daily_demand, r.stock_at_order,
                               data_dir=OUT_DIR) == r.supplier_id for r in sample.itertuples())
    check("select_supplier reproduces 100 sampled orders", same == 100, f"{same}/100 match")

    full = DATES.strftime("%Y-%m-%d")
    check("calendar.csv and weather.csv cover every day without gaps",
          list(cal.date) == list(full) and list(wx.date) == list(full), f"{len(full)} days {full[0]} -> {full[-1]}")

    mandatory = {
        "sales": (sales, sales.columns), "stores": (st, st.columns.drop("secondary_zone_type")),
        "products": (pr, pr.columns), "suppliers": (su, su.columns), "supplier_products": (sp, sp.columns),
        "calendar": (cal, cal.columns.drop(["holiday_name", "event_name", "event_scope"])),
        "weather": (wx, wx.columns), "inventory_lots": (lots, lots.columns),
        "purchase_orders": (po, po.columns.drop("actual_delivery_date")),
    }
    nulls = {k: int(df[list(cols)].isna().sum().sum()) for k, (df, cols) in mandatory.items()}
    check("No negative units_sold and no nulls in mandatory columns",
          int((sales.units_sold < 0).sum()) == 0 and not any(nulls.values()),
          f"min units_sold={sales.units_sold.min()}, nulls={ {k: v for k, v in nulls.items() if v} or 0}; "
          f"{po.actual_delivery_date.isna().sum()} orders still in transit on {full[-1]}")

    exp_ok = (pd.to_datetime(lots.expiration_date) > pd.to_datetime(lots.received_date)).all()
    check("inventory_lots: expiration_date > received_date", bool(exp_ok), f"{len(lots):,} lots")

    check("sales.csv ~3.0M rows (full grid)", len(sales) == len(full) * len(st) * len(pr),
          f"{len(sales):,} rows = {len(full)} x {len(st)} x {len(pr)}")

    print("\nSANITY CHECKS")
    cats = t["categories"].set_index("category_id").category_name
    pinfo = pr.assign(category=pr.category_id.map(cats)).set_index("product_id")
    sales["category"] = sales.product_id.map(pinfo.category)
    sales["zone"] = sales.store_id.map(st.set_index("store_id").zone_type)
    daily_cat = sales.groupby(["date", "category"]).units_sold.sum().unstack()

    beer_ids = pinfo.index[pinfo.product_name.str.contains("Tecate|Carta Blanca|Indio|Corona|Victoria|Modelo|Michelob|Bud Light")]
    beer = sales[sales.product_id.isin(beer_ids)].groupby("date").units_sold.sum()
    sep16 = beer[beer.index.str.endswith("-09-16")]
    print(f"  1) Beer units on Sep 16 vs daily average: {sep16.mean():,.0f} vs {beer.mean():,.0f} "
          f"(x{sep16.mean() / beer.mean():.2f}); by year: { {k: int(v) for k, v in sep16.items()} }")
    sep15 = beer[beer.index.str.endswith("-09-15")]
    print(f"     (Sep 15, El Grito: x{sep15.mean() / beer.mean():.2f})")

    month = pd.Series(daily_cat.index.str[5:7], index=daily_cat.index)
    cof = daily_cat["Coffee and Hot Beverages"]
    hot_ids = pinfo.index[pinfo.product_name.str.contains("Americano|Cappuccino|Latte|Hot Chocolate")]
    hot = sales[sales.product_id.isin(hot_ids)].groupby("date").units_sold.sum()
    print(f"  2) Coffee & hot beverages, avg units/day January vs July: {cof[month == '01'].mean():,.0f} vs "
          f"{cof[month == '07'].mean():,.0f} (x{cof[month == '01'].mean() / cof[month == '07'].mean():.2f}); "
          f"hot coffee/chocolate only: x{hot[month == '01'].mean() / hot[month == '07'].mean():.2f}")

    school = sales[sales.zone == "school"].groupby(["date", "store_id"]).units_sold.sum().groupby("date").mean()
    c = cal.set_index("date")
    weekday_regular = (c.is_weekend == 0) & (c.is_holiday == 0) & ~c.event_name.fillna("").str.contains("School Technical")
    vac = school[weekday_regular & (c.is_school_vacation == 1)]
    term = school[weekday_regular & (c.is_school_vacation == 0)]
    print(f"  3) School-zone store, avg units/day on weekdays: vacation {vac.mean():,.0f} vs school days "
          f"{term.mean():,.0f} ({vac.mean() / term.mean() - 1:+.0%})")
    college = sales[sales.zone == "college"].groupby(["date", "store_id"]).units_sold.sum().groupby("date").mean()
    cv = college[weekday_regular & (c.is_college_vacation == 1)].mean() / college[weekday_regular & (c.is_college_vacation == 0)].mean()
    print(f"     College-zone store, university vacation vs term weekdays: {cv - 1:+.0%}")

    hw = sales[sales.zone == "highway"].groupby("product_id").units_sold.sum().nlargest(5)
    print("  4) Top 5 products in highway stores:")
    for pid, u in hw.items():
        print(f"       {pid} {pinfo.loc[pid, 'product_name']:<36} {pinfo.loc[pid, 'category']:<26} {u:,} units")

    hwc = sales[sales.zone == "highway"].groupby("category").units_sold.sum().nlargest(5)
    print("     Top 5 categories in highway stores: " + ", ".join(f"{k} ({v:,})" for k, v in hwc.items()))

    share = sales.groupby(["zone", "category"]).units_sold.sum() / sales.groupby("zone").units_sold.sum()
    print("  5) School vs college mix (share of units): "
          f"alcohol school {share['school', 'Alcoholic Beverages']:.1%} vs college {share['college', 'Alcoholic Beverages']:.1%}; "
          f"candy school {share['school', 'Candy and Chocolate']:.1%} vs college {share['college', 'Candy and Chocolate']:.1%}")

    lots_cat = lots.assign(category=lots.product_id.map(pinfo.category),
                           expired=pd.to_datetime(lots.expiration_date) <= DATES[-1])
    w = lots_cat[lots_cat.expired].groupby("category").quantity_remaining.sum() / lots_cat.groupby("category").quantity_received.sum()
    print("  6) Expired share of received units (perishables): " +
          ", ".join(f"{k} {v:.1%}" for k, v in w.sort_values(ascending=False).head(5).items()))
    print(f"     Orders flagged urgent: {po.is_urgent.mean():.1%}; orders to cheapest supplier: "
          f"{(po.merge(sp.loc[sp.groupby('product_id').unit_cost.idxmin(), ['product_id', 'supplier_id']], on=['product_id', 'supplier_id']).shape[0] / len(po)):.1%}")

    passed = sum(RESULTS)
    print(f"\n{passed}/{len(RESULTS)} acceptance checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

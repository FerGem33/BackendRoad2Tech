"""Acceptance checks for the v2 dataset. Exits non-zero on failure."""
import csv
import sys
from collections import defaultdict

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


def ticket_reconciliation(sales: pd.DataFrame, products: pd.DataFrame, stores: pd.DataFrame) -> tuple[bool, str]:
    """Stream ticket lines store-day by store-day, so validation remains bounded in memory."""
    path = OUT_DIR / "ticket_lines.csv"
    expected_groups = sales.groupby(["date", "store_id"], sort=False)
    product_ids, store_ids = set(products.product_id), set(stores.store_id)
    rows = bad_fk = bad_values = bad_timestamp = groups = mismatches = 0
    last_ticket = 0

    def actual_groups():
        nonlocal rows, bad_fk, bad_values, bad_timestamp, last_ticket
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fields = ["ticket_id", "timestamp", "date", "store_id", "product_id", "quantity", "gross_amount",
                      "discount_amount", "net_amount", "campaign_id", "line_type"]
            if reader.fieldnames != fields:
                raise ValueError(f"unexpected ticket_lines columns: {reader.fieldnames}")
            key, totals = None, None
            for row in reader:
                rows += 1
                current = (row["date"], row["store_id"])
                if key is not None and current != key:
                    yield key, totals
                    totals = None
                key = current
                if totals is None:
                    totals = defaultdict(lambda: np.zeros(4, dtype=float))
                try:
                    ticket_number = int(row["ticket_id"][1:])
                    quantity = int(row["quantity"])
                    gross, discount, net = (float(row[k]) for k in ("gross_amount", "discount_amount", "net_amount"))
                except (ValueError, KeyError):
                    bad_values += 1
                    continue
                if ticket_number != last_ticket and ticket_number != last_ticket + 1:
                    bad_values += 1
                last_ticket = max(last_ticket, ticket_number)
                if row["product_id"] not in product_ids or row["store_id"] not in store_ids:
                    bad_fk += 1
                if quantity <= 0 or gross < 0 or discount < 0 or net < 0 or not np.isclose(net, gross - discount, atol=.01):
                    bad_values += 1
                if not row["timestamp"].startswith(row["date"] + "T"):
                    bad_timestamp += 1
                totals[row["product_id"]] += [quantity, gross, discount, net]
            if key is not None:
                yield key, totals

    actual = actual_groups()
    for expected_key, expected in expected_groups:
        try:
            actual_key, totals = next(actual)
        except StopIteration:
            mismatches += 1
            break
        groups += 1
        if tuple(expected_key) != actual_key:
            mismatches += 1
            continue
        expected_by_product = expected.set_index("product_id")
        active_products = set(expected_by_product.index[expected_by_product.units_sold > 0])
        if set(totals) != active_products:
            mismatches += 1
            continue
        for product_id, values in totals.items():
            source = expected_by_product.loc[product_id]
            target = np.array([source.units_sold, source.gross_revenue, source.discount_amount, source.net_revenue], dtype=float)
            if not np.allclose(values, target, atol=.01):
                mismatches += 1
                break
    try:
        next(actual)
        mismatches += 1
    except StopIteration:
        pass
    ok = not (bad_fk or bad_values or bad_timestamp or mismatches)
    return ok, (f"{rows:,} ticket lines; {groups:,} store-days; fk={bad_fk}, values={bad_values}, "
                f"timestamp={bad_timestamp}, aggregate mismatches={mismatches}")


def main() -> int:
    names = ["stores", "zones", "categories", "products", "suppliers", "supplier_products", "calendar", "weather",
             "inventory_lots", "purchase_orders", "inventory_daily", "promotion_campaigns", "promotion_store_days",
             "forecast_context"]
    tables = {name: read_csv(name) for name in names}
    sales = read_csv("sales")
    st, pr, su, sp = tables["stores"], tables["products"], tables["suppliers"], tables["supplier_products"]
    po, lots, cal, wx = (tables["purchase_orders"], tables["inventory_lots"], tables["calendar"], tables["weather"])
    inv, campaigns, assignments, future = (tables["inventory_daily"], tables["promotion_campaigns"],
                                            tables["promotion_store_days"], tables["forecast_context"])

    print("\nACCEPTANCE CRITERIA")
    fk = {
        "sales.store/product": orphans(sales.store_id, st.store_id) + orphans(sales.product_id, pr.product_id),
        "products.category": orphans(pr.category_id, tables["categories"].category_id),
        "stores.zone": orphans(st.zone_type, tables["zones"].zone_type) + orphans(st.secondary_zone_type.dropna(), tables["zones"].zone_type),
        "supplier offers": orphans(sp.supplier_id, su.supplier_id) + orphans(sp.product_id, pr.product_id),
        "orders": orphans(po.store_id, st.store_id) + orphans(po.product_id, pr.product_id) + orphans(po.supplier_id, su.supplier_id),
        "lots": orphans(lots.store_id, st.store_id) + orphans(lots.product_id, pr.product_id) + orphans(lots.supplier_id, su.supplier_id),
        "order/lot supplier offer": orphans(po.supplier_id + "|" + po.product_id, sp.supplier_id + "|" + sp.product_id)
        + orphans(lots.supplier_id + "|" + lots.product_id, sp.supplier_id + "|" + sp.product_id),
        "daily inventory": orphans(inv.store_id, st.store_id) + orphans(inv.product_id, pr.product_id),
        "campaign definition": orphans(campaigns.anchor_product_id, pr.product_id) + orphans(campaigns.promoted_product_id, pr.product_id),
        "campaign assignments": orphans(assignments.campaign_id, campaigns.campaign_id) + orphans(assignments.store_id, st.store_id),
    }
    bad = {key: value for key, value in fk.items() if value}
    check("All foreign keys resolve", not bad, f"{len(fk)} relationships checked" + (f"; orphans: {bad}" if bad else ""))

    inside = st.latitude.between(*LAT_RANGE) & st.longitude.between(*LON_RANGE)
    check("All stores inside Saltillo urban area", bool(inside.all()) and not any(on_hill(a, b) for a, b in zip(st.latitude, st.longitude)),
          f"lat {st.latitude.min():.4f}..{st.latitude.max():.4f}, lon {st.longitude.min():.4f}..{st.longitude.max():.4f}")

    offers = sp.merge(pr[["product_id", "retail_price"]])
    n_sup = offers.groupby("product_id").size()
    check("Every product has 3-5 suppliers, distinct costs and positive retail margin",
          n_sup.between(3, 5).all() and (offers.groupby("product_id").unit_cost.nunique() == n_sup).all()
          and (offers.unit_cost < offers.retail_price).all() and len(n_sup) == len(pr),
          f"suppliers/product {n_sup.min()}-{n_sup.max()}")

    price = sales.product_id.map(pr.set_index("product_id").retail_price)
    revenue_ok = (np.allclose(sales.gross_revenue, np.round(sales.units_sold * price, 2), atol=.005)
                  and np.allclose(sales.net_revenue, sales.gross_revenue - sales.discount_amount, atol=.005)
                  and np.allclose(sales.revenue, sales.net_revenue, atol=.005))
    po_cost = po.merge(sp[["supplier_id", "product_id", "unit_cost"]], on=["supplier_id", "product_id"], suffixes=("", "_catalog"))
    check("gross price, discounts and net revenue are internally consistent", revenue_ok and (po_cost.unit_cost == po_cost.unit_cost_catalog).all(),
          f"{len(sales):,} sales rows; gross = units x retail_price")

    sample = po.sample(min(100, len(po)), random_state=SEED)
    same = sum(select_supplier(row.store_id, row.product_id, row.order_date, row.predicted_daily_demand, row.stock_at_order,
                               data_dir=OUT_DIR) == row.supplier_id for row in sample.itertuples())
    check("select_supplier reproduces sampled orders", same == len(sample), f"{same}/{len(sample)} match")

    full = DATES.strftime("%Y-%m-%d").tolist()
    check("calendar.csv and weather.csv cover every observed day", cal.date.tolist() == full and wx.date.tolist() == full,
          f"{len(full)} days {full[0]} -> {full[-1]}")
    expected_future = pd.date_range(DATES[-1] + pd.Timedelta(days=1), periods=14).strftime("%Y-%m-%d").tolist()
    forbidden = {"units_sold", "revenue", "gross_revenue", "net_revenue", "closing_qty", "lost_demand"}
    check("forecast_context is a 14-day feature-only horizon", future.date.tolist() == expected_future and not (set(future.columns) & forbidden),
          f"{future.date.iloc[0]} -> {future.date.iloc[-1]}; forbidden={set(future.columns) & forbidden}")

    mandatory = {
        "sales": (sales, sales.columns.drop("campaign_id")), "stores": (st, st.columns.drop("secondary_zone_type")),
        "products": (pr, pr.columns), "suppliers": (su, su.columns), "supplier_products": (sp, sp.columns),
        "calendar": (cal, cal.columns.drop(["holiday_name", "event_name", "event_scope"])), "weather": (wx, wx.columns),
        "inventory_lots": (lots, lots.columns), "inventory_daily": (inv, inv.columns), "promotion_campaigns": (campaigns, campaigns.columns),
        "promotion_store_days": (assignments, assignments.columns), "forecast_context": (future, future.columns.drop(["holiday_name", "event_name", "event_scope"])),
        "purchase_orders": (po, po.columns.drop("actual_delivery_date")),
    }
    nulls = {name: int(df[list(columns)].isna().sum().sum()) for name, (df, columns) in mandatory.items()}
    check("No nulls in mandatory columns or negative sold units", (sales.units_sold >= 0).all() and not any(nulls.values()),
          f"min units_sold={sales.units_sold.min()}; nulls={ {k: v for k, v in nulls.items() if v} or 0}")

    check("sales.csv is the complete daily store-SKU grid", len(sales) == len(full) * len(st) * len(pr),
          f"{len(sales):,} = {len(full)} x {len(st)} x {len(pr)}")
    balance = inv.opening_qty + inv.received_qty - inv.expired_qty - inv.units_sold
    keys_match = np.array_equal(inv[["date", "store_id", "product_id"]].to_numpy(), sales[["date", "store_id", "product_id"]].to_numpy())
    inventory_ok = ((balance == inv.closing_qty).all() and keys_match and np.array_equal(inv.units_sold.to_numpy(), sales.units_sold.to_numpy())
                    and (inv.lost_demand >= 0).all() and (inv.stockout_flag == (inv.lost_demand > 0).astype(int)).all())
    check("daily inventory balances and reconciles to sales", bool(inventory_ok),
          f"balance violations={(balance != inv.closing_qty).sum():,}; stockout rows={inv.stockout_flag.sum():,}")

    assignment_dates = assignments.merge(campaigns[["campaign_id", "start_date", "end_date"]], on="campaign_id")
    in_window = ((assignment_dates.date >= assignment_dates.start_date) & (assignment_dates.date <= assignment_dates.end_date)).all()
    mix = assignments.groupby("campaign_id").is_treatment.agg(["sum", "count"])
    no_overlap = assignments.groupby(["date", "store_id"]).campaign_id.nunique().max() <= 1
    campaign_sales = sales.dropna(subset=["campaign_id"])
    discounts_ok = (campaign_sales.discount_amount >= 0).all() and (sales.loc[sales.campaign_id.isna(), "discount_amount"] == 0).all()
    check("campaigns have treatment/control, valid dates, no overlap and coherent discounts",
          bool(in_window and no_overlap and (mix["sum"] > 0).all() and (mix["sum"] < mix["count"]).all() and discounts_ok),
          f"{len(campaigns)} campaigns; treated share={assignments.is_treatment.mean():.1%}")

    ticket_ok, ticket_detail = ticket_reconciliation(sales, pr, st)
    check("ticket_lines reconcile exactly to daily sales", ticket_ok, ticket_detail)

    print("\nSANITY CHECKS")
    categories = tables["categories"].set_index("category_id").category_name
    pinfo = pr.assign(category=pr.category_id.map(categories)).set_index("product_id")
    sanity = sales.assign(category=sales.product_id.map(pinfo.category), zone=sales.store_id.map(st.set_index("store_id").zone_type))
    beer_ids = pinfo.index[pinfo.product_name.str.contains("Tecate|Carta Blanca|Indio|Corona|Victoria|Modelo|Michelob|Bud Light")]
    beer = sanity[sanity.product_id.isin(beer_ids)].groupby("date").units_sold.sum()
    sep16 = beer[beer.index.str.endswith("-09-16")]
    print(f"  Beer units on Sep 16 vs daily average: {sep16.mean():,.0f} vs {beer.mean():,.0f} (x{sep16.mean() / beer.mean():.2f})")
    daily_cat = sanity.groupby(["date", "category"]).units_sold.sum().unstack()
    month = pd.Series(daily_cat.index.str[5:7], index=daily_cat.index)
    coffee = daily_cat["Coffee and Hot Beverages"]
    print(f"  Coffee & hot beverages January vs July: {coffee[month == '01'].mean():,.0f} vs {coffee[month == '07'].mean():,.0f} "
          f"(x{coffee[month == '01'].mean() / coffee[month == '07'].mean():.2f})")
    highway = sanity[sanity.zone == "highway"].groupby("category").units_sold.sum().nlargest(5)
    print("  Top highway categories: " + ", ".join(f"{name} ({units:,})" for name, units in highway.items()))
    print("  Note: 'Alcoholic Beverages' is the formal category; do not abbreviate it as 'alcohol' alongside beverages.")

    passed = sum(RESULTS)
    print(f"\n{passed}/{len(RESULTS)} acceptance checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

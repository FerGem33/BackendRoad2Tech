"""Phase 0 read-only audit of the original hackathon CSVs (prints a report)."""
import sys, glob, os
import pandas as pd

from common import INPUT_DIR

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)
D = sys.argv[1] if len(sys.argv) > 1 else str(INPUT_DIR)
dfs = {}
for f in sorted(glob.glob(os.path.join(D, "*.csv"))):
    name = os.path.basename(f)[:-4]
    df = pd.read_csv(f, dtype=str, keep_default_na=False)
    df = df.replace("", pd.NA)
    dfs[name] = df
    print(f"\n{'='*100}\n{name}.csv  rows={len(df):,}  cols={df.shape[1]}  size={os.path.getsize(f)/1e6:.2f}MB")
    typed = pd.read_csv(f, low_memory=False)
    print("dtypes:", dict(typed.dtypes.astype(str)))
    nulls = (df.isna().mean() * 100).round(2)
    print("null% (non-zero):", {k: v for k, v in nulls.items() if v > 0} or "none")
    print("exact duplicate rows:", int(df.duplicated().sum()))
    print(df.sample(3, random_state=42).to_string(index=False))

def fk(child, col, parent, pcol):
    c = set(dfs[child][col].dropna()); p = set(dfs[parent][pcol].dropna())
    orphans = c - p
    print(f"FK {child}.{col} -> {parent}.{pcol}: {len(c)} distinct, orphans={len(orphans)} {sorted(orphans)[:5]}")

print(f"\n{'='*100}\nKEYS")
for t, k in [("sites", ["site_id"]), ("stores", ["store_id"]), ("products", ["sku"]), ("suppliers", ["supplier_id"]),
             ("sales_daily", ["date", "store_id", "sku"]), ("inventory_daily", ["date", "store_id", "sku"]),
             ("purchase_orders", ["po_id"]), ("calendar", ["date"]), ("weather_daily", ["site_id", "date"]),
             ("store_daily_kpis", ["store_id", "date"]), ("product_barcodes", ["barcode"])]:
    print(f"PK {t}{k}: duplicates={int(dfs[t].duplicated(k).sum())}")
fk("stores", "site_id", "sites", "site_id")
for t in ["sales_daily", "inventory_daily", "purchase_orders", "prices", "store_daily_kpis", "store_closures"]:
    fk(t, "store_id", "stores", "store_id")
for t in ["sales_daily", "inventory_daily", "purchase_orders", "prices", "product_barcodes"]:
    fk(t, "sku", "products", "sku")
fk("products", "supplier_id", "suppliers", "supplier_id")
fk("purchase_orders", "supplier_id", "suppliers", "supplier_id")
fk("sales_daily", "promo_id", "promotions", "promo_id")
fk("weather_daily", "site_id", "sites", "site_id")

print(f"\n{'='*100}\nDATE RANGES")
for t, df in dfs.items():
    for c in df.columns:
        if "date" in c or c in ("effective_from", "effective_to", "last_updated"):
            s = pd.to_datetime(df[c], errors="coerce")
            bad = int(s.isna().sum() - df[c].isna().sum())
            print(f"{t}.{c}: {s.min().date() if s.notna().any() else None} -> {s.max().date() if s.notna().any() else None}  unparseable={bad}")

print(f"\n{'='*100}\nCONTENT CHECKS")
num = lambda t, c: pd.to_numeric(dfs[t][c], errors="coerce")
for t, cols in {"sales_daily": ["units_sold", "unit_price", "gross_sales", "net_sales", "discount_amount"],
                "products": ["unit_cost", "list_price_base", "shelf_life_days", "case_size"],
                "purchase_orders": ["qty_ordered", "qty_received", "unit_cost"],
                "prices": ["unit_price"], "inventory_daily": ["opening_qty", "closing_qty", "expired_qty"],
                "weather_daily": ["temp_max_c", "temp_min_c", "precip_mm"]}.items():
    for c in cols:
        s = num(t, c)
        print(f"{t}.{c}: min={s.min()} max={s.max()} mean={s.mean():.2f} neg={(s<0).sum()} zero={(s==0).sum()}")
p = dfs["products"]
pc, pl = num("products", "unit_cost"), num("products", "list_price_base")
print("products cost>=price:", int((pc >= pl).sum()), " price==0:", int((pl == 0).sum()))
print("products status:", p["status"].value_counts().to_dict())
print("products inventory_tracked:", p["inventory_tracked"].value_counts().to_dict())
print("products per department:", p["department"].value_counts().to_dict())
print("products per category:", p["category"].value_counts().to_dict())
print("brands:", p["brand"].value_counts().to_dict())
print("storage:", p["storage_type"].value_counts().to_dict())
print("perishable w/ null shelf life:", int(((p.is_perishable == "1") & p.shelf_life_days.isna()).sum()))
print("sites:", dfs["sites"][["site_id", "site_type", "city"]].to_string(index=False))
print("store formats:", dfs["stores"]["format"].value_counts().to_dict(), " closed:", int(dfs["stores"].closed_date.notna().sum()))
print("suppliers:\n", dfs["suppliers"].to_string(index=False))
pr = dfs["prices"]
print("prices: distinct prices per store/sku max:", pr.groupby(["store_id", "sku"]).unit_price.nunique().max())
s = dfs["sales_daily"]
g = num("sales_daily", "units_sold") * num("sales_daily", "unit_price") - num("sales_daily", "gross_sales")
print("sales gross mismatch rows (>0.01):", int((g.abs() > 0.01).sum()))
print("sales distinct store/sku pairs:", s.groupby(["store_id", "sku"]).ngroups, " days:", s.date.nunique(),
      " stores:", s.store_id.nunique(), " skus:", s.sku.nunique())
print("calendar seasons:", dfs["calendar"].season.value_counts().to_dict())
print("calendar holidays:", dfs["calendar"].holiday_name.dropna().unique().tolist())
print("calendar day_of_week:", dfs["calendar"].day_of_week.unique().tolist())
w = dfs["weather_daily"]
print("weather sites:", w.site_id.nunique(), " Saltillo/Coahuila sites:",
      dfs["sites"][dfs["sites"].city.str.contains("Saltillo|Coahuila", na=False) | dfs["sites"].state.str.contains("Coahuila", na=False)].site_id.tolist())
po = dfs["purchase_orders"]
print("PO status:", po.status.value_counts().to_dict())
print("PO received<order:", int((pd.to_datetime(po.received_date) < pd.to_datetime(po.order_date)).sum()))
print("PO qty_received>qty_ordered:", int((num("purchase_orders", "qty_received") > num("purchase_orders", "qty_ordered")).sum()))
inv = dfs["inventory_daily"]
eq = num("inventory_daily", "opening_qty") + num("inventory_daily", "received_qty") - num("inventory_daily", "units_sold") \
     - num("inventory_daily", "expired_qty") - num("inventory_daily", "damaged_qty") - num("inventory_daily", "closing_qty")
print("inventory equation violations:", int((eq.abs() > 0.001).sum()))

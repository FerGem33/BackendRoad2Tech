"""Write v2/README.md: overview, generation rules and the data dictionary (examples read from the CSVs)."""
import pandas as pd

from common import (CYCLE_DAYS, END, FORECAST_DATES, OUT_DIR, SAFETY_DAYS_PERISHABLE, SAFETY_DAYS_STABLE, START,
                    out_path)

# file -> (description, [(column, type, unit, description)])
DICTIONARY = {
    "stores": ("One row per store (25 stores in Saltillo, Coahuila).", [
        ("store_id", "string", "-", "Store key (S001-S025)."),
        ("store_name", "string", "-", "Display name."),
        ("colonia", "string", "-", "Saltillo neighborhood or landmark used as the location reference."),
        ("latitude", "float", "degrees", "Approximate latitude, map display only (not a model feature)."),
        ("longitude", "float", "degrees", "Approximate longitude, map display only (not a model feature)."),
        ("zone_type", "string", "-", "Primary zone, FK to zones.zone_type. Model feature."),
        ("secondary_zone_type", "string (nullable)", "-", "Secondary zone for mixed areas (30% weight in demand). Model feature."),
        ("size_category", "string", "-", "small / medium / large (demand scale 0.8 / 1.0 / 1.25)."),
        ("opening_hours", "string", "HH:MM-HH:MM", "Daily opening hours."),
        ("is_24h", "int (0/1)", "-", "1 if the store opens 24 hours."),
        ("opened_date", "date", "YYYY-MM-DD", "Opening date (all before 2024-01-01)."),
    ]),
    "zones": ("Zone catalog.", [
        ("zone_type", "string", "-", "Zone key."),
        ("description", "string", "-", "Who shops there."),
        ("peak_hours", "string", "HH:MM-HH:MM, `|`-separated", "Typical peak windows (informational; data is daily)."),
        ("weekend_multiplier", "float", "x", "Average Saturday/Sunday demand multiplier vs. 1.0."),
        ("notes", "string", "-", "Key demand behavior."),
    ]),
    "categories": ("Category catalog (20 categories).", [
        ("category_id", "string", "-", "Category key (C01-C20)."),
        ("category_name", "string", "-", "Category name."),
        ("is_perishable", "int (0/1)", "-", "1 for short-life fresh categories."),
        ("typical_shelf_life_days", "int", "days", "Typical shelf life in the category."),
        ("requires_refrigeration", "int (0/1)", "-", "1 if the category needs cold storage."),
    ]),
    "products": ("Product catalog: 120 products, identical in every store.", [
        ("product_id", "string", "-", "Product key (P001-P120)."),
        ("product_name", "string", "-", "Product name."),
        ("category_id", "string", "-", "FK to categories.category_id."),
        ("brand", "string", "-", "Consumer brand (Private Label for store-made items)."),
        ("unit", "string", "-", "Selling unit (bottle, can, pack, piece, cup...)."),
        ("shelf_life_days", "int", "days", "Days from receipt to expiration."),
        ("retail_price", "float", "MXN", "Shelf price, fixed for the whole period."),
        ("is_seasonal", "int (0/1)", "-", "1 if demand is concentrated in a season (Three Kings' cake, roses)."),
        ("weather_sensitivity", "string", "-", "hot (sells more in heat) / cold (sells more in cold) / neutral."),
        ("base_daily_demand", "float", "units/day", "Expected units/day in a medium store on a neutral day (before multipliers)."),
    ]),
    "suppliers": ("Supplier catalog (19 fictional distributors).", [
        ("supplier_id", "string", "-", "Supplier key (SUP01-SUP19)."),
        ("supplier_name", "string", "-", "Fictional supplier name."),
        ("categories_served", "string", "`|`-separated category_id", "Categories the supplier may sell."),
        ("city", "string", "-", "Supplier base city."),
        ("reliability_score", "float", "probability", "Probability of on-time delivery (0.70-0.99)."),
    ]),
    "supplier_products": ("Each supplier's offer per product (3-5 offers per product).", [
        ("supplier_id", "string", "-", "FK to suppliers."),
        ("product_id", "string", "-", "FK to products."),
        ("unit_cost", "float", "MXN", "Purchase cost per unit, fixed; margin vs. retail 10-45%."),
        ("min_order_qty", "int", "units", "Minimum order quantity."),
        ("lead_time_days", "int", "days", "Days from order to expected delivery."),
        ("delivery_days", "string", "`|`-separated weekdays", "Supplier route days (informational; lead_time_days already accounts for it)."),
    ]),
    "calendar": ("One row per day, 2024-01-01 to 2026-09-30.", [
        ("date", "date", "YYYY-MM-DD", "Day."),
        ("day_of_week", "string", "-", "Monday..Sunday."),
        ("is_weekend", "int (0/1)", "-", "Saturday or Sunday."),
        ("is_holiday", "int (0/1)", "-", "Official rest day (Federal Labor Law)."),
        ("holiday_name", "string (nullable)", "-", "Official holiday name."),
        ("season_name", "string", "-", "winter / spring / summer / autumn."),
        ("event_name", "string (nullable)", "`|`-separated", "Commercial, religious, sports, local and school events active that day."),
        ("event_scope", "string (nullable)", "`|`-separated", "Scope of each event in event_name (national, local, sports, commercial, religious, school)."),
        ("is_payday", "int (0/1)", "-", "15th or last day of the month."),
        ("is_school_vacation", "int (0/1)", "-", "SEP (K-12) vacation; drives the school zone."),
        ("is_college_vacation", "int (0/1)", "-", "University vacation; drives the college zone."),
        ("demand_multiplier_hint", "float", "x", "Network-wide weighted average of calendar effects (events, vacations, paydays) for the day."),
    ]),
    "weather": ("Daily synthetic weather for Saltillo (semi-arid).", [
        ("date", "date", "YYYY-MM-DD", "Day."),
        ("temp_max_c", "float", "°C", "Maximum temperature."),
        ("temp_min_c", "float", "°C", "Minimum temperature."),
        ("precipitation_mm", "float", "mm", "Daily rainfall."),
        ("weather_label", "string", "-", "rainy (>= 5 mm) / hot (max >= 30 °C) / cold (max < 17 °C or min <= 1 °C) / mild."),
    ]),
    "sales": ("Main daily outcome table: full grid of date x store x product (zero-sale rows included).", [
        ("date", "date", "YYYY-MM-DD", "Day."),
        ("store_id", "string", "-", "FK to stores."),
        ("product_id", "string", "-", "FK to products."),
        ("units_sold", "int", "units", "Units sold = min(demand, stock on hand). Target variable."),
        ("gross_revenue", "float", "MXN", "units_sold x retail_price before promotion discount."),
        ("discount_amount", "float", "MXN", "Campaign discount applied to sold units; 0 outside treatment."),
        ("net_revenue", "float", "MXN", "gross_revenue - discount_amount."),
        ("revenue", "float", "MXN", "Backward-compatible alias of net_revenue."),
        ("campaign_id", "string (nullable)", "-", "Observed treatment campaign affecting this SKU/day/store, if any."),
        ("campaign_units", "int", "units", "Sold units exposed to campaign treatment; 0 otherwise."),
    ]),
    "inventory_daily": ("Daily inventory state after the operations simulation; one row per date x store x product.", [
        ("date", "date", "YYYY-MM-DD", "Day."),
        ("store_id", "string", "-", "FK to stores."),
        ("product_id", "string", "-", "FK to products."),
        ("opening_qty", "int", "units", "On-hand units at start of day, before deliveries."),
        ("received_qty", "int", "units", "Delivered units received during the morning."),
        ("expired_qty", "int", "units", "Units removed because their lot expired before sale."),
        ("units_sold", "int", "units", "Must equal sales.units_sold for the same key."),
        ("lost_demand", "int", "units", "Unfulfilled simulated demand after inventory was exhausted."),
        ("closing_qty", "int", "units", "On-hand units after expirations and sales."),
        ("on_order_qty", "int", "units", "Open purchase-order quantity at end of day."),
        ("stockout_flag", "int (0/1)", "-", "1 when lost_demand > 0."),
    ]),
    "promotion_campaigns": ("Historical promotion definitions. These are observed experiments, not agent recommendations.", [
        ("campaign_id", "string", "-", "Campaign key."),
        ("strategy_type", "string", "-", "affinity_combo / rescue_bundle / contextual_offer."),
        ("objective", "string", "-", "Business objective of the experiment."),
        ("anchor_product_id", "string", "-", "High-velocity or complementary SKU, FK to products."),
        ("promoted_product_id", "string", "-", "Discounted / rescued SKU, FK to products."),
        ("discount_pct", "float", "fraction", "Discount on promoted_product_id in treatment stores."),
        ("uplift_anchor_pct", "float", "fraction", "Demand effect intentionally simulated for the anchor."),
        ("uplift_promoted_pct", "float", "fraction", "Demand effect intentionally simulated for the promoted SKU."),
        ("start_date", "date", "YYYY-MM-DD", "Campaign start."),
        ("end_date", "date", "YYYY-MM-DD", "Campaign end."),
        ("trigger_reason", "string", "-", "Business rationale used by the synthetic generator."),
    ]),
    "promotion_store_days": ("Store-day campaign assignment; treatment and control make uplift estimable.", [
        ("campaign_id", "string", "-", "FK to promotion_campaigns."),
        ("date", "date", "YYYY-MM-DD", "Eligible campaign day."),
        ("store_id", "string", "-", "FK to stores."),
        ("assignment_group", "string", "-", "Zone-based stratum used to split treatment/control."),
        ("is_eligible", "int (0/1)", "-", "Store was in the experiment population."),
        ("is_treatment", "int (0/1)", "-", "Store received the offer; 0 means comparable control."),
        ("expected_uplift_pct", "float", "fraction", "Simulated promoted-SKU effect used only for data generation."),
    ]),
    "ticket_lines": ("Synthetic transactional baskets for affinity and ticket KPI analysis; reconciles to sales.csv.", [
        ("ticket_id", "string", "-", "Synthetic transaction key; repeats for each line in a basket."),
        ("timestamp", "datetime", "ISO-8601", "Synthetic checkout timestamp."),
        ("date", "date", "YYYY-MM-DD", "Date component of timestamp."),
        ("store_id", "string", "-", "FK to stores."),
        ("product_id", "string", "-", "FK to products."),
        ("quantity", "int", "units", "Units on this ticket line."),
        ("gross_amount", "float", "MXN", "quantity x retail_price."),
        ("discount_amount", "float", "MXN", "Line discount for treatment campaign, if any."),
        ("net_amount", "float", "MXN", "gross_amount - discount_amount."),
        ("campaign_id", "string (nullable)", "-", "Observed campaign attribution, if any."),
        ("line_type", "string", "-", "regular / affinity_bundle / campaign_bundle."),
    ]),
    "forecast_context": ("Known external features for the 14-day period immediately after observed sales; no targets included.", [
        ("date", "date", "YYYY-MM-DD", f"Future date ({END.date() + pd.Timedelta(days=1)} to {FORECAST_DATES[-1].date()})."),
        ("day_of_week", "string", "-", "Monday..Sunday."),
        ("is_weekend", "int (0/1)", "-", "Saturday or Sunday."),
        ("is_holiday", "int (0/1)", "-", "Official holiday."),
        ("holiday_name", "string (nullable)", "-", "Official holiday name."),
        ("season_name", "string", "-", "winter / spring / summer / autumn."),
        ("event_name", "string (nullable)", "-", "Known events active that day."),
        ("event_scope", "string (nullable)", "-", "Scope of event_name."),
        ("is_payday", "int (0/1)", "-", "15th or last day of month."),
        ("is_school_vacation", "int (0/1)", "-", "SEP vacation feature."),
        ("is_college_vacation", "int (0/1)", "-", "University vacation feature."),
        ("temp_max_c", "float", "°C", "Synthetic forecast maximum temperature."),
        ("temp_min_c", "float", "°C", "Synthetic forecast minimum temperature."),
        ("precipitation_mm", "float", "mm", "Synthetic forecast rainfall."),
        ("weather_label", "string", "-", "rainy / hot / cold / mild."),
    ]),
    "inventory_lots": ("One row per received lot (initial stock on 2024-01-01 plus every delivered order).", [
        ("lot_id", "string", "-", "Lot key."),
        ("store_id", "string", "-", "FK to stores."),
        ("product_id", "string", "-", "FK to products."),
        ("supplier_id", "string", "-", "FK to suppliers (supplier that delivered the lot)."),
        ("received_date", "date", "YYYY-MM-DD", "Delivery date (= purchase_orders.actual_delivery_date)."),
        ("quantity_received", "int", "units", "Units received."),
        ("expiration_date", "date", "YYYY-MM-DD", "received_date + shelf_life_days (+/- small variation). Sellable before this date."),
        ("quantity_remaining", "int", "units", "Units left when the lot expired (= waste) or, for lots still valid on 2026-09-30, units on the shelf that day."),
    ]),
    "purchase_orders": ("Order history generated with select_supplier().", [
        ("order_id", "string", "-", "Order key."),
        ("store_id", "string", "-", "FK to stores."),
        ("product_id", "string", "-", "FK to products."),
        ("supplier_id", "string", "-", "Supplier returned by select_supplier()."),
        ("order_date", "date", "YYYY-MM-DD", "Day the order was placed (end of day)."),
        ("quantity", "int", "units", "max(min_order_qty, ceil(predicted x (lead_time + cycle_days)))."),
        ("unit_cost", "float", "MXN", "Catalog unit cost of the chosen supplier."),
        ("expected_delivery_date", "date", "YYYY-MM-DD", "order_date + lead_time_days."),
        ("actual_delivery_date", "date (nullable)", "YYYY-MM-DD", "Expected date, delayed 1-3 days with probability 1 - reliability_score. Empty if after 2026-09-30 (in transit)."),
        ("is_urgent", "int (0/1)", "-", "1 if no supplier could deliver before the stock ran out."),
        ("predicted_daily_demand", "float", "units/day", "Forecast passed to select_supplier (noise-free expected demand, next 7 days)."),
        ("stock_at_order", "int", "units", "Stock on hand passed to select_supplier as current_stock."),
    ]),
}


def example(df: pd.DataFrame, col: str) -> str:
    s = df[col].dropna()
    return "" if s.empty else str(s.iloc[0])


def main() -> None:
    sizes, sections = [], []
    for name, (desc, cols) in DICTIONARY.items():
        path = out_path(name)
        df = pd.read_csv(path, nrows=20000, dtype=str)
        rows = sum(1 for _ in open(path, encoding="utf-8")) - 1
        sizes.append(f"| `{name}.csv` | {rows:,} | {path.stat().st_size / 1e6:.2f} MB | {desc} |")
        missing = set(df.columns) ^ {c[0] for c in cols}
        assert not missing, f"{name}: dictionary out of sync with columns {missing}"
        lines = [f"### `{name}.csv`\n\n{desc}\n", "| Column | Type | Unit | Description | Example |",
                 "|---|---|---|---|---|"]
        lines += [f"| `{c}` | {t} | {u} | {d} | `{example(df, c)}` |" for c, t, u, d in cols]
        sections.append("\n".join(lines))

    text = f"""# Road2Tech Hackathon Dataset v2 - OXXO Stores in Saltillo, Coahuila

> **This dataset is 100% synthetic.** It contains no real sales, inventory, prices, locations or supplier data
> from OXXO, FEMSA or any other company. Product brands are real consumer brands used only for realism, with
> approximate shelf prices; all supplier names are fictional; store coordinates are approximate points in real
> Saltillo neighborhoods; Mexico's 2026 World Cup knockout results are invented. Use it only for the hackathon.

## Overview

- **Period:** {START.date()} to {END.date()} (1,004 days, daily granularity, includes the 2026 FIFA World Cup)
- **Network:** 25 stores in Saltillo, 8 zone types, 120 products (same catalog in every store), 20 categories, 19 suppliers
- **Format:** CSV, UTF-8, comma delimiter, `snake_case` columns, dates `YYYY-MM-DD`, money in MXN with 2 decimals
- **Reproducible:** `python3 scripts/dataset/run_all.py` regenerates every file (fixed seed `SEED = 42`)
- **Setup:** `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

| File | Rows | Size | Content |
|---|---|---|---|
{chr(10).join(sizes)}

### Relationships

```
stores.zone_type / secondary_zone_type -> zones.zone_type
products.category_id                    -> categories.category_id
supplier_products.(supplier_id, product_id) -> suppliers, products
sales.(store_id, product_id)            -> stores, products;   sales.date -> calendar.date, weather.date
inventory_daily.(store_id, product_id)  -> stores, products; inventory_daily.units_sold -> sales.units_sold
promotion_store_days.campaign_id        -> promotion_campaigns.campaign_id
ticket_lines.(store_id, product_id)     -> stores, products; aggregates -> sales
inventory_lots / purchase_orders .(store_id, product_id, supplier_id) -> stores, products, suppliers
```

### Zones

`residential` (11 stores), and 2 stores each in `business`, `school`, `college`, `highway`, `industrial`,
`shopping_center` and `hospital`. `school` (K-12, SEP calendar) and `college` (universities) are separate on purpose:
school stores over-index on candy, snacks and juice and sell almost no alcoholic beverages or cigarettes, while college stores
over-index on alcoholic beverages (with a Thursday-Saturday peak), energy drinks, coffee and cigarettes, and follow the
university vacation calendar (`calendar.is_college_vacation`).

## Demand generation (`scripts/dataset/build_demand.py`)

```
expected_units = base_daily_demand
                 x store_scale[size_category] x store_product_affinity (lognormal, sigma 0.15)
                 x zone_multiplier[zone_type, category/tag]   (0.7 primary + 0.3 secondary zone)
                 x weekday_multiplier[zone_type, weekday] x category_weekday[category, weekday]
                 x weather_multiplier[weather_label, weather_sensitivity] x temperature_slope
                 x event_multiplier[event, category/tag, zone_type]
                 x annual_trend (+3.5 % volume per year; shelf prices are fixed, campaign discounts alter net price)
demand        = Poisson(expected_units x Gamma(shape=8, scale=1/8))
units_sold    = min(demand, stock on hand)
```

Controlled campaigns add a known treatment multiplier only for the assigned store-days. They are generated
before operations so inventory constraints can cap their observed effect. `promotion_store_days.is_treatment = 0`
rows are concurrent controls. Campaign outcome columns must **not** be used as future-looking forecast features.

Business rules encoded (exact factors are in `EVENT_RULES`, `ZONE_MULT`, `WEATHER_MULT` in the script):

- **Weather:** hot days lift soft drinks, water, beer, ice cream and ice (x1.30 + 2 %/°C above 26 °C); cold days lift
  coffee, hot chocolate, instant soups and sweet bread (x1.40); rain trims traffic.
- **Highway:** snacks, water, soft drinks, energy drinks and coffee lead every day; long weekends, Holy Week and summer vacation add traffic.
- **School / college:** sharp drops on weekends and during their own vacations; school-zone teacher-council Fridays behave like days off.
- **Business / industrial:** weekday breakfast/lunch profile (coffee, prepared foods), weekend and holiday drop.
- **Parties** (Sep 15-16, Super Bowl, Mexico match days, Liga MX playoffs, Father's Day): beer, snacks, ice, dips and soft
  drinks, strongest in `residential` and `college`.
- **Christmas Eve / New Year's Eve:** alcoholic beverages, 2.5-3 L soft drinks, ice and disposables; hangover products the day after.
- **Halloween / Day of the Dead:** candy (and flowers, sweet bread). **Three Kings' Day:** bakery, Three Kings' cake (sold only Dec 26-Jan 10), hot chocolate.
  **Candlemas:** tamales. **Valentine's / Mother's Day:** chocolates and flowers.
- **Paydays** (15th and last day, plus the next day): +6 % traffic, +12 % alcoholic beverages. **Dry law** on the June 2024 election weekend.

## Best-supplier rule (`scripts/dataset/supplier_selection.py`)

```python
from supplier_selection import select_supplier
select_supplier(store_id, product_id, order_date, predicted_daily_demand, current_stock) -> supplier_id
```

For an order on date `t`:

1. `days_coverage = current_stock / predicted_daily_demand`
2. Discard suppliers with `lead_time_days > days_coverage`. If none remain, take the lowest `lead_time_days` and flag the order `is_urgent = 1`.
3. `qty = max(min_order_qty, ceil(predicted_daily_demand x (lead_time_days + cycle_days)))`, with `cycle_days = min({CYCLE_DAYS}, shelf_life_days)`.
4. `sellable = predicted_daily_demand x shelf_life_days`; `waste = max(0, qty - sellable)`.
5. `expected_profit = (retail_price - unit_cost) x (qty - waste) - unit_cost x waste`; `score = expected_profit x reliability_score`.
6. Highest score wins; ties go to the lowest `lead_time_days` (then lowest `supplier_id`).

Prices are constant, so `store_id` and `order_date` do not change the result; they are kept for the dashboard.
`decide()` returns the full decision (supplier, quantity, lead time, urgency) for the alert
"Order [product] from [supplier] on [date]; it arrives on [date + lead_time_days]".

## Replenishment simulation (`scripts/dataset/simulate_operations.py`)

Each store x product is simulated day by day:

1. **Morning:** orders whose `actual_delivery_date` is today arrive as new lots; lots reaching `expiration_date` are removed (their `quantity_remaining` is the waste).
2. **Day:** sales consume stock first-expiring-first-out.
3. **Evening:** if `stock on hand + stock on order <= reorder_point`, an order is placed with `select_supplier`.
   `reorder_point = predicted_daily_demand x (median supplier lead time + safety_days)`, with
   `safety_days = {SAFETY_DAYS_PERISHABLE}` for products with shelf life <= 30 days and `{SAFETY_DAYS_STABLE}` otherwise.
   `predicted_daily_demand` is the noise-free expected demand averaged over the next 7 days (a stand-in for the
   model forecast) and is stored in `purchase_orders` together with `stock_at_order`, so any order can be re-checked.
4. Deliveries are late by 1-3 days with probability `1 - reliability_score`.

Resulting behavior: about 1.4 % of demand is lost to stockouts; perishables expire at realistic-to-high rates
(prepared foods ~24 %, fresh fruit ~28 %, flowers ~32 %, bakery ~4 %, dairy <1 %). Most perishable waste comes from the
quantity formula, which orders lead-time demand that existing stock already covers.

## Data dictionary

{chr(10).join(s + chr(10) for s in sections)}
## Validation

`python3 scripts/dataset/validate.py` checks foreign keys, store coordinates, supplier counts/prices/categories,
daily inventory balance, treatment/control validity, campaign pricing, ticket-to-sales reconciliation, leakage
guards, `select_supplier` reproducibility on 100 sampled orders, calendar/weather coverage, nulls, negative units,
lot dates and the sales grid size, and prints sanity checks.
"""
    (OUT_DIR / "README.md").write_text(text, encoding="utf-8")
    print(f"  wrote {OUT_DIR / 'README.md'}")


if __name__ == "__main__":
    main()

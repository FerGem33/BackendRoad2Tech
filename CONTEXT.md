# ROLE

You are a data engineer. Your job is to audit, clean, and expand a synthetic dataset of OXXO stores to train a Gradient Boosting model (LightGBM / XGBoost). You work in English and generate the data using reproducible Python scripts, not manually.

# PROJECT CONTEXT

- Softtek Road2Tech Hackathon. Challenge: improve store management.
- End user: a **regional manager** who oversees all OXXO stores in **Saltillo, Coahuila, Mexico**.
- Final product: a dashboard with a map of Saltillo showing one marker per store. Clicking a store opens a calendar with daily alerts:

  1. **Expiration:** "On [date], the batch of [product] expires."
  2. **Ordering / restocking:** "On [date], [product] must be ordered from supplier [X]; it arrives on [date + lead time]."
  3. **Demand spike:** "On [date], demand for [product/category] is expected to increase by +N% due to [event/weather]."

- The model predicts **daily demand per store and product**. Expiration and restocking alerts are derived from that prediction plus inventory and supplier lead time. Selecting the best supplier is an optimization rule (margin, lead time, minimum order quantity), not a model prediction.

# DATA LOCATION

- Input folder: `public/softtek/dataset/dataset-hackathon-road2tech/` (relative to the repository root; if it does not exist, search for it and report the actual path before proceeding).
- **Do not overwrite the original CSV files.** Write everything to `public/softtek/dataset/dataset-hackathon-road2tech/v2/`.
- Scripts must be placed in `scripts/dataset/` and must use a fixed random seed (`SEED = 42`) to ensure reproducibility.

# WORKFLOW (MANDATORY, IN THIS ORDER)

## Phase 0 — Audit (read-only)

Read all CSV files in the input folder and print a report to the terminal containing:

- For each file: filename, number of rows, columns and their data types, a sample of 3 rows, percentage of null values per column, and duplicates.
- Detected primary and foreign keys (e.g., `store_id`, `product_id`) and whether the relationships are consistent.
- Date range covered.
- Issues found (impossible values, negative prices, invalid dates, inconsistent categories, mixed English/Spanish column names, etc.).
- Which columns in the target schema (below) already exist, which are missing, and which are unnecessary.

## Phase 1 — Proposal (wait for my approval)

Based on the audit, propose:

- A mapping from the current CSV files to the target schema (what will be retained, renamed, transformed, or discarded).
- Additional categories, zones, or seasons you consider necessary, with a one-line justification for each.
- Estimated size of each output CSV (rows and MB).
- Any questions or contradictions you identify.

**Stop here and wait for my confirmation before writing any files.**

## Phase 2 — Generation

Implement the scripts and generate the CSV files according to the target schema.

## Phase 3 — Validation

Run the validations listed in the "ACCEPTANCE CRITERIA" section and show me the results. Generate `v2/README.md` containing the data dictionary (each file, each column, data type, unit, description, and example).

# TARGET SCHEMA (OUTPUT CSV FILES)

Conventions: column names in `snake_case`, dates in `YYYY-MM-DD` format, monetary values in MXN with 2 decimal places, UTF-8 encoding, comma delimiter. Date range: **2024-01-01 through 2026-09-30**, including the 2026 FIFA World Cup.

## Confirmed dimensions

- **25 stores**, each carrying the **same 120 products**.
- **Daily** granularity.
- 1,004 days × 25 stores × 120 products ≈ **3.0 million rows** in `sales.csv`.

## 1. `stores.csv` — one row per store (25 stores)

`store_id, store_name, colonia, latitude, longitude, zone_type, secondary_zone_type, size_category, opening_hours, is_24h, opened_date`

- Reassign all stores to **random locations within Saltillo's urban area** (approximately latitude 25.36–25.49, longitude −101.06 to −100.92). Verify that no point falls on hills or outside the city; use real Saltillo neighborhoods as references.
- Locations must be **consistent with the zone**: `carretera` stores near Highway 57, Periférico Luis Echeverría, or the exits toward Monterrey/Zacatecas; `escolar` stores near universities/schools (e.g., UAdeC, Tec de Monterrey); `industrial` stores toward Derramadero or industrial park areas; `residencial` stores in residential neighborhoods.
- Coordinates are **only for displaying the map**. The model uses `zone_type` and `secondary_zone_type` as features, not latitude/longitude.

## 2. `zones.csv` — zone catalog

`zone_type, description, peak_hours, weekend_multiplier, notes`

Confirmed zones: `residencial`, `empresarial`, `escolar`, `carretera`, `industrial`, `centro_comercial`, `hospital`.

Distribute the 25 stores realistically: most should be in `residencial`, with at least 2 stores in each of the other zones.

## 3. `categories.csv` — category catalog

`category_id, category_name, is_perishable, typical_shelf_life_days, requires_refrigeration`

Base categories: Cleaning Products, Groceries, Candy and Chocolate, Prepared Foods, Personal Hygiene.

Expand the catalog with categories found in real OXXO stores, such as Non-Alcoholic Beverages, Alcoholic Beverages, Snacks and Chips, Dairy and Refrigerated Products, Bakery and Cookies, Frozen Foods and Ice Cream, Ice, Coffee and Hot Beverages, Pharmacy (OTC), Cigarettes, Pet Supplies, Baby Products, Fruits and Vegetables.

## 4. `products.csv` — product catalog (exactly 120 products, the same across all stores)

Distribute the 120 products across all categories, assigning greater weight to categories that sell the most at OXXO (beverages, snacks, candy, beer, and prepared foods).

`product_id, product_name, category_id, brand, unit, shelf_life_days, retail_price, is_seasonal, weather_sensitivity, base_daily_demand`

- `weather_sensitivity`: `calor` (increases in hot weather: soft drinks, water, beer, ice cream, ice), `frio` (increases in cold weather: coffee, hot chocolate, instant soups, bread), `neutral`.
- `retail_price` must be realistic for Mexico in 2024–2026.

## 5. `suppliers.csv` — supplier catalog

`supplier_id, supplier_name, categories_served, city, reliability_score`

- Between **15 and 20 suppliers in total**. Each supplier serves **multiple categories** (separated by `|` in `categories_served`), as a real distributor would.
- Each category must have at least 3 suppliers serving it, so every product can have between 3 and 5 supplier options.
- `reliability_score` must range from 0.70 to 0.99 (probability of delivering on time).
- Use **fictional supplier names** (not real brands as suppliers) to avoid attributing invented prices to real companies.

## 6. `supplier_products.csv` — each supplier's product offering

`supplier_id, product_id, unit_cost, min_order_qty, lead_time_days, delivery_days`

- Each product must have **between 3 and 5 suppliers**, each with a distinct `unit_cost`.
- `unit_cost` must always be lower than `retail_price` (profit margin between 10% and 45%).
- A supplier may only offer products belonging to the categories listed in its `categories_served`.
- There must be a meaningful trade-off: the cheapest supplier is not always the one with the shortest lead time or lowest minimum order quantity.
- **Prices must remain fixed throughout the entire date range**: no inflation, promotions, or discounts. The same applies to `retail_price` in `products.csv`.

### Rule for selecting the best supplier

Document this rule in `v2/README.md` and implement it as a reusable function in `scripts/dataset/supplier_selection.py`, with the following signature:

`select_supplier(store_id, product_id, order_date, predicted_daily_demand, current_stock) -> supplier_id`

Use this function to generate `purchase_orders.csv` as well, so the order history remains consistent with the rule.

For an order of a product at a store on date `t`:

1. **Days of inventory coverage:**

   `days_coverage = current_stock / predicted_daily_demand`

2. **On-time delivery filter:** discard suppliers for which `lead_time_days > days_coverage` (they would arrive after the product has run out). If no supplier passes this filter, choose the supplier with the lowest `lead_time_days` and flag the order as `urgent`.

3. **Order quantity:**

   `qty = max(min_order_qty, predicted_daily_demand × (lead_time_days + cycle_days))`

   where `cycle_days = 7`.

4. **Expiration waste filter:** estimate

   `sellable = predicted_daily_demand × shelf_life_days`

   If `qty > sellable`, expected waste is `waste = qty − sellable`; otherwise, `waste = 0`.

5. **Score each remaining supplier:**

   ```
   expected_profit = (retail_price − unit_cost) × (qty − waste) − unit_cost × waste

   score = expected_profit × reliability_score
   ```

6. **Choose** the supplier with the highest `score`. In case of a tie, choose the one with the lowest `lead_time_days`.

The dashboard will use this same function to generate the alert: "Order [product] from [supplier] on [date]; it arrives on [date + lead_time_days]."

## 7. `calendar.csv` — one row per day in the date range

`date, day_of_week, is_weekend, is_holiday, holiday_name, season_name, event_name, event_scope, is_payday, is_school_vacation, demand_multiplier_hint`

Include at least the following events (add any missing ones):

- **Official holidays:** New Year's Day, Constitution Day (first Monday in February), Benito Juárez's Birthday (third Monday in March), Labor Day, Independence Day (September 15–16), Revolution Day (third Monday in November), Christmas.
- **Seasons and commercial dates:** Three Kings' Day (rosca), Candlemas, Super Bowl, Valentine's Day, Lent, Holy Week/Easter, Children's Day, Mother's Day (May 10), Father's Day, Back to School, Halloween, Day of the Dead, Buen Fin, Guadalupe-Reyes season (December 12–January 6), Christmas Eve, New Year's Eve.
- **Sports events:** 2026 FIFA World Cup (June 11–July 19, 2026, with Mexico match days explicitly marked), Liga MX playoffs.
- **Local Saltillo events:** city anniversary (July 25), Santo Cristo de la Capilla festivities (early August), Saltillo Fair.
- **Cycles:** biweekly paydays (the 15th and the last day of each month → `is_payday`); school vacations (which strongly affect the `escolar` zone).

## 8. `weather.csv` — daily weather in Saltillo

`date, temp_max_c, temp_min_c, precipitation_mm, weather_label`

- `weather_label`: `frio`, `templado`, `caluroso`, `lluvioso`.
- Weather must be realistic for Saltillo's semi-arid climate: summer highs around 30–34 °C, winter lows near 0 °C with occasional cold fronts, and rainfall concentrated from June through September.

## 9. `sales.csv` — sales facts (main training table)

`date, store_id, product_id, units_sold, revenue`

Generate demand using an explicit formula documented in the README, for example:

```
units = base_daily_demand
        × zone_multiplier[zone_type, category]
        × weekday_multiplier
        × weather_multiplier[weather_label, weather_sensitivity]
        × event_multiplier[event, category, zone_type]
        × payday_multiplier
        × annual_trend
        + noise (Poisson or normal distribution truncated at 0)
```

Business rules that must be reflected:

- **Hot weather** → increased sales of soft drinks, water, beer, ice cream, and ice. **Cold weather** → increased sales of coffee, hot chocolate, instant soups, and sweet bread.
- **Highway stores** → snacks, water, soft drinks, energy drinks, and coffee are the top sellers every day.
- **School-zone stores** → sharp declines during vacations and weekends.
- **Business/industrial zones** → peaks during weekday breakfast and lunch hours, with declines on weekends.
- **National holidays (September 15–16), the Super Bowl, and Mexico match days** → increased sales of beer, snacks, ice, sauces/dips, and soft drinks, with a stronger effect in the `residencial` zone.
- **Christmas/New Year's Eve** → increased sales of alcoholic beverages, 2–3 L soft drinks, ice, and disposable products.
- **Day of the Dead/Halloween** → candy sales increase. **Three Kings' Day** → bread sales increase. **Mother's Day/Valentine's Day** → chocolates and flowers sales increase.

## 10. `inventory_lots.csv` — inventory by batch (for expiration alerts)

`lot_id, store_id, product_id, supplier_id, received_date, quantity_received, expiration_date, quantity_remaining`

`expiration_date = received_date + shelf_life_days` for the product, with minor variation.

## 11. `purchase_orders.csv` — order history (for restocking alerts)

`order_id, store_id, product_id, supplier_id, order_date, quantity, unit_cost, expected_delivery_date, actual_delivery_date, is_urgent`

The `supplier_id` for each order must match the one returned by `select_supplier`. The `actual_delivery_date` is delayed by 1–3 days with probability `1 − reliability_score`.

Orders must be consistent with sales and inventory (orders are placed when stock falls below a reorder point).

# ACCEPTANCE CRITERIA (Phase 3)

- All foreign keys resolve (no orphaned `store_id`, `product_id`, or `supplier_id` values).
- All stores have coordinates within Saltillo.
- Every product has 3–5 suppliers with distinct prices and `unit_cost < retail_price`.
- There are between 15 and 20 suppliers; none offers products outside its `categories_served`.
- `unit_cost` and `retail_price` remain constant throughout the date range (no inflation or promotions).
- Re-running `select_supplier` on a sample of 100 orders from `purchase_orders.csv` returns the same `supplier_id` in 100% of cases.
- `calendar.csv` and `weather.csv` cover every day in the date range without gaps.
- No negative `units_sold` values or null values in mandatory columns.
- `inventory_lots`: `expiration_date > received_date`.
- Show 4 sanity checks with numerical results: beer sales on September 16 versus the average, coffee sales in January versus July, school-zone sales during vacations versus school days, and the top 5 products in highway-zone stores.

# CONSTRAINTS

- Do not invent real OXXO/FEMSA data (sales or exact locations); everything must be synthetic, and the README must explicitly state this.
- `sales.csv` should contain approximately 3.0 million rows (~100 MB). If the result differs significantly, notify me before proceeding.
- If any of these instructions contradict what you find in the CSV files, ask me instead of making assumptions.
# Road2Tech Hackathon Dataset v2 - OXXO Stores in Saltillo, Coahuila

> **This dataset is 100% synthetic.** It contains no real sales, inventory, prices, locations or supplier data
> from OXXO, FEMSA or any other company. Product brands are real consumer brands used only for realism, with
> approximate shelf prices; all supplier names are fictional; store coordinates are approximate points in real
> Saltillo neighborhoods; Mexico's 2026 World Cup knockout results are invented. Use it only for the hackathon.

## Overview

- **Period:** 2024-01-01 to 2026-09-30 (1,004 days, daily granularity, includes the 2026 FIFA World Cup)
- **Network:** 25 stores in Saltillo, 8 zone types, 120 products (same catalog in every store), 20 categories, 19 suppliers
- **Format:** CSV, UTF-8, comma delimiter, `snake_case` columns, dates `YYYY-MM-DD`, money in MXN with 2 decimals
- **Reproducible:** `python3 scripts/dataset/run_all.py` regenerates every file (fixed seed `SEED = 42`)

| File | Rows | Size | Content |
|---|---|---|---|
| `stores.csv` | 25 | 0.00 MB | One row per store (25 stores in Saltillo, Coahuila). |
| `zones.csv` | 8 | 0.00 MB | Zone catalog. |
| `categories.csv` | 20 | 0.00 MB | Category catalog (20 categories). |
| `products.csv` | 120 | 0.01 MB | Product catalog: 120 products, identical in every store. |
| `suppliers.csv` | 19 | 0.00 MB | Supplier catalog (19 fictional distributors). |
| `supplier_products.csv` | 405 | 0.01 MB | Each supplier's offer per product (3-5 offers per product). |
| `calendar.csv` | 1,004 | 0.06 MB | One row per day, 2024-01-01 to 2026-09-30. |
| `weather.csv` | 1,004 | 0.03 MB | Daily synthetic weather for Saltillo (semi-arid). |
| `sales.csv` | 3,012,000 | 89.38 MB | Main training table: full grid of date x store x product (zero-sale rows included). |
| `inventory_lots.csv` | 313,500 | 16.36 MB | One row per received lot (initial stock on 2024-01-01 plus every delivered order). |
| `purchase_orders.csv` | 311,517 | 24.81 MB | Order history generated with select_supplier(). |

### Relationships

```
stores.zone_type / secondary_zone_type -> zones.zone_type
products.category_id                    -> categories.category_id
supplier_products.(supplier_id, product_id) -> suppliers, products
sales.(store_id, product_id)            -> stores, products;   sales.date -> calendar.date, weather.date
inventory_lots / purchase_orders .(store_id, product_id, supplier_id) -> stores, products, suppliers
```

### Zones

`residential` (11 stores), and 2 stores each in `business`, `school`, `college`, `highway`, `industrial`,
`shopping_center` and `hospital`. `school` (K-12, SEP calendar) and `college` (universities) are separate on purpose:
school stores over-index on candy, snacks and juice and sell almost no alcohol or cigarettes, while college stores
over-index on alcohol (with a Thursday-Saturday peak), energy drinks, coffee and cigarettes, and follow the
university vacation calendar (`calendar.is_college_vacation`).

## Demand generation (`scripts/dataset/build_demand.py`)

```
expected_units = base_daily_demand
                 x store_scale[size_category] x store_product_affinity (lognormal, sigma 0.15)
                 x zone_multiplier[zone_type, category/tag]   (0.7 primary + 0.3 secondary zone)
                 x weekday_multiplier[zone_type, weekday] x category_weekday[category, weekday]
                 x weather_multiplier[weather_label, weather_sensitivity] x temperature_slope
                 x event_multiplier[event, category/tag, zone_type]
                 x annual_trend (+3.5 % volume per year; prices never change)
demand        = Poisson(expected_units x Gamma(shape=8, scale=1/8))
units_sold    = min(demand, stock on hand)
```

Business rules encoded (exact factors are in `EVENT_RULES`, `ZONE_MULT`, `WEATHER_MULT` in the script):

- **Weather:** hot days lift soft drinks, water, beer, ice cream and ice (x1.30 + 2 %/°C above 26 °C); cold days lift
  coffee, hot chocolate, instant soups and sweet bread (x1.40); rain trims traffic.
- **Highway:** snacks, water, soft drinks, energy drinks and coffee lead every day; long weekends, Holy Week and summer vacation add traffic.
- **School / college:** sharp drops on weekends and during their own vacations; school-zone teacher-council Fridays behave like days off.
- **Business / industrial:** weekday breakfast/lunch profile (coffee, prepared foods), weekend and holiday drop.
- **Parties** (Sep 15-16, Super Bowl, Mexico match days, Liga MX playoffs, Father's Day): beer, snacks, ice, dips and soft
  drinks, strongest in `residential` and `college`.
- **Christmas Eve / New Year's Eve:** alcohol, 2.5-3 L soft drinks, ice and disposables; hangover products the day after.
- **Halloween / Day of the Dead:** candy (and flowers, sweet bread). **Three Kings' Day:** bakery, Three Kings' cake (sold only Dec 26-Jan 10), hot chocolate.
  **Candlemas:** tamales. **Valentine's / Mother's Day:** chocolates and flowers.
- **Paydays** (15th and last day, plus the next day): +6 % traffic, +12 % alcohol. **Dry law** on the June 2024 election weekend.

## Best-supplier rule (`scripts/dataset/supplier_selection.py`)

```python
from supplier_selection import select_supplier
select_supplier(store_id, product_id, order_date, predicted_daily_demand, current_stock) -> supplier_id
```

For an order on date `t`:

1. `days_coverage = current_stock / predicted_daily_demand`
2. Discard suppliers with `lead_time_days > days_coverage`. If none remain, take the lowest `lead_time_days` and flag the order `is_urgent = 1`.
3. `qty = max(min_order_qty, ceil(predicted_daily_demand x (lead_time_days + cycle_days)))`, with `cycle_days = min(7, shelf_life_days)`.
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
   `safety_days = 1` for products with shelf life <= 30 days and `3` otherwise.
   `predicted_daily_demand` is the noise-free expected demand averaged over the next 7 days (a stand-in for the
   model forecast) and is stored in `purchase_orders` together with `stock_at_order`, so any order can be re-checked.
4. Deliveries are late by 1-3 days with probability `1 - reliability_score`.

Resulting behavior: about 1.4 % of demand is lost to stockouts; perishables expire at realistic-to-high rates
(prepared foods ~24 %, fresh fruit ~28 %, flowers ~32 %, bakery ~4 %, dairy <1 %). Most perishable waste comes from the
quantity formula, which orders lead-time demand that existing stock already covers.

## Data dictionary

### `stores.csv`

One row per store (25 stores in Saltillo, Coahuila).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `store_id` | string | - | Store key (S001-S025). | `S001` |
| `store_name` | string | - | Display name. | `OXXO República Oriente` |
| `colonia` | string | - | Saltillo neighborhood or landmark used as the location reference. | `República Oriente` |
| `latitude` | float | degrees | Approximate latitude, map display only (not a model feature). | `25.430881` |
| `longitude` | float | degrees | Approximate longitude, map display only (not a model feature). | `-100.983762` |
| `zone_type` | string | - | Primary zone, FK to zones.zone_type. Model feature. | `residential` |
| `secondary_zone_type` | string (nullable) | - | Secondary zone for mixed areas (30% weight in demand). Model feature. | `school` |
| `size_category` | string | - | small / medium / large (demand scale 0.8 / 1.0 / 1.25). | `medium` |
| `opening_hours` | string | HH:MM-HH:MM | Daily opening hours. | `00:00-24:00` |
| `is_24h` | int (0/1) | - | 1 if the store opens 24 hours. | `1` |
| `opened_date` | date | YYYY-MM-DD | Opening date (all before 2024-01-01). | `2022-03-26` |

### `zones.csv`

Zone catalog.

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `zone_type` | string | - | Zone key. | `residential` |
| `description` | string | - | Who shops there. | `Housing neighborhoods; neighbors buy daily groceries, drinks and party supplies.` |
| `peak_hours` | string | HH:MM-HH:MM, `|`-separated | Typical peak windows (informational; data is daily). | `07:00-09:00|18:00-22:00` |
| `weekend_multiplier` | float | x | Average Saturday/Sunday demand multiplier vs. 1.0. | `1.11` |
| `notes` | string | - | Key demand behavior. | `Strongest reaction to parties, matches and holidays.` |

### `categories.csv`

Category catalog (20 categories).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `category_id` | string | - | Category key (C01-C20). | `C01` |
| `category_name` | string | - | Category name. | `Non-Alcoholic Beverages` |
| `is_perishable` | int (0/1) | - | 1 for short-life fresh categories. | `0` |
| `typical_shelf_life_days` | int | days | Typical shelf life in the category. | `180` |
| `requires_refrigeration` | int (0/1) | - | 1 if the category needs cold storage. | `0` |

### `products.csv`

Product catalog: 120 products, identical in every store.

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `product_id` | string | - | Product key (P001-P120). | `P001` |
| `product_name` | string | - | Product name. | `Coca-Cola 600 ml` |
| `category_id` | string | - | FK to categories.category_id. | `C01` |
| `brand` | string | - | Consumer brand (Private Label for store-made items). | `Coca-Cola` |
| `unit` | string | - | Selling unit (bottle, can, pack, piece, cup...). | `bottle` |
| `shelf_life_days` | int | days | Days from receipt to expiration. | `180` |
| `retail_price` | float | MXN | Shelf price, fixed for the whole period. | `20.00` |
| `is_seasonal` | int (0/1) | - | 1 if demand is concentrated in a season (Three Kings' cake, roses). | `0` |
| `weather_sensitivity` | string | - | hot (sells more in heat) / cold (sells more in cold) / neutral. | `hot` |
| `base_daily_demand` | float | units/day | Expected units/day in a medium store on a neutral day (before multipliers). | `30.00` |

### `suppliers.csv`

Supplier catalog (19 fictional distributors).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `supplier_id` | string | - | Supplier key (SUP01-SUP19). | `SUP01` |
| `supplier_name` | string | - | Fictional supplier name. | `Northern Beverage Distributors` |
| `categories_served` | string | `|`-separated category_id | Categories the supplier may sell. | `C01|C02|C11` |
| `city` | string | - | Supplier base city. | `Saltillo` |
| `reliability_score` | float | probability | Probability of on-time delivery (0.70-0.99). | `0.78` |

### `supplier_products.csv`

Each supplier's offer per product (3-5 offers per product).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `supplier_id` | string | - | FK to suppliers. | `SUP01` |
| `product_id` | string | - | FK to products. | `P001` |
| `unit_cost` | float | MXN | Purchase cost per unit, fixed; margin vs. retail 10-45%. | `13.65` |
| `min_order_qty` | int | units | Minimum order quantity. | `144` |
| `lead_time_days` | int | days | Days from order to expected delivery. | `3` |
| `delivery_days` | string | `|`-separated weekdays | Supplier route days (informational; lead_time_days already accounts for it). | `Tue|Fri` |

### `calendar.csv`

One row per day, 2024-01-01 to 2026-09-30.

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `date` | date | YYYY-MM-DD | Day. | `2024-01-01` |
| `day_of_week` | string | - | Monday..Sunday. | `Monday` |
| `is_weekend` | int (0/1) | - | Saturday or Sunday. | `0` |
| `is_holiday` | int (0/1) | - | Official rest day (Federal Labor Law). | `1` |
| `holiday_name` | string (nullable) | - | Official holiday name. | `New Year's Day` |
| `season_name` | string | - | winter / spring / summer / autumn. | `winter` |
| `event_name` | string (nullable) | `|`-separated | Commercial, religious, sports, local and school events active that day. | `Guadalupe-Reyes Season` |
| `event_scope` | string (nullable) | `|`-separated | Scope of each event in event_name (national, local, sports, commercial, religious, school). | `commercial` |
| `is_payday` | int (0/1) | - | 15th or last day of the month. | `0` |
| `is_school_vacation` | int (0/1) | - | SEP (K-12) vacation; drives the school zone. | `1` |
| `is_college_vacation` | int (0/1) | - | University vacation; drives the college zone. | `1` |
| `demand_multiplier_hint` | float | x | Network-wide weighted average of calendar effects (events, vacations, paydays) for the day. | `0.716` |

### `weather.csv`

Daily synthetic weather for Saltillo (semi-arid).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `date` | date | YYYY-MM-DD | Day. | `2024-01-01` |
| `temp_max_c` | float | °C | Maximum temperature. | `19.2` |
| `temp_min_c` | float | °C | Minimum temperature. | `3.5` |
| `precipitation_mm` | float | mm | Daily rainfall. | `0.0` |
| `weather_label` | string | - | rainy (>= 5 mm) / hot (max >= 30 °C) / cold (max < 17 °C or min <= 1 °C) / mild. | `mild` |

### `sales.csv`

Main training table: full grid of date x store x product (zero-sale rows included).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `date` | date | YYYY-MM-DD | Day. | `2024-01-01` |
| `store_id` | string | - | FK to stores. | `S001` |
| `product_id` | string | - | FK to products. | `P001` |
| `units_sold` | int | units | Units sold = min(demand, stock on hand). Target variable. | `14` |
| `revenue` | float | MXN | units_sold x retail_price. | `280.00` |

### `inventory_lots.csv`

One row per received lot (initial stock on 2024-01-01 plus every delivered order).

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `lot_id` | string | - | Lot key. | `L0000001` |
| `store_id` | string | - | FK to stores. | `S001` |
| `product_id` | string | - | FK to products. | `P001` |
| `supplier_id` | string | - | FK to suppliers (supplier that delivered the lot). | `SUP14` |
| `received_date` | date | YYYY-MM-DD | Delivery date (= purchase_orders.actual_delivery_date). | `2024-01-01` |
| `quantity_received` | int | units | Units received. | `186` |
| `expiration_date` | date | YYYY-MM-DD | received_date + shelf_life_days (+/- small variation). Sellable before this date. | `2024-07-03` |
| `quantity_remaining` | int | units | Units left when the lot expired (= waste) or, for lots still valid on 2026-09-30, units on the shelf that day. | `0` |

### `purchase_orders.csv`

Order history generated with select_supplier().

| Column | Type | Unit | Description | Example |
|---|---|---|---|---|
| `order_id` | string | - | Order key. | `PO0000001` |
| `store_id` | string | - | FK to stores. | `S015` |
| `product_id` | string | - | FK to products. | `P106` |
| `supplier_id` | string | - | Supplier returned by select_supplier(). | `SUP05` |
| `order_date` | date | YYYY-MM-DD | Day the order was placed (end of day). | `2024-01-01` |
| `quantity` | int | units | max(min_order_qty, ceil(predicted x (lead_time + cycle_days))). | `12` |
| `unit_cost` | float | MXN | Catalog unit cost of the chosen supplier. | `53.13` |
| `expected_delivery_date` | date | YYYY-MM-DD | order_date + lead_time_days. | `2024-01-03` |
| `actual_delivery_date` | date (nullable) | YYYY-MM-DD | Expected date, delayed 1-3 days with probability 1 - reliability_score. Empty if after 2026-09-30 (in transit). | `2024-01-05` |
| `is_urgent` | int (0/1) | - | 1 if no supplier could deliver before the stock ran out. | `0` |
| `predicted_daily_demand` | float | units/day | Forecast passed to select_supplier (noise-free expected demand, next 7 days). | `0.2467` |
| `stock_at_order` | int | units | Stock on hand passed to select_supplier as current_stock. | `1` |

## Validation

`python3 scripts/dataset/validate.py` checks foreign keys, store coordinates, supplier counts/prices/categories,
constant prices, `select_supplier` reproducibility on 100 sampled orders, calendar/weather coverage, nulls,
negative units, lot dates and the sales grid size, and prints the sanity checks.

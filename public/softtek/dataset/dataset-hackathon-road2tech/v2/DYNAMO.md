# Modelo de datos en DynamoDB — Dashboard regional de tiendas (Saltillo)

Este documento describe cómo se deben ver los datos del dataset `v2/` dentro de **Amazon DynamoDB** para soportar
el sistema descrito en `CONTEXT.md` (raíz del repositorio):

- Un **mapa de Saltillo** con un marcador por tienda (25 tiendas).
- Al hacer clic en una tienda, un **calendario con alertas diarias**:
  1. **Caducidad:** "On [date], the batch of [product] expires."
  2. **Pedido / reabasto:** "On [date], [product] must be ordered from supplier [X]; it arrives on [date + lead time]."
  3. **Pico de demanda:** "On [date], demand for [product/category] is expected to increase by +N% due to [event/weather]."
- Las alertas se derivan de la **predicción de demanda diaria por tienda × producto** (LightGBM / XGBoost),
  el **inventario por lote** y el **lead time del proveedor**. El proveedor se elige con la regla
  `select_supplier()` (`scripts/dataset/supplier_selection.py`), no con el modelo.

> DynamoDB guarda el **estado operativo y lo que consume el dashboard**. El histórico completo (`sales.csv`,
> ~3 M filas, y el histórico de lotes/pedidos) vive en **S3 + Athena** para entrenamiento y análisis, tal como
> define `ARCHITECTURE.md`. DynamoDB sólo conserva una ventana reciente de ventas.

---

## 1. Decisiones de diseño

| Decisión | Valor |
|---|---|
| Patrón | **Single-table design** (una tabla, entidades distinguidas por prefijos de llave) |
| Nombre de tabla | `road2tech-stores` |
| Llave primaria | `PK` (string, partition key) + `SK` (string, sort key) |
| Índices | `GSI1` (`GSI1PK`, `GSI1SK`) y `GSI2` (`GSI2PK`, `GSI2SK`), proyección `ALL` |
| Capacidad | `PAY_PER_REQUEST` (on-demand) |
| TTL | Atributo `ttl` (epoch en segundos) para ventas, pronósticos, alertas y lotes cerrados |
| Streams | `NEW_AND_OLD_IMAGES` → Lambda que recalcula alertas cuando cambia stock, lote o pedido |
| Convenciones | Atributos en `snake_case` (igual que los CSV), fechas `YYYY-MM-DD`, dinero en MXN (`Number`, 2 decimales), IDs iguales a los del dataset (`S001`, `P001`, `SUP01`, `C01`, `L0000001`, `PO0000001`) |
| Atributo común | `entity_type` en todos los ítems (`STORE`, `PRODUCT`, `OFFER`, `STOCK`, `LOT`, `PO`, `FORECAST`, `ALERT`, …) |

¿Por qué una sola tabla? Las consultas del dashboard son siempre "todo lo de una tienda en un rango de fechas" o
"todo lo de un producto/proveedor". Agrupar esos ítems bajo la misma `PK` permite resolver cada pantalla con
**una sola `Query`**, sin `Scan` ni joins.

---

## 2. Patrones de acceso

| # | Pantalla / proceso | Operación |
|---|---|---|
| AP1 | Mapa: listar las 25 tiendas con coordenadas y zona | `Query GSI1` `GSI1PK = STORE` |
| AP2 | Mapa: color del marcador según alertas del día | `Query GSI1` `GSI1PK = ALERTDAY#<date>` (25 ítems resumen) |
| AP3 | Calendario de la tienda: contadores por día en el mes | `Query` `PK = STORE#<id>`, `SK BETWEEN ALERTDAY#<from> AND ALERTDAY#<to>` |
| AP4 | Detalle de un día en el calendario | `Query` `PK = STORE#<id>`, `SK begins_with ALERT#<date>` |
| AP5 | Alertas de un tipo en un rango para una tienda | AP4 con rango + `FilterExpression alert_type = :t` (o `SK begins_with ALERT#<date>#REORDER`) |
| AP6 | Vista regional: todas las alertas de un día (todas las tiendas) | `Query GSI1` `GSI1PK = ALERTS#<date>` (opcional `begins_with GSI1SK = <TYPE>#`) |
| AP7 | Stock actual de todos los productos de una tienda | `Query` `PK = STORE#<id>`, `SK begins_with STOCK#` |
| AP8 | Lotes activos de una tienda ordenados por caducidad | `Query` `PK = STORE#<id>`, `SK BETWEEN LOT#<today> AND LOT#<today+N>` |
| AP9 | Línea de tiempo de un producto en una tienda (ventas, pronóstico, lotes, pedidos) | `Query GSI2` `GSI2PK = STORE#<id>#PRODUCT#<pid>`, `GSI2SK begins_with SALE# / FCST# / LOT# / PO#` |
| AP10 | `select_supplier()`: producto + sus 3–5 ofertas | `Query` `PK = PRODUCT#<pid>` (devuelve `#META` + `OFFER#*`) |
| AP11 | Catálogo de productos / proveedores | `Query GSI1` `GSI1PK = PRODUCT` / `SUPPLIER` |
| AP12 | Productos que ofrece un proveedor y sus pedidos | `Query GSI1` `GSI1PK = SUPPLIER#<sid>` (`OFFER#*`, `PO#*`) |
| AP13 | Pedidos de una tienda (recientes / en tránsito) | `Query` `PK = STORE#<id>`, `SK begins_with PO#`, `ScanIndexForward = false` |
| AP14 | Entregas esperadas en un día (toda la red) | `Query GSI2` `GSI2PK = DELIVERY#<date>` |
| AP15 | Calendario + clima para un rango (features del modelo y texto de las alertas) | `Query` `PK = CALENDAR`, `SK BETWEEN DATE#<from> AND DATE#<to>` |
| AP16 | Pronóstico de la tienda para un día / horizonte | `Query` `PK = STORE#<id>#FCST`, `SK BETWEEN <from> AND <to>~` |
| AP17 | Ventas recientes de una tienda (KPIs, gráfica) | `Query` `PK = STORE#<id>#SALES`, `SK BETWEEN DAY#<from> AND DAY#<to>` |
| AP18 | Zonas y categorías (catálogos pequeños) | `Query` `PK = CATALOG`, `SK begins_with ZONE# / CATEGORY#` |

---

## 3. Mapa de llaves por entidad

| Entidad | `PK` | `SK` | `GSI1PK` | `GSI1SK` | `GSI2PK` | `GSI2SK` | Origen |
|---|---|---|---|---|---|---|---|
| Zona | `CATALOG` | `ZONE#<zone_type>` | – | – | – | – | `zones.csv` |
| Categoría | `CATALOG` | `CATEGORY#<category_id>` | – | – | – | – | `categories.csv` |
| Tienda | `STORE#<store_id>` | `#META` | `STORE` | `ZONE#<zone_type>#<store_id>` | – | – | `stores.csv` |
| Producto | `PRODUCT#<product_id>` | `#META` | `PRODUCT` | `CATEGORY#<category_id>#<product_id>` | – | – | `products.csv` |
| Proveedor | `SUPPLIER#<supplier_id>` | `#META` | `SUPPLIER` | `<supplier_id>` | – | – | `suppliers.csv` |
| Oferta proveedor-producto | `PRODUCT#<product_id>` | `OFFER#<supplier_id>` | `SUPPLIER#<supplier_id>` | `OFFER#<product_id>` | – | – | `supplier_products.csv` |
| Día (calendario + clima) | `CALENDAR` | `DATE#<date>` | – | – | – | – | `calendar.csv` + `weather.csv` |
| Stock actual | `STORE#<store_id>` | `STOCK#<product_id>` | – | – | `STORE#<store_id>#PRODUCT#<product_id>` | `STOCK` | derivado de `inventory_lots` / `purchase_orders` |
| Lote | `STORE#<store_id>` | `LOT#<expiration_date>#<product_id>#<lot_id>` | – | – | `STORE#<store_id>#PRODUCT#<product_id>` | `LOT#<expiration_date>#<lot_id>` | `inventory_lots.csv` |
| Pedido | `STORE#<store_id>` | `PO#<order_date>#<order_id>` | `SUPPLIER#<supplier_id>` | `PO#<order_date>#<order_id>` | `DELIVERY#<expected_delivery_date>` *(sólo si está en tránsito)* | `STORE#<store_id>#<order_id>` | `purchase_orders.csv` |
| Venta diaria (línea) | `STORE#<store_id>#SALES` | `LINE#<date>#<product_id>` | – | – | `STORE#<store_id>#PRODUCT#<product_id>` | `SALE#<date>` | `sales.csv` (ventana reciente) |
| Venta diaria (resumen tienda) | `STORE#<store_id>#SALES` | `DAY#<date>` | – | – | – | – | agregado de `sales.csv` |
| Pronóstico | `STORE#<store_id>#FCST` | `<date>#<product_id>` | – | – | `STORE#<store_id>#PRODUCT#<product_id>` | `FCST#<date>` | salida del modelo |
| Alerta | `STORE#<store_id>` | `ALERT#<alert_date>#<alert_type>#<target_id>[#<ref_id>]` | `ALERTS#<alert_date>` | `<alert_type>#<store_id>#<target_id>` | – | – | motor de alertas |
| Resumen de alertas por día | `STORE#<store_id>` | `ALERTDAY#<date>` | `ALERTDAY#<date>` | `STORE#<store_id>` | – | – | motor de alertas |

Notas:

- `GSI2` en `PO` es **sparse**: `GSI2PK/GSI2SK` sólo existen mientras el pedido no se ha entregado; al recibirlo se
  eliminan con `REMOVE GSI2PK, GSI2SK`, así AP14 sólo devuelve pedidos en tránsito.
- Ventas y pronósticos usan una `PK` separada de la tienda (`#SALES`, `#FCST`) para que las consultas del calendario
  (`ALERT#`, `STOCK#`, `LOT#`) no recorran cientos de miles de ítems de serie de tiempo.
- `<target_id>` en alertas es `product_id` (`P001`) o `category_id` (`C02`) en picos de demanda por categoría.
  `<ref_id>` es el `lot_id` en caducidad, para que dos lotes del mismo producto que caducan el mismo día no choquen.
- La `SK` de alerta es **determinística**: regenerar alertas hace `PutItem` sobre la misma llave (idempotente),
  sin duplicados.

---

## 4. Atributos por entidad

Los atributos son los mismos que las columnas del CSV de origen, salvo los marcados como *derivados*.

### 4.1 Zona — `PK=CATALOG`, `SK=ZONE#residential`

```json
{
  "PK": "CATALOG", "SK": "ZONE#residential", "entity_type": "ZONE",
  "zone_type": "residential",
  "description": "Housing neighborhoods; neighbors buy daily groceries, drinks and party supplies.",
  "peak_hours": ["07:00-09:00", "18:00-22:00"],
  "weekend_multiplier": 1.11,
  "notes": "Strongest reaction to parties, matches and holidays."
}
```

Zonas: `residential`, `business`, `school`, `college`, `highway`, `industrial`, `shopping_center`, `hospital`.

### 4.2 Categoría — `PK=CATALOG`, `SK=CATEGORY#C01`

```json
{
  "PK": "CATALOG", "SK": "CATEGORY#C01", "entity_type": "CATEGORY",
  "category_id": "C01", "category_name": "Non-Alcoholic Beverages",
  "is_perishable": false, "typical_shelf_life_days": 180, "requires_refrigeration": false
}
```

### 4.3 Tienda — `PK=STORE#S001`, `SK=#META`

```json
{
  "PK": "STORE#S001", "SK": "#META", "entity_type": "STORE",
  "GSI1PK": "STORE", "GSI1SK": "ZONE#residential#S001",
  "store_id": "S001", "store_name": "OXXO República Oriente", "colonia": "República Oriente",
  "latitude": 25.430881, "longitude": -100.983762,
  "zone_type": "residential", "secondary_zone_type": null,
  "size_category": "medium", "opening_hours": "00:00-24:00", "is_24h": true,
  "opened_date": "2022-03-26"
}
```

`latitude`/`longitude` son sólo para el mapa; el modelo usa `zone_type` y `secondary_zone_type`.

### 4.4 Producto — `PK=PRODUCT#P001`, `SK=#META`

```json
{
  "PK": "PRODUCT#P001", "SK": "#META", "entity_type": "PRODUCT",
  "GSI1PK": "PRODUCT", "GSI1SK": "CATEGORY#C01#P001",
  "product_id": "P001", "product_name": "Coca-Cola 600 ml",
  "category_id": "C01", "category_name": "Non-Alcoholic Beverages",
  "brand": "Coca-Cola", "unit": "bottle", "shelf_life_days": 180,
  "retail_price": 20.00, "is_seasonal": false,
  "weather_sensitivity": "hot", "base_daily_demand": 30.00
}
```

`category_name` está desnormalizado (derivado) para pintar alertas sin otra lectura.

### 4.5 Proveedor — `PK=SUPPLIER#SUP01`, `SK=#META`

```json
{
  "PK": "SUPPLIER#SUP01", "SK": "#META", "entity_type": "SUPPLIER",
  "GSI1PK": "SUPPLIER", "GSI1SK": "SUP01",
  "supplier_id": "SUP01", "supplier_name": "Northern Beverage Distributors",
  "email": "northern.beverage.distributors@gmail.com",
  "categories_served": ["C01", "C02", "C11"],
  "city": "Saltillo", "reliability_score": 0.78
}
```

`categories_served` pasa de texto `C01|C02|C11` a lista (o `StringSet`).

### 4.6 Oferta proveedor-producto — `PK=PRODUCT#P001`, `SK=OFFER#SUP01`

```json
{
  "PK": "PRODUCT#P001", "SK": "OFFER#SUP01", "entity_type": "OFFER",
  "GSI1PK": "SUPPLIER#SUP01", "GSI1SK": "OFFER#P001",
  "supplier_id": "SUP01", "product_id": "P001",
  "unit_cost": 13.65, "min_order_qty": 144, "lead_time_days": 3,
  "delivery_days": ["Tue", "Fri"],
  "supplier_name": "Northern Beverage Distributors",
  "supplier_email": "northern.beverage.distributors@gmail.com",
  "reliability_score": 0.78,
  "margin_pct": 0.3175
}
```

`supplier_name`, `supplier_email`, `reliability_score` y `margin_pct` (= `1 − unit_cost / retail_price`) son
desnormalizados: con **una sola `Query` (AP10)** se obtienen todos los datos que `select_supplier()` necesita
(`retail_price`, `shelf_life_days`, `unit_cost`, `min_order_qty`, `lead_time_days`, `reliability_score`).
Los precios son fijos en todo el periodo, así que la desnormalización no se desactualiza.

### 4.7 Día (calendario + clima) — `PK=CALENDAR`, `SK=DATE#2026-06-11`

```json
{
  "PK": "CALENDAR", "SK": "DATE#2026-06-11", "entity_type": "DAY",
  "date": "2026-06-11", "day_of_week": "Thursday", "is_weekend": false,
  "is_holiday": false, "holiday_name": null, "season_name": "summer",
  "events": [
    {"event_name": "FIFA World Cup 2026", "event_scope": "sports"},
    {"event_name": "Mexico Match Day", "event_scope": "sports"}
  ],
  "is_payday": false, "is_school_vacation": false, "is_college_vacation": true,
  "demand_multiplier_hint": 1.214,
  "temp_max_c": 31.4, "temp_min_c": 17.9, "precipitation_mm": 0.0, "weather_label": "hot",
  "is_forecast": false
}
```

- Une `calendar.csv` y `weather.csv` por `date` (1:1). `event_name|event_scope` (separados por `|`) se convierten
  en la lista `events`.
- 1,004 ítems históricos (2024-01-01 → 2026-09-30) + los días del horizonte de pronóstico con `is_forecast = true`
  (clima pronosticado y sin `demand_multiplier_hint`).
- Toda la tabla de calendario cabe en una partición (~1 KB × ~1,020 ítems).

### 4.8 Stock actual — `PK=STORE#S001`, `SK=STOCK#P001`

Un ítem por tienda × producto (25 × 120 = **3,000 ítems**). Es el estado vivo que alimenta la regla de reabasto.

```json
{
  "PK": "STORE#S001", "SK": "STOCK#P001", "entity_type": "STOCK",
  "GSI2PK": "STORE#S001#PRODUCT#P001", "GSI2SK": "STOCK",
  "store_id": "S001", "product_id": "P001", "product_name": "Coca-Cola 600 ml", "category_id": "C01",
  "on_hand": 42,
  "on_order": 144,
  "open_order_ids": ["PO0311207"],
  "reorder_point": 96.5,
  "predicted_daily_demand": 31.2,
  "days_coverage": 1.35,
  "next_expiration_date": "2027-03-02",
  "active_lots": 2,
  "last_sale_date": "2026-09-30",
  "as_of_date": "2026-09-30",
  "updated_at": "2026-09-30T23:59:00Z",
  "version": 1532
}
```

- `on_hand` = suma de `quantity_remaining` de los lotes activos.
- `on_order` = suma de `quantity` de los pedidos sin `actual_delivery_date`.
- `reorder_point = predicted_daily_demand × (lead time mediano + safety_days)` (igual que `simulate_operations.py`).
- `days_coverage = on_hand / predicted_daily_demand` (paso 1 de `select_supplier`).
- `version` se usa para **bloqueo optimista** (`ConditionExpression version = :v`) cuando ventas y recepciones
  actualizan el mismo producto.

### 4.9 Lote — `PK=STORE#S001`, `SK=LOT#2026-10-04#P059#L0313122`

```json
{
  "PK": "STORE#S001", "SK": "LOT#2026-10-04#P059#L0313122", "entity_type": "LOT",
  "GSI2PK": "STORE#S001#PRODUCT#P059", "GSI2SK": "LOT#2026-10-04#L0313122",
  "lot_id": "L0313122", "store_id": "S001", "product_id": "P059", "product_name": "Ham Sandwich",
  "supplier_id": "SUP07", "order_id": "PO0310877",
  "received_date": "2026-09-30", "quantity_received": 12,
  "expiration_date": "2026-10-04", "quantity_remaining": 9,
  "unit_cost": 21.40, "value_at_risk": 192.60,
  "status": "ACTIVE",
  "ttl": null
}
```

- La `SK` empieza con `expiration_date`: AP8 devuelve, en orden, los lotes que caducan en los próximos N días
  de **todos** los productos de la tienda (fuente de las alertas de caducidad).
- `GSI2` ordena los lotes de un producto por caducidad → consumo **FEFO** (first-expired-first-out) al registrar ventas.
- `status`: `ACTIVE` → `DEPLETED` (`quantity_remaining = 0`) o `EXPIRED` (llegó a `expiration_date`; lo que quede es merma).
  Al cerrar el lote se fija `ttl = expiration_date + 30 días`; el histórico completo queda en S3.
- `order_id`, `unit_cost` y `value_at_risk` (= `quantity_remaining × unit_cost`) son derivados.
- Carga inicial: sólo lotes con `expiration_date > 2026-09-30` y `quantity_remaining > 0` (**3,711 lotes**).

### 4.10 Pedido — `PK=STORE#S015`, `SK=PO#2026-09-29#PO0311402`

```json
{
  "PK": "STORE#S015", "SK": "PO#2026-09-29#PO0311402", "entity_type": "PO",
  "GSI1PK": "SUPPLIER#SUP05", "GSI1SK": "PO#2026-09-29#PO0311402",
  "GSI2PK": "DELIVERY#2026-10-01", "GSI2SK": "STORE#S015#PO0311402",
  "order_id": "PO0311402", "store_id": "S015", "product_id": "P106", "product_name": "...",
  "supplier_id": "SUP05", "supplier_name": "...", "supplier_email": "...",
  "order_date": "2026-09-29", "quantity": 12, "unit_cost": 53.13, "total_cost": 637.56,
  "lead_time_days": 2,
  "expected_delivery_date": "2026-10-01", "actual_delivery_date": null,
  "is_urgent": false,
  "predicted_daily_demand": 0.2467, "stock_at_order": 1,
  "status": "IN_TRANSIT",
  "source_alert_sk": "ALERT#2026-09-29#REORDER#P106"
}
```

- `status`: `SUGGESTED` (creado desde una alerta, aún sin confirmar) → `PLACED` / `IN_TRANSIT` → `DELIVERED` o `LATE`
  (si `today > expected_delivery_date` y no ha llegado).
- `predicted_daily_demand` y `stock_at_order` se conservan para poder re-ejecutar `select_supplier()` y auditar la decisión.
- Carga inicial: pedidos en tránsito (**1,015**, `actual_delivery_date` vacío) + los últimos 90 días de historial
  (~28 k) con `ttl`. El resto del histórico (311,517 pedidos) queda en S3.

### 4.11 Ventas — `PK=STORE#S001#SALES`

Línea por tienda × producto × día:

```json
{
  "PK": "STORE#S001#SALES", "SK": "LINE#2026-09-30#P001", "entity_type": "SALE",
  "GSI2PK": "STORE#S001#PRODUCT#P001", "GSI2SK": "SALE#2026-09-30",
  "date": "2026-09-30", "store_id": "S001", "product_id": "P001",
  "units_sold": 14, "revenue": 280.00,
  "ttl": 1801353600
}
```

Resumen por tienda × día (para KPIs y la gráfica del panel de la tienda):

```json
{
  "PK": "STORE#S001#SALES", "SK": "DAY#2026-09-30", "entity_type": "SALE_DAY",
  "date": "2026-09-30", "store_id": "S001",
  "units_sold": 1873, "revenue": 41235.50,
  "revenue_by_category": {"C01": 9820.00, "C02": 7310.50, "...": 0},
  "stockout_products": 3
}
```

- **Ventana en DynamoDB: últimos 90 días** → 25 × 120 × 90 = **270,000** líneas + 2,250 resúmenes.
  `ttl = date + 90 días`. Para la demo con datos estáticos se puede cargar sin `ttl` o cargar los 1,004 días
  (3.0 M ítems, ~0.5 GB) si se requiere; el entrenamiento siempre lee de S3/Athena.

### 4.12 Pronóstico — `PK=STORE#S001#FCST`, `SK=2026-10-01#P001`

Salida batch diaria del modelo (horizonte por defecto 14 días: 25 × 120 × 14 = **42,000** ítems).

```json
{
  "PK": "STORE#S001#FCST", "SK": "2026-10-01#P001", "entity_type": "FORECAST",
  "GSI2PK": "STORE#S001#PRODUCT#P001", "GSI2SK": "FCST#2026-10-01",
  "date": "2026-10-01", "store_id": "S001", "product_id": "P001", "category_id": "C01",
  "predicted_units": 33.8,
  "p50": 33.8, "p80": 39.0, "p90": 42.1,
  "baseline_units": 28.6,
  "uplift_pct": 18.2,
  "drivers": [
    {"type": "WEATHER", "name": "hot", "detail": "temp_max_c 32.1"},
    {"type": "EVENT", "name": "Payday", "detail": null}
  ],
  "model_version": "lgbm-2026-09-30",
  "generated_at": "2026-09-30T23:30:00Z",
  "ttl": 1767225600
}
```

- `predicted_units` es el `predicted_daily_demand` que consumen el reabasto y `select_supplier()`.
- `baseline_units` = demanda esperada del mismo producto en un día "neutral" (p. ej. promedio de los últimos 28 días
  sin evento ni clima extremo). `uplift_pct = predicted_units / baseline_units − 1`; origen de las alertas de pico.
- `drivers` explica el pico (evento del calendario o etiqueta de clima); se toma del ítem `DAY` y de la importancia
  de features del modelo.

### 4.13 Alerta — `PK=STORE#<id>`, `SK=ALERT#<date>#<type>#<target>[#<ref>]`

Atributos comunes:

| Atributo | Tipo | Descripción |
|---|---|---|
| `alert_id` | S | Igual a la `SK` sin el prefijo `ALERT#` (único dentro de la tienda). |
| `alert_type` | S | `EXPIRATION` \| `REORDER` \| `DEMAND_SPIKE` |
| `alert_date` | S | Día del calendario en que se muestra la alerta. |
| `store_id`, `store_name` | S | Tienda. |
| `target_type` | S | `PRODUCT` \| `CATEGORY` |
| `product_id` / `category_id` | S | Objetivo de la alerta. |
| `product_name` / `category_name` | S | Para pintar sin lecturas extra. |
| `severity` | S | `HIGH` \| `MEDIUM` \| `LOW` (reglas abajo). |
| `status` | S | `OPEN` → `ACKNOWLEDGED` → `RESOLVED` \| `DISMISSED` |
| `message` | S | Texto ya renderizado con la plantilla de `CONTEXT.md`. |
| `generated_at` | S | ISO-8601. |
| `run_id` | S | Corrida del motor de alertas que la generó. |
| `ttl` | N | `alert_date + 60 días`. |

**Caducidad** — `SK=ALERT#2026-10-04#EXPIRATION#P059#L0313122`

```json
{
  "alert_type": "EXPIRATION", "alert_date": "2026-10-04", "severity": "HIGH",
  "target_type": "PRODUCT", "product_id": "P059", "product_name": "Ham Sandwich",
  "lot_id": "L0313122", "expiration_date": "2026-10-04",
  "quantity_remaining": 9, "expected_units_sold_before_expiry": 4.2,
  "expected_waste_units": 4.8, "value_at_risk": 102.72,
  "message": "On 2026-10-04, the batch of Ham Sandwich expires."
}
```

Se genera para cada lote `ACTIVE` cuya `expiration_date` cae dentro del horizonte. `expected_waste_units` =
`quantity_remaining − Σ predicted_units hasta expiration_date` (FEFO). `severity = HIGH` si `expected_waste_units > 0`.

**Pedido / reabasto** — `SK=ALERT#2026-10-02#REORDER#P001`

```json
{
  "alert_type": "REORDER", "alert_date": "2026-10-02", "severity": "MEDIUM",
  "target_type": "PRODUCT", "product_id": "P001", "product_name": "Coca-Cola 600 ml",
  "supplier_id": "SUP02", "supplier_name": "Coahuila Bottling Logistics",
  "supplier_email": "coahuila.bottling.logistics@gmail.com",
  "order_quantity": 280, "unit_cost": 15.04, "total_cost": 4211.20,
  "lead_time_days": 2, "expected_arrival_date": "2026-10-04",
  "is_urgent": false,
  "projected_stock": 61, "predicted_daily_demand": 31.2, "days_coverage": 1.96, "reorder_point": 96.5,
  "expected_profit": 1388.80, "score": 1360.99,
  "message": "On 2026-10-02, Coca-Cola 600 ml must be ordered from supplier Coahuila Bottling Logistics; it arrives on 2026-10-04."
}
```

`alert_date` = primer día del horizonte en que `stock proyectado + on_order <= reorder_point`. El proveedor, la
cantidad y `is_urgent` vienen de `select_supplier()` / `decide()`. `severity = HIGH` si `is_urgent`.

**Pico de demanda** — `SK=ALERT#2026-10-03#DEMAND_SPIKE#C02` (categoría) o `...#DEMAND_SPIKE#P026` (producto)

```json
{
  "alert_type": "DEMAND_SPIKE", "alert_date": "2026-10-03", "severity": "MEDIUM",
  "target_type": "CATEGORY", "category_id": "C02", "category_name": "Alcoholic Beverages",
  "uplift_pct": 34, "baseline_units": 96.0, "expected_units": 128.6,
  "driver_type": "EVENT", "driver_name": "Liga MX Playoffs",
  "top_products": ["P026", "P027", "P028"],
  "message": "On 2026-10-03, demand for Alcoholic Beverages is expected to increase by +34% due to Liga MX Playoffs."
}
```

Se genera cuando `uplift_pct ≥ 20 %` (umbral configurable) a nivel producto, y se agrupa a nivel categoría cuando
≥ 3 productos de la misma categoría comparten el mismo `driver`.

Reglas de `severity` sugeridas:

| Tipo | HIGH | MEDIUM | LOW |
|---|---|---|---|
| `EXPIRATION` | merma esperada > 0 | caduca en ≤ 2 días sin merma esperada | resto |
| `REORDER` | `is_urgent = true` | pedir hoy o mañana | resto |
| `DEMAND_SPIKE` | `uplift_pct ≥ 50 %` | 20 % – 50 % | – |

### 4.14 Resumen de alertas por día — `PK=STORE#S001`, `SK=ALERTDAY#2026-10-04`

```json
{
  "PK": "STORE#S001", "SK": "ALERTDAY#2026-10-04", "entity_type": "ALERT_DAY",
  "GSI1PK": "ALERTDAY#2026-10-04", "GSI1SK": "STORE#S001",
  "store_id": "S001", "date": "2026-10-04",
  "counts": {"EXPIRATION": 3, "REORDER": 7, "DEMAND_SPIKE": 1},
  "open_count": 11, "max_severity": "HIGH",
  "latitude": 25.430881, "longitude": -100.983762, "store_name": "OXXO República Oriente", "zone_type": "residential",
  "ttl": 1765497600
}
```

- El calendario mensual (AP3) lee ~30 ítems pequeños en lugar de todas las alertas.
- El mapa (AP2) lee 25 ítems para colorear marcadores por `max_severity` (lleva coordenadas desnormalizadas).
- Se recalcula cada vez que el motor escribe/actualiza alertas de ese día (o vía DynamoDB Streams).

---

## 5. Índices

```text
Tabla road2tech-stores
  PK (S) | SK (S)

GSI1  — listados por tipo, vista por proveedor, vista regional por fecha
  GSI1PK (S) | GSI1SK (S)   proyección ALL
    STORE                 ZONE#<zone>#<store_id>
    PRODUCT               CATEGORY#<category_id>#<product_id>
    SUPPLIER              <supplier_id>
    SUPPLIER#<id>         OFFER#<product_id> | PO#<date>#<order_id>
    ALERTS#<date>         <alert_type>#<store_id>#<target_id>
    ALERTDAY#<date>       STORE#<store_id>

GSI2  — línea de tiempo tienda × producto, entregas en tránsito
  GSI2PK (S) | GSI2SK (S)   proyección ALL
    STORE#<id>#PRODUCT#<pid>   STOCK | LOT#<exp>#<lot> | SALE#<date> | FCST#<date>
    DELIVERY#<date>            STORE#<store_id>#<order_id>   (sparse, sólo pedidos en tránsito)
```

Ningún patrón requiere `Scan`. Los catálogos (`STORE`, `PRODUCT`, `SUPPLIER`, `CATALOG`, `CALENDAR`) son
particiones pequeñas y de sólo lectura; el backend puede cachearlos en memoria de la Lambda.

---

## 6. Flujos de escritura

### 6.1 Carga inicial (desde `v2/`)

| Origen | Ítems | Transformación |
|---|---|---|
| `zones.csv` | 8 | `peak_hours` → lista |
| `categories.csv` | 20 | 0/1 → boolean |
| `stores.csv` | 25 | 0/1 → boolean, `secondary_zone_type` vacío → `null` |
| `products.csv` | 120 | + `category_name` |
| `suppliers.csv` | 19 | `categories_served` → lista |
| `supplier_products.csv` | 405 | + nombre/email/confiabilidad del proveedor, `margin_pct` |
| `calendar.csv` + `weather.csv` | 1,004 | join por `date`; `event_name/event_scope` → lista `events` |
| `inventory_lots.csv` | 3,711 | sólo lotes activos al 2026-09-30 |
| `purchase_orders.csv` | ~29 k | en tránsito (1,015) + últimos 90 días |
| derivado de lotes + pedidos | 3,000 | ítems `STOCK` |
| `sales.csv` | 270,000 + 2,250 | últimos 90 días + resúmenes diarios |
| modelo | 42,000 | pronóstico 14 días |
| motor de alertas | variable | alertas + resúmenes `ALERTDAY` |

Se carga con `BatchWriteItem` (25 ítems por llamada) desde un script en `scripts/` o con *DynamoDB Import from S3*
(CSV/JSON) si se prefiere no escribir código de carga.

### 6.2 Operación diaria

```text
Venta registrada (tienda, producto, unidades)
  TransactWriteItems:
    - UPDATE STOCK#<pid>  on_hand -= u, last_sale_date, version += 1  (condición version)
    - UPDATE LOT#...      quantity_remaining -= u  (lote con menor expiration_date, FEFO vía GSI2)
    - UPDATE LINE#<date>#<pid>  ADD units_sold, revenue
    - UPDATE DAY#<date>         ADD units_sold, revenue

Pedido confirmado desde una alerta REORDER
  TransactWriteItems:
    - PUT    PO#<date>#<order_id>  status PLACED, GSI2PK DELIVERY#<expected_date>
    - UPDATE STOCK#<pid>           on_order += qty, open_order_ids
    - UPDATE ALERT#...             status RESOLVED

Recepción de pedido
  TransactWriteItems:
    - UPDATE PO#...       actual_delivery_date, status DELIVERED, REMOVE GSI2PK, GSI2SK
    - PUT    LOT#<exp>#<pid>#<lot_id>  expiration_date = received_date + shelf_life_days
    - UPDATE STOCK#<pid>  on_hand += qty, on_order -= qty

Cierre del día (EventBridge Scheduler, ~23:30)
  1. Lotes con expiration_date = hoy → status EXPIRED, ttl
  2. Batch del modelo → PUT FCST (horizonte 14 días)
  3. Motor de alertas → recalcula ALERT# y ALERTDAY# del horizonte (PutItem idempotente)
```

DynamoDB Streams sobre `STOCK`, `LOT` y `PO` puede disparar el recálculo de alertas de **esa tienda × producto**
durante el día (por ejemplo, una venta atípica que adelanta un reabasto), sin esperar al cierre.

---

## 7. Tamaño estimado

| Entidad | Ítems | Tamaño aprox. por ítem | Total |
|---|---|---|---|
| Catálogos (zonas, categorías, tiendas, productos, proveedores, ofertas) | ~600 | 0.3–0.6 KB | < 0.5 MB |
| Calendario + clima | ~1,020 | 0.5 KB | 0.5 MB |
| Stock | 3,000 | 0.5 KB | 1.5 MB |
| Lotes activos | ~3,700 | 0.5 KB | 2 MB |
| Pedidos (en tránsito + 90 días) | ~29,000 | 0.7 KB | 20 MB |
| Ventas (90 días) | ~272,000 | 0.2 KB | 55 MB |
| Pronóstico (14 días) | 42,000 | 0.5 KB | 21 MB |
| Alertas (horizonte 14 días) | ~10,000–30,000 | 0.6 KB | ≤ 18 MB |
| **Total** | **~380 k** | | **~120 MB** |

Muy por debajo de cualquier límite; con `PAY_PER_REQUEST` el costo para la demo es de centavos.

---

## 8. Ejemplo: cargar el calendario de una tienda

```python
import boto3
from boto3.dynamodb.conditions import Key

table = boto3.resource("dynamodb").Table("road2tech-stores")

# Contadores del mes (badges del calendario)
days = table.query(
    KeyConditionExpression=Key("PK").eq("STORE#S001")
    & Key("SK").between("ALERTDAY#2026-10-01", "ALERTDAY#2026-10-31"),
)["Items"]

# Detalle del día seleccionado
alerts = table.query(
    KeyConditionExpression=Key("PK").eq("STORE#S001") & Key("SK").begins_with("ALERT#2026-10-04#"),
)["Items"]

# Mapa: color de los 25 marcadores para hoy
markers = table.query(
    IndexName="GSI1",
    KeyConditionExpression=Key("GSI1PK").eq("ALERTDAY#2026-10-04"),
)["Items"]
```

> Nota: `begins_with("ALERT#2026-10-04#")` no devuelve los ítems `ALERTDAY#...` porque el prefijo difiere
> (`ALERT#` vs `ALERTDAY#`); para rangos de alertas usar `between("ALERT#<from>#", "ALERT#<to>#~")`.

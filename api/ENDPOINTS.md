# Contrato propuesto frontend ↔️ backend (StockCopilot)
Documento para revisión y aprobación del equipo de backend. Lo escribe el frontend (repo `road2tech-frontend`, change `align-frontend-to-backend-model`) a partir de `milestones.md`, `ARCHITECTURE.md`, `FLOW.md` y el diccionario de datos nuevo (`scripts/dataset/build_readme.py`) del repo `BackendRoad2Tech`.

Estado: **PROPUESTA**. Los 7 endpoints M0 ya los define el backend y aquí solo se referencian. Todo lo demás (sección 3) es lo que el frontend necesita y hoy no existe; el backend puede cambiar nombres o formas, siempre que actualice este archivo, el esquema y los fixtures en el mismo PR (regla 1 de `milestones.md`).

Ejemplos: valores tomados del dataset real (`S001` = "OXXO República Oriente", colonia `República Oriente`, zona `residential`; `P001` = Coca-Cola 600 ml, `retail_price` 20.00, categoría `C01`; `SUP01` = Northern Beverage Distributors, oferta de `P001`: `unit_cost` 13.65, `min_order_qty` 144, `lead_time_days` 3, `delivery_days` `Tue|Fri`). Las cifras de impacto de los ejemplos son ilustrativas.

---

## 1. Convenciones

| Tema | Regla |
|---|---|
| Envoltorio | Toda respuesta 2xx: `{ "request_id": "req_...", "data_as_of": "2026-09-30", "data": ... }` |
| Errores | `{ "code": "invalid_transition", "message": "...", "request_id": "req_..." }` con HTTP 4xx/5xx. Sin trazas internas. El frontend muestra un mensaje genérico en español y guarda el `request_id`. |
| `data_as_of` | Fecha de corte (`YYYY-MM-DD`). El frontend **no usa el reloj del navegador**: "hoy" = `data_as_of`; historial termina ahí y el forecast inicia al día siguiente. En la demo: `2026-09-30` (último día de `sales`); `forecast_context` cubre 2026-10-01 a 2026-10-14. |
| Fechas | `date` = `YYYY-MM-DD`; timestamps ISO-8601 UTC (`2026-09-30T14:05:00Z`). |
| Dinero | MXN, número con 2 decimales (`1234.50`), nunca string. El frontend da formato. |
| Proporciones | Fracciones `0..1` (`discount_pct` del dataset es fracción: `0.10` = 10 %). **Aviso:** `proposed_discount_pct` se pide también como fracción, para coincidir con `promotion_campaigns.discount_pct`. |
| IDs | Cadenas opacas: `S001..S025`, `P001..P120`, `SUP01..SUP19`, `C01..C20`; campañas `campaign_id`; borradores `draft_id`. El frontend no los interpreta ni los hardcodea. |
| Códigos | Enumeraciones en inglés tal como el dataset (`residential`, `hot`, `Tue|Fri`, `C01` + `category_name` en inglés). **El frontend traduce a español**; el backend no localiza. |
| Auth | `Authorization: Bearer <token>` en todo salvo `POST /auth/signin`. 401 → el frontend cierra sesión. Sin roles ni permisos por tienda en esta fase (todo usuario ve todas las tiendas). |
| Paginación | Query `page` (desde 1) y `page_size` (máx. 100). Respuesta: `data.items[]` y `data.page`, `data.page_size`, `data.total`. |
| Idempotencia | Acciones de estado (`approve`, `reject`, `add-to-plan`) y `POST /restock-orders` aceptan cabecera `Idempotency-Key`. |
| Tienda en rutas | Los endpoints por tienda usan `/stores/{store_id}/...`. |
| Sin cálculo en cliente | El frontend solo formatea. Todo impacto, margen, merma, portafolio, estado de stock, cobertura, fechas de entrega y totales los calcula el backend. |

---

## 2. Endpoints M0 (definidos por backend; no se redefinen aquí)

Se consumen tal cual en `milestones.md`:

| Método | Ruta | Uso en el frontend |
|---|---|---|
| GET | `/stores/{store_id}/tendency?product_id=&history_days=&forecast_days=` | Gráfico de tendencia (historial + forecast P50/P80/P90; series `baseline`/`treated` cuando aplique) |
| POST | `/stores/{store_id}/strategies:generate` | "Regenerar plan" |
| POST | `/stores/{store_id}/strategies:prompt` | Estrategia por prompt desde el chat (ver §3.17) |
| POST | `/drafts/{draft_id}/add-to-plan` | "Agregar al plan" (`draft → selected`) |
| POST | `/drafts/{draft_id}/approve` | "Aprobar" con confirmación (`selected → approved`) |
| POST | `/drafts/{draft_id}/reject` | "Rechazar / Descartar" |
| GET | `/stores/{store_id}/plan?from=&to=` | Plan por tienda y portafolio |

Estados: `draft → selected → approved → scheduled → active → measured → expired`, y `draft → rejected`. El backend decide las transiciones; el frontend solo habilita acciones según el estado y muestra el error si una transición es inválida.

Lo que el frontend asume de estos endpoints (forma en §4): `plan` devuelve `{ store_id, period, generated_at, strategies[], portfolio, reason_if_fewer }`; `tendency` incluye `p50`, `p80`, `p90` y `drivers[]`; `add-to-plan` y `approve` devuelven la estrategia actualizada **y el portafolio recalculado**. Mapeo de estados a etiquetas: `draft` Borrador, `selected` En el plan, `approved` Aprobada, `scheduled` Programada, `active` Activa, `measured` Medida, `expired` Expirada, `rejected` Rechazada.

---

## 3. Endpoints propuestos

Resumen:

| # | Método | Ruta |
|---|---|---|
| 3.1 | GET | `/meta` |
| 3.2 | GET | `/chain/summary` |
| 3.3 | GET | `/stores` |
| 3.4 | GET | `/stores/{store_id}/kpis` |
| 3.5 | GET | `/heat` |
| 3.6 | GET | `/stores/{store_id}/behaviors` |
| 3.7 | GET | `/stores/{store_id}/stock` |
| 3.8 | GET | `/stock/summary` |
| 3.9 | GET / PUT | `/stores/{store_id}/stock-policy` |
| 3.10 | POST | `/stores/{store_id}/stock-policy:suggest` |
| 3.11 | GET | `/stores/{store_id}/restock-suggestions` |
| 3.12 | POST | `/restock-orders` |
| 3.13 | GET | `/stores/{store_id}/calendar` |
| 3.14 | GET | `/suppliers`, `/suppliers/{supplier_id}/offers` |
| 3.15 | POST | `/suppliers/{supplier_id}/test-email` |
| 3.16 | GET | `/products` |
| 3.17 | POST | `/chat` |
| 3.18 | POST | `/auth/signin`, `/auth/logout` |

### 3.1 `GET /meta`

Bootstrap: el frontend lo llama al abrir la app para fijar `data_as_of`, catálogos pequeños y horizontes.

Parámetros: ninguno.

```json
{
  "request_id": "req_01HZX",
  "data_as_of": "2026-09-30",
  "data": {
    "data_as_of": "2026-09-30",
    "history_start": "2024-01-01",
    "forecast_horizon_days": 14,
    "horizons": { "history_days": [7, 30], "forecast_days": [7, 14, 30] },
    "default_plan_days": 7,
    "max_strategies_per_plan": 5,
    "categories": [
      { "category_id": "C01", "category_name": "Non-Alcoholic Beverages", "is_perishable": 0, "typical_shelf_life_days": 180, "requires_refrigeration": 0 }
    ],
    "zones": [
      { "zone_type": "residential", "description": "Housing neighborhoods; neighbors buy daily groceries, drinks and party supplies." }
    ],
    "demo_mode": true
  }
}
```

Requeridos: `data_as_of`, `forecast_horizon_days`, `categories[]` (las 20), `zones[]`. `demo_mode` indica si los correos se redirigen (§3.12).

### 3.2 `GET /chain/summary`

Única fuente de la vista "Todas las sucursales" (el plan y los pedidos no se muestran en esa vista).

Parámetros: `from`, `to` (fechas; por defecto ventana de 30 días terminando en `data_as_of`), `mode` = `actual | forecast` (Datos: Actuales / Predicción ML), `zone` (opcional).

```json
{
  "request_id": "req_01HZY",
  "data_as_of": "2026-09-30",
  "data": {
    "period": { "from": "2026-09-01", "to": "2026-09-30" },
    "mode": "actual",
    "kpis": {
      "average_ticket": 48.72,
      "average_ticket_delta_pct": 0.034,
      "items_per_ticket": 2.6,
      "items_per_ticket_delta_pct": 0.012,
      "stockout_products": 37,
      "no_demand_products": 9,
      "ai_opportunity_mxn": 184250.00
    },
    "portfolio_impact": {
      "net_expected_margin_increment": 184250.00,
      "confidence_interval": { "low": 131000.00, "high": 232500.00, "level": 0.8 },
      "stores_with_plan": 25,
      "strategies_count": 118
    },
    "store_ranking": [
      { "store_id": "S002", "colonia": "Saltillo 2000", "zone_type": "residential", "opportunity_mxn": 11840.00, "strategies_count": 5 },
      { "store_id": "S001", "colonia": "República Oriente", "zone_type": "residential", "opportunity_mxn": 9420.00, "strategies_count": 5 }
    ]
  }
}
```

Requeridos: `average_ticket` (monto `net_amount` promedio por `ticket_id` en `ticket_lines`), `average_ticket_delta_pct` (contra el periodo previo de igual longitud), `items_per_ticket` (suma `quantity` / tickets), `stockout_products` (producto×tienda con `stockout_flag = 1` en `inventory_daily` al corte), `no_demand_products`, `portfolio_impact.net_expected_margin_increment` (**del optimizador, no suma de tarjetas**), `store_ranking[]` ordenado por oportunidad descendente (máx. 25). Definición de "sin demanda": producto×tienda con 0 unidades en los últimos 7 días; **backend fija la regla** (duda abierta D3).

### 3.3 `GET /stores`

Parámetros: ninguno. Sin paginar (25 filas).

```json
{
  "data": {
    "items": [
      {
        "store_id": "S001",
        "store_name": "OXXO República Oriente",
        "colonia": "República Oriente",
        "latitude": 25.430881,
        "longitude": -100.983762,
        "zone_type": "residential",
        "secondary_zone_type": null,
        "size_category": "medium",
        "opening_hours": "00:00-24:00",
        "is_24h": 1,
        "opened_date": "2022-03-26"
      }
    ]
  }
}
```

Requeridos: todos los campos de `stores.csv`. El frontend **ignora `store_name`** y muestra `"Retail " + colonia`.

### 3.4 `GET /stores/{store_id}/kpis`

Parámetros: `from`, `to`, `mode`. Misma forma de `kpis` que en §3.2 más la oportunidad de la tienda:

```json
{
  "data": {
    "store_id": "S001",
    "period": { "from": "2026-09-01", "to": "2026-09-30" },
    "mode": "actual",
    "kpis": {
      "average_ticket": 51.10,
      "average_ticket_delta_pct": 0.021,
      "items_per_ticket": 2.7,
      "items_per_ticket_delta_pct": -0.004,
      "stockout_products": 3,
      "no_demand_products": 1,
      "ai_opportunity_mxn": 9420.00
    }
  }
}
```

`ai_opportunity_mxn` = `net_expected_margin_increment` del portafolio vigente de la tienda.

### 3.5 `GET /heat`

Parámetros: `product_id` (opcional), `zone` (opcional, `zone_type`), `from`, `to`, `mode`.

```json
{
  "data": {
    "product_id": "P001",
    "metric": "units_sold",
    "items": [
      { "store_id": "S001", "movement": 78, "units": 1520, "is_top": false },
      { "store_id": "S002", "movement": 100, "units": 1944, "is_top": true },
      { "store_id": "S003", "movement": 41, "units": 802, "is_top": false }
    ]
  }
}
```

Requeridos: `movement` entero 0–100 (normalizado por el backend dentro del conjunto devuelto), `is_top` (una sola tienda líder; el frontend dibuja la ★). Sin `product_id`, `movement` refleja el volumen total de la tienda. Devuelve las 25 tiendas (o las de la `zone`).

### 3.6 `GET /stores/{store_id}/behaviors`

Comportamientos de compra detectados. Parámetros: `from`, `to`, `limit` (por defecto 6).

```json
{
  "data": {
    "items": [
      {
        "id": "beh_S001_P001_P017",
        "kind": "crosssell",
        "source": "observed",
        "title": "Coca-Cola 600 ml se compra con Sabritas Original 45 g",
        "text": "31 % de los tickets con Coca-Cola 600 ml incluyen el snack.",
        "product_id": "P001",
        "related_product_id": "P017",
        "affinity": { "observed_ticket_share": 0.31, "estimated_ticket_share": null, "lift": 1.8, "tickets_sample": 4120 },
        "estimated_gain_mxn": 3180.00
      },
      {
        "id": "beh_S001_P090_waste",
        "kind": "waste",
        "source": "estimated",
        "title": "Producto con riesgo de caducar",
        "text": "Exceso frente a la demanda proyectada.",
        "product_id": "P090",
        "related_product_id": null,
        "affinity": null,
        "estimated_gain_mxn": 1250.00
      }
    ]
  }
}
```

Requeridos: `kind` ∈ `crosssell | waste | demand`, `source` ∈ `observed | estimated` (observado = medido en `ticket_lines`/campañas con control; estimado = inferido o reglas de negocio; la UI muestra "Observado"/"Estimado"), `product_id`, `estimated_gain_mxn`. Para `crosssell`, `affinity` con `observed_ticket_share` si `source = observed`, o `estimated_ticket_share` si `estimated`. Los IDs de producto del ejemplo (`P017`, `P090`) son ilustrativos.

### 3.7 `GET /stores/{store_id}/stock`

Stock por producto en una tienda. Paginado y filtrado en servidor. (Para la vista de todas las tiendas el frontend usa `store_id = all`; **duda D5**: si prefieren `GET /stock?store_id=` abierto, equivalente.)

Parámetros: `page`, `page_size`, `status` (`out | low | ok | over`), `category_id`, `q` (nombre de producto, contiene), `sort` (`coverage_days | quantity | product_name`).

```json
{
  "data": {
    "items": [
      {
        "store_id": "S001",
        "product_id": "P001",
        "product_name": "Coca-Cola 600 ml",
        "category_id": "C01",
        "closing_qty": 96,
        "on_order_qty": 144,
        "in_transit": [
          { "order_id": "PO-2026-091822", "quantity": 144, "supplier_id": "SUP01", "expected_delivery_date": "2026-10-02", "is_delayed": false }
        ],
        "reorder_point": 120,
        "max_stock": 480,
        "coverage_days": 3.2,
        "status": "low",
        "earliest_expiry": "2027-03-14",
        "expiring_qty_7d": 0,
        "lost_demand_7d": 12,
        "next_restock_date": "2026-10-02"
      }
    ],
    "page": 1, "page_size": 25, "total": 120
  }
}
```

Requeridos: `closing_qty` (de `inventory_daily` al `data_as_of`), `on_order_qty`, `in_transit[]`, `reorder_point`, `max_stock`, `coverage_days`, `status`, `earliest_expiry`, `next_restock_date`. **El estado lo calcula el backend:** `out` si `closing_qty = 0`; `low` si `0 < closing_qty <= reorder_point`; `over` si `closing_qty > max_stock`; si no, `ok`. `earliest_expiry` sale de `inventory_lots` (lotes con `quantity_remaining > 0` y `expiration_date` mínima). `lost_demand_7d` suma de `lost_demand` en 7 días.

### 3.8 `GET /stock/summary`

Conteos por estado con los mismos filtros que §3.7 (no cuenta filas cargadas).

Parámetros: `store_id` (opcional; sin él, toda la cadena), `category_id`, `q`.

```json
{
  "data": { "total": 3000, "out": 37, "low": 214, "ok": 2561, "over": 188 }
}
```

Con una tienda: `total` = 120. Debe cumplirse `out + low + ok + over = total`.

### 3.9 `GET` / `PUT /stores/{store_id}/stock-policy`

Mínimo/máximo por producto de una tienda. El **mínimo** que muestra la UI es `reorder_point` (el mismo que usa `simulate_operations.py`).

`GET` — parámetros: `page`, `page_size`, `category_id`, `q`.

```json
{
  "data": {
    "store_id": "S001",
    "items": [
      {
        "product_id": "P001",
        "product_name": "Coca-Cola 600 ml",
        "category_id": "C01",
        "unit_cost": 13.65,
        "retail_price": 20.00,
        "min_order_qty": 144,
        "shelf_life_days": 180,
        "reorder_point": 120,
        "max_stock": 480,
        "updated_at": "2026-09-12T10:00:00Z"
      }
    ],
    "page": 1, "page_size": 25, "total": 120
  }
}
```

`PUT` — body (lote; solo los cambiados):

```json
{ "items": [ { "product_id": "P001", "reorder_point": 130, "max_stock": 500 } ] }
```

Validación del backend: enteros, `0 <= reorder_point <= max_stock`; error `invalid_policy` con `code` y detalle por `product_id`. Respuesta: items actualizados y `updated_at`. `unit_cost` aquí es el de la oferta elegida por `select_supplier` (**duda D6**).

### 3.10 `POST /stores/{store_id}/stock-policy:suggest`

Body: `{ "product_ids": ["P001", "P002"] }` (vacío = todos los visibles/filtrados; el frontend manda los ids de la página).

```json
{
  "data": {
    "store_id": "S001",
    "items": [
      {
        "product_id": "P001",
        "current": { "reorder_point": 120, "max_stock": 480 },
        "suggested": { "reorder_point": 150, "max_stock": 520 },
        "reason": "Demanda proyectada de 34 u/día en los próximos 14 días (viernes y quincena) y lead time de 3 días."
      }
    ]
  }
}
```

Requeridos: `suggested.reorder_point`, `suggested.max_stock`, `reason` (una línea, en español o clave estable; **duda D7**: ¿en español, o `reason_codes[]` que el frontend traduce?). No persiste nada: el usuario aplica y guarda con `PUT`.

### 3.11 `GET /stores/{store_id}/restock-suggestions`

Parámetros: `from`, `to` (por defecto `data_as_of`..+14), `urgent_only` (bool), `page`, `page_size`.

```json
{
  "data": {
    "items": [
      {
        "id": "rs_S001_P001_20260930",
        "store_id": "S001",
        "product_id": "P001",
        "order_date": "2026-09-30",
        "expected_delivery_date": "2026-10-03",
        "is_urgent": true,
        "reasons": ["below_min", "demand_spike"],
        "suggested_qty": 288,
        "estimated_cost": 3931.20,
        "stock_at_order": 96,
        "predicted_daily_demand": 31.4,
        "supplier": {
          "supplier_id": "SUP01",
          "supplier_name": "Northern Beverage Distributors",
          "email": "northern.beverage.distributors@gmail.com",
          "unit_cost": 13.65,
          "min_order_qty": 144,
          "lead_time_days": 3,
          "delivery_days": "Tue|Fri",
          "reliability_score": 0.78
        },
        "alternatives": [
          { "supplier_id": "SUP02", "supplier_name": "Coahuila Bottling Logistics", "unit_cost": 15.04, "min_order_qty": 120, "lead_time_days": 2, "expected_delivery_date": "2026-10-02", "suggested_qty": 240, "estimated_cost": 3609.60 }
        ],
        "expected_lost_sales_if_skipped": 2100.00,
        "expected_gain_if_ordered": 1650.00,
        "expected_waste_if_ordered": 0.00
      }
    ],
    "page": 1, "page_size": 25, "total": 14
  }
}
```

Requeridos: `order_date` y `expected_delivery_date` **separados** (`expected_delivery_date = order_date + lead_time_days`), `supplier` elegido por `select_supplier()` (mismo resultado del dataset, mismas reglas: filtrar `lead_time_days > cobertura`, `qty = max(min_order_qty, ceil(demanda × (lead_time + cycle_days)))`), `is_urgent` (= ningún proveedor llega antes del quiebre), `reasons[]` ∈ `below_min | demand_spike | stockout | expiry | season`, `suggested_qty` (siempre ≥ `supplier.min_order_qty`), `estimated_cost` (= qty × unit_cost), `alternatives[]` con su `suggested_qty`, `estimated_cost` y `expected_delivery_date` ya calculados por oferta (el frontend no calcula al cambiar de proveedor). Las razones `expiry` y `season` extienden el enum del dataset (`below_min | demand_spike | stockout | season`); **duda D8**.

La UI compone: "Pide 288 a Northern Beverage Distributors hoy; llega el 3 de oct" a partir de estos campos.

### 3.12 `POST /restock-orders`

Envía el correo con Resend. **Solo se llama tras la confirmación explícita del usuario.**

Body:

```json
{ "suggestion_id": "rs_S001_P001_20260930", "supplier_id": "SUP01", "qty": 288 }
```

Respuesta 201:

```json
{
  "data": {
    "order_id": "PO-2026-091830",
    "suggestion_id": "rs_S001_P001_20260930",
    "store_id": "S001",
    "product_id": "P001",
    "supplier_id": "SUP01",
    "qty": 288,
    "unit_cost": 13.65,
    "estimated_cost": 3931.20,
    "order_date": "2026-09-30",
    "expected_delivery_date": "2026-10-03",
    "status": "sent",
    "sent_at": "2026-09-30T16:20:11Z",
    "email": {
      "to": "northern.beverage.distributors@gmail.com",
      "subject": "Orden de compra PO-2026-091830 — Retail República Oriente",
      "provider_message_id": "re_8f2c..."
    },
    "demo_redirect": true
  }
}
```

Reglas:
- El cliente **no** manda destinatario. El backend toma el correo del proveedor, y si `DEMO_MAILBOX` (variable de entorno) está definida, envía a ese buzón, pero devuelve en `email.to` el correo **mostrado** (el del proveedor, como si hubiera ido allí) y `demo_redirect: true`. La UI avisa "En demostración el envío real se redirige a un buzón de pruebas".
- Valida `qty >= min_order_qty` de la oferta; si no, `422 below_min_order_qty` con `min_order_qty` en `message`.
- Idempotente por `Idempotency-Key` / `suggestion_id` (un reintento no manda dos correos).
- Fallo de Resend: `502 email_failed`, sin cambiar el estado de la sugerencia.
- Marca: el correo usa "Retail <colonia>" como remitente/firma (el backend puede construirlo desde `colonia`).

### 3.13 `GET /stores/{store_id}/calendar`

Parámetros: `from`, `to` (mes visible), `layers` = lista separada por comas de `deliveries,strategies,alerts,context` (por defecto todas).

```json
{
  "data": {
    "store_id": "S001",
    "from": "2026-10-01",
    "to": "2026-10-31",
    "events": [
      { "id": "ev_1", "date": "2026-10-03", "kind": "delivery", "label_code": "delivery", "product_id": "P001", "supplier_id": "SUP01", "order_id": "PO-2026-091830", "qty": 288 },
      { "id": "ev_2", "date": "2026-10-04", "kind": "strategy", "strategy_id": "dr_01J...", "status": "scheduled", "strategy_type": "affinity_combo", "start_date": "2026-10-04", "end_date": "2026-10-10" },
      { "id": "ev_3", "date": "2026-10-02", "kind": "alert", "alert_type": "stockout_risk", "product_id": "P001", "severity": "high" },
      { "id": "ev_4", "date": "2026-10-05", "kind": "alert", "alert_type": "expiring_soon", "product_id": "P090", "qty": 36 },
      { "id": "ev_5", "date": "2026-09-28", "kind": "alert", "alert_type": "order_delayed", "product_id": "P002", "order_id": "PO-2026-091700", "expected_delivery_date": "2026-09-27", "delay_days": 3 }
    ],
    "context": [
      {
        "date": "2026-10-15",
        "day_of_week": "Thursday",
        "is_holiday": 0, "holiday_name": null,
        "event_name": "Mexico vs USA",
        "event_scope": "sports",
        "is_payday": 1, "is_school_vacation": 0, "is_college_vacation": 0,
        "temp_max_c": 31.2, "temp_min_c": 16.4, "precipitation_mm": 0.0, "weather_label": "hot",
        "is_forecast": true
      }
    ]
  }
}
```

Requeridos: `events[]` con `kind` ∈ `delivery | strategy | alert`; `alert_type` ∈ `stockout_risk | expiring_soon | order_delayed`. Para `order_delayed`: `delay_days` (o `expected_delivery_date` y `actual_delivery_date` nulo si sigue pendiente; **duda D9**, pendiente del diseño). `context[]` con las columnas de `calendar.csv` / `forecast_context.csv` (`event_name` y `event_scope` pueden traer varios valores separados por `|`); los feriados/eventos los traduce el frontend por clave exacta. El ejemplo de evento deportivo es ilustrativo.

### 3.14 `GET /suppliers` y `GET /suppliers/{supplier_id}/offers`

`GET /suppliers` — sin parámetros (19 filas):

```json
{
  "data": {
    "items": [
      {
        "supplier_id": "SUP01",
        "supplier_name": "Northern Beverage Distributors",
        "email": "northern.beverage.distributors@gmail.com",
        "city": "Saltillo",
        "categories_served": ["C01", "C02", "C11"],
        "reliability_score": 0.78,
        "offers_count": 41
      }
    ]
  }
}
```

`categories_served` se entrega ya como arreglo (el CSV usa `|`). `reliability_score` = probabilidad de entrega a tiempo; la UI lo rotula "Puntualidad" (no surtido).

`GET /suppliers/{supplier_id}/offers` — parámetros: `page`, `page_size`, `category_id`, `q`:

```json
{
  "data": {
    "supplier_id": "SUP01",
    "items": [
      { "product_id": "P001", "product_name": "Coca-Cola 600 ml", "category_id": "C01", "unit_cost": 13.65, "min_order_qty": 144, "lead_time_days": 3, "delivery_days": "Tue|Fri", "retail_price": 20.00 }
    ],
    "page": 1, "page_size": 25, "total": 41
  }
}
```

Editar proveedores queda fuera de alcance (solo lectura): el costo, mínimo y lead time viven en `supplier_products`.

### 3.15 `POST /suppliers/{supplier_id}/test-email`

Body: `{}`. Respuesta:

```json
{ "data": { "supplier_id": "SUP01", "status": "sent", "sent_at": "2026-09-30T16:31:00Z", "email": { "to": "northern.beverage.distributors@gmail.com" }, "demo_redirect": true } }
```

Misma política de redirección que §3.12.

### 3.16 `GET /products`

Catálogo (120 productos). Parámetros: `category_id`, `q`, `page`, `page_size`.

```json
{
  "data": {
    "items": [
      {
        "product_id": "P001",
        "product_name": "Coca-Cola 600 ml",
        "category_id": "C01",
        "brand": "Coca-Cola",
        "unit": "bottle",
        "retail_price": 20.00,
        "shelf_life_days": 180,
        "is_seasonal": 0,
        "weather_sensitivity": "hot",
        "base_daily_demand": 30.00,
        "default_supplier": { "supplier_id": "SUP01", "unit_cost": 13.65, "min_order_qty": 144 }
      }
    ],
    "page": 1, "page_size": 25, "total": 120
  }
}
```

`base_daily_demand` puede ser < 1 en baja rotación (la UI muestra decimales). El min/max por tienda va en §3.9, no aquí. También se usa para el autocompletado del mapa.

### 3.17 `POST /chat`

Chat del asistente (GenUI). Respuesta completa, sin streaming. Requiere tienda.

Body:

```json
{
  "store_id": "S001",
  "conversation_id": null,
  "message": "Aumenta las energéticas el sábado con máximo 10% de descuento"
}
```

`conversation_id` nulo la primera vez; el backend lo devuelve y el frontend lo reenvía (checkpoint de LangGraph). Si la tienda falta: `422 store_required`.

Respuesta:

```json
{
  "request_id": "req_01HZZ",
  "data_as_of": "2026-09-30",
  "data": {
    "conversation_id": "conv_01J8",
    "message_id": "msg_02",
    "tools": [
      { "name": "get_demand_forecast", "args": { "store_id": "S001", "horizon": 14 } },
      { "name": "simulate_strategy", "args": { "strategy_type": "contextual_offer", "category": "Energy drinks", "max_discount_pct": 0.10 } },
      { "name": "create_promotion_draft", "args": { "store_id": "S001" } }
    ],
    "interpreted_constraints": {
      "product_ids": [], "category_id": "C01", "weekday": "Saturday", "max_discount_pct": 0.10
    },
    "blocks": [
      { "type": "text", "text": "Preparé un borrador con descuento de 8 % en el sábado." },
      { "type": "strategy_card", "strategy": { "...": "ver Strategy en §4" }, "requires_approval": true },
      { "type": "plan_summary", "current_net_expected_margin_increment": 9420.00, "with_draft_net_expected_margin_increment": 10310.00, "delta": 890.00, "overlaps": [] }
    ]
  }
}
```

**Unión discriminada de bloques** (`type`):

| `type` | Campos |
|---|---|
| `text` | `text` |
| `forecast_chart` | `store_id`, `product_id`, `history[]` y `forecast[]` de puntos `{ date, value }`; `forecast[]` con `p50`, `p80`, `p90`; opcional `treated[]` |
| `strategy_card` | `strategy` (forma Strategy, §4.1), `requires_approval: true` |
| `plan_summary` | `current_net_expected_margin_increment`, `with_draft_net_expected_margin_increment`, `delta`, `overlaps[]` |
| `restock_table` | `suggestions[]` con la forma de §3.11 (puede ser un subconjunto) |

El frontend filtra bloques desconocidos o inválidos. `tools[]` son los realmente ejecutados, en orden (`get_current_store_context`, `get_demand_forecast`, `get_historical_insights`, `get_business_constraints`, `simulate_strategy`, `optimize_portfolio`, `create_promotion_draft`, `get_product_tendency`, `get_product_sell_strategy`).

Reglas: el chat **nunca aprueba ni activa**; si el usuario pide "actívala", responde con texto indicando que la aprobación se hace desde el plan. Un borrador creado por el chat vive como `draft` y se agrega con `POST /drafts/{draft_id}/add-to-plan`. Error de agente: HTTP 5xx con el formato de §1 (el frontend ofrece "Reintentar" con el mismo `message`).

### 3.18 `POST /auth/signin` y `POST /auth/logout`

`/auth/signin` reemplaza a `/auth/login` (misma forma). Body: `{ "email": "demo@stockcopilot.com", "password": "demo1234" }`.

```json
{ "data": { "token": "eyJhbGciOi...", "expires_at": "2026-10-01T00:00:00Z", "user": { "id": "u_01", "name": "Demo", "email": "demo@stockcopilot.com" } } }
```

Credenciales incorrectas: `401 invalid_credentials`. Token vencido en cualquier otra ruta: `401 token_expired`.

`POST /auth/logout` (con `Authorization`, body vacío) revoca la sesión del token: `{ "data": { "logged_out": true } }`.
Después, ese token recibe `401 unauthorized` en cualquier ruta. Token ausente o inválido: `401 unauthorized`.

### 3.19 `POST /emails`

Envía un correo libre con Resend al destinatario indicado (requiere `Authorization`). No aplica `DEMO_MAILBOX`: el
usuario elige el destinatario.

Body (`text`, `htmlBody` o ambos; si van los dos, el cliente de correo muestra el HTML y `text` queda de respaldo):

```json
{ "to": "cliente@ejemplo.com", "subject": "Tu pedido", "text": "Hola...", "htmlBody": "<!DOCTYPE html>...", "from_name": "StockCopilot" }
```

`htmlBody` se envía tal cual, sin escapar ni sanitizar, así que se puede generar en el frontend con react-email
(`await render(<MiCorreo />)` y `render(<MiCorreo />, { plainText: true })` para `text`), mjml u otra librería.
`from_name` es opcional (por defecto `StockCopilot`); el remitente es `EMAIL_FROM_ADDRESS`.

Respuesta 201:

```json
{ "data": { "status": "sent", "sent_at": "2026-10-10T12:20:11Z", "email": { "to": "cliente@ejemplo.com", "subject": "Tu pedido", "provider_message_id": "01a125c1-..." } } }
```

Errores: `400 invalid_request` (`to` inválido, falta `subject`, faltan `text` y `htmlBody`), `413 body_too_large`
(`text` o `htmlBody` > 1 MB), `502 email_failed` (Resend rechazó el envío).

---

## 4. Formas de Strategy y Portfolio

### 4.1 Strategy

Coincide con `StrategyCandidate` + `StrategySimulation` + `PromotionDraft` de M0, y `strategy_type` con `promotion_campaigns.strategy_type`.

```json
{
  "draft_id": "dr_01J8ABC",
  "store_id": "S001",
  "status": "selected",
  "strategy_type": "affinity_combo",
  "title": "Combo Coca-Cola 600 ml + snack",
  "anchor_product_id": "P001",
  "promoted_product_id": "P017",
  "period": { "start_date": "2026-10-03", "end_date": "2026-10-09" },
  "proposed_discount_pct": 0.10,
  "constraints": {
    "min_margin_pct": 0.12,
    "max_discount_pct": 0.25,
    "budget_mxn": 2500.00,
    "satisfied": true,
    "violations": []
  },
  "assumptions": [
    "Afinidad observada en 31 % de los tickets (ticket_lines, últimos 90 días).",
    "Uplift estimado con campañas tratadas y control similares."
  ],
  "baseline": { "units": 420, "net_revenue": 8400.00, "margin": 2650.00, "waste_units": 0, "waste_cost": 0.00, "stockout_risk": 0.18 },
  "treated":  { "units": 505, "net_revenue": 9330.00, "margin": 3120.00, "waste_units": 0, "waste_cost": 0.00, "stockout_risk": 0.22 },
  "expected_units_increment": 85,
  "expected_sales_increment": 930.00,
  "expected_margin_increment": 470.00,
  "expected_waste_reduction": 0.00,
  "confidence_interval": { "low": 210.00, "high": 720.00, "level": 0.8 },
  "explanation": "Quienes compran Coca-Cola 600 ml suelen llevar un snack; el viernes y la quincena elevan el flujo.",
  "drivers": ["bought_together", "demand"],
  "created_at": "2026-09-30T15:00:00Z",
  "approved_at": null,
  "measurement": null,
  "requires_approval": true
}
```

Requeridos: `draft_id`, `store_id`, `status` (enum de §2, incluye `rejected`), `strategy_type` ∈ `affinity_combo | rescue_bundle | contextual_offer`, `anchor_product_id` (puede ser `null` en `contextual_offer`), `promoted_product_id`, `period`, `proposed_discount_pct` (fracción), `baseline` y `treated` (ambos con `units`, `net_revenue`, `margin`, `waste_units`, `waste_cost`, `stockout_risk` 0..1), los cuatro incrementales, `confidence_interval` `{low, high, level}` sobre `expected_margin_increment`, `assumptions[]`, `constraints` (incluye `violations[]`), `explanation`, `drivers[]` ∈ `demand | weather | event | season | shelf_life | bought_together`.

Plantillas:
- `affinity_combo`: ancla + producto promovido de alta afinidad; el descuento aplica al promovido.
- `rescue_bundle`: producto con riesgo de merma (`earliest_expiry` cercano) atado a uno de alta rotación.
- `contextual_offer`: oferta por contexto (evento, clima, quincena, estacionalidad); puede ser de un solo producto.

Estrategia rechazada por restricción (demo): llega con `status: "rejected"`, `treated` presente y `constraints.satisfied: false`, `violations: [{ "code": "min_margin", "message": "...", "limit": 0.12, "actual": 0.04 }]`. Por favor incluir una en fixtures (M0 ya la pide).

Estrategia `measured`: `measurement` = `{ "measured_at": "...", "actual": { "units": 498, "net_revenue": 9210.00, "margin": 3050.00, "waste_units": 0 }, "actual_margin_increment": 400.00, "control_group_used": true }`; la UI compara con lo esperado.

### 4.2 Portfolio y respuesta de `plan`

```json
{
  "store_id": "S001",
  "period": { "from": "2026-10-01", "to": "2026-10-07" },
  "generated_at": "2026-09-30T06:00:00Z",
  "strategies": [ { "...": "Strategy" } ],
  "portfolio": {
    "net_expected_margin_increment": 9420.00,
    "confidence_interval": { "low": 6100.00, "high": 12400.00, "level": 0.8 },
    "conflicts": [
      { "strategy_ids": ["dr_01J8ABC", "dr_01J8ABD"], "type": "overlapping_product", "description": "Comparten P001 entre el 03 y el 05", "resolution": "kept_higher_impact" }
    ]
  },
  "reason_if_fewer": null
}
```

- `strategies[]` ordenadas por impacto neto descendente, máx. 5 (no cuenta `rejected`/`expired` históricas; el backend decide si las manda aparte con `include_history=true`).
- `portfolio.net_expected_margin_increment` considera canibalización y conflictos: **no es la suma de las tarjetas**.
- `reason_if_fewer`: texto o código estable cuando hay menos de 5 (p. ej. `"no_viable_candidates"`, `"constraints_filtered_2"`). Con 0 estrategias, `strategies: []` y el motivo es obligatorio.
- `add-to-plan`, `approve`, `reject` y `strategies:generate` devuelven `{ strategy, portfolio }` o el `plan` completo (cualquiera, pero con el portafolio recalculado en la misma respuesta).

---

## 5. Campos que el dataset aún no entrega y se piden

| Campo | Para qué | Propuesta de fuente |
|---|---|---|
| `reorder_point` por tienda×producto | Mínimo de stock, estados `low` | Ya usado en `simulate_operations.py` (`predicted_daily_demand × (mediana lead time + safety_days)`) pero **no se persiste**; exponerlo y permitir sobrescribirlo (§3.9) |
| `max_stock` | Estado `over`, tope visual | Definir regla (p. ej. `predicted_daily_demand × (lead_time + cycle_days + safety)`) o configuración por tienda |
| `lost_demand` agregado | "Artículos sin stock", riesgo de quiebre | Ya existe en `inventory_daily`; falta agregarlo por ventana (§3.7) |
| `earliest_expiry`, `expiring_qty_7d` | Caducidad próxima y alerta "por caducar" | Derivar de `inventory_lots` |
| Percentiles `p50/p80/p90` | Banda de incertidumbre del forecast | M4 (el contrato M0 `Forecast` ya los incluye); en histórico, solo `value` |
| `proposed_discount_pct` | Descuento propuesto por el motor (sin slider) | M5; fracción 0..1 |
| `reason_if_fewer` | Aviso cuando hay < 5 estrategias | M5 / optimizador |
| Resultados de medición (`measurement`) | Estado `measured`: real vs. esperado | M7 (`promotion.measured.v1`) usando control de `promotion_store_days` |
| `average_ticket`, `items_per_ticket` y deltas | KPIs de ticket | Agregar `ticket_lines` por ventana |
| Afinidad observada vs. estimada | Etiqueta Observado/Estimado | Athena sobre `ticket_lines` + reglas de negocio |
| Pedidos retrasados (`delay_days`) | Alerta "Pedido retrasado" | `purchase_orders.expected_delivery_date` vs. `actual_delivery_date` (o nulo con fecha vencida) |
| Correo mostrado vs. real | Aviso de demo | Variable de entorno `DEMO_MAILBOX` + `demo_redirect` (§3.12) |

---

## 6. Dataset: archivos faltantes en el repo backend y regeneración

El diccionario nuevo (`scripts/dataset/build_readme.py`) describe 17 archivos y el generador ya los produce (`run_all.py`, `SEED = 42`, 2024-01-01 a 2026-09-30, forecast de 14 días), pero **los CSV versionados en `public/softtek/dataset/dataset-hackathon-road2tech/v2/` son anteriores** al generador y no coinciden con él.

Presentes en `v2/` (versión vieja): `calendar`, `categories`, `inventory_lots`, `products`, `purchase_orders`, `sales`, `stores`, `supplier_products`, `suppliers`, `weather`, `zones`.

**Faltan** respecto al diccionario nuevo:
- `ticket_lines.csv`
- `inventory_daily.csv` (en la raíz hay un `inventory_daily.csv` del dataset original, de otro esquema)
- `promotion_campaigns.csv`
- `promotion_store_days.csv`
- `forecast_context.csv`

Además `sales.csv` vigente **no trae** las columnas nuevas `discount_amount`, `net_revenue`, `revenue`, `campaign_id`, `campaign_units`.

Pedido al backend: regenerar con `python3 scripts/dataset/run_all.py` (o el gate Docker de M0: `docker compose build dataset` y `docker compose run --rm dataset`), pasar `validate.py` y publicar el `v2/README.md` resultante. El frontend no versiona CSV: extrae un subconjunto compacto con su propio script contra esa salida, y mientras tanto usa un conjunto semilla con la misma forma construido del catálogo real.

---

## 7. Decisiones del frontend que el backend debe conocer

1. **Marca:** la arma el frontend: `"Retail " + colonia`. `store_name` ("OXXO ...") no se muestra; no hace falta cambiarlo.
2. **Traducción:** categorías, zonas, días de entrega (`Tue|Fri` → "Mar/Vie"), clima, estados, motivos, feriados y eventos (~40, por clave exacta) los traduce el frontend con diccionarios. Un código sin traducción se muestra tal cual; **avisen si agregan eventos nuevos**. Nombres de producto y marca se muestran como llegan (en inglés).
3. **Sin slider:** el detalle muestra `proposed_discount_pct` y dos escenarios (`baseline` = "Si no aceptas", `treated` = "Si aceptas"). No hay curva de descuento ni recalcular en el cliente.
4. **Sin cálculos financieros en cliente:** ni suma de portafolio, ni margen, ni estado de stock, ni fechas de entrega, ni mínimo de pedido (la UI solo valida `qty >= min_order_qty` con el valor que manda el backend).
5. **Vista "Todas":** solo usa `GET /chain/summary`, `GET /stores`, `GET /heat` y `GET /stock/summary`/`GET /stock` con filtros. No muestra plan, chat ni pedidos; hay que elegir una tienda.
6. **Aprobación humana:** nada se aprueba ni se envía sin un diálogo de confirmación; el chat no aprueba.
7. **Filtros de la UI:** sucursal, "Datos" (`actual | forecast`) y rango relativo a `data_as_of` (últimos 7/30, próximos 14/30 días) se traducen a `from`, `to`, `mode`.
8. **Validación:** el frontend valida cada respuesta con esquemas zod; una respuesta con forma distinta se muestra como error con `request_id`. Cambios de contrato → avisar para actualizar el esquema.
9. **Modo mock:** `NEXT_PUBLIC_API_MODE=mock|http`, `NEXT_PUBLIC_API_URL`. Con mock aparece la insignia "Datos de ejemplo".
10. **Escala:** 25 tiendas, 120 productos, 20 categorías, 19 proveedores; listas paginadas en servidor.

---

## 8. Checklist de aceptación para backend

- [ ] Envoltorio `{request_id, data_as_of, data}` y error `{code, message, request_id}` en todos los endpoints.
- [ ] `data_as_of` consistente entre endpoints (demo: `2026-09-30`).
- [ ] CSV regenerados con `run_all.py`, `validate.py` en verde, y los 5 archivos faltantes disponibles (§6).
- [ ] Los 7 endpoints M0 con fixtures de éxito/error y la forma de Strategy/Portfolio de §4 (incluye `proposed_discount_pct`, `baseline`/`treated`, `confidence_interval`, `reason_if_fewer`).
- [ ] `GET /meta`, `GET /chain/summary`, `GET /stores`, `GET /stores/{id}/kpis`, `GET /heat`, `GET /stores/{id}/behaviors`.
- [ ] `GET /stores/{id}/stock` paginado/filtrado y `GET /stock/summary` con `out+low+ok+over = total`.
- [ ] `GET/PUT /stores/{id}/stock-policy` y `POST .../stock-policy:suggest` con `reason`.
- [ ] `GET /stores/{id}/restock-suggestions` con fechas de pedido/entrega separadas, `is_urgent`, `reasons`, alternativas con costo y fecha por oferta; cantidad nunca menor a `min_order_qty`.
- [ ] `POST /restock-orders` con Resend, `DEMO_MAILBOX`, `demo_redirect`, idempotencia y error `below_min_order_qty`.
- [ ] `GET /stores/{id}/calendar` con entregas, estrategias, alertas (sin stock, por caducar, pedido retrasado) y capa de contexto.
- [ ] `GET /suppliers`, `GET /suppliers/{id}/offers`, `POST /suppliers/{id}/test-email`, `GET /products`.
- [ ] `POST /chat` con bloques de la unión discriminada, `tools[]` reales, `conversation_id`, y `requires_approval: true` en borradores; el chat no puede aprobar.
- [ ] `POST /auth/signin` con token Bearer, `POST /auth/logout` y 401 en token vencido o revocado.
- [ ] Transiciones inválidas de estado rechazadas con error claro; `approve`/`reject` idempotentes.
- [ ] CORS habilitado para el origen del frontend.
- [ ] Respuestas a nuestros dudas abiertas (§9).

---

## 9. Dudas abiertas para el backend

- **D1.** ¿`discount_pct` y `proposed_discount_pct` siempre como fracción (0.10)? El frontend asume que sí.
- **D2.** ¿`GET /plan` devuelve también estrategias `rejected`/`expired`/`measured`? Propuesta: sí, con `include_history`.
- **D3.** Regla exacta de "artículos sin demanda" y "artículos sin stock" para KPIs.
- **D4.** ¿`ai_opportunity_mxn` de la cadena es la suma de portafolios por tienda (cada uno optimizado) o un optimizador global? (Solo importa que lo calcule el backend.)
- **D5.** Forma de pedir stock de toda la cadena (`store_id=all` o `GET /stock`).
- **D6.** `unit_cost` mostrado en políticas: ¿oferta elegida por `select_supplier` o la más barata?
- **D7.** `reason` de `stock-policy:suggest` y `explanation` de estrategias: ¿texto en español o códigos?
- **D8.** Motivos de restock: ¿se agregan `expiry`/`season` al enum?
- **D9.** Forma exacta del pedido retrasado (`delay_days` vs. fechas).
- **D10.** ¿El chat flotará en todas las pantallas? Hoy solo en el dashboard con tienda elegida.
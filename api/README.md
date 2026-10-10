# API StockCopilot (Go + AWS Lambda)

Endpoints §3.1–§3.21 de [`ENDPOINTS.md`](ENDPOINTS.md), una Lambda por carpeta, detrás de un API Gateway HTTP API, sobre la
tabla única de DynamoDB descrita en [`docs/DYNAMO.md`](../docs/DYNAMO.md).

Base URL: `https://avbylri05m.execute-api.us-east-1.amazonaws.com`

| § | Método y ruta | Lambda (`functions/…`) |
|---|---|---|
| 3.1 | `GET /meta` | [`meta`](#meta) |
| 3.2 | `GET /chain/summary` | [`chain-summary`](#chain-summary) |
| 3.3 | `GET /stores` | [`stores`](#stores) |
| 3.4 | `GET /stores/{store_id}/kpis` | [`kpis`](#kpis) |
| 3.5 | `GET /heat` | [`heat`](#heat) |
| 3.6 | `GET /stores/{store_id}/behaviors` | [`behaviors`](#behaviors) |
| 3.7 | `GET /stores/{store_id}/stock` | [`stock`](#stock) |
| 3.8 | `GET /stock/summary` | [`stock-summary`](#stock-summary) |
| 3.9 | `GET` / `PUT /stores/{store_id}/stock-policy` | [`stock-policy`](#stock-policy) |
| 3.10 | `POST /stores/{store_id}/stock-policy:suggest` | [`stock-policy-suggest`](#stock-policy-suggest) |
| 3.11 | `GET /stores/{store_id}/restock-suggestions` | [`restock-suggestions`](#restock-suggestions) |
| 3.12 | `POST /restock-orders` | [`restock-orders`](#restock-orders) |
| 3.13 | `GET /stores/{store_id}/calendar` | [`calendar`](#calendar) |
| 3.14 | `GET /suppliers`, `GET /suppliers/{supplier_id}/offers` | [`suppliers`](#suppliers) |
| 3.15 | `POST /suppliers/{supplier_id}/test-email` | [`supplier-test-email`](#supplier-test-email) |
| 3.16 | `GET /products` | [`products`](#products) |
| 3.17 | `POST /chat` | [`chat`](#chat) |
| 3.18 | `POST /auth/signin` | [`auth-signin`](#auth-signin) |
| 3.18 | `POST /auth/logout` | [`auth-logout`](#auth-logout) |
| 3.19 | `POST /emails` | [`send-email`](#send-email) |
| 3.20 | `GET /predictions/stores` | [`predictions-stores`](#predictions-stores) |
| 3.21 | `GET /stores/{store_id}/predictions/products` | [`predictions-products`](#predictions-products) |

Todas, salvo `/auth/signin`, piden `Authorization: Bearer <token>`. Usuario demo: `demo@stockcopilot.com` / `demo1234`.
El detalle de cada Lambda está en [Referencia de Lambdas](#referencia-de-lambdas).

## Estructura

```text
api/
  functions/<lambda>/main.go   un handler por endpoint (package main)
  internal/httpx               envoltorio {request_id, data_as_of, data}, errores, paginación, CORS
  internal/auth                JWT HS256 + sesiones en DynamoDB (logout revoca), PBKDF2 para contraseñas
  internal/db                  acceso a la tabla road2tech-stores (modelos y consultas por patrón de acceso)
  internal/domain              select_supplier(), proyección de reabasto, política mín/máx, forma Strategy
  internal/mail                envío con Resend (DEMO_MAILBOX, modo log sin API key)
  scripts/build.sh             compila linux/arm64 → dist/lambdas/<lambda>.zip (bootstrap)
  scripts/deploy.sh            sube los zip y actualiza el código de cada Lambda (aws CLI)
  scripts/set-mail-env.sh      copia RESEND_PASS/RESEND_EMAIL de ../.env.local a las Lambdas que envían correo
../scripts/dynamo/build_import.py   genera la carga de DynamoDB desde los CSV de v2/
```

## Reglas implementadas

- **Proveedor y cantidad**: `domain.Decide` es el port de `scripts/dataset/supplier_selection.py`
  (`go test ./internal/domain` compara contra resultados de Python).
- **Reabasto (§3.11)**: por producto, día a día desde `data_as_of`: llegan pedidos abiertos, se consume el pronóstico
  `p50`, y si `stock + on_order <= reorder_point` se sugiere un pedido con `Decide` (igual que `simulate_operations.py`).
  `reasons`: `below_min`, `stockout`, `demand_spike` (expiry/season quedan pendientes de la duda D8).
- **Política (§3.10)**: `reorder_point = D·(lead + safety)`, `max_stock = max(D·(lead + safety + cycle), reorder_point + min_order_qty)`,
  `D` = promedio del pronóstico a 14 días; `reason` en español y `reason_codes[]` (duda D7).
- **Calendario (§3.13)**: entregas y estrategias leídas de la tabla; alertas derivadas al leer (ver DYNAMO.md §4.18).
- **Chat (§3.17)**: orquestador por reglas con los mismos nombres de herramientas que el agente LangGraph; detecta
  intención (oferta, reabasto, pronóstico, aprobar), categoría, producto, día y descuento máximo; crea borradores
  `contextual_offer` con `status: draft` y nunca aprueba. Elasticidad supuesta 4.0, margen mínimo 12 %, presupuesto $2,500.
- **Pronóstico**: seasonal-naive mientras no exista la salida del modelo (DYNAMO.md §4.12).
- **Dashboard (§3.1–§3.9)**: `/meta` lee catálogos (categorías, zonas); `/chain/summary` y `/kpis` agregan
  tickets (average_ticket, items_per_ticket), stockouts, sin-demanda y el portafolio (suma del margen incremental
  de las estrategias en plan); `/heat` normaliza el movimiento 0–100 entre tiendas; `/behaviors` combina afinidad
  observada (reglas de `build_tickets.py`), merma y picos de demanda; `/stock` y `/stock/summary` calculan el estado
  `out/low/ok/over`; `/stock-policy` lee y edita `reorder_point`/`max_stock`.

Los KPIs de ticket y `lost_demand_7d` requieren que `build_import.py` cargue `ticket_lines.csv` e
`inventory_daily.csv` (dataset regenerado con `run_all.py`, ver §6 de `ENDPOINTS.md`).

## Referencia de Lambdas

Cada carpeta de `functions/` es una Lambda `road2tech-<carpeta>` (binario `bootstrap`, `provided.al2023`, arm64). Todas
se arman igual: `lambda.Start(httpx.Wrap(handle, auth.Required))`, así que comparten este comportamiento:

- **Envoltorio**: la respuesta exitosa es `{request_id, data_as_of, data}`; lo que se describe abajo como "respuesta" es `data`.
- **Errores**: `{code, message, request_id, details?}` con el status HTTP del error. Cualquier error no tipado o *panic*
  se convierte en `500 internal_error`.
- **Autenticación** (`auth.Required`): sin `Authorization: Bearer <token>` válido → `401 unauthorized`; token vencido →
  `401 token_expired`; sesión revocada (logout) → `401 unauthorized`. La única Lambda pública es `auth-signin`.
- **CORS**: `OPTIONS` responde `204` con los encabezados CORS sin pasar por el handler.
- **Fechas**: "hoy" es siempre `DATA_AS_OF` (por defecto `2026-09-30`), nunca el reloj. Las fechas van en `YYYY-MM-DD`;
  un formato inválido → `400 invalid_request`.
- **Paginación** (donde aplica): `page` ≥ 1 y `page_size` 1–100; la respuesta agrega `page`, `page_size` y `total`.
- **Tienda inexistente**: los endpoints con `{store_id}` responden `404 store_not_found`.

### `auth-signin`

`POST /auth/signin` · §3.18 · **pública**

Valida correo y contraseña contra `USER#<email>` (hash PBKDF2-SHA256) y abre una sesión `SESSION#<jti>` con TTL.

- **Body**: `{ "email": string, "password": string }` (el correo se normaliza a minúsculas).
- **Respuesta 200**: `{ token, expires_at, user: { id, name, email } }`. El token es un JWT HS256 firmado con `JWT_SECRET`,
  válido 12 h.
- **Errores**: `401 invalid_credentials` (usuario inexistente, contraseña incorrecta o campos vacíos).
- **DynamoDB**: `GetItem USER#…`, `PutItem SESSION#…`.

### `auth-logout`

`POST /auth/logout` · §3.18

Revoca la sesión del token: borra `SESSION#<jti>`, y desde ese momento el mismo token responde `401`.

- **Body**: ninguno.
- **Respuesta 200**: `{ logged_out: true }`.
- **DynamoDB**: `DeleteItem SESSION#…`.

### `meta`

`GET /meta` · §3.1

Datos de arranque del frontend: catálogos y horizontes.

- **Respuesta 200**: `data_as_of`, `history_start` (`2024-01-01`), `forecast_horizon_days` (14),
  `horizons { history_days: [7, 30], forecast_days: [7, 14, 30] }`, `default_plan_days` (7), `max_strategies_per_plan` (5),
  `categories[]` (`category_id`, `category_name`, `is_perishable`, `typical_shelf_life_days`, `requires_refrigeration`;
  booleanos como 0/1), `zones[]` (`zone_type`, `description`) y `demo_mode`.
- `demo_mode` es `true` si existe `DEMO_MAILBOX` o si falta `RESEND_API_KEY`; en este caso, ningún proveedor recibe correos reales.

### `chain-summary`

`GET /chain/summary` · §3.2

Vista "Todas las tiendas": KPIs de la cadena, impacto del portafolio y ranking de tiendas.

- **Query**: `from`, `to` (por defecto los últimos 30 días hasta `data_as_of`; exige `from ≤ to ≤ data_as_of`),
  `mode` (`actual` | `forecast`; se valida y se devuelve, pero no cambia el cálculo), `zone` (filtra por `zone_type`).
- **Respuesta 200**:
  - `period { from, to }` y `mode`.
  - `kpis`: `average_ticket`, `items_per_ticket` y sus `*_delta_pct` contra la ventana anterior de igual longitud;
    `stockout_products` (productos con `on_hand ≤ 0`), `no_demand_products` (productos sin ventas en los últimos 7 días)
    y `ai_opportunity_mxn`.
  - `portfolio_impact`: `net_expected_margin_increment`, `confidence_interval { low, high, level: 0.8 }`,
    `stores_with_plan` y `strategies_count`.
  - `store_ranking[]`: `store_id`, `colonia`, `zone_type`, `opportunity_mxn` y `strategies_count`, ordenado por
    oportunidad descendente.
- Consulta en paralelo, por tienda, el portafolio, el stock, las ventas de 7 días y los `TICKET_DAY`.

### `stores`

`GET /stores` · §3.3

Lista completa de tiendas (25, sin paginación).

- **Respuesta 200**: `items[]` con `store_id`, `store_name`, `colonia`, `latitude`, `longitude`, `zone_type`,
  `secondary_zone_type` (nullable), `size_category`, `opening_hours`, `is_24h` (0/1) y `opened_date`.

### `kpis`

`GET /stores/{store_id}/kpis` · §3.4

Los mismos KPIs de `chain-summary`, pero de una sola tienda.

- **Query**: `from`, `to` y `mode`, con las mismas reglas que `chain-summary`.
- **Respuesta 200**: `{ store_id, period, mode, kpis }`. `kpis` tiene la misma forma que en §3.2 y
  `ai_opportunity_mxn` es el margen neto esperado del plan de la tienda.

### `heat`

`GET /heat` · §3.5

Intensidad de demanda entre tiendas para el mapa de calor.

- **Query**: `mode` (`actual` por defecto | `forecast`), `from`/`to` (por defecto los últimos 30 días en `actual` y los
  próximos 14 días en `forecast`), `product_id` (opcional) y `zone`.
- **Cálculo**: en `actual` suma las unidades vendidas (`DAY` de la tienda o `SALE#` del producto); en `forecast` suma el `p50`
  del pronóstico. `movement` = `round(100 · units / max(units))`.
- **Respuesta 200**: `{ product_id | null, metric: "units_sold", items[] }`, donde cada item es
  `{ store_id, movement, units, is_top }`, ordenado por `store_id`. Sólo una tienda lleva `is_top: true`, y sólo si su
  total es mayor que 0.

### `behaviors`

`GET /stores/{store_id}/behaviors` · §3.6

Comportamientos de compra detectados en la tienda, ordenados por `estimated_gain_mxn` descendente.

- **Query**: `from`/`to` (por defecto de `data_as_of + 1` a `+14`) y `limit` (1–50, por defecto 6).
- **Tipos** (`kind`):
  - `crosssell` (`source: observed`): las 4 reglas de afinidad de `build_tickets.py`, por ejemplo café + conchas.
    Incluye `related_product_id` y `affinity { observed_ticket_share, lift, tickets_sample }`; `lift` es ilustrativo.
  - `waste` (`estimated`): lotes `ACTIVE` que caducan en la ventana. La ganancia es `cantidad × unit_cost`.
  - `demand` (`estimated`): productos cuyo `p50` en la ventana es ≥ 1.2 × el baseline. La ganancia es `uplift × margen`.
- Cada item se enlaza con `strategy_id` (el borrador del plan que promueve ese producto) y con `restock_suggestion_id`
  (la sugerencia de reabasto vigente en los próximos 7 días); ambos campos son nullable.

### `stock`

`GET /stores/{store_id}/stock` · §3.7

Stock por producto, paginado (25 por defecto). Con `store_id = all` devuelve toda la cadena.

- **Query**: `status` (`out` | `low` | `ok` | `over`), `category_id`, `q` (busca en el nombre o el id),
  `sort` (`coverage_days` ascendente | `quantity` ascendente | por defecto, `product_name`) y la paginación.
- **Estado**: `out` si `on_hand ≤ 0`; `low` si `on_hand ≤ reorder_point`; `over` si `on_hand > max_stock`; si no, `ok`.
- **Item**: `store_id`, `product_id`, `product_name`, `category_id`, `closing_qty`, `on_order_qty`,
  `in_transit[] { order_id, quantity, supplier_id, expected_delivery_date, is_delayed }`, `reorder_point`, `max_stock`,
  `coverage_days` (`on_hand / demanda diaria pronosticada`; null si no hay demanda), `status`, `earliest_expiry`,
  `expiring_qty_7d`, `lost_demand_7d`, `next_restock_date` (la siguiente entrega abierta que no está atrasada) y
  `expected_waste_units` (lotes que caducan en 14 días menos la demanda de esos 14 días).

### `stock-summary`

`GET /stock/summary` · §3.8

Conteo por estado con los mismos filtros que §3.7.

- **Query**: `store_id` (opcional; sin él, toda la cadena), `category_id` y `q`.
- **Respuesta 200**: `{ total, out, low, ok, over }`.

### `stock-policy`

`GET` / `PUT /stores/{store_id}/stock-policy` · §3.9

Política mín./máx. por producto. El mínimo es `reorder_point`, el mismo valor que usa `simulate_operations.py`.

- **GET**
  - **Query**: `category_id`, `q` y la paginación (25 por defecto).
  - **Respuesta 200**: `items[]` con `product_id`, `product_name`, `category_id`, `unit_cost` y `min_order_qty`
    (del proveedor por defecto), `retail_price`, `shelf_life_days`, `reorder_point`, `max_stock` y `updated_at`,
    ordenados por `product_id`.
- **PUT**
  - **Body**: `{ items: [{ product_id, reorder_point, max_stock }] }`, con 1–120 productos.
  - **Validación**: si algún item falla, no se guarda nada y la Lambda responde `422 invalid_policy` con
    `details.items[] { product_id, code }`. Los códigos son `product_not_in_store` y
    `must be 0 <= reorder_point <= max_stock`.
  - **Respuesta 200**: `{ store_id, items[] { product_id, reorder_point, max_stock, updated_at }, updated_at }`.
  - **DynamoDB**: un `UpdateItem STORE#…/STOCK#…` por producto.

### `stock-policy-suggest`

`POST /stores/{store_id}/stock-policy:suggest` · §3.10

Sugiere `reorder_point` y `max_stock` a partir del pronóstico de 14 días y el *lead time* del proveedor elegido.
**No persiste nada.**

- **Body**: `{ product_ids?: string[] }` (máximo 120; vacío = todos los productos de la tienda).
- **Respuesta 200**: `{ store_id, items[] { product_id, current { reorder_point, max_stock }, suggested { … }, reason, reason_codes[] } }`.
- **Errores**: `404 product_not_found` con `details.product_ids` si algún id no existe en la tienda.

### `restock-suggestions`

`GET /stores/{store_id}/restock-suggestions` · §3.11

Proyecta el stock día a día con el pronóstico y los pedidos abiertos, y devuelve los pedidos que haría `select_supplier()`.

- **Query**: `from` (por defecto `data_as_of`; un valor anterior se ajusta a `data_as_of`), `to` (por defecto
  `data_as_of + 14`, máximo `data_as_of + 60`), `urgent_only` (`true`/`false`), `product_id` y la paginación (25 por defecto).
- **Item** (`Suggestion`): `id` (`rs_<store>_<product>_<YYYYMMDD>`), `store_id`, `product_id`, `product_name`,
  `order_date`, `expected_delivery_date`, `is_urgent`, `reasons[]` (`below_min`, `stockout`, `demand_spike`),
  `suggested_qty`, `estimated_cost`, `stock_at_order`, `predicted_daily_demand`, `supplier { … }`, `alternatives[]`,
  `expected_lost_sales_if_skipped`, `expected_gain_if_ordered` y `expected_waste_if_ordered`.
- **Orden**: `order_date` ascendente, luego las urgentes primero y luego `expected_lost_sales_if_skipped` descendente.

### `restock-orders`

`POST /restock-orders` · §3.12

Envía la orden de compra al proveedor por correo (Resend), registra el PO y lo suma al *pipeline* de stock.

- **Headers**: `Idempotency-Key` (opcional; si falta, se usa `suggestion_id`).
- **Body**: `{ suggestion_id, supplier_id, qty }`. `suggestion_id` tiene el formato `rs_<store>_<product>_<YYYYMMDD>`.
- **Flujo**:
  1. Valida que la sugerencia no esté vencida (`order_date ≥ data_as_of`), que exista la oferta
     `PRODUCT#…/OFFER#<supplier>`, que `qty ≥ min_order_qty` y que exista la tienda.
  2. Reserva la idempotencia `IDEMP#restock-orders#<key>` con TTL de 7 días y condición `attribute_not_exists`.
  3. Genera el `order_id` `PO-<año>-<nnnnnn>` con el contador atómico `COUNTER/PO`.
  4. Envía el correo HTML. Si `DEMO_MAILBOX` está definido, el correo va a ese buzón.
  5. Hace un `TransactWriteItems`: `PutItem` del PO (`STORE#…/PO#…`, con GSI1 por proveedor y GSI2 por fecha de entrega)
     y `UpdateItem` del stock (`on_order += qty`, agrega el id a `open_order_ids`, `version += 1`).
  6. Guarda la respuesta en el registro de idempotencia con `status: completed`.
- **Respuesta 201**: `order_id`, `suggestion_id`, `store_id`, `product_id`, `supplier_id`, `qty`, `unit_cost`,
  `estimated_cost`, `order_date`, `expected_delivery_date` (`order_date + lead_time_days`), `status: "sent"`, `sent_at`,
  `email { to, subject, provider_message_id }` y `demo_redirect`.
- **Reintentos**: con la misma llave, un pedido ya completado devuelve la misma respuesta `201`; uno en curso devuelve
  `409 request_in_progress`. Si falla el envío del correo, se libera la llave. Si falla la transacción después de
  enviar el correo, se conserva la llave para no enviarlo dos veces.
- **Errores**: `400` (body inválido), `422 suggestion_expired`, `404 offer_not_found`, `422 below_min_order_qty`
  (con `details.min_order_qty`), `404 store_not_found` y `502 email_failed`.

### `calendar`

`GET /stores/{store_id}/calendar` · §3.13

Calendario por capas de la tienda.

- **Query**: `from`/`to` (por defecto, el mes de `data_as_of`; máximo 62 días) y `layers` (lista separada por comas de
  `deliveries`, `strategies`, `alerts`, `context`; por defecto todas). Una capa desconocida → `400`.
- **Respuesta 200**: `{ store_id, from, to, events[], context[] }`. `events` va ordenado por `date` y luego por `id`.
  - `delivery`: entregas de POs (la fecha real si ya llegó, si no la esperada), con `order_id`, `qty` y `delivered`.
  - `strategy`: borradores del plan cuyo periodo cruza el rango; excluye `draft`, `rejected` y `expired`.
  - `alert`, que se deriva al leer:
    - `order_delayed`: el pedido llegó o va tarde. Lleva `delay_days` y es `high` si son 3 días o más.
    - `expiring_soon`: lotes `ACTIVE` que caducan en el rango. Lleva `value_at_risk` y es `high` si caducan en 2 días o menos.
    - `stockout_risk`: quiebres que quedan aun siguiendo las sugerencias de reabasto. Lleva `expected_lost_units` y es
      `high` si ocurren en 3 días o menos.
  - `context[]`: un registro por día con festivo, eventos, quincena, vacaciones, clima e `is_forecast`.

### `suppliers`

`GET /suppliers` y `GET /suppliers/{supplier_id}/offers` · §3.14 · sólo lectura

La misma Lambda atiende las dos rutas; la distingue la presencia de `supplier_id` en el path.

- **`/suppliers`**: `items[]` con `supplier_id`, `supplier_name`, `email`, `city`, `categories_served[]`,
  `reliability_score` y `offers_count`, ordenados por id.
- **`/suppliers/{id}/offers`**
  - **Query**: `category_id`, `q` y la paginación (25 por defecto).
  - **Respuesta**: `items[]` con `product_id`, `product_name`, `category_id`, `unit_cost`, `min_order_qty`,
    `lead_time_days`, `delivery_days` (unidos con `|`) y `retail_price`.
  - **Errores**: `404 supplier_not_found`.

### `supplier-test-email`

`POST /suppliers/{supplier_id}/test-email` · §3.15

Envía un correo de prueba al proveedor, con la misma redirección a `DEMO_MAILBOX` que §3.12.

- **Respuesta 200**: `{ supplier_id, status: "sent", sent_at, email { to, provider_message_id }, demo_redirect }`.
- **Errores**: `404 supplier_not_found` y `502 email_failed`.

### `products`

`GET /products` · §3.16

Catálogo de productos.

- **Query**: `category_id`, `q` (busca en el nombre, la marca o el id) y la paginación (25 por defecto).
- **Item**: `product_id`, `product_name`, `category_id`, `brand`, `unit`, `retail_price`, `shelf_life_days`,
  `is_seasonal` (0/1), `weather_sensitivity`, `base_daily_demand` y `default_supplier`.

### `chat`

`POST /chat` · §3.17

Orquestador por reglas (`main.go`, `parse.go`, `strategy.go`) que usa los mismos nombres de herramientas que el agente
LangGraph. **Nunca aprueba ni activa estrategias.**

- **Body**: `{ store_id, conversation_id?, message }`. `message` tiene 1–2000 caracteres.
- **Interpretación** (`parse.go`): normaliza el texto (minúsculas, sin acentos) y detecta:
  - **Intención**, en este orden de prioridad: `approve` → `promo` → `restock` → `forecast`; si no hay ninguna, `context`.
  - **Descuento máximo**: el patrón `NN %` o `NN por ciento`.
  - **Día de la semana**: en español.
  - **Categoría**: por palabras clave, de C01 a C20.
  - **Productos**: por nombre o marca.
- **Intenciones**:
  - `approve`: responde que la aprobación se hace desde el plan.
  - `promo`: simula una `contextual_offer`. Prueba descuentos de 1 % en 1 % hasta el máximo (25 % por defecto), con
    elasticidad de 4.0, margen mínimo de 12 % y presupuesto de $2,500. Guarda un borrador `DRAFT#…` con `status: draft`
    (o `rejected` si ninguna opción cumple las restricciones) y `requires_approval: true`. Devuelve los bloques
    `strategy_card` y `plan_summary` (plan actual vs. plan con el borrador, más los traslapes).
  - `restock`: devuelve las 5 sugerencias más importantes de los próximos 7 días en un bloque `restock_table`.
  - `forecast`: devuelve un `forecast_chart` con 28 días de historia y el pronóstico p50/p80/p90 del producto con mayor
    demanda entre los candidatos.
  - `context`: resume los pedidos de la semana y los riesgos de quiebre.
- **Persistencia**: crea `CONV#<id>` si no llega `conversation_id`. Si llega, debe pertenecer al usuario y a la tienda;
  si no, responde `404 conversation_not_found`. Guarda los dos mensajes (`MSG#…#1` del usuario y `#2` del asistente).
- **Respuesta 200**: `{ conversation_id, message_id, tools[] { name, args }, interpreted_constraints { product_ids, category_id, weekday, max_discount_pct }, blocks[] }`.
- **Errores**: `422 store_required` y `400` si `message` está vacío o es demasiado largo.

### `send-email`

`POST /emails` · §3.19

Envía un correo libre por Resend a la dirección indicada. `DEMO_MAILBOX` **no** aplica: el destinatario lo elige el usuario.

- **Body**: `{ to, subject, text?, htmlBody?, from_name? }`. Se requiere `text` o `htmlBody`, cada uno de menos de 1 MiB.
  `htmlBody` se envía tal cual, así que el frontend puede renderizarlo, por ejemplo, con react-email. `from_name` es
  `StockCopilot` por defecto.
- **Respuesta 201**: `{ status: "sent", sent_at, email { to, subject, provider_message_id } }`.
- **Errores**: `400` (correo inválido, falta `subject` o falta el cuerpo), `413 body_too_large` y `502 email_failed`.

### `predictions-stores`

`GET /predictions/stores` · §3.20

Pronóstico de demanda de todas las tiendas, para el mapa de demanda.

- **Query**: `product_id` (una fila por tienda para ese producto), `category_id` (suma de los productos de la
  categoría), `tag` (se devuelve, pero aún no filtra) y `horizon_days` (1–30, por defecto 7; se devuelve, pero no cambia
  el cálculo).
- **Fila** (`storeRow`): `store_id`, `product_id` (null si es un agregado), `products_count`, `current_daily_demand`
  (promedio vendido en 14 días), `predicted_daily_demand`, `uplift_pct`, `confidence` (1 − dispersión relativa p80/p50),
  `closing_qty`, `on_order_qty`, `coverage_days`, `recommended_extra_qty` (`max(0, pred × 4 − on_hand)`), `risk` (el peor
  entre los productos: `critical` | `watch` | `healthy`) y `reason_codes[]` (máximo 3: `weather_cold`, `weather_hot`,
  `payday`, `holiday`, `commute_peak`).
- **Respuesta 200**: `{ product_id, horizon_days, target_days: 4, filters { category_id, tag }, summary { stores_count, current_daily_demand, predicted_daily_demand, average_uplift_pct, stores_at_risk, recommended_extra_qty }, items[] }`.
  Si ningún producto coincide con los filtros, devuelve `items` vacío y `summary` en ceros.
- **Riesgo**:
  - `critical`: no hay stock o la cobertura es ≤ el *lead time* del proveedor elegido.
  - `watch`: `on_hand ≤ reorder_point` o la cobertura es ≤ *lead time* + días de seguridad.
  - `healthy`: cualquier otro caso.

### `predictions-products`

`GET /stores/{store_id}/predictions/products` · §3.21

Productos de una tienda ordenados por prioridad, para el panel lateral del mapa de demanda. Cada fila tiene la misma
forma que en §3.20, sin `store_id` ni `products_count` y con `product_id`.

- **Query**: `category_id`, `tag` (se devuelve, pero no filtra), `horizon_days` (1–30, se devuelve),
  `sort` (`priority` por defecto | `predicted_daily_demand` | `uplift_pct` | `recommended_extra_qty`, todos descendentes)
  y la paginación (**8** por defecto).
- **`priority`**: primero el riesgo (`critical` > `watch` > `healthy`), luego `recommended_extra_qty` y luego
  `predicted_daily_demand`.
- **Respuesta 200**: `{ store_id, horizon_days, target_days, filters, items[], page, page_size, total }`.

## Variables de entorno de las Lambdas

| Variable | Uso |
|---|---|
| `TABLE_NAME` | `road2tech-stores-v2` (tabla activa; el código usa `road2tech-stores` si no se define) |
| `DATA_AS_OF` | `2026-09-30` (hoy de la demo) |
| `JWT_SECRET` | firma de tokens (generado al desplegar) |
| `CORS_ALLOW_ORIGIN` | `*` (restringir al dominio del frontend) |
| `RESEND_API_KEY` | sólo en `restock-orders`, `supplier-test-email` y `send-email` (`scripts/set-mail-env.sh`); sin definir → los correos sólo se registran en CloudWatch (`provider_message_id = log_…`) |
| `EMAIL_FROM_ADDRESS` | `no-reply@tindertec.com`, remitente verificado en Resend (por defecto `onboarding@resend.dev`) |
| `DEMO_MAILBOX` | si existe, los correos a proveedores (§3.12, §3.15) van a ese buzón y la respuesta trae `demo_redirect: true`; `/emails` lo ignora |

## Infraestructura (us-east-1)

- DynamoDB `road2tech-stores-v2` (on-demand, GSI1, GSI2, TTL `ttl`), creada con *Import from S3* desde
  `dynamo-import/v2/` (dataset regenerado con `run_all.py`, 353,087 ítems, incluye `TICKET_DAY` y `lost_demand_7d`).
  `road2tech-stores` es la carga anterior (dataset viejo, sin tickets).
- Lambdas `road2tech-<carpeta>`: `provided.al2023`, arm64, rol `road2tech-api-lambda` (DynamoDB sólo sobre la tabla + logs).
- API Gateway HTTP API `road2tech-api` (`$default`, auto-deploy, CORS, throttling 25 rps / ráfaga 50).
- Bucket privado `road2tech-artifacts-510936382741`: zips de Lambdas y archivos de importación.

## Desarrollo

```bash
cd api
go test ./...
./scripts/build.sh            # dist/lambdas/*.zip
./scripts/deploy.sh chat      # redeploy de una Lambda (requiere aws CLI con sesión)
python3 ../scripts/dynamo/build_import.py   # regenera dist/dynamo-import/*.json.gz
```

Recargar la tabla desde cero: subir `dist/dynamo-import/*.json.gz` a `s3://road2tech-artifacts-510936382741/dynamo-import/<versión>/`
y ejecutar `ImportTable` hacia una tabla nueva (Import from S3 siempre crea la tabla). Después: agregar la tabla al
permiso del rol `road2tech-api-lambda` y cambiar `TABLE_NAME` en todas las Lambdas. Las sesiones viven en la tabla,
así que al cambiarla hay que volver a iniciar sesión.

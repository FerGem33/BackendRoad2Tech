# Dataset del Hackathon – Simulador de tiendas OXXO (México)

## ⚠️ Aviso legal y descargo de responsabilidad

- Este dataset es **100 % sintético**. Fue generado para uso exclusivo en este hackathon y **no contiene información real, confidencial ni sensible** de OXXO, FEMSA ni de ninguna otra empresa, persona, cliente o proveedor.
- Las ventas, inventarios, KPIs, precios, productos y proveedores son **ficticios**. Las ciudades y zonas son reales solo como referencia geográfica.
- **No debe utilizarse fuera del hackathon** ni presentarse como datos reales de ninguna compañía.
- Los datos se entregan "tal cual", sin garantía alguna.

## Contexto

Datos operativos de una red simulada de **38 tiendas OXXO** en **20 plazas/zonas de México** (aeropuertos, estaciones de metro, centrales de autobuses, gasolineras, hospitales, zonas urbanas, turísticas e industriales). Sirve para administrar tiendas, inventarios y KPIs: se vende producto perecedero de vida corta (1–3 días), hay proveedores que entregan incompleto o tarde, y conviven **quiebres de stock** y **mermas**.

- **Periodo:** 2025-10-01 → 2026-09-30 (365 días)
- **Alcance:** 20 sitios y 38 tiendas (algunas abrieron, cerraron definitivamente o cerraron temporalmente durante el año)
- **Moneda:** MXN (pesos mexicanos; precios de venta redondeados a $0.50)
- **Formato:** CSV, UTF-8, separador coma, fechas ISO `YYYY-MM-DD`

## Tablas

| Archivo                | Granularidad                                                    | Llaves                                 |
| ---------------------- | --------------------------------------------------------------- | -------------------------------------- |
| `sites.csv`            | un registro por sitio/zona                                      | `site_id`                              |
| `stores.csv`           | un registro por tienda                                          | `store_id`, `site_id`                  |
| `store_closures.csv`   | ventanas de cierre de tienda                                    | `store_id`, `start_date`, `end_date`   |
| `products.csv`         | maestro de productos (177 SKUs)                                 | `sku`                                  |
| `product_barcodes.csv` | códigos de barras EAN-13 (prefijo 750) por SKU                  | `barcode`, `sku`                       |
| `suppliers.csv`        | proveedores y condiciones contractuales                         | `supplier_id`                          |
| `prices.csv`           | lista de precios por tienda con vigencia                        | `store_id`, `sku`, `effective_from/to` |
| `sales_daily.csv`      | unidades e ingresos por tienda/SKU/día                          | `date`, `store_id`, `sku`              |
| `inventory_daily.csv`  | movimiento de inventario por tienda/SKU/día (SKUs inventariados) | `date`, `store_id`, `sku`              |
| `purchase_orders.csv`  | líneas de órdenes de reabastecimiento                           | `po_id`                                |
| `store_daily_kpis.csv` | tráfico, tickets, ventas y horas de personal por tienda/día     | `store_id`, `date`                     |
| `promotions.csv`       | calendario de promociones                                       | `promo_id`                             |
| `calendar.csv`         | dimensión de fechas con días festivos de México y calendario SEP | `date`                                 |
| `weather_daily.csv`    | temperatura y precipitación diaria por sitio                    | `site_id`, `date`                      |

### Relaciones

`sales_daily`, `inventory_daily`, `purchase_orders`, `prices` → `products` (`sku`) y `stores` (`store_id`) → `sites` (`site_id`). `products.supplier_id` y `purchase_orders.supplier_id` → `suppliers`. `sales_daily.promo_id` → `promotions`. Las fechas se unen con `calendar`; `weather_daily` se une por `site_id` + `date`.

## Diccionario de datos (columnas clave)

**sites**: `site_type` (Aeropuerto, Estación de Metro, Central de Autobuses, Gasolinera, Hospital, Zona Urbana, Zona Turística, Zona Universitaria, Zona Corporativa, Parque Industrial), `city`, `state`, `region` (Noreste, Noroeste, Occidente, Bajío, Centro, Sur, Sureste), `traffic_tier` (nivel relativo de tráfico).

**stores**: `format` (Tradicional, Compacta, Kiosko), `operator_type` (Corporativa / Comisionista), `opening_time`/`closing_time` (00:00/00:00 = 24 h), `opened_date`/`closed_date`, `area_m2`.

**products**: jerarquía `department` → `category` → `subcategory`; `brand` (Andatti, Marca Propia, Sin Marca); `unit_cost` y `list_price_base` en MXN; `case_size` (los pedidos son múltiplos); `shelf_life_days`; `is_perishable`; `storage_type` (Ambiente / Refrigerado / Congelado); `inventory_tracked = 1` → el SKU aparece en `inventory_daily` y `purchase_orders` (las bebidas preparadas al momento se venden pero no se inventarían); `status` (Activo / Descontinuado). Los SKUs con precio 0 son componentes incluidos en combos o extras sin cargo.

**prices**: `effective_to` vacío = precio vigente. En marzo de 2026 hubo un cambio de precios en parte del catálogo.

**sales_daily**: `gross_sales = units_sold × unit_price`, `net_sales = gross_sales − discount_amount`. El descuento incluye pequeños descuentos de lealtad sin promoción.

**inventory_daily**: `closing_qty = opening_qty + received_qty − units_sold − expired_qty − damaged_qty`; `stockout_hours` (horas sin existencia con la tienda abierta); `on_order_qty` (en tránsito al cierre).

**purchase_orders**: `status` (Entregada / Entrega parcial / En tránsito); `qty_received` puede ser menor que `qty_ordered`.

**store_daily_kpis**: `footfall` (personas que pasan), `transactions` (tickets), `units_sold` y `net_sales` (suma exacta de `sales_daily`), `staff_hours`, `open_hours`.

**promotions**: tipo (Porcentaje / Paquete), vigencia, % de descuento, alcance (departamento/categoría, `region_scope` Nacional o región) y uplift esperado.

**calendar**: día de la semana, semana ISO, mes, trimestre, estación, fin de semana, festivo (con nombre: días de descanso oficiales, Buen Fin, Día de Muertos, Guadalupe, Semana Santa, etc.) y vacaciones escolares SEP.

**weather_daily**: `temp_max_c`, `temp_min_c` (°C) y `precip_mm`, ajustados al clima mensual típico de cada ciudad.

## Limpieza aplicada

El extracto original tenía errores de calidad intencionales. Se corrigieron así:

- **Formatos**: fechas `MM/DD/YYYY` y `DD-Mon-YY` a ISO; IDs de tienda (`S0001`, `s018`) y SKU (`sku-…`, `SKU100…`, espacios) normalizados; moneda unificada; `#N/A` y `-999` tratados como faltantes.
- **Duplicados**: filas exactas eliminadas en ventas, inventario, precios, KPIs y clima; SKUs, `po_id` y llaves de venta duplicados resueltos.
- **Referencias inválidas**: eliminadas tiendas fantasma (S039 duplicado de S004, S040/ST99, S099/S100/S120 en precios), SKUs de venta inexistentes en el catálogo y el código de promoción `PR999`; se quitó la promoción `PR013` (ninguna venta la usa) y se corrigió la fecha de fin de `PR003`.
- **Ventas**: unidades negativas (error de signo), unidades ×10, unidades faltantes, precios unitarios incoherentes con el importe y precios de marcador $0.01; se quitaron ventas fechadas antes de la apertura o durante un cierre de la tienda.
- **Precios / costos**: precios 0, ×10, ×100 o vacíos reparados con el precio real cobrado; vigencias invertidas; costos 0, negativos o atípicos en productos y órdenes de compra.
- **Inventario**: `closing_qty` recalculada cuando no cumplía la ecuación (negativos, decimales), aperturas en 0 con ventas restauradas, `stockout_hours` limitadas al horario de la tienda.
- **Órdenes de compra**: fechas esperadas faltantes, fechas de recepción anteriores al pedido, recibido > pedido, estatus incoherentes y proveedor distinto al del producto.
- **KPIs**: tickets mayores que unidades, tráfico 0 o vacío y horas de personal negativas o `999` imputadas con la mediana de cada tienda; ventas y unidades recalculadas desde `sales_daily`.
- **Catálogo**: se conservaron solo los SKUs usados en transacciones o activos; categorías inconsistentes corregidas; fechas de lanzamiento/descontinuación invertidas y estatus incoherentes corregidos.

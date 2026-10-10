# API StockCopilot (Go + AWS Lambda)

Endpoints §3.1–§3.19 de [`ENDPOINTS.md`](ENDPOINTS.md), una Lambda por carpeta, detrás de un API Gateway HTTP API, sobre la
tabla única de DynamoDB descrita en [`docs/DYNAMO.md`](../docs/DYNAMO.md).

Base URL: `https://avbylri05m.execute-api.us-east-1.amazonaws.com`

| § | Método y ruta | Lambda (`functions/…`) |
|---|---|---|
| 3.1 | `GET /meta` | `meta` |
| 3.2 | `GET /chain/summary` | `chain-summary` |
| 3.3 | `GET /stores` | `stores` |
| 3.4 | `GET /stores/{store_id}/kpis` | `kpis` |
| 3.5 | `GET /heat` | `heat` |
| 3.6 | `GET /stores/{store_id}/behaviors` | `behaviors` |
| 3.7 | `GET /stores/{store_id}/stock` | `stock` |
| 3.8 | `GET /stock/summary` | `stock-summary` |
| 3.9 | `GET` / `PUT /stores/{store_id}/stock-policy` | `stock-policy` |
| 3.10 | `POST /stores/{store_id}/stock-policy:suggest` | `stock-policy-suggest` |
| 3.11 | `GET /stores/{store_id}/restock-suggestions` | `restock-suggestions` |
| 3.12 | `POST /restock-orders` | `restock-orders` |
| 3.13 | `GET /stores/{store_id}/calendar` | `calendar` |
| 3.14 | `GET /suppliers`, `GET /suppliers/{supplier_id}/offers` | `suppliers` |
| 3.15 | `POST /suppliers/{supplier_id}/test-email` | `supplier-test-email` |
| 3.16 | `GET /products` | `products` |
| 3.17 | `POST /chat` | `chat` |
| 3.18 | `POST /auth/signin` | `auth-signin` |
| 3.18 | `POST /auth/logout` | `auth-logout` |
| 3.19 | `POST /emails` | `send-email` |

Todas, salvo `/auth/signin`, piden `Authorization: Bearer <token>`. Usuario demo: `demo@stockcopilot.com` / `demo1234`.

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

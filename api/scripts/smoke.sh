#!/usr/bin/env bash
# Calls every endpoint once against the deployed API and prints status + a short summary.
set -uo pipefail
API=${API:-https://avbylri05m.execute-api.us-east-1.amazonaws.com}
J='content-type: application/json'
TOKEN=$(curl -s -X POST "$API/auth/signin" -H "$J" -d '{"email":"demo@stockcopilot.com","password":"demo1234"}' | jq -r .data.token)
A="authorization: Bearer $TOKEN"
call() { # method path [body]
  local out code
  out=$(curl -s -w '\n%{http_code}' -X "$1" "$API$2" -H "$A" -H "$J" ${3:+-d "$3"})
  code=$(tail -n1 <<<"$out"); body=$(sed '$d' <<<"$out")
  echo "== $code $1 $2"; jq -c "${JQ:-.data | keys}" <<<"$body" 2>/dev/null || echo "$body" | head -c 300; echo
}
call POST /auth/signin '{"email":"demo@stockcopilot.com","password":"bad"}'
JQ='{cats: (.data.categories|length), zones: (.data.zones|length), as_of: .data.data_as_of, demo: .data.demo_mode}' call GET /meta
JQ='{period: .data.period, kpis: .data.kpis, ranking: (.data.store_ranking|length), plan: .data.portfolio_impact.stores_with_plan}' call GET '/chain/summary'
JQ='{n: (.data.items|length), first: .data.items[0]}' call GET /stores
JQ='.data.kpis' call GET '/stores/S001/kpis'
JQ='{n: (.data.items|length), top: [.data.items[] | select(.is_top) | {store_id, movement, units}]}' call GET '/heat?product_id=P001'
JQ='{n: (.data.items|length), kinds: ([.data.items[].kind] | unique)}' call GET '/stores/S001/behaviors'
JQ='{n: (.data.items|length), summary: .data.summary, first: (.data.items[0] | {store_id, product_id, predicted_daily_demand, risk, recommended_extra_qty})}' call GET '/predictions/stores?product_id=P001'
JQ='{total: .data.total, first: (.data.items[0] | {product_id, predicted_daily_demand, risk, recommended_extra_qty})}' call GET '/stores/S001/predictions/products?page_size=2'
JQ='{total: .data.total, first: .data.items[0] | {product_id, status, closing_qty, coverage_days, next_restock_date}}' call GET '/stores/S001/stock?page_size=2'
JQ='.data' call GET '/stock/summary'
JQ='{total: .data.total, first: .data.items[0]}' call GET '/stores/S001/stock-policy?page_size=2'
JQ='.data.items[:2]' call POST '/stores/S001/stock-policy:suggest' '{"product_ids":["P001","P059"]}'
JQ='{total: .data.total, first: .data.items[0] | {id,order_date,expected_delivery_date,is_urgent,reasons,suggested_qty,estimated_cost,supplier: .supplier.supplier_id, alts: (.alternatives|length)}}' call GET '/stores/S001/restock-suggestions?page_size=5'
JQ='{events: (.data.events|length), kinds: ([.data.events[] | (.alert_type // .kind)] | group_by(.) | map({(.[0]): length}) | add), context: (.data.context|length)}' call GET '/stores/S001/calendar?from=2026-09-20&to=2026-10-14'
JQ='{n: (.data.items|length), first: .data.items[0]}' call GET /suppliers
JQ='{total: .data.total, first: .data.items[0]}' call GET '/suppliers/SUP01/offers?page_size=2'
JQ='.data' call POST /suppliers/SUP01/test-email '{}'
JQ='.data' call POST /emails '{"to":"demo@stockcopilot.com","subject":"Prueba","text":"Hola desde smoke","from_name":"StockCopilot"}'
JQ='.' call POST /emails '{"to":"no-valido","subject":"x","text":"hola"}'
JQ='{total: .data.total, first: .data.items[0]}' call GET '/products?q=coca&page_size=2'
JQ='{tools: [.data.tools[].name], blocks: [.data.blocks[].type], text: .data.blocks[0].text, ic: .data.interpreted_constraints}' call POST /chat '{"store_id":"S001","conversation_id":null,"message":"Aumenta las energéticas el sábado con máximo 10% de descuento"}'
JQ='{blocks: [.data.blocks[].type], text: .data.blocks[0].text}' call POST /chat '{"store_id":"S001","conversation_id":null,"message":"¿Qué tengo que pedir esta semana?"}'
JQ='{blocks: [.data.blocks[].type], text: .data.blocks[0].text}' call POST /chat '{"store_id":"S001","conversation_id":null,"message":"Pronóstico de Coca-Cola 600 ml"}'
JQ='.data.blocks[0].text' call POST /chat '{"store_id":"S001","conversation_id":null,"message":"actívala ya"}'
JQ='.' call POST /chat '{"store_id":"","message":"hola"}'
SUG=$(curl -s "$API/stores/S001/restock-suggestions?page_size=1" -H "$A" | jq -c '.data.items[0] | {suggestion_id: .id, supplier_id: .supplier.supplier_id, qty: .suggested_qty}')
JQ='.' call POST /restock-orders "$(jq -c '.qty = 1' <<<"$SUG")"
JQ='.data | {order_id, status, email, demo_redirect}' call POST /restock-orders "$SUG"
JQ='.data | {order_id, status}' call POST /restock-orders "$SUG"
JQ='.data' call PUT '/stores/S001/stock-policy' '{"items":[{"product_id":"P001","reorder_point":120,"max_stock":480}]}'
JQ='.data' call POST /auth/logout
JQ='.' call GET /products

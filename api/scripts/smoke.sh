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
JQ='.data.items[:2]' call POST '/stores/S001/stock-policy:suggest' '{"product_ids":["P001","P059"]}'
JQ='{total: .data.total, first: .data.items[0] | {id,order_date,expected_delivery_date,is_urgent,reasons,suggested_qty,estimated_cost,supplier: .supplier.supplier_id, alts: (.alternatives|length)}}' call GET '/stores/S001/restock-suggestions?page_size=5'
JQ='{events: (.data.events|length), kinds: ([.data.events[] | (.alert_type // .kind)] | group_by(.) | map({(.[0]): length}) | add), context: (.data.context|length)}' call GET '/stores/S001/calendar?from=2026-09-20&to=2026-10-14'
JQ='{n: (.data.items|length), first: .data.items[0]}' call GET /suppliers
JQ='{total: .data.total, first: .data.items[0]}' call GET '/suppliers/SUP01/offers?page_size=2'
JQ='.data' call POST /suppliers/SUP01/test-email '{}'
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
JQ='.data' call POST /auth/logout
JQ='.' call GET /products

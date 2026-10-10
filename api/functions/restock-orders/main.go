// POST /restock-orders — ENDPOINTS.md §3.12. Emails the purchase order to the supplier (Resend), records the PO
// and adds it to the stock pipeline. Idempotent by Idempotency-Key (or suggestion_id when the header is absent).
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"html"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/mail"
)

type body struct {
	SuggestionID string `json:"suggestion_id"`
	SupplierID   string `json:"supplier_id"`
	Qty          int    `json:"qty"`
}

type emailOut struct {
	To                string `json:"to"`
	Subject           string `json:"subject"`
	ProviderMessageID string `json:"provider_message_id"`
}

type orderOut struct {
	OrderID              string   `json:"order_id"`
	SuggestionID         string   `json:"suggestion_id"`
	StoreID              string   `json:"store_id"`
	ProductID            string   `json:"product_id"`
	SupplierID           string   `json:"supplier_id"`
	Qty                  int      `json:"qty"`
	UnitCost             float64  `json:"unit_cost"`
	EstimatedCost        float64  `json:"estimated_cost"`
	OrderDate            string   `json:"order_date"`
	ExpectedDeliveryDate string   `json:"expected_delivery_date"`
	Status               string   `json:"status"`
	SentAt               string   `json:"sent_at"`
	Email                emailOut `json:"email"`
	DemoRedirect         bool     `json:"demo_redirect"`
}

type idempotency struct {
	PK         string `dynamodbav:"PK"`
	SK         string `dynamodbav:"SK"`
	EntityType string `dynamodbav:"entity_type"`
	Status     string `dynamodbav:"status"`
	Response   string `dynamodbav:"response,omitempty"`
	CreatedAt  string `dynamodbav:"created_at"`
	TTL        int64  `dynamodbav:"ttl"`
}

// parseSuggestion splits "rs_S001_P001_20260930".
func parseSuggestion(id string) (store, product, date string, ok bool) {
	p := strings.Split(id, "_")
	if len(p) != 4 || p[0] != "rs" || len(p[3]) != 8 {
		return "", "", "", false
	}
	date = p[3][:4] + "-" + p[3][4:6] + "-" + p[3][6:]
	if _, err := domain.ParseDate(date); err != nil {
		return "", "", "", false
	}
	return p[1], p[2], date, true
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	var b body
	if err := httpx.Decode(r, &b); err != nil {
		return 0, nil, err
	}
	storeID, productID, orderDate, ok := parseSuggestion(b.SuggestionID)
	if !ok || b.SupplierID == "" || b.Qty <= 0 {
		return 0, nil, httpx.BadRequest("Se requieren suggestion_id válido, supplier_id y qty > 0.")
	}
	if orderDate < httpx.DataAsOf() {
		return 0, nil, httpx.Errorf(422, "suggestion_expired", "La sugerencia %s ya venció.", b.SuggestionID)
	}

	var offer db.Offer
	found, err := db.Get(ctx, "PRODUCT#"+productID, "OFFER#"+b.SupplierID, &offer)
	if err != nil {
		return 0, nil, err
	}
	if !found {
		return 0, nil, httpx.NotFound("offer_not_found", "El proveedor %s no ofrece %s.", b.SupplierID, productID)
	}
	if b.Qty < offer.MinOrderQty {
		e := httpx.Errorf(422, "below_min_order_qty", "La cantidad mínima de pedido es %d.", offer.MinOrderQty)
		e.Details = map[string]any{"min_order_qty": offer.MinOrderQty}
		return 0, nil, e
	}
	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	if store == nil {
		return 0, nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}

	key := httpx.Header(r, "Idempotency-Key")
	if key == "" {
		key = b.SuggestionID
	}
	idemPK := "IDEMP#restock-orders#" + key
	now := time.Now().UTC()
	err = db.Put(ctx, idempotency{PK: idemPK, SK: "#META", EntityType: "IDEMPOTENCY", Status: "in_progress",
		CreatedAt: now.Format(time.RFC3339), TTL: now.Add(7 * 24 * time.Hour).Unix()}, "attribute_not_exists(PK)")
	if db.IsConditionFailed(err) {
		var prev idempotency
		if _, err := db.Get(ctx, idemPK, "#META", &prev); err != nil {
			return 0, nil, err
		}
		if prev.Status == "completed" {
			var out orderOut
			if err := json.Unmarshal([]byte(prev.Response), &out); err != nil {
				return 0, nil, err
			}
			return 201, out, nil
		}
		return 0, nil, httpx.Errorf(409, "request_in_progress", "El pedido ya se está procesando.")
	} else if err != nil {
		return 0, nil, err
	}
	release := func() { _ = db.Delete(context.WithoutCancel(ctx), idemPK, "#META") }

	n, err := db.NextCounter(ctx, "PO")
	if err != nil {
		release()
		return 0, nil, err
	}
	orderID := fmt.Sprintf("PO-%s-%06d", orderDate[:4], n)
	delivery := domain.AddDays(orderDate, offer.LeadTimeDays)
	total := httpx.Money(float64(b.Qty) * offer.UnitCost)
	brand := "Retail " + store.Colonia

	sent, err := mail.Send(ctx, mail.Message{
		FromName: brand, To: offer.SupplierEmail,
		Subject: fmt.Sprintf("Orden de compra %s — %s", orderID, brand),
		HTML:    orderHTML(orderID, brand, offer, b.Qty, total, orderDate, delivery),
	})
	if err != nil {
		release()
		return 0, nil, err
	}
	sentAt := time.Now().UTC().Format(time.RFC3339)

	po := db.PurchaseOrder{
		PK: "STORE#" + storeID, SK: "PO#" + orderDate + "#" + orderID, EntityType: "PO",
		GSI1PK: "SUPPLIER#" + b.SupplierID, GSI1SK: "PO#" + orderDate + "#" + orderID,
		GSI2PK: "DELIVERY#" + delivery, GSI2SK: "STORE#" + storeID + "#" + orderID,
		OrderID: orderID, StoreID: storeID, ProductID: productID, ProductName: offer.ProductName,
		SupplierID: b.SupplierID, SupplierName: offer.SupplierName, SupplierEmail: offer.SupplierEmail,
		OrderDate: orderDate, Quantity: b.Qty, UnitCost: offer.UnitCost, TotalCost: total,
		LeadTimeDays: offer.LeadTimeDays, ExpectedDeliveryDate: delivery, Status: "PLACED",
		SuggestionID: b.SuggestionID, SentAt: sentAt,
	}
	poItem, err := attributevalue.MarshalMap(po)
	if err != nil {
		return 0, nil, err
	}
	_, err = db.Client(ctx).TransactWriteItems(ctx, &dynamodb.TransactWriteItemsInput{TransactItems: []types.TransactWriteItem{
		{Put: &types.Put{TableName: aws.String(db.Table()), Item: poItem}},
		{Update: &types.Update{
			TableName:        aws.String(db.Table()),
			Key:              db.Key("STORE#"+storeID, "STOCK#"+productID),
			UpdateExpression: aws.String("SET on_order = on_order + :q, open_order_ids = list_append(if_not_exists(open_order_ids, :empty), :ids), version = version + :one, updated_at = :now"),
			ExpressionAttributeValues: map[string]types.AttributeValue{
				":q": &types.AttributeValueMemberN{Value: fmt.Sprint(b.Qty)}, ":one": &types.AttributeValueMemberN{Value: "1"},
				":ids":   &types.AttributeValueMemberL{Value: []types.AttributeValue{db.S(orderID)}},
				":empty": &types.AttributeValueMemberL{Value: []types.AttributeValue{}}, ":now": db.S(sentAt),
			},
		}},
	}})
	if err != nil {
		// The email already left: keep the idempotency record so a retry does not send it twice.
		return 0, nil, err
	}

	out := orderOut{OrderID: orderID, SuggestionID: b.SuggestionID, StoreID: storeID, ProductID: productID,
		SupplierID: b.SupplierID, Qty: b.Qty, UnitCost: offer.UnitCost, EstimatedCost: total, OrderDate: orderDate,
		ExpectedDeliveryDate: delivery, Status: "sent", SentAt: sentAt,
		Email:        emailOut{To: sent.To, Subject: sent.Subject, ProviderMessageID: sent.ProviderMessageID},
		DemoRedirect: sent.DemoRedirect}
	raw, _ := json.Marshal(out)
	_ = db.Put(ctx, idempotency{PK: idemPK, SK: "#META", EntityType: "IDEMPOTENCY", Status: "completed",
		Response: string(raw), CreatedAt: now.Format(time.RFC3339), TTL: now.Add(7 * 24 * time.Hour).Unix()}, "")
	return 201, out, nil
}

func orderHTML(orderID, brand string, o db.Offer, qty int, total float64, orderDate, delivery string) string {
	e := html.EscapeString
	return fmt.Sprintf(`<p>Hola %s,</p>
<p>%s solicita el siguiente pedido:</p>
<table cellpadding="6" style="border-collapse:collapse">
<tr><td><b>Orden</b></td><td>%s</td></tr>
<tr><td><b>Producto</b></td><td>%s (%s)</td></tr>
<tr><td><b>Cantidad</b></td><td>%d</td></tr>
<tr><td><b>Costo unitario</b></td><td>$%.2f MXN</td></tr>
<tr><td><b>Total estimado</b></td><td>$%.2f MXN</td></tr>
<tr><td><b>Fecha de pedido</b></td><td>%s</td></tr>
<tr><td><b>Entrega esperada</b></td><td>%s</td></tr>
</table>
<p>Gracias,<br>%s</p>`, e(o.SupplierName), e(brand), e(orderID), e(o.ProductName), e(o.ProductID), qty, o.UnitCost,
		total, orderDate, delivery, e(brand))
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

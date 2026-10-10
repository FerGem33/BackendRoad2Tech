// POST /suppliers/{supplier_id}/test-email — ENDPOINTS.md §3.15. Same DEMO_MAILBOX redirection as §3.12.
package main

import (
	"context"
	"fmt"
	"html"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/mail"
)

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	id := httpx.Path(r, "supplier_id")
	var sup db.Supplier
	found, err := db.Get(ctx, "SUPPLIER#"+id, "#META", &sup)
	if err != nil {
		return 0, nil, err
	}
	if !found {
		return 0, nil, httpx.NotFound("supplier_not_found", "El proveedor %s no existe.", id)
	}
	sent, err := mail.Send(ctx, mail.Message{
		FromName: "StockCopilot", To: sup.Email,
		Subject: "Correo de prueba — StockCopilot",
		HTML: fmt.Sprintf("<p>Hola %s,</p><p>Este es un correo de prueba de StockCopilot para confirmar la recepción de órdenes de compra.</p>",
			html.EscapeString(sup.SupplierName)),
	})
	if err != nil {
		return 0, nil, err
	}
	return 200, map[string]any{
		"supplier_id": id, "status": "sent", "sent_at": time.Now().UTC().Format(time.RFC3339),
		"email":         map[string]any{"to": sent.To, "provider_message_id": sent.ProviderMessageID},
		"demo_redirect": sent.DemoRedirect,
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

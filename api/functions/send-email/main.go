// POST /emails — sends a free-form email through Resend to the address in the body.
// htmlBody is sent as-is, so the frontend can render it with react-email (render()) or any other template library.
package main

import (
	"context"
	"net/mail"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	mailer "github.com/fernandovmedina/BackendRoad2Tech/api/internal/mail"
)

const maxBodyBytes = 1 << 20 // 1 MiB per part; well under Resend's 40 MB and Lambda's 6 MB payload limits

type request struct {
	To       string `json:"to"`
	Subject  string `json:"subject"`
	Text     string `json:"text"`
	HTMLBody string `json:"htmlBody"`
	FromName string `json:"from_name"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	var req request
	if err := httpx.Decode(r, &req); err != nil {
		return 0, nil, err
	}
	addr, err := mail.ParseAddress(strings.TrimSpace(req.To))
	if err != nil {
		return 0, nil, httpx.BadRequest("to debe ser un correo válido.")
	}
	req.Subject = strings.TrimSpace(req.Subject)
	if req.Subject == "" {
		return 0, nil, httpx.BadRequest("subject es obligatorio.")
	}
	if strings.TrimSpace(req.Text) == "" && strings.TrimSpace(req.HTMLBody) == "" {
		return 0, nil, httpx.BadRequest("Se requiere text o htmlBody.")
	}
	if len(req.Text) > maxBodyBytes || len(req.HTMLBody) > maxBodyBytes {
		return 0, nil, httpx.Errorf(413, "body_too_large", "text y htmlBody deben pesar menos de 1 MB cada uno.")
	}
	if req.FromName == "" {
		req.FromName = "StockCopilot"
	}
	sent, err := mailer.Send(ctx, mailer.Message{
		FromName: req.FromName, To: addr.Address, Subject: req.Subject,
		HTML: req.HTMLBody, Text: req.Text, Direct: true,
	})
	if err != nil {
		return 0, nil, err
	}
	return 201, map[string]any{
		"status": "sent", "sent_at": time.Now().UTC().Format(time.RFC3339),
		"email": map[string]any{"to": sent.To, "subject": req.Subject, "provider_message_id": sent.ProviderMessageID},
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

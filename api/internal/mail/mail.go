// Package mail sends supplier emails through Resend (ENDPOINTS.md §3.12 and §3.15).
//
// Env: RESEND_API_KEY, EMAIL_FROM_ADDRESS (default onboarding@resend.dev), DEMO_MAILBOX.
// Without RESEND_API_KEY the message is only logged (provider_message_id "log_...").
package mail

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type Message struct {
	FromName string
	To       string // shown address (the supplier); DEMO_MAILBOX overrides the real recipient
	Subject  string
	HTML     string
	Text     string // plain-text part; at least one of HTML or Text is required
	Direct   bool   // send to To even when DEMO_MAILBOX is set (recipient chosen explicitly by the user)
}

type Result struct {
	To                string `json:"to"`
	Subject           string `json:"subject,omitempty"`
	ProviderMessageID string `json:"provider_message_id,omitempty"`
	DemoRedirect      bool   `json:"-"`
}

var ErrEmailFailed = httpx.Errorf(502, "email_failed", "No se pudo enviar el correo; intenta de nuevo.")

func Send(ctx context.Context, m Message) (*Result, error) {
	res := &Result{To: m.To, Subject: m.Subject}
	recipient := m.To
	if demo := os.Getenv("DEMO_MAILBOX"); demo != "" && !m.Direct {
		recipient, res.DemoRedirect = demo, true
	}
	key := os.Getenv("RESEND_API_KEY")
	if key == "" {
		res.ProviderMessageID = auth.NewID("log_")
		slog.Info("email (log mode)", "to", recipient, "shown_to", m.To, "subject", m.Subject)
		return res, nil
	}
	from := os.Getenv("EMAIL_FROM_ADDRESS")
	if from == "" {
		from = "onboarding@resend.dev"
	}
	payload := map[string]any{"from": fmt.Sprintf("%s <%s>", m.FromName, from), "to": []string{recipient}, "subject": m.Subject}
	if m.HTML != "" {
		payload["html"] = m.HTML
	}
	if m.Text != "" {
		payload["text"] = m.Text
	}
	body, _ := json.Marshal(payload)
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "https://api.resend.com/emails", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Error("resend request failed", "err", err)
		return nil, ErrEmailFailed
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		slog.Error("resend rejected email", "status", resp.StatusCode, "body", string(raw))
		return nil, ErrEmailFailed
	}
	var out struct {
		ID string `json:"id"`
	}
	_ = json.Unmarshal(raw, &out)
	res.ProviderMessageID = out.ID
	return res, nil
}

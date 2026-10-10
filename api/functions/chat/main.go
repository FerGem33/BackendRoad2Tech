// POST /chat — ENDPOINTS.md §3.17. Rule-based orchestrator with the same tool names as the LangGraph agent
// (get_current_store_context, get_demand_forecast, get_business_constraints, simulate_strategy,
// optimize_portfolio, create_promotion_draft, get_product_tendency). It never approves or activates: drafts
// are created with status "draft" and requires_approval=true.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type body struct {
	StoreID        string  `json:"store_id"`
	ConversationID *string `json:"conversation_id"`
	Message        string  `json:"message"`
}

type tool struct {
	Name string         `json:"name"`
	Args map[string]any `json:"args"`
}

type block = map[string]any

type conversation struct {
	PK         string `dynamodbav:"PK"`
	SK         string `dynamodbav:"SK"`
	EntityType string `dynamodbav:"entity_type"`
	StoreID    string `dynamodbav:"store_id"`
	UserID     string `dynamodbav:"user_id"`
	CreatedAt  string `dynamodbav:"created_at"`
}

type message struct {
	PK         string `dynamodbav:"PK"`
	SK         string `dynamodbav:"SK"`
	EntityType string `dynamodbav:"entity_type"`
	MessageID  string `dynamodbav:"message_id"`
	Role       string `dynamodbav:"role"`
	Content    string `dynamodbav:"content"`
	CreatedAt  string `dynamodbav:"created_at"`
}

type turn struct {
	sc     *domain.StoreContext
	parsed parsed
	tools  []tool
	blocks []block
	convID string
}

func (t *turn) use(name string, args map[string]any) { t.tools = append(t.tools, tool{name, args}) }

func (t *turn) text(format string, args ...any) {
	t.blocks = append(t.blocks, block{"type": "text", "text": fmt.Sprintf(format, args...)})
}

// candidates: products named in the message, else the category, else the whole catalog.
func (t *turn) candidates() []db.Stock {
	want := map[string]bool{}
	for _, id := range t.parsed.ProductIDs {
		want[id] = true
	}
	var out []db.Stock
	for _, s := range t.sc.Stocks {
		switch {
		case len(want) > 0:
			if want[s.ProductID] {
				out = append(out, s)
			}
		case t.parsed.CategoryID != "":
			if s.CategoryID == t.parsed.CategoryID {
				out = append(out, s)
			}
		default:
			out = append(out, s)
		}
	}
	return out
}

func (t *turn) promo(ctx context.Context) error {
	storeID := t.sc.Store.StoreID
	t.use("get_demand_forecast", map[string]any{"store_id": storeID, "horizon": domain.ForecastHorizonDays})
	t.use("get_business_constraints", map[string]any{"store_id": storeID})
	args := map[string]any{"strategy_type": "contextual_offer"}
	if t.parsed.CategoryID != "" {
		args["category_id"] = t.parsed.CategoryID
	}
	if len(t.parsed.ProductIDs) > 0 {
		args["product_ids"] = t.parsed.ProductIDs
	}
	if t.parsed.MaxDiscountPct != nil {
		args["max_discount_pct"] = *t.parsed.MaxDiscountPct
	}
	t.use("simulate_strategy", args)
	s, err := buildStrategy(ctx, t.sc, t.parsed, t.candidates(), t.convID)
	if err != nil {
		return err
	}
	if s == nil {
		t.text("No encontré productos para simular con esas condiciones. Prueba con otra categoría o producto.")
		return nil
	}
	t.use("create_promotion_draft", map[string]any{"store_id": storeID, "draft_id": s.DraftID})
	t.use("optimize_portfolio", map[string]any{"store_id": storeID})
	summary, err := planSummary(ctx, storeID, s)
	if err != nil {
		return err
	}
	pct := s.ProposedDiscountPct * 100
	switch {
	case s.Status == "rejected":
		t.text("Ningún descuento dentro del límite cumple las restricciones del negocio; dejé la simulación como rechazada para que veas el motivo.")
	case s.ExpectedMarginIncrement <= 0:
		t.text("Preparé un borrador con %.0f %% de descuento, pero el margen esperado no mejora; te sugiero no agregarlo al plan.", pct)
	default:
		t.text("Preparé un borrador con descuento de %.0f %%. Margen incremental esperado: $%.2f. Revísalo y agrégalo al plan si te convence.", pct, s.ExpectedMarginIncrement)
	}
	t.blocks = append(t.blocks, block{"type": "strategy_card", "strategy": s, "requires_approval": true}, summary)
	return nil
}

func (t *turn) restock() {
	t.use("get_demand_forecast", map[string]any{"store_id": t.sc.Store.StoreID, "horizon": domain.ForecastHorizonDays})
	allowed := map[string]bool{}
	for _, s := range t.candidates() {
		allowed[s.ProductID] = true
	}
	var list []domain.Suggestion
	for _, s := range t.sc.RestockSuggestions(t.sc.AsOf, domain.AddDays(t.sc.AsOf, 7)) {
		if allowed[s.ProductID] {
			list = append(list, s)
		}
	}
	sort.SliceStable(list, func(i, j int) bool {
		if list[i].IsUrgent != list[j].IsUrgent {
			return list[i].IsUrgent
		}
		if list[i].OrderDate != list[j].OrderDate {
			return list[i].OrderDate < list[j].OrderDate
		}
		return list[i].ExpectedLostSalesIfSkipped > list[j].ExpectedLostSalesIfSkipped
	})
	if len(list) == 0 {
		t.text("No hay pedidos necesarios en los próximos 7 días para esa selección.")
		return
	}
	urgent := 0
	for _, s := range list {
		if s.IsUrgent {
			urgent++
		}
	}
	n := len(list)
	if len(list) > 5 {
		list = list[:5]
	}
	t.text("Hay %d pedidos sugeridos para los próximos 7 días (urgentes: %d). Estos son los más importantes; el envío al proveedor se confirma desde la tabla.", n, urgent)
	t.blocks = append(t.blocks, block{"type": "restock_table", "suggestions": list})
}

func (t *turn) forecast(ctx context.Context) error {
	cands := t.candidates()
	if len(cands) == 0 {
		t.text("No encontré el producto que mencionas.")
		return nil
	}
	sort.SliceStable(cands, func(i, j int) bool {
		return t.sc.Curve(cands[i].ProductID).Sum(t.sc.AsOf, 7) > t.sc.Curve(cands[j].ProductID).Sum(t.sc.AsOf, 7)
	})
	pid := cands[0].ProductID
	storeID := t.sc.Store.StoreID
	t.use("get_product_tendency", map[string]any{"store_id": storeID, "product_id": pid, "history_days": 28,
		"forecast_days": domain.ForecastHorizonDays})
	var sales []db.Sale
	if err := db.ProductTimeline(ctx, storeID, pid, "SALE#", &sales); err != nil {
		return err
	}
	since := domain.AddDays(t.sc.AsOf, -27)
	history := []block{}
	for _, s := range sales {
		if s.Date >= since {
			history = append(history, block{"date": s.Date, "value": s.UnitsSold})
		}
	}
	fc := []block{}
	c := t.sc.Curve(pid)
	for _, f := range c.Items {
		fc = append(fc, block{"date": f.Date, "value": httpx.Round(f.P50, 2), "p50": httpx.Round(f.P50, 2),
			"p80": httpx.Round(f.P80, 2), "p90": httpx.Round(f.P90, 2)})
	}
	name := t.sc.Products[pid].ProductName
	t.text("Se esperan %.0f unidades de %s en los próximos 7 días (promedio reciente: %.1f u/día).", c.Sum(t.sc.AsOf, 7), name, c.Baseline)
	t.blocks = append(t.blocks, block{"type": "forecast_chart", "store_id": storeID, "product_id": pid,
		"history": history, "forecast": fc})
	return nil
}

func (t *turn) context() {
	week := t.sc.RestockSuggestions(t.sc.AsOf, domain.AddDays(t.sc.AsOf, 7))
	urgent := 0
	for _, s := range week {
		if s.IsUrgent {
			urgent++
		}
	}
	_, risks := t.sc.Plan(t.sc.AsOf, domain.AddDays(t.sc.AsOf, 7))
	stockouts := len(risks)
	t.text("En %s hay %d pedidos sugeridos para esta semana (urgentes: %d) y %d productos con riesgo de quedarse sin stock en 7 días aun pidiendo a tiempo. "+
		"Puedo preparar una oferta (\"descuento de 10 %% en refrescos el sábado\"), mostrarte qué pedir o el pronóstico de un producto.",
		t.sc.Store.StoreName, len(week), urgent, stockouts)
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	var b body
	if err := httpx.Decode(r, &b); err != nil {
		return 0, nil, err
	}
	if strings.TrimSpace(b.StoreID) == "" {
		return 0, nil, httpx.Errorf(422, "store_required", "Selecciona una tienda para usar el asistente.")
	}
	b.Message = strings.TrimSpace(b.Message)
	if b.Message == "" || len(b.Message) > 2000 {
		return 0, nil, httpx.BadRequest("message es obligatorio (máximo 2000 caracteres).")
	}
	user := auth.FromContext(ctx)
	now := time.Now().UTC().Format(time.RFC3339Nano)

	convID := ""
	if b.ConversationID != nil && *b.ConversationID != "" {
		convID = *b.ConversationID
		var c conversation
		found, err := db.Get(ctx, "CONV#"+convID, "#META", &c)
		if err != nil {
			return 0, nil, err
		}
		if !found || c.StoreID != b.StoreID || c.UserID != user.Sub {
			return 0, nil, httpx.NotFound("conversation_not_found", "La conversación no existe para esta tienda.")
		}
	} else {
		convID = auth.NewID("conv_")
		if err := db.Put(ctx, conversation{PK: "CONV#" + convID, SK: "#META", EntityType: "CONVERSATION",
			StoreID: b.StoreID, UserID: user.Sub, CreatedAt: now}, ""); err != nil {
			return 0, nil, err
		}
	}

	sc, err := domain.LoadStoreContext(ctx, b.StoreID)
	if err != nil {
		return 0, nil, err
	}
	products, err := db.Products(ctx)
	if err != nil {
		return 0, nil, err
	}
	t := &turn{sc: sc, parsed: parse(b.Message, products), convID: convID}
	t.use("get_current_store_context", map[string]any{"store_id": b.StoreID})

	switch t.parsed.Intent {
	case intentApprove:
		t.text("No puedo aprobar ni activar estrategias desde el chat. La aprobación se hace desde el plan, con tu confirmación.")
	case intentPromo:
		err = t.promo(ctx)
	case intentRestock:
		t.restock()
	case intentForecast:
		err = t.forecast(ctx)
	default:
		t.context()
	}
	if err != nil {
		return 0, nil, err
	}

	var weekday, category any
	if t.parsed.Weekday != "" {
		weekday = t.parsed.Weekday
	}
	if t.parsed.CategoryID != "" {
		category = t.parsed.CategoryID
	}
	pids := t.parsed.ProductIDs
	if pids == nil {
		pids = []string{}
	}
	msgID := auth.NewID("msg_")
	content, _ := json.Marshal(t.blocks)
	for _, m := range []message{
		{PK: "CONV#" + convID, SK: "MSG#" + now + "#1", EntityType: "MESSAGE", MessageID: auth.NewID("msg_"), Role: "user",
			Content: b.Message, CreatedAt: now},
		{PK: "CONV#" + convID, SK: "MSG#" + now + "#2", EntityType: "MESSAGE", MessageID: msgID, Role: "assistant",
			Content: string(content), CreatedAt: now},
	} {
		if err := db.Put(ctx, m, ""); err != nil {
			return 0, nil, err
		}
	}
	return 200, map[string]any{
		"conversation_id": convID, "message_id": msgID, "tools": t.tools,
		"interpreted_constraints": map[string]any{"product_ids": pids, "category_id": category, "weekday": weekday,
			"max_discount_pct": t.parsed.MaxDiscountPct},
		"blocks": t.blocks,
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

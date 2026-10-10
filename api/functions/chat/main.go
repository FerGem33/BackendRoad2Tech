// POST /chat — ENDPOINTS.md §3.17. Rule-based orchestrator with the same tool names as the LangGraph agent
// (get_current_store_context, get_demand_forecast, get_business_constraints, simulate_strategy,
// optimize_portfolio, create_promotion_draft, get_product_tendency). It never approves or activates: drafts
// are created with status "draft" and requires_approval=true.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

type body struct {
	StoreID          string  `json:"store_id"`
	ConversationID   *string `json:"conversation_id"`
	ContextProductID string  `json:"context_product_id"`
	Message          string  `json:"message"`
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
	ProductID  string `dynamodbav:"product_id,omitempty"`
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

const inventoryHorizonDays = 30

type inventoryOutlook struct {
	Store          db.Store
	Stock          db.Stock
	Curve          domain.Curve
	Available      float64
	ForecastDemand float64
	Remaining      float64
	StockoutDate   string
	ExpiryDate     string
	ExpiryWaste    float64
}

func curveFromForecast(asOf string, items []db.Forecast) domain.Curve {
	sort.Slice(items, func(i, j int) bool { return items[i].Date < items[j].Date })
	c := domain.Curve{AsOf: asOf, Items: items}
	for _, f := range items {
		c.P50 = append(c.P50, f.P50)
		c.P80 = append(c.P80, f.P80)
		c.Baseline = f.BaselineUnits
	}
	return c
}

func makeOutlook(store db.Store, stock db.Stock, curve domain.Curve, asOf string) inventoryOutlook {
	available := stock.OnHand + stock.OnOrder
	demand := curve.Sum(asOf, inventoryHorizonDays)
	cumulative, stockoutDate := 0.0, ""
	for day := 1; day <= inventoryHorizonDays; day++ {
		date := domain.AddDays(asOf, day)
		cumulative += curve.At(date)
		if stockoutDate == "" && cumulative > available {
			stockoutDate = date
		}
	}
	expiryDate, expiryWaste := "", 0.0
	end := domain.AddDays(asOf, inventoryHorizonDays)
	if stock.NextExpirationDate != nil && *stock.NextExpirationDate > asOf && *stock.NextExpirationDate <= end {
		expiryDate = *stock.NextExpirationDate
		expiryWaste = math.Max(0, stock.OnHand-curve.Sum(asOf, domain.DaysBetween(asOf, expiryDate)))
	}
	return inventoryOutlook{
		Store: store, Stock: stock, Curve: curve, Available: available, ForecastDemand: demand,
		Remaining: available - demand, StockoutDate: stockoutDate, ExpiryDate: expiryDate, ExpiryWaste: expiryWaste,
	}
}

func strongerSignal(a, b inventoryOutlook) bool {
	if (a.StockoutDate != "") != (b.StockoutDate != "") {
		return a.StockoutDate != ""
	}
	if a.StockoutDate != "" && b.StockoutDate != "" && a.StockoutDate != b.StockoutDate {
		return a.StockoutDate < b.StockoutDate
	}
	if (a.ExpiryWaste > 0) != (b.ExpiryWaste > 0) {
		return a.ExpiryWaste > 0
	}
	if a.ExpiryWaste != b.ExpiryWaste {
		return a.ExpiryWaste > b.ExpiryWaste
	}
	return a.Remaining < b.Remaining
}

func chainOutlook(ctx context.Context, productID string) (*inventoryOutlook, error) {
	stores, err := db.Stores(ctx)
	if err != nil {
		return nil, err
	}
	asOf := httpx.DataAsOf()
	outlooks := make([]*inventoryOutlook, len(stores))
	g, gctx := errgroup.WithContext(ctx)
	for i := range stores {
		i := i
		g.Go(func() error {
			stocks, err := db.Stocks(gctx, stores[i].StoreID)
			if err != nil {
				return err
			}
			var stock *db.Stock
			for j := range stocks {
				if stocks[j].ProductID == productID {
					stock = &stocks[j]
					break
				}
			}
			if stock == nil {
				return nil
			}
			var forecast []db.Forecast
			if err := db.ProductTimeline(gctx, stores[i].StoreID, productID, "FCST#", &forecast); err != nil {
				return err
			}
			value := makeOutlook(stores[i], *stock, curveFromForecast(asOf, forecast), asOf)
			outlooks[i] = &value
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	var best *inventoryOutlook
	for _, candidate := range outlooks {
		if candidate != nil && (best == nil || strongerSignal(*candidate, *best)) {
			best = candidate
		}
	}
	return best, nil
}

func shortDate(iso string) string {
	parts := strings.Split(iso, "-")
	if len(parts) != 3 {
		return iso
	}
	return parts[2] + "/" + parts[1]
}

func forecastPoint(curve domain.Curve, date string) block {
	idx := domain.DaysBetween(curve.AsOf, date) - 1
	if len(curve.Items) == 0 {
		value := httpx.Round(curve.At(date), 2)
		return block{"date": date, "value": value, "p50": value, "p80": value, "p90": value}
	}
	for idx >= len(curve.Items) {
		idx -= 7
	}
	idx = max(idx, 0)
	source := curve.Items[idx]
	p50 := httpx.Round(curve.At(date), 2)
	ratio80, ratio90 := 1.0, 1.0
	if source.P50 > 0 {
		ratio80, ratio90 = source.P80/source.P50, source.P90/source.P50
	}
	return block{"date": date, "value": p50, "p50": p50, "p80": httpx.Round(p50*ratio80, 2), "p90": httpx.Round(p50*ratio90, 2)}
}

func (t *turn) appendOutlook(ctx context.Context, outlook *inventoryOutlook, chainScope bool) error {
	if outlook == nil {
		t.text("No encontré inventario de ese producto para construir el análisis.")
		return nil
	}
	end := domain.AddDays(outlook.Curve.AsOf, inventoryHorizonDays)
	lots, err := db.LotsExpiringBetween(ctx, outlook.Store.StoreID, domain.AddDays(outlook.Curve.AsOf, 1), end)
	if err != nil {
		return err
	}
	firstExpiry, expiringQty := "", 0.0
	for _, lot := range lots {
		if lot.ProductID != outlook.Stock.ProductID || lot.QuantityRemaining <= 0 {
			continue
		}
		if firstExpiry == "" || lot.ExpirationDate < firstExpiry {
			firstExpiry, expiringQty = lot.ExpirationDate, float64(lot.QuantityRemaining)
		} else if lot.ExpirationDate == firstExpiry {
			expiringQty += float64(lot.QuantityRemaining)
		}
	}
	if firstExpiry != "" {
		outlook.ExpiryDate = firstExpiry
		outlook.ExpiryWaste = math.Max(0, expiringQty-outlook.Curve.Sum(outlook.Curve.AsOf, domain.DaysBetween(outlook.Curve.AsOf, firstExpiry)))
	}

	prefix := fmt.Sprintf("%s · %s. ", outlook.Store.StoreName, outlook.Stock.ProductName)
	if chainScope {
		prefix = fmt.Sprintf("Analicé las sucursales y fijé %s porque presenta la señal más relevante para %s. ", outlook.Store.StoreName, outlook.Stock.ProductName)
	}
	wasteFirst := outlook.ExpiryDate != "" && outlook.ExpiryWaste > 0 && (outlook.StockoutDate == "" || outlook.ExpiryDate <= outlook.StockoutDate)
	answer := ""
	switch {
	case wasteFirst:
		answer = fmt.Sprintf("Existe riesgo de merma primero: cerca de %.1f u podrían seguir en inventario cuando llegue la primera caducidad del %s.", outlook.ExpiryWaste, shortDate(outlook.ExpiryDate))
		if outlook.StockoutDate != "" {
			answer += fmt.Sprintf(" Después de esa caducidad, la demanda también rebasa el inventario alrededor del %s.", shortDate(outlook.StockoutDate))
		}
	case outlook.StockoutDate != "":
		answer = fmt.Sprintf("No hay stock suficiente: con la tendencia P50 se agotaría alrededor del %s y faltarían aproximadamente %.1f u al cierre del horizonte.", shortDate(outlook.StockoutDate), math.Abs(outlook.Remaining))
	default:
		answer = fmt.Sprintf("Sí hay stock suficiente: después de cubrir la demanda central quedarían aproximadamente %.1f u y no aparece riesgo de merma dentro de los próximos 30 días.", math.Max(0, outlook.Remaining))
	}
	t.text("%s%s Disponibles durante el horizonte (incluyendo órdenes abiertas): %.1f u; demanda central prevista a 30 días: %.1f u.", prefix, answer, outlook.Available, outlook.ForecastDemand)

	var sales []db.Sale
	if err := db.ProductTimeline(ctx, outlook.Store.StoreID, outlook.Stock.ProductID, "SALE#", &sales); err != nil {
		return err
	}
	since := domain.AddDays(outlook.Curve.AsOf, -20)
	history := []block{}
	for _, sale := range sales {
		if sale.Date >= since {
			history = append(history, block{"date": sale.Date, "value": sale.UnitsSold})
		}
	}
	forecast := make([]block, 0, inventoryHorizonDays)
	for day := 1; day <= inventoryHorizonDays; day++ {
		forecast = append(forecast, forecastPoint(outlook.Curve, domain.AddDays(outlook.Curve.AsOf, day)))
	}
	t.blocks = append(t.blocks, block{"type": "forecast_chart", "store_id": outlook.Store.StoreID,
		"product_id": outlook.Stock.ProductID, "history": history, "forecast": forecast})
	return nil
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

func (t *turn) inventory(ctx context.Context) error {
	candidates := t.candidates()
	if len(candidates) == 0 {
		t.text("Dime qué producto quieres analizar. Por ejemplo: «¿Tengo stock suficiente de Coca-Cola 600 ml?»")
		return nil
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		return t.sc.Curve(candidates[i].ProductID).Sum(t.sc.AsOf, inventoryHorizonDays) >
			t.sc.Curve(candidates[j].ProductID).Sum(t.sc.AsOf, inventoryHorizonDays)
	})
	stock := candidates[0]
	outlook := makeOutlook(*t.sc.Store, stock, t.sc.Curve(stock.ProductID), t.sc.AsOf)
	t.use("get_store_inventory_outlook", map[string]any{"store_id": t.sc.Store.StoreID, "product_id": stock.ProductID,
		"horizon_days": inventoryHorizonDays})
	t.use("get_product_tendency", map[string]any{"store_id": t.sc.Store.StoreID, "product_id": stock.ProductID,
		"history_days": 21, "forecast_days": inventoryHorizonDays})
	return t.appendOutlook(ctx, &outlook, false)
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
	b.StoreID = strings.TrimSpace(b.StoreID)
	b.ContextProductID = strings.TrimSpace(b.ContextProductID)
	b.Message = strings.TrimSpace(b.Message)
	if b.Message == "" || len(b.Message) > 2000 {
		return 0, nil, httpx.BadRequest("message es obligatorio (máximo 2000 caracteres).")
	}
	products, err := db.Products(ctx)
	if err != nil {
		return 0, nil, err
	}
	parsedMessage := parse(b.Message, products)
	if len(parsedMessage.ProductIDs) == 0 && b.ContextProductID != "" {
		for _, product := range products {
			if product.ProductID == b.ContextProductID {
				parsedMessage.ProductIDs = []string{b.ContextProductID}
				break
			}
		}
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
		if len(parsedMessage.ProductIDs) == 0 && c.ProductID != "" {
			parsedMessage.ProductIDs = []string{c.ProductID}
		}
	} else {
		convID = auth.NewID("conv_")
		productID := ""
		if len(parsedMessage.ProductIDs) > 0 {
			productID = parsedMessage.ProductIDs[0]
		}
		if err := db.Put(ctx, conversation{PK: "CONV#" + convID, SK: "#META", EntityType: "CONVERSATION",
			StoreID: b.StoreID, ProductID: productID, UserID: user.Sub, CreatedAt: now}, ""); err != nil {
			return 0, nil, err
		}
	}

	t := &turn{parsed: parsedMessage, convID: convID}
	focusStoreID, focusProductID := "", ""
	if b.StoreID == "" {
		switch t.parsed.Intent {
		case intentInventory, intentForecast:
			if len(t.parsed.ProductIDs) == 0 {
				t.use("search_products", map[string]any{"query": b.Message})
				t.text("Dime qué producto quieres analizar. Por ejemplo: «¿Tengo stock suficiente de Coca-Cola 600 ml?» o «Muéstrame la tendencia de Tecate Light».")
				break
			}
			focusProductID = t.parsed.ProductIDs[0]
			t.use("get_chain_inventory_outlook", map[string]any{"product_id": focusProductID, "horizon_days": inventoryHorizonDays})
			outlook, outlookErr := chainOutlook(ctx, focusProductID)
			if outlookErr != nil {
				return 0, nil, outlookErr
			}
			if outlook != nil {
				focusStoreID = outlook.Store.StoreID
				t.use("get_product_tendency", map[string]any{"store_id": focusStoreID, "product_id": focusProductID,
					"history_days": 21, "forecast_days": inventoryHorizonDays})
			}
			err = t.appendOutlook(ctx, outlook, true)
		default:
			t.text("Puedo comparar tendencias, stock y riesgo de merma sin elegir una sucursal. Para promociones, pedidos o el plan comercial, dime primero qué sucursal quieres usar.")
		}
	} else {
		t.sc, err = domain.LoadStoreContext(ctx, b.StoreID)
		if err != nil {
			return 0, nil, err
		}
		t.use("get_current_store_context", map[string]any{"store_id": b.StoreID})
		switch t.parsed.Intent {
		case intentApprove:
			t.text("No puedo aprobar ni activar estrategias desde el chat. La aprobación se hace desde el plan, con tu confirmación.")
		case intentPromo:
			err = t.promo(ctx)
		case intentRestock:
			t.restock()
		case intentInventory:
			err = t.inventory(ctx)
		case intentForecast:
			err = t.forecast(ctx)
		default:
			t.context()
		}
		if len(t.parsed.ProductIDs) > 0 && (t.parsed.Intent == intentInventory || t.parsed.Intent == intentForecast) {
			focusProductID = t.parsed.ProductIDs[0]
		}
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
	response := map[string]any{
		"conversation_id": convID, "message_id": msgID, "tools": t.tools,
		"interpreted_constraints": map[string]any{"product_ids": pids, "category_id": category, "weekday": weekday,
			"max_discount_pct": t.parsed.MaxDiscountPct},
		"blocks": t.blocks,
	}
	if focusStoreID != "" {
		response["focus_store_id"] = focusStoreID
	}
	if focusProductID != "" {
		response["focus_product_id"] = focusProductID
	}
	return 200, response, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

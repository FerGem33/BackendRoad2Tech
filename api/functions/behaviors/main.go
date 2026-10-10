// GET /stores/{store_id}/behaviors — ENDPOINTS.md §3.6. Detected purchase behaviors: cross-sell (observed
// affinity baked into the dataset), waste risk (active lots expiring soon) and demand spikes (forecast uplift).
package main

import (
	"context"
	"fmt"
	"sort"
	"strconv"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

// affinityRules mirror scripts/dataset/build_tickets.py AFFINITY_RULES (anchor, related, share of
// anchor tickets that also carry the related product).
var affinityRules = []struct {
	anchor, related string
	rate            float64
}{
	{"P069", "P085", 0.16}, // coffee + conchas
	{"P026", "P034", 0.12}, // Corona + chips
	{"P015", "P047", 0.10}, // energy drink + chocolate
	{"P059", "P001", 0.08}, // sandwich + cold drink
}

type affinityOut struct {
	ObservedTicketShare  *float64 `json:"observed_ticket_share"`
	EstimatedTicketShare *float64 `json:"estimated_ticket_share"`
	Lift                 float64  `json:"lift"`
	TicketsSample        int      `json:"tickets_sample"`
}

type behaviorOut struct {
	ID               string       `json:"id"`
	Kind             string       `json:"kind"`
	Source           string       `json:"source"`
	Title            string       `json:"title"`
	Text             string       `json:"text"`
	ProductID        string       `json:"product_id"`
	RelatedProductID *string      `json:"related_product_id"`
	Affinity         *affinityOut `json:"affinity"`
	EstimatedGainMXN float64      `json:"estimated_gain_mxn"`
}

func marginOf(p db.Product) float64 {
	return p.RetailPrice - p.DefaultSupplier.UnitCost
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
	asOf := httpx.DataAsOf()
	from, to := httpx.Query(r, "from"), httpx.Query(r, "to")
	if from == "" {
		from = domain.AddDays(asOf, 1)
	}
	if to == "" {
		to = domain.AddDays(asOf, domain.ForecastHorizonDays)
	}
	if _, err := domain.ParseDate(from); err != nil {
		return 0, nil, httpx.BadRequest("from debe tener formato YYYY-MM-DD.")
	}
	if _, err := domain.ParseDate(to); err != nil {
		return 0, nil, httpx.BadRequest("to debe tener formato YYYY-MM-DD.")
	}
	if to < from {
		return 0, nil, httpx.BadRequest("Rango inválido: to >= from.")
	}
	limit := 6
	if v := httpx.Query(r, "limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 50 {
			return 0, nil, httpx.BadRequest("limit debe estar entre 1 y 50.")
		}
		limit = n
	}

	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	if store == nil {
		return 0, nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}

	var (
		products map[string]db.Product
		forecast map[string][]db.Forecast
		lots     []db.Lot
	)
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() (err error) { products, err = db.ProductMap(gctx); return })
	g.Go(func() (err error) { forecast, err = db.Forecasts(gctx, storeID, from, to); return })
	g.Go(func() (err error) { lots, err = db.LotsExpiringBetween(gctx, storeID, max(from, asOf), to); return })
	if err := g.Wait(); err != nil {
		return 0, nil, err
	}

	behaviors := []behaviorOut{}

	// Cross-sell: observed affinity (deterministic, from build_tickets.py).
	for _, rule := range affinityRules {
		anchor, ok1 := products[rule.anchor]
		related, ok2 := products[rule.related]
		if !ok1 || !ok2 {
			continue
		}
		share := rule.rate
		// Illustrative lift vs independent co-occurrence (no ticket-level data is loaded).
		lift := 1 + share*5
		gain := related.BaseDailyDemand * 30 * marginOf(related) * share
		behaviors = append(behaviors, behaviorOut{
			ID:               "beh_" + storeID + "_" + rule.anchor + "_" + rule.related,
			Kind:             "crosssell",
			Source:           "observed",
			Title:            anchor.ProductName + " se compra con " + related.ProductName,
			Text:             fmt.Sprintf("%.0f %% de los tickets con %s incluyen %s.", share*100, anchor.ProductName, related.ProductName),
			ProductID:        rule.anchor,
			RelatedProductID: &rule.related,
			Affinity:         &affinityOut{ObservedTicketShare: &share, Lift: httpx.Round(lift, 1), TicketsSample: int(anchor.BaseDailyDemand * 90)},
			EstimatedGainMXN: httpx.Money(gain),
		})
	}

	// Waste: active lots expiring in the window, excess vs projected demand.
	expiring := map[string]float64{}
	cost := map[string]float64{}
	for _, l := range lots {
		if l.Status != "ACTIVE" {
			continue
		}
		expiring[l.ProductID] += float64(l.QuantityRemaining)
		cost[l.ProductID] = l.UnitCost
	}
	wastePIDs := make([]string, 0, len(expiring))
	for pid := range expiring {
		wastePIDs = append(wastePIDs, pid)
	}
	sort.Slice(wastePIDs, func(i, j int) bool { return expiring[wastePIDs[i]] > expiring[wastePIDs[j]] })
	for _, pid := range wastePIDs {
		if _, ok := products[pid]; !ok {
			continue
		}
		behaviors = append(behaviors, behaviorOut{
			ID: "beh_" + storeID + "_" + pid + "_waste", Kind: "waste", Source: "estimated",
			Title:            "Producto con riesgo de caducar",
			Text:             "Exceso frente a la demanda proyectada.",
			ProductID:        pid,
			RelatedProductID: nil,
			Affinity:         nil,
			EstimatedGainMXN: httpx.Money(expiring[pid] * cost[pid]),
		})
	}

	// Demand: forecast uplift vs baseline over the window.
	demandPIDs := []string{}
	uplift := map[string]float64{}
	for pid, list := range forecast {
		p50, base := 0.0, 0.0
		for _, f := range list {
			p50 += f.P50
			base += f.BaselineUnits
		}
		if base > 0 && p50 >= 1.2*base {
			demandPIDs = append(demandPIDs, pid)
			uplift[pid] = p50 - base
		}
	}
	sort.Slice(demandPIDs, func(i, j int) bool { return uplift[demandPIDs[i]] > uplift[demandPIDs[j]] })
	for _, pid := range demandPIDs {
		p, ok := products[pid]
		if !ok {
			continue
		}
		behaviors = append(behaviors, behaviorOut{
			ID: "beh_" + storeID + "_" + pid + "_demand", Kind: "demand", Source: "estimated",
			Title:            "Pico de demanda esperado",
			Text:             "La demanda proyectada supera el promedio reciente.",
			ProductID:        pid,
			RelatedProductID: nil,
			Affinity:         nil,
			EstimatedGainMXN: httpx.Money(uplift[pid] * marginOf(p)),
		})
	}

	sort.SliceStable(behaviors, func(i, j int) bool { return behaviors[i].EstimatedGainMXN > behaviors[j].EstimatedGainMXN })
	if len(behaviors) > limit {
		behaviors = behaviors[:limit]
	}
	return 200, map[string]any{"items": behaviors}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

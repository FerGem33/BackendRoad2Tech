// GET /chain/summary — ENDPOINTS.md §3.2. Single source of the "All stores" view: chain KPIs, portfolio
// impact and store ranking. No plan or orders are shown in that view.
package main

import (
	"context"
	"sort"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

type rankingOut struct {
	StoreID         string  `json:"store_id"`
	Colonia         string  `json:"colonia"`
	ZoneType        string  `json:"zone_type"`
	OpportunityMXN  float64 `json:"opportunity_mxn"`
	StrategiesCount int     `json:"strategies_count"`
}

type storeAgg struct {
	store   db.Store
	port    domain.PortfolioSummary
	stocks  []db.Stock
	sales7d []db.Sale
	tickets []db.TicketDay
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	asOf := httpx.DataAsOf()
	from, to := httpx.Query(r, "from"), httpx.Query(r, "to")
	if from == "" {
		from = domain.AddDays(asOf, -29)
	}
	if to == "" {
		to = asOf
	}
	if _, err := domain.ParseDate(from); err != nil {
		return 0, nil, httpx.BadRequest("from debe tener formato YYYY-MM-DD.")
	}
	if _, err := domain.ParseDate(to); err != nil {
		return 0, nil, httpx.BadRequest("to debe tener formato YYYY-MM-DD.")
	}
	if to < from || to > asOf {
		return 0, nil, httpx.BadRequest("Rango inválido: from <= to <= %s.", asOf)
	}
	mode := httpx.Query(r, "mode")
	if mode == "" {
		mode = "actual"
	}
	if mode != "actual" && mode != "forecast" {
		return 0, nil, httpx.BadRequest("mode debe ser actual o forecast.")
	}
	zone := httpx.Query(r, "zone")

	stores, err := db.Stores(ctx)
	if err != nil {
		return 0, nil, err
	}
	products, err := db.Products(ctx)
	if err != nil {
		return 0, nil, err
	}
	totalProducts := len(products)

	// windows: [from, to] and the previous window of equal length.
	length := domain.DaysBetween(from, to) + 1
	prevFrom := domain.AddDays(from, -length)
	since7 := domain.AddDays(asOf, -6)

	filtered := stores[:0]
	for _, s := range stores {
		if zone != "" && s.ZoneType != zone {
			continue
		}
		filtered = append(filtered, s)
	}

	aggs := make([]storeAgg, len(filtered))
	g, gctx := errgroup.WithContext(ctx)
	for i := range filtered {
		i := i
		g.Go(func() error {
			a := &aggs[i]
			id := filtered[i].StoreID
			eg, egctx := errgroup.WithContext(gctx)
			eg.Go(func() (err error) { a.port, err = domain.StorePortfolio(egctx, id); return })
			eg.Go(func() (err error) { a.stocks, err = db.Stocks(egctx, id); return })
			eg.Go(func() (err error) { a.sales7d, err = db.StoreSales(egctx, id, since7, asOf); return })
			eg.Go(func() (err error) { a.tickets, err = db.StoreTicketDays(egctx, id, prevFrom, to); return })
			return eg.Wait()
		})
	}
	if err := g.Wait(); err != nil {
		return 0, nil, err
	}

	var (
		net, low, high   float64
		strategies, plan int
		stockouts, noDem int
		curTickets       []db.TicketDay
		prevTickets      []db.TicketDay
		ranking          []rankingOut
	)
	for _, a := range aggs {
		net += a.port.Net
		low += a.port.ConfidenceLow
		high += a.port.ConfidenceHigh
		strategies += a.port.StrategiesCount
		if a.port.StrategiesCount > 0 {
			plan++
		}
		for _, s := range a.stocks {
			if s.OnHand <= 0 {
				stockouts++
			}
		}
		sold := map[string]bool{}
		for _, s := range a.sales7d {
			if s.UnitsSold > 0 {
				sold[s.ProductID] = true
			}
		}
		noDem += totalProducts - len(sold)
		for _, t := range a.tickets {
			if t.Date >= from && t.Date <= to {
				curTickets = append(curTickets, t)
			} else {
				prevTickets = append(prevTickets, t)
			}
		}
		ranking = append(ranking, rankingOut{a.store.StoreID, a.store.Colonia, a.store.ZoneType,
			a.port.Net, a.port.StrategiesCount})
	}
	sort.Slice(ranking, func(i, j int) bool {
		if ranking[i].OpportunityMXN != ranking[j].OpportunityMXN {
			return ranking[i].OpportunityMXN > ranking[j].OpportunityMXN
		}
		return ranking[i].StoreID < ranking[j].StoreID
	})

	avgTicket, avgDelta, items, itemsDelta := domain.TicketStats(curTickets, prevTickets)
	kpis := map[string]any{
		"average_ticket":             avgTicket,
		"average_ticket_delta_pct":   avgDelta,
		"items_per_ticket":           items,
		"items_per_ticket_delta_pct": itemsDelta,
		"stockout_products":          stockouts,
		"no_demand_products":         noDem,
		"ai_opportunity_mxn":         httpx.Money(net),
	}
	impact := map[string]any{
		"net_expected_margin_increment": httpx.Money(net),
		"confidence_interval":           map[string]any{"low": httpx.Money(low), "high": httpx.Money(high), "level": 0.8},
		"stores_with_plan":              plan,
		"strategies_count":              strategies,
	}
	return 200, map[string]any{
		"period":           map[string]string{"from": from, "to": to},
		"mode":             mode,
		"kpis":             kpis,
		"portfolio_impact": impact,
		"store_ranking":    ranking,
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

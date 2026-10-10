// GET /stores/{store_id}/kpis — ENDPOINTS.md §3.4. Store KPIs: ticket stats, stockouts, no-demand and the
// opportunity of the store (net expected margin increment of its plan).
package main

import (
	"context"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
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

	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	if store == nil {
		return 0, nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}
	products, err := db.Products(ctx)
	if err != nil {
		return 0, nil, err
	}
	totalProducts := len(products)

	length := domain.DaysBetween(from, to) + 1
	prevFrom := domain.AddDays(from, -length)
	since7 := domain.AddDays(asOf, -6)

	var (
		port    domain.PortfolioSummary
		stocks  []db.Stock
		sales7d []db.Sale
		tickets []db.TicketDay
	)
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() (err error) { port, err = domain.StorePortfolio(gctx, storeID); return })
	g.Go(func() (err error) { stocks, err = db.Stocks(gctx, storeID); return })
	g.Go(func() (err error) { sales7d, err = db.StoreSales(gctx, storeID, since7, asOf); return })
	g.Go(func() (err error) { tickets, err = db.StoreTicketDays(gctx, storeID, prevFrom, to); return })
	if err := g.Wait(); err != nil {
		return 0, nil, err
	}

	stockouts := 0
	for _, s := range stocks {
		if s.OnHand <= 0 {
			stockouts++
		}
	}
	sold := map[string]bool{}
	for _, s := range sales7d {
		if s.UnitsSold > 0 {
			sold[s.ProductID] = true
		}
	}
	noDem := totalProducts - len(sold)

	var curTickets, prevTickets []db.TicketDay
	for _, t := range tickets {
		if t.Date >= from && t.Date <= to {
			curTickets = append(curTickets, t)
		} else {
			prevTickets = append(prevTickets, t)
		}
	}
	avgTicket, avgDelta, items, itemsDelta := domain.TicketStats(curTickets, prevTickets)

	return 200, map[string]any{
		"store_id": storeID,
		"period":   map[string]string{"from": from, "to": to},
		"mode":     mode,
		"kpis": map[string]any{
			"average_ticket":             avgTicket,
			"average_ticket_delta_pct":   avgDelta,
			"items_per_ticket":           items,
			"items_per_ticket_delta_pct": itemsDelta,
			"stockout_products":          stockouts,
			"no_demand_products":         noDem,
			"ai_opportunity_mxn":         port.Net,
		},
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

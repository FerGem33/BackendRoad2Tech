// GET /stock/summary — ENDPOINTS.md §3.8. Stock counts by status (out/low/ok/over) with the same filters
// as §3.7. store_id is optional: without it, the whole chain.
package main

import (
	"context"
	"strings"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Query(r, "store_id")
	category := httpx.Query(r, "category_id")
	q := strings.ToLower(httpx.Query(r, "q"))

	var all []db.Stock
	if storeID != "" {
		var err error
		all, err = db.Stocks(ctx, storeID)
		if err != nil {
			return 0, nil, err
		}
	} else {
		stores, err := db.Stores(ctx)
		if err != nil {
			return 0, nil, err
		}
		lists := make([][]db.Stock, len(stores))
		g, gctx := errgroup.WithContext(ctx)
		for i := range stores {
			i := i
			g.Go(func() (err error) { lists[i], err = db.Stocks(gctx, stores[i].StoreID); return })
		}
		if err := g.Wait(); err != nil {
			return 0, nil, err
		}
		for _, l := range lists {
			all = append(all, l...)
		}
	}

	counts := map[string]int{"out": 0, "low": 0, "ok": 0, "over": 0}
	total := 0
	for _, s := range all {
		if category != "" && s.CategoryID != category {
			continue
		}
		if q != "" && !strings.Contains(strings.ToLower(s.ProductName), q) && !strings.Contains(strings.ToLower(s.ProductID), q) {
			continue
		}
		counts[domain.StockStatus(s.OnHand, s.ReorderPoint, s.MaxStock)]++
		total++
	}
	return 200, map[string]any{
		"total": total, "out": counts["out"], "low": counts["low"], "ok": counts["ok"], "over": counts["over"],
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

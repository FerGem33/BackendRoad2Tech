// GET /heat — ENDPOINTS.md §3.5. Demand intensity across stores (movement 0..100, one top store), with an
// optional product filter, zone filter and actual/forecast mode.
package main

import (
	"context"
	"math"
	"sort"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

type heatItem struct {
	StoreID  string `json:"store_id"`
	Movement int    `json:"movement"`
	Units    int    `json:"units"`
	IsTop    bool   `json:"is_top"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	asOf := httpx.DataAsOf()
	mode := httpx.Query(r, "mode")
	if mode == "" {
		mode = "actual"
	}
	if mode != "actual" && mode != "forecast" {
		return 0, nil, httpx.BadRequest("mode debe ser actual o forecast.")
	}
	from, to := httpx.Query(r, "from"), httpx.Query(r, "to")
	if from == "" {
		if mode == "forecast" {
			from = domain.AddDays(asOf, 1)
		} else {
			from = domain.AddDays(asOf, -29)
		}
	}
	if to == "" {
		if mode == "forecast" {
			to = domain.AddDays(asOf, domain.ForecastHorizonDays)
		} else {
			to = asOf
		}
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
	productID := httpx.Query(r, "product_id")
	zone := httpx.Query(r, "zone")

	stores, err := db.Stores(ctx)
	if err != nil {
		return 0, nil, err
	}
	filtered := stores[:0]
	for _, s := range stores {
		if zone != "" && s.ZoneType != zone {
			continue
		}
		filtered = append(filtered, s)
	}

	units := make([]float64, len(filtered))
	g, gctx := errgroup.WithContext(ctx)
	for i := range filtered {
		i := i
		g.Go(func() error {
			id := filtered[i].StoreID
			if mode == "forecast" {
				fc, err := db.Forecasts(gctx, id, from, to)
				if err != nil {
					return err
				}
				for pid, list := range fc {
					if productID != "" && pid != productID {
						continue
					}
					for _, f := range list {
						units[i] += f.P50
					}
				}
				return nil
			}
			if productID != "" {
				var sales []db.Sale
				if err := db.ProductTimeline(gctx, id, productID, "SALE#", &sales); err != nil {
					return err
				}
				for _, s := range sales {
					if s.Date >= from && s.Date <= to {
						units[i] += float64(s.UnitsSold)
					}
				}
				return nil
			}
			days, err := db.StoreDaySales(gctx, id, from, to)
			if err != nil {
				return err
			}
			for _, d := range days {
				units[i] += float64(d.UnitsSold)
			}
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return 0, nil, err
	}

	maxU := 0.0
	for _, u := range units {
		maxU = math.Max(maxU, u)
	}
	items := make([]heatItem, len(filtered))
	topIdx := -1
	for i, u := range units {
		m := 0
		if maxU > 0 {
			m = int(math.Round(100 * u / maxU))
		}
		items[i] = heatItem{filtered[i].StoreID, m, int(math.Round(u)), false}
		if topIdx == -1 || u > units[topIdx] {
			topIdx = i
		}
	}
	if topIdx >= 0 && units[topIdx] > 0 {
		items[topIdx].IsTop = true
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i].StoreID < items[j].StoreID })

	var pid any
	if productID != "" {
		pid = productID
	}
	return 200, map[string]any{"product_id": pid, "metric": "units_sold", "items": items}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

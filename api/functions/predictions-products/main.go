// GET /stores/{store_id}/predictions/products — ENDPOINTS.md §3.21. Products of one store ordered
// by priority, for the demand-map side panel. Same row shape as §3.20 without store_id/products_count.
package main

import (
	"context"
	"sort"
	"strconv"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type productRow struct {
	domain.PredictionRow
	ProductID string `json:"product_id"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
	asOf := httpx.DataAsOf()
	categoryID := httpx.Query(r, "category_id")
	tag := httpx.Query(r, "tag")

	horizonDays := 7
	if v := httpx.Query(r, "horizon_days"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 30 {
			return 0, nil, httpx.BadRequest("horizon_days debe estar entre 1 y 30.")
		}
		horizonDays = n
	}
	sortBy := httpx.Query(r, "sort")
	if sortBy == "" {
		sortBy = "priority"
	}
	if sortBy != "priority" && sortBy != "predicted_daily_demand" && sortBy != "uplift_pct" && sortBy != "recommended_extra_qty" {
		return 0, nil, httpx.BadRequest("sort debe ser priority, predicted_daily_demand, uplift_pct o recommended_extra_qty.")
	}
	page, err := httpx.ParsePage(r, 8)
	if err != nil {
		return 0, nil, err
	}

	sc, err := domain.LoadStoreContext(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	sales, err := db.StoreSales(ctx, storeID, domain.AddDays(asOf, -13), asOf)
	if err != nil {
		return 0, nil, err
	}
	sold := map[string]float64{}
	for _, s := range sales {
		sold[s.ProductID] += float64(s.UnitsSold)
	}
	for pid := range sold {
		sold[pid] = sold[pid] / 14
	}

	items := []productRow{}
	for _, s := range sc.Stocks {
		if categoryID != "" && s.CategoryID != categoryID {
			continue
		}
		pr := domain.PredictionRowFor(sc, s, sold[s.ProductID], domain.TargetDays)
		items = append(items, productRow{PredictionRow: pr, ProductID: s.ProductID})
	}

	switch sortBy {
	case "predicted_daily_demand":
		sort.SliceStable(items, func(i, j int) bool { return items[i].PredictedDailyDemand > items[j].PredictedDailyDemand })
	case "uplift_pct":
		sort.SliceStable(items, func(i, j int) bool { return items[i].UpliftPct > items[j].UpliftPct })
	case "recommended_extra_qty":
		sort.SliceStable(items, func(i, j int) bool { return items[i].RecommendedExtraQty > items[j].RecommendedExtraQty })
	default: // priority
		sort.SliceStable(items, func(i, j int) bool {
			ri, rj := riskRank(items[i].Risk), riskRank(items[j].Risk)
			if ri != rj {
				return ri > rj // critical first, then watch, then healthy
			}
			if items[i].RecommendedExtraQty != items[j].RecommendedExtraQty {
				return items[i].RecommendedExtraQty > items[j].RecommendedExtraQty
			}
			return items[i].PredictedDailyDemand > items[j].PredictedDailyDemand
		})
	}

	paged := httpx.Paginate(items, &page)
	return 200, map[string]any{
		"store_id": storeID, "horizon_days": horizonDays, "target_days": domain.TargetDays,
		"filters": map[string]any{"category_id": nullIfEmpty(categoryID), "tag": nullIfEmpty(tag)},
		"items":   paged, "page": page.Page, "page_size": page.PageSize, "total": page.Total,
	}, nil
}

func riskRank(risk string) int {
	switch risk {
	case "critical":
		return 2
	case "watch":
		return 1
	default:
		return 0
	}
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

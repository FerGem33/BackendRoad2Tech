// GET /predictions/stores — ENDPOINTS.md §3.20. Demand forecast of every store for one product,
// or for the products matching the category/tag filters (aggregate). Feeds the demand map.
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
	"golang.org/x/sync/errgroup"
)

type storeRow struct {
	StoreID              string   `json:"store_id"`
	ProductID            *string  `json:"product_id"`
	ProductsCount        int      `json:"products_count"`
	CurrentDailyDemand   float64  `json:"current_daily_demand"`
	PredictedDailyDemand float64  `json:"predicted_daily_demand"`
	UpliftPct            float64  `json:"uplift_pct"`
	Confidence           float64  `json:"confidence"`
	ClosingQty           float64  `json:"closing_qty"`
	OnOrderQty           float64  `json:"on_order_qty"`
	CoverageDays         *float64 `json:"coverage_days"`
	RecommendedExtraQty  int      `json:"recommended_extra_qty"`
	Risk                 string   `json:"risk"`
	ReasonCodes          []string `json:"reason_codes"`
}

type storeInput struct {
	sc   *domain.StoreContext
	sold map[string]float64 // product_id -> avg daily units over the last 14 days
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	asOf := httpx.DataAsOf()
	productID := httpx.Query(r, "product_id")
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

	stores, err := db.Stores(ctx)
	if err != nil {
		return 0, nil, err
	}
	products, err := db.Products(ctx)
	if err != nil {
		return 0, nil, err
	}
	// Resolve the matching product set. tag has no effect until products carry tags (duda D20).
	matches := func(pid, cat string) bool {
		if productID != "" {
			return pid == productID
		}
		if categoryID != "" {
			return cat == categoryID
		}
		return true
	}
	matched := 0
	for _, p := range products {
		if matches(p.ProductID, p.CategoryID) {
			matched++
		}
	}
	if matched == 0 {
		return 200, map[string]any{
			"product_id": nil, "horizon_days": horizonDays, "target_days": domain.TargetDays,
			"filters": map[string]any{"category_id": nullIfEmpty(categoryID), "tag": nullIfEmpty(tag)},
			"summary": map[string]any{"stores_count": 0, "current_daily_demand": 0.0, "predicted_daily_demand": 0.0,
				"average_uplift_pct": 0.0, "stores_at_risk": 0, "recommended_extra_qty": 0},
			"items": []storeRow{},
		}, nil
	}

	since14 := domain.AddDays(asOf, -13)
	inputs := make([]storeInput, len(stores))
	g, gctx := errgroup.WithContext(ctx)
	for i := range stores {
		i := i
		g.Go(func() error {
			sc, err := domain.LoadStoreContext(gctx, stores[i].StoreID)
			if err != nil {
				return err
			}
			sales, err := db.StoreSales(gctx, stores[i].StoreID, since14, asOf)
			if err != nil {
				return err
			}
			sold := map[string]float64{}
			for _, s := range sales {
				sold[s.ProductID] += float64(s.UnitsSold)
			}
			for pid := range sold {
				sold[pid] = sold[pid] / 14
			}
			inputs[i] = storeInput{sc: sc, sold: sold}
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return 0, nil, err
	}

	items := make([]storeRow, len(stores))
	for i, in := range inputs {
		items[i] = aggregateStoreRow(stores[i].StoreID, productID, categoryID, matches, in)
	}

	var (
		sumCur, sumPred, sumExtra float64
		atRisk                    int
	)
	for _, it := range items {
		sumCur += it.CurrentDailyDemand
		sumPred += it.PredictedDailyDemand
		sumExtra += float64(it.RecommendedExtraQty)
		if it.Risk != "healthy" {
			atRisk++
		}
	}
	uplift := 0.0
	if sumCur > 0 {
		uplift = sumPred/sumCur - 1
	}
	return 200, map[string]any{
		"product_id":   nullIfEmpty(productID),
		"horizon_days": horizonDays,
		"target_days":  domain.TargetDays,
		"filters":      map[string]any{"category_id": nullIfEmpty(categoryID), "tag": nullIfEmpty(tag)},
		"summary": map[string]any{
			"stores_count":           len(stores),
			"current_daily_demand":   httpx.Round(sumCur, 1),
			"predicted_daily_demand": httpx.Round(sumPred, 1),
			"average_uplift_pct":     httpx.Round(uplift, 3),
			"stores_at_risk":         atRisk,
			"recommended_extra_qty":  int(sumExtra),
		},
		"items": items,
	}, nil
}

// aggregateStoreRow builds one row per store, either for the single requested product or the
// sum of the matching products (product_id null).
func aggregateStoreRow(storeID, productID, categoryID string, matches func(pid, cat string) bool, in storeInput) storeRow {
	single := productID != ""
	row := storeRow{StoreID: storeID, Risk: "healthy"}
	if single {
		row.ProductID = &productID
		row.ProductsCount = 1
	}
	var (
		confSum   float64
		confN     int
		codes     = map[string]bool{}
		worstRisk = 0 // 0 healthy, 1 watch, 2 critical
	)
	for _, s := range in.sc.Stocks {
		if !matches(s.ProductID, s.CategoryID) {
			continue
		}
		pr := domain.PredictionRowFor(in.sc, s, in.sold[s.ProductID], domain.TargetDays)
		row.CurrentDailyDemand += pr.CurrentDailyDemand
		row.PredictedDailyDemand += pr.PredictedDailyDemand
		row.ClosingQty += pr.ClosingQty
		row.OnOrderQty += pr.OnOrderQty
		row.RecommendedExtraQty += pr.RecommendedExtraQty
		confSum += pr.Confidence
		confN++
		for _, c := range pr.ReasonCodes {
			codes[c] = true
		}
		if lvl := riskLevel(pr.Risk); lvl > worstRisk {
			worstRisk = lvl
		}
		if !single {
			row.ProductsCount++
		}
	}
	row.CurrentDailyDemand = httpx.Round(row.CurrentDailyDemand, 1)
	row.PredictedDailyDemand = httpx.Round(row.PredictedDailyDemand, 1)
	if confN > 0 {
		row.Confidence = httpx.Round(confSum/float64(confN), 2)
	}
	if row.PredictedDailyDemand > 0 {
		c := httpx.Round(row.ClosingQty/row.PredictedDailyDemand, 1)
		row.CoverageDays = &c
	}
	row.UpliftPct = 0
	if row.CurrentDailyDemand > 0 {
		row.UpliftPct = httpx.Round(row.PredictedDailyDemand/row.CurrentDailyDemand-1, 3)
	}
	row.Risk = riskLabel(worstRisk)
	row.ReasonCodes = make([]string, 0, len(codes))
	for c := range codes {
		row.ReasonCodes = append(row.ReasonCodes, c)
	}
	sort.Strings(row.ReasonCodes)
	if len(row.ReasonCodes) > 3 {
		row.ReasonCodes = row.ReasonCodes[:3]
	}
	return row
}

func riskLevel(risk string) int {
	switch risk {
	case "critical":
		return 2
	case "watch":
		return 1
	default:
		return 0
	}
}

func riskLabel(level int) string {
	switch level {
	case 2:
		return "critical"
	case 1:
		return "watch"
	default:
		return "healthy"
	}
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

// GET /stores/{store_id}/stock — ENDPOINTS.md §3.7. Paginated/filtered stock per product. store_id may be
// "all" to list the whole chain (each item carries its store_id).
package main

import (
	"context"
	"math"
	"sort"
	"strings"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

type transitOut struct {
	OrderID              string `json:"order_id"`
	Quantity             int    `json:"quantity"`
	SupplierID           string `json:"supplier_id"`
	ExpectedDeliveryDate string `json:"expected_delivery_date"`
	IsDelayed            bool   `json:"is_delayed"`
}

type stockItem struct {
	StoreID         string       `json:"store_id"`
	ProductID       string       `json:"product_id"`
	ProductName     string       `json:"product_name"`
	CategoryID      string       `json:"category_id"`
	ClosingQty      int          `json:"closing_qty"`
	OnOrderQty      int          `json:"on_order_qty"`
	InTransit       []transitOut `json:"in_transit"`
	ReorderPoint    int          `json:"reorder_point"`
	MaxStock        int          `json:"max_stock"`
	CoverageDays    *float64     `json:"coverage_days"`
	Status          string       `json:"status"`
	EarliestExpiry  *string      `json:"earliest_expiry"`
	ExpiringQty7d   int          `json:"expiring_qty_7d"`
	LostDemand7d    int          `json:"lost_demand_7d"`
	NextRestockDate *string      `json:"next_restock_date"`
}

func loadStock(ctx context.Context, storeID string) ([]stockItem, error) {
	sc, err := domain.LoadStoreContext(ctx, storeID)
	if err != nil {
		return nil, err
	}
	lots, err := db.LotsExpiringBetween(ctx, storeID, sc.AsOf, domain.AddDays(sc.AsOf, 7))
	if err != nil {
		return nil, err
	}
	expiring := map[string]int{}
	for _, l := range lots {
		if l.Status == "ACTIVE" {
			expiring[l.ProductID] += l.QuantityRemaining
		}
	}
	items := make([]stockItem, 0, len(sc.Stocks))
	for _, s := range sc.Stocks {
		onHand := int(math.Round(s.OnHand))
		onOrder := int(math.Round(s.OnOrder))
		status := domain.StockStatus(s.OnHand, s.ReorderPoint, s.MaxStock)

		transit := []transitOut{}
		next := ""
		for _, o := range sc.OpenOrders[s.ProductID] {
			delayed := o.ExpectedDeliveryDate < sc.AsOf
			transit = append(transit, transitOut{o.OrderID, o.Quantity, o.SupplierID, o.ExpectedDeliveryDate, delayed})
			if !delayed && (next == "" || o.ExpectedDeliveryDate < next) {
				next = o.ExpectedDeliveryDate
			}
		}
		sort.Slice(transit, func(i, j int) bool { return transit[i].ExpectedDeliveryDate < transit[j].ExpectedDeliveryDate })

		var coverage *float64
		if s.PredictedDailyDemand > 0 {
			c := httpx.Round(s.OnHand/s.PredictedDailyDemand, 1)
			coverage = &c
		}
		var nextPtr *string
		if next != "" {
			nextPtr = &next
		}
		items = append(items, stockItem{
			StoreID: storeID, ProductID: s.ProductID, ProductName: s.ProductName, CategoryID: s.CategoryID,
			ClosingQty: onHand, OnOrderQty: onOrder, InTransit: transit,
			ReorderPoint: int(math.Round(s.ReorderPoint)), MaxStock: int(math.Round(s.MaxStock)),
			CoverageDays: coverage, Status: status, EarliestExpiry: s.NextExpirationDate,
			ExpiringQty7d: expiring[s.ProductID], LostDemand7d: s.LostDemand7d, NextRestockDate: nextPtr,
		})
	}
	return items, nil
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
	page, err := httpx.ParsePage(r, 25)
	if err != nil {
		return 0, nil, err
	}
	status := httpx.Query(r, "status")
	if status != "" && status != "out" && status != "low" && status != "ok" && status != "over" {
		return 0, nil, httpx.BadRequest("status debe ser out, low, ok u over.")
	}
	category := httpx.Query(r, "category_id")
	q := strings.ToLower(httpx.Query(r, "q"))
	sortBy := httpx.Query(r, "sort")

	var all []stockItem
	if storeID == "all" {
		stores, err := db.Stores(ctx)
		if err != nil {
			return 0, nil, err
		}
		lists := make([][]stockItem, len(stores))
		g, gctx := errgroup.WithContext(ctx)
		for i := range stores {
			i := i
			g.Go(func() (err error) { lists[i], err = loadStock(gctx, stores[i].StoreID); return })
		}
		if err := g.Wait(); err != nil {
			return 0, nil, err
		}
		for _, l := range lists {
			all = append(all, l...)
		}
	} else {
		all, err = loadStock(ctx, storeID)
		if err != nil {
			return 0, nil, err
		}
	}

	items := all[:0]
	for _, it := range all {
		if status != "" && it.Status != status {
			continue
		}
		if category != "" && it.CategoryID != category {
			continue
		}
		if q != "" && !strings.Contains(strings.ToLower(it.ProductName), q) && !strings.Contains(strings.ToLower(it.ProductID), q) {
			continue
		}
		items = append(items, it)
	}
	switch sortBy {
	case "coverage_days":
		sort.SliceStable(items, func(i, j int) bool { return cov(items[i]) < cov(items[j]) })
	case "quantity":
		sort.SliceStable(items, func(i, j int) bool { return items[i].ClosingQty < items[j].ClosingQty })
	default:
		sort.SliceStable(items, func(i, j int) bool { return items[i].ProductName < items[j].ProductName })
	}
	paged := httpx.Paginate(items, &page)
	return 200, map[string]any{"store_id": storeID, "items": paged, "page": page.Page,
		"page_size": page.PageSize, "total": page.Total}, nil
}

func cov(it stockItem) float64 {
	if it.CoverageDays == nil {
		return math.MaxFloat64
	}
	return *it.CoverageDays
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

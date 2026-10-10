// GET /suppliers and GET /suppliers/{supplier_id}/offers — ENDPOINTS.md §3.14 (read only).
package main

import (
	"context"
	"sort"
	"strings"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type supplierOut struct {
	SupplierID       string   `json:"supplier_id"`
	SupplierName     string   `json:"supplier_name"`
	Email            string   `json:"email"`
	City             string   `json:"city"`
	CategoriesServed []string `json:"categories_served"`
	ReliabilityScore float64  `json:"reliability_score"`
	OffersCount      int      `json:"offers_count"`
}

type offerOut struct {
	ProductID    string  `json:"product_id"`
	ProductName  string  `json:"product_name"`
	CategoryID   string  `json:"category_id"`
	UnitCost     float64 `json:"unit_cost"`
	MinOrderQty  int     `json:"min_order_qty"`
	LeadTimeDays int     `json:"lead_time_days"`
	DeliveryDays string  `json:"delivery_days"`
	RetailPrice  float64 `json:"retail_price"`
}

func list(ctx context.Context) (int, any, error) {
	sups, err := db.Suppliers(ctx)
	if err != nil {
		return 0, nil, err
	}
	items := make([]supplierOut, 0, len(sups))
	for _, s := range sups {
		items = append(items, supplierOut{s.SupplierID, s.SupplierName, s.Email, s.City, s.CategoriesServed,
			s.ReliabilityScore, s.OffersCount})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].SupplierID < items[j].SupplierID })
	return 200, map[string]any{"items": items}, nil
}

func offers(ctx context.Context, r *httpx.Request, supplierID string) (int, any, error) {
	page, err := httpx.ParsePage(r, 25)
	if err != nil {
		return 0, nil, err
	}
	var sup db.Supplier
	found, err := db.Get(ctx, "SUPPLIER#"+supplierID, "#META", &sup)
	if err != nil {
		return 0, nil, err
	}
	if !found {
		return 0, nil, httpx.NotFound("supplier_not_found", "El proveedor %s no existe.", supplierID)
	}
	all, err := db.SupplierOffers(ctx, supplierID)
	if err != nil {
		return 0, nil, err
	}
	category, q := httpx.Query(r, "category_id"), strings.ToLower(httpx.Query(r, "q"))
	items := []offerOut{}
	for _, o := range all {
		if category != "" && o.CategoryID != category {
			continue
		}
		if q != "" && !strings.Contains(strings.ToLower(o.ProductName), q) && !strings.Contains(strings.ToLower(o.ProductID), q) {
			continue
		}
		items = append(items, offerOut{o.ProductID, o.ProductName, o.CategoryID, o.UnitCost, o.MinOrderQty,
			o.LeadTimeDays, strings.Join(o.DeliveryDays, "|"), o.RetailPrice})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].ProductID < items[j].ProductID })
	paged := httpx.Paginate(items, &page)
	return 200, map[string]any{"supplier_id": supplierID, "items": paged, "page": page.Page,
		"page_size": page.PageSize, "total": page.Total}, nil
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	if id := httpx.Path(r, "supplier_id"); id != "" {
		return offers(ctx, r, id)
	}
	return list(ctx)
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

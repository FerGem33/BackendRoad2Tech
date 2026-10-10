// GET /products — ENDPOINTS.md §3.16. Catalog with filters category_id and q, paginated.
package main

import (
	"context"
	"strings"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type productOut struct {
	ProductID          string             `json:"product_id"`
	ProductName        string             `json:"product_name"`
	CategoryID         string             `json:"category_id"`
	Brand              string             `json:"brand"`
	Unit               string             `json:"unit"`
	RetailPrice        float64            `json:"retail_price"`
	ShelfLifeDays      int                `json:"shelf_life_days"`
	IsSeasonal         int                `json:"is_seasonal"`
	WeatherSensitivity string             `json:"weather_sensitivity"`
	BaseDailyDemand    float64            `json:"base_daily_demand"`
	DefaultSupplier    db.DefaultSupplier `json:"default_supplier"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	page, err := httpx.ParsePage(r, 25)
	if err != nil {
		return 0, nil, err
	}
	all, err := db.Products(ctx)
	if err != nil {
		return 0, nil, err
	}
	category, q := httpx.Query(r, "category_id"), strings.ToLower(httpx.Query(r, "q"))
	items := []productOut{}
	for _, p := range all {
		if category != "" && p.CategoryID != category {
			continue
		}
		if q != "" && !strings.Contains(strings.ToLower(p.ProductName+" "+p.Brand+" "+p.ProductID), q) {
			continue
		}
		seasonal := 0
		if p.IsSeasonal {
			seasonal = 1
		}
		items = append(items, productOut{p.ProductID, p.ProductName, p.CategoryID, p.Brand, p.Unit, p.RetailPrice,
			p.ShelfLifeDays, seasonal, p.WeatherSensitivity, p.BaseDailyDemand, p.DefaultSupplier})
	}
	paged := httpx.Paginate(items, &page)
	return 200, map[string]any{"items": paged, "page": page.Page, "page_size": page.PageSize, "total": page.Total}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

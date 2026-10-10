// GET /stores/{store_id}/restock-suggestions — ENDPOINTS.md §3.11. Projects stock day by day with the forecast
// and the open orders, and returns the orders select_supplier() would place between from and to.
package main

import (
	"context"
	"sort"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

const maxRangeDays = 60

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	asOf := httpx.DataAsOf()
	from, to := httpx.Query(r, "from"), httpx.Query(r, "to")
	if from == "" {
		from = asOf
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
	if from < asOf {
		from = asOf // suggestions only exist from data_as_of onwards
	}
	if to < from || domain.DaysBetween(asOf, to) > maxRangeDays {
		return 0, nil, httpx.BadRequest("Rango inválido: to >= from y como máximo %d días después de %s.", maxRangeDays, asOf)
	}
	urgentOnly, err := httpx.QueryBool(r, "urgent_only")
	if err != nil {
		return 0, nil, err
	}
	productID := httpx.Query(r, "product_id")
	page, err := httpx.ParsePage(r, 25)
	if err != nil {
		return 0, nil, err
	}
	sc, err := domain.LoadStoreContext(ctx, httpx.Path(r, "store_id"))
	if err != nil {
		return 0, nil, err
	}
	all := sc.RestockSuggestions(from, to)
	items := all[:0]
	for _, s := range all {
		if !urgentOnly || s.IsUrgent {
			if productID != "" && s.ProductID != productID {
				continue
			}
			items = append(items, s)
		}
	}
	sort.SliceStable(items, func(i, j int) bool {
		if items[i].OrderDate != items[j].OrderDate {
			return items[i].OrderDate < items[j].OrderDate
		}
		if items[i].IsUrgent != items[j].IsUrgent {
			return items[i].IsUrgent
		}
		return items[i].ExpectedLostSalesIfSkipped > items[j].ExpectedLostSalesIfSkipped
	})
	paged := httpx.Paginate(items, &page)
	return 200, map[string]any{"store_id": sc.Store.StoreID, "from": from, "to": to, "items": paged,
		"page": page.Page, "page_size": page.PageSize, "total": page.Total}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

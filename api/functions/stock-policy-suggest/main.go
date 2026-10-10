// POST /stores/{store_id}/stock-policy:suggest — ENDPOINTS.md §3.10. Suggests min (reorder_point) and max per
// product from the 14-day forecast and the lead time of the chosen supplier. Persists nothing.
package main

import (
	"context"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type body struct {
	ProductIDs []string `json:"product_ids"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	var b body
	if err := httpx.Decode(r, &b); err != nil {
		return 0, nil, err
	}
	if len(b.ProductIDs) > 120 {
		return 0, nil, httpx.BadRequest("product_ids admite máximo 120 productos.")
	}
	sc, err := domain.LoadStoreContext(ctx, httpx.Path(r, "store_id"))
	if err != nil {
		return 0, nil, err
	}
	want := map[string]bool{}
	for _, id := range b.ProductIDs {
		want[id] = true
	}
	items := []domain.PolicySuggestion{}
	for _, s := range sc.Stocks {
		if len(want) == 0 || want[s.ProductID] {
			items = append(items, sc.SuggestPolicy(s))
			delete(want, s.ProductID)
		}
	}
	if len(b.ProductIDs) > 0 && len(want) > 0 {
		missing := make([]string, 0, len(want))
		for id := range want {
			missing = append(missing, id)
		}
		e := httpx.NotFound("product_not_found", "Productos inexistentes en la tienda.")
		e.Details = map[string]any{"product_ids": missing}
		return 0, nil, e
	}
	return 200, map[string]any{"store_id": sc.Store.StoreID, "items": items}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

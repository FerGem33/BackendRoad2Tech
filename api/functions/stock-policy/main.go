// GET / PUT /stores/{store_id}/stock-policy — ENDPOINTS.md §3.9. Per-product min (reorder_point) / max stock.
// The minimum shown is reorder_point (the same value simulate_operations.py uses). unit_cost is the offer
// chosen by select_supplier (the product's default_supplier; duda D6).
package main

import (
	"context"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type policyItem struct {
	ProductID     string  `json:"product_id"`
	ProductName   string  `json:"product_name"`
	CategoryID    string  `json:"category_id"`
	UnitCost      float64 `json:"unit_cost"`
	RetailPrice   float64 `json:"retail_price"`
	MinOrderQty   int     `json:"min_order_qty"`
	ShelfLifeDays int     `json:"shelf_life_days"`
	ReorderPoint  int     `json:"reorder_point"`
	MaxStock      int     `json:"max_stock"`
	UpdatedAt     string  `json:"updated_at"`
}

type putItem struct {
	ProductID    string `json:"product_id"`
	ReorderPoint int    `json:"reorder_point"`
	MaxStock     int    `json:"max_stock"`
}

type putBody struct {
	Items []putItem `json:"items"`
}

func get(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
	page, err := httpx.ParsePage(r, 25)
	if err != nil {
		return 0, nil, err
	}
	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	if store == nil {
		return 0, nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}
	products, err := db.ProductMap(ctx)
	if err != nil {
		return 0, nil, err
	}
	stocks, err := db.Stocks(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	category := httpx.Query(r, "category_id")
	q := strings.ToLower(httpx.Query(r, "q"))
	items := []policyItem{}
	for _, s := range stocks {
		if category != "" && s.CategoryID != category {
			continue
		}
		if q != "" && !strings.Contains(strings.ToLower(s.ProductName), q) && !strings.Contains(strings.ToLower(s.ProductID), q) {
			continue
		}
		p := products[s.ProductID]
		items = append(items, policyItem{
			ProductID: s.ProductID, ProductName: s.ProductName, CategoryID: s.CategoryID,
			UnitCost: p.DefaultSupplier.UnitCost, RetailPrice: s.RetailPrice, MinOrderQty: p.DefaultSupplier.MinOrderQty,
			ShelfLifeDays: s.ShelfLifeDays, ReorderPoint: int(math.Round(s.ReorderPoint)),
			MaxStock: int(math.Round(s.MaxStock)), UpdatedAt: s.UpdatedAt,
		})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].ProductID < items[j].ProductID })
	paged := httpx.Paginate(items, &page)
	return 200, map[string]any{"store_id": storeID, "items": paged, "page": page.Page,
		"page_size": page.PageSize, "total": page.Total}, nil
}

func put(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
	var b putBody
	if err := httpx.Decode(r, &b); err != nil {
		return 0, nil, err
	}
	if len(b.Items) == 0 {
		return 0, nil, httpx.BadRequest("items es obligatorio.")
	}
	if len(b.Items) > 120 {
		return 0, nil, httpx.BadRequest("items admite máximo 120 productos.")
	}
	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	if store == nil {
		return 0, nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}
	stocks, err := db.Stocks(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	known := map[string]bool{}
	for _, s := range stocks {
		known[s.ProductID] = true
	}
	var invalid []map[string]any
	valid := []putItem{}
	for _, it := range b.Items {
		reason := ""
		switch {
		case !known[it.ProductID]:
			reason = "product_not_in_store"
		case it.ReorderPoint < 0 || it.MaxStock < 0 || it.ReorderPoint > it.MaxStock:
			reason = "must be 0 <= reorder_point <= max_stock"
		}
		if reason != "" {
			invalid = append(invalid, map[string]any{"product_id": it.ProductID, "code": reason})
			continue
		}
		valid = append(valid, it)
	}
	if len(invalid) > 0 {
		e := httpx.Errorf(422, "invalid_policy", "Hay %d políticas inválidas.", len(invalid))
		e.Details = map[string]any{"items": invalid}
		return 0, nil, e
	}

	now := time.Now().UTC().Format(time.RFC3339)
	for _, it := range valid {
		_, err := db.Client(ctx).UpdateItem(ctx, &dynamodb.UpdateItemInput{
			TableName:        aws.String(db.Table()),
			Key:              db.Key("STORE#"+storeID, "STOCK#"+it.ProductID),
			UpdateExpression: aws.String("SET reorder_point = :rp, max_stock = :mx, updated_at = :now"),
			ExpressionAttributeValues: map[string]types.AttributeValue{
				":rp":  &types.AttributeValueMemberN{Value: strconv.Itoa(it.ReorderPoint)},
				":mx":  &types.AttributeValueMemberN{Value: strconv.Itoa(it.MaxStock)},
				":now": db.S(now),
			},
		})
		if err != nil {
			return 0, nil, err
		}
	}
	updated := make([]map[string]any, 0, len(valid))
	for _, it := range valid {
		updated = append(updated, map[string]any{"product_id": it.ProductID, "reorder_point": it.ReorderPoint,
			"max_stock": it.MaxStock, "updated_at": now})
	}
	return 200, map[string]any{"store_id": storeID, "items": updated, "updated_at": now}, nil
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	if r.RequestContext.HTTP.Method == "PUT" {
		return put(ctx, r)
	}
	return get(ctx, r)
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

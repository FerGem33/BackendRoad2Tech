// GET /stores — ENDPOINTS.md §3.3. Full store list (25 rows, no pagination).
package main

import (
	"context"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

type storeOut struct {
	StoreID           string  `json:"store_id"`
	StoreName         string  `json:"store_name"`
	Colonia           string  `json:"colonia"`
	Latitude          float64 `json:"latitude"`
	Longitude         float64 `json:"longitude"`
	ZoneType          string  `json:"zone_type"`
	SecondaryZoneType *string `json:"secondary_zone_type"`
	SizeCategory      string  `json:"size_category"`
	OpeningHours      string  `json:"opening_hours"`
	Is24h             int     `json:"is_24h"`
	OpenedDate        string  `json:"opened_date"`
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	stores, err := db.Stores(ctx)
	if err != nil {
		return 0, nil, err
	}
	items := make([]storeOut, 0, len(stores))
	for _, s := range stores {
		items = append(items, storeOut{s.StoreID, s.StoreName, s.Colonia, s.Latitude, s.Longitude, s.ZoneType,
			s.SecondaryZoneType, s.SizeCategory, s.OpeningHours, b2i(s.Is24h), s.OpenedDate})
	}
	return 200, map[string]any{"items": items}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

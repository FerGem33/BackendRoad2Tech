// GET /meta — ENDPOINTS.md §3.1. Bootstrap: data_as_of, category/zone catalogs and forecast horizons.
package main

import (
	"context"
	"os"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

type categoryOut struct {
	CategoryID            string `json:"category_id"`
	CategoryName          string `json:"category_name"`
	IsPerishable          int    `json:"is_perishable"`
	TypicalShelfLifeDays  int    `json:"typical_shelf_life_days"`
	RequiresRefrigeration int    `json:"requires_refrigeration"`
}

type zoneOut struct {
	ZoneType    string `json:"zone_type"`
	Description string `json:"description"`
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	cats, err := db.Categories(ctx)
	if err != nil {
		return 0, nil, err
	}
	zones, err := db.Zones(ctx)
	if err != nil {
		return 0, nil, err
	}
	cOut := make([]categoryOut, 0, len(cats))
	for _, c := range cats {
		cOut = append(cOut, categoryOut{c.CategoryID, c.CategoryName, b2i(c.IsPerishable),
			c.TypicalShelfLifeDays, b2i(c.RequiresRefrigeration)})
	}
	zOut := make([]zoneOut, 0, len(zones))
	for _, z := range zones {
		zOut = append(zOut, zoneOut{z.ZoneType, z.Description})
	}
	// demo_mode: emails are redirected to DEMO_MAILBOX or only logged (no RESEND_API_KEY), so no
	// supplier receives a real email.
	demo := os.Getenv("DEMO_MAILBOX") != "" || os.Getenv("RESEND_API_KEY") == ""
	return 200, map[string]any{
		"data_as_of":              httpx.DataAsOf(),
		"history_start":           "2024-01-01",
		"forecast_horizon_days":   14,
		"horizons":                map[string]any{"history_days": []int{7, 30}, "forecast_days": []int{7, 14, 30}},
		"default_plan_days":       7,
		"max_strategies_per_plan": 5,
		"categories":              cOut,
		"zones":                   zOut,
		"demo_mode":               demo,
	}, nil
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

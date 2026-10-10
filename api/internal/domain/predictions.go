// Demand-prediction rows shared by §3.20 (GET /predictions/stores) and §3.21
// (GET /stores/{id}/predictions/products). Every number is the same one the other
// endpoints expose (stock, restock-suggestions, forecast); no client-side math.
package domain

import (
	"math"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

// TargetDays is how many days of predicted demand recommended_extra_qty must cover (duda D24).
const TargetDays = 4

// PredictionRow holds the computed demand-prediction fields of one store x product
// (ENDPOINTS.md §3.20/§3.21). The handlers add store_id/product_id/products_count.
type PredictionRow struct {
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

// PredictionRisk maps on-hand stock and coverage to the §3.20 risk levels (duda D21, same
// rule as the stock status): critical = out or coverage < lead time, watch = low or
// coverage < lead time + safety, healthy otherwise.
func PredictionRisk(s db.Stock, sc *StoreContext) string {
	pred := s.PredictedDailyDemand
	if s.OnHand <= 0 {
		return "critical"
	}
	lead, safety := 0, SafetyDaysFor(s.ShelfLifeDays)
	if offs := sc.Offers[s.ProductID]; len(offs) > 0 {
		dec := Decide(offs, s.RetailPrice, s.ShelfLifeDays, math.Max(pred, 1e-9), s.OnHand)
		lead = dec.Offer.LeadTimeDays
	}
	if pred <= 0 {
		return "healthy"
	}
	coverage := s.OnHand / pred
	switch {
	case coverage <= float64(lead):
		return "critical"
	case s.OnHand <= s.ReorderPoint || coverage <= float64(lead+safety):
		return "watch"
	default:
		return "healthy"
	}
}

// predictionConfidence is 1 - mean relative (p80-p50)/p50 spread, clamped to 0..1 (duda D22).
func predictionConfidence(c Curve) float64 {
	n, sum := 0, 0.0
	for i, p50 := range c.P50 {
		if p50 <= 0 || i >= len(c.P80) {
			continue
		}
		sum += math.Max(0, math.Min(1, 1-(c.P80[i]-p50)/p50))
		n++
	}
	if n == 0 {
		return 0
	}
	return httpx.Round(sum/float64(n), 2)
}

// predictionReasonCodes maps the forecast drivers to the stable §3.20 code list (máx. 3).
func predictionReasonCodes(c Curve) []string {
	seen := map[string]bool{}
	var out []string
	for _, f := range c.Items {
		for _, d := range f.Drivers {
			code := ""
			switch d.Type {
			case "WEATHER":
				if d.Name == "cold" {
					code = "weather_cold"
				} else {
					code = "weather_hot"
				}
			case "EVENT":
				if d.Name == "Payday" {
					code = "payday"
				} else {
					code = "holiday"
				}
			case "DEMAND":
				code = "commute_peak"
			}
			if code != "" && !seen[code] {
				seen[code] = true
				out = append(out, code)
			}
		}
		if len(out) >= 3 {
			break
		}
	}
	return out
}

// PredictionRowFor builds the prediction row for one store x product. currentDaily is the
// average units sold over the last 14 days (avg_daily_units_14d).
func PredictionRowFor(sc *StoreContext, s db.Stock, currentDaily float64, targetDays int) PredictionRow {
	pred := s.PredictedDailyDemand
	uplift := 0.0
	if currentDaily > 0 {
		uplift = pred/currentDaily - 1
	}
	var coverage *float64
	if pred > 0 {
		v := httpx.Round(s.OnHand/pred, 1)
		coverage = &v
	}
	extra := math.Max(0, pred*float64(targetDays)-s.OnHand)
	return PredictionRow{
		CurrentDailyDemand:   httpx.Round(currentDaily, 1),
		PredictedDailyDemand: httpx.Round(pred, 1),
		UpliftPct:            httpx.Round(uplift, 3),
		Confidence:           predictionConfidence(sc.Curve(s.ProductID)),
		ClosingQty:           s.OnHand,
		OnOrderQty:           s.OnOrder,
		CoverageDays:         coverage,
		RecommendedExtraQty:  int(math.Round(extra)),
		Risk:                 PredictionRisk(s, sc),
		ReasonCodes:          predictionReasonCodes(sc.Curve(s.ProductID)),
	}
}

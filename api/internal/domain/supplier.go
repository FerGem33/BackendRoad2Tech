// Package domain holds the business rules shared by the lambdas: supplier selection, restock projection
// and stock policy, ported from scripts/dataset/supplier_selection.py and simulate_operations.py.
package domain

import (
	"math"
	"time"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
)

const (
	CycleDays              = 7
	SafetyDaysPerishable   = 1
	SafetyDaysStable       = 3
	PerishableMaxShelfLife = 30
	MinDemandToOrder       = 0.05
	ForecastHorizonDays    = 14
)

const dateLayout = "2006-01-02"

func ParseDate(s string) (time.Time, error) { return time.Parse(dateLayout, s) }

func MustDate(s string) time.Time {
	t, err := ParseDate(s)
	if err != nil {
		panic(err)
	}
	return t
}

func FormatDate(t time.Time) string { return t.Format(dateLayout) }

func AddDays(s string, n int) string { return FormatDate(MustDate(s).AddDate(0, 0, n)) }

func DaysBetween(a, b string) int { return int(MustDate(b).Sub(MustDate(a)).Hours() / 24) }

func CycleDaysFor(shelf int) int { return min(CycleDays, shelf) }

func SafetyDaysFor(shelf int) int {
	if shelf <= PerishableMaxShelfLife {
		return SafetyDaysPerishable
	}
	return SafetyDaysStable
}

// OrderQuantity = max(min_order_qty, ceil(demand x (lead_time + cycle_days))).
func OrderQuantity(o db.Offer, demand float64, shelf int) int {
	need := demand * float64(o.LeadTimeDays+CycleDaysFor(shelf))
	return max(o.MinOrderQty, int(math.Ceil(need-1e-9)))
}

type Decision struct {
	Offer          db.Offer
	Quantity       int
	IsUrgent       bool
	WasteUnits     float64
	ExpectedProfit float64
	Score          float64
}

// Evaluate computes quantity, waste and expected profit of ordering from one offer.
func Evaluate(o db.Offer, retailPrice float64, shelf int, demand float64) Decision {
	demand = math.Max(demand, 1e-9)
	qty := OrderQuantity(o, demand, shelf)
	waste := math.Max(0, float64(qty)-demand*float64(shelf))
	profit := (retailPrice-o.UnitCost)*(float64(qty)-waste) - o.UnitCost*waste
	return Decision{Offer: o, Quantity: qty, WasteUnits: waste, ExpectedProfit: profit, Score: profit * o.ReliabilityScore}
}

// Decide is supplier_selection.decide(): keep offers that arrive before the stock runs out (else the fastest,
// flagged urgent), then the highest expected_profit x reliability; ties by lead time, then supplier_id.
func Decide(offers []db.Offer, retailPrice float64, shelf int, demand, stock float64) Decision {
	d := math.Max(demand, 1e-9)
	coverage := stock / d
	var eligible []db.Offer
	for _, o := range offers {
		if float64(o.LeadTimeDays) <= coverage {
			eligible = append(eligible, o)
		}
	}
	urgent := len(eligible) == 0
	if urgent {
		fastest := math.MaxInt
		for _, o := range offers {
			fastest = min(fastest, o.LeadTimeDays)
		}
		for _, o := range offers {
			if o.LeadTimeDays == fastest {
				eligible = append(eligible, o)
			}
		}
	}
	var best *Decision
	for _, o := range eligible {
		e := Evaluate(o, retailPrice, shelf, d)
		if best == nil || e.Score > best.Score ||
			(e.Score == best.Score && (o.LeadTimeDays < best.Offer.LeadTimeDays ||
				(o.LeadTimeDays == best.Offer.LeadTimeDays && o.SupplierID < best.Offer.SupplierID))) {
			best = &e
		}
	}
	best.IsUrgent = urgent
	return *best
}

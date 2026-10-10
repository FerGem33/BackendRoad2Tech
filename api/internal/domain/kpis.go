// KPI helpers shared by the dashboard endpoints (ENDPOINTS.md §3.2 / §3.4 / §3.7 / §3.8):
// ticket KPIs, stock status and the store portfolio (net expected margin increment of the plan).
package domain

import (
	"context"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

// StockStatus maps on-hand stock to the §3.7 states: out, low, ok, over.
func StockStatus(onHand, reorderPoint, maxStock float64) string {
	switch {
	case onHand <= 0:
		return "out"
	case onHand <= reorderPoint:
		return "low"
	case onHand > maxStock:
		return "over"
	default:
		return "ok"
	}
}

// TicketStats aggregates ticket day summaries into the ticket KPIs of §3.2/§3.4:
// average_ticket (mean net_amount per ticket), items_per_ticket (mean quantity per ticket) and their
// deltas vs the previous window of equal length.
func TicketStats(current, previous []db.TicketDay) (avgTicket, avgDelta, items, itemsDelta float64) {
	curTickets, curNet, curUnits := 0, 0.0, 0.0
	for _, t := range current {
		curTickets += t.Tickets
		curNet += t.NetAmount
		curUnits += float64(t.Units)
	}
	prevTickets, prevNet, prevUnits := 0, 0.0, 0.0
	for _, t := range previous {
		prevTickets += t.Tickets
		prevNet += t.NetAmount
		prevUnits += float64(t.Units)
	}
	if curTickets > 0 {
		avgTicket = curNet / float64(curTickets)
		items = curUnits / float64(curTickets)
	}
	if prevTickets > 0 {
		prevAvg := prevNet / float64(prevTickets)
		prevItems := prevUnits / float64(prevTickets)
		if prevAvg > 0 {
			avgDelta = avgTicket/prevAvg - 1
		}
		if prevItems > 0 {
			itemsDelta = items/prevItems - 1
		}
	}
	return httpx.Money(avgTicket), httpx.Round(avgDelta, 3), httpx.Round(items, 1), httpx.Round(itemsDelta, 3)
}

// PortfolioSummary is the plan impact of one store: the sum of expected margin increments of every
// strategy in a plan state (selected/approved/scheduled/active), as optimize_portfolio does.
type PortfolioSummary struct {
	Net             float64
	StrategiesCount int
	ConfidenceLow   float64
	ConfidenceHigh  float64
	ConfidenceLevel float64
}

// StorePortfolio sums the plan-status strategies of a store.
func StorePortfolio(ctx context.Context, storeID string) (PortfolioSummary, error) {
	drafts, err := StoreDrafts(ctx, storeID)
	if err != nil {
		return PortfolioSummary{}, err
	}
	p := PortfolioSummary{ConfidenceLevel: 0.8}
	for _, d := range drafts {
		if !PlanStatuses[d.Status] {
			continue
		}
		p.Net += d.ExpectedMarginIncrement
		p.StrategiesCount++
		p.ConfidenceLow += d.ConfidenceInterval.Low
		p.ConfidenceHigh += d.ConfidenceInterval.High
	}
	p.Net = httpx.Money(p.Net)
	p.ConfidenceLow = httpx.Money(p.ConfidenceLow)
	p.ConfidenceHigh = httpx.Money(p.ConfidenceHigh)
	return p, nil
}

package main

import (
	"context"
	"fmt"
	"math"
	"time"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
)

// Business constraints of a contextual offer (get_business_constraints).
const (
	minMarginPct      = 0.12
	defaultMaxDiscPct = 0.25
	budgetMXN         = 2500.0
	priceElasticity   = 4.0 // uplift = 1 + elasticity x discount
	ciLevel           = 0.8
)

type simulation struct {
	discount         float64
	baseline         domain.Scenario
	treated          domain.Scenario
	marginIncrement  float64
	wasteReduction   float64
	spreadUnits      float64
	discountCost     float64
	treatedMarginPct float64
}

type candidate struct {
	stock    db.Stock
	product  db.Product
	period   domain.Period
	expiring float64 // units of active lots expiring by the end of the period
}

func scenario(units, available, demand, price, cost, expiring float64) domain.Scenario {
	sold := math.Min(units, available)
	waste := math.Max(0, expiring-sold)
	risk := 0.0
	if demand > 0 {
		risk = math.Max(0, math.Min(1, 1-available/(demand*1.2)))
	}
	return domain.Scenario{Units: math.Round(sold), NetRevenue: httpx.Money(sold * price), Margin: httpx.Money(sold * (price - cost)),
		WasteUnits: math.Round(waste), WasteCost: httpx.Money(waste * cost), StockoutRisk: httpx.Round(risk, 2)}
}

// simulate the contextual offer for one candidate and discount over its period.
func simulate(sc *domain.StoreContext, c candidate, disc float64) simulation {
	curve := sc.Curve(c.stock.ProductID)
	start, end := c.period.StartDate, c.period.EndDate
	stock, later := sc.StockAt(c.stock, start, end)
	available := stock + later
	base, spread := 0.0, 0.0
	for d := start; d <= end; d = domain.AddDays(d, 1) {
		base += curve.At(d)
		i := domain.DaysBetween(sc.AsOf, d) - 1
		if i >= 0 && i < len(curve.P80) {
			spread += curve.P80[i] - curve.P50[i]
		}
	}
	price, cost := c.product.RetailPrice, c.product.DefaultSupplier.UnitCost
	net := price * (1 - disc)
	treatedDemand := base * (1 + priceElasticity*disc)
	b := scenario(base, available, base, price, cost, c.expiring)
	t := scenario(treatedDemand, available, treatedDemand, net, cost, c.expiring)
	s := simulation{discount: disc, baseline: b, treated: t,
		marginIncrement: t.Margin - b.Margin, wasteReduction: b.WasteCost - t.WasteCost,
		spreadUnits: spread * (1 + priceElasticity*disc), discountCost: t.Units * price * disc}
	if net > 0 {
		s.treatedMarginPct = (net - cost) / net
	}
	return s
}

// optimize picks the discount (1 % steps up to maxDisc) with the best margin increment + waste reduction that
// satisfies the constraints. ok=false when no discount satisfies them (the best infeasible one is returned).
func optimize(sc *domain.StoreContext, c candidate, maxDisc float64) (simulation, []domain.Violation, bool) {
	var best, fallback *simulation
	for k := 1; float64(k)/100 <= maxDisc+1e-9; k++ {
		s := simulate(sc, c, float64(k)/100)
		if fallback == nil {
			fallback = &s
		}
		if s.treatedMarginPct < minMarginPct || s.discountCost > budgetMXN {
			continue
		}
		if best == nil || s.marginIncrement+s.wasteReduction > best.marginIncrement+best.wasteReduction {
			best = &s
		}
	}
	if best != nil {
		return *best, []domain.Violation{}, true
	}
	if fallback == nil {
		s := simulate(sc, c, maxDisc)
		fallback = &s
	}
	var v []domain.Violation
	if fallback.treatedMarginPct < minMarginPct {
		v = append(v, domain.Violation{Code: "min_margin", Message: "El margen con descuento queda por debajo del mínimo.",
			Limit: minMarginPct, Actual: httpx.Round(fallback.treatedMarginPct, 4)})
	}
	if fallback.discountCost > budgetMXN {
		v = append(v, domain.Violation{Code: "budget", Message: "El costo del descuento excede el presupuesto.",
			Limit: budgetMXN, Actual: httpx.Money(fallback.discountCost)})
	}
	return *fallback, v, false
}

// periodFor: the next weekday asked for inside the forecast horizon, else the next 7 days.
func periodFor(asOf, weekday string) domain.Period {
	if weekday != "" {
		for k := 1; k <= 7; k++ {
			d := domain.AddDays(asOf, k)
			if domain.MustDate(d).Weekday().String() == weekday {
				return domain.Period{StartDate: d, EndDate: d}
			}
		}
	}
	return domain.Period{StartDate: domain.AddDays(asOf, 1), EndDate: domain.AddDays(asOf, 7)}
}

func expiringUnits(ctx context.Context, storeID, productID, until string) (float64, error) {
	var lots []db.Lot
	if err := db.ProductTimeline(ctx, storeID, productID, "LOT#", &lots); err != nil {
		return 0, err
	}
	total := 0.0
	for _, l := range lots {
		if l.Status == "ACTIVE" && l.ExpirationDate <= until {
			total += float64(l.QuantityRemaining)
		}
	}
	return total, nil
}

// buildStrategy runs simulate_strategy over the candidate products and returns the best contextual offer.
func buildStrategy(ctx context.Context, sc *domain.StoreContext, p parsed, candidates []db.Stock, convID string) (*domain.Strategy, error) {
	maxDisc := defaultMaxDiscPct
	if p.MaxDiscountPct != nil {
		maxDisc = *p.MaxDiscountPct
	}
	period := periodFor(sc.AsOf, p.Weekday)

	var bestC *candidate
	var bestS simulation
	var bestV []domain.Violation
	bestOK := false
	for _, s := range candidates {
		prod, ok := sc.Products[s.ProductID]
		if !ok {
			continue
		}
		c := candidate{stock: s, product: prod, period: period}
		sim, v, feasible := optimize(sc, c, maxDisc)
		score := sim.marginIncrement + sim.wasteReduction
		if bestC == nil || (feasible && !bestOK) ||
			(feasible == bestOK && score > bestS.marginIncrement+bestS.wasteReduction) {
			cc := c
			bestC, bestS, bestV, bestOK = &cc, sim, v, feasible
		}
	}
	if bestC == nil {
		return nil, nil
	}
	// re-simulate the winner with its expiring lots to account for waste
	exp, err := expiringUnits(ctx, sc.Store.StoreID, bestC.stock.ProductID, period.EndDate)
	if err != nil {
		return nil, err
	}
	bestC.expiring = exp
	if exp > 0 {
		bestS, bestV, bestOK = optimize(sc, *bestC, maxDisc)
	}

	days, err := db.Calendar(ctx, period.StartDate, period.EndDate)
	if err != nil {
		return nil, err
	}
	drivers := []string{"demand"}
	assumptions := []string{
		fmt.Sprintf("Pronóstico base de %s (promedio de 28 días ajustado por día de la semana y quincena).", sc.Store.StoreID),
		fmt.Sprintf("Elasticidad supuesta: cada 1 %% de descuento eleva %.0f %% las unidades.", priceElasticity),
		fmt.Sprintf("Costo unitario del proveedor por defecto (%s).", bestC.product.DefaultSupplier.SupplierID),
	}
	curve := sc.Curve(bestC.stock.ProductID)
	for _, f := range curve.Items {
		if f.Date < period.StartDate || f.Date > period.EndDate {
			continue
		}
		for _, d := range f.Drivers {
			if d.Type == "EVENT" && !has(drivers, "event") {
				drivers = append(drivers, "event")
				assumptions = append(assumptions, "Quincena en el periodo: el pronóstico incluye su efecto.")
			}
		}
	}
	for _, d := range days {
		if d.WeatherLabel == bestC.product.WeatherSensitivity && !has(drivers, "weather") {
			drivers = append(drivers, "weather")
			assumptions = append(assumptions, fmt.Sprintf("Clima %s esperado; el producto es sensible a ese clima.", d.WeatherLabel))
		}
	}
	if exp > 0 {
		drivers = append(drivers, "shelf_life")
		assumptions = append(assumptions, fmt.Sprintf("%.0f unidades caducan antes del %s.", exp, period.EndDate))
	}

	now := time.Now().UTC().Format(time.RFC3339)
	status := "draft"
	if !bestOK {
		status = "rejected"
	}
	inc := httpx.Money(bestS.marginIncrement)
	unitMargin := bestC.product.RetailPrice*(1-bestS.discount) - bestC.product.DefaultSupplier.UnitCost
	half := bestS.spreadUnits * math.Max(unitMargin, 0)
	when := fmt.Sprintf("del %s al %s", fmtDay(period.StartDate), fmtDay(period.EndDate))
	if period.StartDate == period.EndDate {
		when = "el " + domain.WeekdayES(period.StartDate) + " " + fmtDay(period.StartDate)
	}
	id := auth.NewID("dr_")
	s := &domain.Strategy{
		PK: "DRAFT#" + id, SK: "#META", EntityType: "DRAFT", GSI1PK: "STORE#" + sc.Store.StoreID + "#DRAFTS", GSI1SK: now,
		DraftID: id, StoreID: sc.Store.StoreID, Status: status, StrategyType: "contextual_offer",
		Title:             fmt.Sprintf("Oferta %s: %s -%.0f %%", domain.WeekdayES(period.StartDate), bestC.product.ProductName, bestS.discount*100),
		PromotedProductID: bestC.stock.ProductID, Period: period, ProposedDiscountPct: bestS.discount,
		Constraints: domain.Constraints{MinMarginPct: minMarginPct, MaxDiscountPct: maxDisc, BudgetMXN: budgetMXN,
			Satisfied: bestOK, Violations: bestV},
		Assumptions: assumptions, Baseline: bestS.baseline, Treated: bestS.treated,
		ExpectedUnitsIncrement:  bestS.treated.Units - bestS.baseline.Units,
		ExpectedSalesIncrement:  httpx.Money(bestS.treated.NetRevenue - bestS.baseline.NetRevenue),
		ExpectedMarginIncrement: inc, ExpectedWasteReduction: httpx.Money(bestS.wasteReduction),
		ConfidenceInterval: domain.Interval{Low: httpx.Money(inc - half), High: httpx.Money(inc + half), Level: ciLevel},
		Explanation: fmt.Sprintf("Con %.0f %% de descuento en %s %s, la demanda esperada pasa de %.0f a %.0f unidades y el margen cambia en $%.2f.",
			bestS.discount*100, bestC.product.ProductName, when, bestS.baseline.Units, bestS.treated.Units, inc),
		Drivers: drivers, CreatedAt: now, RequiresApproval: true, ConversationID: convID,
	}
	return s, db.Put(ctx, s, "")
}

func fmtDay(d string) string { return domain.MustDate(d).Format("02/01") }

func has(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// planSummary is optimize_portfolio over the store plan with and without the new draft.
func planSummary(ctx context.Context, storeID string, draft *domain.Strategy) (map[string]any, error) {
	drafts, err := domain.StoreDrafts(ctx, storeID)
	if err != nil {
		return nil, err
	}
	current := 0.0
	overlaps := []map[string]any{}
	for _, d := range drafts {
		if !domain.PlanStatuses[d.Status] {
			continue
		}
		current += d.ExpectedMarginIncrement
		if d.PromotedProductID == draft.PromotedProductID && d.Period.StartDate <= draft.Period.EndDate &&
			d.Period.EndDate >= draft.Period.StartDate {
			overlaps = append(overlaps, map[string]any{"draft_id": d.DraftID, "product_id": d.PromotedProductID,
				"start_date": d.Period.StartDate, "end_date": d.Period.EndDate, "type": "overlapping_product"})
		}
	}
	with := current
	if draft.Status != "rejected" && len(overlaps) == 0 {
		with += draft.ExpectedMarginIncrement
	}
	return map[string]any{"type": "plan_summary", "current_net_expected_margin_increment": httpx.Money(current),
		"with_draft_net_expected_margin_increment": httpx.Money(with), "delta": httpx.Money(with - current),
		"overlaps": overlaps}, nil
}

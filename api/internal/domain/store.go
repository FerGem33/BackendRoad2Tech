package domain

import (
	"context"
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

// Curve is the daily demand forecast of one store x product. Index 0 is AsOf+1.
type Curve struct {
	AsOf     string
	P50      []float64
	P80      []float64
	Baseline float64
	Items    []db.Forecast
}

// At returns the expected demand on date. Beyond the horizon it repeats the weekly pattern of the last 7 days.
func (c Curve) At(date string) float64 {
	if len(c.P50) == 0 {
		return c.Baseline
	}
	i := DaysBetween(c.AsOf, date) - 1
	if i < 0 {
		return c.Baseline
	}
	for i >= len(c.P50) {
		i -= 7
	}
	return c.P50[max(i, 0)]
}

// Sum of demand over (from, from+n].
func (c Curve) Sum(from string, n int) float64 {
	s := 0.0
	for k := 1; k <= n; k++ {
		s += c.At(AddDays(from, k))
	}
	return s
}

// Mean of demand over (from, from+n] (the "next n days" demand used to size orders).
func (c Curve) Mean(from string, n int) float64 { return c.Sum(from, n) / float64(n) }

// StoreContext is everything the restock and policy rules need for one store, read in parallel.
type StoreContext struct {
	Store      *db.Store
	AsOf       string
	Stocks     []db.Stock
	Offers     map[string][]db.Offer
	Products   map[string]db.Product
	Curves     map[string]Curve
	OpenOrders map[string][]db.PurchaseOrder
}

func LoadStoreContext(ctx context.Context, storeID string) (*StoreContext, error) {
	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return nil, err
	}
	if store == nil {
		return nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}
	sc := &StoreContext{Store: store, AsOf: httpx.DataAsOf(), Curves: map[string]Curve{}, OpenOrders: map[string][]db.PurchaseOrder{}}
	var fc map[string][]db.Forecast
	var open []db.PurchaseOrder
	g, gctx := errgroup.WithContext(ctx)
	g.Go(func() (err error) { sc.Stocks, err = db.Stocks(gctx, storeID); return })
	g.Go(func() (err error) { sc.Offers, err = db.AllOffers(gctx); return })
	g.Go(func() (err error) { sc.Products, err = db.ProductMap(gctx); return })
	g.Go(func() (err error) {
		fc, err = db.Forecasts(gctx, storeID, AddDays(sc.AsOf, 1), AddDays(sc.AsOf, ForecastHorizonDays))
		return
	})
	g.Go(func() (err error) { open, err = db.OpenOrders(gctx, storeID); return })
	if err := g.Wait(); err != nil {
		return nil, err
	}
	sort.Slice(sc.Stocks, func(i, j int) bool { return sc.Stocks[i].ProductID < sc.Stocks[j].ProductID })
	for pid, items := range fc {
		c := Curve{AsOf: sc.AsOf, Items: items}
		for _, f := range items {
			c.P50 = append(c.P50, f.P50)
			c.P80 = append(c.P80, f.P80)
			c.Baseline = f.BaselineUnits
		}
		sc.Curves[pid] = c
	}
	for _, o := range open {
		sc.OpenOrders[o.ProductID] = append(sc.OpenOrders[o.ProductID], o)
	}
	return sc, nil
}

func (sc *StoreContext) Curve(pid string) Curve {
	if c, ok := sc.Curves[pid]; ok {
		return c
	}
	return Curve{AsOf: sc.AsOf}
}

// arrivals maps delivery date -> units for the open orders of a product. Late orders arrive tomorrow.
func (sc *StoreContext) arrivals(pid string) (map[string]float64, float64) {
	m := map[string]float64{}
	total := 0.0
	for _, o := range sc.OpenOrders[pid] {
		d := o.ExpectedDeliveryDate
		if d <= sc.AsOf {
			d = AddDays(sc.AsOf, 1)
		}
		m[d] += float64(o.Quantity)
		total += float64(o.Quantity)
	}
	return m, total
}

// projectLost simulates stock from `from` (exclusive) for n days with the given arrivals and returns
// the unmet units and the first day stock runs out ("" if never).
func projectLost(c Curve, stock float64, arrivals map[string]float64, from string, n int) (float64, string) {
	lost, first := 0.0, ""
	for k := 1; k <= n; k++ {
		d := AddDays(from, k)
		stock += arrivals[d]
		stock -= c.At(d)
		if stock < 0 {
			lost += -stock
			stock = 0
			if first == "" {
				first = d
			}
		}
	}
	return lost, first
}

// StockAt projects the stock at the start of `date` (after that day's arrivals, before its sales) with the
// current pipeline only, and returns it with the arrivals expected between date and end (inclusive, after date).
func (sc *StoreContext) StockAt(s db.Stock, date, end string) (stock, laterArrivals float64) {
	arr, _ := sc.arrivals(s.ProductID)
	c := sc.Curve(s.ProductID)
	stock = s.OnHand
	for d := AddDays(sc.AsOf, 1); d <= date; d = AddDays(d, 1) {
		stock += arr[d]
		if d < date {
			stock = math.Max(0, stock-c.At(d))
		}
	}
	for d := AddDays(date, 1); d <= end; d = AddDays(d, 1) {
		laterArrivals += arr[d]
	}
	return stock, laterArrivals
}

type SupplierRef struct {
	SupplierID       string  `json:"supplier_id"`
	SupplierName     string  `json:"supplier_name"`
	Email            string  `json:"email"`
	UnitCost         float64 `json:"unit_cost"`
	MinOrderQty      int     `json:"min_order_qty"`
	LeadTimeDays     int     `json:"lead_time_days"`
	DeliveryDays     string  `json:"delivery_days"`
	ReliabilityScore float64 `json:"reliability_score"`
}

type Alternative struct {
	SupplierID           string  `json:"supplier_id"`
	SupplierName         string  `json:"supplier_name"`
	UnitCost             float64 `json:"unit_cost"`
	MinOrderQty          int     `json:"min_order_qty"`
	LeadTimeDays         int     `json:"lead_time_days"`
	ExpectedDeliveryDate string  `json:"expected_delivery_date"`
	SuggestedQty         int     `json:"suggested_qty"`
	EstimatedCost        float64 `json:"estimated_cost"`
}

type Suggestion struct {
	ID                         string        `json:"id"`
	StoreID                    string        `json:"store_id"`
	ProductID                  string        `json:"product_id"`
	ProductName                string        `json:"product_name"`
	OrderDate                  string        `json:"order_date"`
	ExpectedDeliveryDate       string        `json:"expected_delivery_date"`
	IsUrgent                   bool          `json:"is_urgent"`
	Reasons                    []string      `json:"reasons"`
	SuggestedQty               int           `json:"suggested_qty"`
	EstimatedCost              float64       `json:"estimated_cost"`
	StockAtOrder               int           `json:"stock_at_order"`
	PredictedDailyDemand       float64       `json:"predicted_daily_demand"`
	Supplier                   SupplierRef   `json:"supplier"`
	Alternatives               []Alternative `json:"alternatives"`
	ExpectedLostSalesIfSkipped float64       `json:"expected_lost_sales_if_skipped"`
	ExpectedGainIfOrdered      float64       `json:"expected_gain_if_ordered"`
	ExpectedWasteIfOrdered     float64       `json:"expected_waste_if_ordered"`
}

func SupplierRefFrom(o db.Offer) SupplierRef {
	return SupplierRef{SupplierID: o.SupplierID, SupplierName: o.SupplierName, Email: o.SupplierEmail,
		UnitCost: o.UnitCost, MinOrderQty: o.MinOrderQty, LeadTimeDays: o.LeadTimeDays,
		DeliveryDays: strings.Join(o.DeliveryDays, "|"), ReliabilityScore: o.ReliabilityScore}
}

func SuggestionID(storeID, productID, orderDate string) string {
	return fmt.Sprintf("rs_%s_%s_%s", storeID, productID, strings.ReplaceAll(orderDate, "-", ""))
}

// RestockSuggestions walks every product day by day from AsOf to `to` with the forecast and the open orders,
// and emits an order each evening stock + on_order <= reorder_point (same rule as simulate_operations.py).
// Orders emitted before `from` still enter the pipeline but are not returned.
func (sc *StoreContext) RestockSuggestions(from, to string) []Suggestion {
	out, _ := sc.Plan(from, to)
	return out
}

// Stockout is the first day a product runs out even when the suggested orders are placed.
type Stockout struct {
	ProductID string
	Date      string
	LostUnits float64
}

// Plan runs the restock projection and returns the suggestions emitted between from and to, plus the products
// that still run out (a stockout no order placed on time can prevent).
func (sc *StoreContext) Plan(from, to string) ([]Suggestion, []Stockout) {
	out := []Suggestion{}
	var stockouts []Stockout
	for _, s := range sc.Stocks {
		offers := sc.Offers[s.ProductID]
		if len(offers) == 0 {
			continue
		}
		c := sc.Curve(s.ProductID)
		shelf := s.ShelfLifeDays
		arr, onOrder := sc.arrivals(s.ProductID)
		stock := s.OnHand
		var so *Stockout
		for d := sc.AsOf; d <= to; d = AddDays(d, 1) {
			if d != sc.AsOf {
				a := arr[d]
				stock += a
				onOrder -= a
				if dem := c.At(d); dem > stock+1e-9 {
					if so == nil {
						so = &Stockout{ProductID: s.ProductID, Date: d}
					}
					so.LostUnits += dem - stock
				}
				stock = math.Max(0, stock-c.At(d))
			}
			pred := c.Mean(d, CycleDays)
			if pred < MinDemandToOrder || stock+onOrder > s.ReorderPoint {
				continue
			}
			dec := Decide(offers, s.RetailPrice, shelf, pred, stock)
			lead := dec.Offer.LeadTimeDays
			arrival := AddDays(d, lead)
			if d >= from {
				out = append(out, sc.suggestion(s, c, offers, dec, d, stock, pred, arr))
			}
			arr[arrival] += float64(dec.Quantity)
			onOrder += float64(dec.Quantity)
		}
		// a fraction of a unit short is forecast noise, not a stockout
		if so != nil && so.LostUnits >= 1 {
			stockouts = append(stockouts, *so)
		}
	}
	return out, stockouts
}

func (sc *StoreContext) suggestion(s db.Stock, c Curve, offers []db.Offer, dec Decision, d string, stock, pred float64,
	arr map[string]float64) Suggestion {
	lead := dec.Offer.LeadTimeDays
	window := lead + CycleDaysFor(s.ShelfLifeDays)
	lostBefore, _ := projectLost(c, stock, arr, d, lead)
	lostWindow, _ := projectLost(c, stock, arr, d, window)
	reasons := []string{"below_min"}
	if lostBefore > 0 {
		reasons = append(reasons, "stockout")
	}
	if c.Baseline > 0 && c.Mean(d, window) >= 1.2*c.Baseline {
		reasons = append(reasons, "demand_spike")
	}
	alts := []Alternative{}
	for _, o := range offers {
		if o.SupplierID == dec.Offer.SupplierID {
			continue
		}
		q := OrderQuantity(o, pred, s.ShelfLifeDays)
		alts = append(alts, Alternative{SupplierID: o.SupplierID, SupplierName: o.SupplierName, UnitCost: o.UnitCost,
			MinOrderQty: o.MinOrderQty, LeadTimeDays: o.LeadTimeDays, ExpectedDeliveryDate: AddDays(d, o.LeadTimeDays),
			SuggestedQty: q, EstimatedCost: httpx.Money(float64(q) * o.UnitCost)})
	}
	sort.Slice(alts, func(i, j int) bool { return alts[i].EstimatedCost < alts[j].EstimatedCost })
	return Suggestion{
		ID: SuggestionID(s.StoreID, s.ProductID, d), StoreID: s.StoreID, ProductID: s.ProductID, ProductName: s.ProductName,
		OrderDate: d, ExpectedDeliveryDate: AddDays(d, lead), IsUrgent: dec.IsUrgent, Reasons: reasons,
		SuggestedQty: dec.Quantity, EstimatedCost: httpx.Money(float64(dec.Quantity) * dec.Offer.UnitCost),
		StockAtOrder: int(math.Round(stock)), PredictedDailyDemand: httpx.Round(pred, 2),
		Supplier: SupplierRefFrom(dec.Offer), Alternatives: alts,
		ExpectedLostSalesIfSkipped: httpx.Money(lostWindow * s.RetailPrice),
		ExpectedGainIfOrdered:      httpx.Money(math.Max(0, dec.ExpectedProfit)),
		ExpectedWasteIfOrdered:     httpx.Money(dec.WasteUnits * dec.Offer.UnitCost),
	}
}

var weekdayES = map[string]string{"Monday": "lunes", "Tuesday": "martes", "Wednesday": "miércoles",
	"Thursday": "jueves", "Friday": "viernes", "Saturday": "sábado", "Sunday": "domingo"}

func WeekdayES(date string) string { return weekdayES[MustDate(date).Weekday().String()] }

type Policy struct {
	ReorderPoint int `json:"reorder_point"`
	MaxStock     int `json:"max_stock"`
}

type PolicySuggestion struct {
	ProductID   string   `json:"product_id"`
	Current     Policy   `json:"current"`
	Suggested   Policy   `json:"suggested"`
	Reason      string   `json:"reason"`
	ReasonCodes []string `json:"reason_codes"`
}

// SuggestPolicy: reorder_point = D x (lead + safety), max_stock = max(D x (lead + safety + cycle), reorder_point +
// min_order_qty), with D the mean forecast of the next 14 days and lead time of the supplier select_supplier() picks.
func (sc *StoreContext) SuggestPolicy(s db.Stock) PolicySuggestion {
	c := sc.Curve(s.ProductID)
	cur := Policy{ReorderPoint: int(math.Round(s.ReorderPoint)), MaxStock: int(math.Round(s.MaxStock))}
	offers := sc.Offers[s.ProductID]
	if len(offers) == 0 {
		return PolicySuggestion{ProductID: s.ProductID, Current: cur, Suggested: cur,
			Reason: "Sin ofertas de proveedor; se conserva la política actual.", ReasonCodes: []string{"no_offers"}}
	}
	d := c.Mean(sc.AsOf, ForecastHorizonDays)
	peak, peakVal := "", 0.0
	for k := 1; k <= ForecastHorizonDays; k++ {
		day := AddDays(sc.AsOf, k)
		if v := c.At(day); v > peakVal {
			peak, peakVal = day, v
		}
	}
	dec := Decide(offers, s.RetailPrice, s.ShelfLifeDays, d, s.OnHand)
	lead, safety := dec.Offer.LeadTimeDays, SafetyDaysFor(s.ShelfLifeDays)
	rp := int(math.Ceil(d*float64(lead+safety) - 1e-9))
	mx := max(int(math.Ceil(d*float64(lead+safety+CycleDaysFor(s.ShelfLifeDays))-1e-9)), rp+dec.Offer.MinOrderQty)
	sug := Policy{ReorderPoint: rp, MaxStock: mx}

	codes := []string{"forecast_demand", "lead_time"}
	reason := fmt.Sprintf("Demanda proyectada de %s u/día en los próximos 14 días", fmtUnits(d))
	if peak != "" && c.Baseline > 0 && peakVal >= 1.1*d {
		reason += fmt.Sprintf(" (pico el %s %s)", WeekdayES(peak), MustDate(peak).Format("02/01"))
		codes = append(codes, "demand_peak")
	}
	reason += fmt.Sprintf(" y lead time de %d días con %s", lead, dec.Offer.SupplierName)
	if rp+dec.Offer.MinOrderQty == mx {
		reason += fmt.Sprintf("; el máximo cubre el pedido mínimo de %d", dec.Offer.MinOrderQty)
		codes = append(codes, "min_order_qty")
	}
	if sug == cur {
		codes = append(codes, "unchanged")
	}
	return PolicySuggestion{ProductID: s.ProductID, Current: cur, Suggested: sug, Reason: reason + ".", ReasonCodes: codes}
}

func fmtUnits(v float64) string {
	if v >= 10 {
		return fmt.Sprintf("%.0f", v)
	}
	return strings.Replace(fmt.Sprintf("%.1f", v), ".0", "", 1)
}

// GET /stores/{store_id}/calendar — ENDPOINTS.md §3.13. Layers: deliveries, strategies, alerts, context.
// Alerts are derived on read: stockout_risk (stock projection with the forecast and open orders),
// expiring_soon (active lots) and order_delayed (orders past their expected delivery date).
package main

import (
	"context"
	"sort"
	"strings"

	"github.com/aws/aws-lambda-go/lambda"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/auth"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/domain"
	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/httpx"
	"golang.org/x/sync/errgroup"
)

const maxRangeDays = 62

var allLayers = []string{"deliveries", "strategies", "alerts", "context"}

type event map[string]any

type contextDay struct {
	Date              string  `json:"date"`
	DayOfWeek         string  `json:"day_of_week"`
	IsHoliday         int     `json:"is_holiday"`
	HolidayName       *string `json:"holiday_name"`
	EventName         *string `json:"event_name"`
	EventScope        *string `json:"event_scope"`
	IsPayday          int     `json:"is_payday"`
	IsSchoolVacation  int     `json:"is_school_vacation"`
	IsCollegeVacation int     `json:"is_college_vacation"`
	TempMaxC          float64 `json:"temp_max_c"`
	TempMinC          float64 `json:"temp_min_c"`
	PrecipitationMM   float64 `json:"precipitation_mm"`
	WeatherLabel      string  `json:"weather_label"`
	IsForecast        bool    `json:"is_forecast"`
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func joinOrNil(vals []string) *string {
	if len(vals) == 0 {
		return nil
	}
	s := strings.Join(vals, "|")
	return &s
}

func handle(ctx context.Context, r *httpx.Request) (int, any, error) {
	storeID := httpx.Path(r, "store_id")
	asOf := httpx.DataAsOf()
	from, to := httpx.Query(r, "from"), httpx.Query(r, "to")
	if from == "" {
		from = asOf[:8] + "01"
	}
	if to == "" {
		to = domain.AddDays(domain.AddDays(from[:8]+"01", 32)[:8]+"01", -1) // end of from's month
	}
	if _, err := domain.ParseDate(from); err != nil {
		return 0, nil, httpx.BadRequest("from debe tener formato YYYY-MM-DD.")
	}
	if _, err := domain.ParseDate(to); err != nil {
		return 0, nil, httpx.BadRequest("to debe tener formato YYYY-MM-DD.")
	}
	if to < from || domain.DaysBetween(from, to) > maxRangeDays {
		return 0, nil, httpx.BadRequest("Rango inválido: to >= from y máximo %d días.", maxRangeDays)
	}
	layers := map[string]bool{}
	if v := httpx.Query(r, "layers"); v != "" {
		for _, l := range strings.Split(v, ",") {
			l = strings.TrimSpace(l)
			if l == "" {
				continue
			}
			if !contains(allLayers, l) {
				return 0, nil, httpx.BadRequest("Capa desconocida: %s.", l)
			}
			layers[l] = true
		}
	} else {
		for _, l := range allLayers {
			layers[l] = true
		}
	}

	store, err := db.GetStore(ctx, storeID)
	if err != nil {
		return 0, nil, err
	}
	if store == nil {
		return 0, nil, httpx.NotFound("store_not_found", "La tienda %s no existe.", storeID)
	}

	var (
		orders []db.PurchaseOrder
		lots   []db.Lot
		drafts []domain.Strategy
		days   []db.Day
		sc     *domain.StoreContext
	)
	g, gctx := errgroup.WithContext(ctx)
	if layers["deliveries"] || layers["alerts"] {
		// orders placed up to 60 days before the range can still be delivered or late inside it
		g.Go(func() (err error) {
			orders, err = db.OrdersBetween(gctx, storeID, domain.AddDays(from, -60), to)
			return
		})
	}
	if layers["alerts"] {
		lotFrom := max(from, asOf)
		if lotFrom <= to {
			g.Go(func() (err error) { lots, err = db.LotsExpiringBetween(gctx, storeID, lotFrom, to); return })
		}
		if to > asOf {
			g.Go(func() (err error) { sc, err = domain.LoadStoreContext(gctx, storeID); return })
		}
	}
	if layers["strategies"] {
		g.Go(func() (err error) { drafts, err = domain.StoreDrafts(gctx, storeID); return })
	}
	if layers["context"] {
		g.Go(func() (err error) { days, err = db.Calendar(gctx, from, to); return })
	}
	if err := g.Wait(); err != nil {
		return 0, nil, err
	}

	inRange := func(d string) bool { return d >= from && d <= to }
	events := []event{}

	if layers["deliveries"] {
		for _, o := range orders {
			d := o.ExpectedDeliveryDate
			if o.ActualDeliveryDate != nil {
				d = *o.ActualDeliveryDate
			}
			if !inRange(d) {
				continue
			}
			events = append(events, event{"id": "ev_dl_" + o.OrderID, "date": d, "kind": "delivery", "label_code": "delivery",
				"product_id": o.ProductID, "supplier_id": o.SupplierID, "order_id": o.OrderID, "qty": o.Quantity,
				"delivered": o.ActualDeliveryDate != nil})
		}
	}

	if layers["strategies"] {
		for _, s := range drafts {
			if s.Status == "draft" || s.Status == "rejected" || s.Status == "expired" {
				continue
			}
			if s.Period.EndDate < from || s.Period.StartDate > to {
				continue
			}
			events = append(events, event{"id": "ev_st_" + s.DraftID, "date": max(s.Period.StartDate, from), "kind": "strategy",
				"strategy_id": s.DraftID, "status": s.Status, "strategy_type": s.StrategyType,
				"start_date": s.Period.StartDate, "end_date": s.Period.EndDate})
		}
	}

	if layers["alerts"] {
		for _, o := range orders {
			late, delay := false, 0
			if o.ActualDeliveryDate != nil && *o.ActualDeliveryDate > o.ExpectedDeliveryDate {
				late, delay = true, domain.DaysBetween(o.ExpectedDeliveryDate, *o.ActualDeliveryDate)
			} else if o.ActualDeliveryDate == nil && o.ExpectedDeliveryDate < asOf {
				late, delay = true, domain.DaysBetween(o.ExpectedDeliveryDate, asOf)
			}
			d := domain.AddDays(o.ExpectedDeliveryDate, 1)
			if !late || !inRange(d) {
				continue
			}
			events = append(events, event{"id": "ev_od_" + o.OrderID, "date": d, "kind": "alert", "alert_type": "order_delayed",
				"product_id": o.ProductID, "order_id": o.OrderID, "expected_delivery_date": o.ExpectedDeliveryDate,
				"actual_delivery_date": o.ActualDeliveryDate, "delay_days": delay, "severity": severity(delay >= 3)})
		}
		for _, l := range lots {
			if l.Status != "ACTIVE" || !inRange(l.ExpirationDate) {
				continue
			}
			events = append(events, event{"id": "ev_ex_" + l.LotID, "date": l.ExpirationDate, "kind": "alert",
				"alert_type": "expiring_soon", "product_id": l.ProductID, "lot_id": l.LotID, "qty": l.QuantityRemaining,
				"value_at_risk": httpx.Money(float64(l.QuantityRemaining) * l.UnitCost),
				"severity":      severity(domain.DaysBetween(asOf, l.ExpirationDate) <= 2)})
		}
		if sc != nil {
			// stockouts that remain even when following the restock suggestions
			_, stockouts := sc.Plan(asOf, to)
			for _, so := range stockouts {
				if !inRange(so.Date) {
					continue
				}
				events = append(events, event{"id": "ev_so_" + so.ProductID + "_" + strings.ReplaceAll(so.Date, "-", ""),
					"date": so.Date, "kind": "alert", "alert_type": "stockout_risk", "product_id": so.ProductID,
					"severity":            severity(domain.DaysBetween(asOf, so.Date) <= 3),
					"expected_lost_units": httpx.Round(so.LostUnits, 1)})
			}
		}
	}

	sort.SliceStable(events, func(i, j int) bool {
		if events[i]["date"].(string) != events[j]["date"].(string) {
			return events[i]["date"].(string) < events[j]["date"].(string)
		}
		return events[i]["id"].(string) < events[j]["id"].(string)
	})

	ctxDays := []contextDay{}
	for _, d := range days {
		var names, scopes []string
		for _, e := range d.Events {
			names = append(names, e.EventName)
			if e.EventScope != nil {
				scopes = append(scopes, *e.EventScope)
			}
		}
		ctxDays = append(ctxDays, contextDay{Date: d.Date, DayOfWeek: d.DayOfWeek, IsHoliday: b2i(d.IsHoliday),
			HolidayName: d.HolidayName, EventName: joinOrNil(names), EventScope: joinOrNil(scopes),
			IsPayday: b2i(d.IsPayday), IsSchoolVacation: b2i(d.IsSchoolVacation), IsCollegeVacation: b2i(d.IsCollegeVacation),
			TempMaxC: d.TempMaxC, TempMinC: d.TempMinC, PrecipitationMM: d.PrecipitationMM, WeatherLabel: d.WeatherLabel,
			IsForecast: d.IsForecast})
	}

	return 200, map[string]any{"store_id": storeID, "from": from, "to": to, "events": events, "context": ctxDays}, nil
}

func severity(high bool) string {
	if high {
		return "high"
	}
	return "medium"
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

func main() { lambda.Start(httpx.Wrap(handle, auth.Required)) }

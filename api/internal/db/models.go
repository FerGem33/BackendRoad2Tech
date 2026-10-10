package db

import (
	"context"
	"sort"
	"sync"

	"golang.org/x/sync/errgroup"
)

type Store struct {
	StoreID           string  `dynamodbav:"store_id" json:"store_id"`
	StoreName         string  `dynamodbav:"store_name" json:"store_name"`
	Colonia           string  `dynamodbav:"colonia" json:"colonia"`
	Latitude          float64 `dynamodbav:"latitude" json:"latitude"`
	Longitude         float64 `dynamodbav:"longitude" json:"longitude"`
	ZoneType          string  `dynamodbav:"zone_type" json:"zone_type"`
	SecondaryZoneType *string `dynamodbav:"secondary_zone_type" json:"secondary_zone_type"`
	SizeCategory      string  `dynamodbav:"size_category" json:"size_category"`
	OpeningHours      string  `dynamodbav:"opening_hours" json:"opening_hours"`
	Is24h             bool    `dynamodbav:"is_24h" json:"is_24h"`
	OpenedDate        string  `dynamodbav:"opened_date" json:"opened_date"`
}

type DefaultSupplier struct {
	SupplierID  string  `dynamodbav:"supplier_id" json:"supplier_id"`
	UnitCost    float64 `dynamodbav:"unit_cost" json:"unit_cost"`
	MinOrderQty int     `dynamodbav:"min_order_qty" json:"min_order_qty"`
}

type Product struct {
	ProductID          string          `dynamodbav:"product_id"`
	ProductName        string          `dynamodbav:"product_name"`
	CategoryID         string          `dynamodbav:"category_id"`
	CategoryName       string          `dynamodbav:"category_name"`
	Brand              string          `dynamodbav:"brand"`
	Unit               string          `dynamodbav:"unit"`
	ShelfLifeDays      int             `dynamodbav:"shelf_life_days"`
	RetailPrice        float64         `dynamodbav:"retail_price"`
	IsSeasonal         bool            `dynamodbav:"is_seasonal"`
	WeatherSensitivity string          `dynamodbav:"weather_sensitivity"`
	BaseDailyDemand    float64         `dynamodbav:"base_daily_demand"`
	DefaultSupplier    DefaultSupplier `dynamodbav:"default_supplier"`
}

type Supplier struct {
	SupplierID       string   `dynamodbav:"supplier_id"`
	SupplierName     string   `dynamodbav:"supplier_name"`
	Email            string   `dynamodbav:"email"`
	CategoriesServed []string `dynamodbav:"categories_served"`
	City             string   `dynamodbav:"city"`
	ReliabilityScore float64  `dynamodbav:"reliability_score"`
	OffersCount      int      `dynamodbav:"offers_count"`
}

type Offer struct {
	SupplierID       string   `dynamodbav:"supplier_id"`
	ProductID        string   `dynamodbav:"product_id"`
	UnitCost         float64  `dynamodbav:"unit_cost"`
	MinOrderQty      int      `dynamodbav:"min_order_qty"`
	LeadTimeDays     int      `dynamodbav:"lead_time_days"`
	DeliveryDays     []string `dynamodbav:"delivery_days"`
	SupplierName     string   `dynamodbav:"supplier_name"`
	SupplierEmail    string   `dynamodbav:"supplier_email"`
	ReliabilityScore float64  `dynamodbav:"reliability_score"`
	ProductName      string   `dynamodbav:"product_name"`
	CategoryID       string   `dynamodbav:"category_id"`
	RetailPrice      float64  `dynamodbav:"retail_price"`
}

type Stock struct {
	StoreID              string   `dynamodbav:"store_id"`
	ProductID            string   `dynamodbav:"product_id"`
	ProductName          string   `dynamodbav:"product_name"`
	CategoryID           string   `dynamodbav:"category_id"`
	RetailPrice          float64  `dynamodbav:"retail_price"`
	ShelfLifeDays        int      `dynamodbav:"shelf_life_days"`
	OnHand               float64  `dynamodbav:"on_hand"`
	OnOrder              float64  `dynamodbav:"on_order"`
	OpenOrderIDs         []string `dynamodbav:"open_order_ids"`
	ReorderPoint         float64  `dynamodbav:"reorder_point"`
	MaxStock             float64  `dynamodbav:"max_stock"`
	PredictedDailyDemand float64  `dynamodbav:"predicted_daily_demand"`
	NextExpirationDate   *string  `dynamodbav:"next_expiration_date"`
	LostDemand7d         int      `dynamodbav:"lost_demand_7d"`
	UpdatedAt            string   `dynamodbav:"updated_at"`
}

type Lot struct {
	LotID             string  `dynamodbav:"lot_id"`
	StoreID           string  `dynamodbav:"store_id"`
	ProductID         string  `dynamodbav:"product_id"`
	ProductName       string  `dynamodbav:"product_name"`
	ExpirationDate    string  `dynamodbav:"expiration_date"`
	QuantityRemaining int     `dynamodbav:"quantity_remaining"`
	UnitCost          float64 `dynamodbav:"unit_cost"`
	Status            string  `dynamodbav:"status"`
}

type PurchaseOrder struct {
	PK                   string  `dynamodbav:"PK"`
	SK                   string  `dynamodbav:"SK"`
	EntityType           string  `dynamodbav:"entity_type"`
	GSI1PK               string  `dynamodbav:"GSI1PK"`
	GSI1SK               string  `dynamodbav:"GSI1SK"`
	GSI2PK               string  `dynamodbav:"GSI2PK,omitempty"`
	GSI2SK               string  `dynamodbav:"GSI2SK,omitempty"`
	OrderID              string  `dynamodbav:"order_id"`
	StoreID              string  `dynamodbav:"store_id"`
	ProductID            string  `dynamodbav:"product_id"`
	ProductName          string  `dynamodbav:"product_name"`
	SupplierID           string  `dynamodbav:"supplier_id"`
	SupplierName         string  `dynamodbav:"supplier_name"`
	SupplierEmail        string  `dynamodbav:"supplier_email"`
	OrderDate            string  `dynamodbav:"order_date"`
	Quantity             int     `dynamodbav:"quantity"`
	UnitCost             float64 `dynamodbav:"unit_cost"`
	TotalCost            float64 `dynamodbav:"total_cost"`
	LeadTimeDays         int     `dynamodbav:"lead_time_days"`
	ExpectedDeliveryDate string  `dynamodbav:"expected_delivery_date"`
	ActualDeliveryDate   *string `dynamodbav:"actual_delivery_date"`
	IsUrgent             bool    `dynamodbav:"is_urgent"`
	PredictedDailyDemand float64 `dynamodbav:"predicted_daily_demand"`
	StockAtOrder         int     `dynamodbav:"stock_at_order"`
	Status               string  `dynamodbav:"status"`
	SuggestionID         string  `dynamodbav:"suggestion_id,omitempty"`
	SentAt               string  `dynamodbav:"sent_at,omitempty"`
}

type Driver struct {
	Type   string  `dynamodbav:"type"`
	Name   string  `dynamodbav:"name"`
	Detail *string `dynamodbav:"detail"`
}

type Forecast struct {
	Date          string   `dynamodbav:"date"`
	ProductID     string   `dynamodbav:"product_id"`
	CategoryID    string   `dynamodbav:"category_id"`
	P50           float64  `dynamodbav:"p50"`
	P80           float64  `dynamodbav:"p80"`
	P90           float64  `dynamodbav:"p90"`
	BaselineUnits float64  `dynamodbav:"baseline_units"`
	UpliftPct     float64  `dynamodbav:"uplift_pct"`
	Drivers       []Driver `dynamodbav:"drivers"`
}

type Sale struct {
	Date      string  `dynamodbav:"date"`
	ProductID string  `dynamodbav:"product_id"`
	UnitsSold int     `dynamodbav:"units_sold"`
	Revenue   float64 `dynamodbav:"revenue"`
}

type DayEvent struct {
	EventName  string  `dynamodbav:"event_name"`
	EventScope *string `dynamodbav:"event_scope"`
}

type Day struct {
	Date              string     `dynamodbav:"date"`
	DayOfWeek         string     `dynamodbav:"day_of_week"`
	IsHoliday         bool       `dynamodbav:"is_holiday"`
	HolidayName       *string    `dynamodbav:"holiday_name"`
	Events            []DayEvent `dynamodbav:"events"`
	IsPayday          bool       `dynamodbav:"is_payday"`
	IsSchoolVacation  bool       `dynamodbav:"is_school_vacation"`
	IsCollegeVacation bool       `dynamodbav:"is_college_vacation"`
	TempMaxC          float64    `dynamodbav:"temp_max_c"`
	TempMinC          float64    `dynamodbav:"temp_min_c"`
	PrecipitationMM   float64    `dynamodbav:"precipitation_mm"`
	WeatherLabel      string     `dynamodbav:"weather_label"`
	IsForecast        bool       `dynamodbav:"is_forecast"`
}

type Category struct {
	CategoryID            string `dynamodbav:"category_id"`
	CategoryName          string `dynamodbav:"category_name"`
	IsPerishable          bool   `dynamodbav:"is_perishable"`
	TypicalShelfLifeDays  int    `dynamodbav:"typical_shelf_life_days"`
	RequiresRefrigeration bool   `dynamodbav:"requires_refrigeration"`
}

type Zone struct {
	ZoneType    string `dynamodbav:"zone_type"`
	Description string `dynamodbav:"description"`
}

// SaleDay is the store x day sales summary (PK=STORE#<id>#SALES, SK=DAY#<date>).
type SaleDay struct {
	Date          string             `dynamodbav:"date"`
	StoreID       string             `dynamodbav:"store_id"`
	UnitsSold     int                `dynamodbav:"units_sold"`
	Revenue       float64            `dynamodbav:"revenue"`
	RevenueByCat  map[string]float64 `dynamodbav:"revenue_by_category"`
	ZeroSaleProds int                `dynamodbav:"zero_sale_products"`
}

// TicketDay is the store x day ticket aggregate (PK=STORE#<id>#SALES, SK=TICKET#<date>).
type TicketDay struct {
	Date      string  `dynamodbav:"date"`
	StoreID   string  `dynamodbav:"store_id"`
	Tickets   int     `dynamodbav:"tickets"`
	Units     int     `dynamodbav:"units"`
	NetAmount float64 `dynamodbav:"net_amount"`
}

// --- catalog reads (cached for the life of the Lambda container; the catalog is read-only) ---

var (
	catMu     sync.Mutex
	products  []Product
	suppliers []Supplier
	offers    map[string][]Offer
)

func Products(ctx context.Context) ([]Product, error) {
	catMu.Lock()
	defer catMu.Unlock()
	if products != nil {
		return products, nil
	}
	var out []Product
	if err := QueryAll(ctx, Q{Index: "GSI1", PK: "PRODUCT"}, &out); err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ProductID < out[j].ProductID })
	products = out
	return out, nil
}

func ProductMap(ctx context.Context) (map[string]Product, error) {
	ps, err := Products(ctx)
	if err != nil {
		return nil, err
	}
	m := make(map[string]Product, len(ps))
	for _, p := range ps {
		m[p.ProductID] = p
	}
	return m, nil
}

func Suppliers(ctx context.Context) ([]Supplier, error) {
	catMu.Lock()
	defer catMu.Unlock()
	if suppliers != nil {
		return suppliers, nil
	}
	var out []Supplier
	if err := QueryAll(ctx, Q{Index: "GSI1", PK: "SUPPLIER"}, &out); err != nil {
		return nil, err
	}
	suppliers = out
	return out, nil
}

func SupplierOffers(ctx context.Context, supplierID string) ([]Offer, error) {
	var out []Offer
	err := QueryAll(ctx, Q{Index: "GSI1", PK: "SUPPLIER#" + supplierID, Begins: "OFFER#"}, &out)
	return out, err
}

// AllOffers returns every supplier offer grouped by product_id.
func AllOffers(ctx context.Context) (map[string][]Offer, error) {
	sups, err := Suppliers(ctx)
	if err != nil {
		return nil, err
	}
	catMu.Lock()
	cached := offers
	catMu.Unlock()
	if cached != nil {
		return cached, nil
	}
	lists := make([][]Offer, len(sups))
	g, gctx := errgroup.WithContext(ctx)
	for i, s := range sups {
		g.Go(func() error {
			o, err := SupplierOffers(gctx, s.SupplierID)
			lists[i] = o
			return err
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	m := map[string][]Offer{}
	for _, l := range lists {
		for _, o := range l {
			m[o.ProductID] = append(m[o.ProductID], o)
		}
	}
	for k := range m {
		sort.Slice(m[k], func(i, j int) bool { return m[k][i].SupplierID < m[k][j].SupplierID })
	}
	catMu.Lock()
	offers = m
	catMu.Unlock()
	return m, nil
}

func GetStore(ctx context.Context, storeID string) (*Store, error) {
	var s Store
	ok, err := Get(ctx, "STORE#"+storeID, "#META", &s)
	if err != nil || !ok {
		return nil, err
	}
	return &s, nil
}

func Stocks(ctx context.Context, storeID string) ([]Stock, error) {
	var out []Stock
	err := QueryAll(ctx, Q{PK: "STORE#" + storeID, Begins: "STOCK#"}, &out)
	return out, err
}

// Forecasts returns the store forecast between from and to (inclusive), grouped by product and ordered by date.
func Forecasts(ctx context.Context, storeID, from, to string) (map[string][]Forecast, error) {
	var out []Forecast
	if err := QueryAll(ctx, Q{PK: "STORE#" + storeID + "#FCST", From: from, To: to + "~"}, &out); err != nil {
		return nil, err
	}
	m := map[string][]Forecast{}
	for _, f := range out {
		m[f.ProductID] = append(m[f.ProductID], f)
	}
	return m, nil
}

// OrdersBetween returns the store purchase orders placed between from and to (inclusive).
func OrdersBetween(ctx context.Context, storeID, from, to string) ([]PurchaseOrder, error) {
	var out []PurchaseOrder
	err := QueryAll(ctx, Q{PK: "STORE#" + storeID, From: "PO#" + from, To: "PO#" + to + "~"}, &out)
	return out, err
}

// OpenOrders returns the store orders not yet delivered.
func OpenOrders(ctx context.Context, storeID string) ([]PurchaseOrder, error) {
	all, err := OrdersBetween(ctx, storeID, "0000", "9999")
	if err != nil {
		return nil, err
	}
	var out []PurchaseOrder
	for _, o := range all {
		if o.ActualDeliveryDate == nil {
			out = append(out, o)
		}
	}
	return out, nil
}

// LotsExpiringBetween returns active lots of the store whose expiration_date is in [from, to].
func LotsExpiringBetween(ctx context.Context, storeID, from, to string) ([]Lot, error) {
	var out []Lot
	err := QueryAll(ctx, Q{PK: "STORE#" + storeID, From: "LOT#" + from, To: "LOT#" + to + "~"}, &out)
	return out, err
}

func Calendar(ctx context.Context, from, to string) ([]Day, error) {
	var out []Day
	err := QueryAll(ctx, Q{PK: "CALENDAR", From: "DATE#" + from, To: "DATE#" + to}, &out)
	return out, err
}

func Categories(ctx context.Context) ([]Category, error) {
	var out []Category
	err := QueryAll(ctx, Q{PK: "CATALOG", Begins: "CATEGORY#"}, &out)
	return out, err
}

func Zones(ctx context.Context) ([]Zone, error) {
	var out []Zone
	err := QueryAll(ctx, Q{PK: "CATALOG", Begins: "ZONE#"}, &out)
	return out, err
}

// Stores returns the 25 stores (GSI1 PK=STORE), sorted by store_id.
func Stores(ctx context.Context) ([]Store, error) {
	var out []Store
	if err := QueryAll(ctx, Q{Index: "GSI1", PK: "STORE"}, &out); err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].StoreID < out[j].StoreID })
	return out, nil
}

// StoreSales returns the store SALE lines (PK=STORE#<id>#SALES, SK=LINE#<date>#<product>) in [from, to].
func StoreSales(ctx context.Context, storeID, from, to string) ([]Sale, error) {
	var out []Sale
	err := QueryAll(ctx, Q{PK: "STORE#" + storeID + "#SALES", From: "LINE#" + from, To: "LINE#" + to + "~"}, &out)
	return out, err
}

// StoreDaySales returns the store x day summaries (SK=DAY#<date>) in [from, to].
func StoreDaySales(ctx context.Context, storeID, from, to string) ([]SaleDay, error) {
	var out []SaleDay
	err := QueryAll(ctx, Q{PK: "STORE#" + storeID + "#SALES", From: "DAY#" + from, To: "DAY#" + to + "~"}, &out)
	return out, err
}

// StoreTicketDays returns the store x day ticket aggregates (SK=TICKET#<date>) in [from, to].
func StoreTicketDays(ctx context.Context, storeID, from, to string) ([]TicketDay, error) {
	var out []TicketDay
	err := QueryAll(ctx, Q{PK: "STORE#" + storeID + "#SALES", From: "TICKET#" + from, To: "TICKET#" + to + "~"}, &out)
	return out, err
}

// ProductTimeline reads one store x product timeline from GSI2 (prefix SALE#, FCST#, LOT#).
func ProductTimeline(ctx context.Context, storeID, productID, prefix string, out any) error {
	return QueryAll(ctx, Q{Index: "GSI2", PK: "STORE#" + storeID + "#PRODUCT#" + productID, Begins: prefix}, out)
}

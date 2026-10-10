package domain

import (
	"context"

	"github.com/fernandovmedina/BackendRoad2Tech/api/internal/db"
)

// Strategy is the shape of ENDPOINTS.md §4.1. Stored as PK=DRAFT#<draft_id>, SK=#META,
// GSI1PK=STORE#<store_id>#DRAFTS, GSI1SK=<created_at> (lists the drafts of a store).
type Strategy struct {
	PK         string `dynamodbav:"PK" json:"-"`
	SK         string `dynamodbav:"SK" json:"-"`
	EntityType string `dynamodbav:"entity_type" json:"-"`
	GSI1PK     string `dynamodbav:"GSI1PK" json:"-"`
	GSI1SK     string `dynamodbav:"GSI1SK" json:"-"`

	DraftID                 string          `dynamodbav:"draft_id" json:"draft_id"`
	StoreID                 string          `dynamodbav:"store_id" json:"store_id"`
	Status                  string          `dynamodbav:"status" json:"status"`
	StrategyType            string          `dynamodbav:"strategy_type" json:"strategy_type"`
	Title                   string          `dynamodbav:"title" json:"title"`
	AnchorProductID         *string         `dynamodbav:"anchor_product_id" json:"anchor_product_id"`
	PromotedProductID       string          `dynamodbav:"promoted_product_id" json:"promoted_product_id"`
	Period                  Period          `dynamodbav:"period" json:"period"`
	ProposedDiscountPct     float64         `dynamodbav:"proposed_discount_pct" json:"proposed_discount_pct"`
	Constraints             Constraints     `dynamodbav:"constraints" json:"constraints"`
	Assumptions             []string        `dynamodbav:"assumptions" json:"assumptions"`
	Baseline                Scenario        `dynamodbav:"baseline" json:"baseline"`
	Treated                 Scenario        `dynamodbav:"treated" json:"treated"`
	ExpectedUnitsIncrement  float64         `dynamodbav:"expected_units_increment" json:"expected_units_increment"`
	ExpectedSalesIncrement  float64         `dynamodbav:"expected_sales_increment" json:"expected_sales_increment"`
	ExpectedMarginIncrement float64         `dynamodbav:"expected_margin_increment" json:"expected_margin_increment"`
	ExpectedWasteReduction  float64         `dynamodbav:"expected_waste_reduction" json:"expected_waste_reduction"`
	ConfidenceInterval      Interval        `dynamodbav:"confidence_interval" json:"confidence_interval"`
	Explanation             string          `dynamodbav:"explanation" json:"explanation"`
	Drivers                 []string        `dynamodbav:"drivers" json:"drivers"`
	CreatedAt               string          `dynamodbav:"created_at" json:"created_at"`
	ApprovedAt              *string         `dynamodbav:"approved_at" json:"approved_at"`
	Measurement             *map[string]any `dynamodbav:"measurement" json:"measurement"`
	RequiresApproval        bool            `dynamodbav:"requires_approval" json:"requires_approval"`
	ConversationID          string          `dynamodbav:"conversation_id,omitempty" json:"-"`
}

type Period struct {
	StartDate string `dynamodbav:"start_date" json:"start_date"`
	EndDate   string `dynamodbav:"end_date" json:"end_date"`
}

type Violation struct {
	Code    string  `dynamodbav:"code" json:"code"`
	Message string  `dynamodbav:"message" json:"message"`
	Limit   float64 `dynamodbav:"limit" json:"limit"`
	Actual  float64 `dynamodbav:"actual" json:"actual"`
}

type Constraints struct {
	MinMarginPct   float64     `dynamodbav:"min_margin_pct" json:"min_margin_pct"`
	MaxDiscountPct float64     `dynamodbav:"max_discount_pct" json:"max_discount_pct"`
	BudgetMXN      float64     `dynamodbav:"budget_mxn" json:"budget_mxn"`
	Satisfied      bool        `dynamodbav:"satisfied" json:"satisfied"`
	Violations     []Violation `dynamodbav:"violations" json:"violations"`
}

type Scenario struct {
	Units        float64 `dynamodbav:"units" json:"units"`
	NetRevenue   float64 `dynamodbav:"net_revenue" json:"net_revenue"`
	Margin       float64 `dynamodbav:"margin" json:"margin"`
	WasteUnits   float64 `dynamodbav:"waste_units" json:"waste_units"`
	WasteCost    float64 `dynamodbav:"waste_cost" json:"waste_cost"`
	StockoutRisk float64 `dynamodbav:"stockout_risk" json:"stockout_risk"`
}

type Interval struct {
	Low   float64 `dynamodbav:"low" json:"low"`
	High  float64 `dynamodbav:"high" json:"high"`
	Level float64 `dynamodbav:"level" json:"level"`
}

// PlanStatuses are the states that count in the store plan / portfolio.
var PlanStatuses = map[string]bool{"selected": true, "approved": true, "scheduled": true, "active": true}

func StoreDrafts(ctx context.Context, storeID string) ([]Strategy, error) {
	var out []Strategy
	err := db.QueryAll(ctx, db.Q{Index: "GSI1", PK: "STORE#" + storeID + "#DRAFTS"}, &out)
	return out, err
}

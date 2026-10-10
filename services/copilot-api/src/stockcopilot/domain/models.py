"""Public response contracts (JSON). The frontend mirrors these in `types/copilot.ts`.

Every number in these models is produced by deterministic code. Only `ai_summary` may be written
by the LLM, and it is generated from the already-computed fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high"]
Trend = Literal["increasing", "stable", "decreasing"]
StrategyType = Literal["contextual_discount", "rescue_combo", "affinity_combo"]
Objective = Literal["increase_sales", "reduce_waste", "maximize_margin"]
DraftStatus = Literal["draft", "selected", "approved", "rejected"]
RestockReason = Literal["stock_limit", "demand", "season"]


class StoreRef(BaseModel):
    store_id: str
    store_name: str
    colonia: str
    zone_type: str
    secondary_zone_type: str | None = None
    size_category: str
    latitude: float
    longitude: float


class ProductRef(BaseModel):
    product_id: str
    product_name: str
    category_id: str
    category_name: str
    brand: str
    unit: str
    retail_price: float
    shelf_life_days: int
    is_perishable: bool
    weather_sensitivity: str


class SupplierRef(BaseModel):
    supplier_id: str
    supplier_name: str
    email: str
    city: str
    reliability_score: float


class SeriesPoint(BaseModel):
    date: date
    value: float


class ForecastPoint(BaseModel):
    date: date
    p50: float
    p80: float
    p90: float


class DriverOut(BaseModel):
    kind: str
    name: str
    impact_pct: float
    detail: str


class StockLotOut(BaseModel):
    expiration_date: date
    quantity: float


class InventoryOut(BaseModel):
    on_hand: int
    in_transit: int
    next_delivery_date: date | None
    lots: list[StockLotOut]


class StockoutRisk(BaseModel):
    level: RiskLevel
    score: float = Field(ge=0, le=1)
    days_of_cover: float | None
    first_stockout_date: date | None
    expected_lost_units: float
    detail: str


class WasteRisk(BaseModel):
    level: RiskLevel
    score: float = Field(ge=0, le=1)
    expiring_units: float
    expected_waste_units: float
    expected_waste_value: float
    detail: str


class ModelInfo(BaseModel):
    name: str
    backtest_wape_14d: float | None = Field(description="Weighted absolute % error on the last 14 days")
    source: str = "deterministic baseline"


class ProductTendency(BaseModel):
    kind: Literal["product_tendency"] = "product_tendency"
    store: StoreRef
    product: ProductRef
    data_as_of: date
    history_days: int
    forecast_days: int
    historical_series: list[SeriesPoint]
    forecast_series: list[ForecastPoint]
    trend: Trend
    trend_strength: float = Field(ge=0, le=1)
    trend_pct_per_week: float
    recent_avg_daily_units: float
    forecast_avg_daily_units: float
    forecast_vs_recent_pct: float
    main_drivers: list[DriverOut]
    stockout_risk: StockoutRisk
    waste_risk: WasteRisk
    inventory: InventoryOut
    model: ModelInfo
    ai_summary: str


class ScenarioPoint(BaseModel):
    date: date
    units: float
    revenue: float
    waste_units: float
    in_promotion: bool


class ConfidenceInterval(BaseModel):
    low: float
    high: float
    metric: str = "expected_net_impact"
    basis: str


class StrategyConstraints(BaseModel):
    max_discount_pct: float
    min_unit_margin_pct: float
    budget_mxn: float = Field(0.0, description="Margin the manager accepts to invest (increase_sales only)")
    period_days: int
    max_stockout_risk: RiskLevel


class StrategyCandidateOut(BaseModel):
    candidate_id: str
    strategy_type: StrategyType
    title: str
    discount_pct: float
    cross_sell_product: ProductRef | None
    expected_units_increment: float
    expected_sales_increment: float
    expected_margin_increment: float
    expected_waste_reduction: float
    expected_net_impact: float
    stockout_risk: RiskLevel
    viable: bool
    violations: list[str]


class ProductSellStrategy(BaseModel):
    kind: Literal["product_sell_strategy"] = "product_sell_strategy"
    store: StoreRef
    product: ProductRef
    data_as_of: date
    objective: Objective
    recommended_action: str
    strategy_type: StrategyType | None
    discount_pct: float
    cross_sell_product: ProductRef | None
    cross_sell_basis: str | None
    valid_from: date
    valid_to: date
    baseline_series: list[ScenarioPoint]
    strategy_series: list[ScenarioPoint]
    expected_units_increment: float
    expected_sales_increment: float
    expected_margin_increment: float
    expected_waste_reduction: float
    expected_waste_reduction_value: float
    expected_net_impact: float
    stockout_risk: RiskLevel
    confidence_interval: ConfidenceInterval
    constraints: StrategyConstraints
    assumptions: list[str]
    candidates: list[StrategyCandidateOut]
    draft_id: str | None
    draft_status: DraftStatus | None
    requires_approval: bool = True
    expected_anchor_units_increment: float = 0.0
    uplift_source: Literal["campaigns", "assumed"] = "assumed"
    disclaimer: str = "Simulated estimate on synthetic data, not a guaranteed result."


class SupplierEmail(BaseModel):
    to: str
    subject: str
    body: str


class RestockSuggestionOut(BaseModel):
    id: str
    store_id: str
    product: ProductRef
    supplier: SupplierRef
    suggested_qty: int
    order_date: date
    expected_arrival_date: date
    lead_time_days: int
    unit_cost: float
    order_cost: float
    is_urgent: bool
    on_hand: int
    in_transit: int
    predicted_daily_demand: float
    days_of_cover: float | None
    reorder_point: float
    reasons: list[RestockReason]
    reason_details: list[str]
    status: Literal["pending", "approved", "dismissed"] = "pending"
    email_draft: SupplierEmail


class RestockPlan(BaseModel):
    kind: Literal["restock_plan"] = "restock_plan"
    store: StoreRef
    data_as_of: date
    checked_products: int
    suggestions: list[RestockSuggestionOut]
    notes: list[str] = []


class MoverOut(BaseModel):
    product: ProductRef
    recent_units: float
    previous_units: float
    change_pct: float


class AlertOut(BaseModel):
    kind: Literal["run_out", "expiration", "demand_spike", "no_demand"]
    severity: RiskLevel
    product_id: str
    product_name: str
    date: date | None
    message: str


class StoreKpis(BaseModel):
    total_sales: float
    total_sales_previous: float
    sales_change_pct: float
    units_sold: int
    run_outs: int
    no_demand: int
    waste_risk_products: int


class StoreOverview(BaseModel):
    kind: Literal["store_overview"] = "store_overview"
    store: StoreRef
    data_as_of: date
    period_from: date
    period_to: date
    kpis: StoreKpis
    daily_sales: list[SeriesPoint]
    top_movers_up: list[MoverOut]
    top_movers_down: list[MoverOut]
    alerts: list[AlertOut]


class StoreDemandOut(BaseModel):
    store: StoreRef
    recent_units: float
    previous_units: float
    change_pct: float
    forecast_next_7d_units: float


class DemandByStore(BaseModel):
    kind: Literal["demand_by_store"] = "demand_by_store"
    product: ProductRef
    data_as_of: date
    days: int
    network_recent_units: float
    stores: list[StoreDemandOut]


class DraftEvent(BaseModel):
    status: DraftStatus
    at: datetime
    actor: str
    note: str | None = None


class PromotionDraft(BaseModel):
    draft_id: str
    status: DraftStatus
    store_id: str
    product_id: str
    title: str
    valid_from: date
    valid_to: date
    expected_net_impact: float
    requires_approval: bool = True
    created_at: datetime
    updated_at: datetime
    history: list[DraftEvent]
    strategy: ProductSellStrategy


class Portfolio(BaseModel):
    kind: Literal["portfolio"] = "portfolio"
    store: StoreRef
    data_as_of: date
    period_from: date
    period_to: date
    strategies: list[ProductSellStrategy]
    net_expected_margin_increment: float
    net_expected_impact: float
    conflicts: list[str]
    skipped: list[str]

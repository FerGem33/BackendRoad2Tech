"""`get_product_sell_strategy`: a deterministic LangGraph pipeline behind one facade.

    get_current_store_context -> get_demand_forecast -> get_historical_insights
    -> get_business_constraints -> generate_strategy_candidates -> simulate_strategy
    -> validate_strategy -> select_strategy -> create_promotion_draft

No node calls the LLM. The copilot agent only chooses the parameters (store, product,
objective, max discount, period) and explains the returned numbers.
"""

from __future__ import annotations

import contextvars
from datetime import date, timedelta
from typing import Any, TypedDict

import numpy as np
from langgraph.graph import END, START, StateGraph

from ...data.repository import Affinity
from ..drafts import DraftStore
from ..models import (
    ConfidenceInterval,
    Objective,
    ProductSellStrategy,
    RiskLevel,
    ScenarioPoint,
    StrategyCandidateOut,
    StrategyConstraints,
)
from ..restock import reorder_policy
from ..risk import waste_risk
from ..services import Services
from .candidates import (
    ATTACH_BASE,
    ATTACH_PER_POINT,
    COMBO_CANNIBALIZATION,
    COMPLEMENTS,
    Candidate,
    elasticity,
    generate_candidates,
)
from .simulator import POST_PROMO_DAYS, SimulationContext, SimulationResult, simulate
from .validator import ActivePromotion, validate

ACTIVE_STATUSES = {"selected", "approved"}


class StrategyRequest(TypedDict, total=False):
    store: str
    product: str
    objective: Objective
    max_discount_pct: float
    min_unit_margin_pct: float
    budget_mxn: float
    period_days: int
    start_date: date | None
    exclude_products: list[str]
    create_draft: bool


class StrategyState(TypedDict, total=False):
    request: StrategyRequest
    # context
    store_id: str
    product_id: str
    price: float
    unit_cost: float
    valid_from: date
    valid_to: date
    promo_offset: int
    # forecast + insights
    p50: np.ndarray
    p80: np.ndarray
    has_waste_risk: bool
    insights: list[str]
    # constraints
    constraints: StrategyConstraints
    active: list[ActivePromotion]
    # candidates
    candidates: list[Candidate]
    affinity_anchor: tuple[str, str] | None
    rescue_anchor: tuple[str, str] | None
    observed_partner: Affinity | None
    contexts: dict[str, SimulationContext]
    results: list[SimulationResult]
    violations: dict[str, list[str]]
    chosen: SimulationResult | None
    output: ProductSellStrategy


def _top_seller(
    svc: Services, store_id: str, product_id: str, categories: tuple[str, ...], exclude: set[str]
) -> tuple[str, str] | None:
    history = svc.history(store_id)
    best: tuple[float, str] | None = None
    for i, pid in enumerate(history.product_ids):
        if pid == product_id or pid in exclude or svc.catalog.products[pid].category_id not in categories:
            continue
        units = float(history.units[i, -28:].sum())
        if best is None or units > best[0]:
            best = (units, pid)
    if best is None or best[0] <= 0:
        return None
    return best[1], svc.catalog.products[best[1]].product_name


def build_strategy_graph(svc: Services, drafts: DraftStore) -> Any:
    def get_current_store_context(state: StrategyState) -> StrategyState:
        req = state["request"]
        store = svc.catalog.resolve_store(req.get("store"))
        product = svc.catalog.resolve_product(req.get("product"))
        position = svc.position(store.store_id).products[product.product_id]
        period = int(np.clip(req.get("period_days") or 7, 1, 14))
        first_day = svc.as_of + timedelta(days=1)
        start = req.get("start_date") or first_day
        offset = int(np.clip((start - first_day).days, 0, 14))
        valid_from = first_day + timedelta(days=offset)
        return {
            "store_id": store.store_id,
            "product_id": product.product_id,
            "price": product.retail_price,
            "unit_cost": svc.catalog.unit_cost(product.product_id, position.last_supplier_id),
            "promo_offset": offset,
            "valid_from": valid_from,
            "valid_to": valid_from + timedelta(days=period - 1),
        }

    def get_demand_forecast(state: StrategyState) -> StrategyState:
        horizon = state["promo_offset"] + (state["valid_to"] - state["valid_from"]).days + 1 + POST_PROMO_DAYS
        forecast = svc.forecast(state["store_id"], state["product_id"], horizon)
        position = svc.position(state["store_id"]).products[state["product_id"]]
        waste = waste_risk(svc.catalog, position, forecast, svc.as_of, state["unit_cost"])
        return {"p50": forecast.p50, "p80": forecast.p80, "has_waste_risk": waste.expected_waste_units >= 1}

    def get_historical_insights(state: StrategyState) -> StrategyState:
        horizon = len(state["p50"])
        forecast = svc.forecast(state["store_id"], state["product_id"], horizon)
        insights = [d.detail for d in forecast.drivers[:3]]
        insights.append("Dataset v2 has no promotion history, so uplift cannot be learned from past campaigns yet.")
        return {"insights": insights}

    def get_business_constraints(state: StrategyState) -> StrategyState:
        req = state["request"]
        constraints = StrategyConstraints(
            max_discount_pct=float(np.clip(req.get("max_discount_pct") or 20.0, 1.0, 60.0)),
            min_unit_margin_pct=float(np.clip(req.get("min_unit_margin_pct") or 5.0, 0.0, 100.0)),
            budget_mxn=float(max(0.0, req.get("budget_mxn") or 0.0)),
            period_days=(state["valid_to"] - state["valid_from"]).days + 1,
            max_stockout_risk="medium",
        )
        active = [
            ActivePromotion(d.draft_id, d.product_id, d.valid_from, d.valid_to)
            for d in drafts.list(store_id=state["store_id"], statuses=set(ACTIVE_STATUSES))
        ]
        return {"constraints": constraints, "active": active}

    def generate_strategy_candidates(state: StrategyState) -> StrategyState:
        product = svc.catalog.products[state["product_id"]]
        complements = COMPLEMENTS.get(product.category_id, ())
        exclude = set(state["request"].get("exclude_products") or [])
        store_id = state["store_id"]
        affinity = _top_seller(svc, store_id, product.product_id, complements[:1], exclude) or _top_seller(
            svc, store_id, product.product_id, complements, exclude
        )
        rescue = _top_seller(svc, store_id, product.product_id, (*complements, "C01", "C06"), exclude)
        # Prefer a partner observed in real baskets (ticket_lines) over the category business rule.
        observed = next(
            (a for a in svc.affinity_partners(product.product_id) if a.other_product_id not in exclude),
            None,
        )
        if observed is not None:
            partner = (observed.other_product_id, svc.catalog.products[observed.other_product_id].product_name)
            affinity = rescue = partner
        candidates = generate_candidates(
            product.product_id,
            product.product_name,
            state["constraints"].max_discount_pct,
            state["request"].get("objective") or "maximize_margin",
            state["has_waste_risk"],
            affinity,
            rescue,
        )
        return {
            "candidates": candidates,
            "affinity_anchor": affinity,
            "rescue_anchor": rescue,
            "observed_partner": observed,
        }

    def simulate_strategy(state: StrategyState) -> StrategyState:
        product = svc.catalog.products[state["product_id"]]
        position = svc.position(state["store_id"]).products[state["product_id"]]
        horizon = len(state["p50"])
        policy = reorder_policy(svc, state["store_id"], state["product_id"])
        uplift = svc.uplift()
        anchors: dict[str, np.ndarray] = {}
        results: list[SimulationResult] = []
        contexts: dict[str, SimulationContext] = {}
        for candidate in state["candidates"]:
            anchor = None
            anchor_product = svc.catalog.products.get(candidate.anchor_product_id or "")
            if candidate.anchor_product_id:
                if candidate.anchor_product_id not in anchors:
                    anchors[candidate.anchor_product_id] = svc.forecast(
                        state["store_id"], candidate.anchor_product_id, horizon
                    ).p50
                anchor = anchors[candidate.anchor_product_id]
            ctx = SimulationContext(
                start=svc.as_of + timedelta(days=1),
                promo_offset=state["promo_offset"],
                period_days=state["constraints"].period_days,
                price=state["price"],
                unit_cost=state["unit_cost"],
                shelf_life_days=product.shelf_life_days,
                elasticity=elasticity(product.category_id),
                position=position,
                p50=state["p50"],
                p80=state["p80"],
                anchor_p50=anchor,
                policy=policy,
                uplift=uplift.get(candidate.strategy_type),
                anchor_price=anchor_product.retail_price if anchor_product else 0.0,
                anchor_cost=svc.catalog.unit_cost(anchor_product.product_id) if anchor_product else 0.0,
            )
            contexts[candidate.candidate_id] = ctx
            results.append(simulate(candidate, ctx))
        return {"results": results, "contexts": contexts}

    def validate_strategy(state: StrategyState) -> StrategyState:
        return {
            "violations": {
                r.candidate.candidate_id: validate(
                    r,
                    state["constraints"],
                    state["price"],
                    state["unit_cost"],
                    state["valid_from"],
                    state["valid_to"],
                    state["active"],
                    state["product_id"],
                    state["request"].get("objective") or "maximize_margin",
                    state["constraints"].budget_mxn,
                )
                for r in state["results"]
            }
        }

    def select_strategy(state: StrategyState) -> StrategyState:
        objective = state["request"].get("objective") or "maximize_margin"
        viable = [r for r in state["results"] if not state["violations"][r.candidate.candidate_id]]
        if not viable:
            return {"chosen": None}
        if objective == "increase_sales":
            chosen = max(viable, key=lambda r: (r.sales_increment, r.net_impact))
        elif objective == "reduce_waste":
            chosen = max(viable, key=lambda r: (round(r.waste_reduction_value, 2), r.net_impact))
        else:
            chosen = max(viable, key=lambda r: r.net_impact)
        return {"chosen": chosen}

    def create_promotion_draft(state: StrategyState) -> StrategyState:
        output = _build_output(svc, state)
        if state.get("chosen") is not None and state["request"].get("create_draft", True):
            draft = drafts.create(output)
            output = draft.strategy
        return {"output": output}

    graph = StateGraph(StrategyState)
    for node in (
        get_current_store_context,
        get_demand_forecast,
        get_historical_insights,
        get_business_constraints,
        generate_strategy_candidates,
        simulate_strategy,
        validate_strategy,
        select_strategy,
        create_promotion_draft,
    ):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "get_current_store_context")
    graph.add_edge("get_current_store_context", "get_demand_forecast")
    graph.add_edge("get_demand_forecast", "get_historical_insights")
    graph.add_edge("get_historical_insights", "get_business_constraints")
    graph.add_edge("get_business_constraints", "generate_strategy_candidates")
    graph.add_edge("generate_strategy_candidates", "simulate_strategy")
    graph.add_edge("simulate_strategy", "validate_strategy")
    graph.add_edge("validate_strategy", "select_strategy")
    graph.add_edge("select_strategy", "create_promotion_draft")
    graph.add_edge("create_promotion_draft", END)
    return graph.compile(name="strategy_pipeline")


def _points(result: SimulationResult, treated: bool, ctx: SimulationContext) -> list[ScenarioPoint]:
    scenario = result.treated if treated else result.baseline
    mask = ctx.promo_mask()
    return [
        ScenarioPoint(
            date=ctx.start + timedelta(days=k),
            units=round(float(scenario.projection.units_sold[k]), 2),
            revenue=round(float(scenario.revenue[k]), 2),
            waste_units=round(float(scenario.projection.waste_units[k]), 2),
            in_promotion=bool(mask[k]),
        )
        for k in range(ctx.horizon)
    ]


def _action_text(candidate: Candidate, product_name: str, valid_from: date, valid_to: date) -> str:
    span = f"{valid_from.strftime('%b %d')} to {valid_to.strftime('%b %d')}"
    if candidate.strategy_type == "contextual_discount":
        return f"Offer {candidate.discount_pct:g}% off {product_name} from {span}."
    if candidate.strategy_type == "rescue_combo":
        return (
            f"Run a rescue combo from {span}: shoppers who buy {candidate.anchor_product_name} "
            f"get {product_name} at {candidate.discount_pct:g}% off."
        )
    return (
        f"Cross-sell from {span}: shoppers who buy {candidate.anchor_product_name} "
        f"get {product_name} at {candidate.discount_pct:g}% off."
    )


def _cross_sell_basis(svc: Services, state: StrategyState, anchor_id: str, category_name: str) -> str:
    observed = state.get("observed_partner")
    anchor = svc.catalog.products[anchor_id]
    if observed is not None and observed.other_product_id == anchor_id:
        return (
            f"Observed in {observed.co_tickets:,} tickets: {observed.confidence * 100:.1f}% of baskets with this "
            f"product also include {anchor.product_name} (lift {observed.lift:.1f}x)."
        )
    return (
        f"Business rule (synthetic affinity): {svc.catalog.categories[anchor.category_id].category_name} shoppers "
        f"are offered {category_name}. Not observed in ticket data."
    )


def _build_output(svc: Services, state: StrategyState) -> ProductSellStrategy:
    store = svc.catalog.stores[state["store_id"]]
    product = svc.catalog.products[state["product_id"]]
    category = svc.catalog.categories[product.category_id]
    objective: Objective = state["request"].get("objective") or "maximize_margin"
    chosen = state.get("chosen")
    violations = state["violations"]
    results = sorted(
        state["results"],
        key=lambda r: (bool(violations[r.candidate.candidate_id]), -r.net_impact),
    )
    candidates_out = [
        StrategyCandidateOut(
            candidate_id=r.candidate.candidate_id,
            strategy_type=r.candidate.strategy_type,
            title=r.candidate.title,
            discount_pct=r.candidate.discount_pct,
            cross_sell_product=svc.product_ref(svc.catalog.products[r.candidate.anchor_product_id])
            if r.candidate.anchor_product_id
            else None,
            expected_units_increment=round(r.units_increment, 1),
            expected_sales_increment=round(r.sales_increment, 2),
            expected_margin_increment=round(r.margin_increment, 2),
            expected_waste_reduction=round(r.waste_reduction, 1),
            expected_net_impact=round(r.net_impact, 2),
            stockout_risk=r.stockout_risk,
            viable=not violations[r.candidate.candidate_id],
            violations=violations[r.candidate.candidate_id],
        )
        for r in results
    ]

    model_name = svc.forecast(state["store_id"], state["product_id"], len(state["p50"])).model
    reference = chosen or (results[0] if results else None)
    ref_ctx = state["contexts"].get(reference.candidate.candidate_id) if reference else None
    uplift = ref_ctx.uplift if ref_ctx else None
    assumptions = [f"Baseline demand: {model_name} forecast (P50) from data as of {svc.as_of.isoformat()}."]
    if uplift is not None:
        assumptions.append(
            f"Demand response measured from {uplift.campaigns} past '{uplift.campaign_type}' campaigns "
            f"(treatment vs control stores): median +{uplift.typical_uplift_pct:g}% units at "
            f"{uplift.typical_discount_pct:g}% off, scaled linearly with the discount (capped at twice that discount)."
        )
        if reference and reference.candidate.is_combo:
            assumptions.append(
                f"Combo anchor sells +{uplift.anchor_per_unit * 100:.0f}% per 100% discount at full price "
                "(measured in the same campaigns); every promoted unit gets the discount."
            )
    else:
        assumptions.append(
            f"Price response: elasticity {elasticity(product.category_id)} for {category.category_name} "
            "(assumed: the dataset has no promotion campaigns)."
        )
        if reference and reference.candidate.is_combo:
            assumptions.append(
                f"Combo attach rate: {ATTACH_BASE * 100:g}% + {ATTACH_PER_POINT * 100:g} pp per discount point of "
                f"the anchor's buyers; {COMBO_CANNIBALIZATION * 100:g}% of the item's usual buyers also get the "
                "combo price (assumed).",
            )
    assumptions += [
        "Inventory: today's stock, orders in transit and the store's usual reorder rule in both scenarios.",
        f"The window includes {POST_PROMO_DAYS} days after the promotion to capture expirations and waste.",
        (
            "Confidence range: interquartile range of the measured campaigns."
            if uplift is not None
            else "Confidence range: the same simulation with the promotion effect at 60% and 140%."
        ),
        *state.get("insights", []),
    ]
    disclaimer = (
        "Simulated estimate on synthetic data, not a guaranteed result. Uplift is measured from past campaigns "
        "on a few product pairs and transferred to this product."
        if uplift is not None
        else "Simulated estimate on synthetic data, not a guaranteed result. Uplift uses assumed price elasticities "
        "because the dataset has no promotion campaigns."
    )

    first_ctx = next(iter(state["contexts"].values()), None) if state.get("contexts") else None
    if chosen is None or first_ctx is None:
        empty_ctx = first_ctx
        baseline_points = _points(results[0], False, empty_ctx) if results and empty_ctx else []
        reasons = sorted({v for r in results for v in violations[r.candidate.candidate_id]})
        return ProductSellStrategy(
            store=svc.store_ref(store),
            product=svc.product_ref(product),
            data_as_of=svc.as_of,
            objective=objective,
            recommended_action="No action recommended: no candidate passed the business rules.",
            strategy_type=None,
            discount_pct=0.0,
            cross_sell_product=None,
            cross_sell_basis=None,
            valid_from=state["valid_from"],
            valid_to=state["valid_to"],
            baseline_series=baseline_points,
            strategy_series=baseline_points,
            expected_units_increment=0.0,
            expected_sales_increment=0.0,
            expected_margin_increment=0.0,
            expected_waste_reduction=0.0,
            expected_waste_reduction_value=0.0,
            expected_net_impact=0.0,
            stockout_risk="low",
            confidence_interval=ConfidenceInterval(low=0.0, high=0.0, basis="No viable candidate."),
            constraints=state["constraints"],
            assumptions=assumptions + [f"Rejected because: {r}" for r in reasons[:4]],
            candidates=candidates_out,
            draft_id=None,
            draft_status=None,
            requires_approval=True,
            uplift_source="campaigns" if uplift is not None else "assumed",
            disclaimer=disclaimer,
        )

    ctx = state["contexts"][chosen.candidate.candidate_id]
    anchor = svc.catalog.products.get(chosen.candidate.anchor_product_id or "")
    risk: RiskLevel = chosen.stockout_risk
    return ProductSellStrategy(
        store=svc.store_ref(store),
        product=svc.product_ref(product),
        data_as_of=svc.as_of,
        objective=objective,
        recommended_action=_action_text(chosen.candidate, product.product_name, state["valid_from"], state["valid_to"]),
        strategy_type=chosen.candidate.strategy_type,
        discount_pct=chosen.candidate.discount_pct,
        cross_sell_product=svc.product_ref(anchor) if anchor else None,
        cross_sell_basis=_cross_sell_basis(svc, state, anchor.product_id, category.category_name) if anchor else None,
        valid_from=state["valid_from"],
        valid_to=state["valid_to"],
        baseline_series=_points(chosen, False, ctx),
        strategy_series=_points(chosen, True, ctx),
        expected_units_increment=round(chosen.units_increment, 1),
        expected_sales_increment=round(chosen.sales_increment, 2),
        expected_margin_increment=round(chosen.margin_increment, 2),
        expected_waste_reduction=round(chosen.waste_reduction, 1),
        expected_waste_reduction_value=round(chosen.waste_reduction_value, 2),
        expected_net_impact=round(chosen.net_impact, 2),
        stockout_risk=risk,
        confidence_interval=ConfidenceInterval(
            low=round(chosen.net_impact_low, 2),
            high=round(chosen.net_impact_high, 2),
            basis=(
                f"Interquartile range of {ctx.uplift.campaigns} measured campaigns."
                if ctx.uplift is not None
                else "Promotion effect at 60%-140% of the assumed elasticity / attach rate."
            ),
        ),
        constraints=state["constraints"],
        assumptions=assumptions,
        candidates=candidates_out,
        draft_id=None,
        draft_status=None,
        requires_approval=True,
        expected_anchor_units_increment=round(chosen.anchor_units_increment, 1),
        uplift_source="campaigns" if ctx.uplift is not None else "assumed",
        disclaimer=disclaimer,
    )


class StrategyEngine:
    """Facade used by REST endpoints and agent tools."""

    def __init__(self, svc: Services, drafts: DraftStore):
        self.svc = svc
        self.drafts = drafts
        self.graph = build_strategy_graph(svc, drafts)

    def run(self, request: StrategyRequest) -> ProductSellStrategy:
        # Run in a fresh context, detached from the caller's callbacks: the copilot stream should
        # carry the tool result, not a state snapshot for every internal pipeline step.
        state = contextvars.Context().run(self.graph.invoke, {"request": request})
        return state["output"]

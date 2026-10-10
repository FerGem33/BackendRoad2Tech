"""Agent tools: thin, validated wrappers over the deterministic domain functions.

Each tool returns the JSON of a public contract (`domain/models.py`) with a `kind` field. The
frontend renders that JSON as a component (chart, card, table) for the matching tool name,
so the LLM never has to restate the numbers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from ..domain.catalog import NotFoundError
from ..domain.overview import get_demand_by_store, get_store_overview
from ..domain.restock import get_restock_suggestions
from ..domain.services import Services
from ..domain.strategy.pipeline import StrategyEngine
from ..domain.strategy.portfolio import generate_plan
from ..domain.tendency import get_product_tendency

log = logging.getLogger(__name__)

TOOL_TENDENCY = "get_product_tendency"
TOOL_STRATEGY = "get_product_sell_strategy"
TOOL_RESTOCK = "get_restock_suggestions"
TOOL_OVERVIEW = "get_store_overview"
TOOL_DEMAND = "get_demand_by_store"
TOOL_PLAN = "generate_store_plan"
TOOL_SEARCH = "search_products"

StoreArg = Annotated[
    str | None,
    Field(description="Store id (S001-S025) or name. Omit to use the store selected in the dashboard."),
]
ProductArg = Annotated[str, Field(description="Product id (P001-P120) or product name, in English or Spanish.")]


def _error(message: str) -> str:
    return json.dumps({"kind": "error", "message": message})


def _store_from_state(store: str | None, state: dict[str, Any] | None) -> str:
    chosen = store or (state or {}).get("selected_store_id")
    if not chosen:
        raise NotFoundError("Which store? Pick one in the dashboard or name it (for example S001).")
    return chosen


def _safe(fn: Callable[[], BaseModel]) -> str:
    try:
        return fn().model_dump_json()
    except (NotFoundError, ValueError) as exc:
        return _error(str(exc))
    except Exception:
        log.exception("Tool failed")
        return _error("The analysis failed unexpectedly. Please try again.")


class TendencyArgs(BaseModel):
    product: ProductArg
    store: StoreArg = None
    history_days: int = Field(90, ge=14, le=365, description="Days of history to show.")
    forecast_days: int = Field(14, ge=1, le=28, description="Days to forecast.")
    state: Annotated[dict[str, Any] | None, InjectedState] = None


class StrategyArgs(BaseModel):
    product: ProductArg
    store: StoreArg = None
    objective: Literal["increase_sales", "reduce_waste", "maximize_margin"] = Field(
        "maximize_margin",
        description="increase_sales (sell more units), reduce_waste (avoid expiry/merma), maximize_margin.",
    )
    max_discount_pct: float = Field(20, ge=1, le=60, description="Highest discount the manager accepts, in %.")
    period_days: int = Field(7, ge=1, le=14, description="Promotion length in days.")
    start_date: date | None = Field(None, description="First promotion day (YYYY-MM-DD). Default: tomorrow.")
    budget_mxn: float = Field(
        0, ge=0, description="Margin (MXN) the manager accepts to invest to raise sales. Only for increase_sales."
    )
    state: Annotated[dict[str, Any] | None, InjectedState] = None


class RestockArgs(BaseModel):
    store: StoreArg = None
    product: str | None = Field(None, description="Limit to one product (id or name). Omit to check all products.")
    limit: int = Field(5, ge=1, le=20)
    state: Annotated[dict[str, Any] | None, InjectedState] = None


class OverviewArgs(BaseModel):
    store: StoreArg = None
    period_days: int = Field(7, ge=1, le=90, description="KPI period length in days.")
    state: Annotated[dict[str, Any] | None, InjectedState] = None


class DemandArgs(BaseModel):
    product: ProductArg
    days: int = Field(28, ge=7, le=180, description="Length of the comparison window in days.")


class PlanArgs(BaseModel):
    store: StoreArg = None
    period_days: int = Field(7, ge=1, le=14)
    state: Annotated[dict[str, Any] | None, InjectedState] = None


class SearchArgs(BaseModel):
    query: str = Field(description="Words to look for in product names, brands and categories.")


def build_tools(svc: Services, engine: StrategyEngine) -> list[BaseTool]:
    def tendency(
        product: str,
        store: str | None = None,
        history_days: int = 90,
        forecast_days: int = 14,
        state: dict[str, Any] | None = None,
    ) -> str:
        return _safe(
            lambda: get_product_tendency(svc, _store_from_state(store, state), product, history_days, forecast_days)
        )

    def strategy(
        product: str,
        store: str | None = None,
        objective: str = "maximize_margin",
        max_discount_pct: float = 20,
        period_days: int = 7,
        start_date: date | None = None,
        budget_mxn: float = 0,
        state: dict[str, Any] | None = None,
    ) -> str:
        return _safe(
            lambda: engine.run(
                {
                    "store": _store_from_state(store, state),
                    "product": product,
                    "objective": objective,  # type: ignore[typeddict-item]
                    "max_discount_pct": max_discount_pct,
                    "period_days": period_days,
                    "start_date": start_date,
                    "budget_mxn": budget_mxn,
                    "create_draft": True,
                }
            )
        )

    def restock(
        store: str | None = None, product: str | None = None, limit: int = 5, state: dict[str, Any] | None = None
    ) -> str:
        return _safe(lambda: get_restock_suggestions(svc, _store_from_state(store, state), product, limit))

    def overview(store: str | None = None, period_days: int = 7, state: dict[str, Any] | None = None) -> str:
        return _safe(lambda: get_store_overview(svc, _store_from_state(store, state), period_days))

    def demand(product: str, days: int = 28) -> str:
        return _safe(lambda: get_demand_by_store(svc, product, days))

    def plan(store: str | None = None, period_days: int = 7, state: dict[str, Any] | None = None) -> str:
        return _safe(lambda: generate_plan(engine, _store_from_state(store, state), period_days))

    def search(query: str) -> str:
        found = svc.catalog.search_products(query, limit=8)
        return json.dumps(
            {
                "kind": "product_search",
                "query": query,
                "products": [svc.product_ref(p).model_dump() for p in found],
            }
        )

    return [
        StructuredTool.from_function(
            tendency,
            name=TOOL_TENDENCY,
            args_schema=TendencyArgs,
            description=(
                "Demand trend of one product in one store: daily history, P50/P80/P90 forecast, trend "
                "(increasing/stable/decreasing), main drivers (weekday, weather, events), run-out and waste "
                "risk, current stock. Use for 'how is X selling', 'forecast', 'tendencia', 'pronóstico'."
            ),
        ),
        StructuredTool.from_function(
            strategy,
            name=TOOL_STRATEGY,
            args_schema=StrategyArgs,
            description=(
                "Design a sell strategy (discount, rescue combo or cross-sell) for one product in one store. "
                "Runs the deterministic simulator (baseline vs strategy), validates business rules and saves a "
                "promotion DRAFT that the manager must approve. Use for promotions, 'vender más', 'merma', "
                "'descuento', combos."
            ),
        ),
        StructuredTool.from_function(
            restock,
            name=TOOL_RESTOCK,
            args_schema=RestockArgs,
            description=(
                "Restock suggestions: which products to order today, from which supplier (with email), how many "
                "units and when they arrive, with the reasons (stock limit, demand, season)."
            ),
        ),
        StructuredTool.from_function(
            overview,
            name=TOOL_OVERVIEW,
            args_schema=OverviewArgs,
            description=(
                "Store dashboard: total sales vs previous period, run-outs, products with no demand, waste "
                "risk, top movers and alerts. Use for 'how is my store doing', 'resumen', 'alertas'."
            ),
        ),
        StructuredTool.from_function(
            demand,
            name=TOOL_DEMAND,
            args_schema=DemandArgs,
            description="Compare one product's demand across all 25 branches (recent vs previous, next 7 days).",
        ),
        StructuredTool.from_function(
            plan,
            name=TOOL_PLAN,
            args_schema=PlanArgs,
            description=(
                "Automatic commercial plan for a store: up to 5 compatible, simulated strategies saved as drafts, "
                "with the total portfolio impact."
            ),
        ),
        StructuredTool.from_function(
            search,
            name=TOOL_SEARCH,
            args_schema=SearchArgs,
            description="Find catalog products by name, brand or category when the product is ambiguous.",
        ),
    ]

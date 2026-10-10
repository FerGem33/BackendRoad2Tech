"""REST endpoints (contract from BackendRoad2Tech `milestones.md`, "API inicial").

Every success body is `{request_id, data_as_of, data}`; every error body is
`{code, message, request_id}` without internal exception details.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Generic, Literal, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import __version__
from ..agent.intent import parse_strategy_prompt, summarize_tendency
from ..domain.models import (
    DemandByStore,
    DraftStatus,
    Objective,
    Portfolio,
    ProductRef,
    ProductSellStrategy,
    ProductTendency,
    PromotionDraft,
    RestockPlan,
    StoreOverview,
    StoreRef,
    SupplierRef,
)
from ..domain.overview import get_demand_by_store, get_store_overview
from ..domain.restock import get_restock_suggestions
from ..domain.strategy.portfolio import generate_plan
from ..domain.tendency import get_product_tendency
from .context import AppContext

T = TypeVar("T")

router = APIRouter()


class Envelope(BaseModel, Generic[T]):
    request_id: str
    data_as_of: date
    data: T


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class CategoryOut(BaseModel):
    category_id: str
    category_name: str
    is_perishable: bool


class MetaOut(BaseModel):
    version: str
    data_as_of: date
    data_from: date
    data_to: date
    data_backend: str
    llm: str
    stores: int
    products: int


class StrategyPromptBody(BaseModel):
    prompt: str | None = Field(None, description="Natural-language request, e.g. 'reduce waste of sandwiches'")
    product_id: str | None = None
    objective: Objective | None = None
    max_discount_pct: float | None = Field(None, ge=1, le=60)
    period_days: int | None = Field(None, ge=1, le=14)
    start_date: date | None = None
    budget_mxn: float | None = Field(None, ge=0)


class GeneratePlanBody(BaseModel):
    period_days: int = Field(7, ge=1, le=14)


class DraftActionBody(BaseModel):
    actor: str = Field("store-manager", max_length=80)
    note: str | None = Field(None, max_length=500)


def _first(*values: object) -> object:
    """First value that is not None (explicit body field > parsed prompt > default)."""
    return next((v for v in values if v is not None), None)


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


Ctx = Annotated[AppContext, Depends(ctx)]


def envelope(request: Request, c: AppContext, data: T) -> Envelope[T]:
    return Envelope(request_id=request.state.request_id, data_as_of=c.svc.as_of, data=data)


ERRORS = {
    404: {"model": ErrorBody, "description": "Store, product or draft not found"},
    409: {"model": ErrorBody, "description": "Invalid draft status transition"},
    422: {"model": ErrorBody, "description": "Invalid parameters"},
}


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ping", tags=["system"], summary="AgentCore Runtime health check")
def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@router.get("/meta", tags=["system"], response_model=Envelope[MetaOut])
def meta(request: Request, c: Ctx) -> Envelope[MetaOut]:
    first, last = c.svc.repo.data_bounds()
    return envelope(
        request,
        c,
        MetaOut(
            version=__version__,
            data_as_of=c.svc.as_of,
            data_from=first,
            data_to=last,
            data_backend=c.settings.data_backend,
            llm=c.llm,
            stores=len(c.svc.catalog.stores),
            products=len(c.svc.catalog.products),
        ),
    )


@router.get("/stores", tags=["catalog"], response_model=Envelope[list[StoreRef]])
def list_stores(request: Request, c: Ctx) -> Envelope[list[StoreRef]]:
    return envelope(request, c, [c.svc.store_ref(s) for s in c.svc.catalog.stores.values()])


@router.get("/products", tags=["catalog"], response_model=Envelope[list[ProductRef]])
def list_products(
    request: Request,
    c: Ctx,
    category_id: str | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
) -> Envelope[list[ProductRef]]:
    products = c.svc.catalog.search_products(q, limit=50) if q else list(c.svc.catalog.products.values())
    if category_id:
        products = [p for p in products if p.category_id == category_id]
    return envelope(request, c, [c.svc.product_ref(p) for p in products])


@router.get("/categories", tags=["catalog"], response_model=Envelope[list[CategoryOut]])
def list_categories(request: Request, c: Ctx) -> Envelope[list[CategoryOut]]:
    return envelope(
        request,
        c,
        [
            CategoryOut(category_id=x.category_id, category_name=x.category_name, is_perishable=x.is_perishable)
            for x in c.svc.catalog.categories.values()
        ],
    )


@router.get("/suppliers", tags=["catalog"], response_model=Envelope[list[SupplierRef]])
def list_suppliers(request: Request, c: Ctx) -> Envelope[list[SupplierRef]]:
    return envelope(request, c, [c.svc.supplier_ref(sid) for sid in c.svc.catalog.suppliers])


@router.get("/stores/{store_id}/overview", tags=["model"], response_model=Envelope[StoreOverview], responses=ERRORS)
def store_overview(
    request: Request, c: Ctx, store_id: str, period_days: Annotated[int, Query(ge=1, le=90)] = 7
) -> Envelope[StoreOverview]:
    return envelope(request, c, get_store_overview(c.svc, store_id, period_days))


@router.get("/stores/{store_id}/tendency", tags=["model"], response_model=Envelope[ProductTendency], responses=ERRORS)
def product_tendency(
    request: Request,
    c: Ctx,
    store_id: str,
    product_id: Annotated[str, Query(min_length=1)],
    history_days: Annotated[int, Query(ge=14, le=365)] = 90,
    forecast_days: Annotated[int, Query(ge=1, le=28)] = 14,
    summarize: bool = True,
) -> Envelope[ProductTendency]:
    """`get_product_tendency`. With `summarize=true` and Gemini configured, `ai_summary` is LLM-written."""
    result = get_product_tendency(c.svc, store_id, product_id, history_days, forecast_days)
    if summarize:
        result.ai_summary = summarize_tendency(c.model, result)
    return envelope(request, c, result)


@router.get("/stores/{store_id}/restock", tags=["agent"], response_model=Envelope[RestockPlan], responses=ERRORS)
def restock(
    request: Request,
    c: Ctx,
    store_id: str,
    product_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> Envelope[RestockPlan]:
    return envelope(request, c, get_restock_suggestions(c.svc, store_id, product_id, limit))


@router.get(
    "/products/{product_id}/demand-by-store", tags=["model"], response_model=Envelope[DemandByStore], responses=ERRORS
)
def demand_by_store(
    request: Request, c: Ctx, product_id: str, days: Annotated[int, Query(ge=7, le=180)] = 28
) -> Envelope[DemandByStore]:
    return envelope(request, c, get_demand_by_store(c.svc, product_id, days))


@router.post(
    "/stores/{store_id}/strategies:generate", tags=["agent"], response_model=Envelope[Portfolio], responses=ERRORS
)
def strategies_generate(
    request: Request, c: Ctx, store_id: str, body: Annotated[GeneratePlanBody | None, Body()] = None
) -> Envelope[Portfolio]:
    """Automatic plan: up to five compatible strategies, each saved as a draft."""
    period_days = body.period_days if body else GeneratePlanBody().period_days
    return envelope(request, c, generate_plan(c.engine, store_id, period_days))


@router.post(
    "/stores/{store_id}/strategies:prompt",
    tags=["agent"],
    response_model=Envelope[ProductSellStrategy],
    responses=ERRORS,
)
def strategies_prompt(
    request: Request, c: Ctx, store_id: str, body: StrategyPromptBody
) -> Envelope[ProductSellStrategy]:
    """`get_product_sell_strategy` from structured fields, a natural-language `prompt`, or both."""
    params = parse_strategy_prompt(c.model, body.prompt, c.svc.catalog, c.svc.as_of) if body.prompt else None
    product = body.product_id or (params.product if params else None)
    if not product:
        raise HTTPException(status_code=422, detail="Name a product (product_id or in the prompt).")
    result = c.engine.run(
        {
            "store": store_id,
            "product": product,
            "objective": _first(body.objective, params and params.objective, "maximize_margin"),
            "max_discount_pct": _first(body.max_discount_pct, params and params.max_discount_pct, 20.0),
            "period_days": _first(body.period_days, params and params.period_days, 7),
            "start_date": _first(body.start_date, params and params.start_date),
            "budget_mxn": _first(body.budget_mxn, params and params.budget_mxn, 0.0),
            "create_draft": True,
        }
    )
    return envelope(request, c, result)


@router.get("/drafts", tags=["approval"], response_model=Envelope[list[PromotionDraft]])
def list_drafts(
    request: Request, c: Ctx, store_id: str | None = None, status: DraftStatus | None = None
) -> Envelope[list[PromotionDraft]]:
    return envelope(request, c, c.drafts.list(store_id=store_id, statuses={status} if status else None))


@router.get("/drafts/{draft_id}", tags=["approval"], response_model=Envelope[PromotionDraft], responses=ERRORS)
def get_draft(request: Request, c: Ctx, draft_id: str) -> Envelope[PromotionDraft]:
    return envelope(request, c, c.drafts.get(draft_id))


def _transition(
    request: Request, c: AppContext, draft_id: str, to: DraftStatus, body: DraftActionBody | None
) -> Envelope[PromotionDraft]:
    body = body or DraftActionBody()
    return envelope(request, c, c.drafts.transition(draft_id, to, actor=body.actor, note=body.note))


@router.post(
    "/drafts/{draft_id}/add-to-plan", tags=["approval"], response_model=Envelope[PromotionDraft], responses=ERRORS
)
def add_to_plan(
    request: Request, c: Ctx, draft_id: str, body: Annotated[DraftActionBody | None, Body()] = None
) -> Envelope[PromotionDraft]:
    return _transition(request, c, draft_id, "selected", body)


@router.post("/drafts/{draft_id}/approve", tags=["approval"], response_model=Envelope[PromotionDraft], responses=ERRORS)
def approve(
    request: Request, c: Ctx, draft_id: str, body: Annotated[DraftActionBody | None, Body()] = None
) -> Envelope[PromotionDraft]:
    return _transition(request, c, draft_id, "approved", body)


@router.post("/drafts/{draft_id}/reject", tags=["approval"], response_model=Envelope[PromotionDraft], responses=ERRORS)
def reject(
    request: Request, c: Ctx, draft_id: str, body: Annotated[DraftActionBody | None, Body()] = None
) -> Envelope[PromotionDraft]:
    return _transition(request, c, draft_id, "rejected", body)


class PlanOut(BaseModel):
    store: StoreRef
    period_from: date
    period_to: date
    drafts: list[PromotionDraft]
    net_expected_impact: float
    statuses: list[Literal["selected", "approved"]]


@router.get("/stores/{store_id}/plan", tags=["approval"], response_model=Envelope[PlanOut], responses=ERRORS)
def store_plan(
    request: Request,
    c: Ctx,
    store_id: str,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> Envelope[PlanOut]:
    """Strategies in the current plan (selected or approved) that overlap the period."""
    store = c.svc.catalog.resolve_store(store_id)
    start = date_from or c.svc.as_of
    end = date_to or start.replace(year=start.year + 1)
    if end < start:
        raise HTTPException(status_code=422, detail="'to' must be on or after 'from'.")
    items = c.drafts.list(store_id=store.store_id, statuses={"selected", "approved"}, overlaps=(start, end))
    return envelope(
        request,
        c,
        PlanOut(
            store=c.svc.store_ref(store),
            period_from=start,
            period_to=end,
            drafts=items,
            net_expected_impact=round(sum(d.expected_net_impact for d in items), 2),
            statuses=["selected", "approved"],
        ),
    )

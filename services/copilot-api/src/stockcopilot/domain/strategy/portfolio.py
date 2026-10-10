"""Automatic commercial plan: up to five compatible strategies for a store (FLOW.md).

Opportunities are found deterministically (expected waste, overstock) and ranked by value at
risk. Each one goes through the same strategy pipeline, greedily: no product is promoted twice
or reused as a combo anchor by another strategy, and at most two strategies share a category.
Because the selected strategies never touch the same product, the portfolio impact is the sum
of the selected strategies (no double counting).
"""

from __future__ import annotations

from datetime import timedelta

from ..models import Portfolio, ProductSellStrategy
from ..risk import waste_risk
from .pipeline import StrategyEngine

MAX_STRATEGIES = 5
MAX_PER_CATEGORY = 2
MAX_EVALUATED = 12


def generate_plan(engine: StrategyEngine, store: str, period_days: int = 7) -> Portfolio:
    svc = engine.svc
    s = svc.catalog.resolve_store(store)
    position = svc.position(s.store_id)

    scored: list[tuple[float, str, str]] = []
    for product_id, pos in position.products.items():
        forecast = svc.forecast(s.store_id, product_id, 14)
        mean = float(forecast.p50.mean())
        if mean <= 0:
            continue
        unit_cost = svc.catalog.unit_cost(product_id, pos.last_supplier_id)
        waste = waste_risk(svc.catalog, pos, forecast, svc.as_of, unit_cost)
        if waste.expected_waste_value > 0:
            scored.append((waste.expected_waste_value, product_id, "reduce_waste"))
            continue
        cover = pos.on_hand / mean
        if cover > 30:
            # Overstock: value of the stock beyond 30 days of demand.
            scored.append(((pos.on_hand - 30 * mean) * unit_cost * 0.05, product_id, "maximize_margin"))
    scored.sort(reverse=True)

    chosen: list[ProductSellStrategy] = []
    used: set[str] = set()
    per_category: dict[str, int] = {}
    conflicts: list[str] = []
    skipped: list[str] = []
    evaluated = 0
    for _, product_id, objective in scored:
        if len(chosen) >= MAX_STRATEGIES or evaluated >= MAX_EVALUATED:
            break
        category = svc.catalog.products[product_id].category_id
        name = svc.catalog.products[product_id].product_name
        if product_id in used:
            conflicts.append(f"{name}: already used as a combo anchor in the plan.")
            continue
        if per_category.get(category, 0) >= MAX_PER_CATEGORY:
            conflicts.append(f"{name}: category limit ({MAX_PER_CATEGORY} per category) reached.")
            continue
        evaluated += 1
        output = engine.run(
            {
                "store": s.store_id,
                "product": product_id,
                "objective": objective,  # type: ignore[typeddict-item]
                "period_days": period_days,
                "create_draft": False,
                # Products already in the plan cannot anchor another combo (they would compete).
                "exclude_products": sorted(used),
            }
        )
        if output.strategy_type is None:
            skipped.append(f"{name}: no candidate passed the business rules.")
            continue
        chosen.append(output)
        used.add(product_id)
        if output.cross_sell_product:
            used.add(output.cross_sell_product.product_id)
        per_category[category] = per_category.get(category, 0) + 1

    chosen.sort(key=lambda o: o.expected_net_impact, reverse=True)
    saved = [engine.drafts.create(output, actor="strategy-planner").strategy for output in chosen]
    first = svc.as_of + timedelta(days=1)
    return Portfolio(
        store=svc.store_ref(s),
        data_as_of=svc.as_of,
        period_from=first,
        period_to=first + timedelta(days=period_days - 1),
        strategies=saved,
        net_expected_margin_increment=round(sum(o.expected_margin_increment for o in saved), 2),
        net_expected_impact=round(sum(o.expected_net_impact for o in saved), 2),
        conflicts=conflicts,
        skipped=skipped,
    )

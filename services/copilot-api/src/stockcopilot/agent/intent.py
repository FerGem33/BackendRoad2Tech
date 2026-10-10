"""LLM helpers outside the chat loop: prompt -> strategy parameters, and tendency summaries.

Both fall back to deterministic code when Gemini is not configured or fails.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..domain.catalog import Catalog
from ..domain.models import ProductTendency
from ..domain.tendency import template_summary
from .offline import OfflineCopilotModel, parse_intent
from .tools import TOOL_STRATEGY

log = logging.getLogger(__name__)


class StrategyPromptParams(BaseModel):
    product: str = Field(description="Product id or name mentioned by the manager")
    objective: Literal["increase_sales", "reduce_waste", "maximize_margin"] = "maximize_margin"
    max_discount_pct: float = Field(20, ge=1, le=60)
    period_days: int = Field(7, ge=1, le=14)
    start_date: date | None = None
    budget_mxn: float = Field(0, ge=0)


def parse_strategy_prompt(
    model: BaseChatModel | None, prompt: str, catalog: Catalog, today: date
) -> StrategyPromptParams | None:
    if model is not None and not isinstance(model, OfflineCopilotModel):
        try:
            structured = model.with_structured_output(StrategyPromptParams)
            result = structured.invoke(
                [
                    SystemMessage(
                        content=(
                            f"Today is {today.isoformat()} ({today.strftime('%A')}). Extract the promotion request "
                            "parameters. Map 'vender más' to increase_sales, 'merma/caducidad' to reduce_waste, "
                            "otherwise maximize_margin. Keep the product words as written."
                        )
                    ),
                    HumanMessage(content=prompt),
                ]
            )
            if isinstance(result, StrategyPromptParams):
                return result
        except Exception:
            log.exception("Gemini could not parse the strategy prompt; using the keyword parser")
    parsed = parse_intent(prompt, catalog, today)
    if not parsed:
        return None
    name, args = parsed
    if name != TOOL_STRATEGY:
        args = {"objective": "maximize_margin", **{k: v for k, v in args.items() if k == "product"}}
    if "product" not in args:
        return None
    return StrategyPromptParams(**{k: v for k, v in args.items() if k in StrategyPromptParams.model_fields})


def summarize_tendency(model: BaseChatModel | None, tendency: ProductTendency) -> str:
    """Let the LLM phrase the summary from computed facts only; otherwise use the template."""
    if model is None or isinstance(model, OfflineCopilotModel):
        return template_summary(tendency)
    facts = tendency.model_dump(mode="json", exclude={"historical_series", "forecast_series", "ai_summary"})
    facts["inventory"].pop("lots", None)
    try:
        reply = model.invoke(
            [
                SystemMessage(
                    content=(
                        "You write a 2-3 sentence suggestion for a convenience-store manager. Use only the numbers "
                        "in the JSON facts; do not compute new figures. Mention the trend, the main driver, the "
                        "biggest risk and one concrete next step. English, plain language."
                    )
                ),
                HumanMessage(content=json.dumps(facts)),
            ]
        )
        return reply.text.strip() or template_summary(tendency)
    except Exception:
        log.exception("Gemini summary failed; using the template")
        return template_summary(tendency)

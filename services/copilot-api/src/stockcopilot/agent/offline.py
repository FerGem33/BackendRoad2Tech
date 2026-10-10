"""Offline chat model used when no Gemini API key is configured.

It keeps the demo (and the tests) working without network access: a keyword parser turns the
user's message into one tool call, and a template turns the tool result into a short answer.
It speaks the same LangChain interface as Gemini, so the graph does not know the difference.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, Field

from ..domain.catalog import Catalog, NotFoundError
from .tools import (
    TOOL_DEMAND,
    TOOL_OVERVIEW,
    TOOL_PLAN,
    TOOL_RESTOCK,
    TOOL_SEARCH,
    TOOL_STRATEGY,
    TOOL_TENDENCY,
)

HELP = (
    "I can analyze a product's trend and forecast, design a promotion (discount, rescue combo or cross-sell), "
    "suggest restock orders, summarize a store, compare demand across branches or build a weekly plan. "
    'Try: "Trend of Coca-Cola 600 ml", "Strategy to reduce waste of Ham and Cheese Sandwich" or '
    '"What should I restock today?"'
)

KEYWORDS: dict[str, tuple[str, ...]] = {
    TOOL_PLAN: ("plan", "portafolio", "portfolio", "estrategias de la tienda", "weekly plan"),
    TOOL_STRATEGY: (
        "estrategia",
        "strategy",
        "promo",
        "descuento",
        "discount",
        "vender mas",
        "sell more",
        "merma",
        "waste",
        "rescat",
        "rescue",
        "combo",
        "cross",
        "oferta",
        "offer",
    ),
    TOOL_RESTOCK: (
        "reabast",
        "restock",
        "surtir",
        "resurt",
        "pedido",
        "pedir",
        "order",
        "proveedor",
        "supplier",
        "comprar",
        "reorder",
    ),
    TOOL_DEMAND: ("sucursal", "branches", "branch", "compar", "mapa", "map", "por tienda", "todas las tiendas"),
    TOOL_OVERVIEW: (
        "resumen",
        "overview",
        "kpi",
        "como va",
        "how is my store",
        "how is the store",
        "alerta",
        "alert",
        "dashboard",
        "ventas de la tienda",
        "store summary",
    ),
    TOOL_TENDENCY: ("tendencia", "trend", "pronostico", "forecast", "demanda", "demand", "predic", "como se vende"),
}

STOPWORDS = {
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "para",
    "en",
    "un",
    "una",
    "y",
    "a",
    "al",
    "con",
    "por",
    "que",
    "mi",
    "me",
    "muestra",
    "muestrame",
    "dame",
    "quiero",
    "cual",
    "es",
    "como",
    "the",
    "of",
    "for",
    "in",
    "an",
    "and",
    "to",
    "show",
    "my",
    "what",
    "is",
    "give",
    "please",
    "porfavor",
    "favor",
    "tienda",
    "store",
    "producto",
    "product",
    "sin",
    "mas",
    "más",
    "este",
    "esta",
    "this",
    "next",
    "proximo",
    "proxima",
    "hoy",
    "today",
    "dias",
    "days",
    "semana",
    "week",
    "sabado",
    "saturday",
    "domingo",
    "sunday",
    "maximo",
    "max",
    "ventas",
    "sales",
    "aumentar",
    "subir",
    "increase",
    "reducir",
    "reduce",
    "evitar",
    "avoid",
    "pesos",
    "mxn",
    "should",
    "deberia",
    "debo",
    "hacer",
    "do",
    "can",
    "puedo",
    "invertir",
    "invest",
    "hasta",
    "up",
    "hola",
    "hi",
    "hello",
    "hey",
    "gracias",
    "thanks",
    "buenos",
    "buenas",
    "dia",
    "tardes",
    "noches",
    "ok",
}
INTENT_WORDS = {w for words in KEYWORDS.values() for phrase in words for w in phrase.split()}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.lower()).strip()


def _last_human(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message.content if isinstance(message.content, str) else str(message.content)
    return ""


def _next_weekday(today: date, weekday: int) -> date:
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def parse_intent(text: str, catalog: Catalog, today: date) -> tuple[str, dict[str, Any]] | None:
    """Keyword intent parser: returns (tool name, arguments) or None."""
    norm = normalize(text)
    if not norm:
        return None
    intent = next((tool for tool, words in KEYWORDS.items() if any(w in norm for w in words)), None)

    args: dict[str, Any] = {}
    store_match = re.search(r"\bs\s?0*(\d{1,2})\b", norm)
    if store_match:
        args["store"] = f"S{int(store_match.group(1)):03d}"
    else:
        for store in catalog.stores.values():
            colonia = normalize(store.colonia)
            if len(colonia) > 4 and colonia in norm:
                args["store"] = store.store_id
                break

    product_match = re.search(r"\bp\s?0*(\d{1,3})\b", norm)
    product: str | None = None
    if product_match:
        product = f"P{int(product_match.group(1)):03d}"
    else:
        words = [
            w
            for w in re.findall(r"[a-z0-9\-]+", norm)
            if w not in STOPWORDS and w not in INTENT_WORDS and not re.fullmatch(r"s?\d+%?", w)
        ]
        store_words = set(normalize(catalog.stores[args["store"]].colonia).split()) if "store" in args else set()
        words = [w for w in words if w not in store_words]
        if words:
            found = catalog.search_products(" ".join(words), limit=1, require_word_match=True)
            if found:
                product = found[0].product_id

    if intent is None:
        intent = TOOL_TENDENCY if product else None
    if intent is None:
        return None

    if intent in (TOOL_TENDENCY, TOOL_STRATEGY, TOOL_DEMAND) and not product:
        return TOOL_SEARCH, {"query": text}
    if product and intent in (TOOL_TENDENCY, TOOL_STRATEGY, TOOL_DEMAND, TOOL_RESTOCK):
        args["product"] = product

    if intent == TOOL_STRATEGY:
        if any(w in norm for w in ("merma", "waste", "caduc", "rescat", "rescue", "expir", "venc")):
            args["objective"] = "reduce_waste"
        elif any(w in norm for w in ("vender mas", "sell more", "aumentar", "subir", "increase", "impulsar")):
            args["objective"] = "increase_sales"
        else:
            args["objective"] = "maximize_margin"
        pct = re.search(r"(\d{1,2})\s?%", norm)
        if pct:
            args["max_discount_pct"] = float(pct.group(1))
        budget = re.search(r"\$\s?(\d[\d,]*)|(\d[\d,]*)\s?(pesos|mxn)", norm)
        if budget:
            args["budget_mxn"] = float((budget.group(1) or budget.group(2)).replace(",", ""))
        if "fin de semana" in norm or "weekend" in norm:
            args["start_date"] = _next_weekday(today, 5).isoformat()
            args["period_days"] = 2
        elif "sabado" in norm or "saturday" in norm:
            args["start_date"] = _next_weekday(today, 5).isoformat()
            args["period_days"] = 1
        elif "domingo" in norm or "sunday" in norm:
            args["start_date"] = _next_weekday(today, 6).isoformat()
            args["period_days"] = 1
        days = re.search(r"(\d{1,2})\s?(dias|days)", norm)
        if days:
            args["period_days"] = max(1, min(14, int(days.group(1))))
    if intent == TOOL_TENDENCY:
        days = re.search(r"(\d{1,2})\s?(dias|days)", norm)
        if days:
            args["forecast_days"] = max(1, min(28, int(days.group(1))))
    if intent == TOOL_DEMAND:
        args.pop("store", None)
    return intent, args


def summarize_tool_result(name: str, content: str) -> str:
    """Short, number-faithful explanation of a tool result (used when no LLM is available)."""
    try:
        data = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return "I could not read the analysis result."
    kind = data.get("kind")
    if kind == "error":
        return data.get("message", "Something went wrong.")
    if kind == "product_tendency":
        return data["ai_summary"]
    if kind == "product_sell_strategy":
        if not data.get("strategy_type"):
            rejected = sum(1 for c in data.get("candidates", []) if not c["viable"])
            return (
                f"No promotion is recommended for {data['product']['product_name']}: all {rejected} simulated "
                "candidates broke a business rule (see the rejected list). Doing nothing is the better option."
            )
        ci = data["confidence_interval"]
        return (
            f"{data['recommended_action']} Expected net impact: ${data['expected_net_impact']:,.2f} MXN "
            f"(range ${ci['low']:,.2f} to ${ci['high']:,.2f}), +{data['expected_units_increment']:.0f} units and "
            f"{data['expected_waste_reduction']:.0f} fewer units wasted vs doing nothing. It is saved as draft "
            f"{data['draft_id']} and needs your approval. This is a simulation on synthetic data, not a guarantee."
        )
    if kind == "restock_plan":
        items = data["suggestions"]
        if not items:
            notes = " ".join(data.get("notes", []))
            return notes or f"No product at {data['store']['store_name']} needs an order today."
        first = items[0]
        urgent = sum(1 for i in items if i["is_urgent"])
        return (
            f"{len(items)} product(s) at {data['store']['store_name']} should be ordered today"
            f"{f' ({urgent} urgent)' if urgent else ''}. First: {first['suggested_qty']} x "
            f"{first['product']['product_name']} from {first['supplier']['supplier_name']}, arriving "
            f"{first['expected_arrival_date']}. Approve each card to prepare the supplier email."
        )
    if kind == "store_overview":
        k = data["kpis"]
        return (
            f"{data['store']['store_name']} sold ${k['total_sales']:,.0f} MXN in the last 7 days "
            f"({k['sales_change_pct']:+.1f}% vs the previous week). Run-outs: {k['run_outs']}, no demand: "
            f"{k['no_demand']}, products at waste risk: {k['waste_risk_products']}. Check the alerts below."
        )
    if kind == "demand_by_store":
        top = data["stores"][0]
        return (
            f"{data['product']['product_name']} sold {data['network_recent_units']:,.0f} units across the network "
            f"in the last {data['days']} days. {top['store']['store_name']} leads with {top['recent_units']:,.0f} "
            f"units ({top['change_pct']:+.1f}% vs the previous period)."
        )
    if kind == "portfolio":
        count = len(data["strategies"])
        if not count:
            return "No strategy passed the business rules for this store this week."
        return (
            f"I built a plan with {count} compatible strategies for {data['store']['store_name']}; total expected "
            f"net impact ${data['net_expected_impact']:,.2f} MXN. They are saved as drafts for your review."
        )
    if kind == "product_search":
        names = ", ".join(p["product_name"] for p in data["products"][:5])
        return f"I found: {names}. Which one do you mean?" if names else "I could not find that product."
    return "Done."


class OfflineCopilotModel(BaseChatModel):
    """Rule-based stand-in for Gemini with tool calling."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    catalog: Any = Field(description="domain.catalog.Catalog (typed as Any: it wraps a Protocol)")
    today: date
    tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "stockcopilot-offline"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> OfflineCopilotModel:  # type: ignore[override]
        names = [getattr(t, "name", None) or t.get("name") for t in tools]
        return self.model_copy(update={"tool_names": [n for n in names if n]})

    def _respond(self, messages: Sequence[BaseMessage]) -> AIMessage:
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            results = []
            for message in reversed(messages):
                if not isinstance(message, ToolMessage):
                    break
                results.append(summarize_tool_result(message.name or "", str(message.content)))
            return AIMessage(content=" ".join(reversed(results)))
        try:
            parsed = parse_intent(_last_human(messages), self.catalog, self.today)
        except NotFoundError as exc:
            return AIMessage(content=str(exc))
        if parsed is None or parsed[0] not in self.tool_names:
            return AIMessage(content=HELP)
        name, args = parsed
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}"}])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._respond(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._respond(messages)
        if message.tool_calls:
            # Name first, then arguments, like a real model stream (AG-UI emits START, then ARGS).
            call = message.tool_calls[0]
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="", tool_call_chunks=[{"name": call["name"], "args": "", "id": call["id"], "index": 0}]
                )
            )
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": None, "args": json.dumps(call["args"]), "id": None, "index": 0}],
                )
            )
            return
        text = str(message.content)
        words = re.findall(r"\S+\s*", text) or [text]
        for word in words:
            yield ChatGenerationChunk(message=AIMessageChunk(content=word))

"""Copilot Orchestrator: explicit LangGraph flow with human approval.

    START -> agent --tool calls--> tools -> track_drafts -> agent ...
             agent --answer, draft pending--> approval (interrupt) -> END
             agent --answer--> END

* `agent`: Gemini (or the offline model) reads intent, picks tools and explains their results.
* `tools`: deterministic analytics (`agent/tools.py`); results stream to the UI as components.
* `approval`: pauses the run with `interrupt()` until the manager approves, rejects or keeps the
  promotion draft; the decision is written to the draft store. The LLM never approves anything.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from ..domain.catalog import NotFoundError
from ..domain.drafts import DraftNotFoundError, DraftStore, InvalidTransitionError
from ..domain.services import Services
from .prompts import system_prompt
from .tools import TOOL_STRATEGY

log = logging.getLogger(__name__)


class CopilotState(MessagesState):
    selected_store_id: str | None
    pending_draft_id: str | None


def _store_label(svc: Services, store_id: str | None) -> str | None:
    if not store_id:
        return None
    try:
        store = svc.catalog.resolve_store(store_id)
    except NotFoundError:
        return store_id
    return f"{store.store_id} ({store.store_name}, {store.zone_type} zone)"


def _approval_payload(draft: Any) -> dict[str, Any]:
    s = draft.strategy
    return {
        "type": "promotion_approval",
        "draft_id": draft.draft_id,
        "title": s.recommended_action,
        "store_name": s.store.store_name,
        "product_name": s.product.product_name,
        "valid_from": s.valid_from.isoformat(),
        "valid_to": s.valid_to.isoformat(),
        "discount_pct": s.discount_pct,
        "expected_net_impact": s.expected_net_impact,
        "expected_sales_increment": s.expected_sales_increment,
        "expected_waste_reduction": s.expected_waste_reduction,
        "confidence_low": s.confidence_interval.low,
        "confidence_high": s.confidence_interval.high,
        "stockout_risk": s.stockout_risk,
    }


def _parse_decision(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {"decision": value}
    if not isinstance(value, dict):
        return "later", None
    decision = str(value.get("decision", "later")).lower()
    if decision not in ("approve", "reject", "later"):
        decision = "later"
    note = value.get("note")
    return decision, str(note) if note else None


def build_copilot_graph(
    svc: Services,
    drafts: DraftStore,
    model: BaseChatModel,
    tools: list[Any],
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    bound = model.bind_tools(tools)

    async def agent(state: CopilotState, config: RunnableConfig) -> dict[str, Any]:
        prompt = system_prompt(svc.as_of, _store_label(svc, state.get("selected_store_id")))
        response = await bound.ainvoke([SystemMessage(content=prompt), *state["messages"]], config)
        return {"messages": [response]}

    def track_drafts(state: CopilotState) -> dict[str, Any]:
        """Remember the draft created by the strategy tool so the run can stop for approval."""
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            if message.name != TOOL_STRATEGY:
                continue
            try:
                data = json.loads(str(message.content))
            except json.JSONDecodeError:
                continue
            if data.get("draft_id"):
                return {"pending_draft_id": data["draft_id"]}
        return {}

    def approval(state: CopilotState) -> dict[str, Any]:
        draft_id = state.get("pending_draft_id")
        if not draft_id:
            return {}
        try:
            draft = drafts.get(draft_id)
        except DraftNotFoundError:
            return {"pending_draft_id": None}
        if draft.status != "draft":
            return {"pending_draft_id": None}

        decision, note = _parse_decision(interrupt(_approval_payload(draft)))
        if decision == "later":
            text = f"Draft {draft_id} stays saved as a draft. You can approve it later from the plan."
        else:
            target = "approved" if decision == "approve" else "rejected"
            try:
                drafts.transition(draft_id, target, actor="store-manager", note=note)
                text = (
                    f'Draft {draft_id} approved: "{draft.title}" is scheduled from {draft.valid_from} to '
                    f"{draft.valid_to}."
                    if target == "approved"
                    else f"Draft {draft_id} rejected. Nothing will run."
                )
            except InvalidTransitionError as exc:
                text = str(exc)
        return {"pending_draft_id": None, "messages": [AIMessage(content=text)]}

    def route_agent(state: CopilotState) -> Literal["tools", "approval", "__end__"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        if state.get("pending_draft_id"):
            return "approval"
        return END

    graph = StateGraph(CopilotState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("track_drafts", track_drafts)
    graph.add_node("approval", approval)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_agent, ["tools", "approval", END])
    graph.add_edge("tools", "track_drafts")
    graph.add_edge("track_drafts", "agent")
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=checkpointer, name="stockcopilot")

from __future__ import annotations

import json


def _events(response_text: str) -> list[dict]:
    return [json.loads(line[5:]) for line in response_text.splitlines() if line.startswith("data:")]


def test_meta_and_envelope(client):
    body = client.get("/meta").json()
    assert set(body) == {"request_id", "data_as_of", "data"}
    assert body["data_as_of"] == "2026-09-16"
    assert body["data"]["llm"] == "offline"


def test_tendency_endpoint(client):
    body = client.get("/stores/S016/tendency", params={"product_id": "P059", "forecast_days": 7}).json()
    assert body["data"]["kind"] == "product_tendency"
    assert len(body["data"]["forecast_series"]) == 7


def test_errors_have_code_message_request_id(client):
    missing = client.get("/stores/S999/overview")
    assert missing.status_code == 404
    assert set(missing.json()) == {"code", "message", "request_id"}
    invalid = client.get("/stores/S016/tendency")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_request"


def test_prompt_strategy_then_approve(client):
    created = client.post(
        "/stores/S016/strategies:prompt", json={"prompt": "reducir merma del sandwich de jamón, máximo 30%"}
    ).json()["data"]
    draft_id = created["draft_id"]
    assert created["requires_approval"] is True and draft_id
    assert client.post(f"/drafts/{draft_id}/add-to-plan").json()["data"]["status"] == "selected"
    assert client.post(f"/drafts/{draft_id}/approve", json={"note": "ok"}).json()["data"]["status"] == "approved"
    conflict = client.post(f"/drafts/{draft_id}/reject")
    assert conflict.status_code == 409 and conflict.json()["code"] == "invalid_transition"
    plan = client.get("/stores/S016/plan", params={"from": "2026-09-17", "to": "2026-09-30"}).json()["data"]
    assert [d["draft_id"] for d in plan["drafts"]] == [draft_id]


def test_generate_plan_endpoint(client):
    plan = client.post("/stores/S016/strategies:generate").json()["data"]
    assert plan["kind"] == "portfolio"
    assert len(plan["strategies"]) <= 5


def test_copilot_streams_tool_call_and_pauses_for_approval(client):
    run = {
        "threadId": "t-approval",
        "runId": "r1",
        "state": {"selected_store_id": "S016"},
        "messages": [
            {"id": "m1", "role": "user", "content": "Estrategia para reducir merma del sandwich de jamón, 30%"}
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    events = _events(client.post("/copilot", json=run, headers={"accept": "text/event-stream"}).text)
    types = [e["type"] for e in events]
    assert "TOOL_CALL_START" in types and "TOOL_CALL_RESULT" in types
    start = next(e for e in events if e["type"] == "TOOL_CALL_START")
    assert start["toolCallName"] == "get_product_sell_strategy"
    interrupt = next(e for e in events if e["type"] == "CUSTOM" and e["name"] == "on_interrupt")
    payload = json.loads(interrupt["value"]) if isinstance(interrupt["value"], str) else interrupt["value"]
    assert payload["type"] == "promotion_approval"

    resume = {
        **run,
        "runId": "r2",
        "messages": [],
        "forwardedProps": {"command": {"resume": '{"decision": "approve"}'}},
    }
    client.post("/copilot", json=resume, headers={"accept": "text/event-stream"})
    draft = client.get(f"/drafts/{payload['draft_id']}").json()["data"]
    assert draft["status"] == "approved"
    assert draft["history"][-1]["actor"] == "store-manager"

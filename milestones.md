# StockCopilot — concurrent implementation milestones

This document divides the architecture into integrable deliverables. The goal is not to assign isolated areas: every milestone produces a verified contract that unlocks the next one. A small team can take multiple milestones in the same phase; a larger team can work on milestones within a phase concurrently.

## Collaboration rules

1. Do not change a shared contract without updating this file, the corresponding schema, and fixtures in the same pull request.
2. Identifiers are stable strings: `store_id`, `product_id`, `campaign_id`, and `draft_id`; dates use ISO `YYYY-MM-DD`; timestamps use UTC ISO-8601; money is MXN with two decimals.
3. The LLM does not calculate or persist economic decisions. Tools and the deterministic engine are the source of truth.
4. A promotion can reach `approved` only through an explicit user endpoint. The copilot creates `draft` only.
5. Work on milestone-prefixed branches: `m1-data/...`, `m2-api/...`, `m5-agent/...`. Avoid unrelated edits in another milestone's files.
6. Every endpoint and event needs a success fixture, an error fixture, and schema validation before integration.

## Shared contracts — frozen in phase 0

These contracts let the frontend, copilot, and engine work with mocks while real services are completed.

### Minimum entities

```text
StoreContext: store_id, as_of, inventory[], in_transit[], alerts[], constraints
Forecast: store_id, product_id, data_as_of, horizon[], p50, p80, p90, drivers[]
StrategyCandidate: strategy_type, store_id, products[], period, discount_pct, constraints, assumptions[]
StrategySimulation: candidate_id, baseline, treated, expected_units_increment,
                    expected_sales_increment, expected_margin_increment,
                    expected_waste_reduction, stockout_risk, confidence_interval,
                    violations[]
PromotionDraft: draft_id, status, strategy, simulation, created_at, requires_approval
Portfolio: store_id, period, strategies[], net_expected_margin_increment, conflicts[]
```

### Initial API

```text
GET  /stores/{store_id}/tendency?product_id=&history_days=&forecast_days=
POST /stores/{store_id}/strategies:generate
POST /stores/{store_id}/strategies:prompt
POST /drafts/{draft_id}/add-to-plan
POST /drafts/{draft_id}/approve
POST /drafts/{draft_id}/reject
GET  /stores/{store_id}/plan?from=&to=
```

Responses include `request_id`, `data_as_of`, and `data`. Errors use `code`, `message`, and `request_id`, and never expose internal exceptions.

### States and events

```text
draft -> selected -> approved -> scheduled -> active -> measured -> expired
draft -> rejected
```

Version events as `stock.updated.v1`, `promotion.draft_created.v1`, `promotion.approved.v1`, `promotion.measured.v1`, and `plan.regenerated.v1`. Producers must not assume which consumer receives an event.

---

## Phase 0 — common foundation (sequential, M0)

**Why sequential:** it unlocks compatible mocks and prevents teams from inventing different formats.

### M0. Contracts, environment, and demonstrable data

**Suggested owners:** technical lead and one data engineer.

**Deliverables**

- Create `contracts/` with JSON Schema or OpenAPI for the entities and endpoints above.
- Create `fixtures/` with one store, three SKUs, a forecast, a viable strategy, a rejected strategy, and a draft.
- Regenerate and validate the dataset with Docker; publish the generated `v2/README.md` dictionary.
- Define a demo `data_as_of` date within history and a 7/14-day horizon.
- Add `.env.example` without secrets and document a development command.

**Integration gate**

```bash
docker compose build dataset
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose run --rm dataset
```

Schemas must validate fixtures, and M1–M3 teams must be able to develop from those fixtures without AWS services.

---

## Phase 1 — parallel foundations (M1, M2, M3)

All milestones depend on M0 but not on one another. They use frozen contracts and fixtures.

### M1. Curated data and analytics layer

**Suggested owner:** data engineer / ML engineer.

**Build**

- Ingest CSVs to S3 Raw and transform them into date-partitioned S3 Curated data.
- Curate sales, daily inventory, tickets, campaigns, calendar/weather, catalog, and supplier tables.
- Add Athena queries for trend, promotion history, affinity, and expiry risk.
- Add quality tests for foreign keys, inventory-sales balance, nulls, duplicates, and time-based train/test separation.

**Delivered contract:** read-only adapters equivalent to `get_historical_insights` and `get_current_store_context`, using M0 response fixtures.

**Do not modify:** API contracts or decision-engine business logic.

### M2. Operational API, persistence, and approval

**Suggested owner:** backend engineer.

**Build**

- API Gateway and Lambda handlers for M0 endpoints.
- DynamoDB for current stock, alerts, drafts, plans, approval audit, and store configuration.
- Promotion state machine that rejects invalid transitions.
- Idempotency for `approve`, `reject`, and operational events; record `request_id`.

**Delivered contract:** a functional API backed by in-memory repositories or DynamoDB that returns M0 fixtures before ML integration.

**Do not modify:** Curated tables or model calculations.

### M3. Dashboard and decision UX

**Suggested owner:** frontend engineer / designer.

**Build**

- Store and period selectors; KPIs; historical series plus P50/P80/P90; alerts; demand map/list.
- Up to five strategy cards showing baseline vs. treated, individual impact, and portfolio total.
- Prompt flow, draft card, add-to-plan, approve/reject, and visible states.
- Typed HTTP client generated from OpenAPI or validated against fixtures.

**Delivered contract:** a fully navigable interface using fixture data and no financial calculations in the frontend.

**Do not modify:** response formats or other teams' infrastructure files.

**Phase 1 gate:** a local demo can show data, a viable strategy, and a rejected strategy using only M0 contracts.

---

## Phase 2 — decision intelligence (M4, M5, M6)

M4 and M5 may begin with M0 fixtures. M6 requires M2 draft persistence. Final integration of all three requires M1.

### M4. Forecast and analytical models

**Suggested owner:** ML engineer.

**Build**

- `date × store × SKU` baseline-demand features: lagged demand, calendar, weather, zone, and product attributes; exclude treated promotions and post-treatment fields.
- Probabilistic P50/P80/P90 CatBoost model with four fixed 14-day rolling backtests against a seasonal-naive baseline.
- Ticket-based affinity; keep business rules distinct from observed affinity.
- Initial uplift from treatment/control campaigns; report uncertainty and exclude post-treatment features.
- SageMaker CatBoost Training Job, Model Registry quality gate, and daily Processing Job that publishes the exact `Forecast` contract to DynamoDB; use the API adapter rather than a permanently running endpoint.

**Gate:** backtest metrics are recorded and inference is reproducible for the demo `data_as_of`.

### M5. Deterministic engine and optimizer

**Suggested owner:** backend / operations-research engineer.

**Build**

- Candidate generator limited to affinity, waste-rescue, and contextual templates.
- Daily inventory simulation: baseline vs. treated, discount, cost, waste, stockout, and cannibalization.
- Validator for minimum margin, inventory, dates, shelf life, maximum discount, budget, and conflicts.
- Portfolio optimizer that calculates joint impact instead of blindly summing cards.
- Deterministic tests for viable cases, negative margin, stockout, expiry, and conflicting strategies.

**Delivered contract:** `simulate_strategy`, `optimize_portfolio`, and M0 `StrategySimulation`/`Portfolio` responses.

**Rule:** the engine does not call the LLM.

### M6. Copilot Orchestrator — LangGraph + Bedrock

**Suggested owner:** AI/backend engineer.

**Build**

- Graph: `intent -> context -> forecast_insights -> candidates -> simulate -> validate -> optimize -> response -> approval_wait -> persist`.
- LangChain `ChatBedrockConverse` for interpretation and explanation; validate output JSON against schema.
- Least-privilege tools for tendency, context, forecast, insights, constraints, simulation, optimization, and drafts.
- `get_product_tendency` and `get_product_sell_strategy` responses showing tool-produced numbers.
- Checkpoints by `conversation_id`/`draft_id`, idempotent retries, and prompt-injection protection for tool parameters.

**Delivered contract:** the orchestrator first works with M0/M5 mocks and can later replace adapters without a frontend change.

**Rule:** do not use Bedrock Agents Classic. On AWS, deploy an ARM64 LangGraph container to AgentCore Runtime using HTTP (`/ping`, `/invocations`), authentication, and a least-privilege IAM role.

**Phase 2 gate:** a natural-language request creates a traceable draft with baseline/treated scenarios, constraints, and `requires_approval: true`.

---

## Phase 3 — operational integration (M7, M8)

These milestones require M1, M2, M4, and M5. M7 and M8 can be built in parallel once contracts return real data.

### M7. Automation and learning loop

**Suggested owner:** backend/cloud engineer.

**Build**

- EventBridge for `stock.updated.v1`, daily plan generation, alerts, and weekly retraining.
- Measurement flow after a promotion ends: sales, margin, waste, stockouts, acceptance, and control group.
- Raw/Curated persistence of outcomes and evaluation-dataset updates.
- Retries, DLQ, and event observability.

**Gate:** an approval emits an event, changes promotion state, and leaves a measurable result.

### M8. Experience integration and security

**Suggested owners:** frontend, backend, and cloud engineers.

**Build**

- Connect M3 dashboard to live endpoints and replace fixtures incrementally using feature flags.
- Role-based authentication/authorization; a manager approves only for assigned stores.
- Telemetry for `request_id`, latency, tool errors, forecast quality, and strategy outcome.
- Agent JSON validation, rate limiting, and message sanitization.

**Gate:** end-to-end journey: tendency query → prompt strategy → simulation → draft → approval → updated plan.

---

## Phase 4 — hackathon closeout (M9, sequential)

### M9. Rehearsal, evidence, and demo

**Suggested owner:** entire team, with one integration owner.

1. Run Docker regeneration and all data validations.
2. Run contract, engine, and API tests; freeze model and dataset versions.
3. Prepare two narratives: preventing a stockout and rescuing waste/increasing basket value.
4. Show a rejected strategy and explain the constraint; this proves control rather than mere generation.
5. Show portfolio impact, not only a single card.
6. Document limits: synthetic data, uplift uncertainty, and mandatory human approval.

**Definition of done:** the demo runs from one reproducible configuration, every number has a traceable source, and no promotion can activate without approval.

## Dependency map

```text
M0
├── M1 Curated data ──────────────┐
├── M2 API/Dynamo ───────┐        │
├── M3 Dashboard ────────┼────┐   │
├── M4 Forecast ─────────┼────┼───┤
├── M5 Engine/optimizer ──┼────┼───┤
└── M6 LangGraph ─────────┘    │   │
                              M7  M8
                               └──┬──┘
                                  M9
```

M3 can demonstrate the experience in phase 1; M4/M5/M6 make it intelligent; M7/M8 connect it to the operational cycle. The integration owner reviews contracts at the end of each phase before opening the next one.

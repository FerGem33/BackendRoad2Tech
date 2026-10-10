# StockCopilot — functional flow

## Two usage modes

### 1. Automatic commercial plan

The interface loads a plan for a store and period, by default the next seven days. It displays up to five eligible strategies prioritized by expected net impact.

The system must not force five profitable promotions. If only three opportunities meet the rules, it returns three and explains why.

Each card includes:

```text
strategy type
target products and store
validity period
proposed action, price, or discount
expected incremental sales
expected incremental margin
waste avoided, when applicable
stockout risk
uncertainty or confidence interval
explanation and constraints considered
```

### 2. On-demand strategy through the copilot

A manager can request a specific action, for example:

> “I want to increase energy-drink sales this Saturday without giving more than a 10% discount.”

The copilot turns the request into an objective and constraints, queries current and historical data plus the forecast, creates alternatives within those bounds, and sends them to simulation. The resulting strategy is shown as a draft that can be added to the plan.

## Operational data flow

```text
Sale / receipt / inventory adjustment
  -> operational event
  -> current-stock update
  -> historical persistence
  -> reevaluation when the event changes a relevant decision
```

Events that warrant recommendation reevaluation:

- low inventory or a merchandise receipt;
- a product nearing expiry;
- delayed or incomplete order;
- unusually high sales;
- meaningful weather or event change;
- promotion start or end;
- explicit manager request.

The model is not retrained after each sale. Sales update the current context; retraining runs periodically.

## Automatic plan generation

```text
1. Load current store context.
2. Retrieve the baseline forecast for the horizon.
3. Detect opportunities: excess inventory, waste, affinity, events, and demand growth/decline.
4. Create candidates from valid strategy templates.
5. Simulate the incremental impact of every candidate.
6. Reject infeasible candidates.
7. Optimize a portfolio of compatible strategies.
8. Save and present the prioritized strategies.
```

### Initial templates

| Template | Objective | Example |
| --- | --- | --- |
| Affinity bundle | Increase items per ticket | Coffee + pastry; beer + snack |
| Waste-rescue bundle | Avoid waste and recover margin | Popular product + product nearing expiry |
| Contextual promotion | Increase volume based on context | Cold drink during a heat wave; snack before a match |

## Impact simulation

Each strategy compares the baseline against a treated scenario:

```text
incremental impact = result with strategy - result without strategy
```

The calculation estimates at least:

```text
additional units
incremental revenue
incremental margin
discount or subsidy cost
waste avoided
expected cost of additional stockouts
```

A strategy is eligible only when it follows inventory and business rules:

- positive net margin or an explicit objective justification;
- sufficient available and in-transit inventory;
- no promoted SKU with high stockout risk;
- respect for shelf life, maximum discount, budget, and dates;
- respect for store operating capacity;
- no incompatibility with another active offer.

## Portfolio optimization

The total shown to the manager must be recalculated whenever a strategy is selected, removed, or added. Individual impacts must not be summed without conflict validation.

Examples of conflicts:

- two strategies use the same SKU, store, and time window;
- two offers consume more product than is available;
- a discount cannibalizes a more profitable bundle;
- a waste-rescue offer competes with a contextual promotion.

The optimizer maximizes expected incremental margin subject to these constraints. A diversity constraint is recommended so the plan does not contain five strategies for the same product or category.

## Copilot Orchestrator and LLM role

LangGraph controls request state, approvals, retries, and draft continuation. LangChain connects the graph to Amazon Bedrock through `ChatBedrockConverse`. The LLM interprets intent and explains results, while SageMaker and the deterministic engine calculate forecasts, risks, and economic impact.

The copilot may access data only through safe tools:

```text
get_current_store_context(store_id)
get_demand_forecast(store_id, horizon)
get_historical_insights(store_id, filters)
get_business_constraints(store_id)
simulate_strategy(strategy)
optimize_portfolio(candidates)
create_promotion_draft(strategy)
```

The request journey is:

```text
intent -> context -> forecast/insights -> candidates
-> simulation -> validation -> optimization -> JSON response
-> human approval -> save a draft or approved promotion
```

The copilot may propose a structured strategy but cannot declare it viable by itself. It must always pass through `simulate_strategy`, and through the optimizer when it affects the current plan.

Two product-facing facades keep the frontend independent from internal tools:

- `get_product_tendency`: returns historical series, P50/P80/P90 forecast, trend, and risks; Bedrock only creates the summary.
- `get_product_sell_strategy`: composes context, forecast, history, candidates, simulation, validation, and a draft; it always compares a baseline and treated scenario.

## Approval and learning

```text
GENERATED -> SIMULATED -> DRAFT -> ADDED_TO_PLAN
    -> APPROVED / REJECTED -> ACTIVE -> MEASURED -> LEARNING
```

After a strategy, the system stores actual sales, margin, bundle units, waste, stockouts, approval/rejection, and context. Those results feed evaluation and future training cycles.

## Suggested cadence

| Process | Cadence |
| --- | --- |
| Stock update | Per event |
| Historical record | Per event |
| Alert review | Per relevant event or hourly |
| Operational forecast | Daily; hourly in a demo if needed |
| Baseline plan generation | Daily or on demand |
| Model retraining | Weekly |
| Promotion evaluation | Daily or weekly |

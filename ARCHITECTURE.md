# StockCopilot — proposed architecture

## Overview

```text
Operational sources
  POS / tickets, stock, catalog, suppliers, weather, events
                         |
                         v
        API Gateway (request) + EventBridge (event/schedule)
               |                    |
               v                    v
      DynamoDB (current state)   S3 Raw (history)
               |                    |
               |                    v
               |            ETL: Lambda / Glue
               |                    |
               |                    v
               |           S3 Curated / features ----> Athena
               |                    |
               |                    v
               +--------> SageMaker AI
                           forecast / affinity / uplift
                                      |
                                      v
                    Simulation engine and optimizer
                                      |
                 +--------------------+-------------------+
                 |                                        |
                 v                                        v
      Automatic plan: up to 5 strategies       Prompt-requested strategy
      per store and period                      from the copilot
                 |                                        |
                 +--------------------+-------------------+
                                      |
                                      v
          Experience API + Copilot Orchestrator
             LangChain / LangGraph + Amazon Bedrock
                                      |
                                      v
                   Dashboard / store manager
                                      |
                                      +--> approval, execution, and feedback
                                              -> EventBridge / DynamoDB / S3 Raw
```

## Components and responsibilities

### Operational sources

| Source | Main data |
| --- | --- |
| POS and tickets | Sale, SKU, quantity, price, discount, ticket, applied promotion |
| Inventory | Current stock, received, in transit, waste, adjustments, and expiry |
| Catalog and costs | Product, category, cost, price, case size, and shelf life |
| Suppliers | Lead time, deliveries, and fulfillment |
| External context | Weather, calendar, seasonality, and local events |

### Amazon API Gateway

Exposes synchronous endpoints for the frontend and operational systems: retrieve a plan or forecast, request a custom strategy, add it to a plan, and approve or reject a draft.

One API can expose two logical routes:

- **operational ingestion:** validated input for sales, receipts, and adjustments;
- **experience:** endpoints consumed by the dashboard and copilot.

### Amazon EventBridge

Asynchronous event bus that decouples a sale or receipt from follow-up work: current-stock updates, historical persistence, alert checks, and recommendation reevaluation. It also schedules daily and weekly jobs.

**It does not replace Lambda:** EventBridge routes or schedules; Lambda runs code. For the MVP, API Gateway may invoke Lambdas directly and reduce internal events, but EventBridge should remain for the daily plan, weekly retraining, and work that must not be coupled to a user request.

### Amazon DynamoDB

Low-latency operational store for:

```text
stock by store-SKU
orders in transit
active alerts
recommendations and strategy drafts
active commercial plan by store and period
approval/activation state
store configuration and constraints
```

It is not the long-term analytical repository; complex historical queries come from S3 Curated and Athena.

### Amazon S3 Raw

Immutable, date/source-partitioned historical data lake. It preserves original events and makes transformations reproducible and datasets rebuildable.

### ETL: AWS Lambda / AWS Glue

ETL validates, normalizes, joins, and enriches sources. It produces the `date × store × SKU` table and the ticket table. Lambda with Python/Pandas is sufficient for the hackathon; AWS Glue is the scalable option.

### Amazon S3 Curated and Athena

S3 Curated stores cleaned tables and derived features ready for ML and analysis. Athena supports historical SQL queries such as most profitable promotions, waste by category, product affinity, and store behavior.

### Amazon SageMaker AI

Responsible for training, evaluating, and serving ML artifacts:

- probabilistic baseline-demand forecast with CatBoost (P50/P80/P90);
- product affinity from tickets;
- promotion impact/uplift model when the dataset supports it;
- time-based backtesting and comparison with the active model.

Forecast inference can run in a daily batch. The decision engine combines it with current stock to generate dynamic recommendations without retraining after every sale.

The implemented forecast path uses a SageMaker Training Job with a custom CatBoost image, four fixed 14-day backtests, and a Model Registry package only after CatBoost beats the seasonal-naive benchmark. An EventBridge Scheduler starts a daily SageMaker Processing Job; its validated `store × SKU × 14-day` P50/P80/P90 output is published atomically to DynamoDB. The Copilot API reads DynamoDB rather than invoking a permanent endpoint. This removes idle endpoint cost while keeping forecast lookups fast and deterministic.

### Decision engine and optimizer

Deterministic service, for example Lambda/Python, that receives forecast, current state, constraints, and candidates. It must:

1. Project inventory over the horizon.
2. Calculate stockout and waste risk.
3. Simulate the incremental impact of every strategy.
4. Remove infeasible strategies.
5. Select the portfolio that maximizes net impact without double counting.

The engine is the source of truth for economic values. The LLM does not perform these calculations.

### Strategy planner

The planner builds the **automatic commercial plan** that always appears in the interface. It runs daily, after a material signal change, or when the manager requests regeneration.

1. Retrieve current context, baseline forecast, and historical insights.
2. Create candidates from allowed templates: affinity bundle, waste-rescue bundle, and contextual promotion.
3. Send every candidate to the simulation engine.
4. Send viable candidates to the portfolio optimizer.
5. Store up to five prioritized strategies for the store and period.

The planner must not force five promotions. If fewer opportunities are profitable and feasible, it returns fewer.

### Copilot Orchestrator — LangChain/LangGraph + Amazon Bedrock

LangGraph explicitly controls conversation state and transitions; LangChain integrates the Amazon Bedrock model through `ChatBedrockConverse`. Bedrock interprets intent, selects allowed tools, and writes responses, but it is not the source of truth for calculations.

For a new AWS deployment, package the graph as an ARM64 container and run it in **Amazon Bedrock AgentCore Runtime**. The recommended initial protocol is HTTP: `GET /ping` for health and `POST /invocations` for dashboard requests. API Gateway continues to provide authentication, rate limits, and the public contract. For a local demo, run the same graph behind a Python API. Do not use Bedrock Agents Classic, which is in maintenance mode for new customers.

The minimum graph is:

```text
INTENT
  -> FETCH_DATA
  -> FORECAST_AND_INSIGHTS
  -> GENERATE_CANDIDATES
  -> SIMULATE
  -> VALIDATE
  -> OPTIMIZE_PORTFOLIO
  -> RESPOND_AS_JSON
  -> WAIT_FOR_APPROVAL
  -> SAVE_DRAFT
```

Data, simulation, and validation nodes are deterministic functions. LangGraph checkpoints allow a draft to resume, support safe retries, and separate human approval from strategy creation.

The frontend must never execute arbitrary HTML or JavaScript generated by the model. The contract is JSON validated against a known component schema.

The copilot has two complementary functions:

- **automatic-plan curation:** summarizes and explains strategies selected by the engine;
- **on-demand strategy:** turns a request such as “increase energy-drink sales on Saturday with no more than a 10% discount” into filters and constraints, creates candidates, and requests simulation.

A prompt-created strategy starts as a draft. When it is added to the plan, the optimizer recalculates full portfolio impact to avoid double counting and cannibalization.

The Runtime uses one least-privilege IAM identity, and every tool validates parameters. The LLM does not receive broad direct access to S3, Athena, or DynamoDB. Production requires VPC configuration when private resources are needed, inbound authentication, CloudTrail, and encrypted CloudWatch logs with limited retention.

### Dashboard and human approval

The dashboard displays KPIs, strategies, simulations, and alerts. The manager may activate or remove a plan strategy, but a promotion requires explicit approval before execution. Its outcome returns as an event to the learning cycle.

## Agent tools

The copilot retrieves information through least-privilege APIs or functions, never through unrestricted direct access to DynamoDB or S3:

```text
get_current_store_context(store_id)
get_demand_forecast(store_id, horizon)
get_historical_insights(store_id, filters)
get_business_constraints(store_id)
simulate_strategy(strategy)
optimize_portfolio(candidates)
create_promotion_draft(strategy)
```

Two experience functions compose those tools without expanding permissions:

```text
get_product_tendency(store_id, sku, history_days, forecast_days)
  -> Athena/S3 Curated history + SageMaker forecast
  -> deterministic trend, risk, and P50/P80/P90 series
  -> Bedrock writes only ai_summary

get_product_sell_strategy(store_id, objective, constraints)
  -> context -> forecast -> insights -> allowed candidates
  -> simulator -> validator -> optimizer -> create_promotion_draft
```

The strategy response explicitly includes `baseline` and `treated` series, incremental units/sales/margin, waste avoided, stockout risk, uncertainty, assumptions, `draft_id`, and `requires_approval: true`. It must never present impact as guaranteed.

## Strategy-generation flows

### Automatic interface plan

```text
EventBridge Scheduler or “regenerate plan” action
  -> Strategy Planner Lambda
  -> current context (DynamoDB)
  -> forecast/impact (SageMaker)
  -> history and affinity (Athena/S3 Curated)
  -> simulator + optimizer (Decision Engine Lambda)
  -> save plan of up to five strategies in DynamoDB
  -> dashboard retrieves the plan through API Gateway
```

### Prompt-requested strategy

```text
Manager enters an intent in the dashboard
  -> API Gateway + Copilot Orchestrator (LangGraph)
  -> Bedrock converts intent into objective/constraints
  -> context, forecast, and history tools
  -> allowed candidates -> simulator -> validator -> optimizer
  -> strategy card and comparison against the active plan
  -> manager adds, discards, or adjusts it
  -> draft/approval persisted in DynamoDB
```

The total shown to the manager is always `net impact of the selected portfolio`; it is never a blind sum of individual impacts.

## Lambda networking and subnets

A Lambda function **is not a subnet**. It can be configured to use private subnets in a VPC and a security group, normally across two Availability Zones. This is needed only for private resources such as a private database or internal endpoint.

Reference configuration when a VPC is required:

```text
VPC
  Private app subnet AZ-a  -> API, copilot, and decision-engine Lambdas
  Private app subnet AZ-b  -> API, copilot, and decision-engine Lambdas
  VPC endpoints            -> S3, DynamoDB, and required private endpoints
  NAT Gateway (optional)   -> only if a Lambda needs Internet egress
```

API Gateway remains outside subnets and invokes Lambda. S3 and DynamoDB are regional services; they are not deployed inside a subnet. For a hackathon, a VPC is not required and adds configuration. Include it only to demonstrate network segmentation or connect private resources; otherwise, Lambda outside a customer VPC reduces complexity.

## Security and control

- Least-privilege IAM per service and function.
- Encryption in transit and at rest for data and logs.
- Validate every tool parameter supplied by the copilot.
- The copilot can create drafts only; activating a promotion requires user authorization.
- Record inputs, decisions, simulations, and approvals for audit.
- Keep current operational data separate from historical analytical data.
- When Lambda uses a VPC, use private subnets, least-privilege security groups, and private endpoints before opening Internet egress through NAT.

## MVP limits

- Forecast and simulation may run on a synthetic or expanded dataset.
- Without real tickets, affinity must be presented as simulated or inferred from generated data, not as observed real behavior.
- Without historical promotion experiments, initial uplift is a simulation based on causal generator rules. A production version must validate it through A/B tests or causal techniques.
- Promotion activation may be simulated; the approval and measurement flow remains valid.

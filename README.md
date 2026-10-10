# StockCopilot — project context

## Purpose

StockCopilot is a commercial and operational copilot for a convenience-store chain. It turns sales, inventory, external context, and historical behavior into decisions that a store manager can understand, simulate, and approve.

The proposal goes beyond demand prediction. Its goal is to answer **which concrete action is worth taking now** to protect availability, raise basket value, and maximize expected margin.

> Working name: **StockCopilot**. The chain and demonstration data are fictional; do not use a real chain's brand in the final product without authorization.

## Language convention

All technical documentation, source code, identifiers, comments, docstrings, logs, and technical UI strings are written in English. Synthetic business data may retain Spanish names where they represent Mexican products, places, or local events.

## Problems addressed

1. **Stockouts:** sales are lost when a product is unavailable.
2. **Waste and excess inventory:** slow-moving or perishable products may expire.
3. **Missed commercial opportunities:** combinations, events, and local context are not used to increase basket value and margin.

## Value proposition

Each store starts with a plan of up to five simulated and prioritized commercial strategies. The manager can inspect their economic impact, add or remove strategies, request specific alternatives from the copilot, and approve only the ones to execute.

Recommendations can include:

- replenishment prioritized by margin at risk;
- **affinity bundles** to increase items per ticket;
- **waste-rescue bundles** that pair a high-demand anchor with a product at risk of remaining unsold or expiring;
- contextual promotions based on weather, event, time, or season, only when profitable and inventory-feasible.

## Design principles

- ML models and the optimizer produce numbers; the LLM interprets and explains them.
- No offer is recommended if it creates an unacceptable stockout risk, violates minimum margin, or exceeds available inventory.
- Impact is measured as **incremental**: expected result with the strategy minus the baseline without it.
- The impact displayed for a plan is the full **portfolio** impact, not a naïve sum of incompatible cards.
- The manager keeps final approval. AI creates drafts and recommendations; it never activates a promotion by itself.

## Primary metrics

- expected incremental sales and margin;
- average ticket value and items per ticket;
- units sold through cross-sell and bundles;
- waste avoided and recovered margin;
- sales and margin at risk from stockouts;
- forecast accuracy;
- recommendation acceptance and actual outcome.

## Required data

The forecast grain is `date × store × SKU`:

```text
sales, price, cost, promotion, opening/closing inventory,
inventory in transit, stockout_hours, expiry, supplier,
weather, event, calendar, location, and store type
```

Affinity and cross-sell additionally require ticket lines:

```text
ticket_id, store_id, timestamp, sku, quantity, unit_price,
discount, promotion_id
```

The repository contains a synthetic hackathon dataset. It may be extended or replaced with custom synthetic data as long as causal relationships remain plausible: weather/event demand, promotion impact, expiry, waste, and inventory constraints.

## Set up and validate the dataset

From the `BackendRoad2Tech` directory, create an isolated environment and install generator dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/dataset/validate.py
```

Regenerate every synthetic file in `public/softtek/dataset/dataset-hackathon-road2tech/v2/`—including daily inventory, campaigns, and tickets—with:

```bash
python scripts/dataset/run_all.py
```

### Generate with Docker

Docker makes the generator independent of the local Python version and installed packages. Build the image and run it with:

```bash
docker compose build dataset
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) docker compose run --rm dataset
```

The volume in `compose.yaml` writes generated CSVs directly to `public/softtek/dataset/dataset-hackathon-road2tech/v2/`. Validate that output only with:

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
  docker compose run --rm dataset python scripts/dataset/validate.py
```

This image is only for the dataset generator; it contains no credentials, AWS services, or web application. The generated `v2/README.md` documents the data dictionary and simulation assumptions. Brand names are used only to make the demo readable; all data is fictional.

## AI approach

1. **Probabilistic forecast:** CatBoost or LightGBM predicts store-SKU demand by horizon using P50/P80/P90 quantiles.
2. **Product affinity:** ticket analysis identifies complementary products and cross-sell opportunities.
3. **Strategy simulator:** compares baseline and promotional scenarios to estimate uplift, margin, waste avoided, and stockout risk.
4. **Portfolio optimizer:** selects compatible strategies subject to inventory, margin, time, and budget constraints.
5. **Copilot Orchestrator:** LangGraph controls conversation and approvals; LangChain uses Amazon Bedrock through `ChatBedrockConverse`. The LLM calls controlled tools and returns structured explanations/UI; it does not invent calculations or write changes without human approval.

## Related documents

- [FLOW.md](./FLOW.md): functional journey, strategy generation, and feedback.
- [ARCHITECTURE.md](./ARCHITECTURE.md): AWS components, responsibilities, and interfaces.
- [milestones.md](./milestones.md): phases, contracts, and ownership for concurrent implementation.
- [AWS architecture diagram (SVG)](./docs/stockcopilot-aws-architecture.svg): editable vector diagram with services, flows, and solution boundaries.
- [AWS architecture diagram (PNG)](./docs/stockcopilot-architecture.png): presentation-ready diagram.

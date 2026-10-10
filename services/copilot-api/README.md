# StockCopilot Copilot API

Python service behind the StockCopilot chat and dashboard (milestones **M2**, **M5** and **M6**).
It answers store-manager questions with **generative UI**: every answer is a typed JSON component
(trend chart, strategy simulation, restock list, approval card) that the frontend renders. The
frontend lives in [`road2tech-frontend`](https://github.com/fernandovmedina/road2tech-frontend)
(`/copilot`, CopilotKit v2 over AG-UI).

- **FastAPI**: REST endpoints (contract from [`milestones.md`](../../milestones.md)) + the AG-UI endpoint.
- **LangGraph**: explicit copilot graph with human approval (`interrupt`) and checkpoints.
- **Gemini** (Google AI Studio key) through `langchain-google-genai`. Without a key it runs **offline**
  (keyword intent parser + templated answers), so the demo and the tests work with no network.
- **Data**: the generated `public/softtek/dataset/dataset-hackathon-road2tech/v2` folder, read
  directly from CSV or loaded into **PostgreSQL**.
- **AWS Lambda** serves the short REST operations (`lambda_handler.py`, `template.yaml`). The agent
  runs as a persistent container, because it streams and resumes approvals.

> Principle (from `ARCHITECTURE.md`): deterministic code produces every number; the LLM only reads
> intent, picks tools and explains results. The agent can only create **drafts**; approving a
> promotion always needs an explicit user action.

## Architecture

```
Frontend /copilot (CopilotKit v2 chat + tool renderers + approval card)
   │  AG-UI (CopilotKit runtime → LangGraphHttpAgent)
   ▼
Copilot API (FastAPI)  ── persistent container (ECS / App Runner / Bedrock AgentCore Runtime)
   ├── POST /copilot ─────────── LangGraph copilot graph
   │                               agent (Gemini) ⇄ tools → track_drafts → agent
   │                               └─ approval (interrupt) → draft approved / rejected / kept
   └── REST /stores/... /drafts/... ── also deployable as Lambda behind API Gateway
        │
        ├── get_product_tendency ──── history + forecast P50/P80/P90 + trend + run-out/waste risk
        ├── get_product_sell_strategy  deterministic LangGraph pipeline:
        │      get_current_store_context → get_demand_forecast → get_historical_insights
        │      → get_business_constraints → generate_strategy_candidates → simulate_strategy
        │      → validate_strategy → select_strategy → create_promotion_draft
        ├── get_restock_suggestions ── the dataset's best-supplier rule
        ├── get_store_overview / get_demand_by_store / generate_store_plan / search_products
        │
        ├── DataRepository: CsvRepository (v2 folder) | PostgresRepository (schema.sql)
        ├── DraftStore: in-memory | PostgreSQL (promotion_drafts)
        └── Checkpointer: memory | PostgreSQL (AsyncPostgresSaver)
```

How this maps to the AWS design in [`ARCHITECTURE.md`](../../ARCHITECTURE.md):

| AWS component | Here (MVP) | Later |
|---|---|---|
| Athena / S3 Curated | `DataRepository` (CSV or PostgreSQL) | an Athena adapter implementing the same protocol |
| SageMaker forecast | `domain/forecast.py` fallback, `JsonlForecastProvider` (local CatBoost output), or `DynamoForecastProvider` (published P50/P80/P90) | SageMaker training and daily Processing Job |
| Uplift / affinity models | `domain/uplift.py` (campaign diff-in-diff), `data/build_affinity.py` (tickets) | SageMaker uplift / affinity models |
| Decision engine Lambda | `domain/strategy/*`, `domain/restock.py` | same code in Lambda |
| DynamoDB drafts | `DraftStore` (memory / PostgreSQL) | a DynamoDB store with the same methods |
| Bedrock (`ChatBedrockConverse`) | Gemini (`ChatGoogleGenerativeAI`) | change `agent/llm.py` only |
| AgentCore Runtime | `Dockerfile` (port 8080, `/ping`, `/invocations`) | deploy the same image |

## Run locally

```bash
# 1. Generate the dataset (repository root; ~9 min) and the basket-affinity table (~30 s)
python scripts/dataset/run_all.py
cd services/copilot-api
uv sync                                                  # Python 3.12+
uv run python -m stockcopilot.data.build_affinity        # writes v2/product_affinity.csv

# 2. Run the API (http://localhost:8000, docs at /docs)
cp .env.example .env                                     # optionally set GOOGLE_API_KEY
uv run python -m stockcopilot
```

Then start the frontend (`pnpm dev` in `road2tech-frontend`, with `COPILOT_API_URL=http://localhost:8000`)
and open http://localhost:3000/copilot. The CSV adapter loads the 3M sales rows in about 2 s.

### With PostgreSQL

```bash
uv run python -m stockcopilot.data.load_postgres --database-url postgresql://...   # builds affinity if missing
DATA_BACKEND=postgres CHECKPOINTER=postgres DATABASE_URL=postgresql://... uv run python -m stockcopilot
```

Or run all three (database, loader and API) with `GOOGLE_API_KEY=... docker compose up --build`
inside this folder. The API is then on port 8000.

### Use the trained CatBoost forecast in the API

The local forecast job writes `../../artifacts/forecast/forecasts/forecasts.jsonl` relative to this directory. After it finishes and passes its quality gate, run the API with that file:

```bash
FORECAST_PROVIDER=jsonl \
FORECAST_FILE=../../artifacts/forecast/forecasts/forecasts.jsonl \
DEMO_AS_OF=2026-09-16 \
uv run python -m stockcopilot
```

For Docker Compose, set `FORECAST_PROVIDER=jsonl` when starting the stack; the artifact directory is mounted read-only at `/artifacts/forecast`. On PowerShell, set the environment variable with `$env:FORECAST_PROVIDER="jsonl"` before `docker compose up --build`. The forecast file must exist before API startup. Restart the API after retraining to load a new file.

The provider validates all dates, quantiles, duplicate store/SKU keys, and the configured demo cutoff at startup. It never silently falls back to the deterministic baseline when the CatBoost artifact is missing or invalid. The same forecast then feeds tendency charts, stockout/waste risk, restock suggestions, and strategy simulations through `Services.forecast()`. The JSONL path is for a local demo; the deployed API continues to use the DynamoDB provider.

### AWS Lambda (REST only)

```bash
sam build && sam deploy --guided   # parameters: DatabaseUrl, DemoAsOf
```

`template.yaml` deploys `stockcopilot.lambda_handler.handler` (arm64, Python 3.12) behind an HTTP API
with `DATA_BACKEND=postgres`. The streaming agent (`/copilot`) is not mounted in Lambda.

## Endpoints

Success bodies are `{request_id, data_as_of, data}`; errors are `{code, message, request_id}`.

| Method | Path | What it does |
|---|---|---|
| GET | `/health`, `/ping` | Health checks (`/ping` for AgentCore) |
| GET | `/meta` | Data range, demo date, data backend, LLM in use |
| GET | `/stores`, `/products?q=&category_id=`, `/categories`, `/suppliers` | Catalog |
| GET | `/stores/{store_id}/overview?period_days=` | KPIs (sales, run-outs, no demand), movers, alerts |
| GET | `/stores/{store_id}/tendency?product_id=&history_days=&forecast_days=` | `get_product_tendency` (Gemini writes `ai_summary` when configured) |
| GET | `/stores/{store_id}/restock?product_id=&limit=` | Restock suggestions with a supplier email draft |
| GET | `/products/{product_id}/demand-by-store?days=` | One product's demand across the 25 branches |
| POST | `/stores/{store_id}/strategies:generate` | Automatic plan: up to 5 compatible strategies (saved as drafts) |
| POST | `/stores/{store_id}/strategies:prompt` | `get_product_sell_strategy` from fields or a natural-language `prompt` |
| GET | `/drafts?store_id=&status=`, `/drafts/{draft_id}` | Promotion drafts |
| POST | `/drafts/{draft_id}/add-to-plan` \| `/approve` \| `/reject` | State machine `draft → selected → approved`, `→ rejected` |
| GET | `/stores/{store_id}/plan?from=&to=` | Selected/approved strategies in a period |
| POST | `/copilot` (and `/invocations`) | AG-UI stream of the LangGraph copilot (used by CopilotKit) |

`ProductSellStrategy` follows the shared `StrategySimulation` contract and adds two fields:
`uplift_source` (`campaigns` | `assumed`) and `expected_anchor_units_increment`.

## Tests and lint

```bash
uv run pytest                                   # 35 tests on tests/fixtures/v2
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
```

The fixture is a 3-store × 12-product slice of a generated v2 dataset (built with
`scripts/make_fixture.py`), including all campaigns and the affinity table.

## Modeling notes and limits

- **Stock**: on-hand stock is rebuilt from lots, sales and expirations. It matches
  `inventory_daily.closing_qty` and `on_order_qty` exactly.
- **Forecast**: a multiplicative baseline (level × weekday × calendar hint × weather), with quantiles
  derived from the generator's demand noise. It stands in for SageMaker, and its 14-day backtest
  WAPE is shown in every trend card.
- **Uplift**:
  - Measured from `promotion_campaigns` + `promotion_store_days` by comparing treatment and control
    stores (difference-in-differences). The central value is the median per strategy type and the
    range is the interquartile range.
  - Only campaigns that ended on or before the demo date are used.
  - The measured effect is extrapolated at most to twice the discount seen in the campaigns.
  - The campaigns cover a few product pairs, so applying their uplift to other products is an
    assumption. The output says so.
  - Without campaign data, the simulator falls back to an assumed elasticity per category.
- **Cross-sell**: when `product_affinity.csv` exists, combos prefer a partner observed in tickets
  (lift > 1). The output labels each pair as observed or as a business rule.
- **Simulation**: FEFO inventory day by day, with orders in transit and the store's usual reorder
  rule in both scenarios. Net impact = margin increment (including the full-price anchor of a
  combo) + value of the waste avoided.

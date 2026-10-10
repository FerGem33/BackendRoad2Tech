"""Wires settings -> repository -> services -> engine -> agent graph."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from ..agent.graph import build_copilot_graph
from ..agent.llm import build_chat_model, llm_label
from ..agent.tools import build_tools
from ..config import Settings
from ..data.repository import DataRepository
from ..domain.drafts import DraftStore, InMemoryDraftStore, PostgresDraftStore
from ..domain.services import Services
from ..domain.strategy.pipeline import StrategyEngine

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    settings: Settings
    svc: Services
    drafts: DraftStore
    engine: StrategyEngine
    model: BaseChatModel | None
    graph: Any | None
    llm: str
    pools: list[Any]
    async_pools: list[Any]


def _repository(settings: Settings, pools: list[Any]) -> DataRepository:
    if settings.data_backend == "postgres":
        if not settings.database_url:
            raise RuntimeError("DATA_BACKEND=postgres requires DATABASE_URL")
        from psycopg_pool import ConnectionPool

        from ..data.postgres_repository import PostgresRepository

        pool = ConnectionPool(settings.database_url, min_size=1, max_size=8, open=True)
        pools.append(pool)
        return PostgresRepository(pool)
    from ..data.csv_repository import CsvRepository

    return CsvRepository(settings.dataset_dir)


def _drafts(settings: Settings, pools: list[Any]) -> DraftStore:
    if settings.database_url and settings.data_backend == "postgres":
        from importlib import resources

        import psycopg

        schema = resources.files("stockcopilot.data").joinpath("schema.sql").read_text(encoding="utf-8")
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            conn.execute(schema)
        return PostgresDraftStore(pools[0])
    return InMemoryDraftStore()


async def open_async(context: AppContext) -> None:
    """Async resources created on startup (inside the server's event loop)."""
    if context.graph is None or context.settings.checkpointer != "postgres":
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        context.settings.database_url or "",
        min_size=1,
        max_size=8,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    context.async_pools.append(pool)
    saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await saver.setup()
    context.graph.checkpointer = saver


async def close_async(context: AppContext) -> None:
    for pool in context.async_pools:
        await pool.close()
    for pool in context.pools:
        pool.close()


def build_context(settings: Settings, include_agent: bool = True) -> AppContext:
    pools: list[Any] = []
    async_pools: list[Any] = []
    repo = _repository(settings, pools)
    forecast_provider = None
    if settings.forecast_provider == "dynamodb":
        if not settings.forecast_table_name:
            raise RuntimeError("FORECAST_PROVIDER=dynamodb requires FORECAST_TABLE_NAME")
        from ..domain.forecast_provider import DynamoForecastProvider

        forecast_provider = DynamoForecastProvider(settings.forecast_table_name, settings.aws_region)
    elif settings.forecast_provider == "jsonl":
        if not settings.forecast_file:
            raise RuntimeError("FORECAST_PROVIDER=jsonl requires FORECAST_FILE")
        if not settings.demo_as_of:
            raise RuntimeError("FORECAST_PROVIDER=jsonl requires DEMO_AS_OF")
        from ..domain.jsonl_forecast_provider import JsonlForecastProvider

        forecast_provider = JsonlForecastProvider(settings.forecast_file, settings.demo_as_of)
    svc = Services.create(repo, settings.demo_as_of, forecast_provider=forecast_provider)
    drafts = _drafts(settings, pools)
    engine = StrategyEngine(svc, drafts)
    model = None
    graph = None
    if include_agent:
        model = build_chat_model(settings, svc.catalog, svc.as_of)
        checkpointer: Any = None
        if settings.checkpointer == "postgres":
            if not settings.database_url:
                raise RuntimeError("CHECKPOINTER=postgres requires DATABASE_URL")
            # The async Postgres saver needs a running event loop: it is attached in `open_async`.
        else:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()
        graph = build_copilot_graph(svc, drafts, model, build_tools(svc, engine), checkpointer)
    log.info("StockCopilot ready: data=%s as_of=%s llm=%s", settings.data_backend, svc.as_of, llm_label(settings))
    return AppContext(
        settings=settings,
        svc=svc,
        drafts=drafts,
        engine=engine,
        model=model,
        graph=graph,
        llm=llm_label(settings) if include_agent else "disabled",
        pools=pools,
        async_pools=async_pools,
    )

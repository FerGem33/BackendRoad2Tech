"""FastAPI application: REST endpoints + the AG-UI endpoint that CopilotKit talks to."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import Settings, get_settings
from ..domain.catalog import NotFoundError
from ..domain.drafts import DraftNotFoundError, InvalidTransitionError
from .context import AppContext, build_context, close_async, open_async
from .routes import router

log = logging.getLogger(__name__)

AGENT_NAME = "stockcopilot"


def _error(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "request_id": request_id},
        headers={"X-Request-Id": request_id},
    )


def _mount_agent(app: FastAPI, context: AppContext) -> None:
    from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint

    agent = LangGraphAgent(
        name=AGENT_NAME,
        description="StockCopilot: trends, restock, promotions and approvals for store managers.",
        graph=context.graph,  # type: ignore[arg-type]
        emit_raw_events=False,  # the UI only needs AG-UI events, not raw LangChain callbacks
    )
    # CopilotKit (Next.js runtime) -> POST /copilot. AgentCore Runtime -> POST /invocations.
    add_langgraph_fastapi_endpoint(app, agent, "/copilot")
    add_langgraph_fastapi_endpoint(app, agent, "/invocations", include_in_schema=False)


def create_app(settings: Settings | None = None, include_agent: bool = True) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    context = build_context(settings, include_agent=include_agent)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await open_async(context)
        yield
        await close_async(context)

    app = FastAPI(
        title="StockCopilot Copilot API",
        version=__version__,
        description=(
            "Deterministic analytics over the Road2Tech v2 dataset (trend, forecast, risks, restock, "
            "strategy simulation and drafts) plus a LangGraph copilot served over AG-UI at `/copilot`."
        ),
        lifespan=lifespan,
    )
    app.state.ctx = context
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(NotFoundError)
    @app.exception_handler(DraftNotFoundError)
    async def not_found(request: Request, exc: Exception) -> JSONResponse:
        return _error(request, 404, "not_found", str(exc))

    @app.exception_handler(InvalidTransitionError)
    async def invalid_transition(request: Request, exc: InvalidTransitionError) -> JSONResponse:
        return _error(request, 409, "invalid_transition", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p not in ("query", "body", "path"))
        return _error(request, 422, "invalid_request", f"{where}: {first.get('msg', 'invalid value')}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 422: "invalid_request"}.get(exc.status_code, "error")
        return _error(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return _error(request, 500, "internal_error", "Unexpected error. Use the request_id to find the logs.")

    app.include_router(router)
    if include_agent and context.graph is not None:
        _mount_agent(app, context)
    return app

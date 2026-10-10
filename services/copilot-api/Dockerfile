# Persistent Copilot API (REST + LangGraph agent over AG-UI).
# Builds for amd64 or arm64 (`--platform linux/arm64` for Bedrock AgentCore Runtime / Graviton).
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app
USER app
ENV PATH="/app/.venv/bin:$PATH" PORT=8080
EXPOSE 8080
# AgentCore Runtime expects port 8080 with GET /ping and POST /invocations (both served).
CMD ["sh", "-c", "uvicorn stockcopilot.api.main:app --host 0.0.0.0 --port ${PORT}"]

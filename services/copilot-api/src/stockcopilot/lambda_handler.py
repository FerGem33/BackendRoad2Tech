"""AWS Lambda entry point for the short, stateless REST operations (API Gateway -> Lambda).

The streaming copilot agent (`/copilot`) is not mounted here: long-running, resumable LangGraph
runs belong in a persistent service (container on ECS/App Runner or Bedrock AgentCore Runtime).
Use DATA_BACKEND=postgres in Lambda so cold starts do not read the 130 MB CSV folder.
"""

from __future__ import annotations

from mangum import Mangum

from .api.app import create_app

app = create_app(include_agent=False)
handler = Mangum(app, lifespan="auto")

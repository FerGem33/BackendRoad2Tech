"""ASGI entry point for the persistent service (REST + copilot agent)."""

from .app import create_app

app = create_app()

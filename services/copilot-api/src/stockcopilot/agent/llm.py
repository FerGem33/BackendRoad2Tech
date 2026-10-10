"""Chat model factory: Gemini (Google AI Studio API key) or the offline stand-in."""

from __future__ import annotations

import logging
from datetime import date

from langchain_core.language_models.chat_models import BaseChatModel

from ..config import Settings
from ..domain.catalog import Catalog
from .offline import OfflineCopilotModel

log = logging.getLogger(__name__)


def build_chat_model(settings: Settings, catalog: Catalog, today: date) -> BaseChatModel:
    if settings.use_gemini:
        if not settings.google_api_key:
            raise RuntimeError("LLM_MODE=gemini requires GOOGLE_API_KEY (create one in Google AI Studio).")
        from langchain_google_genai import ChatGoogleGenerativeAI

        log.info("Copilot LLM: Gemini %s via Google AI Studio", settings.gemini_model)
        return ChatGoogleGenerativeAI(
            model=settings.gemini_model,
            google_api_key=settings.google_api_key,
            temperature=settings.gemini_temperature,
            max_retries=2,
        )
    log.warning("Copilot LLM: offline mode (no GOOGLE_API_KEY). Intent parsing is keyword-based.")
    return OfflineCopilotModel(catalog=catalog, today=today)


def llm_label(settings: Settings) -> str:
    return f"gemini:{settings.gemini_model}" if settings.use_gemini else "offline"

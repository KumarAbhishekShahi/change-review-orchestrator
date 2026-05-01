"""
Gemini API Client — Change Review Orchestrator

Thin wrapper around google-generativeai SDK.
Handles:
- API key loading from config
- Model selection (gemini-1.5-flash default for cost efficiency)
- Retry with exponential backoff on transient errors
- Token budget enforcement (max_output_tokens)
- Graceful degradation: returns None on any failure so callers
  can fall back to deterministic summaries

Usage:
    client = GeminiClient()
    text = client.generate(prompt="Summarise these findings: ...")
    if text is None:
        # use deterministic fallback
"""

from __future__ import annotations

import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5   # seconds


class GeminiClient:
    """
    Gemini generative AI client with retry and graceful degradation.

    Requires GEMINI_API_KEY in environment (loaded via config.py).
    Falls back silently to None if the SDK is not installed or the
    API is unavailable — the pipeline continues with deterministic output.
    """

    def __init__(
        self,
        model_name: str = "gemini-1.5-flash",
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> None:
        self._model_name = model_name
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._model: Any = None
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        """Initialise the Gemini SDK. Silently marks unavailable on failure."""
        try:
            import google.generativeai as genai  # type: ignore[import]
            from change_review_orchestrator.config import settings

            api_key = settings.gemini_api_key
            if not api_key:
                logger.warning("gemini_api_key_not_set", detail="LLM overlay disabled")
                return

            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel(
                model_name=self._model_name,
                generation_config={
                    "max_output_tokens": self._max_output_tokens,
                    "temperature":       self._temperature,
                },
            )
            self._available = True
            logger.info("gemini_client_ready", model=self._model_name)

        except ImportError:
            logger.warning(
                "google_generativeai_not_installed",
                detail="Install with: pip install google-generativeai",
            )
        except Exception as exc:
            logger.warning("gemini_init_failed", error=str(exc))

    @property
    def available(self) -> bool:
        """True if the Gemini client is ready to make API calls."""
        return self._available

    def generate(self, prompt: str) -> str | None:
        """
        Generate text from a prompt.

        Args:
            prompt: The complete prompt string.

        Returns:
            Generated text, or None if unavailable / error occurred.
        """
        if not self._available or self._model is None:
            logger.debug("gemini_unavailable_skipping")
            return None

        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._model.generate_content(prompt)
                text = response.text.strip() if response.text else None
                logger.info(
                    "gemini_generate_success",
                    attempt=attempt,
                    chars=len(text) if text else 0,
                )
                return text

            except Exception as exc:
                last_error = exc
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "gemini_generate_error",
                    attempt=attempt,
                    error=str(exc),
                    retry_in=wait,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(wait)

        logger.error("gemini_all_retries_failed", error=str(last_error))
        return None

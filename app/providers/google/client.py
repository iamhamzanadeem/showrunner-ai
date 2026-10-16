from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional
from google import genai
from google.genai import errors

from app.config import Settings, settings as default_settings

logger = logging.getLogger("showrunner.providers.google.client")


def get_genai_client(
    cfg: Optional[Settings] = None,
    location: Optional[str] = None,
    api_key: Optional[str] = None,
    project: Optional[str] = None,
) -> genai.Client:
    """
    Creates and returns a google-genai Client.
    Supports Google AI Studio (API Key) or Vertex AI (Project + Location via ADC).
    """
    active_settings = cfg or default_settings
    key = api_key or active_settings.GEMINI_API_KEY
    proj = project or active_settings.GOOGLE_CLOUD_PROJECT

    if key:
        return genai.Client(api_key=key)

    if active_settings.GOOGLE_GENAI_USE_VERTEXAI or proj:
        loc = location or active_settings.GOOGLE_CLOUD_LOCATION or "global"
        return genai.Client(vertexai=True, project=proj, location=loc)

    raise ValueError(
        "Google GenAI credentials not configured. "
        "Please provide GEMINI_API_KEY or set GOOGLE_CLOUD_PROJECT with GOOGLE_GENAI_USE_VERTEXAI=true."
    )


async def call_with_retry(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> Any:
    """
    Executes a synchronous or asynchronous callable with exponential backoff retries
    for transient Google GenAI errors (429, 500, 502, 503, 504).
    Sync functions are executed off the main event loop using asyncio.to_thread.
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:
            last_exc = exc
            status_code = getattr(exc, "status", None) or getattr(exc, "code", None)
            err_str = str(exc).lower()

            is_transient = (
                status_code in (429, 500, 502, 503, 504)
                or isinstance(exc, (errors.ServerError, errors.APIError))
                or any(t in err_str for t in ["429", "503", "resource_exhausted", "quota", "overloaded", "rate limit"])
            )

            if is_transient and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                func_name = getattr(func, "__name__", str(func))
                logger.warning(
                    "Transient Google API error in %s: %s. Retrying in %.1fs (attempt %d/%d)...",
                    func_name,
                    exc,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
            else:
                raise

    if last_exc:
        raise last_exc

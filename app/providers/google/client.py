from __future__ import annotations

import asyncio
import logging
import random
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
    base_delay: float = 2.0,
    retry: bool = True,
    **kwargs: Any,
) -> Any:
    """
    Executes a synchronous or asynchronous callable with exponential backoff retries
    for transient Google GenAI errors (500, 502, 503, 504 and 429 / RESOURCE_EXHAUSTED).
    Sync functions are executed off the main event loop using asyncio.to_thread.
    When retry=False, executes exactly once without retry.
    """
    if not retry:
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return await asyncio.to_thread(func, *args, **kwargs)

    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return await asyncio.to_thread(func, *args, **kwargs)
        except Exception as exc:
            last_exc = exc
            code = getattr(exc, "code", None)
            status = getattr(exc, "status", None)

            # Retry ONLY when:
            # (a) isinstance(exc, errors.ServerError) or HTTP status in (500, 502, 503, 504), or
            # (b) status 429 / "RESOURCE_EXHAUSTED".
            # Never retry other errors.ClientError (400/401/403/404 etc) or generic exceptions.
            is_429 = (
                code == 429
                or str(code) == "429"
                or status == 429
                or str(status) == "429"
                or (isinstance(status, str) and status.upper() == "RESOURCE_EXHAUSTED")
            )

            server_codes = (500, 502, 503, 504)
            server_code_strs = ("500", "502", "503", "504")
            is_server = (
                isinstance(exc, errors.ServerError)
                or code in server_codes
                or str(code) in server_code_strs
                or status in server_codes
                or str(status) in server_code_strs
            )

            is_transient = is_429 or is_server

            if is_transient and attempt < max_retries:
                if is_429:
                    base = 5.0 if base_delay == 2.0 else base_delay
                    cap = 60.0
                else:
                    base = base_delay
                    cap = 30.0

                raw_delay = random.uniform(0.5, 1.0) * base * (2 ** attempt)
                delay = min(cap, raw_delay)
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

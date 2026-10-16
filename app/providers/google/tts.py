from __future__ import annotations

from pathlib import Path
from typing import Optional
from app.providers.base import BaseTTSProvider


class GoogleTTSProvider(BaseTTSProvider):
    """Gemini 3.8 Flash TTS voiceover engine via Interactions API."""

    async def generate_voiceover(
        self,
        text: str,
        duration_s: Optional[float] = None,
        voice_name: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("GoogleTTSProvider implementation scheduled for Milestone 3 Part B.")

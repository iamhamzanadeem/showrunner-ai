from __future__ import annotations

from pathlib import Path
from typing import Optional
from app.providers.base import BaseMusicProvider


class GoogleMusicProvider(BaseMusicProvider):
    """Google Lyria cinematic score and music generator via Interactions API."""

    async def generate_music(
        self,
        prompt: str,
        duration_s: float,
        job_dir: Optional[Path] = None,
    ) -> str:
        raise NotImplementedError("GoogleMusicProvider implementation scheduled for Milestone 3 Part B.")

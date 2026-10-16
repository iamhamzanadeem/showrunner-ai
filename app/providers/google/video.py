from __future__ import annotations

from pathlib import Path
from typing import Optional
from app.providers.base import BaseVideoProvider


class GoogleVideoProvider(BaseVideoProvider):
    """Veo 3.1 Fast image-to-video ~8s clip generator with frame chaining."""

    async def generate_clip(
        self,
        prompt: str,
        duration_s: float = 8.0,
        initial_frame: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
        scene_num: int = 1,
        shot_num: int = 1,
        attempt: int = 1,
    ) -> str:
        raise NotImplementedError("GoogleVideoProvider implementation scheduled for Milestone 3 Part B.")

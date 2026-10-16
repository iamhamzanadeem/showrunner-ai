from __future__ import annotations

from pathlib import Path
from typing import Optional
from app.models import BibleStyle
from app.providers.base import BaseImageProvider


class GoogleImageProvider(BaseImageProvider):
    """Gemini 3.1 Flash Image (Nano Banana 2) keyframe and reference sheet generator."""

    async def generate_keyframe(
        self,
        prompt: str,
        style: BibleStyle,
        ref_image: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
        scene_num: int = 1,
        shot_num: int = 1,
        attempt: int = 1,
    ) -> str:
        raise NotImplementedError("GoogleImageProvider implementation scheduled for Milestone 3 Part B.")

    async def generate_ref_sheet(
        self,
        entity_type: str,
        name: str,
        description: str,
        style: BibleStyle,
        job_dir: Path,
    ) -> str:
        raise NotImplementedError("GoogleImageProvider implementation scheduled for Milestone 3 Part B.")

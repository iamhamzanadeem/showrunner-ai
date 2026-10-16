from __future__ import annotations

from typing import List, Optional
from app.models import Bible, BibleStyle, CritiqueResult, Plan, Shot, VideoMode
from app.providers.base import (
    BaseCriticProvider,
    BaseImageProvider,
    BaseMusicProvider,
    BasePlannerProvider,
    BaseTTSProvider,
    BaseVideoProvider,
)


class GooglePlannerProvider(BasePlannerProvider):
    """Gemini 2.5/3.0 Pro & Flash story and shot planner."""

    def __init__(self, api_key: Optional[str] = None, project: Optional[str] = None):
        self.api_key = api_key
        self.project = project

    async def generate_plan(
        self,
        brief: str,
        mode: VideoMode,
        ref_images: Optional[List[str]] = None,
    ) -> Plan:
        raise NotImplementedError(
            "Google Gemini Planner provider is scheduled for Milestone 2. "
            "Set SHOWRUNNER_PROVIDER=mock for Milestone 1."
        )


class GoogleImageProvider(BaseImageProvider):
    """Gemini multimodal image generation & Imagen 3 keyframe generator."""

    async def generate_keyframe(
        self,
        prompt: str,
        style: BibleStyle,
        ref_image: Optional[str] = None,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Google Imagen / Gemini Image provider not yet implemented.")


class GoogleVideoProvider(BaseVideoProvider):
    """Google Veo image-to-video ~8s clip generator with frame chaining."""

    async def generate_clip(
        self,
        prompt: str,
        initial_frame: Optional[str] = None,
        duration_s: float = 8.0,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Google Veo Video provider not yet implemented.")


class GoogleCriticProvider(BaseCriticProvider):
    """Gemini 2.5 Multimodal video critique and quality assurance."""

    async def critique_clip(
        self,
        video_path: str,
        shot: Shot,
        bible: Bible,
    ) -> CritiqueResult:
        raise NotImplementedError("Google Gemini Critic provider not yet implemented.")


class GoogleTTSProvider(BaseTTSProvider):
    """Gemini Text-to-Speech voiceover engine."""

    async def generate_voiceover(
        self,
        text: str,
        voice_name: Optional[str] = None,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Google Gemini TTS provider not yet implemented.")


class GoogleMusicProvider(BaseMusicProvider):
    """Google Lyria cinematic score and music generator."""

    async def generate_music(
        self,
        prompt: str,
        duration_s: float,
        job_dir: Optional[str] = None,
    ) -> str:
        raise NotImplementedError("Google Lyria Music provider not yet implemented.")

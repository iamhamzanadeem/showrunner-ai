from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional
from pydantic import BaseModel
from app.models import Bible, BibleStyle, CritiqueResult, Plan, Shot, VideoMode


class BasePlannerProvider(ABC):
    """Abstract interface for story & shot planner."""

    @abstractmethod
    async def generate_plan(
        self,
        brief: str,
        mode: VideoMode,
        ref_images: Optional[List[str]] = None,
    ) -> Plan:
        """Generates script, scenes, shot list with durations, and style/character bible."""
        pass


class BaseImageProvider(ABC):
    """Abstract interface for keyframe generation (Gemini Image / Imagen)."""

    @abstractmethod
    async def generate_keyframe(
        self,
        prompt: str,
        style: BibleStyle,
        ref_image: Optional[str] = None,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        """Generates a keyframe image and returns the local file path or URL."""
        pass


class BaseVideoProvider(ABC):
    """Abstract interface for video clip generation (Veo image-to-video)."""

    @abstractmethod
    async def generate_clip(
        self,
        prompt: str,
        initial_frame: Optional[str] = None,
        duration_s: float = 8.0,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        """Generates a video clip and returns the local file path or URL."""
        pass


class BaseCriticProvider(ABC):
    """Abstract interface for multimodal critique and review (Gemini Multimodal)."""

    @abstractmethod
    async def critique_clip(
        self,
        video_path: str,
        shot: Shot,
        bible: Bible,
    ) -> CritiqueResult:
        """Evaluates video clip against shot specification and visual bible."""
        pass


class BaseTTSProvider(ABC):
    """Abstract interface for voiceover synthesis (Gemini TTS)."""

    @abstractmethod
    async def generate_voiceover(
        self,
        text: str,
        voice_name: Optional[str] = None,
        job_dir: Optional[str] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        """Synthesizes voiceover audio and returns the local file path or URL."""
        pass


class BaseMusicProvider(ABC):
    """Abstract interface for soundtrack and score generation (Lyria)."""

    @abstractmethod
    async def generate_music(
        self,
        prompt: str,
        duration_s: float,
        job_dir: Optional[str] = None,
    ) -> str:
        """Generates background music track and returns the local file path or URL."""
        pass


class ProviderBundle(BaseModel):
    """Container for active provider instances across all pipeline modalities."""

    model_config = {"arbitrary_types_allowed": True}

    provider_type: str
    planner: BasePlannerProvider
    image: BaseImageProvider
    video: BaseVideoProvider
    critic: BaseCriticProvider
    tts: BaseTTSProvider
    music: BaseMusicProvider

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, List, Optional
from pydantic import BaseModel
from app.models import Bible, BibleStyle, CritiqueResult, Plan, PlanEdit, Shot, VideoMode


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

    @abstractmethod
    async def edit_plan(
        self,
        plan: Plan,
        message: str,
    ) -> PlanEdit:
        """Refines existing production plan based on director instructions, returning targeted diffs."""
        pass


class BaseImageProvider(ABC):
    """Abstract interface for keyframe generation (Gemini Image / Imagen)."""

    @abstractmethod
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
        ref_images: Optional[List[str]] = None,
    ) -> str:
        """Generates a keyframe image and returns the local file path or URL.

        ref_image: optional continuity frame (previous shot's last frame).
        ref_images: optional character/product reference sheets for identity consistency.
        """
        pass

    @abstractmethod
    async def generate_ref_sheet(
        self,
        entity_type: str,
        name: str,
        description: str,
        style: BibleStyle,
        job_dir: Path,
        ref_image: Optional[str] = None,
    ) -> str:
        """Generates a reference sheet image for a character or product.

        ref_image: optional user-uploaded photo the sheet must stay faithful to.
        """
        pass


class BaseVideoProvider(ABC):
    """Abstract interface for video clip generation (Veo image-to-video)."""

    @abstractmethod
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
        reference_images: Optional[List[str]] = None,
        on_operation_started: Optional[Callable[[str], None]] = None,
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
        attempt: int = 1,
    ) -> CritiqueResult:
        """Evaluates video clip against shot specification and visual bible."""
        pass


class BaseTTSProvider(ABC):
    """Abstract interface for voiceover synthesis (Gemini TTS)."""

    @abstractmethod
    async def generate_voiceover(
        self,
        text: str,
        duration_s: Optional[float] = None,
        voice_name: Optional[str] = None,
        job_dir: Optional[Path] = None,
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
        job_dir: Optional[Path] = None,
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

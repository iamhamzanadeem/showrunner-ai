from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VideoMode(str, Enum):
    ad = "ad"
    short_film = "short_film"


class JobStatus(str, Enum):
    queued = "queued"
    planning = "planning"
    planned = "planned"
    generating = "generating"
    critiquing = "critiquing"
    assembling = "assembling"
    completed = "completed"
    failed = "failed"


class ShotStatus(str, Enum):
    pending = "pending"
    generating = "generating"
    critiquing = "critiquing"
    passed = "passed"
    failed = "failed"
    retrying = "retrying"
    ready = "ready"


class BibleStyle(BaseModel):
    palette: List[str] = Field(
        default_factory=list,
        description="Color palette hex codes or descriptive color names",
    )
    lighting: str = Field(..., description="Lighting design and atmosphere")
    lens: str = Field(..., description="Camera lenses and optical characteristics")
    mood: str = Field(..., description="Tonal mood and visual energy")


class CharacterSpec(BaseModel):
    name: str
    appearance: str
    role: Optional[str] = None
    reference_image: Optional[str] = None


class ProductSpec(BaseModel):
    name: str
    description: str
    reference_image: Optional[str] = None


class Bible(BaseModel):
    style: BibleStyle
    characters: List[CharacterSpec] = Field(default_factory=list)
    products: List[ProductSpec] = Field(default_factory=list)


class CritiqueResult(BaseModel):
    shot_id: str
    passed: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: List[str] = Field(default_factory=list)
    suggested_prompt_fix: Optional[str] = None


class ShotAttempt(BaseModel):
    attempt_number: int
    prompt: str
    image_url: Optional[str] = None
    clip_url: Optional[str] = None
    critique: Optional[CritiqueResult] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Shot(BaseModel):
    id: str
    scene_id: str
    duration_s: float = Field(..., ge=1.0, le=15.0)
    description: str
    camera: str
    visual_prompt: str
    vo_line: Optional[str] = None
    characters_referenced: List[str] = Field(default_factory=list)
    products_referenced: List[str] = Field(default_factory=list)
    chain_from_previous: bool = False
    status: ShotStatus = ShotStatus.pending
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    first_frame_url: Optional[str] = None
    chained_from_shot_id: Optional[str] = None
    retry_count: int = 0
    last_critique: Optional[CritiqueResult] = None
    best_critique: Optional[CritiqueResult] = None
    is_flagged_fallback: bool = False
    attempts: List[ShotAttempt] = Field(default_factory=list)
    prompt_history: List[str] = Field(default_factory=list)


class Scene(BaseModel):
    id: str
    scene_number: int
    title: str
    summary: str
    shots: List[Shot] = Field(default_factory=list)


class Plan(BaseModel):
    title: str
    logline: str
    mode: VideoMode
    target_duration_s: float
    bible: Bible
    scenes: List[Scene] = Field(default_factory=list)

    @property
    def total_duration_s(self) -> float:
        return sum(shot.duration_s for scene in self.scenes for shot in scene.shots)

    @property
    def total_shots(self) -> int:
        return sum(len(scene.shots) for scene in self.scenes)


class AgentEvent(BaseModel):
    id: str
    job_id: str
    step: str = Field(
        ...,
        description="One of: PLAN, REF, KEYFRAME, GEN, CHAIN, CRITIC_PASS, CRITIC_FAIL, RETRY, VO, MUSIC, ASSEMBLE, DONE, ERROR",
    )
    message: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    shot_id: Optional[str] = None
    scene_id: Optional[str] = None
    data: Optional[dict[str, Any]] = None


class Job(BaseModel):
    id: str
    brief: str
    mode: VideoMode
    status: JobStatus = JobStatus.queued
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    reference_images: List[str] = Field(default_factory=list)
    character_refs: Dict[str, str] = Field(default_factory=dict)
    product_refs: Dict[str, str] = Field(default_factory=dict)
    plan: Optional[Plan] = None
    soundtrack_url: Optional[str] = None
    poster_url: Optional[str] = None
    captions_srt_url: Optional[str] = None
    final_video_url: Optional[str] = None
    error: Optional[str] = None


class JobCreateRequest(BaseModel):
    brief: str
    mode: VideoMode = VideoMode.ad


class HealthResponse(BaseModel):
    status: str
    active_provider: str
    version: str
    storage_dir: str

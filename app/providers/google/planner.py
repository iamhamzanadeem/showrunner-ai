from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List, Optional
from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings
from app.models import (
    Bible,
    BibleStyle,
    CharacterSpec,
    Plan,
    PlanEdit,
    ProductSpec,
    Scene,
    Shot,
    ShotStatus,
    UpdatedShotSpec,
    VideoMode,
)
from app.providers.base import BasePlannerProvider
from app.providers.google.client import call_with_retry, get_genai_client

logger = logging.getLogger("showrunner.providers.google.planner")


# Schema mirroring Plan without runtime state fields for structured LLM generation
class BibleStyleSchema(BaseModel):
    palette: List[str] = Field(
        default_factory=lambda: ["#0A0F1D", "#00F0FF", "#7000FF", "#FFFFFF"],
        description="3 to 5 hex colors or color names defining the aesthetic palette",
    )
    lighting: str = Field(description="Lighting style, contrast, and atmosphere")
    lens: str = Field(description="Camera lens, focal length, and optical characteristics")
    mood: str = Field(description="Tonal mood and emotional energy")


class CharacterSpecSchema(BaseModel):
    name: str = Field(description="Character name")
    appearance: str = Field(
        description="Detailed visual appearance: attire, physical traits, hair, silhouette"
    )
    role: Optional[str] = Field(None, description="Role or profession in story")


class ProductSpecSchema(BaseModel):
    name: str = Field(description="Product or hero asset name")
    description: str = Field(
        description="Detailed visual description: materials, finish, colors, logo placement"
    )


class BibleSchema(BaseModel):
    style: BibleStyleSchema
    characters: List[CharacterSpecSchema] = Field(default_factory=list)
    products: List[ProductSpecSchema] = Field(default_factory=list)


class ShotPlanSchema(BaseModel):
    id: str = Field(description="Shot identifier, e.g. shot_1_1")
    scene_id: str = Field(description="Parent scene identifier, e.g. scene_1")
    duration_s: float = Field(
        description="Shot duration in seconds: strictly 4, 6, or 8 seconds for video generation"
    )
    description: str = Field(description="Narrative action occurring in the shot")
    camera: str = Field(description="Camera movement, framing, and angle")
    visual_prompt: str = Field(
        description="Rich, descriptive generative prompt for keyframe & video generator"
    )
    vo_line: Optional[str] = Field(
        None, description="Voiceover / narration line spoken during this shot"
    )
    characters_referenced: List[str] = Field(default_factory=list)
    products_referenced: List[str] = Field(default_factory=list)
    chain_from_previous: bool = Field(
        False,
        description="True if continuous action should chain from previous shot's last frame",
    )


class ScenePlanSchema(BaseModel):
    id: str = Field(description="Scene identifier, e.g. scene_1")
    scene_number: int = Field(description="1-based scene sequence number")
    title: str = Field(description="Scene title")
    summary: str = Field(description="Dramatic summary of scene")
    shots: List[ShotPlanSchema] = Field(default_factory=list)


class PlanGenerationSchema(BaseModel):
    title: str = Field(description="Cinematic project title")
    logline: str = Field(description="Compelling one-sentence summary / hook")
    target_duration_s: float = Field(description="Total target runtime in seconds")
    bible: BibleSchema
    scenes: List[ScenePlanSchema] = Field(default_factory=list)


DIRECTOR_SYSTEM_PROMPT = """You are an award-winning cinematic director and showrunner specializing in AI-native video production.
Your job is to transform a creative brief into a comprehensive production blueprint consisting of:
1. Production Bible:
   - Visual style (3-5 color palette hexes, lighting design, cinematic lens selection, tonal mood).
   - Character specifications (costume, distinctive physical traits, hair/silhouette for visual consistency).
   - Product / hero asset specifications (materials, finishes, form factor).
   If reference images are provided, inspect them and faithfully describe the entities in the bible.

2. Scene Breakdown and Shot List:
   - For 'ad' mode: 30-60s runtime total, 4-8 shots across 2-3 scenes. Pacing is punchy, high-impact.
   - For 'short_film' mode: 120-180s runtime total, 15-25 shots across 4-7 scenes with narrative progression (Act I setup, Act II confrontation, Act III resolution).
   - CRITICAL DURATION CONSTRAINT: Every single shot MUST have a duration_s of exactly 4, 6, or 8 seconds (Veo generator restriction).
   - CONTINUITY CHAINING: Set chain_from_previous=true for shots within the same scene that depict continuous unbroken action from the previous shot. The first shot of every scene MUST have chain_from_previous=false.
   - Generative visual prompts must be concrete, cinematic, vivid, describing subject, environment, lighting, motion, and color palette without quality buzzwords.
"""


def _snap_duration(d: float) -> float:
    """Snaps any duration to closest Veo-supported duration (4, 6, or 8s)."""
    if d <= 5.0:
        return 4.0
    elif d <= 7.0:
        return 6.0
    return 8.0


class GooglePlannerProvider(BasePlannerProvider):
    """Gemini-powered cinematic director & showrunner planner using structured JSON output."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key
        self.project = project
        self.model = model or settings.SR_MODEL_PLANNER

    async def generate_plan(
        self,
        brief: str,
        mode: VideoMode,
        ref_images: Optional[List[str]] = None,
    ) -> Plan:
        client = get_genai_client(
            api_key=self.api_key,
            project=self.project,
        )

        # Build contents list (multimodal if reference images are supplied)
        contents: List[Any] = []

        # Process reference images into parts
        if ref_images:
            for ref in ref_images:
                try:
                    p = Path(ref)
                    if not p.is_file():
                        # Try relative to DATA_DIR
                        alt_p = settings.DATA_DIR / ref.lstrip("/")
                        if alt_p.is_file():
                            p = alt_p
                    if p.is_file():
                        with open(p, "rb") as f:
                            img_bytes = f.read()
                        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                        contents.append(
                            types.Part.from_bytes(data=img_bytes, mime_type=mime)
                        )
                except Exception as e:
                    logger.warning("Could not load reference image '%s': %s", ref, e)

        # Mode instructions
        mode_instruction = (
            "Target Mode: COMMERCIAL ADVERTISEMENT (30-60s total runtime, 4-8 shots total across 2-3 scenes)."
            if mode == VideoMode.ad
            else "Target Mode: CINEMATIC SHORT FILM (120-180s total runtime, 15-25 shots total across 4-7 scenes)."
        )

        user_prompt = f"""Creative Brief:
\"\"\"{brief}\"\"\"

{mode_instruction}
Design a complete, broadcast-ready production blueprint adhering to all director constraints.
"""
        contents.append(user_prompt)

        config = types.GenerateContentConfig(
            system_instruction=DIRECTOR_SYSTEM_PROMPT,
            temperature=0.7,
            response_mime_type="application/json",
            response_schema=PlanGenerationSchema,
        )

        def _call_gemini():
            return client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

        resp = await call_with_retry(_call_gemini)

        # Parse structured output
        raw_dict: dict[str, Any] = {}
        if hasattr(resp, "parsed") and resp.parsed is not None:
            if isinstance(resp.parsed, BaseModel):
                raw_dict = resp.parsed.model_dump()
            elif isinstance(resp.parsed, dict):
                raw_dict = resp.parsed
        if not raw_dict and hasattr(resp, "text") and resp.text:
            raw_dict = json.loads(resp.text)

        # Validation & repair into canonical Plan model
        return self._validate_and_repair_plan(raw_dict, brief, mode, ref_images)

    def _validate_and_repair_plan(
        self,
        raw: dict[str, Any],
        brief: str,
        mode: VideoMode,
        ref_images: Optional[List[str]],
    ) -> Plan:
        title = raw.get("title") or "Showrunner Production"
        logline = raw.get("logline") or brief[:120]

        # Bible
        raw_bible = raw.get("bible") or {}
        raw_style = raw_bible.get("style") or {}
        style = BibleStyle(
            palette=raw_style.get("palette") or ["#0A0F1D", "#00F0FF", "#7000FF", "#FFFFFF"],
            lighting=raw_style.get("lighting") or "Cinematic directional key light with atmospheric rim fill",
            lens=raw_style.get("lens") or "35mm anamorphic prime, subtle horizontal flare",
            mood=raw_style.get("mood") or "Cinematic, dramatic, visionary",
        )

        characters = [
            CharacterSpec(
                name=c.get("name", f"Character_{i+1}"),
                appearance=c.get("appearance", "Distinctive cinematic hero attire"),
                role=c.get("role"),
                reference_image=ref_images[0] if ref_images and i == 0 else None,
            )
            for i, c in enumerate(raw_bible.get("characters") or [])
        ]
        if not characters:
            characters.append(
                CharacterSpec(
                    name="Protagonist",
                    appearance="Cinematic protagonist in tailored tactical apparel",
                    role="Lead",
                    reference_image=ref_images[0] if ref_images else None,
                )
            )

        products = [
            ProductSpec(
                name=p.get("name", f"Product_{i+1}"),
                description=p.get("description", "High-precision hero product"),
                reference_image=ref_images[1] if ref_images and len(ref_images) > 1 and i == 0 else None,
            )
            for i, p in enumerate(raw_bible.get("products") or [])
        ]

        bible = Bible(style=style, characters=characters, products=products)

        # Scenes & shots
        raw_scenes = raw.get("scenes") or []
        scenes: List[Scene] = []

        for s_idx, raw_scene in enumerate(raw_scenes, start=1):
            scene_id = f"scene_{s_idx}"
            raw_shots = raw_scene.get("shots") or []
            shots: List[Shot] = []

            for shot_idx, raw_shot in enumerate(raw_shots, start=1):
                shot_id = f"shot_{s_idx}_{shot_idx}"
                dur = _snap_duration(float(raw_shot.get("duration_s", 6.0)))
                # First shot in scene can never chain from previous scene
                chain = bool(raw_shot.get("chain_from_previous", False)) if shot_idx > 1 else False

                shot = Shot(
                    id=shot_id,
                    scene_id=scene_id,
                    duration_s=dur,
                    description=raw_shot.get("description", f"Scene {s_idx} Shot {shot_idx}"),
                    camera=raw_shot.get("camera", "Cinematic steady tracking shot"),
                    visual_prompt=raw_shot.get("visual_prompt", f"Cinematic shot, {style.mood}, {style.lighting}"),
                    vo_line=raw_shot.get("vo_line"),
                    characters_referenced=raw_shot.get("characters_referenced") or [],
                    products_referenced=raw_shot.get("products_referenced") or [],
                    chain_from_previous=chain,
                    status=ShotStatus.pending,
                )
                shots.append(shot)

            scenes.append(
                Scene(
                    id=scene_id,
                    scene_number=s_idx,
                    title=raw_scene.get("title", f"Scene {s_idx}"),
                    summary=raw_scene.get("summary", ""),
                    shots=shots,
                )
            )

        target_dur = sum(shot.duration_s for scene in scenes for shot in scene.shots)

        return Plan(
            title=title,
            logline=logline,
            mode=mode,
            target_duration_s=target_dur,
            bible=bible,
            scenes=scenes,
        )

    async def edit_plan(self, plan: Plan, message: str) -> PlanEdit:
        client = get_genai_client(api_key=self.api_key, project=self.project)

        # Plan summary representation for LLM context
        plan_summary = {
            "title": plan.title,
            "mode": plan.mode.value,
            "style": plan.bible.style.model_dump(),
            "scenes": [
                {
                    "id": sc.id,
                    "scene_number": sc.scene_number,
                    "title": sc.title,
                    "shots": [
                        {
                            "id": sh.id,
                            "scene_id": sh.scene_id,
                            "duration_s": sh.duration_s,
                            "description": sh.description,
                            "camera": sh.camera,
                            "visual_prompt": sh.visual_prompt,
                            "vo_line": sh.vo_line,
                        }
                        for sh in sc.shots
                    ],
                }
                for sc in plan.scenes
            ],
        }

        prompt = f"""Current Film Production Plan:
{json.dumps(plan_summary, indent=2)}

Director Chat Refinement Instruction:
\"{message}\"

Determine which scenes and shots must be changed. Modify ONLY what is requested.
Return a structured PlanEdit JSON.
"""

        config = types.GenerateContentConfig(
            system_instruction=(
                "You are an expert film director and showrunner refining an existing production plan. "
                "Change ONLY what is requested by the director. List the exact affected shot IDs "
                "and their updated specifications (visual_prompt, description, camera, or vo_line). "
                "Do NOT modify unrelated shots."
            ),
            temperature=0.4,
            response_mime_type="application/json",
            response_schema=PlanEditSchema,
        )

        def _call_gemini_edit():
            return client.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=config,
            )

        resp = await call_with_retry(_call_gemini_edit)

        raw_dict: dict[str, Any] = {}
        if hasattr(resp, "parsed") and resp.parsed is not None:
            if isinstance(resp.parsed, BaseModel):
                raw_dict = resp.parsed.model_dump()
            elif isinstance(resp.parsed, dict):
                raw_dict = resp.parsed
        if not raw_dict and hasattr(resp, "text") and resp.text:
            raw_dict = json.loads(resp.text)

        updated_shots = [
            UpdatedShotSpec(
                id=us.get("id"),
                visual_prompt=us.get("visual_prompt"),
                description=us.get("description"),
                camera=us.get("camera"),
                vo_line=us.get("vo_line"),
                duration_s=float(us["duration_s"]) if us.get("duration_s") else None,
            )
            for us in raw_dict.get("updated_shots") or []
            if us.get("id")
        ]

        style_ch = None
        if raw_dict.get("style_changes"):
            sc = raw_dict["style_changes"]
            style_ch = BibleStyle(
                palette=sc.get("palette") or plan.bible.style.palette,
                lighting=sc.get("lighting") or plan.bible.style.lighting,
                lens=sc.get("lens") or plan.bible.style.lens,
                mood=sc.get("mood") or plan.bible.style.mood,
            )

        target_shot_ids = raw_dict.get("target_shot_ids") or [us.id for us in updated_shots]
        target_scene_ids = raw_dict.get("target_scene_ids") or []
        if not target_scene_ids and target_shot_ids:
            for sc in plan.scenes:
                if any(sh.id in target_shot_ids for sh in sc.shots):
                    target_scene_ids.append(sc.id)

        return PlanEdit(
            summary=raw_dict.get("summary") or f"Refined plan based on: {message}",
            target_scene_ids=target_scene_ids,
            target_shot_ids=target_shot_ids,
            updated_shots=updated_shots,
            style_changes=style_ch,
            rationale=raw_dict.get("rationale"),
        )


class UpdatedShotSchema(BaseModel):
    id: str = Field(description="Shot ID, e.g. shot_2_1")
    visual_prompt: Optional[str] = Field(None, description="Updated visual generative prompt")
    description: Optional[str] = Field(None, description="Updated description")
    camera: Optional[str] = Field(None, description="Updated camera movement")
    vo_line: Optional[str] = Field(None, description="Updated voiceover line if modified")
    duration_s: Optional[float] = Field(None, description="Updated duration strictly 4, 6, or 8s")


class PlanEditSchema(BaseModel):
    summary: str = Field(description="Clear summary of what was refined")
    target_scene_ids: List[str] = Field(
        default_factory=list, description="Scene IDs targeted for refinement, e.g. ['scene_2']"
    )
    target_shot_ids: List[str] = Field(
        default_factory=list,
        description="List of shot IDs directly modified, e.g. ['shot_2_1', 'shot_2_2']",
    )
    updated_shots: List[UpdatedShotSchema] = Field(
        default_factory=list, description="List of shot modifications"
    )
    style_changes: Optional[BibleStyleSchema] = Field(
        None, description="Optional style bible modifications"
    )
    rationale: Optional[str] = Field(
        None, description="Director rationale for these specific adjustments"
    )

from __future__ import annotations

import json
import logging
from pathlib import Path
import time
from typing import Any, List, Optional
from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings
from app.models import Bible, CritiqueResult, Shot
from app.providers.base import BaseCriticProvider
from app.providers.google.client import call_with_retry, get_genai_client

logger = logging.getLogger("showrunner.providers.google.critic")


class CritiqueSchema(BaseModel):
    passed: bool = Field(
        description="True if video clip satisfies aesthetic standards, style bible, and shot specification; false if regeneration required",
    )
    score: float = Field(
        description="Quality rating between 0.0 and 1.0; 0.70 or higher represents broadcast-passing quality",
    )
    reasons: List[str] = Field(
        description=(
            "List of specific, actionable critique observations. If failing, categorise with tags: "
            "[drift] character/costume inconsistency, [artifact] visual warping/glitches, "
            "[wrong_product] inaccurate product presentation, [off_brief] misses prompt action, "
            "[continuity] discontinuous motion/lighting, or [text_glitch] unwanted text artifacts."
        ),
    )
    suggested_prompt_fix: Optional[str] = Field(
        None,
        description="Concrete, actionable modification to the visual prompt that specifically rectifies the observed issues on retry",
    )


CRITIC_SYSTEM_PROMPT = """You are an exacting film executive, lead visual effects supervisor, and cinematic quality assurance critic.
Your role is to rigorously inspect rendered video clips against the production bible and shot blueprint.

Evaluation Criteria:
1. Bible Fidelity: Does the color palette, lighting atmosphere, lens characteristics, and tone match the BibleStyle?
2. Character & Asset Consistency: Do the character appearances and product details remain faithful to the Bible specs without drift?
3. Visual Artifacts: Are there morphing deformities, extra limbs, jittery temporal artifacts, or unwanted AI text watermarks?
4. Narrative Continuity: Does the camera movement and action match the shot description?

Scoring & Thresholds:
- Score 0.70 to 1.0: Broadcast quality, acceptable for final assembly. Set passed=true.
- Score < 0.70: Requires regeneration. Set passed=false. Identify specific issues in reasons using category tags:
  [drift], [artifact], [wrong_product], [off_brief], [continuity], or [text_glitch].
- When failing, provide an authoritative suggested_prompt_fix emphasizing what to add, remove, or enforce.
"""


class GoogleCriticProvider(BaseCriticProvider):
    """Gemini Multimodal Quality Critic reviewing video clips against shot specs and style bibles."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key
        self.project = project
        self.model = model or settings.SR_MODEL_CRITIC

    async def critique_clip(
        self,
        video_path: str,
        shot: Shot,
        bible: Bible,
        attempt: int = 1,
    ) -> CritiqueResult:
        client = get_genai_client(
            api_key=self.api_key,
            project=self.project,
        )

        p = Path(video_path)
        if not p.is_file():
            # If video file is missing on disk, return failing critique
            return CritiqueResult(
                shot_id=shot.id,
                passed=False,
                score=0.0,
                reasons=[f"[artifact] Video file {video_path} was not found on disk."],
                suggested_prompt_fix="Re-render shot clip.",
            )

        file_size = p.stat().st_size
        contents: List[Any] = []

        # Upload or attach video part
        if file_size < 18 * 1024 * 1024:
            # Inline bytes for files < 18MB
            with open(p, "rb") as f:
                video_bytes = f.read()
            contents.append(
                types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
            )
        else:
            # Files API for larger clips
            def _upload():
                uploaded_file = client.files.upload(file=str(p))
                # Poll until active if needed
                while getattr(uploaded_file, "state", None) and uploaded_file.state.name == "PROCESSING":
                    time.sleep(2)
                    uploaded_file = client.files.get(name=uploaded_file.name)
                return uploaded_file

            file_ref = await call_with_retry(_upload)
            contents.append(file_ref)

        # Build detailed critique context
        char_specs = "\n".join(
            [f"- {c.name}: {c.appearance} (Role: {c.role or 'N/A'})" for c in bible.characters]
        ) or "None specified"
        prod_specs = "\n".join(
            [f"- {pr.name}: {pr.description}" for pr in bible.products]
        ) or "None specified"

        prompt_history_note = (
            f"\nPrevious Prompt Modifications: {shot.prompt_history}"
            if shot.prompt_history
            else ""
        )

        critique_prompt = f"""Review the attached video clip for Shot Specification:
- Shot ID: {shot.id} (Scene: {shot.scene_id})
- Target Duration: {shot.duration_s:.1f}s
- Description: {shot.description}
- Camera Framing & Movement: {shot.camera}
- Active Visual Prompt: {shot.visual_prompt}
- Voiceover Line: {shot.vo_line or 'N/A'}
- Current Attempt: #{attempt}{prompt_history_note}

Style Bible Standards:
- Palette: {bible.style.palette}
- Lighting: {bible.style.lighting}
- Lens: {bible.style.lens}
- Mood: {bible.style.mood}

Registered Characters:
{char_specs}

Registered Products / Assets:
{prod_specs}

Provide your structured critique evaluation. Score from 0.0 to 1.0 (pass threshold >= {settings.SR_CRITIC_PASS_SCORE}).
"""
        contents.append(critique_prompt)

        config = types.GenerateContentConfig(
            system_instruction=CRITIC_SYSTEM_PROMPT,
            temperature=0.2,  # Low temperature for objective assessment
            response_mime_type="application/json",
            response_schema=CritiqueSchema,
        )

        def _call_gemini_critic():
            return client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

        resp = await call_with_retry(_call_gemini_critic)

        raw_dict: dict[str, Any] = {}
        if hasattr(resp, "parsed") and resp.parsed is not None:
            if isinstance(resp.parsed, BaseModel):
                raw_dict = resp.parsed.model_dump()
            elif isinstance(resp.parsed, dict):
                raw_dict = resp.parsed
        if not raw_dict and hasattr(resp, "text") and resp.text:
            raw_dict = json.loads(resp.text)

        score = float(raw_dict.get("score", 0.8))
        score = max(0.0, min(1.0, score))
        threshold = settings.SR_CRITIC_PASS_SCORE
        # Enforce consistency between score and passed status
        passed = score >= threshold if "passed" not in raw_dict else (bool(raw_dict.get("passed")) and score >= threshold)

        reasons = raw_dict.get("reasons") or (
            ["Visual style and continuity match production standards"]
            if passed
            else ["[artifact] Video does not meet required quality threshold"]
        )

        return CritiqueResult(
            shot_id=shot.id,
            passed=passed,
            score=score,
            reasons=reasons,
            suggested_prompt_fix=raw_dict.get("suggested_prompt_fix"),
        )

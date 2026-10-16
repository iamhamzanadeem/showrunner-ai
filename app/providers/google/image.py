from __future__ import annotations

import base64
import logging
from pathlib import Path
import re
from typing import Any, List, Optional
from google.genai import types

from app.config import settings
from app.models import BibleStyle
from app.providers.base import BaseImageProvider
from app.providers.google.client import call_with_retry, get_genai_client

logger = logging.getLogger("showrunner.providers.google.image")


def _resolve_image_path(ref_path: Optional[str]) -> Optional[Path]:
    """Resolves a reference image path or /media/ URI to a local file on disk."""
    if not ref_path:
        return None
    p = Path(ref_path)
    if p.is_file():
        return p
    # If it is a /media/ path e.g. /media/job_xxx/ref_images/img.png
    clean_path = ref_path.lstrip("/")
    if clean_path.startswith("media/"):
        clean_path = clean_path[len("media/") :]
    alt_p = settings.DATA_DIR / "jobs" / clean_path
    if alt_p.is_file():
        return alt_p
    # Also try directly under DATA_DIR
    direct_p = settings.DATA_DIR / clean_path
    if direct_p.is_file():
        return direct_p
    return None


def _load_image_part(img_path: Path) -> Optional[types.Part]:
    """Loads image bytes from disk and returns a types.Part object."""
    try:
        with open(img_path, "rb") as f:
            data = f.read()
        mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
        return types.Part.from_bytes(data=data, mime_type=mime)
    except Exception as e:
        logger.warning("Failed to load reference image %s: %s", img_path, e)
        return None


def _save_image_from_response(resp: Any, out_path: Path) -> bool:
    """Extracts the generated image from generate_content response parts and writes PNG."""
    candidates = getattr(resp, "candidates", None)
    if not candidates:
        return False

    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            # 1. Try as_image() method
            if hasattr(part, "as_image"):
                try:
                    img = part.as_image()
                    if img is not None:
                        img.save(out_path, format="PNG")
                        return True
                except Exception as e:
                    logger.debug("as_image failed: %s", e)

            # 2. Try inline_data
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                raw = inline.data
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)
                with open(out_path, "wb") as f:
                    f.write(raw)
                return True

    return False


class GoogleImageProvider(BaseImageProvider):
    """
    Gemini 3.1 Flash Image (Nano Banana 2) keyframe and reference sheet generator.
    Passes visual bible constraints and reference image parts for aesthetic consistency.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key
        self.project = project
        self.model = model or settings.SR_MODEL_IMAGE

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
        client = get_genai_client(api_key=self.api_key, project=self.project)
        out_dir = job_dir or settings.DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{shot_id or 'shot'}_att{attempt}_keyframe.png"
        out_path = out_dir / filename

        # Assemble rich multimodal prompt
        palette_str = ", ".join(style.palette) if style.palette else "Cinematic contrast"
        full_prompt = (
            f"Cinematic keyframe photograph for Scene {scene_num}, Shot {shot_num} (Attempt #{attempt}):\n"
            f"Visual Action: {prompt}\n"
            f"Aesthetic Bible Standards: Color palette [{palette_str}], Lighting: {style.lighting}, "
            f"Lens & Optics: {style.lens}, Mood & Tone: {style.mood}.\n"
            f"16:9 widescreen composition, broadcast feature film quality, highly detailed."
        )

        contents: List[Any] = [full_prompt]

        # Add up to 3 reference images (previous frame, character/product sheets)
        if ref_image:
            resolved_p = _resolve_image_path(ref_image)
            if resolved_p:
                part = _load_image_part(resolved_p)
                if part:
                    contents.append(part)

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            temperature=0.7,
        )

        def _call_nano_banana():
            return client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

        resp = await call_with_retry(_call_nano_banana)
        saved = _save_image_from_response(resp, out_path)
        if not saved:
            raise RuntimeError(
                f"Failed to extract image from Google Image model '{self.model}' response for shot {shot_id}."
            )

        return str(out_path)

    async def generate_ref_sheet(
        self,
        entity_type: str,
        name: str,
        description: str,
        style: BibleStyle,
        job_dir: Path,
    ) -> str:
        client = get_genai_client(api_key=self.api_key, project=self.project)
        job_dir.mkdir(parents=True, exist_ok=True)
        filename = f"ref_{entity_type}_{re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())}.png"
        out_path = job_dir / filename

        palette_str = ", ".join(style.palette) if style.palette else "Cohesive cinematic"
        prompt = (
            f"Official Film Production Bible Reference Sheet for {entity_type.upper()}: '{name}'.\n"
            f"Description & Details: {description}\n"
            f"Aesthetic Alignment: Palette [{palette_str}], Lighting: {style.lighting}, Mood: {style.mood}.\n"
            f"Clear full-body or turnaround hero model sheet presentation, neutral cinematic studio background, 16:9."
        )

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            temperature=0.5,
        )

        def _call_nano_banana_ref():
            return client.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=config,
            )

        resp = await call_with_retry(_call_nano_banana_ref)
        saved = _save_image_from_response(resp, out_path)
        if not saved:
            raise RuntimeError(
                f"Failed to extract reference sheet image from Google Image model '{self.model}' for '{name}'."
            )

        return str(out_path)

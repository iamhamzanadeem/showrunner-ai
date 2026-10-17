from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional
from google.genai import types

from app.config import settings
from app.providers.base import BaseMusicProvider
from app.providers.google.client import call_with_retry, get_genai_client

logger = logging.getLogger("showrunner.providers.google.music")


class GoogleMusicProvider(BaseMusicProvider):
    """
    Google Lyria cinematic score and soundtrack generator.
    Uses the Interactions API with Lyria 3 Clip (<=30s) or Lyria 3 Pro (>30s, up to ~3 min).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key
        self.project = project
        self.model = model or settings.SR_MODEL_MUSIC

    async def generate_music(
        self,
        prompt: str,
        duration_s: float,
        job_dir: Optional[Path] = None,
    ) -> str:
        client = get_genai_client(api_key=self.api_key, project=self.project)
        out_dir = job_dir or settings.DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        # Select model and prompt based on duration
        if duration_s > 30.0:
            # Lyria 3 Pro: full compositions up to 184s ("lyria-3.5" does not exist on Vertex).
            target_model = settings.SR_MODEL_MUSIC_LONG
            target_s = int(min(duration_s, 180))
            music_prompt = (
                f"A {target_s}-second cinematic orchestral and ambient score. "
                f"Mood: {prompt}. Full production mix, dynamic crescendo, instrumental only, no vocals."
            )
        else:
            target_model = self.model
            music_prompt = (
                f"Cinematic atmospheric soundtrack score. "
                f"Mood: {prompt}. Instrumental only, no vocals."
            )

        # Primary path: Interactions API per documentation
        try:
            def _call_lyria():
                return client.interactions.create(
                    model=target_model,
                    input=music_prompt,
                )

            it = await call_with_retry(_call_lyria)
            output_audio = getattr(it, "output_audio", None)
            if output_audio and getattr(output_audio, "data", None):
                raw = output_audio.data
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)

                # Determine file extension based on magic bytes
                ext = ".wav" if raw.startswith(b"RIFF") else ".mp3"
                out_path = out_dir / f"soundtrack{ext}"
                with open(out_path, "wb") as f:
                    f.write(raw)
                return str(out_path)

        except (AttributeError, Exception) as primary_exc:
            logger.warning(
                "Primary Interactions API Lyria music generation failed (%s); attempting generate_content fallback",
                primary_exc,
            )

        # Fallback path: generate_content with AUDIO modality
        try:
            config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                temperature=0.8,
            )

            def _call_generate_content_music():
                return client.models.generate_content(
                    model=target_model,
                    contents=music_prompt,
                    config=config,
                )

            resp = await call_with_retry(_call_generate_content_music)

            candidates = getattr(resp, "candidates", None) or []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        raw_data = inline.data
                        if isinstance(raw_data, str):
                            raw_data = base64.b64decode(raw_data)
                        ext = ".wav" if raw_data.startswith(b"RIFF") else ".mp3"
                        out_path = out_dir / f"soundtrack{ext}"
                        with open(out_path, "wb") as f:
                            f.write(raw_data)
                        return str(out_path)

        except Exception as fallback_exc:
            logger.exception("Fallback generate_content music failed: %s", fallback_exc)
            raise RuntimeError(
                f"Both Interactions API and generate_content music failed for prompt: '{prompt}'"
            ) from fallback_exc

        raise RuntimeError(f"Lyria generated no soundtrack audio for prompt: '{prompt}'.")

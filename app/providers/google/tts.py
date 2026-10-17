from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional
import wave
from google.genai import types

from app.config import settings
from app.providers.base import BaseTTSProvider
from app.providers.google.client import call_with_retry, get_genai_client

logger = logging.getLogger("showrunner.providers.google.tts")


def _write_pcm_or_wav(audio_data: bytes, out_path: Path, sample_rate: int = 24000) -> None:
    """Writes audio data to a WAV file, wrapping raw PCM in RIFF header if needed."""
    if audio_data.startswith(b"RIFF"):
        # Already standard WAV format
        with open(out_path, "wb") as f:
            f.write(audio_data)
    else:
        # Raw PCM 16-bit mono 24kHz
        with wave.open(str(out_path), "wb") as wav_out:
            wav_out.setnchannels(1)
            wav_out.setsampwidth(2)  # 16-bit
            wav_out.setframerate(sample_rate)
            wav_out.writeframes(audio_data)


class _SkipInteractions(Exception):
    """Internal sentinel: Interactions API not applicable for this client."""


class GoogleTTSProvider(BaseTTSProvider):
    """
    Gemini Text-to-Speech voiceover engine.
    Uses official Interactions API with speech_metadata styling and fallback to generate_content.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
        voice_name: Optional[str] = None,
    ):
        self.api_key = api_key
        self.project = project
        self.model = model or settings.SR_MODEL_TTS
        self.voice_name = voice_name or settings.SR_TTS_VOICE

    async def generate_voiceover(
        self,
        text: str,
        duration_s: Optional[float] = None,
        voice_name: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
    ) -> str:
        client = get_genai_client(api_key=self.api_key, project=self.project)
        out_dir = job_dir or settings.DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{shot_id or 'vo'}_speech.wav"
        out_path = out_dir / filename

        active_voice = voice_name or self.voice_name

        # Vertex AI does not serve Gemini TTS models through the Interactions API
        # ("Model ... is not supported by the Interactions API"), so go straight to
        # generate_content with the AUDIO modality there.
        use_interactions = getattr(client, "vertexai", False) is not True

        # Primary path: Interactions API per official documentation (AI Studio)
        try:
            if not use_interactions:
                raise _SkipInteractions()

            def _call_interactions():
                return client.interactions.create(
                    model=self.model,
                    input=[
                        {
                            "type": "user_input",
                            "content": [
                                {
                                    "type": "text",
                                    "text": text,
                                    "annotations": [
                                        {
                                            "type": "speech_metadata",
                                            "style": "warm, confident, cinematic narrator",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    response_format={"type": "audio"},
                    generation_config={"speech_config": [{"voice": active_voice}]},
                )

            it = await call_with_retry(_call_interactions)
            output_audio = getattr(it, "output_audio", None)
            if output_audio and getattr(output_audio, "data", None):
                raw = output_audio.data
                if isinstance(raw, str):
                    raw = base64.b64decode(raw)
                _write_pcm_or_wav(raw, out_path)
                return str(out_path)

        except _SkipInteractions:
            pass
        except (AttributeError, Exception) as primary_exc:
            logger.warning(
                "Primary Interactions API TTS failed (%s); attempting fallback via generate_content",
                primary_exc,
            )

        # Fallback path: generate_content with AUDIO modality
        try:
            # Same model: current Gemini TTS models also serve generate_content.
            fallback_model = self.model
            tts_prompt = f"Say in a warm, confident, cinematic narrator voice at a natural, unhurried but not slow pace: {text}"

            speech_config = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=active_voice
                    )
                )
            )

            config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=speech_config,
            )

            def _call_generate_content_tts():
                return client.models.generate_content(
                    model=fallback_model,
                    contents=tts_prompt,
                    config=config,
                )

            resp = await call_with_retry(_call_generate_content_tts)

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
                        _write_pcm_or_wav(raw_data, out_path)
                        return str(out_path)

        except Exception as fallback_exc:
            logger.exception("Fallback generate_content TTS failed: %s", fallback_exc)
            raise RuntimeError(
                f"Both Interactions API and generate_content TTS failed for text: '{text[:40]}...'"
            ) from fallback_exc

        raise RuntimeError(f"TTS generated no audio output for shot {shot_id}.")

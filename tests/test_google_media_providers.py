from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.models import BibleStyle
from app.providers.google.image import GoogleImageProvider
from app.providers.google.music import GoogleMusicProvider
from app.providers.google.tts import GoogleTTSProvider
from app.providers.google.video import GoogleVideoProvider


@pytest.mark.asyncio
async def test_google_image_keyframe(tmp_path: Path):
    mock_resp = MagicMock()
    mock_part = MagicMock()
    # Mock inline_data with 1x1 png bytes
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n"
        b"-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    mock_part.as_image.return_value = None
    mock_part.inline_data.data = png_bytes
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_resp.candidates = [mock_candidate]

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.providers.google.image.get_genai_client", return_value=mock_client):
        provider = GoogleImageProvider(api_key="test-key", model="gemini-3.1-flash-image")
        style = BibleStyle(
            palette=["#0A0F1D", "#00F0FF"],
            lighting="Volumetric neon haze",
            lens="35mm anamorphic",
            mood="Cyberpunk noir",
        )
        out_file = await provider.generate_keyframe(
            prompt="Protagonist walks through rainy neon alleyway",
            style=style,
            job_dir=tmp_path,
            shot_id="shot_1_1",
            scene_num=1,
            shot_num=1,
            attempt=1,
        )

        assert Path(out_file).is_file()
        assert Path(out_file).stat().st_size > 0
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.1-flash-image"
        assert call_kwargs["config"].response_modalities == ["IMAGE"]


@pytest.mark.asyncio
async def test_google_image_ref_sheet(tmp_path: Path):
    mock_resp = MagicMock()
    mock_part = MagicMock()
    mock_part.as_image.return_value = None
    mock_part.inline_data.data = b"\x89PNG\r\n\x1a\nfake_ref_sheet"
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_resp.candidates = [mock_candidate]

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.providers.google.image.get_genai_client", return_value=mock_client):
        provider = GoogleImageProvider(api_key="test-key")
        style = BibleStyle(
            palette=["#111111"],
            lighting="Studio rim",
            lens="50mm",
            mood="Sleek",
        )
        out_file = await provider.generate_ref_sheet(
            entity_type="character",
            name="Apex Hero",
            description="Tactical suit with cybernetics",
            style=style,
            job_dir=tmp_path,
        )

        assert Path(out_file).is_file()
        assert "ref_character_apex_hero.png" in out_file


@pytest.mark.asyncio
async def test_google_video_duration_snapping_and_download(tmp_path: Path):
    mock_op = MagicMock()
    mock_op.done = True
    mock_op.error = None

    mock_vid = MagicMock()
    mock_vid.video.video_bytes = b"\x00\x00\x00\x18ftypmp42fake_veo_clip"
    mock_op.response.generated_videos = [mock_vid]

    mock_client = MagicMock()
    mock_client.models.generate_videos.return_value = mock_op

    with patch("app.providers.google.video.get_genai_client", return_value=mock_client):
        provider = GoogleVideoProvider(
            api_key="test-key",
            model="veo-3.1-fast-generate-preview",
            resolution="720p",
            aspect_ratio="16:9",
        )

        # Test duration 7.2s snaps to 8s
        out_file = await provider.generate_clip(
            prompt="A sleek spaceship accelerates into warp drive",
            duration_s=7.2,
            job_dir=tmp_path,
            shot_id="shot_1_2",
            scene_num=1,
            shot_num=2,
            attempt=1,
        )

        assert Path(out_file).is_file()
        assert Path(out_file).stat().st_size > 0

        mock_client.models.generate_videos.assert_called_once()
        call_kwargs = mock_client.models.generate_videos.call_args.kwargs
        assert call_kwargs["model"] == "veo-3.1-fast-generate-preview"
        assert call_kwargs["config"].duration_seconds == 8
        assert call_kwargs["config"].aspect_ratio == "16:9"
        assert call_kwargs["config"].resolution == "720p"


@pytest.mark.asyncio
async def test_google_tts_interactions_api(tmp_path: Path):
    mock_interaction = MagicMock()
    mock_audio = MagicMock()
    fake_wav_data = b"RIFF" + b"\x00" * 36 + b"WAVEfmt "
    mock_audio.data = base64.b64encode(fake_wav_data).decode("ascii")
    mock_interaction.output_audio = mock_audio

    mock_client = MagicMock()
    mock_client.interactions.create.return_value = mock_interaction

    with patch("app.providers.google.tts.get_genai_client", return_value=mock_client):
        provider = GoogleTTSProvider(
            api_key="test-key",
            model="gemini-3.8-flash-tts",
            voice_name="Kore",
        )
        out_file = await provider.generate_voiceover(
            text="The future is closer than you think.",
            duration_s=4.0,
            job_dir=tmp_path,
            shot_id="shot_1_1",
        )

        assert Path(out_file).is_file()
        mock_client.interactions.create.assert_called_once()
        call_kwargs = mock_client.interactions.create.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.8-flash-tts"


@pytest.mark.asyncio
async def test_google_tts_fallback_generate_content(tmp_path: Path):
    mock_client = MagicMock()
    # Primary interactions raises AttributeError (older SDK or unavailable endpoint)
    mock_client.interactions.create.side_effect = AttributeError("interactions not supported")

    mock_resp = MagicMock()
    mock_part = MagicMock()
    # 24kHz raw PCM samples
    mock_part.inline_data.data = b"\x00\x00" * 240
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    mock_resp.candidates = [mock_candidate]
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.providers.google.tts.get_genai_client", return_value=mock_client):
        provider = GoogleTTSProvider(api_key="test-key", model="gemini-3.8-flash-tts")
        out_file = await provider.generate_voiceover(
            text="Fallback speech synthesis.",
            job_dir=tmp_path,
            shot_id="shot_1_2",
        )

        assert Path(out_file).is_file()
        assert Path(out_file).name.endswith(".wav")
        mock_client.models.generate_content.assert_called_once()


@pytest.mark.asyncio
async def test_google_music_duration_model_selection(tmp_path: Path):
    mock_client = MagicMock()

    mock_interaction = MagicMock()
    mock_audio = MagicMock()
    mock_audio.data = b"\xff\xfb\x90\x44" + b"\x00" * 100  # MP3 sync word
    mock_interaction.output_audio = mock_audio
    mock_client.interactions.create.return_value = mock_interaction

    with patch("app.providers.google.music.get_genai_client", return_value=mock_client):
        provider = GoogleMusicProvider(
            api_key="test-key",
            model="lyria-3-clip-preview",
        )

        # 1. Short track (<= 30s) -> uses lyria-3-clip-preview
        short_file = await provider.generate_music(
            prompt="High energy electronic beats",
            duration_s=25.0,
            job_dir=tmp_path,
        )
        assert Path(short_file).is_file()
        first_call_kwargs = mock_client.interactions.create.call_args.kwargs
        assert first_call_kwargs["model"] == "lyria-3-clip-preview"

        # 2. Long track (> 30s) -> uses lyria-3.5 with duration in prompt
        long_file = await provider.generate_music(
            prompt="Cinematic orchestral adventure",
            duration_s=120.0,
            job_dir=tmp_path,
        )
        assert Path(long_file).is_file()
        second_call_kwargs = mock_client.interactions.create.call_args.kwargs
        assert second_call_kwargs["model"] == "lyria-3.5"
        assert "120-second" in second_call_kwargs["input"]

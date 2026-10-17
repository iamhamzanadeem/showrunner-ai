from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.models import Bible, BibleStyle, CritiqueResult, Plan, Shot, ShotStatus, VideoMode
from app.providers.google.critic import GoogleCriticProvider
from app.providers.google.image import GoogleImageProvider
from app.providers.google.music import GoogleMusicProvider
from app.providers.google.planner import GooglePlannerProvider
from app.providers.google.tts import GoogleTTSProvider
from app.providers.google.video import GoogleVideoProvider


# -----------------------------------------------------------------------------
# 1. PLANNER TESTS: Model ID, Response Schema, Duration Snapping (4/6/8)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_planner_request_and_duration_snapping():
    """Verifies planner request uses model ID, response_schema, and snaps durations to 4/6/8."""
    mock_resp = MagicMock()
    mock_resp.parsed = {
        "title": "Neon Horizon",
        "logline": "A rogue courier discovers the core.",
        "target_duration_s": 30.0,
        "bible": {
            "style": {
                "palette": ["#09090B", "#00F0FF", "#7000FF"],
                "lighting": "Atmospheric neon rim",
                "lens": "35mm anamorphic",
                "mood": "Futuristic cyberpunk",
            },
            "characters": [
                {
                    "name": "Kaelen",
                    "appearance": "Matte-black courier armor with amber visor",
                    "role": "Courier",
                }
            ],
            "products": [
                {
                    "name": "Pulse Shard",
                    "description": "Crystalline energy cell pulsing with azure light",
                }
            ],
        },
        "scenes": [
            {
                "id": "scene_1",
                "scene_number": 1,
                "title": "The Breach",
                "summary": "Infiltration of the vault.",
                "shots": [
                    {
                        "id": "shot_1_1",
                        "scene_id": "scene_1",
                        "duration_s": 4.8,  # Should snap to 4.0
                        "description": "Kaelen cuts through the bulkhead",
                        "camera": "Low angle push in",
                        "visual_prompt": "Cinematic shot of courier cutting steel",
                        "vo_line": "Target acquired.",
                        "characters_referenced": ["Kaelen"],
                        "products_referenced": [],
                        "chain_from_previous": False,
                    },
                    {
                        "id": "shot_1_2",
                        "scene_id": "scene_1",
                        "duration_s": 6.5,  # Should snap to 6.0
                        "description": "The bulkhead drops revealing the shard",
                        "camera": "Medium pan",
                        "visual_prompt": "Steam clears revealing the glowing shard",
                        "vo_line": "No alarms. Not yet.",
                        "characters_referenced": ["Kaelen"],
                        "products_referenced": ["Pulse Shard"],
                        "chain_from_previous": True,
                    },
                    {
                        "id": "shot_1_3",
                        "scene_id": "scene_1",
                        "duration_s": 9.2,  # Should snap to 8.0
                        "description": "Alarms trigger as Kaelen grabs the shard",
                        "camera": "Rapid handheld dolly",
                        "visual_prompt": "Red emergency lights flash as sirens sound",
                        "vo_line": "Time to run.",
                        "characters_referenced": ["Kaelen"],
                        "products_referenced": ["Pulse Shard"],
                        "chain_from_previous": True,
                    },
                ],
            }
        ],
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.providers.google.planner.get_genai_client", return_value=mock_client):
        planner = GooglePlannerProvider(api_key="test-key", model="gemini-3.8-flash")
        plan = await planner.generate_plan(
            brief="Cyberpunk heist for the Pulse Shard",
            mode=VideoMode.ad,
        )

        assert isinstance(plan, Plan)
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.8-flash"
        assert call_kwargs["config"].response_mime_type == "application/json"
        assert call_kwargs["config"].response_schema is not None

        shots = plan.scenes[0].shots
        assert len(shots) == 3
        # Strict duration snapping to 4, 6, 8
        assert shots[0].duration_s == 4.0
        assert shots[1].duration_s == 6.0
        assert shots[2].duration_s == 8.0


# -----------------------------------------------------------------------------
# 2. CRITIC TESTS: JSON Parsing & Threshold Enforcement (0.7)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_critic_parses_json_and_enforces_threshold(tmp_path: Path):
    """Verifies critic parses JSON to CritiqueResult and enforces 0.7 score threshold."""
    dummy_clip = tmp_path / "shot.mp4"
    dummy_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)

    # Sub-case A: Score >= 0.70 passes
    mock_resp_pass = MagicMock()
    mock_resp_pass.parsed = {
        "passed": True,
        "score": 0.85,
        "reasons": ["Consistent character costume", "Accurate neon palette"],
        "suggested_prompt_fix": None,
    }

    # Sub-case B: Score < 0.70 fails even if model claims passed
    mock_resp_fail = MagicMock()
    mock_resp_fail.parsed = {
        "passed": True,  # Discrepancy from LLM
        "score": 0.58,  # Below 0.7 threshold
        "reasons": ["[drift] Character visor color changed from amber to blue"],
        "suggested_prompt_fix": "Enforce amber visor",
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [mock_resp_pass, mock_resp_fail]

    shot = Shot(
        id="shot_1_1",
        scene_id="scene_1",
        duration_s=6.0,
        description="Courier running",
        camera="Tracking",
        visual_prompt="Courier running through neon streets",
    )
    bible = Bible(
        style=BibleStyle(palette=["#000"], lighting="Neon", lens="35mm", mood="Dark")
    )

    with patch("app.providers.google.critic.get_genai_client", return_value=mock_client):
        critic = GoogleCriticProvider(api_key="test-key", model="gemini-3.8-flash")

        # 1. Passing call
        res_pass = await critic.critique_clip(str(dummy_clip), shot, bible, attempt=1)
        assert isinstance(res_pass, CritiqueResult)
        assert res_pass.passed is True
        assert res_pass.score == 0.85

        # 2. Failing call (threshold enforcement)
        res_fail = await critic.critique_clip(str(dummy_clip), shot, bible, attempt=2)
        assert isinstance(res_fail, CritiqueResult)
        assert res_fail.passed is False  # Enforced < 0.7
        assert res_fail.score == 0.58
        assert "[drift]" in res_fail.reasons[0]
        assert res_fail.suggested_prompt_fix == "Enforce amber visor"


# -----------------------------------------------------------------------------
# 3. VEO 3.1 TESTS: Image-to-Video, Config, Duration Snapping, Polling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_veo_image_to_video_and_polling(tmp_path: Path):
    """Verifies Veo passes image, snaps duration, configures 720p/16:9, and polls until done."""
    dummy_frame = tmp_path / "frame.png"
    dummy_frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    # Mock Operation: initially done=False, next get() returns done=True
    op_running = MagicMock()
    op_running.done = False
    op_running.error = None

    op_finished = MagicMock()
    op_finished.done = True
    op_finished.error = None
    mock_vid = MagicMock()
    mock_vid.video.video_bytes = b"\x00\x00\x00\x18ftypmp42veo_rendered_data"
    op_finished.response.generated_videos = [mock_vid]

    mock_client = MagicMock()
    mock_client.models.generate_videos.return_value = op_running
    mock_client.operations.get.return_value = op_finished

    with patch("app.providers.google.video.get_genai_client", return_value=mock_client), \
         patch("asyncio.sleep", return_value=None):  # Fast polling in tests

        provider = GoogleVideoProvider(
            api_key="test-key",
            model="veo-3.1-fast-generate-preview",
            resolution="720p",
            aspect_ratio="16:9",
        )

        out_clip = await provider.generate_clip(
            prompt="Cinematic drone flight across jagged neon canyons",
            duration_s=6.1,  # Snaps to 6
            initial_frame=str(dummy_frame),
            job_dir=tmp_path,
            shot_id="shot_1_1",
        )

        assert Path(out_clip).is_file()
        assert Path(out_clip).read_bytes().endswith(b"veo_rendered_data")

        # Verify generate_videos call arguments
        mock_client.models.generate_videos.assert_called_once()
        call_kwargs = mock_client.models.generate_videos.call_args.kwargs
        assert call_kwargs["model"] == "veo-3.1-fast-generate-preview"
        assert call_kwargs["image"] is not None
        assert call_kwargs["config"].duration_seconds == 6
        assert call_kwargs["config"].resolution == "720p"
        assert call_kwargs["config"].aspect_ratio == "16:9"

        # Verify operation polling
        mock_client.operations.get.assert_called_once_with(op_running)


# -----------------------------------------------------------------------------
# 4. TTS TESTS: Interactions API with speech_config voice
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tts_interactions_api_with_voice(tmp_path: Path):
    """Verifies TTS uses interactions.create with speech_config voice."""
    mock_it = MagicMock()
    mock_audio = MagicMock()
    wav_header = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVEfmt "
    mock_audio.data = base64.b64encode(wav_header).decode("ascii")
    mock_it.output_audio = mock_audio

    mock_client = MagicMock()
    mock_client.interactions.create.return_value = mock_it

    with patch("app.providers.google.tts.get_genai_client", return_value=mock_client):
        provider = GoogleTTSProvider(
            api_key="test-key",
            model="gemini-3.8-flash-tts",
            voice_name="Kore",
        )

        out_vo = await provider.generate_voiceover(
            text="Every journey begins at the boundary.",
            duration_s=4.0,
            voice_name="Kore",
            job_dir=tmp_path,
            shot_id="shot_1_1",
        )

        assert Path(out_vo).is_file()
        mock_client.interactions.create.assert_called_once()
        call_kwargs = mock_client.interactions.create.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.8-flash-tts"
        assert call_kwargs["generation_config"]["speech_config"] == [{"voice": "Kore"}]
        assert call_kwargs["response_format"] == {"type": "audio"}


# -----------------------------------------------------------------------------
# 5. MUSIC TESTS: Lyria model selection based on duration (<=30s vs >30s)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_music_picks_correct_lyria_model(tmp_path: Path):
    """Verifies music picks lyria-3-clip-preview for <=30s and lyria-3-pro-preview for longer."""
    mock_client = MagicMock()
    mock_it = MagicMock()
    mock_audio = MagicMock()
    mock_audio.data = b"\xff\xfb\x90\x00" + b"\x00" * 40  # MP3 frame
    mock_it.output_audio = mock_audio
    mock_client.interactions.create.return_value = mock_it

    with patch("app.providers.google.music.get_genai_client", return_value=mock_client):
        provider = GoogleMusicProvider(
            api_key="test-key",
            model="lyria-3-clip-preview",
        )

        # <= 30 seconds
        res_short = await provider.generate_music(
            prompt="Driving cyberpunk electronic pulse",
            duration_s=30.0,
            job_dir=tmp_path,
        )
        assert Path(res_short).is_file()
        kwargs1 = mock_client.interactions.create.call_args.kwargs
        assert kwargs1["model"] == "lyria-3-clip-preview"

        # > 30 seconds
        res_long = await provider.generate_music(
            prompt="Driving cyberpunk electronic pulse",
            duration_s=90.0,
            job_dir=tmp_path,
        )
        assert Path(res_long).is_file()
        kwargs2 = mock_client.interactions.create.call_args.kwargs
        assert kwargs2["model"] == "lyria-3-pro-preview"
        assert "90-second" in kwargs2["input"]

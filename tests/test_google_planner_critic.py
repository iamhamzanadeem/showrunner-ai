from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.config import Settings
from app.models import (
    Bible,
    BibleStyle,
    CritiqueResult,
    Plan,
    Shot,
    ShotStatus,
    VideoMode,
)
from app.providers.factory import get_providers
from app.providers.google.client import call_with_retry, get_genai_client
from app.providers.google.critic import GoogleCriticProvider
from app.providers.google.planner import GooglePlannerProvider
from app.providers.mock import MockVideoProvider


def test_client_factory_api_key():
    with patch("google.genai.Client") as mock_client:
        cfg = Settings(
            SHOWRUNNER_PROVIDER="google",
            GEMINI_API_KEY="test-studio-key",
            DATA_DIR=Path("./data"),
        )
        client = get_genai_client(cfg=cfg)
        mock_client.assert_called_once_with(api_key="test-studio-key")
        assert client is not None


def test_client_factory_vertex():
    with patch("google.genai.Client") as mock_client:
        cfg = Settings(
            SHOWRUNNER_PROVIDER="google",
            GEMINI_API_KEY=None,
            GOOGLE_CLOUD_PROJECT="showrunner-gcp",
            GOOGLE_GENAI_USE_VERTEXAI=True,
            GOOGLE_CLOUD_LOCATION="us-central1",
            DATA_DIR=Path("./data"),
        )
        client = get_genai_client(cfg=cfg)
        mock_client.assert_called_once_with(
            vertexai=True,
            project="showrunner-gcp",
            location="us-central1",
        )
        assert client is not None


def test_client_factory_unconfigured_error():
    cfg = Settings(
        SHOWRUNNER_PROVIDER="mock",
        GEMINI_API_KEY=None,
        GOOGLE_CLOUD_PROJECT=None,
        GOOGLE_GENAI_USE_VERTEXAI=False,
        DATA_DIR=Path("./data"),
    )
    with pytest.raises(ValueError, match="Google GenAI credentials not configured"):
        get_genai_client(cfg=cfg)


@pytest.mark.asyncio
async def test_call_with_retry_backoff():
    call_counts = 0

    def flaky_func():
        nonlocal call_counts
        call_counts += 1
        if call_counts == 1:
            err = Exception("429 Resource Exhausted: Rate limit exceeded")
            setattr(err, "status", 429)
            raise err
        return "success"

    res = await call_with_retry(flaky_func, max_retries=2, base_delay=0.01)
    assert res == "success"
    assert call_counts == 2


@pytest.mark.asyncio
async def test_google_planner_structured_output():
    mock_resp = MagicMock()
    mock_resp.parsed = {
        "title": "Quantum Odyssey",
        "logline": "A journey beyond the event horizon.",
        "target_duration_s": 32.0,
        "bible": {
            "style": {
                "palette": ["#000000", "#00FFCC", "#FF0055"],
                "lighting": "High contrast cyan neon rim",
                "lens": "35mm anamorphic",
                "mood": "Atmospheric cyberpunk",
            },
            "characters": [
                {
                    "name": "Dr. Aris Thorne",
                    "appearance": "Weathered flight suit with cybernetic ocular rig",
                    "role": "Lead Navigator",
                }
            ],
            "products": [
                {
                    "name": "Chronos Drive",
                    "description": "Sleek obsidian quantum navigation core",
                }
            ],
        },
        "scenes": [
            {
                "id": "scene_1",
                "scene_number": 1,
                "title": "The Departure",
                "summary": "Aris prepares the ship for hyperjump.",
                "shots": [
                    {
                        "id": "shot_1_1",
                        "scene_id": "scene_1",
                        "duration_s": 5.2,  # Should snap to 6.0
                        "description": "Aris flips the ignition switches",
                        "camera": "Close-up slow push in",
                        "visual_prompt": "Cinematic close-up of pilot engaging switches",
                        "vo_line": "The jump coordinates are locked.",
                        "characters_referenced": ["Dr. Aris Thorne"],
                        "products_referenced": ["Chronos Drive"],
                        "chain_from_previous": False,
                    },
                    {
                        "id": "shot_1_2",
                        "scene_id": "scene_1",
                        "duration_s": 3.8,  # Should snap to 4.0
                        "description": "The engine flares alive",
                        "camera": "Wide tracking arc",
                        "visual_prompt": "Wide shot of quantum engine illuminating the hangar",
                        "vo_line": "No turning back now.",
                        "characters_referenced": [],
                        "products_referenced": ["Chronos Drive"],
                        "chain_from_previous": True,
                    },
                ],
            }
        ],
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.providers.google.planner.get_genai_client", return_value=mock_client):
        planner = GooglePlannerProvider(api_key="fake-key", model="gemini-3.8-flash")
        plan = await planner.generate_plan(
            brief="A pilot prepares for an interstellar jump with a chronos drive.",
            mode=VideoMode.ad,
        )

        assert isinstance(plan, Plan)
        assert plan.title == "Quantum Odyssey"
        assert len(plan.scenes) == 1
        assert len(plan.scenes[0].shots) == 2

        # Verify duration snapping
        shot1, shot2 = plan.scenes[0].shots[0], plan.scenes[0].shots[1]
        assert shot1.duration_s == 6.0  # snapped from 5.2
        assert shot2.duration_s == 4.0  # snapped from 3.8
        assert shot1.chain_from_previous is False
        assert shot2.chain_from_previous is True

        # Check model call args
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.8-flash"
        assert call_kwargs["config"].response_mime_type == "application/json"


@pytest.mark.asyncio
async def test_google_planner_edit_plan():
    from app.models import PlanEdit, Scene

    mock_resp = MagicMock()
    mock_resp.parsed = {
        "summary": "Director requested warmer tones in scene 1; adjusted lighting and shot 1_1 visual prompt.",
        "target_scene_ids": ["scene_1"],
        "target_shot_ids": ["shot_1_1"],
        "updated_shots": [
            {
                "id": "shot_1_1",
                "visual_prompt": "Warm golden light illuminates the pilot's cockpit",
                "description": "Cockpit in warm ambient sunlight",
                "camera": "Close-up",
                "vo_line": "The jump coordinates are locked.",
                "duration_s": 6.0,
            }
        ],
        "style_changes": {
            "palette": ["#1A0B02", "#D97706", "#F59E0B", "#FEF3C7"],
            "lighting": "Warm golden sunset key light",
            "lens": "35mm Prime",
            "mood": "Warm and optimistic",
        },
        "rationale": "Applied requested warm palette to scene 1",
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    plan = Plan(
        title="Odyssey",
        logline="A flight across the stars.",
        mode=VideoMode.ad,
        target_duration_s=30,
        total_duration_s=6.0,
        total_shots=1,
        bible=Bible(
            style=BibleStyle(
                palette=["#000000", "#FFFFFF"],
                lighting="Cold neon",
                lens="35mm",
                mood="Cold",
            )
        ),
        scenes=[
            Scene(
                id="scene_1",
                scene_number=1,
                title="Cockpit",
                summary="Entering jump",
                shots=[
                    Shot(
                        id="shot_1_1",
                        scene_id="scene_1",
                        duration_s=6.0,
                        description="Pilot engaging switches",
                        camera="Close-up",
                        visual_prompt="Cold cockpit",
                        status=ShotStatus.passed,
                    )
                ],
            )
        ],
    )

    with patch("app.providers.google.planner.get_genai_client", return_value=mock_client):
        planner = GooglePlannerProvider(api_key="fake-key", model="gemini-3.8-flash")
        edit = await planner.edit_plan(plan, "Make scene 1 warmer and golden")

        assert isinstance(edit, PlanEdit)
        assert edit.target_scene_ids == ["scene_1"]
        assert edit.target_shot_ids == ["shot_1_1"]
        assert len(edit.updated_shots) == 1
        assert "Warm golden" in edit.updated_shots[0].visual_prompt
        assert edit.style_changes is not None
        assert "#F59E0B" in edit.style_changes.palette


@pytest.mark.asyncio
async def test_google_critic_evaluation(tmp_path: Path):
    dummy_clip = tmp_path / "test_shot.mp4"
    dummy_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"fake mp4 content" * 10)

    mock_resp = MagicMock()
    mock_resp.parsed = {
        "passed": True,
        "score": 0.88,
        "reasons": [
            "Visual style and palette accurately match the production bible",
            "Character consistency maintained across keyframe frames",
        ],
        "suggested_prompt_fix": None,
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    shot = Shot(
        id="shot_1_1",
        scene_id="scene_1",
        duration_s=6.0,
        description="Establishing shot",
        camera="Static wide",
        visual_prompt="Cinematic wide shot",
        status=ShotStatus.generating,
    )
    bible = Bible(
        style=BibleStyle(
            palette=["#000000", "#00FFCC"],
            lighting="Volumetric neon",
            lens="35mm",
            mood="Moody",
        )
    )

    with patch("app.providers.google.critic.get_genai_client", return_value=mock_client):
        critic = GoogleCriticProvider(api_key="fake-key", model="gemini-3.8-flash")
        result = await critic.critique_clip(
            video_path=str(dummy_clip),
            shot=shot,
            bible=bible,
            attempt=1,
        )

        assert isinstance(result, CritiqueResult)
        assert result.passed is True
        assert result.score == 0.88
        assert len(result.reasons) == 2


def test_factory_hybrid_mode():
    cfg = Settings(
        SHOWRUNNER_PROVIDER="mock",
        SR_PROVIDER_PLANNER="google",
        SR_PROVIDER_VIDEO="mock",
        SR_PROVIDER_CRITIC="google",
        GEMINI_API_KEY="test-key",
        DATA_DIR=Path("./data"),
    )

    bundle = get_providers(cfg=cfg)
    assert bundle.provider_type == "hybrid"
    assert isinstance(bundle.planner, GooglePlannerProvider)
    assert isinstance(bundle.video, MockVideoProvider)
    assert isinstance(bundle.critic, GoogleCriticProvider)

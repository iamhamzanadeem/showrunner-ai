from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.models import Bible, BibleStyle, CritiqueResult, Shot, ShotStatus
from app.providers.google.critic import GoogleCriticProvider
from app.providers.mock import MockCriticProvider


def _get_text(part) -> str:
    if hasattr(part, "text") and part.text:
        return part.text
    return str(part)


@pytest.mark.asyncio
async def test_critic_with_references_and_previous_frame(tmp_path: Path):
    dummy_clip = tmp_path / "test_shot.mp4"
    dummy_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"fake mp4 content" * 10)

    sheet1 = tmp_path / "sheet1.png"
    sheet1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 20)
    sheet2 = tmp_path / "sheet2.png"
    sheet2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x02" * 20)
    prev_frame = tmp_path / "prev_frame.png"
    prev_frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x03" * 20)

    mock_resp = MagicMock()
    mock_resp.parsed = {
        "passed": True,
        "score": 0.92,
        "reasons": ["Character identity accurately matches reference sheets"],
        "suggested_prompt_fix": None,
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    shot = Shot(
        id="shot_1_1",
        scene_id="scene_1",
        duration_s=6.0,
        description="Hero shows gadget",
        camera="Close-up",
        visual_prompt="Hero holding device",
        status=ShotStatus.generating,
    )
    bible = Bible(
        style=BibleStyle(
            palette=["#000000", "#00FFCC"],
            lighting="Volumetric neon",
            lens="35mm",
            mood="Cyberpunk",
        )
    )

    with patch("app.providers.google.critic.get_genai_client", return_value=mock_client):
        critic = GoogleCriticProvider(api_key="fake-key", model="gemini-3.8-flash")
        result = await critic.critique_clip(
            video_path=str(dummy_clip),
            shot=shot,
            bible=bible,
            attempt=1,
            reference_images=[str(sheet1), str(sheet2)],
            previous_frame=str(prev_frame),
        )

        assert isinstance(result, CritiqueResult)
        assert result.passed is True
        mock_client.models.generate_content.assert_called_once()
        contents = mock_client.models.generate_content.call_args.kwargs["contents"]

        # 1. Video part is first
        video_part = contents[0]
        assert hasattr(video_part, "inline_data")
        assert video_part.inline_data.mime_type == "video/mp4"

        # 2. Sheet 1 label and image part
        assert "REFERENCE SHEET 1 (character/product identity to match):" in _get_text(contents[1])
        sheet1_part = contents[2]
        assert hasattr(sheet1_part, "inline_data")
        assert sheet1_part.inline_data.mime_type == "image/png"
        assert sheet1_part.inline_data.data == sheet1.read_bytes()

        # 3. Sheet 2 label and image part
        assert "REFERENCE SHEET 2 (character/product identity to match):" in _get_text(contents[3])
        sheet2_part = contents[4]
        assert hasattr(sheet2_part, "inline_data")
        assert sheet2_part.inline_data.mime_type == "image/png"
        assert sheet2_part.inline_data.data == sheet2.read_bytes()

        # 4. Previous frame label and image part
        assert "PREVIOUS SHOT LAST FRAME (continuity reference):" in _get_text(contents[5])
        prev_part = contents[6]
        assert hasattr(prev_part, "inline_data")
        assert prev_part.inline_data.mime_type == "image/png"
        assert prev_part.inline_data.data == prev_frame.read_bytes()

        # 5. Critique prompt text mentions reference sheets and continuity
        critique_prompt_text = _get_text(contents[7])
        assert "reference sheets" in critique_prompt_text.lower()
        assert "[drift]/[wrong_product]" in critique_prompt_text
        assert "[continuity]" in critique_prompt_text


@pytest.mark.asyncio
async def test_critic_skips_unresolvable_references(tmp_path: Path):
    dummy_clip = tmp_path / "test_shot.mp4"
    dummy_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"fake mp4 content" * 10)

    valid_sheet = tmp_path / "valid_sheet.png"
    valid_sheet.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 20)

    missing_sheet = tmp_path / "nonexistent_sheet.png"
    missing_prev = tmp_path / "nonexistent_prev.png"

    mock_resp = MagicMock()
    mock_resp.parsed = {
        "passed": True,
        "score": 0.85,
        "reasons": ["Good pacing"],
        "suggested_prompt_fix": None,
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    shot = Shot(
        id="shot_1_1",
        scene_id="scene_1",
        duration_s=6.0,
        description="Hero shows gadget",
        camera="Close-up",
        visual_prompt="Hero holding device",
    )
    bible = Bible(
        style=BibleStyle(
            palette=["#000000"],
            lighting="Neon",
            lens="35mm",
            mood="Cyberpunk",
        )
    )

    with patch("app.providers.google.critic.get_genai_client", return_value=mock_client):
        critic = GoogleCriticProvider(api_key="fake-key", model="gemini-3.8-flash")
        result = await critic.critique_clip(
            video_path=str(dummy_clip),
            shot=shot,
            bible=bible,
            attempt=1,
            reference_images=[str(missing_sheet), str(valid_sheet)],
            previous_frame=str(missing_prev),
        )

        assert result.passed is True
        contents = mock_client.models.generate_content.call_args.kwargs["contents"]

        # Only one reference sheet image part was attached
        image_parts = [
            c for c in contents
            if hasattr(c, "inline_data") and getattr(c.inline_data, "mime_type", "").startswith("image/")
        ]
        assert len(image_parts) == 1
        assert image_parts[0].inline_data.data == valid_sheet.read_bytes()

        # Only REFERENCE SHEET 1 label is present; PREVIOUS SHOT LAST FRAME is absent
        all_text = " ".join(_get_text(c) for c in contents if hasattr(c, "text") or isinstance(c, str))
        assert "REFERENCE SHEET 1" in all_text
        assert "REFERENCE SHEET 2" not in all_text
        assert "PREVIOUS SHOT LAST FRAME" not in all_text


@pytest.mark.asyncio
async def test_critic_reference_sheets_cap_of_3(tmp_path: Path):
    dummy_clip = tmp_path / "test_shot.mp4"
    dummy_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"fake mp4 content" * 10)

    sheets = []
    for i in range(5):
        s = tmp_path / f"sheet_{i}.png"
        s.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 20)
        sheets.append(s)

    mock_resp = MagicMock()
    mock_resp.parsed = {
        "passed": True,
        "score": 0.90,
        "reasons": ["High quality"],
        "suggested_prompt_fix": None,
    }

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    shot = Shot(
        id="shot_1_1",
        scene_id="scene_1",
        duration_s=6.0,
        description="Hero shows gadget",
        camera="Close-up",
        visual_prompt="Hero holding device",
    )
    bible = Bible(
        style=BibleStyle(
            palette=["#000000"],
            lighting="Neon",
            lens="35mm",
            mood="Cyberpunk",
        )
    )

    with patch("app.providers.google.critic.get_genai_client", return_value=mock_client):
        critic = GoogleCriticProvider(api_key="fake-key", model="gemini-3.8-flash")
        result = await critic.critique_clip(
            video_path=str(dummy_clip),
            shot=shot,
            bible=bible,
            attempt=1,
            reference_images=[str(s) for s in sheets],
        )

        assert result.passed is True
        contents = mock_client.models.generate_content.call_args.kwargs["contents"]

        # Exactly 3 reference sheets attached
        image_parts = [
            c for c in contents
            if hasattr(c, "inline_data") and getattr(c.inline_data, "mime_type", "").startswith("image/")
        ]
        assert len(image_parts) == 3

        all_text = " ".join(_get_text(c) for c in contents if hasattr(c, "text") or isinstance(c, str))
        assert "REFERENCE SHEET 1" in all_text
        assert "REFERENCE SHEET 2" in all_text
        assert "REFERENCE SHEET 3" in all_text
        assert "REFERENCE SHEET 4" not in all_text
        assert "REFERENCE SHEET 5" not in all_text


@pytest.mark.asyncio
async def test_mock_critic_accepts_and_ignores_references(tmp_path: Path):
    dummy_clip = tmp_path / "test_shot.mp4"
    dummy_clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"fake mp4 content" * 10)
    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 20)

    shot = Shot(
        id="shot_1_1",
        scene_id="scene_1",
        duration_s=6.0,
        description="Hero shows gadget",
        camera="Close-up",
        visual_prompt="Hero holding device",
    )
    bible = Bible(
        style=BibleStyle(
            palette=["#000000"],
            lighting="Neon",
            lens="35mm",
            mood="Cyberpunk",
        )
    )

    mock_critic = MockCriticProvider()
    result = await mock_critic.critique_clip(
        video_path=str(dummy_clip),
        shot=shot,
        bible=bible,
        attempt=1,
        reference_images=[str(sheet)],
        previous_frame=str(sheet),
    )
    assert isinstance(result, CritiqueResult)
    assert result.passed is True

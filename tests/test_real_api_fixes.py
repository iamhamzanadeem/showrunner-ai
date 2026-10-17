"""Regression tests for bugs exposed by the first real Vertex AI runs."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.pipeline.assembler import VO_MAX_TEMPO, schedule_voiceovers
from app.providers.google.planner import _clean_palette
from app.providers.google.tts import GoogleTTSProvider
from app.providers.google.video import GoogleVideoProvider


def test_clean_palette_splits_collapsed_string():
    raw = ["#0A0B10','#1E2A4A','#5C7CFA','#D4AF37','#E2E8F0"]
    assert _clean_palette(raw) == ["#0A0B10", "#1E2A4A", "#5C7CFA", "#D4AF37", "#E2E8F0"]
    assert _clean_palette(["#abc", "deep teal"]) == ["#ABC", "deep teal"]
    assert _clean_palette(None) == []


def test_schedule_voiceovers_never_overlaps():
    # 9.4s line on a 4s shot followed by a short line on the next shot
    plan = schedule_voiceovers([("a", 0.0, 4.0, 9.4), ("b", 4.0, 6.0, 2.0)])
    assert plan["a"]["tempo"] == pytest.approx(VO_MAX_TEMPO)
    assert plan["b"]["start"] >= plan["a"]["end"]
    # A line that fits is untouched
    fit = schedule_voiceovers([("c", 8.0, 8.0, 3.0)])
    assert fit["c"] == {"start": 8.3, "end": 11.3, "tempo": 1.0}


@pytest.mark.asyncio
async def test_tts_vertex_uses_generate_content_with_same_model(tmp_path: Path):
    client = MagicMock()
    client.vertexai = True
    part = MagicMock()
    part.inline_data.data = b"RIFF" + b"\x00" * 100
    cand = MagicMock()
    cand.content.parts = [part]
    client.models.generate_content.return_value.candidates = [cand]
    with patch("app.providers.google.tts.get_genai_client", return_value=client):
        out = await GoogleTTSProvider(model="gemini-3.8-flash-tts").generate_voiceover(
            text="Hello.", job_dir=tmp_path, shot_id="s1"
        )
    assert Path(out).is_file()
    client.interactions.create.assert_not_called()
    assert client.models.generate_content.call_args.kwargs["model"] == "gemini-3.8-flash-tts"
    # The line alone: a style prefix would be spoken aloud by the TTS model
    assert client.models.generate_content.call_args.kwargs["contents"] == "Hello."


@pytest.mark.asyncio
async def test_veo_vertex_sets_generate_audio_and_studio_maps_model(tmp_path: Path):
    for is_vertex, expected_model in ((True, "veo-3.1-fast-generate-001"), (False, "veo-3.1-fast-generate-preview")):
        client = MagicMock()
        client.vertexai = is_vertex
        op = MagicMock()
        op.done = True
        op.error = None
        vid = MagicMock()
        vid.video.video_bytes = b"\x00\x00\x00\x18ftypmp42"
        op.response.generated_videos = [vid]
        client.models.generate_videos.return_value = op
        with patch("app.providers.google.video.get_genai_client", return_value=client):
            await GoogleVideoProvider(model="veo-3.1-fast-generate-001").generate_clip(
                prompt="x", duration_s=4, job_dir=tmp_path, shot_id=f"v{is_vertex}"
            )
        kwargs = client.models.generate_videos.call_args.kwargs
        assert kwargs["model"] == expected_model
        if is_vertex:
            assert kwargs["config"].generate_audio is False
        else:
            assert kwargs["config"].generate_audio is None


@pytest.mark.asyncio
async def test_keyframe_quota_failure_falls_back_to_text_to_video(tmp_path, monkeypatch):
    from app.config import settings
    from app.models import Job, JobStatus, VideoMode
    from app.pipeline.orchestrator import run_pipeline
    from app.providers.mock import MockImageProvider, MockVideoProvider

    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)
    orig_kf = MockImageProvider.generate_keyframe
    orig_clip = MockVideoProvider.generate_clip
    clip_calls = []

    async def kf_429(self, **kw):
        if kw["shot_id"] == "shot_2_1":
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return await orig_kf(self, **kw)

    async def rec_clip(self, **kw):
        clip_calls.append((kw["shot_id"], kw["initial_frame"]))
        return await orig_clip(self, **kw)

    monkeypatch.setattr(MockImageProvider, "generate_keyframe", kf_429)
    monkeypatch.setattr(MockVideoProvider, "generate_clip", rec_clip)
    job = Job(id="job_kf429", brief="A 30s ad for Lumina smart glass.", mode=VideoMode.ad)
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert ("shot_2_1", None) in clip_calls

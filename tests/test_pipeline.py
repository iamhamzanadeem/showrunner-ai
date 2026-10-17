import asyncio
from pathlib import Path
import subprocess
import pytest
from app.config import settings
from app.events import event_bus
from app.models import Job, JobStatus, VideoMode
from app.pipeline.orchestrator import _select_initial_frame, run_pipeline
from app.providers.mock import MockVideoProvider


@pytest.mark.asyncio
async def test_full_mock_pipeline_ad(tmp_path, monkeypatch):
    """
    Validates end-to-end production loop for Ad mode:
    - Planning & Bible reference sheets
    - Visual keyframes and frame chaining
    - Critic review loop with CRITIC_FAIL -> RETRY -> CRITIC_PASS
    - Subtitle burn-in, VO placement, ducked music mixing
    - Final MP4 duration matching target plan (±3s)
    """
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", test_data_dir)
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)

    job_id = "test_ad_pipeline_job"
    job = Job(
        id=job_id,
        brief="A high-octane 30s cyber commercial for Kinetic Zero quantum energy drink with neon city aesthetics.",
        mode=VideoMode.ad,
    )

    # Execute full orchestrator pipeline
    await run_pipeline(job)

    # 1. Pipeline status and URLs
    assert job.status == JobStatus.completed
    assert job.final_video_url is not None
    assert job.poster_url is not None
    assert job.captions_srt_url is not None

    # 2. Check final.mp4 and poster on disk
    job_dir = settings.get_job_dir(job.id)
    final_mp4 = job_dir / "final.mp4"
    assert final_mp4.exists()
    assert final_mp4.stat().st_size > 1000

    poster_jpg = job_dir / "poster.jpg"
    assert poster_jpg.exists()

    srt_file = job_dir / "captions.srt"
    assert srt_file.exists()

    # 3. Check ffprobe duration approx the plan duration (±3s)
    ffprobe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(final_mp4),
    ]
    result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
    actual_duration = float(result.stdout.strip())
    expected_duration = job.plan.total_duration_s
    assert abs(actual_duration - expected_duration) <= 3.0, (
        f"Actual duration {actual_duration}s differs from expected {expected_duration}s by more than 3s"
    )

    # 4. Check recorded events from event_bus
    history = await event_bus.get_history(job.id)
    steps = [e.step for e in history]
    for required_step in [
        "PLAN", "REF", "KEYFRAME", "GEN", "CHAIN",
        "CRITIC_FAIL", "RETRY", "CRITIC_PASS", "ASSEMBLE", "DONE"
    ]:
        assert required_step in steps, f"Expected step {required_step} not in events: {steps}"

    # 5. Assert: at least one CRITIC_FAIL then RETRY then CRITIC_PASS for the same shot
    fail_shot_ids = [e.shot_id for e in history if e.step == "CRITIC_FAIL" and e.shot_id]
    assert len(fail_shot_ids) >= 1, "Expected at least one shot to fail critique on first attempt"
    failed_shot = fail_shot_ids[0]

    # Verify event sequence for the failed shot
    shot_steps = [e.step for e in history if e.shot_id == failed_shot]
    assert "CRITIC_FAIL" in shot_steps
    assert "RETRY" in shot_steps
    assert "CRITIC_PASS" in shot_steps

    fail_idx = shot_steps.index("CRITIC_FAIL")
    retry_idx = shot_steps.index("RETRY")
    pass_idx = shot_steps.index("CRITIC_PASS")
    assert fail_idx < retry_idx < pass_idx, (
        f"Event order incorrect for shot {failed_shot}: {shot_steps}"
    )

    # 6. Check continuity chaining event occurred
    chain_events = [e for e in history if e.step == "CHAIN"]
    assert len(chain_events) >= 1
    assert chain_events[0].data is not None
    assert "chained_from" in chain_events[0].data
    assert "first_frame_url" in chain_events[0].data

    # 7. Check reference sheets generated
    assert len(job.character_refs) >= 1
    assert len(job.product_refs) >= 1
    for ref_url in list(job.character_refs.values()) + list(job.product_refs.values()):
        ref_filename = Path(ref_url).name
        assert (job_dir / ref_filename).exists()


def test_select_initial_frame_helper(tmp_path: Path):
    """Verifies that chained first frame wins, otherwise falls back to shot keyframe."""
    chained_frame = tmp_path / "chained_lastframe.png"
    keyframe = tmp_path / "shot_keyframe.png"

    # Neither exists
    assert _select_initial_frame(chained_frame, keyframe) is None

    # Only keyframe exists
    keyframe.write_bytes(b"keyframe_bytes")
    assert _select_initial_frame(None, keyframe) == str(keyframe)
    assert _select_initial_frame(chained_frame, keyframe) == str(keyframe)

    # Both exist -> chained first frame wins
    chained_frame.write_bytes(b"chained_bytes")
    assert _select_initial_frame(chained_frame, keyframe) == str(chained_frame)


@pytest.mark.asyncio
async def test_orchestrator_initial_frame_and_references(tmp_path: Path, monkeypatch):
    """
    Validates that orchestrator passes:
    - non-chained shot -> initial_frame == keyframe path
    - chained shot -> initial_frame == chained last frame path
    - reference_images == list of character/product reference URLs (max 3)
    """
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", test_data_dir)
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)

    job_id = "test_frame_and_ref_job"
    job = Job(
        id=job_id,
        brief="A 30s cyberpunk advertisement featuring cyber agent and quantum core device.",
        mode=VideoMode.ad,
    )

    recorded_calls = []
    orig_generate_clip = MockVideoProvider.generate_clip

    async def recording_generate_clip(self, **kwargs):
        recorded_calls.append(kwargs.copy())
        return await orig_generate_clip(self, **kwargs)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", recording_generate_clip)

    await run_pipeline(job)

    assert len(recorded_calls) > 0

    non_chained_calls = []
    chained_calls = []

    for call in recorded_calls:
        init_frame = call.get("initial_frame")
        assert init_frame is not None, "Every shot must have an initial_frame"
        if "_lastframe" in init_frame:
            chained_calls.append(call)
        elif "_keyframe" in init_frame:
            non_chained_calls.append(call)
        else:
            pytest.fail(f"Unexpected initial_frame format: {init_frame}")

    # Non-chained shots must use shot keyframe path (and the file must exist)
    assert len(non_chained_calls) >= 1
    for call in non_chained_calls:
        init_f = Path(call["initial_frame"])
        assert init_f.exists()
        assert "_keyframe.png" in init_f.name
        assert call["shot_id"] in init_f.name

    # Chained shots must use chained lastframe path (and the file must exist)
    assert len(chained_calls) >= 1
    for call in chained_calls:
        init_f = Path(call["initial_frame"])
        assert init_f.exists()
        assert "_lastframe.png" in init_f.name

    # Check reference_images passed to every generate_clip call
    for call in recorded_calls:
        assert "reference_images" in call
        refs = call["reference_images"]
        assert isinstance(refs, list)
        assert 1 <= len(refs) <= 3
        for ref_url in refs:
            assert ref_url.startswith("/media/")

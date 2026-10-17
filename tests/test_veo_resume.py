"""Resume reuses a saved Veo operation instead of paying for a second render."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.main import jobs_db
from app.models import Job, JobStatus, ShotStatus, VideoMode
from app.pipeline.orchestrator import recover_interrupted_job, run_pipeline
from app.pipeline.planner import save_job_state
from app.providers.google.video import GoogleVideoProvider
from app.providers.mock import MockVideoProvider

OP_NAME = "projects/p/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/abc"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)
    monkeypatch.setattr(settings, "MAX_PARALLEL_SCENES", 1)
    jobs_db.clear()


# ---------------------------------------------------------------- provider level (Google, mocked client)


def _op(done: bool, with_video: bool = True, error=None):
    op = MagicMock()
    op.name = OP_NAME
    op.done = done
    op.error = error
    if with_video:
        vid = MagicMock()
        vid.video.video_bytes = b"\x00\x00\x00\x18ftypmp42-clip"
        op.response.generated_videos = [vid]
    else:
        op.response = None
    return op


@pytest.mark.asyncio
async def test_google_resume_pending_operation_keeps_polling_no_new_submit(tmp_path, monkeypatch):
    client = MagicMock()
    client.vertexai = True
    client.operations.get.side_effect = [_op(False), _op(False), _op(True)]
    monkeypatch.setattr("app.providers.google.video.asyncio.sleep", _no_sleep)
    with patch("app.providers.google.video.get_genai_client", return_value=client):
        out = await GoogleVideoProvider().resume_clip(OP_NAME, job_dir=tmp_path, shot_id="shot_1_1", attempt=2)
    assert out and Path(out).name == "shot_1_1_att2.mp4" and Path(out).stat().st_size > 0
    client.models.generate_videos.assert_not_called()
    assert client.operations.get.call_count == 3
    assert client.operations.get.call_args_list[0].args[0].name == OP_NAME


@pytest.mark.asyncio
async def test_google_resume_completed_operation_downloads_result(tmp_path):
    client = MagicMock()
    client.vertexai = True
    client.operations.get.return_value = _op(True)
    with patch("app.providers.google.video.get_genai_client", return_value=client):
        out = await GoogleVideoProvider().resume_clip(OP_NAME, job_dir=tmp_path, shot_id="s", attempt=1)
    assert out and Path(out).read_bytes().endswith(b"-clip")
    client.models.generate_videos.assert_not_called()
    client.operations.get.assert_called_once()


@pytest.mark.asyncio
async def test_google_resume_missing_or_failed_operation_returns_none(tmp_path):
    for side_effect in (RuntimeError("404 NOT_FOUND"), [_op(True, with_video=False, error={"code": 13})]):
        client = MagicMock()
        client.vertexai = True
        client.operations.get.side_effect = side_effect
        with patch("app.providers.google.video.get_genai_client", return_value=client):
            assert await GoogleVideoProvider().resume_clip(OP_NAME, job_dir=tmp_path, shot_id="s") is None
        client.models.generate_videos.assert_not_called()


async def _no_sleep(*_a, **_k):
    return None


# ---------------------------------------------------------------- pipeline level (mock providers)


async def _interrupted_job(monkeypatch) -> Job:
    """Completes a mock production, then rewinds shot_1_1 to 'Veo op submitted, server died'."""
    job = Job(id="job_veoresume", brief="A 30s ad for Lumina smart glass.", mode=VideoMode.ad)
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    shot = job.plan.scenes[0].shots[0]
    Path(shot.video_url).unlink()  # the clip never reached disk
    shot.video_url = None
    shot.status = ShotStatus.generating
    shot.veo_operation_name = OP_NAME
    shot.veo_operation_attempt = 1
    shot.attempts = []
    job.status = JobStatus.generating
    job.final_video_url = None
    job.versions = []
    recover_interrupted_job(job)  # what startup does after a restart
    save_job_state(job)
    assert job.status == JobStatus.failed and shot.status == ShotStatus.failed
    return job


def _install_spies(monkeypatch, resume_impl):
    submits: list[str] = []
    resumes: list[tuple[str, str]] = []
    orig_generate = MockVideoProvider.generate_clip

    async def spy_generate(self, **kw):
        submits.append(kw["shot_id"])
        return await orig_generate(self, **kw)

    async def spy_resume(self, operation_name, job_dir=None, shot_id=None, attempt=1):
        resumes.append((shot_id, operation_name))
        return await resume_impl(job_dir, shot_id, attempt)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", spy_generate)
    monkeypatch.setattr(MockVideoProvider, "resume_clip", spy_resume)
    return submits, resumes


async def _write_clip(job_dir: Path, shot_id: str, attempt: int) -> str:
    """Stand-in for 'Veo operation finished': writes the clip the operation produced."""
    src = next(job_dir.glob("shot_1_2_att*.mp4"))
    out = job_dir / f"{shot_id}_att{attempt}.mp4"
    out.write_bytes(src.read_bytes())
    return str(out)


@pytest.mark.asyncio
async def test_pipeline_resume_with_completed_operation_does_not_resubmit(monkeypatch):
    job = await _interrupted_job(monkeypatch)
    submits, resumes = _install_spies(monkeypatch, _write_clip)
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert resumes == [("shot_1_1", OP_NAME)]
    assert "shot_1_1" not in submits  # no second paid render
    shot = job.plan.scenes[0].shots[0]
    assert shot.status == ShotStatus.passed and Path(shot.video_url).is_file()
    assert shot.veo_operation_name is None


@pytest.mark.asyncio
async def test_pipeline_resume_with_pending_operation_polls_until_done(monkeypatch):
    job = await _interrupted_job(monkeypatch)
    polls = {"n": 0}

    async def pending_then_done(job_dir, shot_id, attempt):
        while polls["n"] < 3:  # operation still running: keep polling, never resubmit
            polls["n"] += 1
            await asyncio.sleep(0)
        return await _write_clip(job_dir, shot_id, attempt)

    submits, resumes = _install_spies(monkeypatch, pending_then_done)
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert polls["n"] == 3 and resumes == [("shot_1_1", OP_NAME)]
    assert submits == []  # every other shot was already done; shot_1_1 came from the saved op


@pytest.mark.asyncio
async def test_pipeline_resume_with_expired_operation_generates_anew(monkeypatch):
    job = await _interrupted_job(monkeypatch)

    async def expired(job_dir, shot_id, attempt):
        return None

    submits, resumes = _install_spies(monkeypatch, expired)
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert resumes == [("shot_1_1", OP_NAME)] and submits == ["shot_1_1"]


@pytest.mark.asyncio
async def test_operation_id_persisted_to_disk_before_polling(monkeypatch):
    seen_on_disk: list = []
    orig_generate = MockVideoProvider.generate_clip

    async def check_after_submit(self, **kw):
        cb = kw["on_operation_started"]

        def wrapped(name):
            cb(name)
            data = json.loads((settings.DATA_DIR / "jobs" / "job_oppersist" / "job.json").read_text())
            shots = [s for sc in data["plan"]["scenes"] for s in sc["shots"]]
            seen_on_disk.append(next(s["veo_operation_name"] for s in shots if s["id"] == kw["shot_id"]))

        kw["on_operation_started"] = wrapped
        return await orig_generate(self, **kw)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", check_after_submit)
    job = Job(id="job_oppersist", brief="A 30s ad for Lumina smart glass.", mode=VideoMode.ad)
    await run_pipeline(job)
    assert seen_on_disk and all(n and n.startswith("mock-op-") for n in seen_on_disk)

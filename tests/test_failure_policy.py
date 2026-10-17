"""Fix #6: per-shot best-so-far fallback, critic failure tolerance, sibling cancellation on fatal errors."""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.models import Job, JobStatus, ShotStatus, VideoMode
from app.pipeline.orchestrator import run_pipeline
from app.providers.mock import MockCriticProvider, MockVideoProvider


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)


def _job(n: str) -> Job:
    return Job(id=f"job_fail{n}", brief="A 30s ad for Lumina smart glass in a modern office.", mode=VideoMode.ad)


@pytest.mark.asyncio
async def test_transient_attempt_failure_is_retried_not_fatal(monkeypatch):
    orig = MockVideoProvider.generate_clip
    failed_once = set()

    async def flaky(self, **kw):
        if kw["shot_id"] not in failed_once:
            failed_once.add(kw["shot_id"])
            raise RuntimeError("Veo hiccup")
        return await orig(self, **kw)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", flaky)
    job = _job("1")
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert all(s.status == ShotStatus.passed for sc in job.plan.scenes for s in sc.shots)


@pytest.mark.asyncio
async def test_critic_error_accepts_clip_without_rerender(monkeypatch):
    calls = []
    orig = MockVideoProvider.generate_clip

    async def rec(self, **kw):
        calls.append(kw["shot_id"])
        return await orig(self, **kw)

    async def broken_critic(self, **kw):
        raise RuntimeError("critic down")

    monkeypatch.setattr(MockVideoProvider, "generate_clip", rec)
    monkeypatch.setattr(MockCriticProvider, "critique_clip", broken_critic)
    job = _job("2")
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    shots = [s for sc in job.plan.scenes for s in sc.shots]
    assert len(calls) == len(shots)  # exactly one render per shot
    assert all(s.is_flagged_fallback for s in shots)


@pytest.mark.asyncio
async def test_fatal_shot_failure_cancels_sibling_scenes(monkeypatch):
    monkeypatch.setattr(settings, "MAX_PARALLEL_SCENES", 4)
    started = []
    orig = MockVideoProvider.generate_clip

    async def doomed(self, **kw):
        started.append(kw["shot_id"])
        if kw["shot_id"].startswith("shot_1_"):
            raise RuntimeError("permanent 400")
        await asyncio.sleep(0.5)  # other scenes are slow
        return await orig(self, **kw)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", doomed)
    job = _job("3")
    with pytest.raises(Exception):
        await run_pipeline(job)
    assert job.status == JobStatus.failed
    n_before = len(started)
    await asyncio.sleep(1.0)
    assert len(started) == n_before, "sibling scenes kept generating after the job failed"
    other_shots = [s for sc in job.plan.scenes[1:] for s in sc.shots]
    # No sibling scene shot completed after cancellation
    assert not any(s.status == ShotStatus.passed for s in other_shots)

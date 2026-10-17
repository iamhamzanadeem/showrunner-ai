"""Fix #7/#8/#9: restart recovery, resume, Veo op persistence, atomic writes, task references."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.events import EventBus
from app.main import _background_tasks, app, jobs_db, recover_interrupted_jobs, spawn_job_task
from app.models import Job, JobStatus, JobVersion, ShotStatus, VideoMode
from app.pipeline import planner as planner_mod
from app.pipeline.orchestrator import run_pipeline
from app.pipeline.planner import save_job_state
from app.providers.mock import MockVideoProvider


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)
    jobs_db.clear()


def _read(job_id: str) -> dict:
    return json.loads((settings.DATA_DIR / "jobs" / job_id / "job.json").read_text())


def test_atomic_save_survives_crash_mid_write(monkeypatch):
    job = Job(id="job_atomic01", brief="b", mode=VideoMode.ad, status=JobStatus.planned)
    save_job_state(job)
    job.brief = "changed"

    def boom(*a, **k):
        raise OSError("disk died mid-write")

    monkeypatch.setattr(planner_mod.os, "replace", boom)
    with pytest.raises(OSError):
        save_job_state(job)
    data = _read("job_atomic01")  # still valid JSON, previous content
    assert data["brief"] == "b"
    assert not list((settings.DATA_DIR / "jobs" / "job_atomic01").glob("*.tmp"))


@pytest.mark.asyncio
async def test_full_pipeline_then_restart_recovery_and_resume(monkeypatch):
    # 1. Run a pipeline where scene 2 fails fatally -> job failed, scene 1 shots passed
    orig = MockVideoProvider.generate_clip

    async def fail_scene2(self, **kw):
        if kw["shot_id"].startswith("shot_2_"):
            raise RuntimeError("Veo outage")
        return await orig(self, **kw)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", fail_scene2)
    monkeypatch.setattr(settings, "MAX_PARALLEL_SCENES", 1)  # scene 1 completes before scene 2 fails
    job = Job(id="job_resume01", brief="A 30s ad for Lumina smart glass.", mode=VideoMode.ad)
    with pytest.raises(Exception):
        await run_pipeline(job)
    assert job.status == JobStatus.failed
    passed_before = {s.id: s.video_url for sc in job.plan.scenes for s in sc.shots if s.status == ShotStatus.passed}
    assert passed_before
    # Veo op name persisted on shots that rendered
    assert all(
        s.veo_operation_name for sc in job.plan.scenes for s in sc.shots if s.status == ShotStatus.passed
    )

    # 2. Resume: only previously failed shots are rendered again
    rendered = []

    async def rec(self, **kw):
        rendered.append(kw["shot_id"])
        return await orig(self, **kw)

    monkeypatch.setattr(MockVideoProvider, "generate_clip", rec)
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert not set(rendered) & set(passed_before)
    all_ids = {s.id for sc in job.plan.scenes for s in sc.shots}
    assert set(rendered) == all_ids - set(passed_before)


def test_startup_marks_inflight_jobs(monkeypatch):
    j1 = Job(id="job_inflight1", brief="b", mode=VideoMode.ad, status=JobStatus.generating)
    j2 = Job(
        id="job_inflight2", brief="b", mode=VideoMode.ad, status=JobStatus.generating,
        final_video_url="/media/job_inflight2/final_v1.mp4",
        versions=[JobVersion(version=1, url="/media/job_inflight2/final_v1.mp4", message="m", created_at="t")],
    )
    j3 = Job(id="job_done00001", brief="b", mode=VideoMode.ad, status=JobStatus.completed)
    for j in (j1, j2, j3):
        save_job_state(j)
    assert recover_interrupted_jobs() == 2
    assert _read("job_inflight1")["status"] == "failed"
    assert "Interrupted" in _read("job_inflight1")["error"]
    assert _read("job_inflight2")["status"] == "completed"  # previous cut stays usable
    assert _read("job_done00001")["status"] == "completed"


def test_lifespan_recovery_unblocks_chat_and_resume_endpoint():
    j = Job(id="job_stuck0001", brief="b", mode=VideoMode.ad, status=JobStatus.generating)
    save_job_state(j)
    with TestClient(app) as client:  # lifespan runs recovery
        data = client.get("/api/jobs/job_stuck0001").json()
        assert data["status"] == "failed"
        # chat on a never-finished job points at resume instead of "busy"
        r = client.post("/api/jobs/job_stuck0001/chat", json={"message": "warmer"})
        assert r.status_code == 409 and "Resume" in r.json()["detail"]
        r = client.post("/api/jobs/job_stuck0001/resume")
        assert r.status_code == 200
        # resuming a non-failed job is refused
        r2 = client.post("/api/jobs/job_stuck0001/resume")
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_spawn_job_task_keeps_strong_reference():
    started = asyncio.Event()
    release = asyncio.Event()

    async def work():
        started.set()
        await release.wait()

    task = spawn_job_task("job_task00001", work())
    await started.wait()
    assert task in _background_tasks
    release.set()
    await task
    await asyncio.sleep(0)
    assert task not in _background_tasks


@pytest.mark.asyncio
async def test_sse_replay_continues_past_old_terminal_event():
    bus = EventBus()
    await bus.publish("job_x", "DONE", "v1 done")
    await bus.publish("job_x", "EDIT", "refining")
    seen = []

    async def consume():
        async for ev in bus.subscribe("job_x", timeout=2.0):
            seen.append(ev.step)

    t = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await bus.publish("job_x", "DONE", "v2 done")
    await t
    assert seen == ["DONE", "EDIT", "DONE"]

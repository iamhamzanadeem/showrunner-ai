"""Public demo abuse protection: per-IP + global daily caps, plan caps, read-only samples."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, jobs_db
from app.models import (
    Bible, BibleStyle, Job, JobStatus, Plan, Scene, Shot, VideoMode,
)
from app.pipeline.planner import enforce_plan_caps, plan_limits_note, save_job_state


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir(parents=True)
    monkeypatch.setattr(settings, "DATA_DIR", d)
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)
    jobs_db.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _post(client, ip):
    return client.post(
        "/api/jobs",
        json={"brief": "Capped", "mode": "ad"},
        headers={"X-Forwarded-For": f"6.6.6.6, {ip}"},  # last hop is the real client
    )


def test_per_ip_daily_cap_with_friendly_message(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JOBS_PER_IP_PER_DAY", 2)
    assert _post(client, "1.1.1.1").status_code == 201
    assert _post(client, "1.1.1.1").status_code == 201
    res = _post(client, "1.1.1.1")
    assert res.status_code == 429 and "come back tomorrow" in res.json()["detail"]
    assert _post(client, "2.2.2.2").status_code == 201  # other visitors unaffected
    raw = (settings.DATA_DIR / "usage.json").read_text()
    assert "1.1.1.1" not in raw  # IPs are stored hashed only


def test_global_daily_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JOBS_PER_DAY", 2)
    monkeypatch.setattr(settings, "MAX_JOBS_PER_IP_PER_DAY", 5)
    assert _post(client, "1.1.1.1").status_code == 201
    assert _post(client, "2.2.2.2").status_code == 201
    res = _post(client, "3.3.3.3")
    assert res.status_code == 429 and "today's limit" in res.json()["detail"]


def test_legacy_usage_file_format_is_migrated(client, monkeypatch):
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (settings.DATA_DIR / "usage.json").write_text(json.dumps({today: 1}))
    monkeypatch.setattr(settings, "MAX_JOBS_PER_DAY", 2)
    assert _post(client, "1.1.1.1").status_code == 201
    assert _post(client, "1.1.1.1").status_code == 429


def test_health_exposes_limits_and_samples(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SHOTS_PER_JOB", 4)
    monkeypatch.setattr(settings, "SAMPLE_JOB_IDS", "job_s1, job_s2")
    data = client.get("/api/health").json()
    assert data["limits"]["max_shots_per_job"] == 4
    assert data["sample_job_ids"] == ["job_s1", "job_s2"]


def test_samples_are_read_only_and_listed_first(client, monkeypatch):
    monkeypatch.setattr(settings, "SAMPLE_JOB_IDS", "job_sample1")
    old = Job(id="job_sample1", brief="sample", mode=VideoMode.ad, status=JobStatus.completed,
              created_at="2020-01-01T00:00:00+00:00", final_video_url="/media/job_sample1/final.mp4")
    new = Job(id="job_newer01", brief="mine", mode=VideoMode.ad, status=JobStatus.completed)
    save_job_state(old)
    save_job_state(new)
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["id"] == "job_sample1" and jobs[0]["is_sample"] is True
    res = client.post("/api/jobs/job_sample1/chat", json={"message": "make it warmer"})
    assert res.status_code == 403 and "read-only sample" in res.json()["detail"]
    assert client.post("/api/jobs/job_sample1/resume").status_code == 403


def _plan(durations):
    shots_by_scene = [durations[:3], durations[3:]]
    scenes = []
    for si, ds in enumerate(shots_by_scene, 1):
        scenes.append(Scene(id=f"scene_{si}", scene_number=si, title="t", summary="s", shots=[
            Shot(id=f"shot_{si}_{k}", scene_id=f"scene_{si}", duration_s=d, description="d",
                 camera="c", visual_prompt="v", chain_from_previous=k > 1)
            for k, d in enumerate(ds, 1)
        ]))
    return Plan(title="T", logline="L", mode=VideoMode.short_film, target_duration_s=sum(durations),
                bible=Bible(style=BibleStyle(palette=["#000"], lighting="l", lens="l", mood="m")),
                scenes=scenes)


def test_enforce_plan_caps_keeps_ending(monkeypatch):
    monkeypatch.setattr(settings, "MAX_SHOTS_PER_JOB", 4)
    monkeypatch.setattr(settings, "MAX_RUNTIME_S", 30)
    plan = _plan([8, 8, 8, 8, 8, 6])
    removed = enforce_plan_caps(plan)
    ids = [s.id for sc in plan.scenes for s in sc.shots]
    assert removed == 2 and ids[-1] == "shot_2_3" and len(ids) == 4
    assert plan.total_duration_s <= 30
    assert all(not sc.shots[0].chain_from_previous for sc in plan.scenes)
    assert "at most 4 shots" in plan_limits_note()


def test_enforce_plan_caps_noop_when_unlimited():
    plan = _plan([8, 8, 8, 8])
    assert enforce_plan_caps(plan) == 0 and plan.total_shots == 4
    assert plan_limits_note() == ""


def test_multi_instance_recovery_only_touches_stale_jobs(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.main import load_job, recover_interrupted_jobs

    monkeypatch.setattr(settings, "RECOVERY_STALE_S", 1200)
    fresh = Job(id="job_freshrun1", brief="b", mode=VideoMode.ad, status=JobStatus.generating)
    save_job_state(fresh)  # updated_at = now: maybe running on a sibling instance
    stale = Job(id="job_staleru1", brief="b", mode=VideoMode.ad, status=JobStatus.generating)
    save_job_state(stale)
    path = settings.DATA_DIR / "jobs" / "job_staleru1" / "job.json"
    data = json.loads(path.read_text())
    data["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(data))
    assert recover_interrupted_jobs() == 1
    assert load_job("job_freshrun1").status == JobStatus.generating
    assert load_job("job_staleru1").status == JobStatus.failed

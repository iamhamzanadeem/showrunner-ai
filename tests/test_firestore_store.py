"""Firestore job store, tested against an in-memory fake client (no network, no billing)."""
import pytest
from fastapi.testclient import TestClient

from app import job_store
from app.config import settings
from app.job_store import FileJobStore, FirestoreJobStore, set_store
from app.main import app, jobs_db, recover_interrupted_jobs
from app.models import CritiqueResult, Job, JobStatus, JobVersion, ShotAttempt, ShotStatus, VideoMode
from app.pipeline.planner import save_job_state
from tests.fake_firestore import FakeFirestore
from tests.test_public_demo import _plan


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    jobs_db.clear()
    db = FakeFirestore()
    set_store(FirestoreJobStore(client=db))
    yield db
    set_store(None)
    jobs_db.clear()


def _job(job_id="job_fs01", takes=2):
    plan = _plan([8, 6])
    shot = plan.scenes[0].shots[0]
    shot.status = ShotStatus.passed
    shot.attempts = [
        ShotAttempt(attempt_number=i + 1, prompt=f"p{i + 1}", clip_url=f"/media/{job_id}/shot_1_1_att{i + 1}.mp4",
                    critique=CritiqueResult(shot_id=shot.id, passed=i == takes - 1, score=0.5 + 0.1 * i,
                                            reasons=["r"], suggested_prompt_fix="fix"))
        for i in range(takes)
    ]
    shot.best_critique = shot.attempts[-1].critique
    return Job(id=job_id, brief="A tiny ad", mode=VideoMode.ad, status=JobStatus.completed, plan=plan,
               versions=[JobVersion(version=1, url=f"/media/{job_id}/final.mp4", changed_shots=["shot_1_1"])])


def test_round_trip_keeps_takes_in_subcollection(fake):
    job = _job()
    save_job_state(job)
    doc = fake.docs["jobs/job_fs01"]
    assert doc["status"] == "completed" and doc["take_count"] == 2 and doc["reshoot_count"] == 1
    assert doc["title"] and doc["shot_count"] == 2 and doc["version_count"] == 1
    # The job document carries no takes; they live in jobs/{id}/takes/{shot_id}.
    for scene in doc["state"]["plan"]["scenes"]:
        assert all("attempts" not in shot for shot in scene["shots"])
    takes = fake.docs["jobs/job_fs01/takes/shot_1_1"]
    assert [a["prompt"] for a in takes["attempts"]] == ["p1", "p2"]

    loaded = job_store.get_store().load("job_fs01")
    assert loaded.model_dump(exclude={"updated_at"}) == job.model_dump(exclude={"updated_at"})


def test_unchanged_takes_are_not_rewritten_and_batch_is_atomic(fake):
    job = _job()
    save_job_state(job)
    writes = fake.writes
    job.status = JobStatus.assembling
    save_job_state(job)
    assert fake.writes == writes + 1  # only the job document changed
    fake.fail_next_commit = True
    job.plan.scenes[0].shots[0].attempts.append(ShotAttempt(attempt_number=3, prompt="p3"))
    job.status = JobStatus.completed
    with pytest.raises(RuntimeError):
        job_store.get_store().save(job)
    assert fake.docs["jobs/job_fs01"]["status"] == "assembling"  # nothing half-written
    assert len(fake.docs["jobs/job_fs01/takes/shot_1_1"]["attempts"]) == 2
    save_job_state(job)  # the retry still writes the takes (digest not cached on failure)
    assert len(fake.docs["jobs/job_fs01/takes/shot_1_1"]["attempts"]) == 3


def test_api_lists_and_serves_jobs_from_firestore(fake, monkeypatch):
    save_job_state(_job("job_fs02"))
    # Remove the mirror file: the API must answer from Firestore alone.
    (settings.DATA_DIR / "jobs" / "job_fs02" / "job.json").unlink()
    monkeypatch.setattr(settings, "SAMPLE_JOB_IDS", "job_fs02")
    with TestClient(app) as client:
        listing = client.get("/api/jobs").json()
        row = next(j for j in listing if j["id"] == "job_fs02")
        assert row["is_sample"] and row["take_count"] == 2 and row["duration_s"] == 14
        assert client.get("/api/jobs/job_fs02").json()["status"] == "completed"
        takes = client.get("/api/jobs/job_fs02/takes").json()
        assert len(takes["shots"][0]["takes"]) == 2
        assert client.post("/api/jobs/job_fs02/chat", json={"message": "x"}).status_code == 403


def test_falls_back_to_job_file_when_not_in_firestore(fake):
    FileJobStore().save(_job("job_legacy1"))
    assert job_store.get_store().load("job_legacy1").id == "job_legacy1"
    assert job_store.get_store().load("job_nothere") is None


def test_veo_operation_survives_round_trip_for_resume(fake):
    job = _job("job_fs03")
    job.status = JobStatus.generating
    shot = job.plan.scenes[0].shots[1]
    shot.veo_operation_name = "projects/p/locations/l/operations/abc"
    shot.veo_operation_attempt = 1
    save_job_state(job)
    loaded = job_store.get_store().load("job_fs03")
    s = loaded.plan.scenes[0].shots[1] if len(loaded.plan.scenes[0].shots) > 1 else loaded.plan.scenes[1].shots[0]
    assert s.veo_operation_name == "projects/p/locations/l/operations/abc" and s.veo_operation_attempt == 1


def test_startup_recovery_reads_firestore(fake, monkeypatch):
    job = _job("job_fs04")
    job.status = JobStatus.generating
    save_job_state(job)
    (settings.DATA_DIR / "jobs" / "job_fs04" / "job.json").unlink()
    monkeypatch.setattr(settings, "RECOVERY_STALE_S", 0)
    assert recover_interrupted_jobs() == 1
    assert fake.docs["jobs/job_fs04"]["status"] != "generating"


def test_usage_counters_use_firestore_transaction(fake, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JOBS_PER_IP_PER_DAY", 2)
    with TestClient(app) as client:
        post = lambda ip: client.post("/api/jobs", json={"brief": "A tiny ad brief here", "mode": "ad"},
                                      headers={"x-forwarded-for": ip})
        assert post("1.1.1.1").status_code == 201
        assert post("1.1.1.1").status_code == 201
        assert post("1.1.1.1").status_code == 429
        assert post("2.2.2.2").status_code == 201
    usage = [v for k, v in fake.docs.items() if k.startswith("usage/")]
    assert len(usage) == 1 and usage[0]["total"] == 3
    assert sorted(usage[0]["ips"].values()) == [1, 2]
    assert "1.1.1.1" not in str(usage[0])  # hashed only

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.events import event_bus
from app.main import app, jobs_db
from app.models import Job, JobStatus, VideoMode, ChatMessage
from app.pipeline.orchestrator import run_pipeline, run_refinement
from app.providers.mock import MockPlannerProvider


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Ensures each test runs with an isolated temporary DATA_DIR."""
    temp_dir = tmp_path / "data"
    temp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", temp_dir)
    monkeypatch.setattr(settings, "MOCK_DELAY_S", 0.0)
    jobs_db.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_mock_planner_edit_plan():
    """Validates MockPlannerProvider.edit_plan rule-based parsing and targeted updates."""
    planner = MockPlannerProvider()
    plan = await planner.generate_plan(
        brief="A 30s ad for Kinetic Zero energy drink with rooftop parkour runner in neon city.",
        mode=VideoMode.ad,
    )

    # 1. Test targeted scene refinement with tone shift
    edit = await planner.edit_plan(plan, "Make scene 2 warmer and more vibrant")
    assert "scene_2" in edit.target_scene_ids
    assert len(edit.target_shot_ids) >= 1
    assert any("shot_2_" in sid for sid in edit.target_shot_ids)
    assert edit.style_changes is not None
    assert any(c.upper() in ["#260D04", "#D84315", "#FF8F00", "#FFF3E0"] for c in edit.style_changes.palette)

    for updated in edit.updated_shots:
        assert "warm" in updated.visual_prompt.lower()

    # 2. Test dialogue rewrite instruction
    edit_dialogue = await planner.edit_plan(
        plan,
        'Make scene 1 cooler and change the line to "Unleash infinite momentum"',
    )
    assert "scene_1" in edit_dialogue.target_scene_ids
    assert any(
        updated.vo_line == "Unleash infinite momentum"
        for updated in edit_dialogue.updated_shots
    )


@pytest.mark.asyncio
async def test_end_to_end_refinement_pipeline(tmp_path):
    """
    Validates end-to-end refinement:
    1. Runs initial ad job to completion (v1).
    2. Runs refinement for 'Make scene 2 warmer'.
    3. Asserts only scene 2 shots were regenerated (new attempts created),
       other scenes' shots were untouched.
    4. Asserts final_v1.mp4 and final_v2.mp4 exist, versions length is 2,
       and EDIT event was emitted.
    """
    job_id = "test_refinement_job"
    job = Job(
        id=job_id,
        brief="A 30s commercial for Lumina smart sunglasses with neon beach aesthetics.",
        mode=VideoMode.ad,
    )
    jobs_db[job_id] = job

    # Step 1: Run initial pipeline
    await run_pipeline(job)
    assert job.status == JobStatus.completed
    assert len(job.versions) == 1
    assert job.versions[0].version == 1

    job_dir = settings.get_job_dir(job.id)
    final_v1 = job_dir / "final_v1.mp4"
    assert final_v1.exists()
    assert (job_dir / "final.mp4").exists()

    # Record initial attempt counts and video paths per shot
    scene_1 = job.plan.scenes[0]
    scene_2 = job.plan.scenes[1]

    scene_1_initial_attempts = {s.id: len(s.attempts) for s in scene_1.shots}
    scene_1_initial_videos = {s.id: s.video_url for s in scene_1.shots}
    scene_2_initial_attempts = {s.id: len(s.attempts) for s in scene_2.shots}

    # Step 2: Run refinement for scene 2
    chat_prompt = "Please make scene 2 warmer and more dramatic"
    job.messages.append(ChatMessage(role="user", text=chat_prompt))
    await run_refinement(job, chat_prompt)

    # Step 3: Assertions on versioning and output files
    assert job.status == JobStatus.completed
    assert len(job.versions) == 2
    assert job.versions[0].version == 1
    assert job.versions[1].version == 2
    assert job.versions[1].message == chat_prompt

    final_v2 = job_dir / "final_v2.mp4"
    assert final_v2.exists()
    assert final_v1.exists()  # v1 is strictly preserved

    # Step 4: Assert ONLY scene 2 shots were reshot
    for shot in scene_1.shots:
        assert len(shot.attempts) == scene_1_initial_attempts[shot.id], (
            f"Scene 1 shot {shot.id} should NOT have been reshot"
        )
        assert shot.video_url == scene_1_initial_videos[shot.id]

    for shot in scene_2.shots:
        assert len(shot.attempts) > scene_2_initial_attempts[shot.id], (
            f"Scene 2 shot {shot.id} SHOULD have been reshot"
        )
        assert "warm" in shot.visual_prompt.lower()

    # Step 5: Check chat history and event bus
    assert any(m.role == "user" and m.text == chat_prompt for m in job.messages)
    assert any(m.role == "agent" for m in job.messages)

    events = await event_bus.get_history(job.id)
    edit_events = [e for e in events if e.step == "EDIT"]
    assert len(edit_events) >= 1
    assert "target_scene_ids" in edit_events[0].data


def test_api_chat_and_recent_jobs(client):
    """
    Tests:
    - GET /api/jobs lists productions.
    - POST /api/jobs/{id}/chat rejects nonexistent job with 404.
    - POST /api/jobs/{id}/chat rejects busy job with 409.
    - POST /api/jobs/{id}/chat starts refinement on completed job.
    """
    # 1. Nonexistent job
    res_404 = client.post("/api/jobs/job_ghost/chat", json={"message": "Make it brighter"})
    assert res_404.status_code == 404

    # 2. Busy job -> 409 Conflict
    now = datetime.now(timezone.utc).isoformat()
    busy_job = Job(
        id="job_busy_test",
        brief="Testing busy guard",
        mode=VideoMode.ad,
        status=JobStatus.generating,
        created_at=now,
        updated_at=now,
    )
    jobs_db[busy_job.id] = busy_job

    res_409 = client.post(f"/api/jobs/{busy_job.id}/chat", json={"message": "Make it cooler"})
    assert res_409.status_code == 409
    assert "busy" in res_409.json()["detail"].lower()

    # 3. Completed job -> 200 OK starting refinement
    done_job = Job(
        id="job_done_test",
        brief="Testing chat initiation",
        mode=VideoMode.ad,
        status=JobStatus.completed,
        created_at=now,
        updated_at=now,
    )
    jobs_db[done_job.id] = done_job

    res_200 = client.post(f"/api/jobs/{done_job.id}/chat", json={"message": "Make scene 1 warmer"})
    assert res_200.status_code == 200
    data = res_200.json()
    assert data["status"] == "refining"
    assert data["job_id"] == done_job.id
    assert data["target_version"] == 1

    # 4. List recent jobs
    list_res = client.get("/api/jobs")
    assert list_res.status_code == 200
    jobs_list = list_res.json()
    assert len(jobs_list) >= 2
    job_ids = [j["id"] for j in jobs_list]
    assert "job_busy_test" in job_ids
    assert "job_done_test" in job_ids

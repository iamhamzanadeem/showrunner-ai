import asyncio
import io
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.events import event_bus
from app.main import app, jobs_db


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Ensures each test runs with an isolated temporary DATA_DIR."""
    temp_dir = tmp_path / "data"
    temp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DATA_DIR", temp_dir)
    jobs_db.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["active_provider"] in ["mock", "google", "hybrid"]
    assert "version" in data
    assert "storage_dir" in data
    assert "providers" in data
    assert "planner" in data["providers"]
    assert "video" in data["providers"]
    assert "models" in data
    assert data["models"]["video"] == settings.SR_MODEL_VIDEO


def test_index_frontend(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "SHOWRUNNER" in response.text


def test_create_job_json_and_fetch(client):
    # 1. Create job via JSON
    payload = {
        "brief": "A sleek 30s commercial for Lumina Smart Glass in modern offices.",
        "mode": "ad",
    }
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 201
    job_info = response.json()
    job_id = job_info["id"]
    assert job_id.startswith("job_")
    assert job_info["status"] in ["queued", "planning", "planned"]
    assert job_info["mode"] == "ad"

    # 2. Fetch the job details
    get_res = client.get(f"/api/jobs/{job_id}")
    assert get_res.status_code == 200
    job_data = get_res.json()
    assert job_data["id"] == job_id
    assert job_data["brief"] == payload["brief"]


def test_create_job_multipart_with_ref_image(client):
    file_content = b"fake-image-bytes"
    files = [
        ("ref_images", ("logo.png", io.BytesIO(file_content), "image/png"))
    ]
    data = {
        "brief": "Sci-fi short film with alien artifact discovery.",
        "mode": "short_film",
    }
    response = client.post("/api/jobs", data=data, files=files)
    assert response.status_code == 201
    job_info = response.json()
    assert job_info["mode"] == "short_film"
    assert len(job_info["reference_images"]) == 1
    assert "logo.png" in job_info["reference_images"][0]


def test_create_job_missing_brief(client):
    response = client.post("/api/jobs", json={"brief": "  ", "mode": "ad"})
    assert response.status_code == 422


def test_get_nonexistent_job(client):
    response = client.get("/api/jobs/job_doesnotexist")
    assert response.status_code == 404


def test_job_events_sse(client):
    # 1. Create a job
    res = client.post("/api/jobs", json={"brief": "Test SSE event stream flow", "mode": "ad"})
    assert res.status_code == 201
    job_id = res.json()["id"]

    # 2. Call SSE endpoint; since generator terminates on completion, TestClient consumes it cleanly
    response = client.get(f"/api/jobs/{job_id}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert ": connected" in response.text


@pytest.mark.asyncio
async def test_event_bus_direct():
    job_id = "job_test_bus"
    await event_bus.publish(job_id=job_id, step="PLAN", message="Initializing test")
    history = await event_bus.get_history(job_id)
    assert len(history) >= 1
    assert history[0].step == "PLAN"
    assert "Initializing test" in history[0].message

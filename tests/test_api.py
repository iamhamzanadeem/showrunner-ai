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


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_create_job_multipart_with_ref_image(client):
    file_content = PNG_BYTES
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
    ref_url = job_info["reference_images"][0]
    # Server-generated UUID filename, client name discarded
    assert "logo" not in ref_url
    assert ref_url.endswith(".png")
    stored = settings.DATA_DIR / "jobs" / job_info["id"] / "ref_images" / ref_url.rsplit("/", 1)[-1]
    assert stored.read_bytes() == file_content


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


# --- Security: uploads, auth, quota, media path ----------------------------------------


def test_upload_path_traversal_filename_is_neutralised(client):
    files = [("ref_images", ("../../../../evil.png", io.BytesIO(PNG_BYTES), "image/png"))]
    res = client.post("/api/jobs", data={"brief": "Traversal attempt", "mode": "ad"}, files=files)
    assert res.status_code == 201
    ref_url = res.json()["reference_images"][0]
    assert ".." not in ref_url and "evil" not in ref_url
    assert not (settings.DATA_DIR.parent / "evil.png").exists()
    assert not list(settings.DATA_DIR.parent.rglob("evil.png"))


def test_upload_absolute_filename_is_neutralised(client, tmp_path):
    target = tmp_path / "abs_evil.png"
    files = [("ref_images", (str(target), io.BytesIO(PNG_BYTES), "image/png"))]
    res = client.post("/api/jobs", data={"brief": "Absolute path attempt", "mode": "ad"}, files=files)
    assert res.status_code == 201
    assert not target.exists()


def test_upload_rejects_disallowed_extension(client):
    files = [("ref_images", ("shell.php", io.BytesIO(b"<?php ?>"), "application/x-php"))]
    res = client.post("/api/jobs", data={"brief": "Bad upload", "mode": "ad"}, files=files)
    assert res.status_code == 415


def test_upload_rejects_fake_image_content(client):
    files = [("ref_images", ("fake.png", io.BytesIO(b"not really a png"), "image/png"))]
    res = client.post("/api/jobs", data={"brief": "Bad upload", "mode": "ad"}, files=files)
    assert res.status_code == 415


def test_demo_token_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_TOKEN", "s3cret")
    health = client.get("/api/health").json()
    assert health["auth_required"] is True

    payload = {"brief": "Token protected job", "mode": "ad"}
    assert client.post("/api/jobs", json=payload).status_code == 401
    assert client.post("/api/jobs", json=payload, headers={"X-Demo-Token": "wrong"}).status_code == 401
    ok = client.post("/api/jobs", json=payload, headers={"X-Demo-Token": "s3cret"})
    assert ok.status_code == 201
    ok_q = client.post("/api/jobs?token=s3cret", json=payload)
    assert ok_q.status_code == 201

    job_id = ok.json()["id"]
    assert client.post(f"/api/jobs/{job_id}/chat", json={"message": "brighter"}).status_code == 401
    # Read endpoints stay open
    assert client.get(f"/api/jobs/{job_id}").status_code == 200


def test_no_auth_when_token_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_TOKEN", None)
    assert client.get("/api/health").json()["auth_required"] is False
    assert client.post("/api/jobs", json={"brief": "Open local dev", "mode": "ad"}).status_code == 201


def test_daily_job_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JOBS_PER_DAY", 2)
    for _ in range(2):
        assert client.post("/api/jobs", json={"brief": "Capped", "mode": "ad"}).status_code == 201
    res = client.post("/api/jobs", json={"brief": "Capped", "mode": "ad"})
    assert res.status_code == 429


def test_cors_does_not_allow_credentials(client):
    res = client.options(
        "/api/jobs",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert res.headers.get("access-control-allow-credentials") != "true"


def test_media_path_confined_to_jobs_dir(client):
    secret = settings.DATA_DIR / "usage.json"
    secret.write_text("{}")
    assert client.get("/media/../usage.json").status_code == 404
    assert client.get("/media/%2e%2e/usage.json").status_code == 404
    job_dir = settings.DATA_DIR / "jobs" / "job_0123456789"
    job_dir.mkdir(parents=True)
    (job_dir / "clip.txt").write_text("ok")
    assert client.get("/media/job_0123456789/clip.txt").status_code == 200


def test_invalid_job_id_rejected(client):
    assert client.get("/api/jobs/..").status_code in (404, 405)
    assert client.get("/api/jobs/not_a_job").status_code == 404
    assert not (settings.DATA_DIR / "jobs" / "not_a_job").exists()

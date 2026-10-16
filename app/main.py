from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
import uuid

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import settings
from app.events import event_bus, sse_event_stream
from app.models import (
    ChatMessage,
    ChatRequest,
    HealthResponse,
    Job,
    JobStatus,
    JobSummaryResponse,
    VideoMode,
)
from app.pipeline.orchestrator import run_pipeline, run_refinement
from app.pipeline.planner import save_job_state

# In-memory fast cache of active jobs
jobs_db: Dict[str, Job] = {}


def load_job(job_id: str) -> Optional[Job]:
    """Loads a job from memory cache or disk."""
    if job_id in jobs_db:
        return jobs_db[job_id]

    job_file = settings.get_job_dir(job_id) / "job.json"
    if job_file.exists():
        try:
            with open(job_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                job = Job.model_validate(data)
                jobs_db[job_id] = job
                return job
        except Exception:
            return None
    return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Showrunner AI",
    description="Agentic AI Video Production Platform",
    version=__version__,
    lifespan=lifespan,
)

# CORS middleware for open development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Returns system status, active provider, providers mapping, models, and version."""
    return HealthResponse(
        status="ok",
        active_provider=settings.resolved_provider,
        version=__version__,
        storage_dir=str(settings.DATA_DIR.resolve()),
        providers=settings.get_all_modality_providers(),
        models=settings.get_configured_models(),
    )


@app.post("/api/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request) -> Dict[str, Any]:
    """
    Creates a new video production job and starts the planning agent loop.
    Supports both JSON and Multipart Form (with optional reference images).
    """
    content_type = request.headers.get("content-type", "")

    brief: str = ""
    mode_str: str = "ad"
    saved_ref_images: List[str] = []

    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job_dir = settings.get_job_dir(job_id)

    if "application/json" in content_type:
        payload = await request.json()
        brief = payload.get("brief", "").strip()
        mode_str = payload.get("mode", "ad")
    else:
        form = await request.form()
        brief = str(form.get("brief", "")).strip()
        mode_str = str(form.get("mode", "ad"))

        # Process uploaded files if any
        ref_files = form.getlist("ref_images")
        if ref_files:
            ref_dir = job_dir / "ref_images"
            ref_dir.mkdir(parents=True, exist_ok=True)
            for f in ref_files:
                filename = getattr(f, "filename", None)
                if filename:
                    target_path = ref_dir / filename
                    with open(target_path, "wb") as buffer:
                        if hasattr(f, "read"):
                            content = await f.read()
                        elif hasattr(f, "file"):
                            content = f.file.read()
                        else:
                            content = b""
                        buffer.write(content)
                    saved_ref_images.append(f"/media/{job_id}/ref_images/{filename}")

    if not brief:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Creative brief is required.",
        )

    try:
        mode = VideoMode(mode_str)
    except ValueError:
        mode = VideoMode.ad

    now = datetime.now(timezone.utc).isoformat()
    job = Job(
        id=job_id,
        brief=brief,
        mode=mode,
        status=JobStatus.queued,
        created_at=now,
        updated_at=now,
        reference_images=saved_ref_images,
    )

    jobs_db[job_id] = job
    save_job_state(job)

    # Publish initial queued event
    await event_bus.publish(
        job_id=job.id,
        step="INFO",
        message=f"Job {job.id} registered. Mode: {job.mode.value}. Pacing planner queued.",
    )

    # Launch full agent production pipeline background task
    asyncio.create_task(run_pipeline(job))

    return {
        "id": job.id,
        "job_id": job.id,
        "status": job.status.value,
        "mode": job.mode.value,
        "brief": job.brief,
        "reference_images": job.reference_images,
        "created_at": job.created_at,
    }


@app.get("/api/jobs", response_model=List[JobSummaryResponse])
async def list_jobs() -> List[JobSummaryResponse]:
    """Lists all recent video production jobs sorted by creation time descending."""
    results: Dict[str, JobSummaryResponse] = {}

    # Scan persisted jobs on disk
    jobs_dir = settings.DATA_DIR / "jobs"
    if jobs_dir.exists():
        for job_path in jobs_dir.iterdir():
            if job_path.is_dir():
                job_json = job_path / "job.json"
                if job_json.exists():
                    try:
                        with open(job_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            j = Job.model_validate(data)
                            title = j.plan.title if j.plan else None
                            results[j.id] = JobSummaryResponse(
                                id=j.id,
                                title=title,
                                brief=j.brief,
                                mode=j.mode,
                                status=j.status,
                                created_at=j.created_at,
                                poster_url=j.poster_url,
                                final_video_url=j.final_video_url,
                            )
                    except Exception:
                        pass

    # Merge in-memory cache
    for j_id, j in jobs_db.items():
        title = j.plan.title if j.plan else None
        results[j_id] = JobSummaryResponse(
            id=j.id,
            title=title,
            brief=j.brief,
            mode=j.mode,
            status=j.status,
            created_at=j.created_at,
            poster_url=j.poster_url,
            final_video_url=j.final_video_url,
        )

    summaries = list(results.values())
    summaries.sort(key=lambda s: s.created_at, reverse=True)
    return summaries


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Job:
    """Retrieves full state, metadata, scenes, and shots for a given job."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return job


@app.post("/api/jobs/{job_id}/chat")
async def chat_job_refinement(job_id: str, payload: ChatRequest) -> Dict[str, Any]:
    """
    Submits a director chat message to refine a completed job.
    Returns 409 Conflict if the job is currently processing another stage.
    """
    job = load_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    message = payload.message.strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Chat message cannot be empty.",
        )

    # Busy state check: only completed or failed jobs can be refined
    busy_statuses = [
        JobStatus.planning,
        JobStatus.generating,
        JobStatus.critiquing,
        JobStatus.assembling,
    ]
    if job.status in busy_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_id}' is currently busy in status '{job.status.value}'. Please wait for completion.",
        )

    # Append user message to chat history
    user_msg = ChatMessage(role="user", text=message)
    job.messages.append(user_msg)
    job.status = JobStatus.generating
    save_job_state(job)

    # Launch refinement in background
    asyncio.create_task(run_refinement(job, message))

    next_ver = len(job.versions) + 1
    return {
        "status": "refining",
        "job_id": job.id,
        "message": message,
        "target_version": next_ver,
    }


@app.get("/api/jobs/{job_id}/events")
async def get_job_events(job_id: str) -> StreamingResponse:
    """Streams live Server-Sent Events (SSE) for agent thinking and progress."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    return StreamingResponse(
        sse_event_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/media/{file_path:path}")
async def get_media_file(file_path: str) -> FileResponse:
    """Serves generated job media assets from active DATA_DIR."""
    target_path = (settings.DATA_DIR / "jobs" / file_path).resolve()
    data_dir_resolved = settings.DATA_DIR.resolve()
    if not str(target_path).startswith(str(data_dir_resolved)) or not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media asset not found.",
        )
    return FileResponse(target_path)

# Serve static frontend assets
static_dir = Path("./static")
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.api_route("/", methods=["GET", "HEAD"])
async def get_index() -> FileResponse:
    """Serves the main single page web application shell."""
    index_path = static_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frontend index.html not found.",
        )
    return FileResponse(index_path, media_type="text/html")

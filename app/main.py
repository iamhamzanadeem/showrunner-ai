from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
import uuid

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
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
    JobTakesResponse,
    ShotTakes,
    TakeCritique,
    TakeInfo,
    VideoMode,
)
from app.pipeline.orchestrator import recover_interrupted_job, run_pipeline, run_refinement
from app.pipeline.planner import save_job_state
from app.security import (
    auth_required,
    consume_daily_quota,
    ensure_not_sample,
    sample_job_ids,
    is_valid_job_id,
    require_demo_token,
    safe_upload_name,
    sniff_image_ok,
)

# In-memory fast cache of active jobs
jobs_db: Dict[str, Job] = {}

# Strong references to running background tasks so they are never garbage-collected mid-run (Fix #8)
_background_tasks: set[asyncio.Task] = set()
# Job ids whose pipeline/refinement task is currently running in this process
_running_jobs: set[str] = set()

logger = logging.getLogger("showrunner.main")


def spawn_job_task(job_id: str, coro) -> asyncio.Task:
    """Starts a background pipeline task, keeping a strong reference until it finishes."""
    task = asyncio.create_task(coro, name=f"job:{job_id}")
    _background_tasks.add(task)
    _running_jobs.add(job_id)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        _running_jobs.discard(job_id)
        if not t.cancelled() and t.exception() is not None:
            logger.info("Background task for %s ended with error: %s", job_id, t.exception())

    task.add_done_callback(_done)
    return task


def _is_stale(job: Job) -> bool:
    """True if the job has not been saved for RECOVERY_STALE_S seconds.

    With several Cloud Run instances sharing the GCS-mounted DATA_DIR, an in-flight job may
    belong to a live sibling instance; it is only treated as interrupted once its state has
    gone quiet for longer than any single step (Veo timeout) could take.
    """
    threshold = settings.RECOVERY_STALE_S
    if threshold <= 0:
        return True
    try:
        updated = datetime.fromisoformat(job.updated_at or job.created_at)
    except Exception:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds() > threshold


def recover_interrupted_jobs() -> int:
    """On startup, marks jobs left in-flight by a previous process as interrupted (Fix #7)."""
    jobs_dir = settings.DATA_DIR / "jobs"
    if not jobs_dir.exists():
        return 0
    recovered = 0
    for job_json in jobs_dir.glob("*/job.json"):
        try:
            job = Job.model_validate(json.loads(job_json.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Skipping unreadable job file %s: %s", job_json, exc)
            continue
        if job.id in _running_jobs or not _is_stale(job):
            continue  # possibly still running on another Cloud Run instance
        if recover_interrupted_job(job):
            save_job_state(job)
            jobs_db.pop(job.id, None)
            recovered += 1
    if recovered:
        logger.warning("Recovered %d interrupted job(s) after restart.", recovered)
    return recovered


def load_job(job_id: str) -> Optional[Job]:
    """Loads a job from memory cache or disk."""
    if not is_valid_job_id(job_id):
        return None
    # Jobs running in this process: the in-memory object is the source of truth.
    if job_id in jobs_db and job_id in _running_jobs:
        return jobs_db[job_id]

    # Otherwise re-read disk: another instance may be producing (or have finished) it.
    job_file = settings.DATA_DIR / "jobs" / job_id / "job.json"
    if job_file.exists():
        try:
            with open(job_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                job = Job.model_validate(data)
        except Exception:
            return jobs_db.get(job_id)
        # Lazy recovery of a job whose owner instance died (see _is_stale).
        if settings.RECOVERY_STALE_S > 0 and _is_stale(job) and recover_interrupted_job(job):
            save_job_state(job)
        jobs_db[job_id] = job
        return job
    return jobs_db.get(job_id)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown hooks."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    recover_interrupted_jobs()
    yield


app = FastAPI(
    title="Showrunner AI",
    description="Agentic AI Video Production Platform",
    version=__version__,
    lifespan=lifespan,
)

# CORS: the bundled UI is same-origin; cross-origin callers get no credentialed access.
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Demo-Token"],
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
        auth_required=auth_required(),
        limits={
            "max_jobs_per_day": settings.MAX_JOBS_PER_DAY,
            "max_jobs_per_ip_per_day": settings.MAX_JOBS_PER_IP_PER_DAY,
            "max_shots_per_job": settings.MAX_SHOTS_PER_JOB,
            "max_runtime_s": settings.MAX_RUNTIME_S,
        },
        sample_job_ids=sorted(sample_job_ids()),
    )


@app.post("/api/jobs", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_demo_token)])
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
    pending_uploads: List[tuple[str, bytes]] = []

    if "application/json" in content_type:
        payload = await request.json()
        brief = str(payload.get("brief", "")).strip()
        mode_str = str(payload.get("mode", "ad"))
    else:
        form = await request.form()
        brief = str(form.get("brief", "")).strip()
        mode_str = str(form.get("mode", "ad"))

        # Validate uploaded reference images (never trust client filenames)
        ref_files = [f for f in form.getlist("ref_images") if getattr(f, "filename", None)]
        if len(ref_files) > settings.MAX_UPLOAD_FILES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"At most {settings.MAX_UPLOAD_FILES} reference images are allowed.",
            )
        for f in ref_files:
            safe_name = safe_upload_name(f.filename)
            content = await f.read()
            if len(content) > settings.MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Reference image exceeds {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                )
            if not sniff_image_ok(content, Path(safe_name).suffix):
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="Uploaded file content is not a valid PNG, JPG or WEBP image.",
                )
            pending_uploads.append((safe_name, content))

    if not brief:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Creative brief is required.",
        )
    if len(brief) > 4000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Creative brief is too long (max 4000 characters).",
        )

    consume_daily_quota(request)

    job_dir = settings.get_job_dir(job_id)
    if pending_uploads:
        ref_dir = job_dir / "ref_images"
        ref_dir.mkdir(parents=True, exist_ok=True)
        for safe_name, content in pending_uploads:
            (ref_dir / safe_name).write_bytes(content)
            saved_ref_images.append(f"/media/{job_id}/ref_images/{safe_name}")

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
    spawn_job_task(job.id, run_pipeline(job))

    return {
        "id": job.id,
        "job_id": job.id,
        "status": job.status.value,
        "mode": job.mode.value,
        "brief": job.brief,
        "reference_images": job.reference_images,
        "created_at": job.created_at,
    }


def _job_summary(j: Job) -> JobSummaryResponse:
    plan = j.plan
    shots = [shot for scene in plan.scenes for shot in scene.shots] if plan else []
    return JobSummaryResponse(
        id=j.id,
        title=plan.title if plan else None,
        brief=j.brief,
        mode=j.mode,
        status=j.status,
        created_at=j.created_at,
        poster_url=j.poster_url,
        final_video_url=j.final_video_url,
        logline=plan.logline if plan else None,
        duration_s=sum(shot.duration_s for shot in shots) if shots else None,
        scene_count=len(plan.scenes) if plan else 0,
        shot_count=len(shots),
        take_count=sum(len(shot.attempts) for shot in shots),
    )


def _media_url(job_id: str, value: Optional[str]) -> Optional[str]:
    """Normalizes stored asset references (``/media/...`` or ``data/jobs/<id>/x``) to media URLs."""
    if not value:
        return None
    if value.startswith("/media/") or value.startswith("http"):
        return value
    return f"/media/{job_id}/{Path(value).name}"


def _media_exists(job_id: str, url: Optional[str]) -> bool:
    if not url or not url.startswith(f"/media/{job_id}/"):
        return False
    rel = url[len("/media/"):].split("?", 1)[0]
    jobs_root = (settings.DATA_DIR / "jobs").resolve()
    target = (jobs_root / rel).resolve()
    return target.is_relative_to(jobs_root) and target.is_file()


def build_job_takes(job: Job) -> JobTakesResponse:
    """Per-shot take history (keyframe, clip, critic verdict) for the workspace UI."""
    import re

    shots_out: List[ShotTakes] = []
    if not job.plan:
        return JobTakesResponse(job_id=job.id)
    for scene in job.plan.scenes:
        for shot in scene.shots:
            video_url = _media_url(job.id, shot.video_url)
            accepted: Optional[int] = None
            if video_url:
                m = re.search(r"_att(\d+)\.mp4", video_url)
                if m:
                    accepted = int(m.group(1))
            if accepted is None and shot.status.value in ("passed", "ready") and shot.attempts:
                best = max(shot.attempts, key=lambda a: a.critique.score if a.critique else 0.0)
                accepted = best.attempt_number
            takes: List[TakeInfo] = []
            for att in shot.attempts:
                clip_url = _media_url(job.id, att.clip_url)
                crit = att.critique
                takes.append(
                    TakeInfo(
                        attempt=att.attempt_number,
                        prompt=att.prompt,
                        image_url=_media_url(job.id, att.image_url),
                        clip_url=clip_url,
                        created_at=att.created_at,
                        critique=TakeCritique(
                            score=crit.score,
                            passed=crit.passed,
                            reasons=crit.reasons,
                            suggested_prompt_fix=crit.suggested_prompt_fix,
                        )
                        if crit
                        else None,
                        accepted=accepted == att.attempt_number,
                        clip_available=_media_exists(job.id, clip_url),
                    )
                )
            shots_out.append(
                ShotTakes(
                    shot_id=shot.id,
                    scene_id=shot.scene_id,
                    status=shot.status.value,
                    duration_s=shot.duration_s,
                    accepted_attempt=accepted,
                    is_flagged_fallback=shot.is_flagged_fallback,
                    chained_from_shot_id=shot.chained_from_shot_id,
                    first_frame_url=_media_url(job.id, shot.first_frame_url),
                    image_url=_media_url(job.id, shot.image_url),
                    video_url=video_url,
                    audio_url=_media_url(job.id, shot.audio_url),
                    takes=takes,
                )
            )
    return JobTakesResponse(job_id=job.id, shots=shots_out)


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
                            results[j.id] = _job_summary(j)
                    except Exception:
                        pass

    # Merge in-memory cache
    for j_id, j in jobs_db.items():
        results[j_id] = _job_summary(j)

    samples = sample_job_ids()
    summaries = list(results.values())
    for summary in summaries:
        summary.is_sample = summary.id in samples
    summaries.sort(key=lambda s: s.created_at, reverse=True)
    summaries.sort(key=lambda s: not s.is_sample)  # stable: samples first, newest first
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


@app.get("/api/jobs/{job_id}/takes", response_model=JobTakesResponse)
async def get_job_takes(job_id: str) -> JobTakesResponse:
    """Every take of every shot: keyframe, clip, critic score/reasons/correction, accepted flag."""
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")
    return build_job_takes(job)


@app.post("/api/jobs/{job_id}/chat", dependencies=[Depends(require_demo_token)])
async def chat_job_refinement(job_id: str, payload: ChatRequest, request: Request) -> Dict[str, Any]:
    """
    Submits a director chat message to refine a completed job.
    Returns 409 Conflict if the job is currently processing another stage.
    """
    ensure_not_sample(job_id)
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

    if job.status == JobStatus.failed and not job.final_video_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This production never finished. Resume it first (POST /api/jobs/{id}/resume).",
        )

    consume_daily_quota(request)

    # Append user message to chat history
    user_msg = ChatMessage(role="user", text=message)
    job.messages.append(user_msg)
    job.status = JobStatus.generating
    save_job_state(job)

    # Publish a non-terminal event before returning so SSE subscribers that connect right away
    # do not stop at the previous version's DONE event.
    await event_bus.publish(job_id=job.id, step="INFO", message=f"Director note received: \"{message[:80]}\"")

    # Launch refinement in background
    spawn_job_task(job.id, run_refinement(job, message))

    next_ver = len(job.versions) + 1
    return {
        "status": "refining",
        "job_id": job.id,
        "message": message,
        "target_version": next_ver,
    }


@app.post("/api/jobs/{job_id}/resume", dependencies=[Depends(require_demo_token)])
async def resume_job(job_id: str, request: Request) -> Dict[str, Any]:
    """
    Resumes a failed/interrupted production, reusing every asset already generated
    (reference sheets, soundtrack, voiceover, accepted shots) and re-rendering the rest.
    """
    ensure_not_sample(job_id)
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")
    if job.id in _running_jobs:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is already running.")
    if job.status != JobStatus.failed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only failed jobs can be resumed (current status '{job.status.value}').",
        )

    consume_daily_quota(request)

    job.status = JobStatus.queued
    job.error = None
    save_job_state(job)
    await event_bus.publish(job_id=job.id, step="INFO", message=f"Resuming production for {job.id}...")
    spawn_job_task(job.id, run_pipeline(job))
    return {"status": "resuming", "job_id": job.id}


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


# Cloud Run rejects HTTP/1 responses with a Content-Length above 32 MiB, so large
# media (a 2-3 min film is ~40 MB) is served in capped byte ranges (video players
# simply request the next range) and full downloads are streamed chunked.
MEDIA_MAX_RANGE_BYTES = 16 * 1024 * 1024
MEDIA_DIRECT_LIMIT_BYTES = 24 * 1024 * 1024
_MEDIA_CHUNK = 1024 * 1024


def _iter_file(path: Path, start: int, length: int):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            data = fh.read(min(_MEDIA_CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _parse_range(header: str, size: int) -> Optional[tuple[int, int]]:
    try:
        unit, _, spec = header.partition("=")
        if unit.strip().lower() != "bytes" or "," in spec:
            return None
        start_s, _, end_s = spec.strip().partition("-")
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                return None
            start, end = max(size - suffix, 0), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return None
        return start, end
    except ValueError:
        return None


@app.api_route("/media/{file_path:path}", methods=["GET", "HEAD"])
async def get_media_file(file_path: str, request: Request):
    """Serves generated job media assets from active DATA_DIR."""
    jobs_root = (settings.DATA_DIR / "jobs").resolve()
    target_path = (jobs_root / file_path).resolve()
    if not target_path.is_relative_to(jobs_root) or not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media asset not found.",
        )
    size = target_path.stat().st_size
    if size <= MEDIA_DIRECT_LIMIT_BYTES:
        return FileResponse(target_path)

    import mimetypes

    media_type = mimetypes.guess_type(target_path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range")
    if range_header:
        parsed = _parse_range(range_header, size)
        if parsed is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start, end = parsed
        end = min(end, start + MEDIA_MAX_RANGE_BYTES - 1)
        length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        if request.method == "HEAD":
            return Response(status_code=206, headers=headers, media_type=media_type)
        return StreamingResponse(
            _iter_file(target_path, start, length), status_code=206, headers=headers, media_type=media_type
        )
    headers = {"Accept-Ranges": "bytes"}
    if request.method == "HEAD":
        return Response(status_code=200, headers=headers, media_type=media_type)
    # No Content-Length: chunked transfer encoding is not subject to the 32 MiB cap.
    return StreamingResponse(_iter_file(target_path, 0, size), headers=headers, media_type=media_type)

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

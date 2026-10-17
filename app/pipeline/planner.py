from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import uuid
from app.config import settings
from app.events import event_bus
from app.models import Job, JobStatus, Plan
from app.providers.factory import get_providers

logger = logging.getLogger("showrunner.pipeline.planner")


def save_job_state(job: Job) -> None:
    """Persists current job state to disk in DATA_DIR/jobs/<job_id>/job.json."""
    job.updated_at = datetime.now(timezone.utc).isoformat()
    job_dir = settings.get_job_dir(job.id)
    job_file = job_dir / "job.json"
    # Atomic write: a crash mid-write must never leave a truncated job.json behind.
    tmp_file = job_dir / f".job.json.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(job.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, job_file)
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass


async def run_planner(job: Job) -> Plan:
    """Executes the planning pipeline stage, emitting live events and saving state."""
    job.status = JobStatus.planning
    save_job_state(job)

    try:
        await event_bus.publish(
            job_id=job.id,
            step="PLAN",
            message=f"Agent initialized with creative brief: '{job.brief[:80]}...' (Mode: {job.mode.value})",
        )

        providers = get_providers()

        await event_bus.publish(
            job_id=job.id,
            step="PLAN",
            message=f"Using provider [{providers.provider_type.upper()}]. Designing aesthetic visual bible...",
        )

        # Call the planner provider
        plan = await providers.planner.generate_plan(
            brief=job.brief,
            mode=job.mode,
            ref_images=job.reference_images,
        )

        await event_bus.publish(
            job_id=job.id,
            step="PLAN",
            message=f"Visual Bible locked: mood '{plan.bible.style.mood}', palette {plan.bible.style.palette}",
            data={
                "palette": plan.bible.style.palette,
                "mood": plan.bible.style.mood,
                "lens": plan.bible.style.lens,
            },
        )

        total_shots = plan.total_shots
        total_duration = plan.total_duration_s
        await event_bus.publish(
            job_id=job.id,
            step="PLAN",
            message=(
                f"Generated story breakdown: '{plan.title}' with {len(plan.scenes)} scenes, "
                f"{total_shots} shots, total target runtime {total_duration:.1f}s"
            ),
            data={
                "title": plan.title,
                "scenes_count": len(plan.scenes),
                "total_shots": total_shots,
                "target_duration_s": total_duration,
            },
        )

        job.plan = plan
        job.status = JobStatus.planned
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="PLAN",
            message=f"Planning stage complete for job {job.id}. Blueprint ready for production.",
            data={"plan": plan.model_dump()},
        )

        return plan

    except Exception as exc:
        logger.exception("Error during plan generation for job %s", job.id)
        job.status = JobStatus.failed
        job.error = str(exc)
        save_job_state(job)
        await event_bus.publish(
            job_id=job.id,
            step="ERROR",
            message=f"Planning pipeline failed: {str(exc)}",
        )
        raise

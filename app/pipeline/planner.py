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
    """Persists current job state via the configured job store (file or Firestore)."""
    job.updated_at = datetime.now(timezone.utc).isoformat()
    from app.job_store import get_store

    get_store().save(job)


def plan_limits_note() -> str:
    """Brief suffix telling the planner about deployment cost caps (empty when uncapped)."""
    parts = []
    if settings.MAX_SHOTS_PER_JOB > 0:
        parts.append(f"at most {settings.MAX_SHOTS_PER_JOB} shots in total")
    if settings.MAX_RUNTIME_S > 0:
        parts.append(f"at most {int(settings.MAX_RUNTIME_S)} seconds total runtime")
    if not parts:
        return ""
    return (
        "\n\nHARD PRODUCTION LIMITS for this deployment (override every other length guidance): "
        + " and ".join(parts)
        + ". Tell the complete story within these limits."
    )


def enforce_plan_caps(plan: Plan) -> int:
    """Trims a plan to MAX_SHOTS_PER_JOB / MAX_RUNTIME_S (safety net if the model ignores
    the limits). Keeps story order, always keeps the final shot as the ending, and drops
    empty scenes. Returns the number of shots removed."""
    max_shots = settings.MAX_SHOTS_PER_JOB
    max_runtime = settings.MAX_RUNTIME_S
    if max_shots <= 0 and max_runtime <= 0:
        return 0
    flat = [(sc, sh) for sc in plan.scenes for sh in sc.shots]
    if not flat:
        return 0
    last = flat[-1]
    keep = []
    runtime = last[1].duration_s
    for item in flat[:-1]:
        if max_shots > 0 and len(keep) + 1 >= max_shots:
            break
        if max_runtime > 0 and runtime + item[1].duration_s > max_runtime:
            break
        keep.append(item)
        runtime += item[1].duration_s
    keep.append(last)
    keep_ids = {id(sh) for _, sh in keep}
    removed = len(flat) - len(keep)
    if removed:
        for sc in plan.scenes:
            sc.shots = [sh for sh in sc.shots if id(sh) in keep_ids]
            if sc.shots:
                sc.shots[0].chain_from_previous = False
        plan.scenes = [sc for sc in plan.scenes if sc.shots]
    return removed


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
            brief=job.brief + plan_limits_note(),
            mode=job.mode,
            ref_images=job.reference_images,
        )
        removed = enforce_plan_caps(plan)
        if removed:
            await event_bus.publish(
                job_id=job.id,
                step="PLAN",
                message=f"Public demo limits applied: trimmed {removed} shot(s) to stay within {settings.MAX_SHOTS_PER_JOB or '∞'} shots / {int(settings.MAX_RUNTIME_S) or '∞'}s.",
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

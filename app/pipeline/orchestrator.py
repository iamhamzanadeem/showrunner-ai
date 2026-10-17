from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import subprocess
from typing import List, Optional, Union

from app.config import settings
from app.events import event_bus
from app.models import (
    ChatMessage,
    CritiqueResult,
    Job,
    JobStatus,
    JobVersion,
    PlanEdit,
    ShotAttempt,
    ShotStatus,
    UpdatedShotSpec,
)
from app.pipeline.assembler import assemble_video
from app.pipeline.planner import run_planner, save_job_state
from app.providers.base import ContentFilteredError
from app.providers.factory import get_providers

logger = logging.getLogger("showrunner.pipeline.orchestrator")


async def extract_last_frame(clip_path: Path, out_image_path: Path) -> Path:
    """Extracts the exact last video frame using FFmpeg for visual continuity chaining."""
    cmd = [
        "ffmpeg", "-y",
        "-sseof", "-0.1",
        "-i", str(clip_path),
        "-frames:v", "1",
        "-update", "1",
        str(out_image_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc.wait()
    return out_image_path


def _text_to_video_prompt(job: Job, shot) -> str:
    """Prompt for a text-to-video fallback: the shot prompt plus the bible's entity specs so
    the character still matches when no first-frame image can be used."""
    plan = job.plan
    extras = []
    if plan:
        for c in plan.bible.characters:
            if not shot.characters_referenced or c.name in shot.characters_referenced:
                extras.append(f"{c.name}: {c.appearance}")
        for pr in plan.bible.products:
            if pr.name in (shot.products_referenced or []):
                extras.append(f"{pr.name}: {pr.description}")
        style = plan.bible.style
        extras.append(f"Look: {style.lighting}; {style.lens}; mood {style.mood}")
    return shot.visual_prompt + (" | " + " | ".join(extras) if extras else "")


async def make_still_clip(image_path: Path, duration_s: float, out_path: Path) -> Optional[Path]:
    """Last-resort shot: a slow push-in on the keyframe (FFmpeg, no model cost)."""
    frames = max(1, int(round(duration_s * 24)))
    vf = (
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
        f"zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1280x720:fps=24"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-vf", vf, "-t", f"{duration_s:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await proc.wait()
    return out_path if out_path.is_file() and out_path.stat().st_size > 0 else None


def _select_initial_frame(
    first_frame_path: Optional[Union[Path, str]],
    kf_path: Optional[Union[Path, str]],
) -> Optional[str]:
    """
    Selects the initial frame for video generation: chained first frame wins,
    otherwise falls back to the generated shot keyframe.
    """
    if first_frame_path and Path(first_frame_path).exists():
        return str(first_frame_path)
    if kf_path and Path(kf_path).exists():
        return str(kf_path)
    return None


def _entity_sheet_url(job: Job, name: str, kind: str) -> Optional[str]:
    refs = job.character_refs if kind == "character" else job.product_refs
    if refs.get(name):
        return refs[name]
    if job.plan:
        entities = job.plan.bible.characters if kind == "character" else job.plan.bible.products
        for e in entities:
            if e.name == name and e.reference_image:
                return e.reference_image
    return None


def _shot_reference_sheets(job: Job, shot, limit: int = 3) -> List[str]:
    """
    Returns reference-sheet URLs for the characters/products relevant to a shot (max `limit`).
    Relevance: explicit shot.characters_referenced / products_referenced, else a case-insensitive
    name match in the shot's visual prompt or description. If nothing matches, falls back to all
    character sheets, then product sheets.
    """
    if limit <= 0 or not job.plan:
        return []
    bible = job.plan.bible
    text = f"{shot.visual_prompt} {shot.description}".lower()
    explicit_chars = {n.lower() for n in (getattr(shot, "characters_referenced", None) or [])}
    explicit_prods = {n.lower() for n in (getattr(shot, "products_referenced", None) or [])}

    relevant: List[str] = []
    for kind, entities, explicit in (
        ("character", bible.characters, explicit_chars),
        ("product", bible.products, explicit_prods),
    ):
        for e in entities:
            lname = e.name.lower()
            if lname in explicit or (lname and lname in text):
                url = _entity_sheet_url(job, e.name, kind)
                if url and url not in relevant:
                    relevant.append(url)

    if not relevant:
        for kind, entities in (("character", bible.characters), ("product", bible.products)):
            for e in entities:
                url = _entity_sheet_url(job, e.name, kind)
                if url and url not in relevant:
                    relevant.append(url)
    return relevant[:limit]


def _resolve_existing_upload(url: Optional[str]) -> Optional[str]:
    """Returns the URL if it points at an existing user-uploaded file under DATA_DIR/jobs."""
    if not url or "/ref_images/" not in url:
        return None
    rel = url.lstrip("/")
    if rel.startswith("media/"):
        rel = rel[len("media/"):]
    candidate = (settings.DATA_DIR / "jobs" / rel).resolve()
    jobs_root = (settings.DATA_DIR / "jobs").resolve()
    if candidate.is_relative_to(jobs_root) and candidate.is_file():
        return url
    return None


def _existing_job_media(job: Job, url: Optional[str]) -> bool:
    """True if a /media/<job_id>/<file> URL points at an existing file of this job."""
    if not url:
        return False
    prefix = f"/media/{job.id}/"
    if not url.startswith(prefix):
        return False
    jobs_root = (settings.DATA_DIR / "jobs").resolve()
    candidate = (jobs_root / job.id / url[len(prefix):]).resolve()
    return candidate.is_relative_to(jobs_root) and candidate.is_file()


IN_FLIGHT_JOB_STATUSES = {
    JobStatus.queued,
    JobStatus.planning,
    JobStatus.generating,
    JobStatus.critiquing,
    JobStatus.assembling,
}
IN_FLIGHT_SHOT_STATUSES = {ShotStatus.generating, ShotStatus.critiquing, ShotStatus.retrying}
INTERRUPTED_MESSAGE = "Interrupted by server restart"


def recover_interrupted_job(job: Job) -> bool:
    """
    Marks a job that was in flight when the process died as no longer busy (Fix #7).
    - Fresh productions become `failed` (resumable via POST /api/jobs/{id}/resume).
    - Interrupted refinements of a job that already has a cut fall back to `completed`
      so the previous version stays viewable and chat edits are allowed again.
    Returns True if the job was modified.
    """
    if job.status not in IN_FLIGHT_JOB_STATUSES:
        return False
    if job.versions and job.final_video_url:
        job.status = JobStatus.completed
        job.error = f"{INTERRUPTED_MESSAGE} during refinement; showing last completed version."
    else:
        job.status = JobStatus.failed
        job.error = f"{INTERRUPTED_MESSAGE}. Use Resume to continue from completed shots."
    if job.plan:
        for scene in job.plan.scenes:
            for shot in scene.shots:
                if shot.status in IN_FLIGHT_SHOT_STATUSES:
                    shot.status = ShotStatus.failed
    return True


class ShotProductionError(RuntimeError):
    """Raised when a shot produced no usable clip after all attempts (fatal for the job)."""


async def _produce_shot(
    job: Job,
    scene,
    shot,
    s_idx: int,
    job_dir: Path,
    providers,
    first_frame_path: Optional[Path],
    start_attempt: int,
    max_attempts: int,
    is_refinement: bool = False,
) -> None:
    """
    Keyframe -> Veo clip -> critic loop for one shot, shared by run_pipeline and run_refinement.

    Failure policy (Fix #6):
      * A provider error on one attempt (keyframe/Veo) is logged and the next attempt is tried.
      * A critic error does not burn another Veo render: the clip is accepted, flagged as fallback.
      * When attempts are exhausted, the best-scoring clip so far is accepted (flagged fallback).
      * Only when NO usable clip exists at all is ShotProductionError raised (fatal for the job).
    """
    plan = job.plan
    verb = "Re-" if is_refinement else ""
    attempt = start_attempt
    best_critique: Optional[CritiqueResult] = None
    best_clip_path: Optional[Path] = None
    best_keyframe_url: Optional[str] = None
    last_error: Optional[BaseException] = None

    def _record_operation(op_name: str) -> None:
        # Persist the Veo long-running operation id atomically (save_job_state writes via
        # temp file + os.replace) right after submission and before polling, so a restarted
        # server can re-attach to it instead of paying for a second render.
        shot.veo_operation_name = op_name
        shot.veo_operation_attempt = attempt
        save_job_state(job)

    # Resume: a Veo operation submitted before a restart may still be running or already done.
    # (Not for chat refinements: a new edit must never be satisfied by a stale render.)
    resumed_clip: Optional[str] = None
    if shot.veo_operation_name and not is_refinement:
        saved_op = shot.veo_operation_name
        resume_attempt = shot.veo_operation_attempt or attempt
        await event_bus.publish(
            job_id=job.id,
            step="GEN",
            message=f"Re-attaching to saved Veo operation for {shot.id} (Attempt #{resume_attempt}) instead of re-rendering...",
            shot_id=shot.id,
            scene_id=scene.id,
            data={"attempt": resume_attempt, "duration_s": shot.duration_s, "image_url": shot.image_url, "resumed": True},
        )
        try:
            resumed_clip = await providers.video.resume_clip(
                saved_op, job_dir=job_dir, shot_id=shot.id, attempt=resume_attempt
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Resuming Veo operation %s for %s failed: %s", saved_op, shot.id, exc)
            resumed_clip = None
        if resumed_clip:
            attempt = max(attempt, resume_attempt)
            logger.info("Reused saved Veo operation %s for %s", saved_op, shot.id)
        else:
            logger.info("Saved Veo operation %s for %s not reusable; generating anew", saved_op, shot.id)
        shot.veo_operation_name = None
        shot.veo_operation_attempt = None
        save_job_state(job)

    # Set when Veo refuses/filters the first-frame image: later attempts go text-to-video.
    text_only = False
    last_kf_path: Optional[str] = None

    while attempt <= max_attempts:
        if resumed_clip:
            clip_path_str = resumed_clip
            resumed_clip = None
            keyframe_rel_url = shot.image_url
        else:
            await event_bus.publish(
                job_id=job.id,
                step="KEYFRAME",
                message=f"{verb}{'g' if verb else 'G'}enerating visual keyframe for {shot.id} (Attempt #{attempt})...",
                shot_id=shot.id,
                scene_id=scene.id,
                data={"attempt": attempt, "prompt": shot.visual_prompt},
            )

            try:
                kf_path: Optional[str] = None
                try:
                    kf_path = await providers.image.generate_keyframe(
                        prompt=shot.visual_prompt,
                        style=plan.bible.style,
                        ref_image=shot.first_frame_url if first_frame_path else None,
                        ref_images=_shot_reference_sheets(job, shot, limit=2 if first_frame_path else 3),
                        job_dir=job_dir,
                        shot_id=shot.id,
                        scene_num=scene.scene_number,
                        shot_num=s_idx + 1,
                        attempt=attempt,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as kf_exc:
                    # A keyframe failure (e.g. image-model 429) must not burn a whole attempt or
                    # fail the job: Veo can still render from the chained frame or from text.
                    logger.warning("Keyframe failed for %s attempt %d: %s", shot.id, attempt, kf_exc)
                    await event_bus.publish(
                        job_id=job.id,
                        step="RETRY",  # never "ERROR": that is terminal for SSE subscribers
                        message=(
                            f"Keyframe for {shot.id} unavailable ({kf_exc}); rendering "
                            f"{'from chained frame' if first_frame_path else 'text-to-video'} instead."
                        ),
                        shot_id=shot.id,
                        scene_id=scene.id,
                        data={"shot_id": shot.id, "attempt": attempt, "error": str(kf_exc)},
                    )
                if kf_path:
                    last_kf_path = kf_path
                    keyframe_rel_url = f"/media/{job.id}/{Path(kf_path).name}"
                elif first_frame_path:
                    keyframe_rel_url = shot.first_frame_url
                else:
                    keyframe_rel_url = None
                shot.image_url = keyframe_rel_url

                await event_bus.publish(
                    job_id=job.id,
                    step="GEN",
                    message=f"{verb}{'r' if verb else 'R'}endering clip for {shot.id} ({shot.duration_s:.1f}s, Attempt #{attempt})...",
                    shot_id=shot.id,
                    scene_id=scene.id,
                    data={"attempt": attempt, "duration_s": shot.duration_s, "image_url": keyframe_rel_url},
                )

                clip_path_str = await providers.video.generate_clip(
                    prompt=_text_to_video_prompt(job, shot) if text_only else shot.visual_prompt,
                    duration_s=shot.duration_s,
                    initial_frame=None if text_only else _select_initial_frame(first_frame_path, kf_path),
                    job_dir=job_dir,
                    shot_id=shot.id,
                    scene_num=scene.scene_number,
                    shot_num=s_idx + 1,
                    attempt=attempt,
                    reference_images=_shot_reference_sheets(job, shot, limit=3),
                    on_operation_started=_record_operation,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # provider failure for this attempt
                last_error = exc
                logger.warning("Attempt %d for %s failed: %s", attempt, shot.id, exc)
                if isinstance(exc, ContentFilteredError) and exc.input_image_rejected and not text_only:
                    text_only = True
                    logger.info("Veo refused the first frame for %s; next attempt is text-to-video", shot.id)
                await event_bus.publish(
                    job_id=job.id,
                    step="RETRY",  # never "ERROR": that is terminal for SSE subscribers
                    message=f"Generation attempt #{attempt} for {shot.id} failed: {exc}",
                    shot_id=shot.id,
                    scene_id=scene.id,
                    data={"shot_id": shot.id, "attempt": attempt, "error": str(exc)},
                )
                attempt += 1
                continue

        # Clip is on disk: the saved operation must never be re-attached to later.
        shot.veo_operation_name = None
        shot.veo_operation_attempt = None
        clip_path = Path(clip_path_str)
        clip_rel_url = f"/media/{job.id}/{clip_path.name}"
        shot.video_url = str(clip_path)

        shot.status = ShotStatus.critiquing
        save_job_state(job)

        critic_failed = False
        try:
            critique = await providers.critic.critique_clip(
                video_path=str(clip_path),
                shot=shot,
                bible=plan.bible,
                attempt=attempt,
                reference_images=_shot_reference_sheets(job, shot, limit=3),
                previous_frame=str(first_frame_path) if first_frame_path and first_frame_path.exists() else None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Critic failed for %s attempt %d: %s", shot.id, attempt, exc)
            critic_failed = True
            critique = CritiqueResult(
                shot_id=shot.id,
                passed=True,
                score=settings.SR_CRITIC_PASS_SCORE,
                reasons=[f"[critic_unavailable] Critic review failed ({exc}); clip accepted without review."],
                suggested_prompt_fix=None,
            )
        shot.last_critique = critique

        shot.attempts.append(
            ShotAttempt(
                attempt_number=attempt,
                prompt=shot.visual_prompt,
                image_url=keyframe_rel_url,
                clip_url=clip_rel_url,
                critique=critique,
            )
        )

        if best_critique is None or critique.score > best_critique.score:
            best_critique = critique
            best_clip_path = clip_path
            best_keyframe_url = keyframe_rel_url

        if critique.passed:
            shot.status = ShotStatus.passed
            shot.best_critique = critique
            if critic_failed:
                shot.is_flagged_fallback = True
            await event_bus.publish(
                job_id=job.id,
                step="CRITIC_PASS",
                message=(
                    f"Critic {'UNAVAILABLE - accepted' if critic_failed else 'PASSED'} {shot.id} "
                    f"(Score: {critique.score:.2f}). {'; '.join(critique.reasons)}"
                ),
                shot_id=shot.id,
                scene_id=scene.id,
                data={
                    "shot_id": shot.id,
                    "score": critique.score,
                    "reasons": critique.reasons,
                    "attempt": attempt,
                    "image_url": shot.image_url,
                },
            )
            return

        if attempt < max_attempts:
            shot.status = ShotStatus.retrying
            shot.retry_count += 1
            await event_bus.publish(
                job_id=job.id,
                step="CRITIC_FAIL",
                message=f"Critic REJECTED {shot.id} (Score: {critique.score:.2f}). Issues: {'; '.join(critique.reasons)}",
                shot_id=shot.id,
                scene_id=scene.id,
                data={
                    "shot_id": shot.id,
                    "score": critique.score,
                    "reasons": critique.reasons,
                    "suggested_prompt_fix": critique.suggested_prompt_fix,
                    "attempt": attempt,
                },
            )
            fix = critique.suggested_prompt_fix or "Improve contrast and character consistency"
            shot.prompt_history.append(shot.visual_prompt)
            revised_prompt = f"{shot.visual_prompt} | Correction: {fix}"
            shot.visual_prompt = revised_prompt
            await event_bus.publish(
                job_id=job.id,
                step="RETRY",
                message=f"Retrying {shot.id} with revised directive: '{fix}'",
                shot_id=shot.id,
                scene_id=scene.id,
                data={"shot_id": shot.id, "revised_prompt": revised_prompt, "next_attempt": attempt + 1},
            )
            save_job_state(job)
        attempt += 1

    # Attempts exhausted
    if best_clip_path is not None and best_critique is not None:
        shot.status = ShotStatus.passed
        shot.is_flagged_fallback = True
        shot.best_critique = best_critique
        shot.video_url = str(best_clip_path)
        shot.image_url = best_keyframe_url
        await event_bus.publish(
            job_id=job.id,
            step="CRITIC_FAIL",
            message=f"Max retries reached for {shot.id}. Accepting best attempt (Score: {best_critique.score:.2f}) with fallback flag.",
            shot_id=shot.id,
            scene_id=scene.id,
            data={"shot_id": shot.id, "score": best_critique.score, "fallback": True, "image_url": best_keyframe_url},
        )
        return

    # No model clip at all (e.g. every attempt safety-filtered): keep the film whole with a
    # slow push-in on the keyframe instead of failing the entire job.
    # Only for safety refusals: outages stay fatal (and resumable) rather than becoming a slideshow.
    still_src = last_kf_path or (str(first_frame_path) if first_frame_path else None)
    if isinstance(last_error, ContentFilteredError) and still_src and Path(still_src).is_file():
        still = await make_still_clip(Path(still_src), shot.duration_s, job_dir / f"{shot.id}_still.mp4")
        if still:
            fallback_critique = CritiqueResult(
                shot_id=shot.id,
                passed=False,
                score=0.0,
                reasons=[f"[fallback_still] Video model produced no clip ({last_error}); using an animated still."],
                suggested_prompt_fix=None,
            )
            shot.status = ShotStatus.passed
            shot.is_flagged_fallback = True
            shot.best_critique = fallback_critique
            shot.video_url = str(still)
            shot.veo_operation_name = None
            shot.veo_operation_attempt = None
            if last_kf_path:
                shot.image_url = f"/media/{job.id}/{Path(last_kf_path).name}"
            save_job_state(job)
            await event_bus.publish(
                job_id=job.id,
                step="CRITIC_FAIL",
                message=f"No usable video for {shot.id} ({last_error}). Using an animated still so the production can finish.",
                shot_id=shot.id,
                scene_id=scene.id,
                data={"shot_id": shot.id, "score": 0.0, "fallback": True, "image_url": shot.image_url},
            )
            return

    shot.status = ShotStatus.failed
    save_job_state(job)
    raise ShotProductionError(
        f"Shot {shot.id} produced no usable clip after {max_attempts - start_attempt + 1} attempt(s): {last_error}"
    )


async def run_pipeline(job: Job) -> None:
    """Full end-to-end agent production loop: Plan -> Ref -> Assets -> Critic Loop -> Assembly."""
    job_dir = settings.get_job_dir(job.id)
    providers = get_providers()
    job.error = None

    try:
        # STEP 1: PLANNING STAGE
        if not job.plan:
            await run_planner(job)

        plan = job.plan
        if not plan:
            raise ValueError(f"Job {job.id} failed to produce a production plan.")

        # STEP 2: BIBLE & REFERENCE SHEETS
        await event_bus.publish(
            job_id=job.id,
            step="REF",
            message=f"Generating style bible reference sheets for {len(plan.bible.characters)} character(s) and {len(plan.bible.products)} product(s)...",
        )

        for char in plan.bible.characters:
            if _existing_job_media(job, job.character_refs.get(char.name)):
                continue  # resume: sheet already generated
            ref_path = await providers.image.generate_ref_sheet(
                entity_type="character",
                name=char.name,
                description=char.appearance,
                style=plan.bible.style,
                job_dir=job_dir,
                ref_image=_resolve_existing_upload(char.reference_image),
            )
            rel_url = f"/media/{job.id}/{Path(ref_path).name}"
            char.reference_image = rel_url
            job.character_refs[char.name] = rel_url
            await event_bus.publish(
                job_id=job.id,
                step="REF",
                message=f"Character reference sheet generated: '{char.name}'",
                data={"entity": char.name, "url": rel_url, "type": "character"},
            )

        for prod in plan.bible.products:
            if _existing_job_media(job, job.product_refs.get(prod.name)):
                continue  # resume: sheet already generated
            ref_path = await providers.image.generate_ref_sheet(
                entity_type="product",
                name=prod.name,
                description=prod.description,
                style=plan.bible.style,
                job_dir=job_dir,
                ref_image=_resolve_existing_upload(prod.reference_image),
            )
            rel_url = f"/media/{job.id}/{Path(ref_path).name}"
            prod.reference_image = rel_url
            job.product_refs[prod.name] = rel_url
            await event_bus.publish(
                job_id=job.id,
                step="REF",
                message=f"Product reference sheet generated: '{prod.name}'",
                data={"entity": prod.name, "url": rel_url, "type": "product"},
            )

        save_job_state(job)

        # STEP 3: VOICEOVER & SOUNDTRACK GENERATION
        job.status = JobStatus.generating
        save_job_state(job)

        # Generate soundtrack
        total_duration = plan.total_duration_s
        await event_bus.publish(
            job_id=job.id,
            step="MUSIC",
            message=f"Synthesizing atmospheric soundtrack score ({total_duration:.1f}s, mood: '{plan.bible.style.mood}')...",
        )
        if job.soundtrack_url and Path(job.soundtrack_url).is_file():
            music_path = Path(job.soundtrack_url)  # resume: soundtrack already generated
        else:
            music_path = await providers.music.generate_music(
                prompt=plan.bible.style.mood,
                duration_s=total_duration,
                job_dir=job_dir,
            )
        job.soundtrack_url = str(music_path)
        await event_bus.publish(
            job_id=job.id,
            step="MUSIC",
            message="Master soundtrack score generated successfully.",
            data={"soundtrack_url": f"/media/{job.id}/{Path(music_path).name}"},
        )

        # Generate voiceover audio tracks
        for scene in plan.scenes:
            for shot in scene.shots:
                if shot.audio_url and Path(shot.audio_url).is_file():
                    continue  # resume: voiceover already generated
                if shot.vo_line and shot.vo_line.strip():
                    await event_bus.publish(
                        job_id=job.id,
                        step="VO",
                        message=f"Synthesizing voiceover for {shot.id}: \"{shot.vo_line[:50]}...\"",
                        shot_id=shot.id,
                        scene_id=scene.id,
                    )
                    vo_path = await providers.tts.generate_voiceover(
                        text=shot.vo_line,
                        duration_s=shot.duration_s,
                        job_dir=job_dir,
                        shot_id=shot.id,
                    )
                    shot.audio_url = str(vo_path)

        save_job_state(job)

        # STEP 4: SHOT SYNTHESIS & CRITIC LOOP (Limited scene concurrency, sequential within scene)
        semaphore = asyncio.Semaphore(settings.MAX_PARALLEL_SCENES)
        async def process_scene(scene) -> None:
            async with semaphore:
                last_clip_path: Optional[Path] = None
                prev_shot_id: Optional[str] = None

                for s_idx, shot in enumerate(scene.shots):
                    if shot.status == ShotStatus.passed and shot.video_url and Path(shot.video_url).is_file():
                        # resume: shot already rendered and accepted
                        last_clip_path = Path(shot.video_url)
                        prev_shot_id = shot.id
                        continue

                    shot.status = ShotStatus.generating
                    save_job_state(job)

                    # Frame Chaining Check
                    first_frame_path: Optional[Path] = None
                    if shot.chain_from_previous and last_clip_path and last_clip_path.exists():
                        first_frame_path = job_dir / f"{prev_shot_id}_lastframe.png"
                        await extract_last_frame(last_clip_path, first_frame_path)
                        shot.first_frame_url = f"/media/{job.id}/{first_frame_path.name}"
                        shot.chained_from_shot_id = prev_shot_id

                        await event_bus.publish(
                            job_id=job.id,
                            step="CHAIN",
                            message=f"Continuity chain locked: {shot.id} first frame derived from {prev_shot_id} last frame",
                            shot_id=shot.id,
                            scene_id=scene.id,
                            data={
                                "shot_id": shot.id,
                                "chained_from": prev_shot_id,
                                "first_frame_url": shot.first_frame_url,
                            },
                        )

                    # Keyframe -> clip -> critic loop with best-so-far fallback
                    await _produce_shot(
                        job, scene, shot, s_idx, job_dir, providers, first_frame_path,
                        start_attempt=1, max_attempts=settings.MAX_RETRIES + 1,
                    )

                    last_clip_path = Path(shot.video_url)
                    prev_shot_id = shot.id
                    save_job_state(job)

        # Run scenes with concurrency control. If any scene fails fatally, cancel its siblings
        # so no further paid generations are started for a job that is already failed (Fix #6).
        scene_tasks = [asyncio.create_task(process_scene(scene)) for scene in plan.scenes]
        try:
            await asyncio.gather(*scene_tasks)
        except BaseException:
            for t in scene_tasks:
                t.cancel()
            await asyncio.gather(*scene_tasks, return_exceptions=True)
            raise

        # STEP 5: FINAL FFMPEG ASSEMBLY
        job.status = JobStatus.assembling
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="ASSEMBLE",
            message=f"Beginning master assembly: concatenating {plan.total_shots} clips, burning captions, mixing VO & score...",
        )

        final_url, poster_url, srt_url = await assemble_video(job, job_dir, version=1)

        job.final_video_url = final_url
        job.poster_url = poster_url
        job.captions_srt_url = srt_url
        job.status = JobStatus.completed

        # Record initial version 1 if not present
        if not job.versions:
            job.versions.append(
                JobVersion(
                    version=1,
                    url=final_url,
                    poster_url=poster_url,
                    message="Initial production cut",
                    changed_shots=[s.id for sc in plan.scenes for s in sc.shots],
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="DONE",
            message=f"Master video production complete! Video assembly available at {final_url}",
            data={
                "version": 1,
                "final_video_url": final_url,
                "poster_url": poster_url,
                "captions_srt_url": srt_url,
                "total_duration_s": plan.total_duration_s,
            },
        )

    except Exception as exc:
        logger.exception("Error in pipeline orchestration for job %s", job.id)
        job.status = JobStatus.failed
        job.error = str(exc)
        save_job_state(job)
        await event_bus.publish(
            job_id=job.id,
            step="ERROR",
            message=f"Pipeline execution halted: {str(exc)}",
        )
        raise


async def run_refinement(job: Job, user_message: str) -> None:
    """
    Executes a targeted refinement on a completed job:
    1. Requests PlanEdit from planner editor.
    2. Modifies affected shots' visual prompts, VO, or camera.
    3. Reshoots ONLY affected shots (plus any chained follow-up shots in the same scene).
    4. Re-assembles new versioned output final_v{N}.mp4 and emits events.
    """
    job_dir = settings.get_job_dir(job.id)
    providers = get_providers()

    try:
        if not job.plan:
            raise ValueError(f"Job {job.id} has no production plan to refine.")

        job.status = JobStatus.generating
        save_job_state(job)

        # 1. Ask planner editor for targeted edits
        edit: PlanEdit = await providers.planner.edit_plan(job.plan, user_message)

        if edit.style_changes:
            job.plan.bible.style = edit.style_changes

        updates_by_id = {u.id: u for u in edit.updated_shots}
        affected_shot_ids = set(edit.target_shot_ids) | set(updates_by_id.keys())

        if not affected_shot_ids and edit.target_scene_ids:
            for sc in job.plan.scenes:
                if sc.id in edit.target_scene_ids:
                    for sh in sc.shots:
                        affected_shot_ids.add(sh.id)

        if not affected_shot_ids and job.plan.scenes:
            for sh in job.plan.scenes[0].shots:
                affected_shot_ids.add(sh.id)

        # Continuity chaining check within scene:
        # If shot k is regenerated and shot k+1 chains from it, also regenerate k+1!
        for scene in job.plan.scenes:
            for idx in range(len(scene.shots) - 1):
                cur_shot = scene.shots[idx]
                nxt_shot = scene.shots[idx + 1]
                if cur_shot.id in affected_shot_ids and nxt_shot.chain_from_previous:
                    if nxt_shot.id not in affected_shot_ids:
                        affected_shot_ids.add(nxt_shot.id)
                        await event_bus.publish(
                            job_id=job.id,
                            step="CHAIN",
                            message=f"Continuity chain: {nxt_shot.id} marked for reshoot because prior shot {cur_shot.id} is being reshot",
                            shot_id=nxt_shot.id,
                            scene_id=scene.id,
                        )

        # Publish EDIT SSE event
        await event_bus.publish(
            job_id=job.id,
            step="EDIT",
            message=f"Plan refinement accepted: {edit.summary}",
            data={
                "summary": edit.summary,
                "target_scene_ids": edit.target_scene_ids,
                "affected_shot_ids": sorted(list(affected_shot_ids)),
                "rationale": edit.rationale,
            },
        )

        # Record agent message in chat thread
        agent_msg = f"{edit.summary} Re-shooting shot(s): {', '.join(sorted(affected_shot_ids))}."
        job.messages.append(
            ChatMessage(
                role="agent",
                text=agent_msg,
            )
        )
        save_job_state(job)

        # 2. Update shot metadata and regenerate VO if line changed
        for scene in job.plan.scenes:
            for shot in scene.shots:
                if shot.id in updates_by_id:
                    spec = updates_by_id[shot.id]
                    if spec.visual_prompt:
                        shot.visual_prompt = spec.visual_prompt
                    if spec.description:
                        shot.description = spec.description
                    if spec.camera:
                        shot.camera = spec.camera
                    if spec.duration_s:
                        shot.duration_s = spec.duration_s
                    if spec.vo_line is not None and spec.vo_line != shot.vo_line:
                        shot.vo_line = spec.vo_line
                        await event_bus.publish(
                            job_id=job.id,
                            step="VO",
                            message=f"Re-synthesizing voiceover for {shot.id}: \"{shot.vo_line[:50]}...\"",
                            shot_id=shot.id,
                            scene_id=scene.id,
                        )
                        vo_path = await providers.tts.generate_voiceover(
                            text=shot.vo_line,
                            duration_s=shot.duration_s,
                            job_dir=job_dir,
                            shot_id=shot.id,
                        )
                        shot.audio_url = str(vo_path)

        save_job_state(job)

        # 3. Reshoot only affected shots in each scene
        for scene in job.plan.scenes:
            scene_affected = [s for s in scene.shots if s.id in affected_shot_ids]
            if not scene_affected:
                continue

            last_clip_path = None
            prev_shot_id = None

            for s_idx, shot in enumerate(scene.shots):
                if shot.id not in affected_shot_ids:
                    if shot.video_url and Path(shot.video_url).exists():
                        last_clip_path = Path(shot.video_url)
                        prev_shot_id = shot.id
                    continue

                shot.status = ShotStatus.generating
                save_job_state(job)

                # Frame Chaining Check
                first_frame_path = None
                if shot.chain_from_previous and last_clip_path and last_clip_path.exists():
                    first_frame_path = job_dir / f"{prev_shot_id}_lastframe.png"
                    await extract_last_frame(last_clip_path, first_frame_path)
                    shot.first_frame_url = f"/media/{job.id}/{first_frame_path.name}"
                    shot.chained_from_shot_id = prev_shot_id
                    await event_bus.publish(
                        job_id=job.id,
                        step="CHAIN",
                        message=f"Continuity chain locked: {shot.id} first frame derived from {prev_shot_id} last frame",
                        shot_id=shot.id,
                        scene_id=scene.id,
                    )

                # Keyframe -> clip -> critic loop with best-so-far fallback
                start_attempt = len(shot.attempts) + 1
                await _produce_shot(
                    job, scene, shot, s_idx, job_dir, providers, first_frame_path,
                    start_attempt=start_attempt, max_attempts=start_attempt + settings.MAX_RETRIES,
                    is_refinement=True,
                )

                last_clip_path = Path(shot.video_url)
                prev_shot_id = shot.id
                save_job_state(job)

        # 4. Master Assembly for new version
        next_version = len(job.versions) + 1
        job.status = JobStatus.assembling
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="ASSEMBLE",
            message=f"Re-assembling production version v{next_version}...",
        )

        final_url, poster_url, srt_url = await assemble_video(job, job_dir, version=next_version)

        job.final_video_url = final_url
        job.poster_url = poster_url
        job.captions_srt_url = srt_url
        job.status = JobStatus.completed

        # Record version
        job.versions.append(
            JobVersion(
                version=next_version,
                url=final_url,
                poster_url=poster_url,
                message=user_message,
                changed_shots=sorted(list(affected_shot_ids)),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="DONE",
            message=f"Version v{next_version} complete! Video available at {final_url}",
            data={
                "version": next_version,
                "final_video_url": final_url,
                "poster_url": poster_url,
                "changed_shots": sorted(list(affected_shot_ids)),
            },
        )

    except Exception as exc:
        logger.exception("Error in refinement orchestration for job %s", job.id)
        # Shots that failed to reshoot keep their previous accepted clip from the last version
        if job.plan:
            for sc in job.plan.scenes:
                for sh in sc.shots:
                    if sh.status != ShotStatus.passed and sh.video_url and Path(sh.video_url).is_file():
                        sh.status = ShotStatus.passed
        job.status = JobStatus.completed
        job.error = str(exc)
        save_job_state(job)
        await event_bus.publish(
            job_id=job.id,
            step="ERROR",
            message=f"Refinement failed: {str(exc)}",
        )
        raise

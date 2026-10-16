from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import subprocess
from typing import Optional

from app.config import settings
from app.events import event_bus
from app.models import (
    Job,
    JobStatus,
    ShotAttempt,
    ShotStatus,
)
from app.pipeline.assembler import assemble_video
from app.pipeline.planner import run_planner, save_job_state
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


async def run_pipeline(job: Job) -> None:
    """Full end-to-end agent production loop: Plan -> Ref -> Assets -> Critic Loop -> Assembly."""
    job_dir = settings.get_job_dir(job.id)
    providers = get_providers()

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
            ref_path = await providers.image.generate_ref_sheet(
                entity_type="character",
                name=char.name,
                description=char.appearance,
                style=plan.bible.style,
                job_dir=job_dir,
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
            ref_path = await providers.image.generate_ref_sheet(
                entity_type="product",
                name=prod.name,
                description=prod.description,
                style=plan.bible.style,
                job_dir=job_dir,
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

                    # Critic & Generation Retry Loop
                    attempt = 1
                    max_attempts = settings.MAX_RETRIES + 1
                    best_critique = None
                    best_clip_path = None
                    best_keyframe_url = None

                    while attempt <= max_attempts:
                        # 1. Generate keyframe
                        await event_bus.publish(
                            job_id=job.id,
                            step="KEYFRAME",
                            message=f"Generating visual keyframe for {shot.id} (Attempt #{attempt})...",
                            shot_id=shot.id,
                            scene_id=scene.id,
                            data={"attempt": attempt, "prompt": shot.visual_prompt},
                        )

                        kf_path = await providers.image.generate_keyframe(
                            prompt=shot.visual_prompt,
                            style=plan.bible.style,
                            ref_image=shot.first_frame_url,
                            job_dir=job_dir,
                            shot_id=shot.id,
                            scene_num=scene.scene_number,
                            shot_num=s_idx + 1,
                            attempt=attempt,
                        )
                        keyframe_rel_url = f"/media/{job.id}/{Path(kf_path).name}"
                        shot.image_url = keyframe_rel_url

                        # 2. Generate video clip
                        await event_bus.publish(
                            job_id=job.id,
                            step="GEN",
                            message=f"Rendering clip for {shot.id} ({shot.duration_s:.1f}s, Attempt #{attempt})...",
                            shot_id=shot.id,
                            scene_id=scene.id,
                            data={"attempt": attempt, "duration_s": shot.duration_s},
                        )

                        clip_path_str = await providers.video.generate_clip(
                            prompt=shot.visual_prompt,
                            duration_s=shot.duration_s,
                            initial_frame=str(first_frame_path) if first_frame_path and first_frame_path.exists() else None,
                            job_dir=job_dir,
                            shot_id=shot.id,
                            scene_num=scene.scene_number,
                            shot_num=s_idx + 1,
                            attempt=attempt,
                        )
                        clip_path = Path(clip_path_str)
                        clip_rel_url = f"/media/{job.id}/{clip_path.name}"
                        shot.video_url = str(clip_path)

                        # 3. Multimodal Critic Review
                        shot.status = ShotStatus.critiquing
                        save_job_state(job)

                        critique = await providers.critic.critique_clip(
                            video_path=str(clip_path),
                            shot=shot,
                            bible=plan.bible,
                            attempt=attempt,
                        )
                        shot.last_critique = critique

                        # Record shot attempt
                        shot_attempt = ShotAttempt(
                            attempt_number=attempt,
                            prompt=shot.visual_prompt,
                            image_url=keyframe_rel_url,
                            clip_url=clip_rel_url,
                            critique=critique,
                        )
                        shot.attempts.append(shot_attempt)

                        if best_critique is None or critique.score > best_critique.score:
                            best_critique = critique
                            best_clip_path = clip_path
                            best_keyframe_url = keyframe_rel_url

                        if critique.passed:
                            shot.status = ShotStatus.passed
                            shot.best_critique = critique
                            await event_bus.publish(
                                job_id=job.id,
                                step="CRITIC_PASS",
                                message=f"Critic PASSED {shot.id} (Score: {critique.score:.2f}). {'; '.join(critique.reasons)}",
                                shot_id=shot.id,
                                scene_id=scene.id,
                                data={
                                    "shot_id": shot.id,
                                    "score": critique.score,
                                    "reasons": critique.reasons,
                                    "attempt": attempt,
                                },
                            )
                            break
                        else:
                            # Critic Failed
                            if attempt < max_attempts:
                                shot.status = ShotStatus.retrying
                                shot.retry_count = attempt
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

                                # Merge prompt revision
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
                                attempt += 1
                                save_job_state(job)
                            else:
                                # Exhausted retries: accept best-scoring fallback
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
                                    data={"shot_id": shot.id, "score": best_critique.score, "fallback": True},
                                )
                                break

                    last_clip_path = Path(shot.video_url)
                    prev_shot_id = shot.id
                    save_job_state(job)

        # Run scenes with concurrency control
        await asyncio.gather(*(process_scene(scene) for scene in plan.scenes))

        # STEP 5: FINAL FFMPEG ASSEMBLY
        job.status = JobStatus.assembling
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="ASSEMBLE",
            message=f"Beginning master assembly: concatenating {plan.total_shots} clips, burning captions, mixing VO & score...",
        )

        final_url, poster_url, srt_url = await assemble_video(job, job_dir)

        job.final_video_url = final_url
        job.poster_url = poster_url
        job.captions_srt_url = srt_url
        job.status = JobStatus.completed
        save_job_state(job)

        await event_bus.publish(
            job_id=job.id,
            step="DONE",
            message=f"Master video production complete! Video assembly available at {final_url}",
            data={
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

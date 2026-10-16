from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from typing import List, Tuple
from app.models import Job


def _format_srt_time(seconds: float) -> str:
    """Formats float seconds into SRT timestamp HH:MM:SS,mmm."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def generate_srt(job: Job, srt_path: Path) -> Path:
    """Generates an SRT subtitle file from shots with voiceover lines."""
    current_time = 0.0
    entries = []
    index = 1

    if job.plan:
        for scene in job.plan.scenes:
            for shot in scene.shots:
                shot_duration = shot.duration_s
                if shot.vo_line and shot.vo_line.strip():
                    start_s = current_time + 0.3
                    vo_duration = min(shot_duration - 0.4, max(1.5, len(shot.vo_line.split()) * 0.35))
                    end_s = start_s + vo_duration

                    start_str = _format_srt_time(start_s)
                    end_str = _format_srt_time(end_s)

                    entries.append(f"{index}\n{start_str} --> {end_str}\n{shot.vo_line.strip()}\n")
                    index += 1

                current_time += shot_duration

    content = "\n".join(entries) if entries else "1\n00:00:00,000 --> 00:00:01,000\n \n"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(content)
    return srt_path


async def assemble_video(job: Job, job_dir: Path) -> Tuple[str, str, str]:
    """
    Subprocess FFmpeg pipeline assembling all shots, VO audio, music, and subtitles into final.mp4.
    Returns: (final_video_url, poster_url, captions_srt_url)
    """
    if not job.plan or not job.plan.scenes:
        raise ValueError(f"Job {job.id} has no planned scenes to assemble.")

    all_shots = [shot for scene in job.plan.scenes for shot in scene.shots]
    if not all_shots:
        raise ValueError(f"Job {job.id} has no shots to assemble.")

    # 1. Normalize each shot clip
    normalized_clips: List[Path] = []
    current_timeline_offset = 0.0
    vo_audio_inputs: List[Tuple[int, Path]] = []  # (offset_ms, vo_path)

    for scene in job.plan.scenes:
        shots_in_scene = len(scene.shots)
        for s_idx, shot in enumerate(scene.shots):
            dur = shot.duration_s
            norm_clip = job_dir / f"norm_{shot.id}.mp4"

            # Check source clip
            src_clip = Path(shot.video_url) if shot.video_url and Path(shot.video_url).exists() else None
            if not src_clip:
                # Fallback to att1
                cand = job_dir / f"{shot.id}_att1.mp4"
                if cand.exists():
                    src_clip = cand

            # Transition fades: fade in at start of scene, fade out at end of scene
            is_scene_start = (s_idx == 0)
            is_scene_end = (s_idx == shots_in_scene - 1)

            fade_filters = []
            if is_scene_start:
                fade_filters.append("fade=t=in:st=0:d=0.35")
            if is_scene_end and dur > 0.7:
                fade_filters.append(f"fade=t=out:st={dur - 0.35:.2f}:d=0.35")

            vf_chain = "scale=1280:720,setsar=1:1"
            if fade_filters:
                vf_chain += "," + ",".join(fade_filters)

            if src_clip and src_clip.exists():
                cmd_norm = [
                    "ffmpeg", "-y",
                    "-i", str(src_clip),
                    "-t", f"{dur:.2f}",
                    "-vf", vf_chain,
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-r", "24",
                    str(norm_clip),
                ]
            else:
                # Generate fallback color card if clip missing
                cmd_norm = [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=0x0a0f1d:s=1280x720:d={dur:.2f}:r=24",
                    "-vf", vf_chain,
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-r", "24",
                    str(norm_clip),
                ]

            proc = await asyncio.create_subprocess_exec(
                *cmd_norm,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()
            normalized_clips.append(norm_clip)

            # Check if this shot has a VO track
            if shot.audio_url and Path(shot.audio_url).exists():
                offset_ms = int((current_timeline_offset + 0.3) * 1000)
                vo_audio_inputs.append((offset_ms, Path(shot.audio_url)))

            current_timeline_offset += dur

    # 2. Write concat list
    concat_list_file = job_dir / "concat_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for clip in normalized_clips:
            f.write(f"file '{clip.resolve()}'\n")

    concatenated_video = job_dir / "temp_concatenated.mp4"
    proc_concat = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy",
        str(concatenated_video),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc_concat.wait()

    # 3. Generate captions SRT
    srt_path = job_dir / "captions.srt"
    generate_srt(job, srt_path)

    # 4. Prepare Audio Mixing (Soundtrack + delayed VO)
    music_file = Path(job.soundtrack_url) if job.soundtrack_url and Path(job.soundtrack_url).exists() else None
    if not music_file or not music_file.exists():
        # Fallback soundtrack path
        cand_music = job_dir / "soundtrack.m4a"
        if cand_music.exists():
            music_file = cand_music

    final_video = job_dir / "final.mp4"

    # Build audio filter graph
    input_args = ["-i", str(concatenated_video)]
    filter_complex_parts = []
    audio_mix_inputs = []

    if music_file and music_file.exists():
        input_args.extend(["-i", str(music_file)])
        bgm_idx = len(input_args) // 2 - 1
        filter_complex_parts.append(f"[{bgm_idx}:a]volume=0.25[bgm]")
        audio_mix_inputs.append("[bgm]")

    for idx, (offset_ms, vo_path) in enumerate(vo_audio_inputs):
        input_args.extend(["-i", str(vo_path)])
        stream_idx = len(input_args) // 2 - 1
        filter_complex_parts.append(f"[{stream_idx}:a]adelay={offset_ms}|{offset_ms}[vo_{idx}]")
        audio_mix_inputs.append(f"[vo_{idx}]")

    has_audio = len(audio_mix_inputs) > 0
    if has_audio:
        if len(audio_mix_inputs) == 1:
            filter_complex_parts.append(f"{audio_mix_inputs[0]}anull[aout]")
        else:
            joined_inputs = "".join(audio_mix_inputs)
            filter_complex_parts.append(
                f"{joined_inputs}amix=inputs={len(audio_mix_inputs)}:duration=first:dropout_transition=2[aout]"
            )

    # Subtitle burn-in filter
    sub_filter = f"subtitles={srt_path.resolve()}:force_style='FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=3,BackColour=&H80000000,Alignment=2,MarginV=30'"

    final_cmd = ["ffmpeg", "-y"] + input_args

    if has_audio:
        filter_complex_str = ";".join(filter_complex_parts)
        final_cmd.extend([
            "-filter_complex", filter_complex_str,
            "-vf", sub_filter,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            str(final_video),
        ])
    else:
        final_cmd.extend([
            "-vf", sub_filter,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(final_video),
        ])

    proc_final = await asyncio.create_subprocess_exec(
        *final_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc_final.wait()

    # Fallback if subtitles filter had any issue (e.g. font / escaping)
    if not final_video.exists() or final_video.stat().st_size == 0:
        # Assemble without subtitles filter as fallback
        fallback_cmd = ["ffmpeg", "-y"] + input_args
        if has_audio:
            fallback_cmd.extend([
                "-filter_complex", ";".join(filter_complex_parts),
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                str(final_video),
            ])
        else:
            fallback_cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(final_video),
            ])
        proc_fb = await asyncio.create_subprocess_exec(
            *fallback_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await proc_fb.wait()

    # 5. Extract poster image
    poster_jpg = job_dir / "poster.jpg"
    proc_poster = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-ss", "00:00:01.00",
        "-i", str(final_video),
        "-vframes", "1",
        str(poster_jpg),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    await proc_poster.wait()

    # Cleanup temporary normalization files to conserve disk
    for norm in normalized_clips:
        try:
            norm.unlink(missing_ok=True)
        except Exception:
            pass
    concatenated_video.unlink(missing_ok=True)
    concat_list_file.unlink(missing_ok=True)

    final_url = f"/media/{job.id}/final.mp4"
    poster_url = f"/media/{job.id}/poster.jpg"
    srt_url = f"/media/{job.id}/captions.srt"

    return final_url, poster_url, srt_url

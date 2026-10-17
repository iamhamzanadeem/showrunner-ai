from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from typing import Dict, List, Optional, Tuple
from app.models import Job

# Max speed-up applied to a voiceover that is longer than its shot before it is allowed
# to spill into the next shot (subsequent lines are then pushed back, never overlapped).
VO_MAX_TEMPO = 1.25
VO_LEAD_IN_S = 0.3
VO_GAP_S = 0.15


def _probe_duration(path: Path) -> Optional[float]:
    """Returns media duration in seconds via ffprobe, or None if unavailable."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def schedule_voiceovers(
    items: List[Tuple[str, float, float, Optional[float]]],
) -> Dict[str, Dict[str, float]]:
    """Plans when each voiceover line plays so lines never overlap.

    items: (shot_id, shot_start_s, shot_duration_s, vo_duration_s or None) in timeline order.
    Returns {shot_id: {"start": s, "end": s, "tempo": t}}. Real TTS lines often run longer
    than a 4-6s shot; they are sped up by at most VO_MAX_TEMPO and otherwise pushed later.
    """
    plan: Dict[str, Dict[str, float]] = {}
    prev_end = 0.0
    for shot_id, shot_start, shot_dur, vo_dur in items:
        slot = max(0.5, shot_dur - VO_LEAD_IN_S)
        tempo = 1.0
        if vo_dur is None:
            vo_dur = min(slot, 1.5)
        if vo_dur > slot:
            tempo = min(VO_MAX_TEMPO, vo_dur / slot)
        eff = vo_dur / tempo
        start = max(shot_start + VO_LEAD_IN_S, prev_end + VO_GAP_S if prev_end else 0.0)
        end = start + eff
        plan[shot_id] = {"start": start, "end": end, "tempo": tempo}
        prev_end = end
    return plan


def _format_srt_time(seconds: float) -> str:
    """Formats float seconds into SRT timestamp HH:MM:SS,mmm."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def generate_srt(
    job: Job, srt_path: Path, vo_timings: Optional[Dict[str, Dict[str, float]]] = None
) -> Path:
    """Generates an SRT subtitle file from shots with voiceover lines.

    When vo_timings (from schedule_voiceovers) is given, captions follow the actual audio.
    """
    current_time = 0.0
    entries = []
    index = 1

    if job.plan:
        for scene in job.plan.scenes:
            for shot in scene.shots:
                shot_duration = shot.duration_s
                if shot.vo_line and shot.vo_line.strip():
                    timing = (vo_timings or {}).get(shot.id)
                    if timing:
                        start_s, end_s = timing["start"], timing["end"]
                    else:
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


async def assemble_video(job: Job, job_dir: Path, version: int = 1) -> Tuple[str, str, str]:
    """
    Subprocess FFmpeg pipeline assembling all shots, VO audio, music, and subtitles into versioned MP4.
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
    vo_items: List[Tuple[str, float, float, Path]] = []  # (shot_id, start_s, dur_s, vo_path)

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
                vo_items.append((shot.id, current_timeline_offset, dur, Path(shot.audio_url)))

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

    total_duration = current_timeline_offset

    # Schedule VO lines so long real-TTS lines don't talk over each other.
    vo_durations = await asyncio.gather(
        *[asyncio.to_thread(_probe_duration, p) for (_, _, _, p) in vo_items]
    )
    vo_timings = schedule_voiceovers(
        [(sid, start, dur, vd) for (sid, start, dur, _), vd in zip(vo_items, vo_durations)]
    )

    # 3. Generate captions SRT
    srt_path = job_dir / "captions.srt"
    generate_srt(job, srt_path, vo_timings=vo_timings)

    # 4. Prepare Audio Mixing (Soundtrack + delayed VO)
    music_file = Path(job.soundtrack_url) if job.soundtrack_url and Path(job.soundtrack_url).exists() else None
    if not music_file or not music_file.exists():
        # Fallback soundtrack path
        cand_music = job_dir / "soundtrack.m4a"
        if cand_music.exists():
            music_file = cand_music

    video_filename = f"final_v{version}.mp4" if version > 1 else "final.mp4"
    poster_filename = f"poster_v{version}.jpg" if version > 1 else "poster.jpg"
    final_video = job_dir / video_filename
    poster_jpg = job_dir / poster_filename

    # Build audio filter graph
    input_args = ["-i", str(concatenated_video)]
    filter_complex_parts = []
    audio_mix_inputs = []

    if music_file and music_file.exists():
        # Loop the score if it is shorter than the cut (output is trimmed with -t).
        input_args.extend(["-stream_loop", "-1", "-i", str(music_file)])
        bgm_idx = input_args.count("-i") - 1
        fade_st = max(0.0, total_duration - 1.5)
        filter_complex_parts.append(
            f"[{bgm_idx}:a]volume=0.25,afade=t=out:st={fade_st:.2f}:d=1.5[bgm]"
        )
        audio_mix_inputs.append("[bgm]")

    for idx, (shot_id, _start, _dur, vo_path) in enumerate(vo_items):
        input_args.extend(["-i", str(vo_path)])
        stream_idx = input_args.count("-i") - 1
        timing = vo_timings[shot_id]
        offset_ms = int(timing["start"] * 1000)
        tempo = timing["tempo"]
        tempo_filter = f"atempo={tempo:.3f}," if tempo > 1.01 else ""
        filter_complex_parts.append(
            f"[{stream_idx}:a]{tempo_filter}adelay={offset_ms}|{offset_ms}[vo_{idx}]"
        )
        audio_mix_inputs.append(f"[vo_{idx}]")

    has_audio = len(audio_mix_inputs) > 0
    if has_audio:
        if len(audio_mix_inputs) == 1:
            filter_complex_parts.append(f"{audio_mix_inputs[0]}anull[aout]")
        else:
            joined_inputs = "".join(audio_mix_inputs)
            filter_complex_parts.append(
                # normalize=0: keep VO at full level instead of dividing every input by N;
                # duration=longest + output -t: music shorter than the cut doesn't truncate VO.
                f"{joined_inputs}amix=inputs={len(audio_mix_inputs)}:duration=longest:dropout_transition=2:normalize=0[aout]"
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
            "-t", f"{total_duration:.2f}",
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
                "-t", f"{total_duration:.2f}",
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

    # Maintain final_v1.mp4 and poster_v1.jpg for version 1
    import shutil
    if version <= 1:
        v1_video = job_dir / "final_v1.mp4"
        v1_poster = job_dir / "poster_v1.jpg"
        if not v1_video.exists() and final_video.exists():
            shutil.copyfile(str(final_video), str(v1_video))
        if not v1_poster.exists() and poster_jpg.exists():
            shutil.copyfile(str(poster_jpg), str(v1_poster))
    else:
        # Also mirror latest version to final.mp4 and poster.jpg
        shutil.copyfile(str(final_video), str(job_dir / "final.mp4"))
        if poster_jpg.exists():
            shutil.copyfile(str(poster_jpg), str(job_dir / "poster.jpg"))

    # Cleanup temporary normalization files to conserve disk
    for norm in normalized_clips:
        try:
            norm.unlink(missing_ok=True)
        except Exception:
            pass
    concatenated_video.unlink(missing_ok=True)
    concat_list_file.unlink(missing_ok=True)

    final_url = f"/media/{job.id}/{video_filename}"
    poster_url = f"/media/{job.id}/{poster_filename}"
    srt_url = f"/media/{job.id}/captions.srt"

    return final_url, poster_url, srt_url

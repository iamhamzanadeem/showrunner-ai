#!/usr/bin/env python3
"""
Showrunner AI - Google GenAI Live Smoke Test Script
Executes one minimal call per modality (Planner, Nano Banana, Veo 3.1, Gemini TTS, Lyria)
when credentials are present.
If credentials are not present, exits 0 with an informative message.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.models import BibleStyle, VideoMode
from app.providers.google import (
    GoogleCriticProvider,
    GoogleImageProvider,
    GoogleMusicProvider,
    GooglePlannerProvider,
    GoogleTTSProvider,
    GoogleVideoProvider,
)


def has_google_credentials() -> bool:
    """Checks whether Google credentials (API Key or Vertex Project) are configured."""
    return bool(
        settings.GEMINI_API_KEY
        or (settings.GOOGLE_CLOUD_PROJECT and settings.GOOGLE_GENAI_USE_VERTEXAI)
        or settings.GOOGLE_CLOUD_PROJECT
    )


ALL_STEPS = ["planner", "image", "video", "critic", "tts", "music"]


async def run_live_smoke(steps: list[str]):
    """Runs a minimal smoke test for each selected Google modality.

    Steps can be run one at a time (``--only planner``); later steps reuse artifacts
    from earlier runs in DATA_DIR/smoke (plan JSON, keyframe PNG, clip MP4).
    """
    from app.models import Plan

    smoke_dir = settings.DATA_DIR / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    plan_path = smoke_dir / "smoke_plan.json"
    kf_default = smoke_dir / "smoke_kf_att1_keyframe.png"
    clip_default = smoke_dir / "smoke_clip_att1.mp4"

    print("========================================================================")
    print("  SHOWRUNNER AI - LIVE GOOGLE PROVIDER SMOKE TEST")
    print("========================================================================")
    if settings.GEMINI_API_KEY:
        print("  Mode: Google AI Studio (API key configured)")
    else:
        print(
            f"  Mode: Vertex AI (Project: {settings.GOOGLE_CLOUD_PROJECT}, "
            f"Location: {settings.GOOGLE_CLOUD_LOCATION}, Media: {settings.VERTEX_LOCATION_MEDIA})"
        )
    print(f"  Steps: {', '.join(steps)}")
    print(f"  Output directory: {smoke_dir}")
    print("------------------------------------------------------------------------")

    plan = None
    if "planner" in steps:
        print(f"\n[planner] {settings.SR_MODEL_PLANNER} ...")
        planner = GooglePlannerProvider()
        brief = "A 30-second commercial for Chrono Apex, an ethereal quantum timepiece."
        plan = await planner.generate_plan(brief=brief, mode=VideoMode.ad)
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        print(f"  -> Plan: '{plan.title}' ({len(plan.scenes)} scene(s), {plan.total_shots} shot(s)) -> {plan_path}")
    elif plan_path.is_file():
        plan = Plan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    style = (plan.bible.style if plan else None) or BibleStyle(
        palette=["#0A0F1D", "#00F0FF", "#7000FF"],
        lighting="Volumetric atmospheric rim light",
        lens="35mm anamorphic",
        mood="Futuristic elegance",
    )

    kf_path = str(kf_default) if kf_default.is_file() else None
    if "image" in steps:
        print(f"\n[image] {settings.SR_MODEL_IMAGE} ...")
        image_prov = GoogleImageProvider()
        kf_path = await image_prov.generate_keyframe(
            prompt="Hero commercial packshot of the Chrono Apex timepiece levitating over black obsidian",
            style=style,
            job_dir=smoke_dir,
            shot_id="smoke_kf",
            scene_num=1,
            shot_num=1,
            attempt=1,
        )
        print(f"  -> Keyframe image saved to: {kf_path}")

    clip_path = str(clip_default) if clip_default.is_file() else None
    if "video" in steps:
        print(f"\n[video] {settings.SR_MODEL_VIDEO} (ONE 4s clip) ...")
        video_prov = GoogleVideoProvider()
        clip_path = await video_prov.generate_clip(
            prompt="Slow orbit around the levitating Chrono Apex timepiece, glowing neon accents",
            duration_s=4.0,
            initial_frame=kf_path,
            job_dir=smoke_dir,
            shot_id="smoke_clip",
            scene_num=1,
            shot_num=1,
            attempt=1,
        )
        print(f"  -> Video clip saved to: {clip_path}")

    if "critic" in steps:
        if not plan or not clip_path:
            raise RuntimeError("critic step needs a smoke plan and clip (run planner and video first)")
        print(f"\n[critic] {settings.SR_MODEL_CRITIC} ...")
        critic_prov = GoogleCriticProvider()
        critique = await critic_prov.critique_clip(
            video_path=clip_path,
            shot=plan.scenes[0].shots[0],
            bible=plan.bible,
            attempt=1,
        )
        print(f"  -> Critic: passed={critique.passed} score={critique.score:.2f} reasons={critique.reasons}")

    if "tts" in steps:
        print(f"\n[tts] {settings.SR_MODEL_TTS} (voice {settings.SR_TTS_VOICE}) ...")
        tts_prov = GoogleTTSProvider()
        vo_path = await tts_prov.generate_voiceover(
            text="Time is no longer a constraint. It is your canvas.",
            duration_s=4.0,
            job_dir=smoke_dir,
            shot_id="smoke_vo",
        )
        print(f"  -> Voiceover WAV saved to: {vo_path}")

    if "music" in steps:
        print(f"\n[music] {settings.SR_MODEL_MUSIC} (30s) ...")
        music_prov = GoogleMusicProvider()
        music_path = await music_prov.generate_music(
            prompt="Atmospheric synthwave and ambient strings, subtle ticking rhythm",
            duration_s=30.0,
            job_dir=smoke_dir,
        )
        print(f"  -> Soundtrack saved to: {music_path}")

    print("\n========================================================================")
    print(f"  SMOKE STEPS COMPLETED: {', '.join(steps)}")
    print(f"  Artifacts stored in: {smoke_dir}")
    print("========================================================================")


def main():
    if not has_google_credentials():
        print("========================================================================")
        print("  [SHOWRUNNER AI] Google Provider Smoke Test Check")
        print("========================================================================")
        print("  Status: NO GOOGLE CREDENTIALS DETECTED (Graceful Skip).")
        print()
        print("  To enable live Google GenAI testing, set the following in .env:")
        print("    EITHER (Google AI Studio):")
        print("      GEMINI_API_KEY=\"<your_gemini_api_key>\"")
        print()
        print("    OR (Google Cloud Vertex AI):")
        print("      GOOGLE_CLOUD_PROJECT=\"<your_gcp_project_id>\"")
        print("      GOOGLE_GENAI_USE_VERTEXAI=\"true\"")
        print("      GOOGLE_CLOUD_LOCATION=\"global\"")
        print("      VERTEX_LOCATION_MEDIA=\"us-central1\"")
        print()
        print("  Currently running in MOCK mode (100% deterministic, zero network calls).")
        print("  Skipping network calls and exiting with status 0.")
        print("========================================================================")
        sys.exit(0)

    import argparse

    parser = argparse.ArgumentParser(description="Live Google provider smoke test")
    parser.add_argument(
        "--only",
        action="append",
        choices=ALL_STEPS,
        help="Run only this step (repeatable). Default: all steps (includes ONE 4s Veo clip).",
    )
    args = parser.parse_args()
    steps = [s for s in ALL_STEPS if not args.only or s in args.only]

    try:
        asyncio.run(run_live_smoke(steps))
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"\n[ERROR] Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

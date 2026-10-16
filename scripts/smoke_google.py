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


async def run_live_smoke():
    """Runs a minimal smoke test for each Google modality."""
    smoke_dir = settings.DATA_DIR / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)

    print("========================================================================")
    print("  SHOWRUNNER AI - LIVE GOOGLE PROVIDER SMOKE TEST")
    print("========================================================================")
    if settings.GEMINI_API_KEY:
        print(f"  Mode: Google AI Studio (Key: {settings.GEMINI_API_KEY[:6]}...)")
    else:
        print(
            f"  Mode: Vertex AI (Project: {settings.GOOGLE_CLOUD_PROJECT}, "
            f"Location: {settings.GOOGLE_CLOUD_LOCATION}, Media: {settings.VERTEX_LOCATION_MEDIA})"
        )
    print(f"  Output directory: {smoke_dir}")
    print("------------------------------------------------------------------------")

    # 1. PLANNER
    print("\n[1/5] Testing Planner (gemini-3.8-flash)...")
    planner = GooglePlannerProvider()
    brief = "A 30-second commercial for Chrono Apex, an ethereal quantum timepiece."
    plan = await planner.generate_plan(brief=brief, mode=VideoMode.ad)
    plan_path = smoke_dir / "smoke_plan.json"
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(plan.model_dump_json(indent=2))
    print(f"  -> Plan generated: '{plan.title}' ({len(plan.scenes)} scene(s), {plan.total_shots} shot(s))")
    print(f"  -> Saved plan to: {plan_path}")

    # 2. IMAGE (Nano Banana)
    print("\n[2/5] Testing Image Generator (gemini-3.1-flash-image)...")
    image_prov = GoogleImageProvider()
    style = plan.bible.style or BibleStyle(
        palette=["#0A0F1D", "#00F0FF", "#7000FF"],
        lighting="Volumetric atmospheric rim light",
        lens="35mm anamorphic",
        mood="Futuristic elegance",
    )
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

    # 3. VIDEO (Veo 3.1)
    print("\n[3/5] Testing Video Generator (veo-3.1-fast-generate-preview, 4s clip)...")
    video_prov = GoogleVideoProvider()
    clip_path = await video_prov.generate_clip(
        prompt="Slow 360-degree orbit around the levitating Chrono Apex timepiece, glowing neon accents",
        duration_s=4.0,
        initial_frame=kf_path,
        job_dir=smoke_dir,
        shot_id="smoke_clip",
        scene_num=1,
        shot_num=1,
        attempt=1,
    )
    print(f"  -> Video clip saved to: {clip_path}")

    # 3b. CRITIC
    print("\n[Optional Critic Check] Evaluating smoke clip with Gemini Multimodal Critic...")
    critic_prov = GoogleCriticProvider()
    sample_shot = plan.scenes[0].shots[0]
    critique = await critic_prov.critique_clip(
        video_path=clip_path,
        shot=sample_shot,
        bible=plan.bible,
        attempt=1,
    )
    print(f"  -> Critic result: Passed={critique.passed}, Score={critique.score:.2f}")

    # 4. TTS (Gemini TTS)
    print("\n[4/5] Testing Text-to-Speech (gemini-3.8-flash-tts, Voice: Kore)...")
    tts_prov = GoogleTTSProvider()
    vo_path = await tts_prov.generate_voiceover(
        text="Time is no longer a constraint. It is your canvas.",
        duration_s=4.0,
        job_dir=smoke_dir,
        shot_id="smoke_vo",
    )
    print(f"  -> Voiceover WAV saved to: {vo_path}")

    # 5. MUSIC (Lyria)
    print("\n[5/5] Testing Soundtrack Generation (lyria-3-clip-preview, 30s)...")
    music_prov = GoogleMusicProvider()
    music_path = await music_prov.generate_music(
        prompt="Atmospheric synthwave and ambient strings, subtle ticking rhythm",
        duration_s=30.0,
        job_dir=smoke_dir,
    )
    print(f"  -> Soundtrack track saved to: {music_path}")

    print("\n========================================================================")
    print("  ALL 5 GOOGLE MODALITY SMOKE CALLS COMPLETED SUCCESSFULLY!")
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

    try:
        asyncio.run(run_live_smoke())
    except Exception as exc:
        print(f"\n[ERROR] Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

from typing import Optional
from app.config import Settings, settings as default_settings
from app.providers.base import ProviderBundle
from app.providers.google import (
    GoogleCriticProvider,
    GoogleImageProvider,
    GoogleMusicProvider,
    GooglePlannerProvider,
    GoogleTTSProvider,
    GoogleVideoProvider,
)
from app.providers.mock import (
    MockCriticProvider,
    MockImageProvider,
    MockMusicProvider,
    MockPlannerProvider,
    MockTTSProvider,
    MockVideoProvider,
)


def get_providers(cfg: Optional[Settings] = None) -> ProviderBundle:
    """
    Factory function returning the configured provider bundle.
    Supports global SHOWRUNNER_PROVIDER as well as per-modality overrides
    (SR_PROVIDER_PLANNER, SR_PROVIDER_IMAGE, SR_PROVIDER_VIDEO,
     SR_PROVIDER_CRITIC, SR_PROVIDER_TTS, SR_PROVIDER_MUSIC).
    """
    active_settings = cfg or default_settings

    planner_choice = active_settings.get_modality_provider("planner")
    image_choice = active_settings.get_modality_provider("image")
    video_choice = active_settings.get_modality_provider("video")
    critic_choice = active_settings.get_modality_provider("critic")
    tts_choice = active_settings.get_modality_provider("tts")
    music_choice = active_settings.get_modality_provider("music")

    choices = [
        planner_choice,
        image_choice,
        video_choice,
        critic_choice,
        tts_choice,
        music_choice,
    ]

    if all(c == "google" for c in choices):
        bundle_type = "google"
    elif all(c == "mock" for c in choices):
        bundle_type = "mock"
    else:
        bundle_type = "hybrid"

    # Instantiate planner
    if planner_choice == "google":
        planner = GooglePlannerProvider(
            api_key=active_settings.GEMINI_API_KEY,
            project=active_settings.GOOGLE_CLOUD_PROJECT,
            model=active_settings.SR_MODEL_PLANNER,
        )
    else:
        planner = MockPlannerProvider()

    # Instantiate image provider
    if image_choice == "google":
        image = GoogleImageProvider()
    else:
        image = MockImageProvider()

    # Instantiate video provider
    if video_choice == "google":
        video = GoogleVideoProvider()
    else:
        video = MockVideoProvider()

    # Instantiate critic provider
    if critic_choice == "google":
        critic = GoogleCriticProvider(
            api_key=active_settings.GEMINI_API_KEY,
            project=active_settings.GOOGLE_CLOUD_PROJECT,
            model=active_settings.SR_MODEL_CRITIC,
        )
    else:
        critic = MockCriticProvider()

    # Instantiate TTS provider
    if tts_choice == "google":
        tts = GoogleTTSProvider()
    else:
        tts = MockTTSProvider()

    # Instantiate music provider
    if music_choice == "google":
        music = GoogleMusicProvider()
    else:
        music = MockMusicProvider()

    return ProviderBundle(
        provider_type=bundle_type,
        planner=planner,
        image=image,
        video=video,
        critic=critic,
        tts=tts,
        music=music,
    )

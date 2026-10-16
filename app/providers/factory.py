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
    """Factory function returning the configured provider bundle."""
    active_settings = cfg or default_settings
    provider_name = active_settings.resolved_provider

    if provider_name == "google":
        return ProviderBundle(
            provider_type="google",
            planner=GooglePlannerProvider(
                api_key=active_settings.GEMINI_API_KEY,
                project=active_settings.GOOGLE_CLOUD_PROJECT,
            ),
            image=GoogleImageProvider(),
            video=GoogleVideoProvider(),
            critic=GoogleCriticProvider(),
            tts=GoogleTTSProvider(),
            music=GoogleMusicProvider(),
        )

    # Default to mock
    return ProviderBundle(
        provider_type="mock",
        planner=MockPlannerProvider(),
        image=MockImageProvider(),
        video=MockVideoProvider(),
        critic=MockCriticProvider(),
        tts=MockTTSProvider(),
        music=MockMusicProvider(),
    )

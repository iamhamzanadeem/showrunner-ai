from pathlib import Path
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration for Showrunner AI."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SHOWRUNNER_PROVIDER: Literal["auto", "mock", "google"] = "auto"
    GEMINI_API_KEY: Optional[str] = None
    GOOGLE_CLOUD_PROJECT: Optional[str] = None
    GOOGLE_GENAI_USE_VERTEXAI: bool = False

    DATA_DIR: Path = Path("./data")
    HOST: str = "0.0.0.0"
    PORT: int = 8080

    MAX_RETRIES: int = 2
    MAX_PARALLEL_SCENES: int = 2
    MOCK_DELAY_S: float = 0.3

    # Google GenAI model configurations & defaults
    SR_MODEL_PLANNER: str = "gemini-3.8-flash"
    SR_MODEL_CRITIC: str = "gemini-3.8-flash"
    SR_MODEL_IMAGE: str = "gemini-3.1-flash-image"
    SR_MODEL_VIDEO: str = "veo-3.1-fast-generate-preview"
    SR_MODEL_TTS: str = "gemini-3.8-flash-tts"
    SR_TTS_VOICE: str = "Kore"
    SR_MODEL_MUSIC: str = "lyria-3-clip-preview"
    SR_VIDEO_RESOLUTION: str = "720p"
    SR_ASPECT: str = "16:9"
    SR_VEO_TIMEOUT_S: int = 600
    SR_VEO_USE_REFERENCES: bool = False
    SR_CRITIC_PASS_SCORE: float = 0.7

    # Vertex AI locations
    GOOGLE_CLOUD_LOCATION: str = "global"
    VERTEX_LOCATION_MEDIA: str = "us-central1"
    SR_GCS_OUTPUT_URI: Optional[str] = None

    # Hybrid mode: per-modality overrides
    SR_PROVIDER_PLANNER: Optional[Literal["mock", "google"]] = None
    SR_PROVIDER_IMAGE: Optional[Literal["mock", "google"]] = None
    SR_PROVIDER_VIDEO: Optional[Literal["mock", "google"]] = None
    SR_PROVIDER_CRITIC: Optional[Literal["mock", "google"]] = None
    SR_PROVIDER_TTS: Optional[Literal["mock", "google"]] = None
    SR_PROVIDER_MUSIC: Optional[Literal["mock", "google"]] = None

    @property
    def resolved_provider(self) -> Literal["mock", "google"]:
        """Resolves active provider based on environment and credentials."""
        explicit = self.SHOWRUNNER_PROVIDER.lower()
        if explicit == "mock":
            return "mock"
        if explicit == "google":
            return "google"
        # auto mode
        if self.GEMINI_API_KEY or (self.GOOGLE_CLOUD_PROJECT and self.GOOGLE_GENAI_USE_VERTEXAI) or self.GOOGLE_CLOUD_PROJECT:
            return "google"
        return "mock"

    def get_modality_provider(self, modality: str) -> Literal["mock", "google"]:
        """Returns the provider for a specific modality, checking per-modality override first."""
        attr_name = f"SR_PROVIDER_{modality.upper()}"
        override = getattr(self, attr_name, None)
        if override:
            return override.lower()  # type: ignore
        return self.resolved_provider

    def get_all_modality_providers(self) -> dict[str, str]:
        """Returns map of active provider for every pipeline modality."""
        modalities = ["planner", "image", "video", "critic", "tts", "music"]
        return {m: self.get_modality_provider(m) for m in modalities}

    def get_configured_models(self) -> dict[str, str]:
        """Returns map of configured model IDs for every modality."""
        return {
            "planner": self.SR_MODEL_PLANNER,
            "critic": self.SR_MODEL_CRITIC,
            "image": self.SR_MODEL_IMAGE,
            "video": self.SR_MODEL_VIDEO,
            "tts": self.SR_MODEL_TTS,
            "music": self.SR_MODEL_MUSIC,
        }

    def get_job_dir(self, job_id: str) -> Path:
        """Returns the storage path for a job, creating parent directories if needed."""
        job_dir = self.DATA_DIR / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir


settings = Settings()

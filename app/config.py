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

    def get_job_dir(self, job_id: str) -> Path:
        """Returns the storage path for a job, creating parent directories if needed."""
        job_dir = self.DATA_DIR / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir


settings = Settings()

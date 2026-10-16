from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time
from typing import Optional
from google.genai import types

from app.config import settings
from app.providers.base import BaseVideoProvider
from app.providers.google.client import call_with_retry, get_genai_client

logger = logging.getLogger("showrunner.providers.google.video")


def _snap_veo_duration(duration_s: float) -> int:
    """Snaps duration to strictly 4, 6, or 8 seconds for Veo 3.1."""
    if duration_s <= 5.0:
        return 4
    elif duration_s <= 7.0:
        return 6
    return 8


class GoogleVideoProvider(BaseVideoProvider):
    """
    Google Veo 3.1 image-to-video clip generator with frame chaining.
    Connects to Vertex AI (us-central1) or AI Studio using official Veo signatures.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        project: Optional[str] = None,
        model: Optional[str] = None,
        resolution: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ):
        self.api_key = api_key
        self.project = project
        self.model = model or settings.SR_MODEL_VIDEO
        self.resolution = resolution or settings.SR_VIDEO_RESOLUTION
        self.aspect_ratio = aspect_ratio or settings.SR_ASPECT
        self.timeout_s = timeout_s or settings.SR_VEO_TIMEOUT_S

    async def generate_clip(
        self,
        prompt: str,
        duration_s: float = 8.0,
        initial_frame: Optional[str] = None,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
        scene_num: int = 1,
        shot_num: int = 1,
        attempt: int = 1,
    ) -> str:
        # Veo calls on Vertex AI require us-central1 (or VERTEX_LOCATION_MEDIA)
        client = get_genai_client(
            api_key=self.api_key,
            project=self.project,
            location=settings.VERTEX_LOCATION_MEDIA or "us-central1",
        )

        out_dir = job_dir or settings.DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{shot_id or 'shot'}_att{attempt}.mp4"
        out_path = out_dir / filename

        snapped_duration = _snap_veo_duration(duration_s)

        # Load first frame for image-to-video generation
        image_arg: Optional[types.Image] = None
        if initial_frame:
            frame_p = Path(initial_frame)
            if not frame_p.is_file():
                # Try relative to DATA_DIR
                alt_p = settings.DATA_DIR / initial_frame.lstrip("/")
                if alt_p.is_file():
                    frame_p = alt_p

            if frame_p.is_file():
                with open(frame_p, "rb") as f:
                    img_bytes = f.read()
                mime = "image/png" if frame_p.suffix.lower() == ".png" else "image/jpeg"
                image_arg = types.Image(image_bytes=img_bytes, mime_type=mime)

        config_kwargs: dict = {
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_seconds": snapped_duration,
            "person_generation": "allow_adult",
            "negative_prompt": "text artifacts, watermarks, bad anatomy, deformed limbs, blurry, low resolution, jitter, flickering",
        }

        # Optional GCS output URI if configured
        if settings.SR_GCS_OUTPUT_URI:
            config_kwargs["output_gcs_uri"] = settings.SR_GCS_OUTPUT_URI

        config = types.GenerateVideosConfig(**config_kwargs)

        # Launch video generation operation
        def _launch_generate():
            kwargs: dict = {
                "model": self.model,
                "prompt": prompt,
                "config": config,
            }
            if image_arg is not None:
                kwargs["image"] = image_arg
            return client.models.generate_videos(**kwargs)

        op = await call_with_retry(_launch_generate)

        # Poll operation asynchronously until completion or timeout
        start_time = time.time()
        poll_interval = 5.0  # seconds between poll checks

        while not op.done:
            elapsed = time.time() - start_time
            if elapsed > self.timeout_s:
                raise TimeoutError(
                    f"Veo video generation timed out after {int(elapsed)}s for shot {shot_id} (timeout={self.timeout_s}s)."
                )
            await asyncio.sleep(poll_interval)
            op = await call_with_retry(client.operations.get, op)

        # Check for errors in completed operation
        if getattr(op, "error", None):
            raise RuntimeError(f"Veo video generation failed for shot {shot_id}: {op.error}")

        response = getattr(op, "response", None)
        if not response or not getattr(response, "generated_videos", None):
            raise RuntimeError(
                f"Veo operation completed but returned no generated videos for shot {shot_id}."
            )

        gen_video = response.generated_videos[0]
        video_obj = getattr(gen_video, "video", None) or gen_video

        # Save video to target destination
        saved = await self._save_video_file(client, video_obj, out_path)
        if not saved or not out_path.is_file() or out_path.stat().st_size == 0:
            raise RuntimeError(f"Failed to download or persist Veo video clip to {out_path}.")

        return str(out_path)

    async def _save_video_file(self, client: Any, video_obj: Any, out_path: Path) -> bool:
        """Saves generated video object from bytes, GCS URI, or client download."""
        # 1. Direct video_bytes if present
        video_bytes = getattr(video_obj, "video_bytes", None)
        if video_bytes:
            with open(out_path, "wb") as f:
                f.write(video_bytes)
            return True

        # 2. Check for GCS URI (Vertex AI mode)
        uri = getattr(video_obj, "uri", None)
        if uri and isinstance(uri, str) and uri.startswith("gs://"):
            try:
                # Guarded import of google.cloud.storage
                from google.cloud import storage  # type: ignore

                parts = uri[5:].split("/", 1)
                bucket_name = parts[0]
                blob_name = parts[1] if len(parts) > 1 else ""

                def _download_gcs():
                    storage_client = storage.Client()
                    bucket = storage_client.bucket(bucket_name)
                    blob = bucket.blob(blob_name)
                    blob.download_to_filename(str(out_path))

                await asyncio.to_thread(_download_gcs)
                return True
            except ImportError:
                logger.warning(
                    "google-cloud-storage is not installed; attempting client.files.download for GCS URI %s",
                    uri,
                )

        # 3. Try client.files.download
        if hasattr(client, "files") and hasattr(client.files, "download"):
            try:
                def _download_file():
                    return client.files.download(file=video_obj, destination=str(out_path))

                dl_bytes = await call_with_retry(_download_file)
                if out_path.is_file() and out_path.stat().st_size > 0:
                    return True
                if dl_bytes and isinstance(dl_bytes, (bytes, bytearray)):
                    with open(out_path, "wb") as f:
                        f.write(dl_bytes)
                    return True
            except Exception as e:
                logger.debug("client.files.download failed: %s", e)

        # 4. Try video_obj.save() method
        if hasattr(video_obj, "save"):
            try:
                await asyncio.to_thread(video_obj.save, str(out_path))
                return True
            except Exception as e:
                logger.debug("video_obj.save failed: %s", e)

        return False

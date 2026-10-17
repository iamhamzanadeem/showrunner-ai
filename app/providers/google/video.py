from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import time
from typing import Any, Callable, List, Optional
from google.genai import types

from app.config import settings
from app.providers.base import BaseVideoProvider
from app.providers.google.client import call_with_retry, get_genai_client
from app.providers.google.image import _resolve_image_path, image_mime_type

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
        reference_images: Optional[List[str]] = None,
        on_operation_started: Optional[Callable[[str], None]] = None,
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
            frame_p = _resolve_image_path(initial_frame) or Path(initial_frame)
            if not frame_p.is_file():
                # Try relative to DATA_DIR
                alt_p = settings.DATA_DIR / initial_frame.lstrip("/")
                if alt_p.is_file():
                    frame_p = alt_p

            if frame_p.is_file():
                with open(frame_p, "rb") as f:
                    img_bytes = f.read()
                mime = image_mime_type(frame_p)
                image_arg = types.Image(image_bytes=img_bytes, mime_type=mime)

        # Veo 3.1 reference images: ONLY when settings.SR_VEO_USE_REFERENCES is true,
        # reference_images is non-empty, and there is NO initial image
        # (Veo does not allow initial image + reference_images together).
        # Note: Since step (1) makes every shot have an initial keyframe,
        # reference images effectively only apply if no keyframe / initial image exists.
        ref_image_configs: list[types.VideoGenerationReferenceImage] = []
        if settings.SR_VEO_USE_REFERENCES and reference_images and image_arg is None:
            for ref_str in reference_images:
                if len(ref_image_configs) >= 3:
                    break
                ref_p = _resolve_image_path(ref_str)
                if not ref_p:
                    p_candidate = Path(ref_str)
                    if p_candidate.is_file():
                        ref_p = p_candidate
                if ref_p and ref_p.is_file():
                    with open(ref_p, "rb") as f:
                        ref_bytes = f.read()
                    mime = image_mime_type(ref_p)
                    ref_image_configs.append(
                        types.VideoGenerationReferenceImage(
                            image=types.Image(image_bytes=ref_bytes, mime_type=mime),
                            reference_type="asset",
                        )
                    )

        config_kwargs: dict = {
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_seconds": snapped_duration,
            "negative_prompt": "text artifacts, watermarks, bad anatomy, deformed limbs, blurry, low resolution, jitter, flickering",
        }

        # Veo 3.1: text-to-video only accepts "allow_all" (omit person_generation on Vertex AI);
        # image-to-video and reference-images requests require "allow_adult".
        if image_arg is not None:
            config_kwargs["person_generation"] = "allow_adult"
        elif ref_image_configs:
            config_kwargs["reference_images"] = ref_image_configs
            config_kwargs["duration_seconds"] = 8
            config_kwargs["person_generation"] = "allow_adult"

        is_vertex = getattr(client, "vertexai", False) is True
        if is_vertex:
            # Vertex requires generateAudio for Veo 3.x; the Gemini API rejects the field.
            config_kwargs["generate_audio"] = bool(settings.SR_VEO_GENERATE_AUDIO)

        # Optional GCS output URI if configured
        if settings.SR_GCS_OUTPUT_URI:
            config_kwargs["output_gcs_uri"] = settings.SR_GCS_OUTPUT_URI

        config = types.GenerateVideosConfig(**config_kwargs)

        # Launch video generation operation
        model_id = self.model
        if not is_vertex and model_id.endswith("-generate-001"):
            # AI Studio (Gemini API) only serves the -preview Veo 3.1 IDs.
            model_id = model_id[: -len("-001")] + "-preview"

        def _launch_generate():
            kwargs: dict = {
                "model": model_id,
                "prompt": prompt,
                "config": config,
            }
            if image_arg is not None:
                kwargs["image"] = image_arg
            return client.models.generate_videos(**kwargs)

        op = await call_with_retry(_launch_generate, retry=False)

        if on_operation_started is not None:
            op_name = getattr(op, "name", None)
            if op_name:
                try:
                    res = on_operation_started(op_name)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.warning("on_operation_started callback failed: %s", e)

        return await self._finish_operation(client, op, out_path, shot_id)

    async def _finish_operation(self, client: Any, op: Any, out_path: Path, shot_id: Optional[str]) -> str:
        """Polls a Veo operation until done, then downloads its first video to out_path."""
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

    async def resume_clip(
        self,
        operation_name: str,
        job_dir: Optional[Path] = None,
        shot_id: Optional[str] = None,
        attempt: int = 1,
    ) -> Optional[str]:
        """Re-attaches to a previously submitted Veo operation (e.g. after a server restart).

        Done -> downloads and returns the clip; still running -> keeps polling it.
        Returns None (caller should submit a new generation) when the operation is
        missing/expired, failed, or produced no video. Never submits a new generation.
        """
        if not operation_name or operation_name.startswith("mock-op-"):
            return None
        client = get_genai_client(
            api_key=self.api_key,
            project=self.project,
            location=settings.VERTEX_LOCATION_MEDIA or "us-central1",
        )
        out_dir = job_dir or settings.DATA_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{shot_id or 'shot'}_att{attempt}.mp4"
        try:
            op = await call_with_retry(
                client.operations.get, types.GenerateVideosOperation(name=operation_name)
            )
        except Exception as exc:  # 404 / expired / malformed name
            logger.warning("Saved Veo operation %s for %s is not retrievable: %s", operation_name, shot_id, exc)
            return None
        try:
            return await self._finish_operation(client, op, out_path, shot_id)
        except TimeoutError:
            raise
        except Exception as exc:
            logger.warning("Saved Veo operation %s for %s unusable: %s", operation_name, shot_id, exc)
            return None

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

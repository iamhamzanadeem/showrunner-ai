from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from google.genai import errors
import pytest

from app.providers.google.client import call_with_retry
from app.providers.google.video import GoogleVideoProvider
from app.providers.mock import MockVideoProvider


# -----------------------------------------------------------------------------
# 1. RETRY POLICY TESTS FOR call_with_retry
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_error_400_called_once_and_raised():
    """ClientError 400 is non-transient: must be called once and raised immediately."""
    err = errors.ClientError(
        400,
        {"error": {"code": 400, "message": "bad request", "status": "INVALID_ARGUMENT"}},
    )
    mock_func = MagicMock(side_effect=err)

    with patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(errors.ClientError) as exc_info:
            await call_with_retry(mock_func, max_retries=3)

        assert exc_info.value.code == 400
        assert mock_func.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_server_error_503_retried_then_succeeds():
    """ServerError 503 is transient: should be retried and succeed on second attempt."""
    err = errors.ServerError(
        503,
        {"error": {"code": 503, "message": "service unavailable", "status": "UNAVAILABLE"}},
    )
    mock_func = MagicMock(side_effect=[err, "success_result"])

    with patch("asyncio.sleep") as mock_sleep:
        res = await call_with_retry(mock_func, max_retries=3)

        assert res == "success_result"
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1
        # Default base_delay = 2.0s, attempt 0: delay in [0.5 * 2.0, 1.0 * 2.0] = [1.0, 2.0]
        delay = mock_sleep.call_args[0][0]
        assert 1.0 <= delay <= 2.0


@pytest.mark.asyncio
async def test_client_error_429_retried_with_longer_base():
    """ClientError 429 / RESOURCE_EXHAUSTED is transient: retried with longer base (5s)."""
    err = errors.ClientError(
        429,
        {"error": {"code": 429, "message": "rate limit", "status": "RESOURCE_EXHAUSTED"}},
    )
    mock_func = MagicMock(side_effect=[err, "success_after_quota"])

    with patch("asyncio.sleep") as mock_sleep:
        res = await call_with_retry(mock_func, max_retries=3)

        assert res == "success_after_quota"
        assert mock_func.call_count == 2
        assert mock_sleep.call_count == 1
        # Longer base for 429 = 5.0s, attempt 0: delay in [0.5 * 5.0, 1.0 * 5.0] = [2.5, 5.0]
        delay = mock_sleep.call_args[0][0]
        assert 2.5 <= delay <= 5.0


@pytest.mark.asyncio
async def test_generic_value_error_not_retried():
    """Generic exceptions like ValueError must not be retried."""
    mock_func = MagicMock(side_effect=ValueError("bad parameter"))

    with patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="bad parameter"):
            await call_with_retry(mock_func, max_retries=3)

        assert mock_func.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_generic_timeout_error_not_retried():
    """Generic exceptions like TimeoutError must not be retried."""
    mock_func = MagicMock(side_effect=TimeoutError("operation timed out"))

    with patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(TimeoutError, match="operation timed out"):
            await call_with_retry(mock_func, max_retries=3)

        assert mock_func.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_retry_false_called_once_even_for_503():
    """When retry=False, call_with_retry executes exactly once even for transient 503 errors."""
    err = errors.ServerError(
        503,
        {"error": {"code": 503, "message": "service unavailable", "status": "UNAVAILABLE"}},
    )
    mock_func = MagicMock(side_effect=err)

    with patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(errors.ServerError):
            await call_with_retry(mock_func, max_retries=3, retry=False)

        assert mock_func.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_other_client_errors_not_retried():
    """Errors 401, 403, 404 should never be retried."""
    for code, status in [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED"), (404, "NOT_FOUND")]:
        err = errors.ClientError(
            code,
            {"error": {"code": code, "message": "error", "status": status}},
        )
        mock_func = MagicMock(side_effect=err)

        with patch("asyncio.sleep") as mock_sleep:
            with pytest.raises(errors.ClientError):
                await call_with_retry(mock_func, max_retries=3)

            assert mock_func.call_count == 1
            mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_other_server_errors_500_502_504_retried():
    """Server errors 500, 502, 504 are transient and must be retried."""
    for code, status in [(500, "INTERNAL"), (502, "BAD_GATEWAY"), (504, "DEADLINE_EXCEEDED")]:
        err = errors.ServerError(
            code,
            {"error": {"code": code, "message": "server error", "status": status}},
        )
        mock_func = MagicMock(side_effect=[err, f"ok_{code}"])

        with patch("asyncio.sleep") as mock_sleep:
            res = await call_with_retry(mock_func, max_retries=2)
            assert res == f"ok_{code}"
            assert mock_func.call_count == 2
            assert mock_sleep.call_count == 1


# -----------------------------------------------------------------------------
# 2. VEO VIDEO PROVIDER TESTS: retry=False & on_operation_started
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_veo_launch_raising_503_called_exactly_once(tmp_path: Path):
    """Veo launch must NOT be retried (retry=False) even when 503 is raised."""
    mock_client = MagicMock()
    err = errors.ServerError(
        503,
        {"error": {"code": 503, "message": "Veo launch service unavailable", "status": "UNAVAILABLE"}},
    )
    mock_client.models.generate_videos.side_effect = err

    with patch("app.providers.google.video.get_genai_client", return_value=mock_client), \
         patch("asyncio.sleep") as mock_sleep:
        provider = GoogleVideoProvider(api_key="fake-key")

        with pytest.raises(errors.ServerError):
            await provider.generate_clip(
                prompt="Futuristic city at dusk",
                duration_s=4.0,
                job_dir=tmp_path,
                shot_id="shot_503",
            )

        assert mock_client.models.generate_videos.call_count == 1
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_on_operation_started_receives_op_name_google_provider(tmp_path: Path):
    """GoogleVideoProvider calls on_operation_started with op.name after launch."""
    mock_op = MagicMock()
    mock_op.name = "operations/veo-clip-operation-9988"
    mock_op.done = True
    mock_op.error = None
    mock_vid = MagicMock()
    mock_vid.video.video_bytes = b"\x00\x00\x00\x18ftypmp42video_content"
    mock_op.response.generated_videos = [mock_vid]

    mock_client = MagicMock()
    mock_client.models.generate_videos.return_value = mock_op

    on_started = MagicMock()

    with patch("app.providers.google.video.get_genai_client", return_value=mock_client), \
         patch("asyncio.sleep"):
        provider = GoogleVideoProvider(api_key="fake-key")
        out_clip = await provider.generate_clip(
            prompt="Sleek sports car accelerating",
            duration_s=4.0,
            job_dir=tmp_path,
            shot_id="shot_cb_test",
            on_operation_started=on_started,
        )

        assert Path(out_clip).is_file()
        on_started.assert_called_once_with("operations/veo-clip-operation-9988")


@pytest.mark.asyncio
async def test_on_operation_started_error_ignored_and_logged(tmp_path: Path):
    """If on_operation_started callback raises an exception, generate_clip succeeds anyway."""
    mock_op = MagicMock()
    mock_op.name = "operations/veo-clip-op-error-test"
    mock_op.done = True
    mock_op.error = None
    mock_vid = MagicMock()
    mock_vid.video.video_bytes = b"\x00\x00\x00\x18ftypmp42video_content"
    mock_op.response.generated_videos = [mock_vid]

    mock_client = MagicMock()
    mock_client.models.generate_videos.return_value = mock_op

    def faulty_callback(op_name: str) -> None:
        raise RuntimeError("Callback crashed!")

    with patch("app.providers.google.video.get_genai_client", return_value=mock_client), \
         patch("asyncio.sleep"):
        provider = GoogleVideoProvider(api_key="fake-key")
        out_clip = await provider.generate_clip(
            prompt="Drone aerial view",
            duration_s=4.0,
            job_dir=tmp_path,
            shot_id="shot_error_ignore",
            on_operation_started=faulty_callback,
        )

        assert Path(out_clip).is_file()


@pytest.mark.asyncio
async def test_on_operation_started_mock_provider(tmp_path: Path):
    """MockVideoProvider calls on_operation_started with mock-op-{shot_id}-{attempt}."""
    on_started = MagicMock()
    provider = MockVideoProvider()

    with patch("asyncio.sleep"):
        out_clip = await provider.generate_clip(
            prompt="Hero character walking",
            duration_s=4.0,
            job_dir=tmp_path,
            shot_id="shot_mock_cb",
            attempt=3,
            on_operation_started=on_started,
        )

        assert Path(out_clip).is_file()
        on_started.assert_called_once_with("mock-op-shot_mock_cb-3")

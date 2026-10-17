"""Lightweight demo access control: shared token, daily job cap, safe uploads, id validation."""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import re
import threading
from typing import Optional
import uuid

from fastapi import HTTPException, Request, status

from app.config import settings

TOKEN_HEADER = "X-Demo-Token"
TOKEN_QUERY_PARAM = "token"

JOB_ID_RE = re.compile(r"^job_[0-9a-f]{10}$")

ALLOWED_IMAGE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

_usage_lock = threading.Lock()


def auth_required() -> bool:
    return bool(settings.DEMO_TOKEN)


def require_demo_token(request: Request) -> None:
    """FastAPI dependency: enforces DEMO_TOKEN (header or ?token=) when configured."""
    expected = settings.DEMO_TOKEN
    if not expected:
        return
    supplied = request.headers.get(TOKEN_HEADER) or request.query_params.get(TOKEN_QUERY_PARAM) or ""
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Demo access token required. Provide it via the X-Demo-Token header.",
        )


def is_valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.match(job_id or ""))


def _usage_file() -> Path:
    return settings.DATA_DIR / "usage.json"


def consume_daily_quota() -> None:
    """Increments today's (UTC) job counter; raises 429 if MAX_JOBS_PER_DAY is exceeded."""
    cap = settings.MAX_JOBS_PER_DAY
    if not cap or cap <= 0:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _usage_lock:
        path = _usage_file()
        data: dict = {}
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        count = int(data.get(today, 0))
        if count >= cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily demo limit reached ({cap} productions/day). Please try again tomorrow.",
            )
        data = {today: count + 1}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)


def safe_upload_name(original_name: Optional[str], content_type: Optional[str] = None) -> str:
    """Returns a server-generated '<uuid>.<ext>' name, or raises 415 for non-allowlisted image types."""
    ext = Path(original_name or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Reference images must be PNG, JPG or WEBP.",
        )
    if ext == ".jpeg":
        ext = ".jpg"
    return f"{uuid.uuid4().hex}{ext}"


def sniff_image_ok(data: bytes, ext: str) -> bool:
    """Basic magic-byte check so non-images can't be stored with an image extension."""
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False

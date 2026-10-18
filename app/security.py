"""Lightweight demo access control: shared token, daily job cap, safe uploads, id validation."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
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

JOB_ID_RE = re.compile(r"^job_[A-Za-z0-9_-]{1,64}$")

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


def client_ip(request: Optional[Request]) -> str:
    """Best-effort client IP. On Cloud Run the Google front end appends the real client
    address as the LAST X-Forwarded-For entry (earlier entries are client-controlled)."""
    if request is None:
        return "unknown"
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _ip_key(ip: str) -> str:
    # Never store raw IPs: a salted hash is enough for per-day counting.
    salt = (settings.GOOGLE_CLOUD_PROJECT or "showrunner").encode("utf-8")
    return hashlib.sha256(salt + ip.encode("utf-8")).hexdigest()[:16]


def consume_daily_quota(request: Optional[Request] = None) -> None:
    """Counts one generation (new job, refinement or resume) for today (UTC).

    Raises 429 with a friendly message when either the global MAX_JOBS_PER_DAY cap or the
    per-client MAX_JOBS_PER_IP_PER_DAY cap is reached. Counters live in Firestore
    usage/{day} (SR_JOB_STORE=firestore) or DATA_DIR/usage.json.
    """
    cap = settings.MAX_JOBS_PER_DAY or 0
    ip_cap = settings.MAX_JOBS_PER_IP_PER_DAY or 0
    if cap <= 0 and ip_cap <= 0:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = _ip_key(client_ip(request))

    def decide(total: int, mine: int) -> None:
        if cap > 0 and total >= cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"The public demo has reached today's limit of {cap} AI productions. "
                    "Please come back tomorrow (UTC) - meanwhile, watch the sample films in the gallery."
                ),
            )
        if ip_cap > 0 and mine >= ip_cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"You've used all {ip_cap} of your free AI productions for today. "
                    "Please come back tomorrow (UTC) - the sample films in the gallery are always available."
                ),
            )

    from app.job_store import get_store

    store = get_store()
    if hasattr(store, "consume_usage"):
        # Firestore: usage/{day} checked and incremented in one transaction (atomic across
        # Cloud Run instances).
        store.consume_usage(today, key, decide)
        return

    with _usage_lock:
        path = _usage_file()
        data: dict = {}
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        day = data.get(today)
        if not isinstance(day, dict):  # legacy format: {"YYYY-MM-DD": count}
            day = {"total": int(day or 0), "ips": {}}
        total = int(day.get("total", 0))
        ips = day.get("ips") or {}
        mine = int(ips.get(key, 0))
        decide(total, mine)
        ips[key] = mine + 1
        data = {today: {"total": total + 1, "ips": ips}}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".json.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)


def sample_job_ids() -> set[str]:
    return {j.strip() for j in re.split(r"[,;]", settings.SAMPLE_JOB_IDS or "") if j.strip()}


def ensure_not_sample(job_id: str) -> None:
    """Showcase samples are read-only so nobody can spend money re-cutting them."""
    if job_id in sample_job_ids():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This is a read-only sample production. Start your own production to direct edits.",
        )


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

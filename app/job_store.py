"""Job state persistence: local/GCS-mounted files or Cloud Firestore.

SR_JOB_STORE=file (default, used locally and in tests)
    DATA_DIR/jobs/<id>/job.json, written atomically (tmp file + os.replace).

SR_JOB_STORE=firestore (Cloud Run)
    Cloud Firestore is the source of truth for job state and the job list:

    jobs/{job_id}                 summary fields for the sidebar/gallery (title, status, mode,
                                  logline, duration, counts, critic stats, versions...) plus
                                  ``state``: the full Job with every shot's take history removed
    jobs/{job_id}/takes/{shot_id} that shot's takes (prompt, keyframe, clip, critic verdict)

    Takes live in a subcollection so the job document stays far below Firestore's 1 MiB limit
    however many reshoots happen. The job document and every changed takes document are
    written in ONE batch, so readers never see a half-updated job. job.json is still written
    next to the media in Cloud Storage as a mirror/backup, and is read only when a job has no
    Firestore document (e.g. not yet backfilled). Media files always stay in Cloud Storage.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional
import uuid

from app.config import settings
from app.models import Job

logger = logging.getLogger("showrunner.job_store")

SUMMARY_FIELDS = [
    "id", "brief", "mode", "status", "created_at", "updated_at", "title", "logline",
    "poster_url", "final_video_url", "duration_s", "scene_count", "shot_count", "take_count",
]


# --------------------------------------------------------------------------- file store

def _job_file(job_id: str) -> Path:
    return settings.DATA_DIR / "jobs" / job_id / "job.json"


def write_job_file(job: Job) -> None:
    """Atomic write: a crash mid-write must never leave a truncated job.json behind."""
    job_dir = settings.get_job_dir(job.id)
    job_file = job_dir / "job.json"
    tmp_file = job_dir / f".job.json.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(job.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, job_file)
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass


def read_job_file(job_id: str) -> Optional[Job]:
    path = _job_file(job_id)
    if not path.exists():
        return None
    return Job.model_validate(json.loads(path.read_text(encoding="utf-8")))


def iter_job_files() -> Iterable[Job]:
    jobs_dir = settings.DATA_DIR / "jobs"
    if not jobs_dir.exists():
        return
    for job_json in jobs_dir.glob("*/job.json"):
        try:
            yield Job.model_validate(json.loads(job_json.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning("Skipping unreadable job file %s: %s", job_json, exc)


class FileJobStore:
    kind = "file"

    def save(self, job: Job) -> None:
        write_job_file(job)

    def load(self, job_id: str) -> Optional[Job]:
        return read_job_file(job_id)

    def list_jobs(self) -> List[Job]:
        return list(iter_job_files())


# --------------------------------------------------------------------------- firestore store

def job_summary_fields(job: Job) -> Dict[str, Any]:
    plan = job.plan
    shots = [s for sc in plan.scenes for s in sc.shots] if plan else []
    scores = [s.best_critique.score for s in shots if s.best_critique]
    versions = getattr(job, "versions", None) or []
    return {
        "id": job.id,
        "brief": job.brief,
        "mode": job.mode.value,
        "status": job.status.value,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "title": plan.title if plan else None,
        "logline": plan.logline if plan else None,
        "poster_url": job.poster_url,
        "final_video_url": job.final_video_url,
        "duration_s": sum(s.duration_s for s in shots) if shots else None,
        "scene_count": len(plan.scenes) if plan else 0,
        "shot_count": len(shots),
        "take_count": sum(len(s.attempts) for s in shots),
        "reshoot_count": sum(max(0, len(s.attempts) - 1) for s in shots),
        "avg_critic_score": round(sum(scores) / len(scores), 3) if scores else None,
        "version_count": len(versions),
    }


def split_job(job: Job) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Returns (job state without takes, {shot_id: takes document})."""
    state = job.model_dump(mode="json")
    takes: Dict[str, Dict[str, Any]] = {}
    for scene in (state.get("plan") or {}).get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            attempts = shot.pop("attempts", []) or []
            takes[shot["id"]] = {
                "shot_id": shot["id"],
                "scene_id": shot.get("scene_id"),
                "attempt_count": len(attempts),
                "attempts": attempts,
            }
    return state, takes


def merge_job(state: Dict[str, Any], takes: Dict[str, Dict[str, Any]]) -> Job:
    for scene in (state.get("plan") or {}).get("scenes", []) or []:
        for shot in scene.get("shots", []) or []:
            shot["attempts"] = (takes.get(shot["id"]) or {}).get("attempts", []) or []
    return Job.model_validate(state)


def summary_job(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: doc.get(k) for k in SUMMARY_FIELDS}


def _digest(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class FirestoreJobStore:
    kind = "firestore"

    def __init__(
        self,
        client: Any = None,
        jobs_collection: Optional[str] = None,
        usage_collection: Optional[str] = None,
        transactional: Optional[Callable] = None,
        mirror_files: bool = True,
    ) -> None:
        if client is None:
            from google.cloud import firestore  # lazy: only needed in firestore mode

            client = firestore.Client(
                project=settings.GOOGLE_CLOUD_PROJECT or None,
                database=settings.SR_FIRESTORE_DATABASE or "(default)",
            )
            transactional = transactional or firestore.transactional
        self.client = client
        self.jobs = client.collection(jobs_collection or settings.SR_FIRESTORE_JOBS_COLLECTION)
        self.usage_collection = usage_collection or settings.SR_FIRESTORE_USAGE_COLLECTION
        self._transactional = transactional or (lambda fn: fn)
        self.mirror_files = mirror_files
        self._takes_digest: Dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    # -- jobs
    def save(self, job: Job) -> None:
        if self.mirror_files:
            try:
                write_job_file(job)
            except Exception as exc:  # the mirror is best effort; Firestore is the truth
                logger.warning("job.json mirror write failed for %s: %s", job.id, exc)
        state, takes = split_job(job)
        doc = job_summary_fields(job)
        doc["state"] = state
        doc["take_shot_ids"] = list(takes.keys())
        doc["schema"] = 1
        ref = self.jobs.document(job.id)
        with self._lock:
            batch = self.client.batch()
            changed: Dict[tuple[str, str], str] = {}
            for shot_id, tdoc in takes.items():
                key = (job.id, shot_id)
                digest = _digest(tdoc)
                if self._takes_digest.get(key) != digest:
                    tdoc = dict(tdoc, updated_at=job.updated_at)
                    batch.set(ref.collection("takes").document(shot_id), tdoc)
                    changed[key] = digest
            batch.set(ref, doc)
            batch.commit()  # one atomic write: job doc + changed takes docs
            self._takes_digest.update(changed)

    def load(self, job_id: str) -> Optional[Job]:
        snap = self.jobs.document(job_id).get()
        if not snap.exists:
            return read_job_file(job_id) if self.mirror_files else None
        doc = snap.to_dict() or {}
        takes = {t.id: (t.to_dict() or {}) for t in self.jobs.document(job_id).collection("takes").stream()}
        return merge_job(dict(doc.get("state") or {}), takes)

    def list_summaries(self) -> List[Dict[str, Any]]:
        query = self.jobs
        if hasattr(query, "select"):
            query = query.select(SUMMARY_FIELDS)
        return [summary_job(s.to_dict() or {}) for s in query.stream()]

    def list_jobs(self) -> List[Job]:
        out: List[Job] = []
        for summary in self.list_summaries():
            try:
                job = self.load(summary["id"])
            except Exception as exc:
                logger.warning("Skipping unreadable Firestore job %s: %s", summary.get("id"), exc)
                continue
            if job:
                out.append(job)
        return out

    # -- usage counters
    def consume_usage(self, day: str, ip_key: str, decide: Callable[[int, int], None]) -> None:
        """Atomically checks and increments usage/{day} in a Firestore transaction.

        ``decide(total, mine)`` raises (HTTP 429) to refuse; otherwise both counters go up by one.
        """
        ref = self.client.collection(self.usage_collection).document(day)

        @self._transactional
        def _txn(transaction: Any) -> None:
            snap = ref.get(transaction=transaction)
            data = (snap.to_dict() or {}) if snap.exists else {}
            total = int(data.get("total", 0))
            ips = dict(data.get("ips") or {})
            mine = int(ips.get(ip_key, 0))
            decide(total, mine)
            ips[ip_key] = mine + 1
            transaction.set(ref, {"day": day, "total": total + 1, "ips": ips})

        _txn(self.client.transaction())


# --------------------------------------------------------------------------- selection

_store: Any = None
_store_lock = threading.Lock()


def get_store() -> Any:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = FirestoreJobStore() if settings.SR_JOB_STORE == "firestore" else FileJobStore()
                logger.info("Job store: %s", _store.kind)
    return _store


def set_store(store: Any) -> None:
    """Overrides the active store (tests)."""
    global _store
    _store = store

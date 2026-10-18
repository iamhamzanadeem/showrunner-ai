"""Backfill job.json files into Firestore (jobs/{id} + jobs/{id}/takes/{shot_id}).

Usage:
  gcloud storage cp 'gs://<bucket>/jobs/*/job.json' ...   # or use a local data/jobs dir
  GOOGLE_CLOUD_PROJECT=<project> python scripts/backfill_firestore.py <jobs_dir> [job_id ...]

Reads <jobs_dir>/<job_id>/job.json and writes it through the same FirestoreJobStore the app
uses (mirror files disabled, so nothing local is modified). Uses Application Default Credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.job_store import FirestoreJobStore  # noqa: E402
from app.models import Job  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    wanted = set(sys.argv[2:])
    store = FirestoreJobStore(mirror_files=False)
    n = 0
    for path in sorted(root.glob("*/job.json")):
        job = Job.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if wanted and job.id not in wanted:
            continue
        store.save(job)  # keeps the original updated_at
        shots = sum(len(sc.shots) for sc in job.plan.scenes) if job.plan else 0
        print(f"backfilled {job.id}  status={job.status.value}  shots={shots}")
        n += 1
    print(f"done: {n} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

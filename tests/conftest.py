"""Test session guard: never let a developer's local .env (real Google credentials)
turn the offline test-suite into billable API calls."""

import os

os.environ["SHOWRUNNER_PROVIDER"] = "mock"
for _modality in ("PLANNER", "IMAGE", "VIDEO", "CRITIC", "TTS", "MUSIC"):
    os.environ.pop(f"SR_PROVIDER_{_modality}", None)
# Tests never talk to Cloud Firestore; the Firestore store is tested with an in-memory fake.
os.environ["SR_JOB_STORE"] = "file"

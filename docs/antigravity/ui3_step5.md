# UI v3 — step 5 (reviewer corrections, final polish)
Context: /workspace/agy_specs/ui3_context.md (same constraints: only static/ files; `git diff app/` must stay empty; no paid calls; never pkill -f/killall; mock server on 8092 via /workspace/agy_specs/run_mock_server.sh).

Reviewer found in screenshots (1440x900 and 1280x800):
1. TIMELINE RULER MISALIGNED: in the final-cut timeline the ruler tick labels do not line up with the clips. For the 30s AURA ad (`/?job=job_aedcb881dc&cut=1` at 1280x800) the "0:30" label sits around 75% across while the track (4 clips) fills 100%. The ruler, the playhead, click-to-seek and the clip blocks MUST all use the SAME px-per-second scale and the same left origin (including any padding). Fix so a tick at t lands exactly at the left edge of the clip starting at t.
2. FIT-TO-WIDTH not applied for a short mock ad: a 32s mock job in the panel (e.g. a completed mock job in /tmp/mockdata) overflows horizontally and clips scene 3. Clips for jobs of ~30-45s must fit the available width without horizontal scroll (recompute on resize/panel open; only the 150s film may use the min px/s and scroll).
3. The "0:00" label on the left is clipped by the container edge: give the ruler a small left inset (the same inset the track uses) so it is fully visible.

Verify: take screenshots with /tmp/pwenv/bin/python /workspace/agy_specs/screenshot.py for job_aedcb881dc&cut=1 at 1280x800 and job_eb80955254&cut=1 at 1440x900 and check the alignment visually; node --check static/app.js; run `.venv/bin/python -m pytest -q -k "not slow"` or the quick tests. Report what you changed.

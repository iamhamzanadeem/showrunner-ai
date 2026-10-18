# UI v3 — step 6 (requested by Hamza): simpler start state, no sample badges
Context: /workspace/agy_specs/ui3_context.md (same constraints: edit only static/ files; `git diff app/` must stay empty; no paid calls; never pkill -f/killall; free mock server on http://127.0.0.1:8092 via /workspace/agy_specs/run_mock_server.sh). Note: the backend now also has a Firestore job store, which is irrelevant here and must not be touched.

1. SIDEBAR: remove the "Sample" badge entirely (no badge, no pill, no "Examples" section). Sort ALL productions newest first by created_at (samples just sit at their date). The client must do this sorting itself; do not rely on the API order (the API still puts samples first).
2. EMPTY / START STATE (center column, no job selected):
   - REMOVE the 5-step "how it works" strip and the big sample production cards.
   - Show a short greeting/hero (keep the title "Your AI film crew." plus a one-line subtitle; the small model pill may stay).
   - Below it, the EXAMPLE PROMPT CARDS: the existing chips (Ceramic mug ad, Night-run sneaker ad, Sci-fi short, Lighthouse drama) become proper cards in the center (a 2x2 or 4-up grid; icon, title, a one-line preview of the prompt, and a mode tag Ad/Short film). Clicking a card fills the composer textarea with the prompt, sets the mode toggle to that card's mode and focuses the input (it does NOT submit).
   - Below them, a "Recent projects" grid: recent productions from /api/jobs, newest first, at most 6, only status completed (or with a poster). Each tile shows the poster thumbnail (16:9, object-fit cover; neutral placeholder if missing), title (ellipsis), mode and duration (e.g. "Short film · 2:30"). Clicking opens the production exactly as the sidebar does. Include the two finished films (Skerry Rock short film, AURA ad) whenever they are in the list. No sample badges here either.
   - The message bar stays at the bottom. REMOVE the example chips from above the input (they are cards now). Keep the mode toggle, reference upload, and limits note.
   - It must look polished in light AND dark at 1440x900 and 1280x800; the start state may scroll inside #chat-scroll, but the composer must not cover content (bottom padding).
3. Samples stay read-only on the backend (403 on chat). Keep the read-only footer hint with the "Start your own production" link in a sample's chat. Just no badge anywhere (also remove "Sample · read-only" pills in the chat header; a subtle "read-only" text there is acceptable but not required).
4. Do not break: deep links, final cut panel, thinking block, theme toggle, SSE, tests.

Verify: screenshots via /tmp/pwenv/bin/python /workspace/agy_specs/screenshot.py "http://127.0.0.1:8092/" /tmp/s6_start.png (plus --theme dark, plus --w 1280 --h 800); node --check static/app.js; `.venv/bin/python -m pytest -q tests/test_api.py tests/test_public_demo.py` (a test needs "SHOWRUNNER" literal in index.html; keep it). Report what changed.

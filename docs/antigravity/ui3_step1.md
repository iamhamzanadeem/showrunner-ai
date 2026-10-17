# UI v3 · Step 1: app shell, sidebar, chat column, composer, empty state

Read `/workspace/agy_specs/ui3_context.md` first (shared context and constraints). Then implement ONLY this milestone, leaving clean extension points for the next steps (thinking block content, final cut panel).

Build:
1. New `static/index.html` + `static/style.css` + `static/app.js` structure for the chat-app layout: left sidebar, center chat column (scrollable message list + bottom composer), and an (initially hidden) right `#final-panel` container that later steps fill.
2. Sidebar: brand mark + "Showrunner AI", "New production" button, productions list from `GET /api/jobs` (samples first with a small "Sample" tag; title or brief snippet; "Ad"/"Short film" + relative date like "Today"/"Oct 17"), active item highlighted, collapse button (collapses to hidden with a small toggle button visible in the chat header), theme toggle (sun/moon icons) at the bottom, persisted (`showrunner_theme`, light default).
3. Chat header (thin): sidebar toggle (when collapsed), production title + status pill + "Sample · read-only" pill.
4. Empty state (route `/`): centered hero "Your AI film crew." + subline "Brief in, finished film out." small eyebrow "Gemini · Veo 3.1 · Gemini TTS · Lyria", a 5-step "how the agent works" mini strip (Plan, Style bible, Render, Critique & reshoot, Score & cut), and a sample gallery of 2 large poster cards (title, logline, runtime, shots, "N takes reviewed by the critic") that open the sample job.
5. Bottom composer (always at the bottom of the chat column): auto-growing textarea, send button, Ad/Short film segmented toggle, "Reference images" attach (image previews as removable chips), example chips (only on the empty state), public limits note from `/api/health` (small, muted). Enter sends, Shift+Enter newline. On submit: POST /api/jobs (multipart), friendly banner for 429/403, then route to `/?job=<id>`.
6. Job route `/?job=<id>`: render the user brief as a user bubble (long briefs clamp with "Show full brief"), and a placeholder agent block (we flesh it out in step 2; you may port v2's thread rendering as a temporary body). Wire SSE + dedupe + polling fallback + refetch of job & takes (reuse v2 logic). After completion the composer becomes the director-note input (POST /chat); for samples it's disabled with the read-only note + "Start your own production →".
7. Notice banner for caps/403 at top of chat column.
8. Responsive at 1280px.

Acceptance: `node --check static/app.js` passes; quick tests `tests/test_api.py tests/test_public_demo.py` pass; screenshots of `/` and `/?job=job_eb80955254` at 1440×900 in light and dark show a polished ChatGPT-style shell with no JS errors. Review your own screenshots and fix visual problems before finishing. Finish with a short summary of what you built and any open issues.

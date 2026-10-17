# Showrunner AI: UI v3 shared context (read this first, every step)

You are rebuilding the web frontend of **Showrunner AI**, an agentic AI video studio (FastAPI backend, vanilla JS frontend, no build step) in `/workspace/showrunner-ai`. Deadline: hackathon judging (Google Cloud AI Builder Cup). The agent loop (plan → style bible → render with Veo → Gemini critic rejects/accepts takes → reshoot with correction → voiceover/music → assembly → director notes) is our main judging point, so the UI must make it visible and beautiful.

## Files you own
- `static/index.html`, `static/style.css`, `static/app.js` (rewrite freely; current v2 versions are a 3-column dashboard you are replacing, reuse logic from them where useful: SSE handling, dedupe, polling, helpers, takes rendering).
- Small backend additions are allowed only if truly needed; do NOT change the pipeline. Do not touch `scripts/`, `.env`, `data/`.

## Hard constraints
- `GET /` HTML must contain the literal text `SHOWRUNNER` (a test checks it; v2 uses `<meta name="application-name" content="SHOWRUNNER AI">`, so keep that).
- No frameworks, no build step. Google Fonts Inter + JetBrains Mono via CDN is fine.
- Light theme is the default; dark via toggle; persisted in `localStorage` key `showrunner_theme` (inline script in `<head>` applies dark before paint). Ignore prefers-color-scheme. Use CSS variables for theming (v2 `style.css` has a good variable set; reuse/adapt).
- NEVER start paid generations. Only use the FREE mock server: `bash /workspace/agy_specs/run_mock_server.sh` (port 8092, mock providers, serves the two real sample jobs). Do NOT start servers in google mode, do NOT POST to any other port.
- Tests: `cd /workspace/showrunner-ai && .venv/bin/python -m pytest -q` must stay green (takes ~4 min; run at least `tests/test_api.py tests/test_public_demo.py` quickly).
- Do not commit or push (the reviewer does that).

## Backend API (existing, keep using)
- `GET /api/health` → `{active_provider, limits:{max_jobs_per_day,max_jobs_per_ip_per_day,max_shots_per_job,max_runtime_s}, sample_job_ids:[...]}`
- `GET /api/jobs` → list `{id,title,brief,mode('ad'|'short_film'),status,created_at,poster_url,final_video_url,is_sample,logline,duration_s,scene_count,shot_count,take_count}`; samples first.
- `POST /api/jobs` multipart form: `brief`, `mode`, `ref_images` (files) → `{id,...}`. Errors: 429 (daily caps, friendly `detail`), 422.
- `GET /api/jobs/{id}` → full Job: `status` (queued|planning|planned|generating|critiquing|assembling|completed|failed), `brief`, `mode`, `created_at`, `updated_at`, `reference_images`, `character_refs{name:url}`, `product_refs{name:url}`, `plan{title,logline,bible{style{palette[],lighting,lens,mood},characters[{name,appearance}],products[{name,description}]},scenes[{id,scene_number,title,summary,shots[{id,scene_id,duration_s,description,camera,visual_prompt,vo_line,status,is_flagged_fallback,attempts[...]}]}]}`, `soundtrack_url`, `poster_url`, `final_video_url`, `messages[{role:'user'|'agent',text,ts}]`, `versions[{version,url,poster_url,message,changed_shots[],created_at}]`, `error`.
- `GET /api/jobs/{id}/takes` → `{shots:[{shot_id,scene_id,status,duration_s,accepted_attempt,is_flagged_fallback,chained_from_shot_id,image_url,video_url,takes:[{attempt,prompt,image_url,clip_url,created_at,critique:{score,passed,reasons[],suggested_prompt_fix},accepted,clip_available}]}]}`. Critic reasons often start with a tag like `[off_brief]`, `[artifact]`; render the tag as a small chip.
- `GET /api/jobs/{id}/events` SSE, `event: agent_event`, data `{id,step,message,timestamp,shot_id,scene_id,data}`. Steps: PLAN, REF, KEYFRAME, GEN, CRITIC_PASS, CRITIC_FAIL, RETRY, CHAIN, VO, MUSIC, ASSEMBLE, EDIT (data.affected_shot_ids), DONE, ERROR, INFO. The server REPLAYS history on each (re)connect, so dedupe by event id and never reset the seen-set (a replayed old DONE must not close the stream after a director note). Keep a polling fallback (refetch job+takes every ~5s while busy).
- `POST /api/jobs/{id}/chat` JSON `{message}` → director note (refinement). 403 for sample jobs ("read-only sample"), 409 while busy, 429 caps. After success the job becomes busy and a new version appears when done.
- `POST /api/jobs/{id}/resume` for failed jobs.
- Media: `/media/<job>/<file>`; big films are served with HTTP range (video element handles it).
- Shot `video_url` in job.json can be a filesystem path; use the `/takes` endpoint's normalized URLs.

## Target layout (ChatGPT / Claude style chat app, for video)
1. LEFT SIDEBAR (~260px): brand, "New production" button, list of productions (samples first with a "Sample" tag, then recent jobs) with title, mode (Ad/Short film) and date; active item highlighted; theme toggle (sun/moon) at bottom; collapsible (button to collapse to a slim rail / hidden).
2. CENTER CHAT: conversation with the "Director" agent. Message bar fixed at the BOTTOM of the chat column. Empty state (new production): centered hero ("Showrunner AI: your AI film crew. Brief in, finished film out."), Ad/Short film mode toggle, reference image upload, example chips, sample gallery (2 big cards: posters of the samples) and public limits note; the composer is the bottom bar.
   After submit: user message bubble (brief), then the agent's work as a collapsible **"Director is working…" / "Worked for 14m · 20 shots · 32 takes · 12 reshoots"** block (like a reasoning trace, chevron to expand/collapse). Inside, step by step: plan card (title, logline, collapsible shot list) → STYLE BIBLE card (palette swatches, mood, lighting, lens, character/product reference sheets) → per scene, each shot (keyframe thumb, takes: "Take 1 · 0.46 rejected · <reason>" → "Correction applied → <fix>" → "Take 2 · 0.74 accepted", thumbnails clickable) → voiceover + music → assembly. Streams live (SSE) during a run, auto-expanded while running; collapsed by default once done (with the one-line summary). Samples: reconstruct from saved state + /takes (duration = updated_at − created_at, or sum of take timestamps).
   On completion: a "Final cut" card in the chat (poster + "Open final cut").
3. FINAL CUT MODE: opening the final cut (auto when a live run finishes, and on click) COLLAPSES the sidebar, makes the chat a narrower left column (~400px), and opens the FINAL CUT PANEL on the right (wide). Closing the panel restores the normal layout.
4. FINAL CUT PANEL: header (title, version tabs v1/v2…, Download MP4, close ×); big video player; editor-style TIMELINE below: scene blocks with width proportional to duration, shot clip thumbnails inside, scene label on each block, time ruler below (0:00, 0:08 …), horizontal scroll when it overflows, playhead synced to video time, click to seek. KPIs (runtime · shots · takes · reshoots · avg critic) as ONE small muted line. Clicking a clip selects it and opens a SHOT DETAIL strip below the timeline: takes side by side (thumb, score, verdict, reasons, correction; click a take to play its clip in the player), action, camera, VO line, final Veo prompt (collapsible); × closes; clicking another clip swaps.
5. Director notes typed in the same bottom bar after completion; show "Regenerating: shot chips" and a new version appears in the panel. Samples: input disabled, note "This is a read-only sample · Start your own production →".
6. Keep: caps banner with friendly 429/403 messages, deep links `/?job=<id>` and `/?job=<id>&shot=<shot_id>` (opens final cut panel with that shot selected), responsiveness at 1280px wide, smooth message entry animations, skeleton loaders, Inter 14–15px base, generous spacing, one accent color (indigo/violet works), polished in BOTH themes.

## How to check your work
- `bash /workspace/agy_specs/run_mock_server.sh` then open `http://127.0.0.1:8092/`. Samples: `/?job=job_eb80955254` (20-shot film, many rejected→accepted takes e.g. shot_4_2), `/?job=job_aedcb881dc` (4-shot ad).
- Screenshots: `/tmp/pwenv/bin/python /workspace/agy_specs/screenshot.py <url> /tmp/x.png --w 1440 --h 900 [--theme dark] [--click "<css>"] [--eval "<js>"]` (prints JS errors). Look at the screenshot image files to self-review.
- `node --check static/app.js`.

## Note (added by reviewer after step 1)
The mock server's DATA_DIR `/tmp/mockdata/jobs/` now contains real COPIES of the two sample jobs (earlier they were symlinks, which is why media 404'd). Do not loosen the media path checks in `app/main.py` for this. The backend must stay unchanged (`git diff app/` should be empty).
- Never use `pkill -f` / `killall` patterns (they can kill your own agent process). The mock server script uses a pidfile.

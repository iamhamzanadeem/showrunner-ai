# UI v3 · Step 2: the "Director is working…" thinking block (the agent trace)

Read `/workspace/agy_specs/ui3_context.md` again for API details (note the new reviewer note at the bottom). Step 1 built the shell; nice work.

## Reviewer corrections from step 1 (do these first)
- REVERT your change to `app/main.py` (`_is_safe_media_target`). The sample jobs in `/tmp/mockdata/jobs/` are now real copies, not symlinks, so media is served fine without loosening the path check. `git diff app/` must be empty at the end.
- The mock server is started with `setsid` now (it survives your session); restart it with `bash /workspace/agy_specs/run_mock_server.sh` if it's down.
- Empty state: the example chips float over the bottom of the sample gallery posters (overlap). Make the scroll area end above the composer (bottom padding = composer height) so nothing is hidden behind the composer/chips, and place the chips cleanly just above the composer box (or inside it, top row).
- The 5-step strip cards are cramped at 1440: shorten the descriptions or use a single-line compact style.

## Step 2 Now build the agent trace inside the chat, the centerpiece for judges.

Build:
1. After the user's brief bubble, an agent message from "Director" (avatar) containing a collapsible **thinking block**:
   - Header row: spinner + "Director is working… <live step text>" while busy (auto-expanded); when done: check icon + "Worked for 14m · 20 shots · 32 takes · 12 reshoots" (collapsed by default for finished jobs and samples). Chevron toggles. Elapsed time: `updated_at − created_at` of the job (or last take created_at − created_at), formatted like "14m" / "1h 3m".
   - Body: a vertical timeline (thin left rail with step dots) of steps:
     a. **Plan**: title, logline, pills (N scenes · N shots · runtime), collapsible shot list grouped by scene.
     b. **Style bible** card: palette swatches with hex labels, mood, lighting, lens, character and product reference sheet images (from `job.character_refs`/`job.product_refs`) with names and short descriptions.
     c. **Scenes**: for each scene a small heading "Scene 3 · Blackout and the Stricken Vessel", and for each shot a compact row: keyframe thumb (accepted take's image_url), shot id + description (ellipsis), status (passed / passed on take 2 / best of 2 flagged / rendering / queued) and the accepted critic score chip. Shots with >1 take or flagged/running show the take flow beneath: `Take 1 [thumb] 0.46 rejected · <first reason with [tag] chip>` → `Correction applied → <suggested_prompt_fix>` (accent left border) → `Take 2 [thumb] 0.74 accepted`. Running shots show "Take N+1 · rendering…" with a shimmer thumb. Clicking a take thumbnail opens the final cut panel (if a version exists) with that shot+take selected; otherwise show the take in a lightweight lightbox/modal player (implement a simple modal).
     d. **Critic loop summary** (when all shots done): takes reviewed, reshoots, passed first try, avg accepted score; note about flagged shots.
     e. **Voiceover & music**: N VO lines, soundtrack audio player (job.soundtrack_url).
     f. **Assembly**: "Assembled the master cut · 2:30 · 20 clips · captions · VO ducked".
   - Live: stream during the run from SSE (+ refetch job/takes on events, throttled ~700ms). Keyed DOM reconciliation so existing nodes are not re-created (no flicker, audio keeps playing, expanded/collapsed state preserved); new steps fade/slide in.
2. After the thinking block, when `versions[0]` exists: a **Final cut card** in the chat (poster thumbnail, title, runtime, "Open final cut" button). For now the button can call a stub `openFinalCut()` (step 3 implements the panel).
3. Director notes history (job.messages in time order with versions ≥2): user bubbles; agent replies parse "Re-shooting shot(s): a, b." into "Regenerating:" shot chips; each later version gets its own small "Cut v2 ready" card with changed shots and "Open". While a refinement runs, show a live "working" block (a new thinking block for the refinement is ideal, showing EDIT event targets as "Will regenerate: chips" and progress).
4. Failed jobs: error card with "Resume production" (not for samples).
5. Read-only samples: same reconstruction; composer disabled with note.
6. Polish: 14–15px text, comfortable spacing, the trace must be readable at a 760px-wide chat column and at the narrow 400px final-cut chat column (step 3), both themes.

Acceptance: screenshots at 1440×900 of `/?job=job_eb80955254` with the thinking block EXPANDED and scrolled so the Style bible card AND a rejected-then-accepted take (e.g. shot_4_2) are visible (you can add a `?expand=1` deep-link param or click the header via the screenshot tool's `--click`). Also verify a live mock run on port 8092 streams into the block (start a job from the UI or `curl -F brief=... -F mode=ad http://127.0.0.1:8092/api/jobs`, then open it). No JS errors, quick tests pass. Review your screenshots critically and fix issues. Summarize what you built.

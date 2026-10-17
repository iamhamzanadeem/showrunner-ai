# UI v3 · Step 3: Final cut mode + Final cut panel (player, editor timeline, shot detail)

Context: `/workspace/agy_specs/ui3_context.md`. Steps 1–2 built the shell and the thinking block (good work). Now the final cut experience.

## Reviewer corrections from step 2 (do these first; reviewer screenshots at 1440×900)
1. Empty state: the example chips STILL overlap the sample gallery cards (they cover the card titles "The Last Light of Skerry Rock" / "AURA: Stay in Flow" and loglines). Fix structurally: the scrollable message area must have bottom padding ≥ composer height + chips height + 24px, OR move the example chips INSIDE the composer box (a row above the textarea, only on the empty state). Verify with a screenshot that the gallery card text is fully readable when scrolled to the bottom.
2. The area behind/below the bottom composer is transparent: scrolled content shows through under and beside it. Give the composer region a solid background (chat bg) with a soft top fade gradient, so content disappears cleanly behind it.
3. Thinking-block take flow: take thumbnails are tiny (~40×24). Make take thumbs 112×63 (16:9) with rounded corners and a hover play icon; shot row keyframe thumbs ~88×50. This is the hero visual for judges.
4. Shot row title is clipped mid-word without an ellipsis ("...burning steadil"): use `text-overflow: ellipsis; white-space: nowrap; overflow: hidden; min-width: 0` on the flex child.
5. Shot status text "passed on take 2" wraps onto two lines and misaligns the meta row; keep the meta row on one line (nowrap, ellipsis on the camera text only).


## Layout mode
- `openFinalCut({version, shot, take})`: add a `final-mode` class on the app root → sidebar collapses (hidden; the header toggle can still re-open it as an overlay), the chat column becomes a fixed narrow left column (~400px at 1440, ~360px at 1280), and `#final-panel` appears on the right taking the remaining width, full height, with its own scroll. Animate the transition (~250ms).
- Opens automatically when a live run finishes (DONE event / status transitions to completed with a version) and when the user clicks a Final cut card / "Open final cut" button / a take thumbnail in the trace. Deep links: `/?job=<id>&shot=<shot_id>` (and optional `&take=N`) open the panel with that shot selected; `/?job=<id>&cut=1` opens the panel. Closing (× in panel header, or Esc) removes `final-mode` and restores the normal layout and updates the URL. Sample jobs on load: do NOT auto-open (show chat first) unless the deep link asks.
- The trace in the narrow chat column must still look good (compact).

## Final cut panel contents (clean, easy to understand)
1. Header: film title + "Final cut", version tabs (v1, v2… only if >1 versions), "Download MP4" (link to the version url, `download` attr), close ×.
2. Video player: large, 16:9, max height so the timeline is visible without scrolling at 1440×900 (player ≈ 55–60% of panel height), dark stage background, poster = version poster. When a take is selected (from shot detail or trace), the player plays that take's clip (loop) with a small badge "shot_4_2 · Take 1 · 0.46 rejected" and a "▶ Back to final cut" button.
3. Editor-style TIMELINE under the player:
   - One horizontal track. Each SCENE is a block whose width is proportional to its real duration (sum of its shots' duration_s) using a px-per-second scale; minimum zoom such that the whole film fits the panel width if possible; if a film is long, allow overflow with horizontal scroll (keep ~12px/s minimum so 4–8s clips are visible). Scene label (e.g. "S3 · Blackout and the Stricken Vessel") on top of each block, truncated with ellipsis.
   - Inside each scene block, the shots as clip tiles (width ∝ duration) showing the accepted keyframe thumbnail (object-fit cover, like a filmstrip; repeat/cover is fine), a tiny shot id and a status marker: green dot = passed, amber = flagged best-of-N, and a small "2 takes" badge if >1 take.
   - Time ruler below the track with tick marks and labels every 5s or 10s depending on scale (0:00, 0:10, 0:20 …), plus minor ticks.
   - Playhead: vertical line across track+ruler, synced to `video.currentTime` (requestAnimationFrame or timeupdate) while playing the final cut; auto-scroll the timeline to keep the playhead visible.
   - Click anywhere on the track/ruler to SEEK the final cut to that time (if a take clip is playing, switch back to the final cut first). Clicking a clip tile also SELECTS that shot (see 5) and seeks to its start.
   - Shot start times = cumulative durations in plan order (the assembled film is the concatenation).
4. KPI line: one small muted line under the timeline, e.g. "2:30 runtime · 20 shots · 32 takes rendered · 12 critic reshoots · avg critic 0.66". Not prominent.
5. SHOT DETAIL strip (below the timeline, appears when a clip is selected; selected clip highlighted with accent outline; × closes; selecting another clip swaps):
   - Header: "Scene 4 · Shot 2" + shot id + status pill + duration.
   - Takes side by side (cards): keyframe thumb (click → play that take's clip in the player), "Take N" + score chip, verdict (Accepted · passed / Accepted · best of N / Rejected), reasons list with [tag] chips, correction ("→ fix") for rejected takes. Accepted take card has a green outline.
   - Right/below: Action, Camera, VO line (italic, accent border), Final Veo prompt (collapsed by default, mono, expandable).
6. Version switching: tabs swap the player source and the poster; the timeline stays (shots same). After a director note produces v2, the panel shows v2 and the tabs.
7. Both themes polished; panel uses surface colors; the video stage stays dark in light theme.

## Acceptance
Screenshots (1440×900) via the screenshot tool:
- `/?job=job_eb80955254&cut=1`: final cut layout (sidebar collapsed, chat left, panel right) with the full timeline visible.
- `/?job=job_eb80955254&shot=shot_4_2`: clip selected + shot detail strip visible (scroll the panel if needed via --eval).
- Same as first in `--theme dark`; and `/?job=job_aedcb881dc&cut=1` at 1280×800.
- Live mock run: start a job on port 8092 (free mock) from the UI, wait until completed: the panel must auto-open; then send a director note ("Make scene 1 warmer"): the chat shows "Regenerating" chips + working block, and on completion the panel shows v2 tabs.
No JS errors; `node --check`; quick tests pass; `git diff app/` empty. Review screenshots critically, fix, then summarize.

/* Showrunner AI: Chat-app studio (UI v3 Step 2). Vanilla JS, no build step. */
(() => {
  "use strict";

  // =========================================================
  // Helpers
  // =========================================================
  const $ = (id) => document.getElementById(id);
  const esc = (v) =>
    String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const BUSY = new Set(["queued", "planning", "planned", "generating", "critiquing", "assembling"]);

  const fmtDur = (s) => {
    if (s == null || isNaN(s)) return "–";
    s = Math.round(s);
    return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${s}s`;
  };

  const fmtTime = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const fmtRelativeDate = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return "Today";
    const yest = new Date(now);
    yest.setDate(now.getDate() - 1);
    if (d.toDateString() === yest.toDateString()) return "Yesterday";
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  const scoreClass = (s) => (s >= 0.7 ? "hi" : s >= 0.55 ? "mid" : "lo");
  const scoreHtml = (s) => (s == null ? "" : `<span class="score ${scoreClass(s)}">${Number(s).toFixed(2)}</span>`);
  const shortId = (id) => String(id || "").replace(/^shot_/, "").replace("_", ".");

  const splitReason = (r) => {
    const m = /^\s*\[([a-z_ -]+)\]\s*(.*)$/i.exec(r || "");
    return m ? { tag: m[1].replace(/_/g, " "), text: m[2] } : { tag: null, text: r || "" };
  };

  const reasonHtml = (r) => {
    const { tag, text } = splitReason(r);
    return `${tag ? `<span class="reason-tag">${esc(tag)}</span>` : ""}${esc(text)}`;
  };

  const ICON = {
    plan: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
    bible: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="8.5" cy="10" r="1.4" fill="currentColor"/><circle cx="12" cy="7.5" r="1.4" fill="currentColor"/><circle cx="15.5" cy="10" r="1.4" fill="currentColor"/></svg>',
    film: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M10 9.5v5l4-2.5z" fill="currentColor"/></svg>',
    critic: '<svg viewBox="0 0 24 24"><path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>',
    music: '<svg viewBox="0 0 24 24"><path d="M9 18V6l11-2v12" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="6.5" cy="18" r="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="17.5" cy="16" r="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/></svg>',
    cut: '<svg viewBox="0 0 24 24"><circle cx="6" cy="7" r="2.6" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="6" cy="17" r="2.6" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M8.2 8.4L20 18M8.2 15.6L20 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    alert: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M12 7.5v6M12 16.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    play: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M8 5l11 7-11 7z" fill="currentColor"/></svg>',
    chev: '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="16" height="16"><circle cx="12" cy="12" r="9" fill="currentColor" opacity="0.15"/><path d="M8 12.5l2.5 2.5L16 9.5" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  // =========================================================
  // Theme & Sidebar State Persistence
  // =========================================================
  const THEME_KEY = "showrunner_theme";
  const SIDEBAR_KEY = "showrunner_sidebar";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* ignore */ }
  }

  $("theme-toggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(cur);
  });

  function setSidebarCollapsed(collapsed) {
    const layout = $("app-layout");
    const expandBtn = $("sidebar-expand-btn");
    layout.classList.toggle("sidebar-collapsed", collapsed);
    expandBtn.classList.toggle("hidden", !collapsed);
    try { localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "expanded"); } catch (e) { /* ignore */ }
  }

  $("sidebar-collapse-btn").addEventListener("click", () => setSidebarCollapsed(true));
  $("sidebar-expand-btn").addEventListener("click", () => setSidebarCollapsed(false));

  try {
    if (localStorage.getItem(SIDEBAR_KEY) === "collapsed") setSidebarCollapsed(true);
  } catch (e) { /* ignore */ }

  // =========================================================
  // State
  // =========================================================
  const S = {
    health: null,
    sampleIds: new Set(),
    limits: {},
    jobs: [],
    job: null,
    takes: {},
    jobId: null,
    es: null,
    seen: new Set(),
    live: null,
    liveImages: {},
    editTargets: null,
    thinkingExpanded: false,
    userToggledThinking: false,
    showPlanShotlist: false,
    localNotes: [],
    firstRender: true,
    refreshing: false,
    refreshQueued: false,
    pollTimer: null,
    stagedFiles: [],
    briefOpen: false,
    inFinalMode: false,
    activeVersion: 1,
    selectedShotId: null,
    selectedTakeAttempt: null,
    playingTake: null,
    timelineScale: 12,
    shotTimings: [],
    totalDuration: 0,
    expandedVeoPrompt: false,
  };

  // =========================================================
  // Notices / Errors
  // =========================================================
  function showNotice(msg) {
    $("notice-text").textContent = msg;
    $("notice-banner").classList.remove("hidden");
  }
  $("notice-close").addEventListener("click", () => $("notice-banner").classList.add("hidden"));

  async function friendlyError(res, fallback) {
    const data = await res.json().catch(() => ({}));
    const msg = data.detail || `${fallback} (${res.status})`;
    if (res.status === 429 || res.status === 403) showNotice(msg);
    return msg;
  }

  // =========================================================
  // Health & Limits
  // =========================================================
  async function checkHealth() {
    try {
      const res = await fetch("/api/health");
      const d = await res.json();
      S.health = d;
      S.sampleIds = new Set(d.sample_job_ids || []);
      S.limits = d.limits || {};
      const chip = $("provider-chip");
      const google = (d.active_provider || "") === "google";
      chip.classList.toggle("live", google);
      chip.classList.toggle("mock", !google);
      $("provider-name").textContent = google ? "Vertex AI · live" : "Mock engine";
      renderLimits();
    } catch (e) {
      $("provider-name").textContent = "Offline";
    }
  }

  function renderLimits() {
    const l = S.limits || {};
    const parts = [];
    if (l.max_shots_per_job) parts.push(`up to ${l.max_shots_per_job} shots`);
    if (l.max_runtime_s) parts.push(`~${Math.round(l.max_runtime_s)}s`);
    if (l.max_jobs_per_ip_per_day) parts.push(`${l.max_jobs_per_ip_per_day} runs per visitor per day`);
    $("limits-note").textContent = parts.length
      ? `Public demo: ${parts.join(" · ")}. Real Gemini, Veo & Lyria calls, takes a few minutes.`
      : "Runs real Gemini, Veo & Lyria models when configured. Takes a few minutes.";
  }
  const healthReady = checkHealth();

  // =========================================================
  // Elapsed Time Calculator
  // =========================================================
  function getElapsedTime(job) {
    if (!job || !job.created_at) return "";
    const created = new Date(job.created_at).getTime();
    let end = job.updated_at ? new Date(job.updated_at).getTime() : 0;
    // Check if take timestamps extend later
    if (S.takes) {
      Object.values(S.takes).forEach((s) => {
        (s.takes || []).forEach((tk) => {
          if (tk.created_at) {
            const t = new Date(tk.created_at).getTime();
            if (t > end) end = t;
          }
        });
      });
    }
    if (!end || isNaN(end) || isNaN(created)) return "";
    const diffSec = Math.max(1, Math.round((end - created) / 1000));
    if (diffSec < 60) return `${diffSec}s`;
    const m = Math.floor(diffSec / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return remM ? `${h}h ${remM}m` : `${h}h`;
  }

  // =========================================================
  // Jobs List & Recent Projects
  // =========================================================
  async function loadJobs() {
    try {
      const res = await fetch("/api/jobs");
      const list = await res.json();
      S.jobs = (Array.isArray(list) ? list : []).sort((a, b) => {
        const ta = a && a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b && b.created_at ? new Date(b.created_at).getTime() : 0;
        return tb - ta;
      });
    } catch (e) {
      S.jobs = [];
    }
    renderSidebarList();
    renderRecentProjects();
  }

  function renderSidebarList() {
    const container = $("productions-list");
    if (!container) return;
    if (!S.jobs.length) {
      container.innerHTML = `<div class="sidebar-empty" style="padding:14px 10px;color:var(--muted);font-size:12.5px">No productions yet.</div>`;
      return;
    }

    container.innerHTML = S.jobs
      .map((j) => {
        const active = j.id === S.jobId ? "active" : "";
        const title = esc(j.title || j.brief.slice(0, 42));
        const mode = j.mode === "short_film" ? "Short film" : "Ad";
        const date = fmtRelativeDate(j.created_at);

        return `
          <button type="button" class="sidebar-job-item ${active}" data-job="${esc(j.id)}">
            <div class="sji-top">
              <span class="sji-title">${title}</span>
            </div>
            <div class="sji-sub">
              <span>${mode}</span>
              ${date ? `<span>·</span><span>${date}</span>` : ""}
            </div>
          </button>
        `;
      })
      .join("");
  }

  function renderRecentProjects() {
    const el = $("recent-grid");
    if (!el) return;
    const completed = S.jobs.filter((j) => j.status === "completed" || j.poster_url);
    const section = $("recent-section");
    if (!completed.length) {
      if (section) section.classList.add("hidden");
      return;
    }
    if (section) section.classList.remove("hidden");

    // Guarantee the 2 sample films (Skerry Rock short film, AURA ad) are included whenever in the list
    const sampleJobs = completed.filter((j) => S.sampleIds.has(j.id) || j.is_sample);
    const userJobs = completed.filter((j) => !S.sampleIds.has(j.id) && !j.is_sample);
    const neededUser = Math.max(0, 6 - sampleJobs.length);
    const pool = [...userJobs.slice(0, neededUser), ...sampleJobs];
    pool.sort((a, b) => {
      const ta = a && a.created_at ? new Date(a.created_at).getTime() : 0;
      const tb = b && b.created_at ? new Date(b.created_at).getTime() : 0;
      return tb - ta;
    });

    el.innerHTML = pool
      .map((j) => {
        const title = esc(j.title || j.brief.slice(0, 42));
        const mode = j.mode === "short_film" ? "Short film" : "Ad";
        const dur = j.duration_s ? fmtDur(j.duration_s) : "";
        const metaStr = dur ? `${mode} · ${dur}` : mode;

        const thumbHtml = j.poster_url
          ? `<img src="${esc(j.poster_url)}" alt="" loading="lazy">`
          : `<div class="recent-tile-thumb-placeholder">
               <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                 <rect x="2" y="2" width="20" height="20" rx="4"></rect>
                 <polygon points="10 8 16 12 10 16 10 8" fill="currentColor"></polygon>
               </svg>
             </div>`;

        return `
          <button type="button" class="recent-tile" data-job="${esc(j.id)}">
            <div class="recent-tile-thumb">
              ${thumbHtml}
            </div>
            <div class="recent-tile-body">
              <div class="recent-tile-title">${title}</div>
              <div class="recent-tile-meta">${metaStr}</div>
            </div>
          </button>
        `;
      })
      .join("");
  }

  // Click delegation
  document.addEventListener("click", (e) => {
    const card = e.target.closest("[data-job]");
    if (card) {
      openJob(card.dataset.job);
      return;
    }
    const presetCard = e.target.closest("[data-preset]");
    if (presetCard) {
      const p = PRESETS[presetCard.dataset.preset];
      if (p) {
        const input = $("composer-input");
        input.value = p.text;
        setMode(p.mode);
        resizeTextarea(input);
        input.focus();
      }
      return;
    }
    if (e.target.id === "start-own") {
      e.preventDefault();
      showHome();
      return;
    }
    const act = e.target.closest("[data-action]");
    if (act) {
      if (act.dataset.action === "open-final-cut") {
        openFinalCut(Number(act.dataset.version) || 1);
      } else if (act.dataset.action === "resume") {
        resumeJob(act);
      } else if (act.dataset.action === "toggle-shotlist") {
        S.showPlanShotlist = !S.showPlanShotlist;
        renderThread();
      }
    }
  });

  // =========================================================
  // Routing
  // =========================================================
  function showHome(push = true) {
    closeStream();
    closeFinalCut(false);
    S.job = null;
    S.jobId = null;
    if (push) history.pushState({}, "", "/");

    $("empty-state-view").classList.remove("hidden");
    $("job-thread-view").classList.add("hidden");
    $("composer-top").classList.remove("hidden");
    $("attach-label").classList.remove("hidden");

    $("chat-header-title").textContent = "Showrunner AI";
    $("chat-header-status").classList.add("hidden");

    document.title = "Showrunner AI · Your AI film crew";
    renderLimits();
    renderComposer();
    renderSidebarList();
    loadJobs();
  }

  $("new-production-btn").addEventListener("click", () => {
    showHome();
    setTimeout(() => $("composer-input").focus(), 50);
  });

  $("brand-link").addEventListener("click", (e) => {
    e.preventDefault();
    showHome();
  });

  window.addEventListener("popstate", route);

  function route() {
    const p = new URLSearchParams(location.search);
    const id = p.get("job");
    if (id) openJob(id, { push: false });
    else showHome(false);
  }

  // =========================================================
  // Composer Form
  // =========================================================
  const PRESETS = {
    "ad-coffee": {
      mode: "ad",
      text: "A 30-second ad for 'Kiln & Co.', a hand-thrown ceramic coffee mug. Soft morning window light, steam curling, slow tactile close-ups of glaze and hands, ending on the tagline 'Made to be held'.",
    },
    "ad-sneaker": {
      mode: "ad",
      text: "A 30-second night-run commercial for 'Lumen Pace', a reflective running shoe. A lone runner through rain-slick city streets, neon reflections, kinetic tracking shots, ending on the tagline 'Own the night'.",
    },
    "film-scifi": {
      mode: "short_film",
      text: "A 2.5-minute moody sci-fi short film, 'The Signal of Kepler-9'. Scout Maya, alone on an abandoned ice-mining colony, decodes a beacon buried under the canyons and realises it is a warning, not a greeting.",
    },
    "film-lighthouse": {
      mode: "short_film",
      text: "A 2.5-minute maritime drama. A solitary lighthouse keeper must improvise a beam with a hand lantern and a salvaged lens when a storm kills the main light and a trawler heads for the reef.",
    },
  };

  function setMode(mode) {
    $("selected-mode").value = mode;
    document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  }
  document.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  function resizeTextarea(el) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }

  $("composer-input").addEventListener("input", (e) => resizeTextarea(e.target));

  $("composer-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("composer-form").requestSubmit();
    }
  });

  $("file-input").addEventListener("change", (e) => {
    for (const f of e.target.files) if (!S.stagedFiles.some((x) => x.name === f.name)) S.stagedFiles.push(f);
    e.target.value = "";
    renderFiles();
  });

  function renderFiles() {
    const el = $("file-preview-list");
    el.innerHTML = "";
    S.stagedFiles.forEach((f, i) => {
      const t = document.createElement("span");
      t.className = "file-tag";
      t.innerHTML = `<img alt=""><span>${esc(f.name)}</span><button type="button" aria-label="Remove">×</button>`;
      t.querySelector("img").src = URL.createObjectURL(f);
      t.querySelector("button").onclick = () => {
        S.stagedFiles.splice(i, 1);
        renderFiles();
      };
      el.appendChild(t);
    });
  }

  $("composer-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("composer-input").value.trim();
    if (!text) return;

    if (!S.jobId) {
      // ----------------- CREATE NEW JOB -----------------
      const btn = $("composer-send");
      btn.disabled = true;
      const fd = new FormData();
      fd.append("brief", text);
      fd.append("mode", $("selected-mode").value);
      S.stagedFiles.forEach((f) => fd.append("ref_images", f));
      try {
        const res = await fetch("/api/jobs", { method: "POST", body: fd });
        if (!res.ok) throw new Error(await friendlyError(res, "Could not start the production"));
        const data = await res.json();
        S.stagedFiles = [];
        renderFiles();
        $("composer-input").value = "";
        resizeTextarea($("composer-input"));
        openJob(data.id || data.job_id);
      } catch (err) {
        if (!$("notice-banner").classList.contains("hidden")) return;
        showNotice(err.message);
      } finally {
        btn.disabled = false;
      }
    } else {
      // ----------------- DIRECTOR NOTE -----------------
      const btn = $("composer-send");
      btn.disabled = true;
      try {
        const res = await fetch(`/api/jobs/${S.jobId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        if (res.status === 409) {
          const d = await res.json().catch(() => ({}));
          S.localNotes.push({
            text: d.detail || "I'm still working on the current cut. Send your note once it's finished.",
            t: new Date().toISOString(),
          });
          renderThread();
          return;
        }
        if (!res.ok) {
          const msg = await friendlyError(res, "Could not apply the note");
          S.localNotes.push({ text: msg, t: new Date().toISOString() });
          renderThread();
          return;
        }
        $("composer-input").value = "";
        resizeTextarea($("composer-input"));
        await refresh();
        connectStream();
        const sc = $("chat-scroll");
        sc.scrollTop = sc.scrollHeight;
      } catch (err) {
        S.localNotes.push({ text: `Could not reach the server: ${err.message}`, t: new Date().toISOString() });
        renderThread();
      } finally {
        if (S.job) renderComposer();
      }
    }
  });

  // =========================================================
  // Job Workspace: Open / Refresh
  // =========================================================
  const isSample = () => S.sampleIds.has(S.jobId) || Boolean(S.job && S.job.is_sample);

  async function openJob(jobId, opts = {}) {
    await healthReady;
    if (opts.push !== false) {
      history.pushState({}, "", `/?job=${jobId}`);
    }
    closeStream();

    const p = new URLSearchParams(location.search);
    const expandParam = p.get("expand") === "1";
    const cutParam = p.get("cut") === "1";
    const shotParam = p.get("shot");
    const takeParam = p.get("take");

    Object.assign(S, {
      jobId,
      job: null,
      takes: {},
      seen: new Set(),
      live: null,
      liveImages: {},
      editTargets: null,
      thinkingExpanded: expandParam,
      userToggledThinking: expandParam,
      showPlanShotlist: false,
      localNotes: [],
      firstRender: true,
      briefOpen: false,
      inFinalMode: false,
      playingTake: null,
      selectedShotId: null,
      selectedTakeAttempt: null,
    });

    $("empty-state-view").classList.add("hidden");
    $("job-thread-view").classList.remove("hidden");
    $("composer-top").classList.add("hidden");
    $("attach-label").classList.add("hidden");

    $("chat-header-title").textContent = "Loading production…";
    $("chat-header-status").classList.add("hidden");

    $("brief-bubble-container").innerHTML = "";
    $("thread").innerHTML = threadSkeleton();

    renderSidebarList();
    await refresh();

    if (cutParam || shotParam) {
      const hasCut = S.job && ((S.job.versions && S.job.versions.length) || S.job.final_video_url);
      if (hasCut) {
        openFinalCut({
          shot: shotParam || null,
          take: takeParam ? Number(takeParam) : null,
          pushState: false,
        });
      }
    } else if (!isSample() && S.job && S.job.status === "completed" && ((S.job.versions && S.job.versions.length) || S.job.final_video_url)) {
      openFinalCut({ pushState: false });
    }

    if (S.job && (BUSY.has(S.job.status) || !S.job.final_video_url)) {
      connectStream();
    }
    if (!S.jobs.length) loadJobs();
  }

  async function refresh() {
    if (!S.jobId) return;
    if (S.refreshing) {
      S.refreshQueued = true;
      return;
    }
    S.refreshing = true;
    const id = S.jobId;

    try {
      const [jr, tr] = await Promise.all([fetch(`/api/jobs/${id}`), fetch(`/api/jobs/${id}/takes`)]);
      if (id !== S.jobId) return;
      if (jr.status === 404) {
        showNotice("That production could not be found.");
        showHome();
        return;
      }
      S.job = await jr.json();
      const takes = tr.ok ? await tr.json() : { shots: [] };
      S.takes = {};
      (takes.shots || []).forEach((s) => (S.takes[s.shot_id] = s));
      renderJob();
    } catch (e) {
      console.error(e);
    } finally {
      S.refreshing = false;
      if (S.refreshQueued) {
        S.refreshQueued = false;
        setTimeout(refresh, 300);
      }
    }
  }

  let refreshTimer = null;
  function scheduleRefresh(ms = 700) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, ms);
  }

  // =========================================================
  // SSE & Live Updates
  // =========================================================
  function closeStream() {
    if (S.es) S.es.close();
    S.es = null;
    clearInterval(S.pollTimer);
    S.pollTimer = null;
  }

  function connectStream() {
    if (!S.jobId) return;
    if (S.es) S.es.close();

    const es = new EventSource(`/api/jobs/${S.jobId}/events`);
    S.es = es;

    es.addEventListener("agent_event", (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }
      if (ev.id) {
        if (S.seen.has(ev.id)) return;
        S.seen.add(ev.id);
      }
      onEvent(ev);
    });

    es.onerror = () => {
      if (!S.pollTimer) {
        S.pollTimer = setInterval(() => {
          if (!S.job || !BUSY.has(S.job.status)) {
            clearInterval(S.pollTimer);
            S.pollTimer = null;
            return;
          }
          refresh();
        }, 5000);
      }
    };
  }

  function ensureWatching() {
    if (!S.job) return;
    const busy = BUSY.has(S.job.status);
    if (busy && !S.es) connectStream();
    if (busy && !S.pollTimer) {
      S.pollTimer = setInterval(() => {
        if (!S.job || !BUSY.has(S.job.status)) {
          clearInterval(S.pollTimer);
          S.pollTimer = null;
          return;
        }
        refresh();
      }, 5000);
    }
    if (!busy) {
      if (S.pollTimer) clearInterval(S.pollTimer);
      S.pollTimer = null;
      S.live = null;
    }
  }

  function onEvent(ev) {
    const d = ev.data || {};
    S.live = ev;
    if (ev.step === "EDIT") S.editTargets = d.affected_shot_ids || null;
    if (ev.shot_id && d.image_url) S.liveImages[ev.shot_id] = d.image_url;
    if (ev.step === "DONE" || ev.step === "ERROR") {
      S.live = null;
      S.editTargets = null;
      refresh().then(() => {
        if (ev.step === "DONE" && S.job && (S.job.final_video_url || (S.job.versions && S.job.versions.length))) {
          const v = S.job.versions && S.job.versions.length ? S.job.versions[S.job.versions.length - 1].version : 1;
          if (!S.inFinalMode) {
            openFinalCut({ version: v });
          } else {
            S.activeVersion = v;
            renderFinalPanel();
          }
        }
      });
      loadJobs();
      if (S.es) S.es.close();
      S.es = null;
      return;
    }
    scheduleRefresh(ev.step === "PLAN" && d.plan ? 50 : 700);
  }

  // =========================================================
  // Render: Job Route
  // =========================================================
  function renderJob() {
    const job = S.job;
    if (!job) return;

    // Header
    const title = (job.plan && job.plan.title) || job.brief.slice(0, 48) || "Untitled production";
    $("chat-header-title").textContent = title;
    document.title = `${title} · Showrunner AI`;

    const st = $("chat-header-status");
    const busy = BUSY.has(job.status);
    st.textContent = busy ? job.status : job.status === "completed" ? "completed" : job.status;
    st.className = `pill ${busy ? "run" : job.status === "completed" ? "ok" : job.status === "failed" ? "bad" : ""}`;
    st.classList.remove("hidden");

    // Auto-expand thinking block if busy, unless user explicitly toggled it
    if (!S.userToggledThinking) {
      S.thinkingExpanded = busy;
    }

    renderBriefBubble();
    renderThread();
    renderComposer();
    renderSidebarList();
    if (S.inFinalMode) {
      renderFinalPanel();
    }

    S.firstRender = false;
    ensureWatching();
  }

  function renderBriefBubble() {
    const job = S.job;
    if (!job) return;

    const refs = (job.reference_images || []).map((u) => `<img src="${esc(u)}" alt="" loading="lazy">`).join("");
    const isLong = (job.brief || "").length > 320;
    const clampClass = isLong && !S.briefOpen ? "clamp" : "";
    const modeLabel = job.mode === "short_film" ? "Short film" : "Ad";

    $("brief-bubble-container").innerHTML = `
      <div class="brief-bubble">
        <div class="brief-avatar">You</div>
        <div class="brief-content">
          <div class="brief-head">
            <span class="brief-who">User Brief</span>
            <span class="brief-meta">${esc(fmtTime(job.created_at))} · ${modeLabel}</span>
          </div>
          <div class="brief-text ${clampClass}">${esc(job.brief)}</div>
          ${isLong ? `<button type="button" class="brief-toggle-btn" id="brief-toggle-btn">${S.briefOpen ? "Show less" : "Show full brief"}</button>` : ""}
          ${refs ? `<div class="brief-refs">${refs}</div>` : ""}
        </div>
      </div>
    `;

    const toggle = $("brief-toggle-btn");
    if (toggle) {
      toggle.onclick = () => {
        S.briefOpen = !S.briefOpen;
        renderBriefBubble();
      };
    }
  }

  function renderComposer() {
    const input = $("composer-input");
    const send = $("composer-send");
    const note = $("composer-note");
    const form = $("composer-form");

    if (!S.jobId) {
      // Home empty state
      form.classList.remove("disabled");
      input.disabled = false;
      send.disabled = false;
      input.placeholder = "Describe your film. e.g. “A 30-second ad for a hand-thrown ceramic coffee mug, morning light, slow and tactile…”";
      renderLimits();
      return;
    }

    // Job route
    const job = S.job;
    if (!job) return;

    const sample = isSample();
    const busy = BUSY.has(job.status);
    const unfinished = job.status === "failed" && !job.final_video_url;

    input.disabled = sample || busy || unfinished;
    send.disabled = sample || busy || unfinished;
    form.classList.toggle("disabled", sample || busy || unfinished);

    if (sample) {
      input.placeholder = "Sample productions are read-only";
      note.innerHTML = `This is a finished sample, kept read-only. <a href="/" id="start-own">Start your own production →</a>`;
    } else if (busy) {
      input.placeholder = "The director agent is working…";
      note.textContent = "You can direct changes once this cut is finished.";
    } else if (unfinished) {
      input.placeholder = "Resume the production first";
      note.textContent = "This production stopped before the first cut. Resume it above.";
    } else {
      input.placeholder = "Direct a change… e.g. “Make scene 2 warmer”";
      note.textContent = "The agent re-shoots only the shots your note affects and cuts a new version.";
    }
  }

  // =========================================================
  // Thread & Thinking Block
  // =========================================================
  const allShots = () => (S.job && S.job.plan ? S.job.plan.scenes.flatMap((sc) => sc.shots) : []);

  function shotState(shot) {
    const t = S.takes[shot.id] || {};
    const n = (t.takes || shot.attempts || []).length;
    switch (shot.status) {
      case "pending": return { cls: "wait", label: "queued", pill: "" };
      case "generating": return { cls: "run", label: n ? `rendering take ${n + 1}` : "rendering", pill: "run" };
      case "critiquing": return { cls: "run", label: "critic reviewing", pill: "run" };
      case "retrying": return { cls: "run", label: "reshooting", pill: "run" };
      case "failed": return { cls: "bad", label: "failed", pill: "bad" };
      default:
        if (shot.is_flagged_fallback) return { cls: "flag", label: n > 1 ? `best of ${n} · flagged` : "accepted (flagged)", pill: "warn" };
        return { cls: "pass", label: n > 1 ? `passed on take ${t.accepted_attempt || n}` : "passed", pill: "ok" };
    }
  }

  function acceptedTake(shotId) {
    const t = S.takes[shotId];
    if (!t || !t.takes) return null;
    return t.takes.find((x) => x.accepted) || null;
  }

  function shotThumb(shot) {
    const t = S.takes[shot.id];
    const acc = acceptedTake(shot.id);
    return (acc && acc.image_url) || (t && t.image_url) || S.liveImages[shot.id] ||
      (t && t.takes && t.takes.length ? t.takes[t.takes.length - 1].image_url : null) || (t && t.first_frame_url) || null;
  }

  function loopStats() {
    const shots = allShots();
    let takes = 0, reshoots = 0, firstPass = 0, flagged = 0, scoreSum = 0, scoreN = 0, done = 0;
    shots.forEach((s) => {
      const t = S.takes[s.id];
      const list = (t && t.takes) || [];
      takes += list.length;
      reshoots += Math.max(0, list.length - 1);
      if (["passed", "ready"].includes(s.status)) {
        done++;
        if (s.is_flagged_fallback) flagged++;
        else if (list.length <= 1) firstPass++;
        const acc = list.find((x) => x.accepted);
        if (acc && acc.critique) { scoreSum += acc.critique.score; scoreN++; }
      }
    });
    const runtime = shots.reduce((a, s) => a + s.duration_s, 0);
    return { shots: shots.length, takes, reshoots, firstPass, flagged, done, avg: scoreN ? scoreSum / scoreN : null, runtime };
  }

  function threadSkeleton() {
    return `
      <div class="msg"><span class="avatar avatar-agent">${ICON.play}</span><div class="msg-body">
        <div class="skel skel-line w40"></div><div class="skel skel-line w90"></div><div class="skel skel-line w75"></div></div></div>
      <div class="msg"><span class="avatar avatar-agent">${ICON.play}</span><div class="msg-body"><div class="skel" style="height:110px;border-radius:12px"></div></div></div>`;
  }

  function liveText() {
    if (S.live) return S.live.message;
    const j = S.job;
    return (
      {
        queued: "Queued…",
        planning: "Writing the script & shot plan…",
        planned: "Plan approved, locking visual bible…",
        generating: "Rendering keyframes with Veo 3.1…",
        critiquing: "Critic reviewing takes…",
        assembling: "Assembling final cut…",
      }[j.status] || "Working…"
    );
  }

  // Build the vertical timeline content inside the thinking block
  function buildTimelineContent() {
    const job = S.job;
    const plan = job.plan;
    if (!plan) {
      return `
        <div class="timeline-step active" data-step="plan">
          <span class="step-dot"></span>
          <div class="step-card">
            <div class="sc-head"><div class="sc-head-left"><span class="sc-icon">${ICON.plan}</span><span>Writing the plan</span></div><span class="pill run">thinking</span></div>
            <div class="sc-body"><div class="skel skel-line w90"></div><div class="skel skel-line w60"></div></div>
          </div>
        </div>
      `;
    }

    const shots = allShots();
    const runtime = shots.reduce((a, s) => a + s.duration_s, 0);
    const bible = plan.bible || {};
    const style = bible.style || {};
    const st = loopStats();
    const busy = BUSY.has(job.status);

    // a. Plan Step
    const planShotlistHtml = S.showPlanShotlist
      ? `<div class="plan-shotlist">
          ${plan.scenes
            .map(
              (sc) => `
            <div class="plan-scene-group">
              <div class="psg-title"><span>Scene ${sc.scene_number}</span> ${esc(sc.title)}</div>
              ${sc.shots
                .map((s) => `<div class="psg-shot"><span class="ps-id">${esc(shortId(s.id))}</span><span class="ps-dur">${fmtDur(s.duration_s)}</span><span>${esc(s.description)}</span></div>`)
                .join("")}
            </div>`
            )
            .join("")}
        </div>`
      : "";

    const planStep = `
      <div class="timeline-step done" data-step="plan">
        <span class="step-dot"></span>
        <div class="step-card">
          <div class="sc-head">
            <div class="sc-head-left"><span class="sc-icon">${ICON.plan}</span><span>Plan</span><span class="sc-model-tag">gemini-3.8-flash</span></div>
            <span class="pill ok">done</span>
          </div>
          <div class="sc-body">
            <div class="plan-hero-title">${esc(plan.title)}</div>
            <div class="plan-hero-logline">${esc(plan.logline)}</div>
            <div class="plan-pills-row">
              <span class="pill">${plan.scenes.length} scenes</span>
              <span class="pill">${shots.length} shots</span>
              <span class="pill">${fmtDur(runtime)} runtime</span>
              <button type="button" class="plan-shotlist-toggle" data-action="toggle-shotlist">
                ${S.showPlanShotlist ? "Hide shot list ▴" : "Shot list ▸"}
              </button>
            </div>
            ${planShotlistHtml}
          </div>
        </div>
      </div>
    `;

    // b. Style Bible Step
    const palette = style.palette || [];
    const swatchesHtml = palette.length
      ? `<div class="bible-palette">
          ${palette
            .map(
              (hex) => `
            <div class="palette-swatch-box" title="${esc(hex)}">
              <span class="palette-swatch-color" style="background:${esc(hex)}"></span>
              <span class="palette-swatch-hex">${esc(hex)}</span>
            </div>`
            )
            .join("")}
        </div>`
      : "";

    const refCards = [
      ...(bible.characters || []).map((c) => ({
        name: c.name,
        kind: "Character",
        desc: c.appearance,
        url: (job.character_refs || {})[c.name] || c.reference_image,
      })),
      ...(bible.products || []).map((p) => ({
        name: p.name,
        kind: "Product",
        desc: p.description,
        url: (job.product_refs || {})[p.name] || p.reference_image,
      })),
    ];

    const refsHtml = refCards.length
      ? `<div class="bible-refs-grid">
          ${refCards
            .map(
              (r) => `
            <div class="bible-ref-card">
              ${r.url ? `<img src="${esc(r.url)}" alt="" loading="lazy">` : '<div class="skel" style="width:44px;height:44px;border-radius:6px;flex:none"></div>'}
              <div class="bible-ref-info">
                <div class="bible-ref-name">${esc(r.name)} <span class="tag-ref-kind" style="font-size:10px">${esc(r.kind)}</span></div>
                <div class="bible-ref-desc">${esc(r.desc || "")}</div>
              </div>
            </div>`
            )
            .join("")}
        </div>`
      : "";

    const bibleStep = `
      <div class="timeline-step done" data-step="bible">
        <span class="step-dot"></span>
        <div class="step-card">
          <div class="sc-head">
            <div class="sc-head-left"><span class="sc-icon">${ICON.bible}</span><span>Style bible</span><span class="sc-model-tag">nano-banana</span></div>
            <span class="pill ok">done</span>
          </div>
          <div class="sc-body">
            ${swatchesHtml}
            <div class="bible-spec-grid">
              <div class="bible-spec-item"><b>Mood:</b> <span>${esc(style.mood || "–")}</span></div>
              <div class="bible-spec-item"><b>Lighting:</b> <span>${esc(style.lighting || "–")}</span></div>
              <div class="bible-spec-item"><b>Lens:</b> <span>${esc(style.lens || "–")}</span></div>
            </div>
            ${refsHtml}
          </div>
        </div>
      </div>
    `;

    // c. Scenes & Shots
    const scenesHtml = plan.scenes
      .map((sc) => {
        const shotsHtml = sc.shots
          .map((shot) => {
            const t = S.takes[shot.id] || { takes: [] };
            const takes = t.takes || [];
            const st = shotState(shot);
            const thumb = shotThumb(shot);
            const acc = takes.find((x) => x.accepted);
            const running = st.cls === "run";
            const headScore = acc && acc.critique ? scoreHtml(acc.critique.score) : "";

            const showFlow = takes.length > 1 || shot.is_flagged_fallback || running || (takes.length === 1 && !acc);

            let flowHtml = "";
            if (showFlow) {
              flowHtml = takes
                .map((tk, i) => {
                  const c = tk.critique;
                  const verdict = tk.accepted
                    ? c && c.passed
                      ? `<span class="pill ok">accepted</span>`
                      : `<span class="pill warn">best of ${takes.length} · flagged</span>`
                    : c && !c.passed
                    ? `<span class="pill bad">rejected</span>`
                    : c
                    ? `<span class="pill">passed</span>`
                    : `<span class="pill run">reviewing</span>`;

                  const reasonsList =
                    c && c.reasons && c.reasons.length
                      ? `<div class="take-reason-text">${reasonHtml(c.reasons[0])}</div>`
                      : "";

                  const next = takes[i + 1];
                  const correction =
                    c && !c.passed && (next || running)
                      ? `<div class="correction-box"><b>Correction applied →</b> <span>${esc(
                          c.suggested_prompt_fix || "Prompt revised from critic notes."
                        )}</span></div>`
                      : "";

                  return `
                    <div class="take-entry ${tk.accepted ? "" : "rejected"}">
                      <button type="button" class="take-thumb-chip" data-shot="${esc(shot.id)}" data-take="${tk.attempt}" title="Inspect Take ${tk.attempt}">
                        ${tk.image_url ? `<img src="${esc(tk.image_url)}" alt="" loading="lazy">` : '<div class="skel" style="width:100%;height:100%"></div>'}
                        <span class="play-overlay">${ICON.play}</span>
                      </button>
                      <div class="take-info-col">
                        <div class="take-headline">
                          <span>Take ${tk.attempt}</span>
                          ${c ? scoreHtml(c.score) : ""}
                          ${verdict}
                        </div>
                        ${reasonsList}
                      </div>
                    </div>
                    ${correction}
                  `;
                })
                .join("");

              if (running) {
                const img = S.liveImages[shot.id];
                flowHtml += `
                  <div class="take-entry running">
                    <div class="take-thumb-chip">
                      ${img ? `<img src="${esc(img)}" alt="">` : '<div class="skel" style="width:100%;height:100%"></div>'}
                    </div>
                    <div class="take-info-col">
                      <div class="take-headline">
                        <span>Take ${takes.length + 1}</span>
                        <span class="pill run">${esc(st.label)}</span>
                      </div>
                      <div class="take-reason-text">Veo 3.1 is rendering this take; critic reviews next.</div>
                    </div>
                  </div>
                `;
              }
            }

            return `
              <div class="shot-row-card" data-shot-card="${esc(shot.id)}">
                <div class="shot-row-head">
                  <button type="button" class="shot-thumb-btn" data-shot="${esc(shot.id)}" data-take="${acc ? acc.attempt : 1}" title="View take media">
                    ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : '<div class="skel" style="width:100%;height:100%"></div>'}
                    <span class="play-overlay">${ICON.play}</span>
                  </button>
                  <div class="shot-meta-col">
                    <div class="shot-top-line">
                      <span class="shot-id-tag">${esc(shortId(shot.id))}</span>
                      <span class="shot-desc-text">${esc(shot.description)}</span>
                    </div>
                    <div class="shot-sub-line">
                      <span class="sdot ${st.cls}"></span>
                      <span class="shot-status-text">${esc(st.label)}</span>
                      <span>·</span>
                      <span class="shot-dur-text">${fmtDur(shot.duration_s)}</span>
                      <span>·</span>
                      <span class="shot-camera-text">${esc(shot.camera)}</span>
                    </div>
                  </div>
                  ${headScore}
                </div>
                ${showFlow ? `<div class="take-flow">${flowHtml}</div>` : ""}
              </div>
            `;
          })
          .join("");

        return `
          <div class="scene-section">
            <div class="scene-heading">
              <span class="scene-badge">Scene ${sc.scene_number}</span>
              <span class="scene-title">${esc(sc.title)}</span>
            </div>
            ${shotsHtml}
          </div>
        `;
      })
      .join("");

    const scenesStep = `
      <div class="timeline-step done" data-step="scenes">
        <span class="step-dot"></span>
        <div class="step-card">
          <div class="sc-head">
            <div class="sc-head-left"><span class="sc-icon">${ICON.film}</span><span>Scenes &amp; Shot Generations</span><span class="sc-model-tag">veo-3.1 &amp; gemini-critic</span></div>
            <span class="pill ${st.done === st.shots ? "ok" : "run"}">${st.done === st.shots ? "completed" : `${st.done}/${st.shots} shots`}</span>
          </div>
          <div class="sc-body">
            ${scenesHtml}
          </div>
        </div>
      </div>
    `;

    // d. Critic loop summary
    let criticStep = "";
    if (st.takes && st.done === st.shots) {
      criticStep = `
        <div class="timeline-step done" data-step="critic">
          <span class="step-dot"></span>
          <div class="step-card">
            <div class="sc-head">
              <div class="sc-head-left"><span class="sc-icon">${ICON.critic}</span><span>Critic loop summary</span><span class="sc-model-tag">gemini-3.8-flash</span></div>
              <span class="pill ok">verified</span>
            </div>
            <div class="sc-body">
              <div class="cs-grid">
                <div class="cs-stat"><b>${st.takes}</b><span>takes reviewed</span></div>
                <div class="cs-stat"><b>${st.reshoots}</b><span>critic reshoots</span></div>
                <div class="cs-stat"><b>${st.firstPass}</b><span>passed first try</span></div>
                <div class="cs-stat"><b>${st.avg != null ? st.avg.toFixed(2) : "–"}</b><span>avg accepted score</span></div>
              </div>
              ${st.flagged ? `<div class="cs-flagged-note">⚠️ ${st.flagged} shot(s) never cleared 0.70; kept best take and flagged.</div>` : ""}
            </div>
          </div>
        </div>
      `;
    }

    // e. Voiceover & music
    let audioStep = "";
    if (job.soundtrack_url || shots.some((s) => s.vo_line)) {
      const voCount = shots.filter((s) => s.vo_line).length;
      const ready = Boolean(job.soundtrack_url);
      audioStep = `
        <div class="timeline-step ${ready ? "done" : "active"}" data-step="audio">
          <span class="step-dot"></span>
          <div class="step-card">
            <div class="sc-head">
              <div class="sc-head-left"><span class="sc-icon">${ICON.music}</span><span>${ready ? "Scored soundtrack & voiced narration" : "Scoring & voicing"}</span><span class="sc-model-tag">lyria-3 &amp; gemini-tts</span></div>
              <span class="pill ${ready ? "ok" : "run"}">${ready ? "done" : "queued"}</span>
            </div>
            <div class="sc-body">
              <div class="as-body">
                <div class="as-info">${voCount} voiceover line${voCount === 1 ? "" : "s"} · Mood: ${esc(style.mood || "–")}</div>
                ${ready ? `<div class="as-player"><audio controls preload="none" src="${esc(job.soundtrack_url)}"></audio></div>` : ""}
              </div>
            </div>
          </div>
        </div>
      `;
    }

    // f. Assembly
    let assemblyStep = "";
    const v1 = (job.versions || [])[0];
    if (v1 || job.status === "completed") {
      assemblyStep = `
        <div class="timeline-step done" data-step="assembly">
          <span class="step-dot"></span>
          <div class="step-card">
            <div class="sc-head">
              <div class="sc-head-left"><span class="sc-icon">${ICON.cut}</span><span>Assembled the master cut</span><span class="sc-model-tag">ffmpeg</span></div>
              <span class="pill ok">v1 ready</span>
            </div>
            <div class="sc-body">
              <div class="asm-body">
                <span>${fmtDur(runtime)} runtime</span> · <span>${shots.length} clips</span> · <span>Captions burned in</span> · <span>VO ducked over score</span>
              </div>
            </div>
          </div>
        </div>
      `;
    }

    return planStep + bibleStep + scenesStep + criticStep + audioStep + assemblyStep;
  }

  function buildThread() {
    const job = S.job;
    if (!job) return [];
    const items = [];
    const busy = BUSY.has(job.status);
    const shots = allShots();
    const st = loopStats();

    // 1. Agent intro
    items.push({
      key: "intro",
      cls: "msg msg-agent",
      html: `
        <span class="avatar avatar-agent">${ICON.play}</span>
        <div class="msg-body">
          <div class="msg-meta">
            <span class="msg-who">Director agent</span>
            <span class="msg-time">${esc(fmtTime(job.created_at))}</span>
          </div>
          <div class="msg-text">
            I'm on it. I'll write the script &amp; shot blueprint, establish the visual bible, animate keyframes with Veo 3.1, and have the Gemini critic review and reshoot takes until approved.
          </div>
        </div>
      `,
    });

    // 2. The Thinking Block
    let headerStatusHtml = "";
    if (busy) {
      headerStatusHtml = `
        <span class="tb-status-icon"><span class="spinner"></span></span>
        <span class="tb-title">Director is working… ${esc(liveText())}</span>
      `;
    } else {
      const elapsed = getElapsedTime(job);
      const reshoots = Math.max(0, st.takes - shots.length);
      headerStatusHtml = `
        <span class="tb-status-icon"><span class="check-icon">${ICON.check}</span></span>
        <span class="tb-title">Worked for ${elapsed || "a few minutes"} · ${shots.length || (job.plan && job.plan.scenes ? allShots().length : "–")} shots · ${st.takes} takes · ${reshoots} reshoots</span>
      `;
    }

    const openClass = S.thinkingExpanded ? "open" : "";
    const thinkingBlockHtml = `
      <div class="thinking-block ${openClass}" id="thinking-block">
        <div class="tb-header" id="tb-header">
          <div class="tb-header-left">${headerStatusHtml}</div>
          <div class="tb-chevron">${ICON.chev}</div>
        </div>
        <div class="tb-body" id="tb-body">
          <div class="timeline">
            ${buildTimelineContent()}
          </div>
        </div>
      </div>
    `;

    items.push({
      key: "thinking",
      cls: "msg msg-indent",
      html: thinkingBlockHtml,
    });

    // 3. Final Cut Card (when version 1 exists)
    const v1 = (job.versions || [])[0];
    if (v1 && (v1.url || job.final_video_url)) {
      const poster = v1.poster_url || job.poster_url || "";
      const runtime = shots.reduce((a, s) => a + s.duration_s, 0);
      const title = (job.plan && job.plan.title) || "Master cut";

      items.push({
        key: "final-cut",
        cls: "msg msg-indent",
        html: `
          <div class="final-cut-card" data-action="open-final-cut" data-version="1">
            <div class="fcc-media">
              <img src="${esc(poster)}" alt="" loading="lazy">
              <span class="fcc-play-overlay">${ICON.play}</span>
            </div>
            <div class="fcc-details">
              <div class="fcc-title-row">
                <span class="fcc-title">${esc(title)}</span>
                <span class="pill ok">v1 ready</span>
              </div>
              <div class="fcc-meta">${fmtDur(runtime)} runtime · ${shots.length} clips · captions burned in</div>
              <button type="button" class="btn ${S.inFinalMode ? "btn-ghost" : "btn-primary"} btn-sm fcc-btn" data-action="open-final-cut" data-version="1">
                ${S.inFinalMode ? `${ICON.check} Viewing in panel` : `${ICON.play} Open final cut`}
              </button>
            </div>
          </div>
        `,
      });
    }

    // 4. Director notes history (messages and versions ≥ 2)
    const later = [];
    (job.messages || []).forEach((m, i) => later.push({ t: m.ts, kind: "msg", m, i }));
    (job.versions || []).slice(1).forEach((v) => later.push({ t: v.created_at, kind: "ver", v }));
    later.sort((a, b) => String(a.t).localeCompare(String(b.t)));

    later.forEach((x) => {
      if (x.kind === "msg") {
        if (x.m.role === "user") {
          items.push({
            key: `m-${x.i}`,
            cls: "msg msg-user",
            html: `
              <span class="avatar avatar-user">You</span>
              <div class="msg-body">
                <div class="msg-meta">
                  <span class="msg-who">Director note</span>
                  <span class="msg-time">${esc(fmtTime(x.m.ts))}</span>
                </div>
                <div class="msg-text">${esc(x.m.text)}</div>
              </div>
            `,
          });
        } else {
          // Parse "Re-shooting shot(s): a, b." into chips
          const m = /Re-shooting shot\(s\):\s*([^.]*)\.?/i.exec(x.m.text || "");
          const ids = m ? m[1].split(",").map((s) => s.trim()).filter(Boolean) : [];
          const text = m ? x.m.text.replace(m[0], "").trim() : x.m.text;
          const chips = ids.length
            ? `<div class="regen-chips"><span>Regenerating:</span>${ids
                .map((s) => `<code data-shot="${esc(s)}">${esc(shortId(s))}</code>`)
                .join("")}</div>`
            : "";

          items.push({
            key: `m-${x.i}`,
            cls: "msg msg-agent",
            html: `
              <span class="avatar avatar-agent">${ICON.play}</span>
              <div class="msg-body">
                <div class="msg-meta">
                  <span class="msg-who">Director agent</span>
                  <span class="msg-time">${esc(fmtTime(x.m.ts))}</span>
                </div>
                <div class="msg-text">${esc(text)}</div>
                ${chips}
              </div>
            `,
          });
        }
      } else {
        // Version card (v2, v3...)
        const changed = (x.v.changed_shots || [])
          .map((s) => `<code data-shot="${esc(s)}">${esc(shortId(s))}</code>`)
          .join("");

        items.push({
          key: `v-${x.v.version}`,
          cls: "msg msg-indent",
          html: `
            <div class="version-card">
              <div class="vc-media" data-action="open-final-cut" data-version="${x.v.version}">
                <img src="${esc(x.v.poster_url || "")}" alt="">
              </div>
              <div class="vc-details">
                <div class="vc-head">
                  <span>Cut v${x.v.version} ready</span>
                  <span class="pill ok">v${x.v.version}</span>
                </div>
                <div class="vc-msg">${esc(x.v.message || "")}</div>
                ${changed ? `<div class="regen-chips"><span>Re-shot:</span>${changed}</div>` : ""}
              </div>
              <button type="button" class="btn btn-ghost btn-sm" data-action="open-final-cut" data-version="${x.v.version}">
                ${S.inFinalMode && S.activeVersion === x.v.version ? "Viewing in panel" : "Open"}
              </button>
            </div>
          `,
        });
      }
    });

    // 5. Local notes & live refinement block
    S.localNotes.forEach((n, i) =>
      items.push({
        key: `local-${i}`,
        cls: "msg msg-agent",
        html: `
          <span class="avatar avatar-agent">${ICON.play}</span>
          <div class="msg-body"><div class="msg-text">${esc(n.text)}</div></div>
        `,
      })
    );

    // Live refinement working block
    if (busy && job.messages && job.messages.length) {
      const targets = (S.editTargets || [])
        .map((s) => `<code data-shot="${esc(s)}">${esc(shortId(s))}</code>`)
        .join("");

      items.push({
        key: "live-refinement",
        cls: "msg msg-indent",
        html: `
          <div class="live-refine-block">
            <span class="spinner"></span>
            <span>Director is updating the film… ${targets ? `Regenerating ${targets}` : esc(liveText())}</span>
          </div>
        `,
      });
    }

    // 6. Failed job error card
    if (job.status === "failed") {
      const canResume = !isSample();
      items.push({
        key: "error",
        cls: "msg msg-indent",
        html: `
          <div class="error-card">
            <b>Production paused</b>
            <div style="margin-top:4px;color:var(--text-2);font-size:12.5px">${esc(job.error || "The pipeline stopped.")}</div>
            ${canResume ? `<div style="margin-top:8px"><button class="btn btn-primary btn-sm" data-action="resume" type="button">Resume production</button></div>` : ""}
          </div>
        `,
      });
    }

    return items;
  }

  // Keyed DOM reconciliation
  function renderThread() {
    const container = $("thread");
    const items = buildThread();
    if (S.firstRender) container.innerHTML = "";

    const existing = new Map();
    [...container.children].forEach((el) => el.dataset.key && existing.set(el.dataset.key, el));
    const keep = new Set();

    items.forEach((it, idx) => {
      keep.add(it.key);
      let el = existing.get(it.key);
      if (!el) {
        el = document.createElement("div");
        el.dataset.key = it.key;
        el.className = `${it.cls} enter`;
        el.innerHTML = it.html;
        el._html = it.html;
      } else if (el._html !== it.html) {
        // Preserve audio playing state if src is unchanged
        const oldAudio = el.querySelector("audio");
        el.innerHTML = it.html;
        el._html = it.html;
        el.className = it.cls;
        if (oldAudio && !oldAudio.paused) {
          const newAudio = el.querySelector("audio");
          if (newAudio && newAudio.src === oldAudio.src) {
            newAudio.currentTime = oldAudio.currentTime;
            newAudio.play().catch(() => {});
          }
        }
      }
      if (container.children[idx] !== el) container.insertBefore(el, container.children[idx] || null);
    });

    existing.forEach((el, key) => !keep.has(key) && el.remove());

    // Wire thinking block toggle
    const tbHeader = $("tb-header");
    if (tbHeader) {
      tbHeader.onclick = () => {
        S.thinkingExpanded = !S.thinkingExpanded;
        S.userToggledThinking = true;
        const tb = $("thinking-block");
        if (tb) tb.classList.toggle("open", S.thinkingExpanded);
      };
    }
  }

  // =========================================================
  // Lightbox Modal Player
  // =========================================================
  function openTakeModal(shotId, takeAttempt) {
    const shotData = S.takes[shotId];
    if (!shotData) return;

    const takes = shotData.takes || [];
    const take = takes.find((t) => t.attempt === Number(takeAttempt)) || takes[0];
    if (!take) return;

    const planShot = allShots().find((s) => s.id === shotId);

    $("modal-title").textContent = `Shot ${shortId(shotId)} · Take ${take.attempt}`;

    const videoEl = $("modal-video");
    const imgEl = $("modal-image");

    if (take.clip_available && take.clip_url) {
      imgEl.classList.add("hidden");
      videoEl.classList.remove("hidden");
      videoEl.src = take.clip_url;
      if (take.image_url) videoEl.poster = take.image_url;
      videoEl.load();
      videoEl.play().catch(() => {});
    } else if (take.image_url) {
      videoEl.pause();
      videoEl.classList.add("hidden");
      imgEl.classList.remove("hidden");
      imgEl.src = take.image_url;
    } else {
      videoEl.pause();
      videoEl.classList.add("hidden");
      imgEl.classList.add("hidden");
    }

    const c = take.critique;
    const verdict = take.accepted
      ? c && c.passed
        ? `<span class="pill ok">Accepted · passed</span>`
        : `<span class="pill warn">Accepted · best of ${takes.length}</span>`
      : c && !c.passed
      ? `<span class="pill bad">Rejected</span>`
      : `<span class="pill run">Reviewing</span>`;

    const reasonsList =
      c && c.reasons && c.reasons.length
        ? `<ul class="md-reasons">${c.reasons.map((r) => `<li>${reasonHtml(r)}</li>`).join("")}</ul>`
        : "";

    const fixHtml =
      c && c.suggested_prompt_fix
        ? `<div class="correction-box" style="margin:4px 0"><b>Prompt fix:</b> <span>${esc(c.suggested_prompt_fix)}</span></div>`
        : "";

    $("modal-details").innerHTML = `
      <div class="md-row">
        ${c ? scoreHtml(c.score) : ""}
        ${verdict}
        <span class="pill">${planShot ? fmtDur(planShot.duration_s) : "–"}</span>
        <span class="pill mono">${esc(shotId)}</span>
      </div>
      ${reasonsList}
      ${fixHtml}
      ${planShot ? `<div style="font-size:12.5px;color:var(--text)"><b>Action:</b> ${esc(planShot.description)}</div>` : ""}
      ${planShot && planShot.camera ? `<div style="font-size:12.5px;color:var(--muted)"><b>Camera:</b> ${esc(planShot.camera)}</div>` : ""}
      ${take.prompt ? `<div class="md-prompt-box"><b>Visual prompt:</b> ${esc(take.prompt)}</div>` : ""}
    `;

    $("take-modal").classList.remove("hidden");
  }

  function openVideoModal(url, poster, title) {
    $("modal-title").textContent = title || "Final cut preview";
    const videoEl = $("modal-video");
    const imgEl = $("modal-image");

    imgEl.classList.add("hidden");
    videoEl.classList.remove("hidden");
    videoEl.src = url;
    if (poster) videoEl.poster = poster;
    videoEl.load();
    videoEl.play().catch(() => {});

    $("modal-details").innerHTML = `
      <div class="md-row">
        <span class="pill ok">Master cut</span>
        <span class="pill">Burned-in captions</span>
      </div>
    `;

    $("take-modal").classList.remove("hidden");
  }

  function closeModal() {
    const videoEl = $("modal-video");
    if (videoEl) {
      videoEl.pause();
      videoEl.removeAttribute("src");
      videoEl.load();
    }
    $("take-modal").classList.add("hidden");
  }

  $("modal-close").addEventListener("click", closeModal);
  $("modal-backdrop").addEventListener("click", closeModal);

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("take-modal").classList.contains("hidden")) {
      closeModal();
    }
  });

  // =========================================================
  // Final Cut Mode & Inspector Panel (Step 3)
  // =========================================================
  function openFinalCut(opts = {}) {
    const job = S.job;
    if (!job) return;

    let versionNum = 1;
    if (typeof opts === "number") {
      versionNum = opts;
    } else if (opts.version != null) {
      versionNum = Number(opts.version);
    } else if (job.versions && job.versions.length) {
      versionNum = job.versions[job.versions.length - 1].version;
    }

    const shotId = opts.shot || null;
    const takeAttempt = opts.take != null ? Number(opts.take) : null;

    S.inFinalMode = true;
    S.activeVersion = versionNum;
    if (shotId) S.selectedShotId = shotId;
    if (takeAttempt != null) S.selectedTakeAttempt = takeAttempt;

    const layout = $("app-layout");
    layout.classList.add("final-mode");
    $("final-panel").classList.remove("hidden");

    if (opts.pushState !== false) {
      const q = new URLSearchParams(location.search);
      q.set("job", S.jobId);
      if (shotId) {
        q.set("shot", shotId);
        if (takeAttempt != null) q.set("take", String(takeAttempt));
        q.delete("cut");
      } else {
        q.set("cut", "1");
        q.delete("shot");
        q.delete("take");
      }
      history.pushState({}, "", `/?${q.toString()}`);
    }

    renderFinalPanel();

    if (shotId && takeAttempt != null) {
      playTakeInPanel(shotId, takeAttempt);
    }
    if (shotId) {
      setTimeout(() => {
        const sd = $("fp-shot-detail");
        if (sd) sd.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 120);
    }
    renderThread();
  }
  window.openFinalCut = openFinalCut;

  function closeFinalCut(pushState = true) {
    S.inFinalMode = false;
    S.playingTake = null;
    S.selectedShotId = null;
    S.selectedTakeAttempt = null;

    const layout = $("app-layout");
    layout.classList.remove("final-mode");
    $("final-panel").classList.add("hidden");

    const video = $("fp-video");
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }

    if (pushState && S.jobId) {
      history.pushState({}, "", `/?job=${S.jobId}`);
    }
    renderThread();
  }

  function renderFinalPanel() {
    const job = S.job;
    if (!job || !S.inFinalMode) return;

    // 1. Header info
    const title = (job.plan && job.plan.title) || job.brief.slice(0, 40) || "Final cut";
    $("fp-title").textContent = title;

    // Version tabs
    const tabsContainer = $("fp-version-tabs");
    const versions = job.versions || [];
    if (versions.length > 1) {
      tabsContainer.innerHTML = versions
        .map((v) => {
          const active = v.version === S.activeVersion ? "active" : "";
          return `<button type="button" class="fp-vtab ${active}" data-v="${v.version}">v${v.version}</button>`;
        })
        .join("");
      tabsContainer.querySelectorAll(".fp-vtab").forEach((btn) => {
        btn.onclick = () => {
          S.activeVersion = Number(btn.dataset.v);
          S.playingTake = null;
          renderFinalPanel();
        };
      });
    } else {
      tabsContainer.innerHTML = "";
    }

    // Active version media
    const curVer = versions.find((v) => v.version === S.activeVersion) || versions[versions.length - 1] || null;
    const finalCutUrl = (curVer && curVer.url) || job.final_video_url || "";
    const posterUrl = (curVer && curVer.poster_url) || job.poster_url || "";

    // Download link
    const dlBtn = $("fp-download-btn");
    dlBtn.href = finalCutUrl;
    dlBtn.download = `${job.id}_v${S.activeVersion}.mp4`;

    // Video player
    const video = $("fp-video");
    if (S.playingTake) {
      const shotData = S.takes[S.playingTake.shotId];
      const tk = shotData && shotData.takes ? shotData.takes.find((x) => x.attempt === S.playingTake.takeAttempt) : null;
      if (tk && tk.clip_url) {
        if (video.src !== tk.clip_url) {
          video.src = tk.clip_url;
          video.loop = true;
          video.poster = tk.image_url || "";
          video.load();
          video.play().catch(() => {});
        }
        const c = tk.critique;
        const scoreStr = c && c.score != null ? c.score.toFixed(2) : "";
        const verdict = tk.accepted ? (c && c.passed ? "accepted" : "accepted (flagged)") : "rejected";
        $("fp-take-badge").textContent = `Shot ${shortId(S.playingTake.shotId)} · Take ${tk.attempt} · ${scoreStr} ${verdict}`;
        $("fp-take-overlay").classList.remove("hidden");
      }
    } else {
      $("fp-take-overlay").classList.add("hidden");
      if (video.src !== finalCutUrl && finalCutUrl) {
        video.src = finalCutUrl;
        video.loop = false;
        if (posterUrl) video.poster = posterUrl;
        video.load();
      }
    }

    // Timings calculation
    const scenes = (job.plan && job.plan.scenes) || [];
    let cumTime = 0;
    const timings = [];
    scenes.forEach((sc) => {
      sc.shots.forEach((s) => {
        timings.push({
          shot: s,
          scene: sc,
          startTime: cumTime,
          duration: s.duration_s,
          endTime: cumTime + s.duration_s,
        });
        cumTime += s.duration_s;
      });
    });
    S.shotTimings = timings;
    S.totalDuration = Math.max(1, cumTime);

    // Timeline scale & dimensions
    const scrollEl = $("fp-timeline-scroll");
    const INSET = 16;
    const availableW = Math.max(300, scrollEl.clientWidth || 860);
    const usableW = Math.max(200, availableW - (2 * INSET));

    // For films ~30-45s, scale to exactly fill the usable width (no horizontal scroll).
    // Only longer films (>50s) use minPxPerSec and scroll horizontally.
    const minPxPerSec = 13;
    const fitScale = usableW / S.totalDuration;
    const fitsWidth = (S.totalDuration <= 50) || (fitScale >= minPxPerSec);
    const scale = fitsWidth ? fitScale : minPxPerSec;
    S.timelineScale = scale;
    S.timelineFitsWidth = fitsWidth;
    S.timelineInset = INSET;

    const trackWidth = Math.round(S.totalDuration * scale);

    const canvas = $("fp-timeline-canvas");
    canvas.style.width = fitsWidth ? "100%" : `${trackWidth + (2 * INSET)}px`;

    // 2. Render Track (Scenes & Clips)
    renderTimelineTrack(scenes, timings, scale, trackWidth);

    // 3. Render Ruler
    renderTimelineRuler(S.totalDuration, scale, trackWidth);

    // 4. Render KPI Line
    const st = loopStats();
    $("fp-kpi-line").textContent = `${fmtDur(st.runtime)} runtime · ${st.shots} shots · ${st.takes} takes rendered · ${st.reshoots} critic reshoots · avg critic ${st.avg ? st.avg.toFixed(2) : "–"}`;

    // 5. Render Shot Detail Strip
    renderShotDetail();
  }

  function renderTimelineTrack(scenes, timings, scale, trackWidth) {
    const track = $("fp-track");
    track.innerHTML = "";

    scenes.forEach((sc) => {
      if (!sc.shots || !sc.shots.length) return;
      const firstTiming = timings.find((t) => t.shot.id === sc.shots[0].id);
      const lastTiming = timings.find((t) => t.shot.id === sc.shots[sc.shots.length - 1].id);
      const scStart = firstTiming ? firstTiming.startTime : 0;
      const scEnd = lastTiming ? lastTiming.endTime : 0;
      const sceneW = Math.round(scEnd * scale) - Math.round(scStart * scale);

      const scBlock = document.createElement("div");
      scBlock.className = "fp-scene-block";
      scBlock.style.width = `${sceneW}px`;
      scBlock.style.flex = `0 0 ${sceneW}px`;

      const label = document.createElement("div");
      label.className = "fp-scene-label";
      label.textContent = `S${sc.scene_number} · ${sc.title}`;
      label.title = `Scene ${sc.scene_number}: ${sc.title}`;
      scBlock.appendChild(label);

      const shotsWrap = document.createElement("div");
      shotsWrap.className = "fp-scene-shots";

      sc.shots.forEach((s) => {
        const item = timings.find((t) => t.shot.id === s.id);
        const sStart = item ? item.startTime : 0;
        const sEnd = item ? item.endTime : s.duration_s;
        const shotW = Math.round(sEnd * scale) - Math.round(sStart * scale);

        const t = S.takes[s.id] || { takes: [] };
        const takes = t.takes || [];
        const thumb = shotThumb(s);
        const st = shotState(s);
        const active = s.id === S.selectedShotId ? "active" : "";

        const tile = document.createElement("div");
        tile.className = `fp-clip-tile ${active}`;
        tile.style.width = `${shotW}px`;
        tile.style.flex = `0 0 ${shotW}px`;
        tile.dataset.shot = s.id;
        tile.title = `Shot ${shortId(s.id)} (${fmtDur(s.duration_s)}) - ${s.description}`;

        const takesBadge = takes.length > 1 ? `<span class="fp-clip-takes-count">${takes.length} takes</span>` : "";

        tile.innerHTML = `
          ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : ""}
          <div class="fp-clip-overlay">
            <div class="fp-clip-id">${esc(shortId(s.id))}</div>
            <div class="fp-clip-badges">
              <span class="fp-clip-sdot ${st.cls}"></span>
              ${takesBadge}
            </div>
          </div>
        `;

        tile.onclick = (e) => {
          e.stopPropagation();
          selectShotInTimeline(s.id);
        };

        shotsWrap.appendChild(tile);
      });

      scBlock.appendChild(shotsWrap);
      track.appendChild(scBlock);
    });
  }

  function renderTimelineRuler(totalDuration, scale, trackWidth) {
    const ruler = $("fp-ruler");
    ruler.innerHTML = "";

    const majorSec = scale >= 18 ? 5 : 10;
    const minorSec = majorSec === 5 ? 1 : 2;

    for (let sec = 0; sec <= totalDuration; sec += minorSec) {
      const isMajor = sec % majorSec === 0 || sec === Math.floor(totalDuration);
      const x = Math.round(sec * scale);
      if (x > trackWidth + 1) break;

      const tick = document.createElement("span");
      tick.className = `fp-ruler-tick ${isMajor ? "major" : ""}`;
      tick.style.left = `${x}px`;
      ruler.appendChild(tick);

      if (isMajor) {
        const lbl = document.createElement("span");
        lbl.className = "fp-ruler-label";
        lbl.style.left = `${x}px`;
        lbl.style.transform = "translateX(-50%)";
        lbl.textContent = fmtRulerTime(sec);
        ruler.appendChild(lbl);
      }
    }

    if (Math.round(totalDuration * scale) > 0) {
      const endX = trackWidth;
      const hasEndTick = ruler.querySelector(`.fp-ruler-tick[style*="${endX}px"]`);
      if (!hasEndTick) {
        const tick = document.createElement("span");
        tick.className = "fp-ruler-tick major";
        tick.style.left = `${endX}px`;
        ruler.appendChild(tick);

        const lbl = document.createElement("span");
        lbl.className = "fp-ruler-label";
        lbl.style.left = `${endX}px`;
        lbl.style.transform = "translateX(-50%)";
        lbl.textContent = fmtRulerTime(totalDuration);
        ruler.appendChild(lbl);
      }
    }
  }

  function fmtRulerTime(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function selectShotInTimeline(shotId) {
    S.selectedShotId = shotId;
    S.selectedTakeAttempt = null;

    document.querySelectorAll(".fp-clip-tile").forEach((tile) => {
      tile.classList.toggle("active", tile.dataset.shot === shotId);
    });

    const item = S.shotTimings.find((t) => t.shot.id === shotId);
    if (item) {
      const video = $("fp-video");
      if (S.playingTake) {
        S.playingTake = null;
        renderFinalPanel();
      }
      video.currentTime = item.startTime;
      video.play().catch(() => {});
    }

    const q = new URLSearchParams(location.search);
    q.set("job", S.jobId);
    q.set("shot", shotId);
    q.delete("cut");
    q.delete("take");
    history.replaceState({}, "", `/?${q.toString()}`);

    renderShotDetail();
    const sd = $("fp-shot-detail");
    if (sd) {
      setTimeout(() => {
        sd.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 50);
    }
  }

  function renderShotDetail() {
    const sd = $("fp-shot-detail");
    const hint = $("fp-hint-line");
    if (!S.selectedShotId) {
      sd.classList.add("hidden");
      sd.innerHTML = "";
      if (hint) hint.classList.remove("hidden");
      return;
    }
    if (hint) hint.classList.add("hidden");

    const timing = S.shotTimings.find((t) => t.shot.id === S.selectedShotId);
    const planShot = timing ? timing.shot : allShots().find((s) => s.id === S.selectedShotId);
    const scene = timing ? timing.scene : null;
    const shotData = S.takes[S.selectedShotId] || { takes: [] };
    const takes = shotData.takes || [];

    if (!planShot) {
      sd.classList.add("hidden");
      if (hint) hint.classList.remove("hidden");
      return;
    }

    const st = shotState(planShot);

    const sceneLabel = scene ? `Scene ${scene.scene_number} · Shot ${shortId(planShot.id)}` : `Shot ${shortId(planShot.id)}`;
    const headHtml = `
      <div class="fp-sd-header">
        <div class="fp-sd-title">
          <span>${esc(sceneLabel)}</span>
          <span class="pill mono">${esc(planShot.id)}</span>
          <span class="pill ${st.pill}">${esc(st.label)}</span>
          <span class="pill">${fmtDur(planShot.duration_s)}</span>
        </div>
        <button type="button" class="fp-sd-close" id="fp-sd-close" aria-label="Close shot detail">×</button>
      </div>
    `;

    const takesHtml = takes.length
      ? takes
          .map((tk) => {
            const c = tk.critique;
            const isPlaying = S.playingTake && S.playingTake.shotId === planShot.id && S.playingTake.takeAttempt === tk.attempt;
            const isAccepted = tk.accepted;
            const verdict = tk.accepted
              ? c && c.passed
                ? `<span class="pill ok">Accepted · passed</span>`
                : `<span class="pill warn">Accepted · best of ${takes.length}</span>`
              : c && !c.passed
              ? `<span class="pill bad">Rejected</span>`
              : `<span class="pill run">Reviewing</span>`;

            const reasonsHtml =
              c && c.reasons && c.reasons.length
                ? `<div class="fp-tc-reasons">${c.reasons.map((r) => `<div>${reasonHtml(r)}</div>`).join("")}</div>`
                : "";

            const correctionHtml =
              c && !c.passed && c.suggested_prompt_fix
                ? `<div class="fp-tc-correction"><b>Correction applied →</b> <span>${esc(c.suggested_prompt_fix)}</span></div>`
                : "";

            return `
              <div class="fp-take-card ${isAccepted ? "accepted" : ""} ${isPlaying ? "playing" : ""}" data-shot="${esc(planShot.id)}" data-take="${tk.attempt}">
                <div class="fp-tc-thumb">
                  ${tk.image_url ? `<img src="${esc(tk.image_url)}" alt="">` : ""}
                  <span class="play-overlay">${ICON.play}</span>
                </div>
                <div class="fp-tc-head">
                  <div class="fp-tc-head-left">
                    <span>Take ${tk.attempt}</span>
                    ${c ? scoreHtml(c.score) : ""}
                  </div>
                  ${verdict}
                </div>
                ${reasonsHtml}
                ${correctionHtml}
              </div>
            `;
          })
          .join("")
      : `<div style="font-size:12px;color:var(--muted)">No takes recorded yet.</div>`;

    const voHtml = planShot.vo_line
      ? `<div class="fp-sd-vo">“${esc(planShot.vo_line)}”</div>`
      : "";

    const acceptedTakeObj = takes.find((t) => t.accepted) || takes[takes.length - 1];
    const finalPrompt = (acceptedTakeObj && acceptedTakeObj.prompt) || planShot.visual_prompt || "";

    const promptHtml = finalPrompt
      ? `
        <div>
          <button type="button" class="fp-sd-prompt-toggle" id="fp-prompt-toggle">
            ${S.expandedVeoPrompt ? "Hide final Veo prompt ▴" : "Show final Veo prompt ▾"}
          </button>
          ${S.expandedVeoPrompt ? `<div class="fp-sd-prompt-box">${esc(finalPrompt)}</div>` : ""}
        </div>
      `
      : "";

    sd.innerHTML = `
      ${headHtml}
      <div class="fp-sd-body">
        <div class="fp-sd-takes-grid">
          ${takesHtml}
        </div>
        <div class="fp-sd-meta">
          <div><b>Action:</b> ${esc(planShot.description)}</div>
          ${planShot.camera ? `<div><b>Camera:</b> ${esc(planShot.camera)}</div>` : ""}
          ${voHtml}
          ${promptHtml}
        </div>
      </div>
    `;

    sd.classList.remove("hidden");

    const closeBtn = $("fp-sd-close");
    if (closeBtn) {
      closeBtn.onclick = () => {
        S.selectedShotId = null;
        renderShotDetail();
        document.querySelectorAll(".fp-clip-tile.active").forEach((t) => t.classList.remove("active"));
        const q = new URLSearchParams(location.search);
        q.set("cut", "1");
        q.delete("shot");
        q.delete("take");
        history.replaceState({}, "", `/?${q.toString()}`);
      };
    }

    const promptToggle = $("fp-prompt-toggle");
    if (promptToggle) {
      promptToggle.onclick = () => {
        S.expandedVeoPrompt = !S.expandedVeoPrompt;
        renderShotDetail();
      };
    }

    sd.querySelectorAll(".fp-take-card").forEach((card) => {
      card.onclick = () => {
        const shotId = card.dataset.shot;
        const takeAttempt = Number(card.dataset.take);
        playTakeInPanel(shotId, takeAttempt);
      };
    });
  }

  function playTakeInPanel(shotId, takeAttempt) {
    const shotData = S.takes[shotId];
    if (!shotData) return;
    const tk = (shotData.takes || []).find((t) => t.attempt === takeAttempt);
    if (!tk) return;

    S.playingTake = { shotId, takeAttempt };

    const video = $("fp-video");
    if (tk.clip_available && tk.clip_url) {
      video.src = tk.clip_url;
      video.loop = true;
      if (tk.image_url) video.poster = tk.image_url;
      video.load();
      video.play().catch(() => {});
    } else if (tk.image_url) {
      video.pause();
      video.poster = tk.image_url;
    }

    const c = tk.critique;
    const scoreStr = c && c.score != null ? c.score.toFixed(2) : "";
    const verdict = tk.accepted ? (c && c.passed ? "accepted" : "accepted (flagged)") : "rejected";
    $("fp-take-badge").textContent = `Shot ${shortId(shotId)} · Take ${tk.attempt} · ${scoreStr} ${verdict}`;
    $("fp-take-overlay").classList.remove("hidden");

    renderShotDetail();
  }

  // Playhead sync & Timeline interaction
  $("fp-video").addEventListener("timeupdate", () => {
    if (S.playingTake) return;
    const ct = $("fp-video").currentTime;
    const playhead = $("fp-playhead");
    if (playhead) {
      const inset = S.timelineInset || 16;
      const x = inset + Math.round(ct * S.timelineScale);
      playhead.style.left = `${x}px`;

      const wrap = $("fp-timeline-scroll");
      if (wrap && !S.timelineFitsWidth) {
        const left = wrap.scrollLeft;
        const right = left + wrap.clientWidth;
        if (x > right - 60) {
          wrap.scrollLeft = x - 120;
        } else if (x < left) {
          wrap.scrollLeft = Math.max(0, x - 40);
        }
      }
    }
  });

  $("fp-timeline-scroll").addEventListener("click", (e) => {
    if (e.target.closest(".fp-clip-tile")) return;

    const canvas = $("fp-timeline-canvas");
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const inset = S.timelineInset || 16;
    const clickX = e.clientX - rect.left - inset;
    const seekTime = Math.max(0, Math.min(S.totalDuration, clickX / S.timelineScale));

    const video = $("fp-video");
    if (S.playingTake) {
      S.playingTake = null;
      renderFinalPanel();
    }
    video.currentTime = seekTime;
    video.play().catch(() => {});
  });

  // Hover time tooltip on timeline
  const canvasEl = $("fp-timeline-canvas");
  const tooltipEl = $("fp-time-tooltip");
  if (canvasEl && tooltipEl) {
    canvasEl.addEventListener("mousemove", (e) => {
      const rect = canvasEl.getBoundingClientRect();
      const inset = S.timelineInset || 16;
      const clickX = e.clientX - rect.left - inset;
      const t = Math.max(0, Math.min(S.totalDuration, clickX / S.timelineScale));
      tooltipEl.textContent = fmtRulerTime(t);
      tooltipEl.style.left = `${inset + Math.round(t * S.timelineScale)}px`;
      tooltipEl.classList.remove("hidden");
    });
    canvasEl.addEventListener("mouseleave", () => {
      tooltipEl.classList.add("hidden");
    });
  }

  $("fp-back-to-cut-btn").addEventListener("click", () => {
    S.playingTake = null;
    const curVer = (S.job.versions || []).find((v) => v.version === S.activeVersion) || (S.job.versions || [])[0];
    const finalCutUrl = (curVer && curVer.url) || S.job.final_video_url;
    const video = $("fp-video");
    video.src = finalCutUrl;
    video.loop = false;
    $("fp-take-overlay").classList.add("hidden");

    if (S.selectedShotId) {
      const item = S.shotTimings.find((t) => t.shot.id === S.selectedShotId);
      if (item) video.currentTime = item.startTime;
    }
    video.play().catch(() => {});
    renderShotDetail();
  });

  $("fp-close-btn").addEventListener("click", () => closeFinalCut());

  window.addEventListener("resize", () => {
    if (S.inFinalMode) renderFinalPanel();
  });

  if (window.ResizeObserver && $("fp-timeline-scroll")) {
    const ro = new ResizeObserver(() => {
      if (S.inFinalMode) renderFinalPanel();
    });
    ro.observe($("fp-timeline-scroll"));
  }

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("take-modal").classList.contains("hidden")) {
        closeModal();
      } else if (S.inFinalMode) {
        closeFinalCut();
      }
    }
  });

  // Clicking take/shot thumbnails
  document.addEventListener("click", (e) => {
    if (e.target.closest("#final-panel")) return;

    const takeEl = e.target.closest("[data-take]");
    if (takeEl && takeEl.dataset.shot) {
      const shotId = takeEl.dataset.shot;
      const takeAttempt = Number(takeEl.dataset.take);
      const hasCut = S.job && ((S.job.versions && S.job.versions.length) || S.job.final_video_url);
      if (hasCut) {
        openFinalCut({ shot: shotId, take: takeAttempt });
      } else {
        openTakeModal(shotId, takeAttempt);
      }
      return;
    }

    const shotEl = e.target.closest("[data-shot]");
    if (shotEl && shotEl.dataset.shot && !shotEl.closest(".regen-chips")) {
      const shotId = shotEl.dataset.shot;
      const hasCut = S.job && ((S.job.versions && S.job.versions.length) || S.job.final_video_url);
      if (hasCut) {
        openFinalCut({ shot: shotId, take: 1 });
      } else {
        openTakeModal(shotId, 1);
      }
    }
  });

  async function resumeJob(btn) {
    btn.disabled = true;
    try {
      const res = await fetch(`/api/jobs/${S.jobId}/resume`, { method: "POST" });
      if (!res.ok) {
        S.localNotes.push({ text: await friendlyError(res, "Could not resume"), t: new Date().toISOString() });
        renderThread();
        return;
      }
      await refresh();
      connectStream();
    } finally {
      btn.disabled = false;
    }
  }

  // =========================================================
  // Boot
  // =========================================================
  loadJobs();
  route();
})();

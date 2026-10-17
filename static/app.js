/* Showrunner AI: agent workspace (vanilla JS, no build step). */
(() => {
  "use strict";

  // ---------- Helpers ----------
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
  const scoreClass = (s) => (s >= 0.7 ? "hi" : s >= 0.55 ? "mid" : "lo");
  const scoreHtml = (s) => (s == null ? "" : `<span class="score ${scoreClass(s)}">${Number(s).toFixed(2)}</span>`);
  const shortId = (id) => String(id || "").replace(/^shot_/, "").replace("_", ".");
  const splitReason = (r) => {
    const m = /^\s*\[([a-z_ -]+)\]\s*(.*)$/i.exec(r || "");
    return m ? { tag: m[1].replace(/_/g, " "), text: m[2] } : { tag: null, text: r || "" };
  };
  const reasonHtml = (r) => {
    const { tag, text } = splitReason(r);
    return `${tag ? `<span class="tag">${esc(tag)}</span>` : ""}${esc(text)}`;
  };

  const ICON = {
    plan: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h10M4 18h7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
    bible: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="8.5" cy="10" r="1.4" fill="currentColor"/><circle cx="12" cy="7.5" r="1.4" fill="currentColor"/><circle cx="15.5" cy="10" r="1.4" fill="currentColor"/></svg>',
    film: '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M10 9.5v5l4-2.5z" fill="currentColor"/></svg>',
    critic: '<svg viewBox="0 0 24 24"><path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>',
    music: '<svg viewBox="0 0 24 24"><path d="M9 18V6l11-2v12" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="6.5" cy="18" r="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="17.5" cy="16" r="2.5" stroke="currentColor" stroke-width="1.8" fill="none"/></svg>',
    cut: '<svg viewBox="0 0 24 24"><circle cx="6" cy="7" r="2.6" stroke="currentColor" stroke-width="1.8" fill="none"/><circle cx="6" cy="17" r="2.6" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M8.2 8.4L20 18M8.2 15.6L20 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linejoin="round"/></svg>',
    alert: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8" fill="none"/><path d="M12 7.5v6M12 16.5v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    play: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M8 5l11 7-11 7z" fill="currentColor"/></svg>',
    chev: '<svg class="card-chev" viewBox="0 0 24 24" width="14" height="14"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
  };

  // ---------- Theme ----------
  const THEME_KEY = "showrunner_theme";
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* ignore */ }
  }
  $("theme-toggle").addEventListener("click", () => {
    applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
  });

  // ---------- State ----------
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
    selectedShot: null,
    selectedTake: null,
    playerMode: "final",
    userPicked: false,
    activeVersion: null,
    openCards: new Set(),
    closedCards: new Set(),
    localNotes: [],
    firstRender: true,
    refreshing: false,
    refreshQueued: false,
    pollTimer: null,
    stagedFiles: [],
  };

  // ---------- Notices / errors ----------
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

  // ---------- Health ----------
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
      $("provider-name").textContent = google ? "Vertex AI · live models" : "Mock engine";
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

  // ---------- Jobs list (gallery, recent, menu) ----------
  async function loadJobs() {
    try {
      const res = await fetch("/api/jobs");
      S.jobs = await res.json();
    } catch (e) {
      S.jobs = [];
    }
    renderGallery();
    renderRecent();
    renderMenu();
  }
  const kindLabel = (m) => (m === "short_film" ? "Short film" : "Commercial");
  function renderGallery() {
    const samples = S.jobs.filter((j) => j.is_sample && j.final_video_url);
    const el = $("gallery-cards");
    if (!samples.length) {
      $("sample-gallery").classList.add("hidden");
      return;
    }
    $("sample-gallery").classList.remove("hidden");
    el.innerHTML = samples
      .map(
        (j) => `
      <button type="button" class="gcard" data-job="${esc(j.id)}">
        <div class="gcard-media">
          <img src="${esc(j.poster_url || "")}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
          <span class="gcard-kind">${kindLabel(j.mode)}</span>
          <span class="gcard-play">${ICON.play} Open workspace</span>
        </div>
        <div class="gcard-body">
          <div class="gcard-title">${esc(j.title || j.id)}</div>
          <div class="gcard-logline">${esc(j.logline || j.brief)}</div>
          <div class="gcard-meta">
            <span class="pill">${fmtDur(j.duration_s)}</span>
            <span class="pill">${j.scene_count} scenes · ${j.shot_count} shots</span>
            <span class="pill run-static pill-sample">${j.take_count} takes reviewed by the critic</span>
          </div>
        </div>
      </button>`
      )
      .join("");
  }
  function jobRow(j, cls) {
    const thumb = j.poster_url ? `<img src="${esc(j.poster_url)}" alt="" loading="lazy">` : `<span class="menu-thumb"></span>`;
    const st = j.is_sample ? "sample" : j.status;
    return `<button type="button" class="${cls}" data-job="${esc(j.id)}">${thumb}<div><div class="mi-title">${esc(
      j.title || j.brief.slice(0, 60)
    )}</div><div class="mi-sub">${kindLabel(j.mode)} · ${esc(st)}${j.duration_s ? " · " + fmtDur(j.duration_s) : ""}</div></div></button>`;
  }
  function renderRecent() {
    const recent = S.jobs.filter((j) => !j.is_sample).slice(0, 6);
    $("recent-section").classList.toggle("hidden", !recent.length);
    $("recent-list").innerHTML = recent.map((j) => jobRow(j, "rcard")).join("");
  }
  function renderMenu() {
    $("productions-menu").innerHTML = S.jobs.length
      ? S.jobs.slice(0, 30).map((j) => jobRow(j, "menu-item")).join("")
      : `<div class="menu-empty">No productions yet.</div>`;
  }
  document.addEventListener("click", (e) => {
    const card = e.target.closest("[data-job]");
    if (card) {
      $("productions-menu").classList.add("hidden");
      openJob(card.dataset.job);
      return;
    }
    if (!e.target.closest(".menu-wrap")) $("productions-menu").classList.add("hidden");
  });
  $("productions-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    $("productions-menu").classList.toggle("hidden");
  });

  // ---------- Routing ----------
  function showHome(push = true) {
    closeStream();
    S.job = null;
    S.jobId = null;
    if (push) history.pushState({}, "", "/");
    $("view-workspace").classList.add("hidden");
    $("view-home").classList.remove("hidden");
    $("topbar-job").classList.add("hidden");
    document.title = "Showrunner AI · Your AI film crew";
    loadJobs();
  }
  $("new-production-btn").addEventListener("click", () => {
    showHome();
    setTimeout(() => $("brief-input").focus(), 50);
  });
  $("brand-link").addEventListener("click", (e) => {
    e.preventDefault();
    showHome();
  });
  window.addEventListener("popstate", route);
  function route() {
    const p = new URLSearchParams(location.search);
    const id = p.get("job");
    if (id) openJob(id, { push: false, shot: p.get("shot"), take: p.get("take") });
    else showHome(false);
  }

  // ---------- Home composer ----------
  const PRESETS = {
    "ad-coffee": { mode: "ad", text: "A 30-second ad for 'Kiln & Co.', a hand-thrown ceramic coffee mug. Soft morning window light, steam curling, slow tactile close-ups of glaze and hands, ending on the tagline 'Made to be held'." },
    "ad-sneaker": { mode: "ad", text: "A 30-second night-run commercial for 'Lumen Pace', a reflective running shoe. A lone runner through rain-slick city streets, neon reflections, kinetic tracking shots, ending on the tagline 'Own the night'." },
    "film-scifi": { mode: "short_film", text: "A 2.5-minute moody sci-fi short film, 'The Signal of Kepler-9'. Scout Maya, alone on an abandoned ice-mining colony, decodes a beacon buried under the canyons and realises it is a warning, not a greeting." },
    "film-lighthouse": { mode: "short_film", text: "A 2.5-minute maritime drama. A solitary lighthouse keeper must improvise a beam with a hand lantern and a salvaged lens when a storm kills the main light and a trawler heads for the reef." },
  };
  function setMode(mode) {
    $("selected-mode").value = mode;
    document.querySelectorAll(".seg").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  }
  document.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.querySelectorAll("#example-chips .chip").forEach((c) =>
    c.addEventListener("click", () => {
      const p = PRESETS[c.dataset.preset];
      if (!p) return;
      $("brief-input").value = p.text;
      setMode(p.mode);
      $("brief-input").focus();
    })
  );
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
  $("brief-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("brief-form").requestSubmit();
  });
  $("brief-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const brief = $("brief-input").value.trim();
    if (!brief) return;
    const btn = $("submit-btn");
    btn.disabled = true;
    btn.firstChild.textContent = "Starting… ";
    const fd = new FormData();
    fd.append("brief", brief);
    fd.append("mode", $("selected-mode").value);
    S.stagedFiles.forEach((f) => fd.append("ref_images", f));
    try {
      const res = await fetch("/api/jobs", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await friendlyError(res, "Could not start the production"));
      const data = await res.json();
      S.stagedFiles = [];
      renderFiles();
      $("brief-input").value = "";
      openJob(data.id || data.job_id);
    } catch (err) {
      if (!$("notice-banner").classList.contains("hidden")) return;
      showNotice(err.message);
    } finally {
      btn.disabled = false;
      btn.firstChild.textContent = "Start production ";
    }
  });

  // ---------- Workspace: open / refresh ----------
  async function openJob(jobId, opts = {}) {
    await healthReady;
    if (opts.push !== false) {
      const q = new URLSearchParams({ job: jobId });
      if (opts.shot) q.set("shot", opts.shot);
      history.pushState({}, "", `/?${q}`);
    }
    closeStream();
    Object.assign(S, {
      jobId, job: null, takes: {}, seen: new Set(), live: null, liveImages: {}, editTargets: null,
      selectedShot: opts.shot || null, selectedTake: opts.take ? Number(opts.take) : null,
      playerMode: opts.shot ? "shot" : "final", userPicked: Boolean(opts.shot), activeVersion: null,
      openCards: new Set(), closedCards: new Set(), localNotes: [], firstRender: true, focusShot: opts.shot || null, briefOpen: false,
    });
    $("view-home").classList.add("hidden");
    $("view-workspace").classList.remove("hidden");
    $("topbar-job").classList.remove("hidden");
    $("thread").innerHTML = threadSkeleton();
    $("filmstrip").innerHTML = "";
    $("stats").innerHTML = "";
    resetPlayer();
    setInspectorOpen(opts.shot ? true : inspectorPref());
    setTab(opts.shot ? "shot" : "bible");
    await refresh();
    if (S.job && (BUSY.has(S.job.status) || !S.job.final_video_url)) connectStream();
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
      renderAll();
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

  // ---------- SSE ----------
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
      // The server replays history on every (re)connect: skip events already handled.
      if (ev.id) {
        if (S.seen.has(ev.id)) return;
        S.seen.add(ev.id);
      }
      onEvent(ev);
    });
    es.onerror = () => {
      // EventSource reconnects by itself; poll meanwhile so the UI never stalls.
      if (!S.pollTimer)
        S.pollTimer = setInterval(() => {
          if (!S.job || !BUSY.has(S.job.status)) {
            clearInterval(S.pollTimer);
            S.pollTimer = null;
            return;
          }
          refresh();
        }, 5000);
    };
  }
  // Safety net: while the job is busy, keep a stream open and poll as a fallback.
  function ensureWatching() {
    if (!S.job) return;
    const busy = BUSY.has(S.job.status);
    if (busy && !S.es) connectStream();
    if (busy && !S.pollTimer)
      S.pollTimer = setInterval(() => {
        if (!S.job || !BUSY.has(S.job.status)) {
          clearInterval(S.pollTimer);
          S.pollTimer = null;
          return;
        }
        refresh();
      }, 6000);
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
      S.activeVersion = null;
      S.playerMode = S.userPicked ? S.playerMode : "final";
      refresh();
      loadJobs();
      if (S.es) S.es.close();
      S.es = null;
      return;
    }
    renderLive();
    scheduleRefresh(ev.step === "PLAN" && d.plan ? 50 : 800);
  }

  // ---------- Derived helpers ----------
  const isSample = () => S.sampleIds.has(S.jobId);
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
        if (shot.is_flagged_fallback) return { cls: "flag", label: n > 1 ? `best of ${n}` : "accepted (flagged)", pill: "warn" };
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

  // ---------- Render: everything ----------
  function renderAll() {
    const job = S.job;
    if (!job) return;
    const title = (job.plan && job.plan.title) || "New production";
    $("topbar-title").textContent = title;
    document.title = `${title} · Showrunner AI`;
    const st = $("topbar-status");
    const busy = BUSY.has(job.status);
    st.textContent = busy ? job.status : job.status === "completed" ? "completed" : job.status;
    st.className = `pill ${busy ? "run" : job.status === "completed" ? "ok" : job.status === "failed" ? "bad" : ""}`;
    $("topbar-sample").classList.toggle("hidden", !isSample());
    renderThread();
    renderStage();
    renderInspector();
    renderDirector();
    S.firstRender = false;
    ensureWatching();
  }

  // ---------- Thread ----------
  function threadSkeleton() {
    return `<div class="msg"><span class="avatar avatar-agent">${ICON.play}</span><div class="msg-body">
      <div class="skel skel-line w40"></div><div class="skel skel-line w90"></div><div class="skel skel-line w75"></div></div></div>
      <div class="msg"><span class="avatar avatar-agent">${ICON.play}</span><div class="msg-body"><div class="skel" style="height:120px;border-radius:12px"></div></div></div>`;
  }
  const agentMsg = (key, html, time, extraCls = "") => ({
    key,
    html: `<span class="avatar avatar-agent">${ICON.play}</span><div class="msg-body"><div class="msg-meta"><span class="msg-who">Director agent</span><span class="msg-time">${esc(fmtTime(time))}</span></div>${html}</div>`,
    cls: `msg ${extraCls}`,
  });
  const indentMsg = (key, html, cls = "") => ({ key, html: `<div class="msg-body">${html}</div>`, cls: `msg msg-indent ${cls}` });
  function toolCard({ id, icon, title, tool, pill, summary, body, defaultOpen, extraCls = "" }) {
    const open = S.openCards.has(id) || (defaultOpen && !S.closedCards.has(id));
    return `<div class="card ${open ? "open" : ""} ${extraCls}" data-card="${esc(id)}">
      <div class="card-head ${body ? "clickable" : ""}" ${body ? `data-toggle="${esc(id)}"` : ""}>
        <span class="card-icon">${icon}</span>
        <span class="card-title"><span class="ct-main">${title}</span>${tool ? `<span class="tool">${esc(tool)}</span>` : ""}</span>
        ${pill || ""}${body ? ICON.chev : ""}
      </div>
      ${summary ? `<div class="card-summary">${summary}</div>` : ""}
      ${body ? `<div class="card-body collapsible">${body}</div>` : ""}
    </div>`;
  }

  function buildThread() {
    const job = S.job;
    const items = [];
    const refs = (job.reference_images || []).map((u) => `<img src="${esc(u)}" alt="">`).join("");
    items.push({
      key: "brief",
      cls: "msg msg-user",
      html: `<span class="avatar avatar-user">You</span><div class="msg-body"><div class="msg-meta"><span class="msg-who">Brief</span><span class="msg-time">${esc(
        fmtTime(job.created_at)
      )} · ${job.mode === "short_film" ? "Short film" : "Ad"}</span></div><div class="msg-text"><div class="brief-text ${job.brief.length > 320 && !S.briefOpen ? "clamp" : ""}">${esc(job.brief)}</div>${
        job.brief.length > 320 ? `<button type="button" class="link-btn" data-action="brief">${S.briefOpen ? "Show less" : "Show full brief"}</button>` : ""
      }${refs ? `<div class="ref-row">${refs}</div>` : ""}</div></div>`,
    });
    items.push(
      agentMsg(
        "intro",
        `<div class="msg-text">On it. I'll write the plan, lock a visual bible, then render every shot with Veo and have the Gemini critic review each take, reshooting anything that misses the brief.</div>`,
        job.created_at
      )
    );

    const plan = job.plan;
    if (!plan) {
      if (job.status === "failed") items.push(errorItem());
      else
        items.push(
          indentMsg(
            "plan-skel",
            toolCard({ id: "plan-skel", icon: ICON.plan, title: "Planning the production", tool: "gemini · planner", pill: '<span class="pill run">thinking</span>', summary: '<div class="skel skel-line w90"></div><div class="skel skel-line w75"></div><div class="skel skel-line w60"></div>' })
          )
        );
      return items;
    }

    const shots = allShots();
    const runtime = shots.reduce((a, s) => a + s.duration_s, 0);
    const planBody = plan.scenes
      .map(
        (sc) => `<div class="plan-scene"><div class="plan-scene-title"><span>Scene ${sc.scene_number}</span>${esc(sc.title)}</div>
        ${sc.shots
          .map((s) => `<div class="plan-shot"><span class="ps-id">${esc(shortId(s.id))}</span><span class="ps-dur">${fmtDur(s.duration_s)}</span><span>${esc(s.description)}</span></div>`)
          .join("")}</div>`
      )
      .join("");
    items.push(
      indentMsg(
        "plan",
        toolCard({
          id: "plan",
          icon: ICON.plan,
          title: "Planned the production",
          tool: "gemini-3.8-flash",
          pill: '<span class="pill ok">done</span>',
          summary: `<div class="plan-title">${esc(plan.title)}</div><div class="plan-logline">${esc(plan.logline)}</div>
            <div class="meta-row"><span class="pill">${plan.scenes.length} scenes</span><span class="pill">${shots.length} shots</span><span class="pill">${fmtDur(runtime)} runtime</span><span class="pill pill-link" data-toggle="plan">shot list ▸</span></div>`,
          body: planBody,
          defaultOpen: shots.length <= 6,
        })
      )
    );

    const bible = plan.bible || {};
    const style = bible.style || {};
    const refCards = [
      ...(bible.characters || []).map((c) => ({ name: c.name, kind: "Character", url: (job.character_refs || {})[c.name] || c.reference_image })),
      ...(bible.products || []).map((p) => ({ name: p.name, kind: "Product", url: (job.product_refs || {})[p.name] || p.reference_image })),
    ];
    const refsHtml = refCards.length
      ? `<div class="refs">${refCards
          .map((r) => `<div class="ref">${r.url ? `<img src="${esc(r.url)}" alt="" loading="lazy">` : '<div class="skel skel-media" style="aspect-ratio:1"></div>'}<div>${esc(r.name)}<small>${r.kind} sheet</small></div></div>`)
          .join("")}</div>`
      : "";
    items.push(
      indentMsg(
        "bible",
        toolCard({
          id: "bible",
          icon: ICON.bible,
          title: "Locked the visual bible",
          tool: "nano-banana",
          pill: refCards.some((r) => !r.url) && BUSY.has(job.status) ? '<span class="pill run">drawing</span>' : '<span class="pill ok">done</span>',
          summary: `<div class="palette">${(style.palette || []).map((c) => `<span class="swatch" style="background:${esc(c)}" title="${esc(c)}"></span>`).join("")}</div>
            <div class="kv"><b>Mood</b> · ${esc(style.mood || "")}</div>${refsHtml}`,
          body: `<div class="kv"><b>Lighting</b> · ${esc(style.lighting || "")}</div><div class="kv"><b>Lens</b> · ${esc(style.lens || "")}</div>`,
        })
      )
    );

    items.push(
      agentMsg(
        "render-intro",
        `<div class="msg-text">Rendering ${shots.length} shots with Veo 3.1. Each take goes to the critic; a score under 0.70 gets a corrected prompt and a reshoot. Consecutive shots chain last frame → first frame for continuity.</div>`,
        (shots.find((s) => (s.attempts || []).length) || {}).attempts?.[0]?.created_at
      )
    );

    plan.scenes.forEach((sc) => {
      items.push({ key: `scene-${sc.id}`, cls: "scene-divider", html: `<span class="sn">Scene ${sc.scene_number}</span>${esc(sc.title)}` });
      sc.shots.forEach((shot) => items.push(indentMsg(`shot-${shot.id}`, shotCard(shot))));
    });

    const st = loopStats();
    if (st.takes && st.done === st.shots) {
      items.push(
        indentMsg(
          "critic-summary",
          toolCard({
            id: "critic-summary",
            icon: ICON.critic,
            title: "Critic loop summary",
            tool: "gemini-3.8-flash · critic",
            summary: `<div class="summary-grid">
              <div><b>${st.takes}</b><span>takes reviewed</span></div>
              <div><b>${st.reshoots}</b><span>reshoots</span></div>
              <div><b>${st.firstPass}</b><span>passed first try</span></div>
              <div><b>${st.avg != null ? st.avg.toFixed(2) : "–"}</b><span>avg accepted score</span></div></div>
              ${st.flagged ? `<div class="kv" style="margin-top:8px">${st.flagged} shot(s) never cleared 0.70, so I kept the best take and flagged it.</div>` : ""}`,
          })
        )
      );
    }

    if (job.soundtrack_url || shots.some((s) => s.vo_line)) {
      const vo = shots.filter((s) => s.vo_line).length;
      const ready = Boolean(job.soundtrack_url);
      items.push(
        indentMsg(
          "audio",
          toolCard({
            id: "audio",
            icon: ICON.music,
            title: ready ? "Scored the soundtrack & voiced the narration" : "Scoring & voicing",
            tool: "lyria-3 · gemini-tts",
            pill: ready ? '<span class="pill ok">done</span>' : '<span class="pill">queued</span>',
            summary: `${vo} voiceover line${vo === 1 ? "" : "s"} · mood: ${esc(style.mood || "")}${ready ? `<audio controls preload="none" src="${esc(job.soundtrack_url)}"></audio>` : ""}`,
          })
        )
      );
    }

    const v1 = (job.versions || [])[0];
    if (v1) {
      items.push(
        indentMsg(
          "done",
          toolCard({
            id: "done",
            icon: ICON.cut,
            title: "Assembled the master cut",
            tool: "ffmpeg",
            pill: '<span class="pill ok">v1 ready</span>',
            summary: `${fmtDur(runtime)} · ${shots.length} clips · captions burned in · VO ducked over the score
              <div class="done-poster" data-play-version="1"><img src="${esc(v1.poster_url || job.poster_url || "")}" alt=""><span class="gcard-play">${ICON.play} Watch the film</span></div>`,
          })
        )
      );
    }

    // Director chat history and later versions, in time order
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
            html: `<span class="avatar avatar-user">You</span><div class="msg-body"><div class="msg-meta"><span class="msg-who">Director note</span><span class="msg-time">${esc(fmtTime(x.m.ts))}</span></div><div class="msg-text">${esc(x.m.text)}</div></div>`,
          });
        } else {
          const m = /Re-shooting shot\(s\):\s*([^.]*)\.?/i.exec(x.m.text || "");
          const ids = m ? m[1].split(",").map((s) => s.trim()).filter(Boolean) : [];
          const text = m ? x.m.text.replace(m[0], "").trim() : x.m.text;
          items.push(
            agentMsg(
              `m-${x.i}`,
              `<div class="msg-text">${esc(text)}</div>${ids.length ? `<div class="regen-list"><span class="msg-time">Regenerating:</span>${ids.map((s) => `<code data-shot="${esc(s)}">${esc(shortId(s))}</code>`).join("")}</div>` : ""}`,
              x.m.ts
            )
          );
        }
      } else {
        items.push(
          indentMsg(
            `v-${x.v.version}`,
            toolCard({
              id: `v-${x.v.version}`,
              icon: ICON.cut,
              title: `Cut v${x.v.version} ready`,
              tool: "ffmpeg",
              pill: `<span class="pill ok">v${x.v.version}</span>`,
              summary: `${esc(x.v.message || "")}${(x.v.changed_shots || []).length ? `<div class="regen-list"><span class="msg-time">Re-shot:</span>${x.v.changed_shots.map((s) => `<code data-shot="${esc(s)}">${esc(shortId(s))}</code>`).join("")}</div>` : ""}
                <div class="done-poster" data-play-version="${x.v.version}"><img src="${esc(x.v.poster_url || "")}" alt=""><span class="gcard-play">${ICON.play} Watch v${x.v.version}</span></div>`,
            })
          )
        );
      }
    });

    S.localNotes.forEach((n, i) => items.push(agentMsg(`local-${i}`, `<div class="msg-text">${esc(n.text)}</div>`, n.t)));
    if (job.status === "failed") items.push(errorItem());
    if (BUSY.has(job.status)) items.push(liveItem());
    return items;
  }

  function errorItem() {
    const canResume = !isSample();
    return indentMsg(
      "error",
      toolCard({
        id: "error",
        icon: ICON.alert,
        title: "Production paused",
        extraCls: "error-card",
        summary: `${esc(S.job.error || "The pipeline stopped.")}${
          canResume ? `<div style="margin-top:10px"><button class="btn btn-primary btn-sm" data-action="resume" type="button">Resume production</button> <span class="msg-time">Finished clips are reused, never re-rendered.</span></div>` : ""
        }`,
      })
    );
  }
  function liveText() {
    if (S.live) return S.live.message;
    const j = S.job;
    return { queued: "Queued…", planning: "Writing the plan…", planned: "Plan ready, starting production…", generating: "Rendering shots…", critiquing: "Critic reviewing takes…", assembling: "Assembling the master cut…" }[j.status] || "Working…";
  }
  function liveItem() {
    const regen = S.editTargets && S.editTargets.length
      ? `<div class="regen-list"><span class="msg-time">Will regenerate:</span>${S.editTargets.map((s) => `<code data-shot="${esc(s)}">${esc(shortId(s))}</code>`).join("")}</div>`
      : "";
    return indentMsg("live", `<div class="live-card"><span class="spinner"></span><span id="live-text">${esc(liveText())}</span></div>${regen}`);
  }
  function renderLive() {
    const el = $("live-text");
    if (el) el.textContent = liveText();
    const sub = $("agent-sub");
    if (S.job && BUSY.has(S.job.status)) {
      sub.textContent = "Working…";
      sub.classList.add("working");
    }
  }

  function shotCard(shot) {
    const t = S.takes[shot.id] || { takes: [] };
    const takes = t.takes || [];
    const st = shotState(shot);
    const thumb = shotThumb(shot);
    const acc = takes.find((x) => x.accepted);
    const running = st.cls === "run";
    const headScore = acc && acc.critique ? scoreHtml(acc.critique.score) : "";
    const head = `<div class="card-head" data-shot="${esc(shot.id)}" style="cursor:pointer">
      ${thumb ? `<img class="shot-thumb" src="${esc(thumb)}" alt="" loading="lazy">` : `<span class="shot-thumb skel"></span>`}
      <div class="shot-head-text"><div class="t1"><span class="sid">${esc(shortId(shot.id))}</span>${esc(shot.description)}</div>
      <div class="t2"><span class="sdot ${st.cls}"></span><span class="st-label ${st.cls}">${esc(st.label)}</span><span>· ${fmtDur(shot.duration_s)} · ${esc(shot.camera)}${t.chained_from_shot_id ? ` · chained from ${esc(shortId(t.chained_from_shot_id))}` : ""}</span></div></div>
      ${headScore}</div>`;
    const showFlow = takes.length > 1 || shot.is_flagged_fallback || running || (takes.length === 1 && !acc);
    if (!showFlow) return `<div class="card shot-card">${head}</div>`;
    let flow = "";
    takes.forEach((tk, i) => {
      const c = tk.critique;
      const verdict = tk.accepted
        ? c && c.passed ? `<span class="pill ok">accepted</span>` : `<span class="pill warn">best of ${takes.length} · flagged</span>`
        : c && !c.passed ? `<span class="pill bad">rejected</span>` : c ? `<span class="pill">passed</span>` : `<span class="pill run">reviewing</span>`;
      flow += `<div class="take-row ${tk.accepted ? "" : "rejected"}">
        <div class="take-thumb" data-shot="${esc(shot.id)}" data-take="${tk.attempt}">${tk.image_url ? `<img src="${esc(tk.image_url)}" alt="" loading="lazy">` : ""}<span class="play">${ICON.play}</span></div>
        <div><div class="take-line">Take ${tk.attempt} ${c ? scoreHtml(c.score) : ""} ${verdict}</div>
        ${c && c.reasons && c.reasons.length && !(tk.accepted && c.passed) ? `<div class="take-reason">${reasonHtml(c.reasons[0])}</div>` : c && tk.accepted && c.reasons && c.reasons.length ? `<div class="take-reason">${esc(splitReason(c.reasons[0]).text)}</div>` : ""}</div></div>`;
      const next = takes[i + 1];
      if (c && !c.passed && (next || running)) {
        flow += `<div class="correction"><b>Correction applied →</b> ${esc(c.suggested_prompt_fix || "Prompt revised from critic notes.")}</div>`;
      }
    });
    if (running) {
      const img = S.liveImages[shot.id];
      flow += `<div class="take-row"><div class="take-thumb">${img ? `<img src="${esc(img)}" alt="">` : '<div class="skel" style="position:absolute;inset:0;border-radius:0"></div>'}</div>
        <div><div class="take-line">Take ${takes.length + 1} <span class="pill run">${esc(st.label)}</span></div><div class="take-reason">Veo is rendering this take; the critic reviews it next.</div></div></div>`;
    }
    return `<div class="card shot-card open">${head}<div class="card-body"><div class="takes-flow">${flow}</div></div></div>`;
  }

  function renderThread() {
    const container = $("thread");
    const items = buildThread();
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 140;
    if (S.firstRender) container.innerHTML = "";
    const existing = new Map();
    [...container.children].forEach((el) => el.dataset.key && existing.set(el.dataset.key, el));
    const keep = new Set();
    let added = 0;
    items.forEach((it, idx) => {
      keep.add(it.key);
      let el = existing.get(it.key);
      if (!el) {
        el = document.createElement("div");
        el.dataset.key = it.key;
        el.className = `${it.cls} enter`;
        if (S.firstRender) el.style.animationDelay = `${Math.min(idx * 30, 900)}ms`;
        el.innerHTML = it.html;
        el._html = it.html;
        added++;
      } else if (el._html !== it.html) {
        el.innerHTML = it.html;
        el._html = it.html;
        el.className = it.cls;
      }
      if (container.children[idx] !== el) container.insertBefore(el, container.children[idx] || null);
    });
    existing.forEach((el, key) => !keep.has(key) && el.remove());
    if (S.focusShot) {
      const target = container.querySelector(`[data-key="shot-${CSS.escape(S.focusShot)}"]`);
      if (target) {
        container.style.scrollBehavior = "auto";
        container.scrollTop = Math.max(0, target.offsetTop - container.offsetTop - 120);
        container.style.scrollBehavior = "";
      }
      S.focusShot = null;
    } else if (added && (nearBottom || S.firstRender) && BUSY.has(S.job.status)) {
      container.scrollTop = container.scrollHeight;
    }
    const sub = $("agent-sub");
    const busy = BUSY.has(S.job.status);
    sub.textContent = busy ? "Working…" : S.job.status === "completed" ? (isSample() ? "Sample production · full agent trace" : "Production complete") : S.job.status === "failed" ? "Paused" : "Idle";
    sub.classList.toggle("working", busy);
  }

  $("thread").addEventListener("click", (e) => {
    const tog = e.target.closest("[data-toggle]");
    const takeEl = e.target.closest("[data-take]");
    const shotEl = e.target.closest("[data-shot]");
    const ver = e.target.closest("[data-play-version]");
    const act = e.target.closest("[data-action]");
    if (act && act.dataset.action === "resume") return resumeJob(act);
    if (act && act.dataset.action === "brief") {
      S.briefOpen = !S.briefOpen;
      return renderThread();
    }
    if (e.target.closest("audio")) return;
    if (ver) return playVersion(Number(ver.dataset.playVersion));
    if (takeEl) return selectShot(takeEl.dataset.shot, Number(takeEl.dataset.take));
    if (shotEl) return selectShot(shotEl.dataset.shot);
    if (tog) {
      const id = tog.dataset.toggle;
      const card = tog.closest(".card");
      const open = !card.classList.contains("open");
      card.classList.toggle("open", open);
      if (open) { S.openCards.add(id); S.closedCards.delete(id); } else { S.openCards.delete(id); S.closedCards.add(id); }
      const wrap = card.closest("[data-key]");
      if (wrap) wrap._html = null;
    }
  });
  $("jump-latest").addEventListener("click", () => ($("thread").scrollTop = $("thread").scrollHeight));

  // ---------- Stage ----------
  function resetPlayer() {
    const v = $("player-video");
    v.pause();
    v.removeAttribute("src");
    v.load();
    v.classList.add("hidden");
    $("player-image").classList.add("hidden");
    $("player-empty").classList.remove("hidden");
    $("player-badge").classList.add("hidden");
    $("stage-label").textContent = "Stage";
    $("progress-fill").style.width = "0";
  }
  function currentVersion() {
    const vs = S.job.versions || [];
    if (!vs.length) return null;
    return vs.find((v) => v.version === S.activeVersion) || vs[vs.length - 1];
  }
  function setVideo(src, poster, { loop = false, autoplay = false } = {}) {
    const v = $("player-video");
    $("player-image").classList.add("hidden");
    $("player-empty").classList.add("hidden");
    v.classList.remove("hidden");
    v.loop = loop;
    if (v.getAttribute("src") !== src) {
      v.setAttribute("src", src);
      if (poster) v.setAttribute("poster", poster); else v.removeAttribute("poster");
      v.load();
      if (autoplay) v.play().catch(() => {});
    }
  }
  function setImage(src) {
    const v = $("player-video");
    v.pause();
    v.classList.add("hidden");
    $("player-empty").classList.add("hidden");
    const img = $("player-image");
    img.classList.remove("hidden");
    if (img.getAttribute("src") !== src) img.setAttribute("src", src);
  }
  function setEmpty(text) {
    $("player-video").classList.add("hidden");
    $("player-image").classList.add("hidden");
    $("player-empty").classList.remove("hidden");
    $("player-empty-text").textContent = text;
  }
  function badge(html) {
    const b = $("player-badge");
    if (!html) return b.classList.add("hidden");
    b.innerHTML = html;
    b.classList.remove("hidden");
  }

  function renderStage() {
    const job = S.job;
    const shots = allShots();
    const ver = currentVersion();
    // Auto-follow the agent while it works (until the user picks a shot).
    if (!S.userPicked) {
      if (ver && !BUSY.has(job.status)) {
        S.playerMode = "final";
      } else {
        const running = shots.find((s) => ["generating", "critiquing", "retrying"].includes(s.status));
        const lastDone = [...shots].reverse().find((s) => shotThumb(s));
        const pick = running || lastDone;
        if (pick && !(ver && S.playerMode === "final")) {
          S.selectedShot = pick.id;
          S.playerMode = "shot";
        }
      }
    }
    if (S.playerMode === "final" && ver) {
      setVideo(ver.url, ver.poster_url);
      $("stage-label").innerHTML = `Final cut <span class="pill">v${ver.version}</span> <span class="sid">${fmtDur(shots.reduce((a, s) => a + s.duration_s, 0))} · ${shots.length} shots</span>`;
      badge("");
    } else if (S.selectedShot && shots.find((s) => s.id === S.selectedShot)) {
      const shot = shots.find((s) => s.id === S.selectedShot);
      const t = S.takes[shot.id] || { takes: [] };
      const take = (S.selectedTake && t.takes.find((x) => x.attempt === S.selectedTake)) || t.takes.find((x) => x.accepted) || t.takes[t.takes.length - 1];
      const st = shotState(shot);
      if (take && take.clip_available && take.clip_url) setVideo(take.clip_url, take.image_url, { loop: true, autoplay: S.userPicked });
      else if (take && take.image_url) setImage(take.image_url);
      else if (shotThumb(shot)) setImage(shotThumb(shot));
      else setEmpty("Rendering this shot…");
      $("stage-label").innerHTML = `<span class="sid">${esc(shot.id)}</span> ${esc(shot.description)}`;
      badge(take ? `Take ${take.attempt}${take.critique ? ` · critic ${take.critique.score.toFixed(2)}` : ""} · ${esc(st.label)}` : esc(st.label));
    } else {
      setEmpty(job.plan ? "The film will appear here as shots are rendered." : "The agent is writing the plan…");
      $("stage-label").textContent = job.plan ? job.plan.title : "Planning";
      badge("");
    }
    $("show-final-btn").classList.toggle("hidden", !(ver && S.playerMode !== "final"));
    const dl = $("download-btn");
    dl.classList.toggle("hidden", !ver);
    if (ver) dl.href = ver.url;
    const vs = job.versions || [];
    $("version-tabs").innerHTML = vs.length > 1
      ? vs.map((v) => `<button type="button" data-version="${v.version}" class="${ver && v.version === ver.version && S.playerMode === "final" ? "active" : ""}">v${v.version}</button>`).join("")
      : "";

    // Progress
    const st = loopStats();
    let pct = 0, label = "";
    const busy = BUSY.has(job.status);
    if (job.status === "completed") { pct = 100; label = "Production complete"; }
    else if (job.status === "failed") { pct = st.shots ? 10 + (80 * st.done) / st.shots : 5; label = "Paused · resumable"; }
    else if (!job.plan) { pct = 6; label = "Planning…"; }
    else if (job.status === "assembling") { pct = 94; label = "Assembling master cut…"; }
    else { pct = 10 + (80 * st.done) / Math.max(1, st.shots); label = `${st.done} / ${st.shots} shots approved`; }
    $("progress-fill").style.width = `${pct}%`;
    $("progress-label").textContent = label;
    $("progress-row").classList.toggle("hidden", !busy && job.status === "completed" && false);

    $("stats").innerHTML = job.plan
      ? `<div class="stat"><b>${fmtDur(st.runtime)}</b><span>runtime</span></div>
         <div class="stat"><b>${st.shots}</b><span>shots · ${job.plan.scenes.length} scenes</span></div>
         <div class="stat"><b>${st.takes}</b><span>takes rendered</span></div>
         <div class="stat"><b>${st.reshoots}</b><span>critic reshoots</span></div>
         <div class="stat"><b>${st.avg != null ? st.avg.toFixed(2) : "–"}</b><span>avg critic score</span></div>`
      : `<div class="stat"><div class="skel skel-line w60"></div><span>runtime</span></div><div class="stat"><div class="skel skel-line w40"></div><span>shots</span></div><div class="stat"><div class="skel skel-line w40"></div><span>takes</span></div><div class="stat"><div class="skel skel-line w40"></div><span>reshoots</span></div><div class="stat"><div class="skel skel-line w40"></div><span>critic</span></div>`;

    // Filmstrip
    $("filmstrip").innerHTML = job.plan
      ? job.plan.scenes
          .map(
            (sc) => `<div class="fs-scene"><div class="fs-scene-label"><b>S${sc.scene_number}</b>${esc(sc.title)}</div><div class="fs-shots">${sc.shots
              .map((s) => {
                const th = shotThumb(s);
                const ss = shotState(s);
                const n = ((S.takes[s.id] || {}).takes || []).length;
                return `<button type="button" class="fs-shot ${S.selectedShot === s.id && S.playerMode === "shot" ? "selected" : ""}" data-fs="${esc(s.id)}" title="${esc(s.description)}">
                  <div class="fs-img">${th ? `<img src="${esc(th)}" alt="" loading="lazy">` : `<div class="skel ${ss.cls === "run" ? "" : ""}"></div>`}<span class="fs-dur">${fmtDur(s.duration_s)}</span>${n > 1 ? `<span class="fs-takes">${n} takes</span>` : ""}</div>
                  <div class="fs-meta"><span class="sdot ${ss.cls}"></span><span class="sid">${esc(shortId(s.id))}</span><span class="fs-label">${esc(fsLabel(s, ss))}</span></div></button>`;
              })
              .join("")}</div></div>`
          )
          .join("")
      : `<div class="fs-scene"><div class="fs-shots">${'<div class="fs-shot"><div class="fs-img"><div class="skel"></div></div></div>'.repeat(5)}</div></div>`;
    const sel = $("filmstrip").querySelector(".fs-shot.selected");
    if (sel && S.userPicked) sel.scrollIntoView({ block: "nearest", inline: "center" });
  }
  function fsLabel(shot, ss) {
    const acc = acceptedTake(shot.id);
    if (ss.cls === "pass" || ss.cls === "flag") return acc && acc.critique ? `${ss.cls === "flag" ? "best" : "pass"} · ${acc.critique.score.toFixed(2)}` : ss.label;
    return ss.label;
  }
  $("filmstrip").addEventListener("click", (e) => {
    const b = e.target.closest("[data-fs]");
    if (b) selectShot(b.dataset.fs);
  });
  $("version-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-version]");
    if (b) playVersion(Number(b.dataset.version));
  });
  $("show-final-btn").addEventListener("click", () => playVersion(null));

  function selectShot(shotId, take = null) {
    S.selectedShot = shotId;
    S.selectedTake = take;
    S.playerMode = "shot";
    S.userPicked = true;
    setInspectorOpen(true);
    setTab("shot");
    const q = new URLSearchParams(location.search);
    q.set("shot", shotId);
    history.replaceState({}, "", `/?${q}`);
    renderStage();
    renderInspector();
  }
  function playVersion(version) {
    S.activeVersion = version;
    S.playerMode = "final";
    S.userPicked = true;
    const q = new URLSearchParams(location.search);
    q.delete("shot");
    history.replaceState({}, "", `/?${q}`);
    renderStage();
    const v = $("player-video");
    if (!v.classList.contains("hidden")) v.play().catch(() => {});
  }

  // ---------- Inspector ----------
  const INSP_KEY = "showrunner_inspector";
  function inspectorPref() {
    try { return localStorage.getItem(INSP_KEY) !== "closed"; } catch (e) { return true; }
  }
  function setInspectorOpen(open, persist = false) {
    $("view-workspace").classList.toggle("no-inspector", !open);
    $("toggle-inspector").classList.toggle("active", open);
    if (persist) try { localStorage.setItem(INSP_KEY, open ? "open" : "closed"); } catch (e) { /* ignore */ }
  }
  $("toggle-inspector").addEventListener("click", () => setInspectorOpen($("view-workspace").classList.contains("no-inspector"), true));
  $("close-inspector").addEventListener("click", () => setInspectorOpen(false, true));
  function setTab(tab) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
    $("inspector-shot").classList.toggle("hidden", tab !== "shot");
    $("inspector-bible").classList.toggle("hidden", tab !== "bible");
  }
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));

  function renderInspector() {
    const job = S.job;
    const plan = job && job.plan;
    const shots = allShots();
    const shot = shots.find((s) => s.id === S.selectedShot);
    const el = $("inspector-shot");
    if (!plan) {
      el.innerHTML = `<div class="skel skel-line w60"></div><div class="skel skel-line w90"></div><div class="skel" style="height:140px;margin-top:14px"></div>`;
    } else if (!shot) {
      el.innerHTML = `<div class="empty-state">Select a shot in the timeline or the agent thread to inspect its prompt, camera, voiceover and every take the critic reviewed.</div>`;
    } else {
      const sc = plan.scenes.find((x) => x.id === shot.scene_id) || {};
      const t = S.takes[shot.id] || { takes: [] };
      const st = shotState(shot);
      const takes = t.takes || [];
      const takesHtml = takes.length
        ? `<div class="takes-grid ${takes.length === 1 ? "single" : ""}">${takes
            .map((tk) => {
              const c = tk.critique;
              const verdict = tk.accepted
                ? c && c.passed ? ["ok", "Accepted · passed"] : ["warn", `Accepted · best of ${takes.length}`]
                : c && !c.passed ? ["bad", "Rejected"] : c ? ["ok", "Passed"] : ["", "Reviewing…"];
              return `<div class="take-card ${tk.accepted ? "accepted" : ""}">
                <div class="tc-media" data-insp-take="${tk.attempt}">${tk.image_url ? `<img src="${esc(tk.image_url)}" alt="" loading="lazy">` : ""}<span class="play">${ICON.play}</span></div>
                <div class="tc-body"><div class="tc-head"><span>Take ${tk.attempt}</span>${c ? scoreHtml(c.score) : ""}</div>
                <div class="tc-verdict ${verdict[0]}">${verdict[1]}</div>
                ${c && c.reasons && c.reasons.length ? `<ul class="tc-reasons">${c.reasons.map((r) => `<li>${reasonHtml(r)}</li>`).join("")}</ul>` : ""}
                ${c && !c.passed && c.suggested_prompt_fix && !tk.accepted ? `<div class="tc-fix">→ ${esc(c.suggested_prompt_fix)}</div>` : ""}</div></div>`;
            })
            .join("")}</div>`
        : `<div class="empty-state" style="padding:16px">No takes yet. ${st.cls === "run" ? "Veo is rendering take 1." : "Queued."}</div>`;
      el.innerHTML = `
        <div class="insp-title">Scene ${sc.scene_number || "?"} · Shot ${esc(shortId(shot.id).split(".")[1] || "")}</div>
        <div class="insp-sub"><span class="pill ${st.pill}">${esc(st.label)}</span><span class="pill">${fmtDur(shot.duration_s)}</span><span class="pill mono">${esc(shot.id)}</span>${t.chained_from_shot_id ? `<span class="pill">chained ← ${esc(shortId(t.chained_from_shot_id))}</span>` : ""}</div>
        <div class="field"><div class="field-label">Takes (${takes.length})</div>${takesHtml}</div>
        <div class="field"><div class="field-label">Action</div><div class="field-value">${esc(shot.description)}</div></div>
        <div class="field"><div class="field-label">Camera</div><div class="field-value">${esc(shot.camera)}</div></div>
        ${shot.vo_line ? `<div class="field"><div class="field-label">Voiceover</div><div class="field-value vo">“${esc(shot.vo_line)}”</div></div>` : ""}
        <div class="field"><div class="field-label">Final Veo prompt</div><div class="prompt-box">${esc(shot.visual_prompt)}</div></div>
        <div class="field"><div class="field-label">Scene</div><div class="field-value">${esc(sc.title || "")}: ${esc(sc.summary || "")}</div></div>`;
    }
    // Bible tab
    const b = $("inspector-bible");
    if (!plan) {
      b.innerHTML = `<div class="skel skel-line w60"></div><div class="skel skel-line w90"></div>`;
      return;
    }
    const style = plan.bible.style || {};
    const chars = plan.bible.characters || [];
    const prods = plan.bible.products || [];
    const entity = (name, kind, desc, url) => `<div class="field"><div class="field-label">${kind}</div>
      <div style="display:flex;gap:10px">${url ? `<img src="${esc(url)}" alt="" style="width:72px;height:72px;border-radius:9px;object-fit:cover;flex:none;border:1px solid var(--border)">` : ""}
      <div><div style="font-weight:600">${esc(name)}</div><div class="field-value" style="font-size:12.5px">${esc(desc)}</div></div></div></div>`;
    b.innerHTML = `
      <div class="field"><div class="field-label">Palette</div><div class="palette palette-lg">${(style.palette || []).map((c) => `<span class="swatch" style="background:${esc(c)}"><span>${esc(c)}</span></span>`).join("")}</div></div>
      <div class="field" style="margin-top:26px"><div class="field-label">Mood</div><div class="field-value">${esc(style.mood || "")}</div></div>
      <div class="field"><div class="field-label">Lighting</div><div class="field-value">${esc(style.lighting || "")}</div></div>
      <div class="field"><div class="field-label">Lens</div><div class="field-value">${esc(style.lens || "")}</div></div>
      ${chars.map((c) => entity(c.name, "Character", c.appearance, (job.character_refs || {})[c.name] || c.reference_image)).join("")}
      ${prods.map((p) => entity(p.name, "Product", p.description, (job.product_refs || {})[p.name] || p.reference_image)).join("")}`;
  }
  $("inspector-shot").addEventListener("click", (e) => {
    const m = e.target.closest("[data-insp-take]");
    if (m && S.selectedShot) selectShot(S.selectedShot, Number(m.dataset.inspTake));
  });

  // ---------- Director chat ----------
  function renderDirector() {
    const form = $("chat-form");
    const input = $("chat-input");
    const note = $("chat-note");
    const job = S.job;
    const sample = isSample();
    const busy = BUSY.has(job.status);
    const unfinished = job.status === "failed" && !job.final_video_url;
    form.classList.toggle("locked", sample || busy || unfinished);
    input.disabled = sample || busy || unfinished;
    $("chat-send").disabled = sample || busy || unfinished;
    if (sample) {
      input.placeholder = "Sample productions are read-only";
      note.innerHTML = `This is a finished sample, kept read-only. <a href="/" id="start-own">Start your own production →</a>`;
    } else if (busy) {
      input.placeholder = "The agent is working…";
      note.textContent = "You can direct changes once this cut is finished.";
    } else if (unfinished) {
      input.placeholder = "Resume the production first";
      note.textContent = "This production stopped before the first cut. Resume it above.";
    } else {
      input.placeholder = "Direct a change… e.g. “Make scene 2 warmer”";
      note.textContent = "The agent re-shoots only the shots your note affects and cuts a new version.";
    }
  }
  document.addEventListener("click", (e) => {
    if (e.target.id === "start-own") {
      e.preventDefault();
      showHome();
    }
  });
  $("chat-input").addEventListener("input", (e) => {
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
  });
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("chat-form").requestSubmit();
    }
  });
  document.querySelectorAll("#chat-chips .chip").forEach((c) =>
    c.addEventListener("click", () => {
      if ($("chat-input").disabled) return;
      $("chat-input").value = c.dataset.prompt;
      $("chat-input").focus();
    })
  );
  $("chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("chat-input").value.trim();
    if (!text || !S.jobId) return;
    $("chat-send").disabled = true;
    try {
      const res = await fetch(`/api/jobs/${S.jobId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (res.status === 409) {
        const d = await res.json().catch(() => ({}));
        S.localNotes.push({ text: d.detail || "I'm still working on the current cut. Send your note once it's finished.", t: new Date().toISOString() });
        renderThread();
        return;
      }
      if (!res.ok) {
        const msg = await friendlyError(res, "Could not apply the note");
        S.localNotes.push({ text: msg, t: new Date().toISOString() });
        renderThread();
        return;
      }
      $("chat-input").value = "";
      $("chat-input").style.height = "auto";
      S.userPicked = false;
      await refresh();
      connectStream();
      $("thread").scrollTop = $("thread").scrollHeight;
    } catch (err) {
      S.localNotes.push({ text: `Could not reach the server: ${err.message}`, t: new Date().toISOString() });
      renderThread();
    } finally {
      if (S.job) renderDirector();
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

  // ---------- Boot ----------
  loadJobs();
  route();
})();

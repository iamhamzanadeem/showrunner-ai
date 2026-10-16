document.addEventListener("DOMContentLoaded", () => {
  // DOM Elements
  const providerBadge = document.getElementById("provider-badge");
  const providerName = document.getElementById("provider-name");
  const jobBadge = document.getElementById("job-badge");
  const jobIdText = document.getElementById("job-id-text");

  const briefForm = document.getElementById("brief-form");
  const briefInput = document.getElementById("brief-input");
  const selectedModeInput = document.getElementById("selected-mode");
  const modeButtons = document.querySelectorAll(".mode-btn");
  const presetChips = document.querySelectorAll(".chip");
  const fileInput = document.getElementById("file-input");
  const filePreviewList = document.getElementById("file-preview-list");
  const submitBtn = document.getElementById("submit-btn");
  const btnSpinner = document.getElementById("btn-spinner");
  const btnText = document.getElementById("btn-text");

  const planTitle = document.getElementById("plan-title");
  const planLogline = document.getElementById("plan-logline");
  const planModeBadge = document.getElementById("plan-mode-badge");
  const statScenes = document.getElementById("stat-scenes");
  const statShots = document.getElementById("stat-shots");
  const statDuration = document.getElementById("stat-duration");
  const boardEmpty = document.getElementById("board-empty");
  const scenesContainer = document.getElementById("scenes-container");

  const bibleCard = document.getElementById("bible-card");
  const paletteSwatches = document.getElementById("palette-swatches");
  const bibleLighting = document.getElementById("bible-lighting");
  const bibleLens = document.getElementById("bible-lens");
  const bibleEntities = document.getElementById("bible-entities");

  const logContainer = document.getElementById("log-container");
  const clearLogsBtn = document.getElementById("clear-logs-btn");
  const autoscrollCheck = document.getElementById("autoscroll-check");

  let activeEventSource = null;
  let stagedFiles = [];

  // 1. Fetch Health and Provider Status
  async function checkHealth() {
    try {
      const res = await fetch("/api/health");
      const data = await res.json();
      const provider = (data.active_provider || "mock").toUpperCase();
      providerName.textContent = provider === "GOOGLE" ? "GOOGLE VEO / GEMINI" : "MOCK ENGINE";
      providerBadge.className = `badge ${provider === "GOOGLE" ? "badge-google" : "badge-mock"}`;
      providerBadge.querySelector(".icon").textContent = provider === "GOOGLE" ? "🌟" : "⚡";
    } catch (err) {
      providerName.textContent = "OFFLINE";
      providerBadge.className = "badge badge-mock";
    }
  }
  checkHealth();

  // 2. Mode Toggle Handling
  modeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      modeButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      selectedModeInput.value = btn.dataset.mode;
    });
  });

  // 3. Preset Briefs
  const presets = {
    "ad-cyberpunk": {
      mode: "ad",
      text: "A 30-second neon cyberpunk commercial for an ultra-fast quantum energy drink called 'Kinetic Zero', starring a lone runner dashing across rain-slicked Tokyo rooftops at midnight.",
    },
    "ad-luxury": {
      mode: "ad",
      text: "A 45-second cinematic luxury ad for 'Aethelgard Chrono', an artisan mechanical watch crafted from meteorite steel, exploring themes of timeless precision and deep craftsmanship.",
    },
    "film-scifi": {
      mode: "short_film",
      text: "A 2.5-minute moody sci-fi short film titled 'The Signal of Kepler-9'. Scout Maya uncovers an encrypted alien beacon buried in ice canyons on an abandoned mining colony.",
    },
  };

  presetChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const key = chip.dataset.preset;
      const preset = presets[key];
      if (preset) {
        briefInput.value = preset.text;
        selectedModeInput.value = preset.mode;
        modeButtons.forEach((b) => {
          b.classList.toggle("active", b.dataset.mode === preset.mode);
        });
      }
    });
  });

  // 4. File Drop & Preview
  fileInput.addEventListener("change", (e) => {
    const files = Array.from(e.target.files);
    files.forEach((file) => {
      if (!stagedFiles.some((f) => f.name === file.name)) {
        stagedFiles.push(file);
      }
    });
    renderFilePreviews();
  });

  function renderFilePreviews() {
    filePreviewList.innerHTML = "";
    stagedFiles.forEach((f, idx) => {
      const tag = document.createElement("span");
      tag.className = "preview-tag";
      tag.innerHTML = `<span>📎 ${f.name}</span> <button type="button" class="btn-ghost" data-idx="${idx}">×</button>`;
      tag.querySelector("button").addEventListener("click", (e) => {
        e.stopPropagation();
        stagedFiles.splice(idx, 1);
        renderFilePreviews();
      });
      filePreviewList.appendChild(tag);
    });
  }

  // 5. Append Log Entries
  function appendLog(step, message, timestamp) {
    const entry = document.createElement("div");
    const safeStep = (step || "INFO").replace(/\s+/g, "_");
    entry.className = `log-entry log-${safeStep}`;

    const timeStr = timestamp
      ? new Date(timestamp).toLocaleTimeString()
      : new Date().toLocaleTimeString();

    entry.innerHTML = `
      <div class="log-meta">
        <span class="log-tag tag-${safeStep}">${step}</span>
        <span class="log-time">${timeStr}</span>
      </div>
      <div class="log-message">${escapeHtml(message)}</div>
    `;

    logContainer.appendChild(entry);
    if (autoscrollCheck.checked) {
      logContainer.scrollTop = logContainer.scrollHeight;
    }
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  clearLogsBtn.addEventListener("click", () => {
    logContainer.innerHTML = "";
  });

  // 6. Render Plan to Board
  function renderPlan(plan) {
    if (!plan) return;

    // Header stats
    planTitle.textContent = plan.title.toUpperCase();
    planLogline.textContent = plan.logline || "";
    planModeBadge.textContent = (plan.mode || "AD").toUpperCase();
    planModeBadge.className = `badge badge-mode`;

    statScenes.textContent = plan.scenes ? plan.scenes.length : 0;
    const totalShots = plan.scenes
      ? plan.scenes.reduce((acc, s) => acc + (s.shots ? s.shots.length : 0), 0)
      : 0;
    statShots.textContent = totalShots;

    const totalDuration = plan.scenes
      ? plan.scenes.reduce(
          (acc, s) =>
            acc +
            (s.shots
              ? s.shots.reduce((shotAcc, shot) => shotAcc + (shot.duration_s || 0), 0)
              : 0),
          0
        )
      : plan.target_duration_s || 0;
    statDuration.textContent = `${Math.round(totalDuration)}s`;

    // Visual Bible
    if (plan.bible) {
      bibleCard.classList.remove("hidden");
      const style = plan.bible.style || {};

      paletteSwatches.innerHTML = "";
      if (style.palette && style.palette.length) {
        style.palette.forEach((hex) => {
          const swatch = document.createElement("div");
          swatch.className = "palette-swatch";
          swatch.style.backgroundColor = hex;
          swatch.title = hex;
          paletteSwatches.appendChild(swatch);
        });
      }

      bibleLighting.textContent = style.lighting || "Cinematic Natural";
      bibleLens.textContent = `${style.lens || "35mm Prime"} • Mood: ${style.mood || "Dynamic"}`;

      bibleEntities.innerHTML = "";
      (plan.bible.characters || []).forEach((c) => {
        const item = document.createElement("div");
        item.className = "entity-pill";
        item.innerHTML = `<span class="entity-name">👤 ${escapeHtml(c.name)}</span> <span class="entity-sub">(${escapeHtml(c.role || "Lead")})</span>`;
        bibleEntities.appendChild(item);
      });
      (plan.bible.products || []).forEach((p) => {
        const item = document.createElement("div");
        item.className = "entity-pill";
        item.innerHTML = `<span class="entity-name">📦 ${escapeHtml(p.name)}</span> <span class="entity-sub">${escapeHtml(p.description || "")}</span>`;
        bibleEntities.appendChild(item);
      });
    }

    // Scenes and Shots Grid
    boardEmpty.classList.add("hidden");
    scenesContainer.classList.remove("hidden");
    scenesContainer.innerHTML = "";

    (plan.scenes || []).forEach((scene) => {
      const sceneBlock = document.createElement("div");
      sceneBlock.className = "scene-block";

      sceneBlock.innerHTML = `
        <div class="scene-header">
          <div class="scene-title-left">
            <span class="scene-num-badge">SCENE ${scene.scene_number}</span>
            <span class="scene-heading">${escapeHtml(scene.title)}</span>
          </div>
          <span class="scene-summary">${escapeHtml(scene.summary)}</span>
        </div>
        <div class="shots-grid" id="grid-${scene.id}"></div>
      `;

      const grid = sceneBlock.querySelector(`#grid-${scene.id}`);
      (scene.shots || []).forEach((shot) => {
        const shotCard = document.createElement("div");
        shotCard.className = "shot-card";
        shotCard.id = `card-${shot.id}`;

        const isChained = shot.chain_from_previous;
        const chainHtml = isChained
          ? `<span class="continuity-pill" title="Chained from previous frame">⛓ CHAINED</span>`
          : "";

        const voHtml = shot.vo_line
          ? `<div class="shot-vo-box">"${escapeHtml(shot.vo_line)}"</div>`
          : "";

        shotCard.innerHTML = `
          <div class="shot-card-header">
            <span class="shot-id">${shot.id.toUpperCase()}</span>
            <span class="shot-duration">${shot.duration_s}s</span>
          </div>
          <div class="shot-thumbnail">
            ${chainHtml}
            <span class="camera-tag">${escapeHtml(shot.camera || "Wide Shot")}</span>
          </div>
          <div class="shot-details">
            <p class="shot-desc">${escapeHtml(shot.description)}</p>
            ${voHtml}
            <div class="shot-status-row">
              <span class="shot-status-badge status-${shot.status || 'planned'}">${(shot.status || 'PLANNED').toUpperCase()}</span>
            </div>
          </div>
        `;

        grid.appendChild(shotCard);
      });

      scenesContainer.appendChild(sceneBlock);
    });
  }

  // 7. Subscribe to SSE events
  function connectJobEvents(jobId) {
    if (activeEventSource) {
      activeEventSource.close();
    }

    jobBadge.classList.remove("hidden");
    jobIdText.textContent = jobId.toUpperCase();

    const url = `/api/jobs/${jobId}/events`;
    activeEventSource = new EventSource(url);

    activeEventSource.addEventListener("agent_event", (e) => {
      try {
        const event = JSON.parse(e.data);
        appendLog(event.step, event.message, event.timestamp);

        // If plan is present in data payload, render it immediately
        if (event.data && event.data.plan) {
          renderPlan(event.data.plan);
          resetSubmitBtn();
        }
      } catch (err) {
        console.error("SSE parse error", err);
      }
    });

    activeEventSource.onerror = () => {
      // Periodic check via REST if SSE drops
      pollJobStatus(jobId);
    };
  }

  async function pollJobStatus(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (res.ok) {
        const job = await res.json();
        if (job.plan) {
          renderPlan(job.plan);
          resetSubmitBtn();
        }
      }
    } catch (e) {
      console.warn("Polling error", e);
    }
  }

  function setSubmitting() {
    submitBtn.disabled = true;
    btnSpinner.classList.remove("hidden");
    btnText.textContent = "Agent Synthesizing Plan...";
  }

  function resetSubmitBtn() {
    submitBtn.disabled = false;
    btnSpinner.classList.add("hidden");
    btnText.textContent = "Generate Production Plan";
  }

  // 8. Form Submission
  briefForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const brief = briefInput.value.trim();
    if (!brief) return;

    setSubmitting();

    appendLog("PLAN", `Initiating Showrunner production agent... Mode: ${selectedModeInput.value}`);

    const formData = new FormData();
    formData.append("brief", brief);
    formData.append("mode", selectedModeInput.value);
    stagedFiles.forEach((file) => {
      formData.append("ref_images", file);
    });

    try {
      const res = await fetch("/api/jobs", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Server error (${res.status})`);
      }

      const jobData = await res.json();
      const jobId = jobData.id || jobData.job_id;
      appendLog("PLAN", `Job created: ${jobId}. Connecting to live agent stream...`);
      connectJobEvents(jobId);
    } catch (err) {
      appendLog("ERROR", `Failed to initiate job: ${err.message}`);
      resetSubmitBtn();
    }
  });
});

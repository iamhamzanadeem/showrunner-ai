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

  const progressFill = document.getElementById("progress-fill");
  const progressStepText = document.getElementById("progress-step-text");
  const progressPctText = document.getElementById("progress-pct-text");

  const bibleCard = document.getElementById("bible-card");
  const paletteSwatches = document.getElementById("palette-swatches");
  const bibleLighting = document.getElementById("bible-lighting");
  const bibleLens = document.getElementById("bible-lens");
  const bibleEntities = document.getElementById("bible-entities");
  const refSheetThumbnails = document.getElementById("ref-sheet-thumbnails");

  const masterVideo = document.getElementById("master-video");
  const playerPlaceholder = document.getElementById("player-placeholder");
  const playerStatusBadge = document.getElementById("player-status-badge");
  const downloadVideoBtn = document.getElementById("download-video-btn");

  const logContainer = document.getElementById("log-container");
  const clearLogsBtn = document.getElementById("clear-logs-btn");
  const autoscrollCheck = document.getElementById("autoscroll-check");

  let activeEventSource = null;
  let stagedFiles = [];
  let currentJobId = null;
  let currentPlan = null;

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
      tag.innerHTML = `<span>📎 ${escapeHtml(f.name)}</span> <button type="button" class="btn-ghost" data-idx="${idx}">×</button>`;
      tag.querySelector("button").addEventListener("click", (e) => {
        e.stopPropagation();
        stagedFiles.splice(idx, 1);
        renderFilePreviews();
      });
      filePreviewList.appendChild(tag);
    });
  }

  // 5. Progress Bar Updater
  function updateProgress(pct, label) {
    const clamped = Math.max(0, Math.min(100, Math.round(pct)));
    progressFill.style.width = `${clamped}%`;
    progressPctText.textContent = `${clamped}%`;
    if (label) {
      progressStepText.textContent = label.toUpperCase();
    }
  }

  // 6. Append Log Entries
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
    if (text === null || text === undefined) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  clearLogsBtn.addEventListener("click", () => {
    logContainer.innerHTML = "";
  });

  // 7. Render Reference Sheets
  function addReferenceSheetThumb(name, type, url) {
    if (!refSheetThumbnails) return;
    const existing = document.getElementById(`ref-sheet-${name}`);
    if (existing) return;

    const card = document.createElement("div");
    card.id = `ref-sheet-${name}`;
    card.className = "ref-sheet-card";
    card.innerHTML = `
      <img src="${url}" alt="${escapeHtml(name)}" onerror="this.style.display='none'">
      <div class="ref-sheet-card-title">${escapeHtml(name)} (${type})</div>
    `;
    refSheetThumbnails.appendChild(card);
  }

  // 8. Render Plan to Board
  function renderPlan(plan) {
    if (!plan) return;
    currentPlan = plan;

    // Header stats
    planTitle.textContent = plan.title.toUpperCase();
    planLogline.textContent = plan.logline || "";
    planModeBadge.textContent = (plan.mode || "AD").toUpperCase();

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
          ? `<span class="continuity-pill" id="chain-pill-${shot.id}" title="Chained from previous frame">⛓ CHAINED</span>`
          : "";

        const voHtml = shot.vo_line
          ? `<div class="shot-vo-box">"${escapeHtml(shot.vo_line)}"</div>`
          : "";

        const imgHtml = shot.image_url
          ? `<img src="${shot.image_url}" class="shot-img-thumb" alt="${shot.id}">`
          : "";

        shotCard.innerHTML = `
          <div class="shot-card-header">
            <div style="display:flex; align-items:center; gap:6px;">
              <span class="shot-id">${shot.id.toUpperCase()}</span>
              <span class="attempt-pill" id="att-badge-${shot.id}">ATTEMPT #1</span>
            </div>
            <span class="shot-duration">${shot.duration_s}s</span>
          </div>
          <div class="shot-thumbnail" id="thumb-${shot.id}">
            ${imgHtml}
            ${chainHtml}
            <span class="camera-tag">${escapeHtml(shot.camera || "Wide Shot")}</span>
          </div>
          <div class="shot-details">
            <p class="shot-desc" id="desc-${shot.id}">${escapeHtml(shot.description)}</p>
            ${voHtml}
            <div class="shot-status-row">
              <span class="shot-status-badge status-${shot.status || 'pending'}" id="status-badge-${shot.id}">${(shot.status || 'PENDING').toUpperCase()}</span>
              <span class="critic-badge hidden" id="critic-badge-${shot.id}"></span>
            </div>
          </div>
          <div class="critic-drawer hidden" id="critic-drawer-${shot.id}">
            <div class="critic-drawer-header">
              <span>CRITIC REVIEW</span>
              <span id="critic-score-${shot.id}">SCORE: 0.00</span>
            </div>
            <ul class="critic-reasons" id="critic-reasons-${shot.id}"></ul>
            <div class="critic-fix-box hidden" id="critic-fix-${shot.id}"></div>
          </div>
        `;

        grid.appendChild(shotCard);
      });

      scenesContainer.appendChild(sceneBlock);
    });
  }

  // 9. Update Specific Shot Card on Live SSE Events
  function updateShotCard(shotId, data) {
    if (!shotId) return;
    const card = document.getElementById(`card-${shotId}`);
    if (!card) return;

    // Status badge
    if (data.status) {
      const badge = document.getElementById(`status-badge-${shotId}`);
      if (badge) {
        badge.textContent = data.status.toUpperCase();
        badge.className = `shot-status-badge status-${data.status.toLowerCase()}`;
      }
    }

    // Keyframe thumbnail
    if (data.image_url) {
      const thumbBox = document.getElementById(`thumb-${shotId}`);
      if (thumbBox) {
        let img = thumbBox.querySelector(".shot-img-thumb");
        if (!img) {
          img = document.createElement("img");
          img.className = "shot-img-thumb";
          thumbBox.insertBefore(img, thumbBox.firstChild);
        }
        img.src = data.image_url;
      }
    }

    // Attempt counter
    if (data.attempt) {
      const attBadge = document.getElementById(`att-badge-${shotId}`);
      if (attBadge) {
        attBadge.textContent = `ATTEMPT #${data.attempt}`;
        if (data.attempt > 1) {
          attBadge.style.borderColor = "rgba(245, 158, 11, 0.5)";
          attBadge.style.color = "#fbbf24";
        }
      }
    }

    // Chaining indicator
    if (data.chained) {
      const chainPill = document.getElementById(`chain-pill-${shotId}`);
      if (!chainPill) {
        const thumbBox = document.getElementById(`thumb-${shotId}`);
        if (thumbBox) {
          const pill = document.createElement("span");
          pill.className = "continuity-pill";
          pill.id = `chain-pill-${shotId}`;
          pill.textContent = "⛓ CHAINED";
          thumbBox.appendChild(pill);
        }
      }
    }

    // Critic results
    if (data.critic) {
      const critBadge = document.getElementById(`critic-badge-${shotId}`);
      const drawer = document.getElementById(`critic-drawer-${shotId}`);
      const scoreSpan = document.getElementById(`critic-score-${shotId}`);
      const reasonsList = document.getElementById(`critic-reasons-${shotId}`);
      const fixBox = document.getElementById(`critic-fix-${shotId}`);

      if (critBadge && drawer) {
        critBadge.classList.remove("hidden");
        drawer.classList.remove("hidden");

        const score = data.critic.score ? data.critic.score.toFixed(2) : "0.00";
        scoreSpan.textContent = `SCORE: ${score}`;

        if (data.critic.passed) {
          critBadge.className = "critic-badge critic-passed";
          critBadge.textContent = `✔ CRITIC ${score}`;
        } else {
          critBadge.className = "critic-badge critic-failed";
          critBadge.textContent = `✘ CRITIC ${score}`;
        }

        reasonsList.innerHTML = "";
        (data.critic.reasons || []).forEach((r) => {
          const li = document.createElement("li");
          li.textContent = r;
          reasonsList.appendChild(li);
        });

        if (data.critic.suggested_prompt_fix) {
          fixBox.classList.remove("hidden");
          fixBox.textContent = `Fix: ${data.critic.suggested_prompt_fix}`;
        } else {
          fixBox.classList.add("hidden");
        }
      }
    }
  }

  // 10. Complete Video Player Setup
  function displayMasterVideo(videoUrl, posterUrl) {
    if (!videoUrl) return;

    playerPlaceholder.classList.add("hidden");
    masterVideo.classList.remove("hidden");
    masterVideo.src = videoUrl;
    if (posterUrl) {
      masterVideo.poster = posterUrl;
    }

    playerStatusBadge.textContent = "MASTER READY";
    playerStatusBadge.style.backgroundColor = "rgba(16, 185, 129, 0.2)";
    playerStatusBadge.style.color = "#34d399";
    playerStatusBadge.style.borderColor = "rgba(16, 185, 129, 0.4)";

    downloadVideoBtn.classList.remove("hidden");
    downloadVideoBtn.href = videoUrl;
  }

  // 11. Subscribe to SSE events
  function connectJobEvents(jobId) {
    if (activeEventSource) {
      activeEventSource.close();
    }
    currentJobId = jobId;

    jobBadge.classList.remove("hidden");
    jobIdText.textContent = jobId.toUpperCase();

    const url = `/api/jobs/${jobId}/events`;
    activeEventSource = new EventSource(url);

    activeEventSource.addEventListener("agent_event", (e) => {
      try {
        const event = JSON.parse(e.data);
        appendLog(event.step, event.message, event.timestamp);

        // Progress bar updates
        handleEventStep(event);

        // If plan is present in data payload, render it immediately
        if (event.data && event.data.plan) {
          renderPlan(event.data.plan);
        }
      } catch (err) {
        console.error("SSE parse error", err);
      }
    });

    activeEventSource.onerror = () => {
      pollJobStatus(jobId);
    };
  }

  function handleEventStep(event) {
    const step = event.step;
    const data = event.data || {};
    const shotId = event.shot_id;

    switch (step) {
      case "PLAN":
        updateProgress(15, "Planning story structure");
        break;

      case "REF":
        updateProgress(25, "Generating reference sheets");
        if (data.entity && data.url) {
          addReferenceSheetThumb(data.entity, data.type || "ref", data.url);
        }
        break;

      case "MUSIC":
      case "VO":
        updateProgress(35, "Synthesizing voiceover & score");
        break;

      case "KEYFRAME":
        updateProgress(45, `Rendering keyframe ${shotId || ""}`);
        if (shotId) {
          updateShotCard(shotId, {
            status: "generating",
            attempt: data.attempt,
          });
        }
        break;

      case "GEN":
        updateProgress(55, `Rendering clip ${shotId || ""}`);
        if (shotId) {
          updateShotCard(shotId, {
            status: "generating",
            attempt: data.attempt,
          });
        }
        break;

      case "CHAIN":
        if (shotId) {
          updateShotCard(shotId, { chained: true });
        }
        break;

      case "CRITIC_PASS":
        updateProgress(65, `Critic approved ${shotId || ""}`);
        if (shotId) {
          updateShotCard(shotId, {
            status: "passed",
            critic: {
              passed: true,
              score: data.score,
              reasons: data.reasons,
            },
          });
        }
        break;

      case "CRITIC_FAIL":
        if (shotId) {
          updateShotCard(shotId, {
            status: data.fallback ? "passed" : "failed",
            critic: {
              passed: false,
              score: data.score,
              reasons: data.reasons,
              suggested_prompt_fix: data.suggested_prompt_fix,
            },
          });
        }
        break;

      case "RETRY":
        if (shotId) {
          updateShotCard(shotId, {
            status: "retrying",
            attempt: data.next_attempt,
          });
        }
        break;

      case "ASSEMBLE":
        updateProgress(85, "FFmpeg master assembly");
        playerStatusBadge.textContent = "ASSEMBLING";
        break;

      case "DONE":
        updateProgress(100, "Production complete");
        if (data.final_video_url) {
          displayMasterVideo(data.final_video_url, data.poster_url);
        }
        resetSubmitBtn();
        break;

      case "ERROR":
        updateProgress(100, "Error halted pipeline");
        resetSubmitBtn();
        break;
    }
  }

  async function pollJobStatus(jobId) {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (res.ok) {
        const job = await res.json();
        if (job.plan && !currentPlan) {
          renderPlan(job.plan);
        }
        if (job.final_video_url) {
          displayMasterVideo(job.final_video_url, job.poster_url);
          updateProgress(100, "Complete");
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
    btnText.textContent = "Showrunner Agent Active...";
  }

  function resetSubmitBtn() {
    submitBtn.disabled = false;
    btnSpinner.classList.add("hidden");
    btnText.textContent = "Run Showrunner Studio";
  }

  // 12. Form Submission
  briefForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const brief = briefInput.value.trim();
    if (!brief) return;

    setSubmitting();
    updateProgress(5, "Initializing production agent");

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

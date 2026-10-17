# Showrunner AI 🎬

> **The Agentic AI Video Studio — "Antigravity for Video Production"**  
> *Submitted to the Google Cloud AI Builder Cup 2026 by Hamza Nadeem & Urooj Nadeem.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.13](https://img.shields.io/badge/Python-3.13-brightgreen.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Built with Gemini](https://img.shields.io/badge/Google%20GenAI-Gemini%20%C2%B7%20Veo%20%C2%B7%20Lyria-4285F4.svg)](https://cloud.google.com/vertex-ai)

### 🔴 Live demo: **[https://showrunner-ai-jdakbjo4ea-uc.a.run.app](https://showrunner-ai-jdakbjo4ea-uc.a.run.app)**
Open it, browse the **sample gallery** (a real 30s ad and a real ~2.5 min short film made end-to-end by the agent), or type a brief to start your own tiny production. There's no login. Public jobs are capped to keep costs safe (see [Public demo limits](#public-demo-limits)).

Showrunner AI is an autonomous, agentic AI video production studio. It transforms a single creative brief (and optional reference images) into a fully assembled, broadcast-ready film with continuous camera movement, visual consistency, professional voiceover, ducked soundtrack, burned-in subtitles, and targeted scene-level chat refinement.

---

## ⚡ The Agent Loop

Traditional generative video tools generate disjointed clips from independent prompts. Showrunner AI operates as an end-to-end autonomous agent with an explicit multi-stage production pipeline:

```mermaid
flowchart TD
    Brief["Creative Brief + Ref Assets"] --> Planner["1. Director Planner (Gemini 3.8)"]
    Planner --> Bible["2. Visual & Character Bible (Nano Banana)"]
    Bible --> Loop["3. Scene-by-Scene Shot Synthesizer"]
    
    subgraph ShotLoop ["Shot Production & Critique Loop"]
        KF["Keyframe Synthesis"] --> Chaining{"Continuity Chaining?"}
        Chaining -- Yes --> ChainedFrame["Extract Prev Clip Last Frame"]
        Chaining -- No --> BaseKF["Synthesize First Frame"]
        ChainedFrame --> Veo["Veo 3.1 Video Generation"]
        BaseKF --> Veo
        Veo --> Critic["Gemini Multimodal Critic"]
        Critic -- Fail (Score < 0.70) --> Retry["Refine Visual Prompt (Max 2 Retries)"]
        Retry --> KF
        Critic -- Pass --> Approved["Shot Approved"]
    end
    
    Loop --> ShotLoop
    Approved --> Audio["4. Voiceover (Gemini TTS) & Score (Lyria)"]
    Audio --> FFmpeg["5. FFmpeg Assembly (Concat + Captions + Mix)"]
    FFmpeg --> Master["Master Video Assembly (.MP4)"]
    Master --> ChatRefine["6. Director Chat Refinement ('Make scene 2 warmer')"]
    ChatRefine --> PlannerEdit["Targeted Plan Edit (Gemini)"]
    PlannerEdit --> ShotLoop
```

### 1. Planning & Pacing (Gemini 3.8 Flash)
The agent synthesizes a cohesive screenplay, scene-by-scene narrative arcs, duration-snapped shot lists (4s, 6s, 8s for Veo), camera directions, voiceover scripts, and character/product definitions.

### 2. Visual & Character Bible (Nano Banana / Gemini Image)
To enforce strict visual fidelity, the agent generates dedicated character and product reference sheets. Every subsequent keyframe is grounded against this locked visual bible.

### 3. Continuity Chaining (Last-Frame to First-Frame)
When consecutive shots share continuous motion within a scene, the orchestrator automatically uses **FFmpeg** to extract the exact last frame of shot $k$ and injects it as the starting frame for shot $k+1$. This eliminates jarring jump cuts and produces continuous physical motion.

### 4. Video Synthesis (Veo 3.1 Image-to-Video)
Each shot is animated using Google's Veo 3.1 image-to-video foundation model (`veo-3.1-fast-generate-001` on Vertex AI), honoring 16:9 720p framing and explicit camera motion directives.

### 5. Multimodal Critic Loop (Gemini Vision)
Before any clip reaches the final timeline, the Gemini Multimodal Critic inspects it against the shot specifications and visual bible. If artifacts, character drift, or pacing discrepancies are identified, the critic issues concrete prompt corrections and triggers an automated reshoot (bounded by `MAX_RETRIES`).

```mermaid
sequenceDiagram
    autonumber
    participant Orch as Agent Orchestrator
    participant Img as Image Provider
    participant Video as Veo Video Provider
    participant Critic as Gemini Multimodal Critic

    Orch->>Img: Generate Shot Keyframe (conditioned on Bible)
    Img-->>Orch: keyframe.png
    Orch->>Video: Generate Clip (initial_frame, prompt, duration)
    Video-->>Orch: clip_att1.mp4
    Orch->>Critic: Critique Clip (clip_att1.mp4, shot_spec, bible)
    Critic-->>Orch: CritiqueResult(passed=False, score=0.54, issues=['Character clothing drift', 'Pacing rush'], prompt_fix='Anchor vest texture and add slow push-in')
    Note over Orch: Bounded Retry Loop (Attempt 2)
    Orch->>Orch: Merge prompt_fix into visual_prompt
    Orch->>Img: Regenerate Keyframe with prompt_fix
    Img-->>Orch: keyframe_att2.png
    Orch->>Video: Regenerate Clip
    Video-->>Orch: clip_att2.mp4
    Orch->>Critic: Critique Clip (clip_att2.mp4, shot_spec, bible)
    Critic-->>Orch: CritiqueResult(passed=True, score=0.91, reasons=['Excellent wardrobe continuity', 'Fluid motion'])
    Orch->>Orch: Accept Shot into Timeline
```

### 6. Sound Design & Master FFmpeg Assembly
- **Voiceover**: Gemini TTS (`gemini-3.8-flash-tts`) synthesizes naturalistic spoken voiceover paced to shot durations.
- **Score**: DeepMind Lyria (`lyria-3-clip-preview` / `lyria-3-pro-preview`) synthesizes atmospheric background music.
- **FFmpeg Engine**: Concat demuxer joins clips with transition buffers, burns high-contrast subtitles, and dynamically ducks background music during active voiceover lines.

### 7. Director Chat Refinement
The production process doesn't end with the first cut. Directors can issue natural language feedback (e.g. *"Make scene 2 warmer and more dramatic"* or *"Rewrite the dialogue in shot 1 to 'Unleash infinite momentum'"*). The agent:
- Identifies **only** the affected shots and scenes.
- Propagates continuity chaining for any follow-up shots in the same scene.
- Re-synthesizes voiceover if lines change.
- Re-shoots only affected clips through the critic loop, reusing all unchanged media from disk.
- Assembles a versioned cut (`final_v2.mp4`) with an instant version switcher in the UI.

---

## 🤖 Google Models & Services

| Pipeline Role | Model ID | Environment Variable | API Surface |
| :--- | :--- | :--- | :--- |
| **Director & Screenplay Planner** | `gemini-3.8-flash` | `SR_MODEL_PLANNER` | Google GenAI SDK (`generate_content` + `response_schema`) |
| **Multimodal Video Critic** | `gemini-3.8-flash` | `SR_MODEL_CRITIC` | Google GenAI SDK (`generate_content` multimodal video) |
| **Visual Bible & Keyframes** | `gemini-3.1-flash-image` | `SR_MODEL_IMAGE` | Google GenAI SDK (`generate_content` image modalities) |
| **Cinematic Video Generation** | `veo-3.1-fast-generate-001` | `SR_MODEL_VIDEO` | Google GenAI SDK (`generate_videos` image-to-video) |
| **Spoken Voiceover Synthesis** | `gemini-3.8-flash-tts` | `SR_MODEL_TTS` | `generate_content` audio modality on Vertex (Interactions API on AI Studio) |
| **Soundtrack & Score (≤30s)** | `lyria-3-clip-preview` | `SR_MODEL_MUSIC` | Interactions API (Vertex, `global`) |
| **Soundtrack & Score (>30s, up to 180s)** | `lyria-3-pro-preview` | `SR_MODEL_MUSIC_LONG` | Interactions API (Vertex, `global`) |
| **Keyframe fallback on 429** | `gemini-2.5-flash-image` | `SR_MODEL_IMAGE_FALLBACK` | `generate_content` image modality |

Veo runs in `us-central1` at 720p (`generate_audio=False`; VO and score are generated separately). Gemini, Nano Banana, TTS and Lyria run on the Vertex `global` endpoint.

---

## 🏛 System Architecture

Showrunner AI is structured as a single self-contained FastAPI service serving both REST/SSE APIs and a zero-build reactive Web Studio frontend:

```mermaid
flowchart LR
    subgraph Browser ["Web Studio Frontend (Vanilla HTML/CSS/JS)"]
        UI_Brief["Creative Brief & Mode Selector"]
        UI_Board["Live Scene & Shot Board"]
        UI_Chat["Director Refinement Chat"]
        UI_Player["Master Video Player (v1/v2 Switcher)"]
        UI_Logs["Real-Time Agent Cognition Stream"]
    end

    subgraph Backend ["FastAPI Single Service (Python 3.13)"]
        API_Endpoints["REST & SSE Endpoints (/api/jobs)"]
        EventBus["InMemoryEventBus (SSE Streams)"]
        Orchestrator["Agent Production Orchestrator"]
        Assembler["FFmpeg Video Assembly Engine"]
        Storage["State & Media Store (/data)"]
    end

    subgraph Providers ["Provider Abstraction Layer"]
        MockProvider["Mock Engine (Pillow, FFmpeg lavfi, Offline)"]
        GoogleProvider["Google GenAI / Vertex AI Engine"]
    end

    Browser <-->|HTTP POST / JSON / Multipart| API_Endpoints
    API_Endpoints --> Orchestrator
    Orchestrator --> EventBus
    EventBus -->|SSE text/event-stream| UI_Logs
    EventBus -->|Live Card Updates| UI_Board
    Orchestrator --> Providers
    Orchestrator --> Assembler
    Assembler --> Storage
    Storage -->|Static Media Streaming| UI_Player
```

---

## 🛠 Provider Modes & Modality Overrides

Showrunner AI is configured via `SHOWRUNNER_PROVIDER`:
- `SHOWRUNNER_PROVIDER=mock`: Complete zero-cost offline mock engine utilizing Pillow for keyframes, FFmpeg Ken-Burns zoompan for clips, and synthetic WAV synthesis.
- `SHOWRUNNER_PROVIDER=google`: Full Google Cloud production pipeline using Gemini 3.8, Veo 3.1, and Lyria. Supports both standard Gemini API Keys and Vertex AI with Workload Identity on Google Cloud.
- **Hybrid Modality Overrides**: You can run any combination of real and mock providers simultaneously:
  ```bash
  SHOWRUNNER_PROVIDER=mock
  SR_PROVIDER_PLANNER=google    # Use real Gemini 3.8 for planning
  SR_PROVIDER_CRITIC=google     # Use real Gemini 3.8 for multimodal review
  SR_PROVIDER_VIDEO=mock        # Use mock video to conserve generation quotas
  ```

---

## 🚀 Local Quickstart

### Prerequisites
- Python 3.13+
- FFmpeg 6.0+ (`sudo apt install ffmpeg fonts-dejavu-core`)

### Installation & Run

1. **Clone the repository**:
   ```bash
   git clone https://github.com/iamhamzanadeem/showrunner-ai.git
   cd showrunner-ai
   ```

2. **Create and activate virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```

3. **Configure environment**:
   ```bash
   cp .env.example .env
   # Mock mode works out of the box. For real models, either set GEMINI_API_KEY,
   # or use Vertex: GOOGLE_GENAI_USE_VERTEXAI=true, GOOGLE_CLOUD_PROJECT=..., 
   # SHOWRUNNER_PROVIDER=google (and run `gcloud auth application-default login`)
   ```

4. **Run the local development server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```
   Open your browser to [http://localhost:8080](http://localhost:8080).

5. **Run the automated test suite**:
   ```bash
   pytest -v   # always runs in mock mode, no billable calls
   ```

---

## ☁️ Google Cloud Run Deployment

Showrunner AI is packaged to run on **Google Cloud Run** with persistent Cloud Storage FUSE mounting:

```mermaid
flowchart LR
    Judge["Browser (public, no login)"] -->|HTTPS + SSE| CR["Cloud Run: showrunner-ai\nmax 2 instances · concurrency 20\nsession affinity · CPU always on"]
    CR -->|Vertex AI, service account showrunner-sa| Models["Gemini 3.8 Flash · Nano Banana · Veo 3.1 Fast · Gemini TTS · Lyria 3"]
    CR -->|Cloud Storage FUSE /data| GCS[("gs://showrunner-ai-1017h-showrunner-media\njob.json · clips · final.mp4 · usage.json")]
    Budget["Billing budget $150\nalerts 50/90/100%"] -.-> CR
```

- **Runtime**: one FastAPI container serving the UI, REST and SSE. Jobs run in-process as asyncio tasks, so `--no-cpu-throttling` is set.
- **Storage**: `DATA_DIR=/data` is a GCS FUSE mount, so jobs, media, finished films and usage counters survive restarts and are shared by both instances.
- **Scaling**: at most 2 instances (`--max-instances=2`, concurrency 20). The event bus is in-memory per instance, so `--session-affinity` keeps a browser on the instance running its job. If a request lands on the other instance, it reads job state from the bucket. A job is only treated as interrupted (and made resumable) once it has had no update for `RECOVERY_STALE_S` (20 min), so a new instance never fails its sibling's live job.
- **Identity**: dedicated service account `showrunner-sa` with only `roles/aiplatform.user` (project) and `roles/storage.objectAdmin` (media bucket). No API keys.
- **SSE**: streams replay job history, then send keepalives every 15s. The client dedupes replayed events on reconnect.
- **Large media**: Cloud Run rejects sized HTTP/1 responses over 32 MiB. Files over 24 MB (a 2.5 min film is ~39 MB) are served in byte ranges of up to 16 MB, which video players request in sequence. Plain downloads are streamed with chunked encoding.

### Restarts & Failure Handling

- On startup, jobs left mid-flight by a previous process are marked `failed` ("Interrupted by server restart"); interrupted chat refinements fall back to the last completed version. Failed jobs show a **Resume production** button (`POST /api/jobs/{id}/resume`).
- Each shot retries provider errors, then accepts its best-scoring clip; only a shot with no usable clip fails the job, and sibling scenes are cancelled immediately to avoid further paid renders.
- The Veo operation id is persisted on each shot (`veo_operation_name`); the Veo launch call is never auto-retried (no duplicate paid jobs). Only 5xx and 429 errors are retried, with jittered backoff.
- `job.json` is written atomically (temp file + `os.replace`).

### Public demo limits

The live link is intentionally open (no password) so judges can use it. Abuse and cost protection come from caps that are all set with env vars (`scripts/deploy.sh` values shown; `0` = unlimited, the local default):

| Variable | Deployed | Purpose |
| --- | --- | --- |
| `MAX_JOBS_PER_DAY` | `25` | Global cap on new productions, chat refinements and resumes per UTC day. |
| `MAX_JOBS_PER_IP_PER_DAY` | `3` | Per-client cap. IPs are stored only as salted SHA-256 hashes in `usage.json`. |
| `MAX_SHOTS_PER_JOB` | `4` | The planner is told the limit, then the plan is hard-trimmed (the ending is kept). |
| `MAX_RUNTIME_S` | `30` | Max film runtime for public jobs. |
| `MAX_RETRIES` | `1` | Critic reshoots per shot. |
| `MAX_PARALLEL_SCENES` | `2` | Concurrent scene renders per job. |
| `SAMPLE_JOB_IDS` | ad + short film | Pre-rendered productions shown first in the gallery. They are read-only (no chat or resume). |
| `CORS_ORIGINS` | `*` | Allowed origins; credentials are never allowed cross-origin. |

When a cap is hit, the API returns `429` with a friendly message (e.g. *"You've used all 3 of your free AI productions for today. Please come back tomorrow (UTC) - the sample films in the gallery are always available."*), and the UI shows it as a banner. Current limits are shown under the brief box (from `/api/health`). Briefs are capped at 4000 characters. A Cloud Billing budget ($150, alerts at 50/90/100%) is set on the project as a backstop.

Uploaded reference images are restricted to PNG/JPG/WEBP (magic-byte checked, max 5 files x 10 MB) and stored under server-generated UUID filenames.

### Automated Deployment

Run the included deployment script:

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"

# Deploy in Vertex AI mode (recommended). This creates the bucket and the
# least-privilege service account, uploads the sample jobs, and deploys:
SAMPLE_JOB_IDS="job_aedcb881dc,job_eb80955254" ./scripts/deploy.sh

# Optionally also create the $150 budget alert:
BILLING_ACCOUNT="XXXXXX-XXXXXX-XXXXXX" ./scripts/deploy.sh

# Or deploy in DEMO_MOCK mode (zero model cost):
DEMO_MOCK=1 ./scripts/deploy.sh

# Or deploy with an API Key stored in Google Secret Manager:
MODE=apikey GEMINI_API_KEY="your-key" ./scripts/deploy.sh
```

---

## 📡 API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the Showrunner Web Studio interface |
| `GET` | `/api/health` | Service health, active provider configuration, and model IDs |
| `GET` | `/api/jobs` | Lists recent productions with metadata and poster previews |
| `POST` | `/api/jobs` | Registers a new creative brief (JSON or multipart with ref images) |
| `GET` | `/api/jobs/{id}` | Retrieves full state, scene specifications, shot attempts, and media URLs |
| `GET` | `/api/jobs/{id}/events` | Live Server-Sent Events (SSE) stream of agent cognition logs |
| `POST` | `/api/jobs/{id}/chat` | Submits director instructions to refine a scene (409 if busy) |
| `POST` | `/api/jobs/{id}/resume` | Resumes a failed/interrupted production, reusing sheets, audio and accepted shots |
| `GET` | `/media/{path:path}` | Serves rendered production assets from `DATA_DIR` |

---

## 📂 Project Structure

```text
showrunner-ai/
├── app/
│   ├── config.py                 # Pydantic settings & environment configuration
│   ├── events.py                 # In-memory SSE event bus for agent logs
│   ├── main.py                   # FastAPI service & REST endpoints
│   ├── models.py                 # Pydantic data schemas (Job, Plan, Shot, Bible)
│   ├── pipeline/
│   │   ├── assembler.py          # FFmpeg master concatenation & audio ducking
│   │   └── orchestrator.py       # Agent orchestrator & chat refinement loop
│   └── providers/
│       ├── base.py               # Abstract provider interfaces
│       ├── factory.py            # Provider resolution & hybrid overrides
│       ├── mock.py               # Deterministic brief-aware mock engine
│       └── google/
│           ├── client.py         # Google GenAI SDK client factory & retry backoff
│           ├── critic.py         # Gemini multimodal video critic
│           ├── image.py          # Gemini image & reference sheet provider
│           ├── music.py          # DeepMind Lyria score provider
│           ├── planner.py        # Gemini structured screenplay & edit planner
│           ├── tts.py            # Gemini speech & audio synthesis
│           └── video.py          # Veo 3.1 image-to-video provider
├── scripts/
│   ├── deploy.sh                 # Cloud Run deployment script
│   └── smoke_google.py           # Provider connectivity validation script
├── static/
│   ├── app.js                    # Reactive Web Studio controller & SSE consumer
│   ├── index.html                # 3-column studio interface
│   └── style.css                 # Dark-mode cinematic aesthetic stylesheet
├── tests/
│   ├── test_api.py               # REST API & SSE streaming tests
│   ├── test_google_media_providers.py  # Mocked Veo, TTS, Lyria provider tests
│   ├── test_google_planner_critic.py   # Mocked Gemini planner & critic tests
│   ├── test_google_providers.py        # Comprehensive Google provider unit tests
│   ├── test_pipeline.py          # End-to-end assembly & critic loop tests
│   ├── test_planner.py           # Brief awareness & duration snapping tests
│   └── test_refinement.py        # Targeted shot refinement & chat tests
├── .dockerignore                 # Docker build exclusions
├── .env.example                  # Environment variable blueprint
├── Dockerfile                    # Container definition for Cloud Run
├── LICENSE                       # MIT License
├── README.md                     # Studio documentation & architecture guide
├── requirements.txt              # Production Python dependencies
└── requirements-dev.txt          # Development & pytest dependencies
```

---

## 🗺 Limitations & Future Roadmap

- **Multi-Instance Scaling**: Up to 2 instances work today using session affinity and shared GCS state. Live SSE events stay on the instance running the job. A future revision will move the event bus to Pub/Sub and orchestration to Cloud Tasks.
- **Provider quirks**: Nano Banana often returns 429 under load (handled with backoff plus a fallback model). Veo sometimes refuses keyframes with realistic faces; the shot is retried text-to-video, and if it is still refused, an animated keyframe still is used and flagged in the UI.
- **Ultra-HD 4K Upscaling**: Integrating Google Imagen Super-Resolution models to upscale generated 720p/1080p clips to broadcast 4K HDR.
- **Custom Voice Cloning**: Permitting producers to upload reference voice samples for automated director voiceover matching via Gemini Voice cloning.

---

## 👥 Authors & Acknowledgements

Created with ❤️ by:
- **Hamza Nadeem**
- **Urooj Nadeem**

Submitted for the **Google Cloud AI Builder Cup 2026**. Built with Google Cloud Vertex AI, Gemini 3.8, Veo 3.1, and DeepMind Lyria.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

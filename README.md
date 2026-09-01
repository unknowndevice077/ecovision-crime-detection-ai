# EcoVision Security Sentinel

[![Downloads](https://img.shields.io/github/downloads/unknowndevice077/ecovision-crime-detection-ai/total.svg)](https://github.com/unknowndevice077/ecovision-crime-detection-ai/releases)

Real-time AI-powered security monitoring and threat detection system. It watches a live camera feed, tracks people, detects weapons and violent behavior, and raises alerts — all through a single desktop app with a live dashboard.

Built for **always-on deployment**: the intended use case is a dedicated machine (with a GPU) running continuously, watching a camera feed 24/7 and flagging incidents automatically for review.

> **Status (as of 19 Aug 2026):** full pipeline runs end-to-end — backend, AI core, and dashboard, dynamic port negotiation, CORS, and runtime config all consistent. The organization hierarchy (barangays, police stations, jurisdictions) has been redesigned and migrated — see [docs/USER_HIERARCHY_PLAN.md](docs/USER_HIERARCHY_PLAN.md). All four detection classes — violence, robbery, vandalism, weapon/sign — are trained, measured, and individually toggleable from the AI Models admin tab. See [START_HERE.md](START_HERE.md) for the live model table and the honest numbers behind each one; the short version is below. `maincode/eval_history.csv` has the full violence accuracy trail.



## What It Does

The system runs three integrated components, packaged as one desktop application:

### 1. **AI/Vision Core** (`maincode/main.py`)
- Pulls frames from a camera feed — camera index is runtime-selectable (not hardcoded), via `/available_cameras` and `/set_camera_index`
- Runs YOLO11-pose for multi-person tracking (each tracked person gets an independent detection state — not limited to one person on screen)
- YOLO-based weapon/sign detector (gun, knife, sign classes) feeding an independent "ARMED" alert path — individually toggleable (`detection.weapon.enabled`)
- X3D-XS scene (whole-frame) classifiers for violence and robbery, each with its own confidence threshold and consecutive-frame confirmation
- Composite robbery rule (armed/violent state + sustained proximity between two people) as a secondary, model-free signal alongside the trained robbery classifier
- Vandalism: a wrist-motion FSM (`robbery_vandalism.py`) gated on a purpose-built graffiti/tag YOLO detector (`weights/vandalism_marks.pt`) — fires on sustained sweeping wrist motion near a detected mark, with no other person nearby (to distinguish spraying from a scuffle). Ships **disabled by default** — see the Key Features list below for both vandalism tracks and their real numbers.
- "Episode" tracking per track/pair so a continuously-active state (e.g. one ongoing assault) mints one incident, not one every frame
- GPU (CUDA) used automatically when available, with `.engine` (TensorRT) preferred over `.pt` when present; falls back to CPU
- Configured through `config.json`, **not** through `.env` — the AI core never loads a `.env` file

### 2. **Backend API** (`app/backend.py`, FastAPI)
- Stores alerts, incident records, and video clips
- Dual database backend: **SQLite by default**, Postgres when `DATABASE_URL` is set — see `app/db.py` and [Database](#database) below
- Manages connected device (ESP32) telemetry and siren control
- Role-based auth (JWT) and permissions system, with every scoped query going through one `scope_clause()` helper so cameras, incidents and records can't disagree about who sees what
- Serves data to the dashboard over REST + a shared `/ws` WebSocket for live push updates

### 3. **Dashboard (Frontend)** (`app/`, Next.js App Router)
- Dark tactical UI, code-split per tab (`next/dynamic`) so only the active tab's code loads
- Live alerts with severity levels, pushed over the shared WebSocket (`app/context/WebSocketContext.tsx`)
- Incident records and DVR-style footage playback (`RecordsView`, `HistoryView`)
- Crime map with Leaflet (`CrimeReportsView`)
- Admin/DevTeam consoles for user, station and location management
- Device status monitoring (ESP32 telemetry)

### 4. **Desktop Shell** (`electron/`)
- Electron wrapper for one-click launch
- Automatically starts backend and AI core as child processes, negotiates free ports if the defaults are taken, and writes the resolved ports to `runtime_ports.json` for the frontend to read
- Opens the dashboard pointed at the resolved local services

## Key Features

- ✅ **Real-time detection pipeline** — pose tracking, weapon/sign detection, and violence classification, all per-person (multi-person scenes supported)
- ✅ **Severity-tagged alerts** — incidents ranked LOW → CRITICAL with confidence scores
- ✅ **Records & DVR view** — auto-captured incident clips (H.264, browser-playable), real ffmpeg-based clip extraction and delete from the Recordings tab. 24/7 continuous archive is **not real yet** — the writer currently saves a black placeholder frame with a timestamp overlay rather than the actual camera feed, and never registers a `video_records` row, so the 24/7 tab is empty by construction, not by bug. Flagged, not fixed.
- ✅ **ESP32 device integration** — hardware connectivity and live telemetry (battery, solar voltage, temperature), remote siren activation with a dashboard emergency-stop, a physical panic button that posts a real incident, and auto-discovery (the board self-registers its IP with the backend on boot and every 30s, so a DHCP lease change doesn't strand it)
- ✅ **Two-organization access control** — barangay and PNP hierarchies with database-enforced scope; see [Organization & Roles](#organization--roles)
- ✅ **Runtime-selectable camera index** — no hardcoded camera number, switchable from the dashboard; `camera.source` also accepts a video file path for deterministic, machine-independent replay/testing
- ✅ **Self-contained desktop app** — single Inno Setup installer (`electron-builder` packages `win.target: dir`, `installer/EcoVisionSentinel.iss` wraps it), ships its own Python runtime (`python-env/`, the official embeddable distribution — not a venv), no separate Python or Node install needed on the target machine
- ✅ **Weapon/sign detection** — Gun/Knife/Sign YOLO detector, independently toggleable (previously had no on/off switch at all)
- ✅ **Robbery detection** — trained X3D-XS scene classifier, enabled by default (threshold 0.70). Deployed checkpoint: 84.2% accuracy / 65.3% recall / 86.5% precision on 8 held-out scenes. A retrain adding 11 verified CamNuvem street-robbery sources is built and measured but **not deployed** — it regressed held-out recall to ~19–52% depending on threshold on the larger, reshuffled test split, so the original checkpoint stays live pending a controlled re-run. See `docs/detection_performance_report.md`.
- 🚧 **Vandalism detection** — two separate tracks, both documented honestly:
  - The original whole-clip X3D scene classifier is trained but ships **disabled** (37.5% FPR at its best threshold) — blocked on training-scene count (11 vs. robbery's 26), not architecture; self-filming ~20 real scenes is the documented fix (`docs/vandalism_data_collection.md`) and has not been done.
  - A newer detector+rule hybrid (`weights/vandalism_marks.pt`, a YOLO graffiti/tag detector feeding the existing wrist-motion FSM) was built and measured 19 Aug: mAP50 0.741 / recall 0.625 / precision 0.806 on a genuine held-out test split. This replaces a structurally-dead prior version of the same rule, which gated on `weapon_signs.pt`'s "sign" class (road signs) and so could never fire on a wall or gate. Wired into `main.py`; ships **disabled** pending an end-to-end recall/false-positive measurement on labelled footage before flipping it on.

## Organization & Roles

Two separate organizations use the system, and the difference between them is **scope**, not seniority:

- A **barangay** owns the physical infrastructure — cameras, smartpoles, ESP32 devices. Everything a barangay user sees is filtered to their one `barangay_id`.
- A **police station** owns no infrastructure. It is a *lens*: a station is granted a **jurisdiction** — a set of barangays, via the `station_barangays` table — and its users see everything in those barangays. A station can cover several barangays, and DevTeam assigns that mapping.

There is no precinct tier.

| Role | Organization | Scope column | Can do |
|---|---|---|---|
| `DEVTEAM` | — | neither (unscoped) | Everything; manages stations, jurisdictions and location approvals |
| `BARANGAY_ADMIN` | Barangay | `barangay_id` | Everything within their barangay; creates `BARANGAY_STAFF` |
| `BARANGAY_STAFF` | Barangay | `barangay_id` | Operator, granular per-key permissions |
| `PNP_ADMIN` | Police station | `station_id` | Everything in their station's jurisdiction **except camera management**; creates `PNP_OFFICER` |
| `PNP_OFFICER` | Police station | `station_id` | Operator within the jurisdiction, granular per-key permissions |

Rules the database itself enforces (`chk_user_scope`, plus partial unique indexes):

- A barangay user has a `barangay_id` and no `station_id`; a PNP user has a `station_id` and no `barangay_id`; `DEVTEAM` has neither. Any other combination is rejected at insert time.
- One `BARANGAY_ADMIN` per barangay, one `PNP_ADMIN` per station.

`manage_cameras` is **barangay-only** (`BARANGAY_ONLY_PERMISSIONS` in `backend.py`): the barangay funded and installed the hardware, PNP consumes the feed. This holds for `PNP_ADMIN` too — an admin tier does not implicitly grant it. Other permissions (`view_map`, `view_records`, `view_history`, `confirm_dismiss_alerts`) are granular per user; see `app/hooks/usePermissions.ts` and the `user_permissions` table.

> Role strings are embedded in issued JWTs, so a hierarchy change means everyone logs in again. Tokens from before the migration fail closed (no visibility) rather than widening.

Existing databases are moved onto this model by `app/migrate_pnp_hierarchy.py` (supports `--dry-run`, backs up the database first, and is idempotent).

## Tech Stack

| Component | Technology |
|---|---|
| Frontend | Next.js (App Router), React, Tailwind CSS v4, TypeScript |
| Desktop | Electron |
| Backend API | FastAPI (Python 3.11), SQLite or Postgres |
| AI/Vision | Ultralytics YOLO11 (pose + weapon detection), PyTorchVideo X3D-XS (violence classification), OpenCV, TensorRT (optional) |
| Hardware | ESP32 integration |

## Repository Structure

```
EcoVisionCode/
├── app/                        # Next.js pages/components + FastAPI backend
│   ├── page.tsx                # Main dashboard shell (tabs lazy-loaded via next/dynamic)
│   ├── components/             # Tab views (CrimeReportsView, RecordsView, ProfileView, dashboard/*)
│   ├── context/WebSocketContext.tsx  # Shared live-update WebSocket + useLiveChannel hook
│   ├── hooks/                  # useRuntimeConfig, usePermissions
│   ├── backend.py              # FastAPI server
│   ├── db.py                   # SQLite/Postgres dual-backend shim
│   ├── migrate_pnp_hierarchy.py    # One-shot role/scope migration (--dry-run supported)
│   ├── reset_devteam_password.py   # Dev convenience, called by run_dev_system.bat
│   ├── port_utils.py           # Runtime port negotiation helpers
│   └── schema_final.sql / schema_sqlite.sql
├── alembic/                    # Postgres migrations (alembic.ini at root)
├── electron/                   # Desktop shell (main.js, setup/launch windows)
├── maincode/                   # AI/vision pipeline + model eval tooling
│   ├── main.py                 # Vision pipeline (pose + weapon + violence + rules)
│   ├── robbery_vandalism.py    # Robbery/vandalism rule logic
│   ├── x3d_violence_detector.py
│   ├── test_x3d_true_heldout.py    # True held-out accuracy eval (live pipeline)
│   ├── generate_eval_report.py, calibrate_threshold.py
│   ├── confidence_trace_plotter.py # Plots live per-frame confidence trace
│   └── eval_history.csv        # Accuracy/recall/precision/FPR log across eval runs
├── docs/USER_HIERARCHY_PLAN.md # Organization/role design rationale
├── weights/                    # Model weights (gitignored, added manually)
├── optimize_weights.py         # Exports .pt -> TensorRT .engine
├── python-env/                 # Bundled runtime for the installer, built by tools/build_python_env.ps1 (gitignored)
├── config.json / config.development.json / config.production.json
├── requirements.txt / requirements-backend.txt / requirements-detector.txt
├── package.json
├── setup.bat                   # Environment setup (.venv + python-env)
├── run_dev_system.bat          # Development startup
├── start.bat                   # Packaged-style local run without building an exe
├── build_release.bat           # Local installer build
├── tests/
└── .github/workflows/          # CI (backend tests, installer build)
```

## Getting Started (Development)

### Requirements
- Python 3.11 (`torch==2.5.1+cu121` wheels are pinned to it)
- Node.js 18+
- Both available on your system PATH
- NVIDIA GPU with a CUDA 12.1-compatible driver, for anything beyond CPU-speed inference

### Setup

```bash
setup.bat
```

This command:
1. Installs frontend dependencies (`npm install`)
2. Builds the frontend (`npm run build`)
3. Creates `.venv` and installs `requirements.txt` — this is what `run_dev_system.bat` uses
4. Creates `python-env/` (via `tools/build_python_env.ps1`, the official embeddable Python distribution) and installs the same requirements — this is what `build_release.bat` bundles into the installer
5. Prints what to run next

Both installs pass `--extra-index-url https://download.pytorch.org/whl/cu121`, which `requirements.txt` also declares, because plain `torch==2.5.1` on PyPI is the CPU build. Steps 3 and 4 skip if the directory already exists — delete it to rebuild from scratch.

### Add Model Weights

Manually place trained model weights into the `weights/` directory (gitignored due to size). `START_HERE.md` has the full live/not-running table; the summary:
- `yolo11s-pose.pt` (or `.engine`) — YOLO11 pose detection
- `weapon_signs.pt` (or `.engine`) — Gun/Knife/Sign detection, toggle at `detection.weapon.enabled`
- `x3d_xs_violence_best.pt` — Per-track (person-crop) violence classification, used when `detection.violence.mode` is `"track"` or `"both"`
- The path in `detection.violence.scene_model_path` (`config.json`) — whole-frame violence classification, used in `"scene"` mode (**the default**) and `"both"`. Currently `weights/x3d_xs_violence_scene_daynight.pt`.
- The path in `detection.robbery.model_path` — whole-frame robbery classification. Currently `weights/x3d_xs_robbery_scene.pt`.
- The path in `detection.vandalism.model_path` — whole-frame vandalism classification (the whole-clip approach). Present but **disabled** — see Key Features above.
- `vandalism_marks.pt` (path at `detection.vandalism.marks_model_path`) — single-class graffiti/tag YOLO detector feeding the vandalism wrist-motion rule. Present but **disabled** — see Key Features above.

Two weight lists have to stay in step whenever you add or rename a model: `package.json`'s `extraResources` filter and `preflight.py`'s `required` dict. An earlier release build shipped 200MB of dead checkpoints and this machine's `.engine` files because the whole `weights/` folder was copied instead of an explicit list.

`main.py` prefers `.engine` over `.pt` when both are present. Generate the `.engine` files with `optimize_weights.py`. They are version-, GPU- and platform-locked, so regenerate them after any PyTorch/TensorRT/driver change — a stale `.engine` fails at load, and the `.pt` fallback is what keeps the app running.

**Checkpoint metadata.** `train_x3d_full.py` writes a `<checkpoint>.pt.meta.json` sidecar next to every checkpoint it saves as "best" — input resolution, frame count, output convention (probabilities vs. logits), the manifest and split it was trained against, and its validation accuracy. `x3d_violence_detector.py` reads this at load time and **overrides a mismatched `config.json` with a loud warning** rather than silently using the wrong value — a checkpoint's own geometry is authoritative over the config. If you drop in a new `.pt` without its sidecar, the detector falls back to `config.json` and prints a note that it could not verify the checkpoint's input contract.

### Environment Files

| File | Read by | Notes |
|---|---|---|
| `.env` | `app/backend.py` (`load_dotenv()`) | `APP_ENV`, `SECRET_KEY`, `CORS_ORIGINS`, `DEVTEAM_BOOTSTRAP_USERNAME`/`PASSWORD`. Copy from `.env.example`. |
| `.env.local` | Next.js | **Must be at the repo root**, not in `app/`. Only `NEXT_PUBLIC_*` reaches the browser; both variables already have working defaults in code. Copy from `.env.local.example`. |
| `config.json` | backend **and** AI core | Detection thresholds, camera index, cooldowns, ESP32, network. The AI core reads only this — never `.env`. |

One caveat worth knowing: `backend.py` imports `db.py` before it calls `load_dotenv()`, and `db.py` reads `DATABASE_URL` at import time — so a `DATABASE_URL` set in `.env` does not reach the database layer. Export it in the real process environment if you want Postgres.

### Start Development

```bash
run_dev_system.bat
```

This boots:
- Backend API (port 8000)
- AI/Vision core (port 8001)
- Next.js dev server (port 3000)

The first two open in their own windows; the Next.js dev server runs in the foreground of the launching window, so **closing that window stops the frontend** while the other two keep running. Ports auto-negotiate to a free alternative if the default is taken; the frontend picks up the resolved ports at runtime (`runtime_ports.json`), so hardcoded `localhost:8000`/`8001` URLs are not required anywhere in the frontend.

`run_dev_system.bat` also runs `app/reset_devteam_password.py` and prints fresh DevTeam credentials — development convenience only.

## Database

`app/db.py` picks its backend on **the presence of `DATABASE_URL` alone** — there is no connect-failure fallback, so setting it without a reachable server raises at import.

| `DATABASE_URL` | Backend | Location |
|---|---|---|
| unset (default) | SQLite | `%USERPROFILE%\EcoVisionSentinelData\ecovision.db` |
| set | Postgres | as given; connections come from a `ThreadedConnectionPool` |

Override the data root with `ECOVISION_WRITABLE_DIR`, or point at one specific file with `SQLITE_PATH`. That writable directory also holds `logs/`, `recordings/`, `runtime_ports.json`, a writable `config.json` override, and `devteam_credentials.txt`.

**First run** creates a `devteam` account. If `DEVTEAM_BOOTSTRAP_USERNAME`/`DEVTEAM_BOOTSTRAP_PASSWORD` are set in `.env`, `app/backend.py`'s `init_db()` uses those directly — a fixed login instead of a random one, currently how the packaged installer ships for the testing phase (`TESTING_PHASE_FIXED_CREDENTIALS` in `electron/main.js`; must be reverted before a real deployment). Otherwise it generates a random password (`secrets.token_urlsafe(12)`), prints it to the console, and writes it once to `devteam_credentials.txt` in the writable directory — not shown again.

Schema lives in `app/schema_final.sql` (Postgres) and `app/schema_sqlite.sql` (SQLite) — keep both in step. Postgres migrations are in `alembic/`; the one-shot hierarchy migration is `app/migrate_pnp_hierarchy.py`.

## Configuration

Runtime behavior is configured via `config.json` at the repo root, with `config.development.json` / `config.production.json` overrides selected by the `APP_ENV` environment variable:
- Detection thresholds (`detection.violence` block: confidence threshold, hysteresis, EMA smoothing, check interval, etc.)
- Camera index
- Alert cooldowns
- ESP32 settings
- Network configuration (CORS origins, backend/AI URLs)

Most tuning requires only editing the config file — no code changes needed. A `config.json` inside the writable data directory, if present, takes precedence over the repo copy.

## Model Evaluation & Retraining

**`docs/progress_report_violence_detection.pdf`** is a standalone write-up of the violence-detection work: the defects found (a training-loss floor from a double-softmax bug, dataset leakage, the tracking-gate blind spot), the corrected model's honest held-out accuracy (95.0%, up from 78.4%), and the wide-camera person-scale limitation above with its measurements. Read that first for the full picture; this section covers the tooling.

The X3D violence classifier's accuracy is tracked over time in `maincode/eval_history.csv` — every run of `test_x3d_true_heldout.py` (the true held-out accuracy eval, run through the actual live pipeline code path) appends a row with accuracy/recall/precision/false-positive-rate and the config snapshot used.

- `maincode/test_x3d_true_heldout.py` — evaluates the model through the real deployed pipeline (not a clean offline loader), against a split it never trained on. Dataset roots default to the local `To_Be_Trained2` checkout; override with `--rwf-root` / `--scvd-root`. Two split sources:
  - **`--manifest` / `--manifest-path`** (recommended) — reads `dataset_manifest.json`, built by `build_dataset_manifest.py`, which assigns each clip's split from a **SHA-256 content hash** rather than a shuffle. This makes train/val leakage from duplicate files structurally impossible (485 duplicates and a 93-clip leak were found and fixed this way). `--manifest-path 3way` uses the three-way manifest (`dataset_manifest_3way.json`), which holds back a `test` split that **neither training nor checkpoint selection ever reads** — pass `--split test` for the only number in this project that can honestly be called held-out accuracy; `--split val` (the default) is the same split training used to pick its best checkpoint and is optimistic by construction.
  - No `--manifest` flag — legacy path, reconstructs the split by re-running training's seed-42 shuffle. Kept for comparing against pre-manifest history rows in `eval_history.csv`; do not use for new evaluations.
  - `--scene` runs the whole-frame classifier (matching the deployed default); omit it to evaluate the per-track path.
- `maincode/test_scene_live.py` — points the deployed whole-frame detector at a webcam, a video file/folder, or the OBS virtual camera, with an on-screen confidence HUD and a running alarm-rate counter. `--check-scale` measures how tall people are in the source relative to what the model was trained on (see the person-scale note below) and tells you whether the framing is usable before you trust anything else it reports.
- `maincode/generate_eval_report.py` / `calibrate_threshold.py` — batch eval / threshold-sweep tooling.
- `maincode/confidence_trace_plotter.py` — reads `logs/x3d_confidence_trace.csv` (written live by the detector) and plots raw vs. EMA confidence per track, for diagnosing instability (flapping, drift) in a real run.

**Known limitations, both measured, not theoretical:**
- *(`"track"` / `"both"` mode only)* a clip only reaches the X3D classifier if pose tracking holds a **single track ID** for `MIN_BUFFER_FOR_INFERENCE` consecutive frames; clips where that never happens are scored "normal" without the model ever running. This was 17.2% of the held-out set and is the reason `"scene"` (whole-frame, no tracking gate) is now the default. The eval CSV's `frames_with_pose`, `had_any_buffer` and `real_inference_count` columns separate classifier errors from this.
- *(all modes)* the model is trained on footage where a person occupies 24–60% of frame height (median 37%). Below roughly 15% — a typical wide, uncropped street-CCTV framing — detection accuracy collapses, not gradually but close to a cliff (measured: 40/40 clips detected at 37% person-height, 0/40 at 9%). This is a real, unresolved limitation for wide-angle city cameras; see `docs/progress_report_violence_detection.pdf` for the full measurement and the tiled-inference approach under evaluation as a fix. `--check-scale` on `test_scene_live.py` measures this against any given source before deployment.

Training itself happens outside this repo, against a separate training-data checkout — `train_x3d_full.py` supports `--manifest` / `--manifest-path`, `--frame-size` (input resolution; resolves at call time and is written to the checkpoint's `.meta.json`, so it can't silently drift from what inference expects), `--unfreeze-blocks`, `--backbone-lr-mult`, `--weaponized-oversample`, and augmentation toggles for experimenting with fine-tuning depth vs. the frozen-backbone linear-probe baseline.

## Building a Release

Releases are created via GitHub Actions and produce a single Inno Setup installer. `build_release.bat` does the same thing locally, bundling `python-env/`.

### Trigger a Release Build

```bash
git tag v1.0.0
git push origin v1.0.0
```

Pushing a `v*` tag triggers the workflow, which:
- Builds the frontend
- Packages with electron-builder into a plain unpacked folder (`win.target: dir`)
- Compiles that folder into one installer with Inno Setup (`installer/EcoVisionSentinel.iss`)

Download from:
- The workflow run's **Artifacts** tab (there is no auto-created GitHub Release — download the artifact and create/attach a Release yourself if you want one)

You can also manually trigger a build from the **Actions** tab without pushing a tag.

### Run a Release Build

Run `EcoVisionSentinel-Setup-<version>.exe` directly. One folder-choice screen, then it installs like any normal Windows application. The app:
- Spawns backend and AI processes locally
- Waits for both to be ready
- Opens the dashboard

## Important Deployment Notes

⚠️ **GPU Recommended** — Violence detector and detection models use CUDA if available, CPU inference may not keep up with 24/7 live feeds

⚠️ **Build Size** — The bundled python-env (CUDA PyTorch + Ultralytics + PyTorchVideo, TensorRT optional via `--with-tensorrt`) alone is several GB; expect a finished installer in the 3 GB range. This is normal for ML-heavy applications, but it is not a small download. It's shipped as a Release asset or a separate link (Google Drive, etc.), never committed to the repo — GitHub rejects any tracked blob over 100 MB, and `dist_installer/` is gitignored specifically to prevent this from happening by accident.

⚠️ **Unsigned Build** — Not code-signed; Windows SmartScreen may show a warning on first run

## License

Apache License 2.0 — see [LICENSE](LICENSE) for full terms

## Support

For issues, questions, or feature requests, please open an issue on GitHub.

---

**Built for 24/7 security monitoring and real-time threat detection.** 🛡️

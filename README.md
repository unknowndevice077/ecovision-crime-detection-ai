# EcoVision Security Sentinel

Real-time AI-powered security monitoring and threat detection system. It watches a live camera feed, tracks people, detects weapons and violent behavior, and raises alerts — all through a single desktop app with a live dashboard.

Built for **always-on deployment**: the intended use case is a dedicated machine (with a GPU) running continuously, watching a camera feed 24/7 and flagging incidents automatically for review.

> **Status:** core pipeline, backend, and dashboard are wired up and connectivity-tested (dynamic port negotiation, CORS, runtime config all fixed and consistent), but the app has not yet been smoke-tested as a full end-to-end launch on this machine. Robbery detection logic exists (`robbery_vandalism.py`) but is not actively tuned — no labeled robbery dataset yet. Violence detection is actively being iterated on (see `maincode/eval_history.csv` for the accuracy trail).

## What It Does

The system runs three integrated components, packaged as one desktop application:

### 1. **AI/Vision Core** (`maincode/main.py`)
- Pulls frames from a camera feed — camera index is runtime-selectable (not hardcoded), via `/available_cameras` and `/set_camera_index`
- Runs YOLO11-pose for multi-person tracking (each tracked person gets an independent detection state — not limited to one person on screen)
- YOLO-based weapon/sign detector (gun, knife, sign classes) feeding an independent "ARMED" alert path
- X3D-XS clip classifier for violence detection, with EMA smoothing + hysteresis to reduce flapping
- Rule-based robbery and vandalism detection on top of the same pose/weapon tracks
- GPU (CUDA) used automatically when available, with `.engine` (TensorRT) preferred over `.pt` when present; falls back to CPU

### 2. **Backend API** (`app/backend.py`, FastAPI)
- Stores alerts, incident records, and video clips
- Dual database backend: SQLite (standalone/portable builds) or Postgres (when `DATABASE_URL` is set, e.g. Docker Compose) — see `app/db.py`
- Manages connected device (ESP32) telemetry and siren control
- Role-based auth (JWT) and permissions system (see Roles below)
- Serves data to the dashboard over REST + a shared `/ws` WebSocket for live push updates

### 3. **Dashboard (Frontend)** (`app/`, Next.js App Router)
- Dark tactical UI, code-split per tab (`next/dynamic`) so only the active tab's code loads
- Live alerts with severity levels, pushed over the shared WebSocket (`app/context/WebSocketContext.tsx`)
- Incident records and DVR-style footage playback (`RecordsView`, `HistoryView`)
- Crime map with Leaflet (`CrimeReportsView`)
- Admin/DevTeam consoles for user and location management
- Device status monitoring (ESP32 telemetry)

### 4. **Desktop Shell** (`electron/`)
- Electron wrapper for one-click launch
- Automatically starts backend and AI core as child processes, negotiates free ports if the defaults are taken, and writes the resolved ports to `runtime_ports.json` for the frontend to read
- Opens the dashboard pointed at the resolved local services

## Key Features

- ✅ **Real-time detection pipeline** — pose tracking, weapon/sign detection, and violence classification, all per-person (multi-person scenes supported)
- ✅ **Severity-tagged alerts** — incidents ranked LOW → CRITICAL with confidence scores
- ✅ **Records & DVR view** — auto-captured incident clips, 24/7 recordings, custom time range extraction
- ✅ **ESP32 device integration** — hardware connectivity and live telemetry (battery, solar voltage, temperature) + remote siren activation
- ✅ **Role-based access control** — see Roles below; granular per-key permissions on top of role
- ✅ **Runtime-selectable camera index** — no hardcoded camera number, switchable from the dashboard
- ✅ **Self-contained desktop app** — portable release (`electron-builder`, `win.target: portable`), ships its own Python runtime (`python-env/`), no separate Python or Node install needed on the target machine
- ✅ **Docker deployment option** — `docker-compose.yml` / `dockerfile.combined` / `dockerfile.detector` / `Dockerfile.backend` for a non-Electron, containerized deployment (Postgres-backed)
- 🚧 **Robbery/vandalism detection** — rule logic exists, not yet actively tuned/validated with real data

## Roles

| Role | Notes |
|---|---|
| `DEVTEAM` | System-wide read visibility, user/location management console |
| `PRECINCT_CAPTAIN` | Admin role — creates/manages `POLICE` accounts |
| `BARANGAY_CAPTAIN` | Admin role — creates/manages `BARANGAY` accounts |
| `POLICE` | Standard operator, scoped to their precinct/barangay |
| `BARANGAY` | Standard operator, scoped to their barangay |

Permissions are granular beyond role (e.g. `manage_cameras`, `confirm_dismiss_alerts`) — see `app/hooks/usePermissions.ts` and `user_permissions` table.

## Tech Stack

| Component | Technology |
|---|---|
| Frontend | Next.js (App Router), React, Tailwind CSS v4, TypeScript |
| Desktop | Electron |
| Backend API | FastAPI (Python), SQLite or Postgres |
| AI/Vision | Ultralytics YOLO11 (pose + weapon detection), PyTorchVideo X3D-XS (violence classification), OpenCV |
| Hardware | ESP32 integration |
| Containerization | Docker / Docker Compose (alternative to the Electron desktop build) |

## Repository Structure

```
EcoVisionCode/
├── app/                        # Next.js pages/components + FastAPI backend
│   ├── page.tsx                # Main dashboard shell
│   ├── components/              # Tab views (CrimeReportsView, RecordsView, ProfileView, dashboard/*)
│   ├── context/WebSocketContext.tsx  # Shared live-update WebSocket + useLiveChannel hook
│   ├── hooks/                  # useRuntimeConfig, usePermissions
│   ├── backend.py              # FastAPI server
│   ├── db.py                   # SQLite/Postgres dual-backend shim
│   ├── port_utils.py           # Runtime port negotiation helpers
│   └── schema_final.sql / schema_sqlite.sql
├── electron/                   # Desktop shell (main.js, setup/launch windows)
├── maincode/                   # AI/vision pipeline + model eval tooling
│   ├── main.py                 # Vision pipeline (pose + weapon + violence + rules)
│   ├── robbery_vandalism.py    # Robbery/vandalism rule logic
│   ├── x3d_violence_detector.py
│   ├── test_x3d_true_heldout.py    # True held-out accuracy eval (live pipeline)
│   ├── generate_eval_report.py, calibrate_threshold.py
│   ├── confidence_trace_plotter.py # Plots live per-frame confidence trace
│   └── eval_history.csv        # Accuracy/recall/precision/FPR log across eval runs
├── weights/                    # Model weights (gitignored, added manually)
├── config.json / config.development.json / config.production.json
├── requirements.txt / requirements-backend.txt / requirements-detector.txt
├── docker-compose.yml, dockerfile.combined, dockerfile.detector, Dockerfile.backend
├── package.json
├── setup.bat                   # Environment setup
├── run_dev_system.bat          # Development startup
└── .github/workflows/          # CI (backend tests, installer build)
```

## Getting Started (Development)

### Requirements
- Python 3.11+
- Node.js 18+
- Both available on your system PATH

### Setup

```bash
setup.bat
```

This command:
- Creates a Python virtual environment (`.venv`)
- Installs Python dependencies from `requirements.txt`
- Installs Node.js dependencies via `npm install`

### Add Model Weights

Manually place trained model weights into the `weights/` directory (gitignored due to size):
- `yolo11s-pose.pt` (or `.engine`) — YOLO11 pose detection
- `weapon_signs.pt` (or `.engine`) — Weapon and sign detection
- `x3d_xs_violence_best.pt` — Violence classification

### Start Development

```bash
run_dev_system.bat
```

This boots:
- Backend API (port 8000)
- AI/Vision core (port 8001)
- Next.js dev server (port 3000)

All three shut down together when you exit. Ports auto-negotiate to a free alternative if the default is taken; the frontend picks up the resolved ports at runtime (`runtime_ports.json`), so hardcoded `localhost:8000`/`8001` URLs are not required anywhere in the frontend.

### Docker (alternative to Electron)

```bash
docker compose up
```

Uses `docker-compose.yml` (Postgres-backed, `DATABASE_URL` set for the backend/detector containers). See `dockerfile.combined` / `dockerfile.detector` / `Dockerfile.backend` for the individual service builds.

## Configuration

Runtime behavior is configured via `config.json` at the repo root, with `config.development.json` / `config.production.json` overrides selected by the `APP_ENV` environment variable:
- Detection thresholds (`detection.violence` block: confidence threshold, hysteresis, EMA smoothing, check interval, etc.)
- Camera index
- Alert cooldowns
- ESP32 settings
- Network configuration (CORS origins, backend/AI URLs)

Most tuning requires only editing the config file — no code changes needed.

## Model Evaluation & Retraining

The X3D violence classifier's accuracy is tracked over time in `maincode/eval_history.csv` — every run of `test_x3d_true_heldout.py` (the true held-out accuracy eval, run through the actual live pipeline code path) appends a row with accuracy/recall/precision/false-positive-rate and the config snapshot used.

- `maincode/test_x3d_true_heldout.py` — reconstructs the exact train/val split used during training (same seed) and evaluates only the clips the model never trained on, through the real deployed pipeline (not a clean offline loader).
- `maincode/generate_eval_report.py` / `calibrate_threshold.py` — batch eval / threshold-sweep tooling.
- `maincode/confidence_trace_plotter.py` — reads `logs/x3d_confidence_trace.csv` (written live by the detector) and plots raw vs. EMA confidence per track, for diagnosing instability (flapping, drift) in a real run.

Training itself happens outside this repo, against a separate training-data checkout — `train_x3d_full.py` supports `--unfreeze-blocks`, `--backbone-lr-mult`, `--weaponized-oversample`, and augmentation toggles for experimenting with fine-tuning depth vs. the frozen-backbone linear-probe baseline.

## Building a Release

Releases are created via GitHub Actions and produce a portable Windows build.

### Trigger a Release Build

```bash
git tag v1.0.0
git push origin v1.0.0
```

Pushing a `v*` tag triggers the workflow, which:
- Builds the frontend
- Packages with electron-builder into a **portable single `.exe`** (no install wizard, no admin elevation — just download and run)

Download from:
- The workflow run's **Artifacts** tab (there is no auto-created GitHub Release — download the artifact and create/attach a Release yourself if you want one)

You can also manually trigger a build from the **Actions** tab without pushing a tag.

### Run a Release Build

Run `EcoVisionSentinel-<version>-portable.exe` directly — no unzip, no install step. The app:
- Spawns backend and AI processes locally
- Waits for both to be ready
- Opens the dashboard
- Runs standalone with no installation required

## Important Deployment Notes

⚠️ **GPU Recommended** — Violence detector and detection models use CUDA if available, CPU inference may not keep up with 24/7 live feeds

⚠️ **Build Size** — The bundled python-env (CUDA PyTorch + Ultralytics + PyTorchVideo) alone is several GB; expect a finished portable exe in the 3–5GB+ range. This is normal for ML-heavy applications, but it is not a small download.

⚠️ **Unsigned Build** — Not code-signed; Windows SmartScreen may show a warning on first run

## License

Apache License 2.0 — see [LICENSE](LICENSE) for full terms

## Support

For issues, questions, or feature requests, please open an issue on GitHub.

---

**Built for 24/7 security monitoring and real-time threat detection.** 🛡️

# EcoVision Security Sentinel - does not work yet

Real-time AI-powered security monitoring and threat detection system. It watches a live camera feed, tracks people, detects weapons and violent behavior, and raises alerts — all through a single desktop app with a live dashboard.

Built for **always-on deployment**: the intended use case is a dedicated machine (with a GPU) running continuously, watching a camera feed 24/7 and flagging incidents automatically for review.

## What It Does

The system runs three integrated components, packaged as one desktop application:

### 1. **AI/Vision Core** 
- Pulls frames from a camera feed
- Runs YOLO11-pose for person tracking
- Weapon and sign detection model
- X3D-XS clip classifier for violence detection
- Detection runs on rolling buffer of recent frames for efficiency

### 2. **Backend API** 
- FastAPI server for data management
- Stores alerts, incident records, and video clips
- Manages connected device (ESP32) telemetry
- Serves data to the dashboard

### 3. **Dashboard (Frontend)** 
- Next.js/React application
- Dark tactical UI
- Live alerts with severity levels
- Incident records and DVR-style footage playback
- Device status monitoring

### 4. **Desktop Shell** 
- Electron wrapper for one-click launch
- Automatically starts backend and AI core
- Opens dashboard pointed at local services

## Key Features

- ✅ **Real-time detection pipeline** — pose tracking, weapon/sign detection, and violence classification
- ✅ **Severity-tagged alerts** — incidents ranked LOW → CRITICAL with confidence scores
- ✅ **Records & DVR view** — auto-captured incident clips, 24/7 recordings, custom time range extraction
- ✅ **ESP32 device integration** — hardware connectivity and live telemetry (battery, solar voltage, temperature)
- ✅ **Role-based access control** — POLICE and BARANGAY operator roles with sector assignment
- ✅ **Self-contained desktop app** — portable release, no separate Python or Node install needed

## Tech Stack

| Component | Technology |
|---|---|
| Frontend | Next.js (App Router), React, Tailwind CSS v4, TypeScript |
| Desktop | Electron |
| Backend API | FastAPI (Python) |
| AI/Vision | Ultralytics YOLO11, PyTorchVideo X3D-XS, OpenCV |
| Hardware | ESP32 integration |

## Repository Structure

```
EcoVisionCodeAI/
├── app/                       # Next.js pages and components
├── electron/                  # Desktop shell configuration
├── maincode/                  # AI/vision and backend logic
│   ├── main.py               # Vision pipeline
│   ├── backend.py            # FastAPI server
│   ├── robbery_vandalism.py
│   └── x3d_violence_detector.py
├── weights/                  # Model weights (gitignored, added manually)
├── config.json              # Runtime configuration
├── requirements.txt         # Python dependencies
├── package.json
├── setup.bat               # Environment setup
├── run_dev_system.bat      # Development startup
└── .github/workflows/      # CI/CD pipeline
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
- `yolo11s-pose.pt` — YOLO11 pose detection
- `weapon_signs.pt` — Weapon and sign detection
- `x3d_xs_violence_best.pt` — Violence classification

### Start Development

```bash
run_dev_system.bat
```

This boots:
- Backend API (port 8000)
- AI/Vision core (port 8001)
- Next.js dev server (port 3000)

All three shut down together when you exit.

## Configuration

All runtime behavior is configured in `config.json` at the repo root:
- Detection thresholds
- Camera index
- Alert cooldowns
- ESP32 settings
- Network configuration

Most tuning requires only editing the config file — no code changes needed.

## Building a Release

Releases are created via GitHub Actions and produce a portable Windows build.

### Trigger a Release Build

```bash
git tag v1.0.0
git push origin v1.0.0
```

Pushing a `v*` tag triggers the workflow, which:
- Builds the frontend
- Packages with electron-builder
- Creates a portable `.zip` file

Download from:
- The tagged **GitHub Release** page (auto-attached)
- The workflow run's **Artifacts** tab

You can also manually trigger a build from the **Actions** tab without pushing a tag.

### Run a Release Build

Unzip `EcoVisionSentinel-<version>-win64.zip` anywhere and run `EcoVisionSentinel.exe`. The app:
- Spawns backend and AI processes locally
- Waits for both to be ready
- Opens the dashboard
- Runs standalone with no installation required

## Important Deployment Notes

⚠️ **GPU Recommended** — Violence detector and detection models use CUDA if available, CPU inference may not keep up with 24/7 live feeds

⚠️ **Build Size** — Expect 500 MB to several GB depending on PyTorch/CUDA bundle. This is normal for ML-heavy applications.

⚠️ **Unsigned Build** — Not code-signed; Windows SmartScreen may show a warning on first run

## License

Apache License 2.0 — see [LICENSE](LICENSE) for full terms

## Support

For issues, questions, or feature requests, please open an issue on GitHub.

---

**Built for 24/7 security monitoring and real-time threat detection.** 🛡️

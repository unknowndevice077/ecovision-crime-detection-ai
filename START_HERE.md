# Start here

The one page. What to run, which models are live, where everything lives, and
the traps. Current as of 14 Aug 2026.

---

## 1. Run it

| Goal | Command | Notes |
|---|---|---|
| **Run everything on this PC** | `run_dev_system.bat` | Backend + detector + dashboard, hot reload. Uses `.venv`. |
| **Build the installer** | `build_release.bat` | → `dist\EcoVisionSentinel-1.0.0-portable.exe` (1.63 GB). ~30 min. |
| …with the GPU optimizer bundled | `build_release.bat --with-tensorrt` | Adds ~3.2 GB. Only if the target PC has no internet and you want the optimize step there. |
| **Install on another PC** | copy the `.exe`, double-click | No Python, no pip, no terminal, no internet. |
| **Check a machine can run it** | `.venv\Scripts\python.exe preflight.py` | Weights, schema, GPU, RAM, throughput. |
| **Speed up models for this GPU** | `.venv\Scripts\python.exe optimize_weights.py` | Optional. Prints before/after. `--revert` undoes it. |
| **Turn a detector on/off** | Dashboard → **AI Models** tab | DevTeam login. Restart detection to apply. |
| **Rebuild paper figures** | `.venv\Scripts\python.exe tools\make_defense_figures.py` | → `docs\figures\` |
| **Rebuild explainer GIFs** | `.venv\Scripts\python.exe tools\make_explainer_gifs.py` | → `docs\media\` |
| **Rebuild the defense PDF** | `node_modules\electron\dist\electron.exe tools\html_to_pdf.js docs\model_behavior_defense.html docs\EcoVision_Defense_Reference.pdf` | See trap #5 below. |

**First-run credentials** are written once to `devteam_credentials.txt` in the
writable directory, and shown in a dialog. Not shown again.

---

## 2. The models

### Live right now

| File | Loaded by | Role |
|---|---|---|
| `yolo11s-pose.pt` | `main.py`, hardcoded name | People + 17 body keypoints |
| `weapon_signs.pt` | `main.py`, hardcoded name | Gun / Knife / Sign |
| `x3d_xs_violence_scene_corpus_neg.pt` | `detection.violence.scene_model_path` | **Deployed violence model.** Mode is `scene`, threshold 0.50, confirmations 3 |
| `x3d_xs_robbery_scene.pt` | `detection.robbery.model_path` | Robbery, threshold 0.70 |

### Present but not running

| File | Why it is here |
|---|---|
| `x3d_xs_violence_scene_daynight.pt` | **Measured better than the deployed model, not yet adopted.** Same 95.0% recall, real-camera alarms 12.75 → 4.50/hr, flyover 45 → 6/hr. Adopt by pointing `scene_model_path` at it. Full evidence in its `.meta.json`. |
| `x3d_xs_violence_best.pt` | `detection.violence.model_path` — the **per-track** model. Only loads if `mode` is `track` or `both`. `run_dev_system.bat` also checks for it. |
| `x3d_xs_vandalism_scene.pt` | Ships so vandalism can be switched on and inspected. **Disabled** — 37.5% FPR at its best threshold. |

`weights/archive/` holds 14 superseded files (173 MB), moved rather than deleted.
`weights/README.md` explains each. **Nothing in `archive/` is loadable.**

### Measured performance

| Class | State | Headline | Say this before you are asked |
|---|---|---|---|
| Violence | on | 95.0% of events, 17 alarms/hr | Per camera that is 0 / 6 / 45. One average hides a >45× spread. |
| Robbery | on | 84.2% acc, 5.6% FPR | **Recall is 65.3%** — misses ~1 in 3. 8 test scenes, not 139 samples. |
| Vandalism | **off** | — | Trained *and* measured. 70.3% accuracy is *below* the 78.4% always-guess baseline. |

**Recall on the actual deployment cameras is unmeasured for every class** — no
labelled incident has ever been recorded on them. Property of the problem, not
an oversight.

---

## 3. Where things live

```
EcoVisionCode/
├── START_HERE.md          ← you are here
├── INSTALL.md             installing elsewhere, minimum specs, the optimize step
├── README.md              architecture and stack
│
├── config.json            EVERY tunable: thresholds, model paths, on/off switches
├── preflight.py           "will this machine run it?" — also runs inside the app
├── optimize_weights.py    optional TensorRT compilation + before/after report
│
├── maincode/              the detector
│   ├── main.py                 camera loop, weapon logic, alert emission
│   └── x3d_violence_detector.py    model wrapper, smoothing, confirmation
│
├── app/                   backend AND frontend share this folder (see trap #1)
│   ├── backend.py              FastAPI: incidents, users, cameras, AI-models API
│   ├── db.py                   SQLite by default; Postgres if DATABASE_URL set
│   ├── globals.css             the design tokens everything follows
│   └── components/dashboard/   DevteamView (has the AI Models tab), HistoryView…
│
├── electron/              desktop shell: setup + launch windows, console.css, logo
├── weights/               live models (+ archive/ for superseded)
├── docs/                  see docs/README.md — it says what to cite
├── tools/                 analysis + build scripts, not part of the running system
└── archive/               superseded files kept rather than deleted
```

**Training lives outside this repo**, in `D:\EcoVisionImagesTraining` — manifests,
training scripts, evaluation scripts, and `ucf_source/` (650 untrimmed UCF-Crime
videos extracted for future MIL work).

---

## 4. Documentation

`docs/README.md` is the index and says what is citable. The short version:

| Need | File |
|---|---|
| Any number you quote | `docs/detection_performance_report.md` |
| The full narrative and derivations | `docs/progress_report_violence_detection.md` |
| Present to a panel | `docs/model_behavior_defense.html` or `EcoVision_Defense_Reference.pdf` |
| Explain the method | `docs/convolution_explainer.md` |
| Figures for the thesis | `docs/figures/*.pdf` (vector) and `*.png` (300 dpi) |
| What changed and why | `docs/final_checks.md` |

**Never cite** `progress_report_violence_detection.pdf` or `.html` — they were
exported 12 Aug and the `.md` gained §30–§32 on 14 Aug.

---

## 5. Traps

**1. `app/` mixes Python and TypeScript, and must stay that way.** Next.js's App
Router requires `layout.tsx`/`page.tsx` at exactly that path, and `package.json`,
`electron/main.js`, `run_dev_system.bat` and `preflight.py` all reference
`app/backend.py`. Splitting it is tidier to look at and breaks four things.

**2. `config.json` is the only place to change behaviour.** Keys beginning `_`
are prose notes explaining why a value is what it is. Read the note before
changing the value above it.

**3. A `.meta.json` beside a checkpoint overrides `config.json`** for input
geometry. Deliberate — geometry is a property of the weights, and config has no
way to be right about it. A wrong `input_repr` costs 5.3 accuracy points
silently.

**4. `.engine` files are machine-specific and are never packaged.** Build them on
the machine that runs them. If one fails to load, the app falls back to the `.pt`
and logs a line. Safe to delete at any time.

**5. `ELECTRON_RUN_AS_NODE=1` is set in this environment.** It makes
`require("electron")` return undefined, so `npx electron` silently runs scripts
as plain Node. Call `node_modules\electron\dist\electron.exe` directly.

**6. Two weight lists must stay in step**: `package.json`'s `extraResources`
filter and `preflight.py`'s `required` dict. Preflight lists what the app
*needs*; extraResources lists that plus the vandalism model. Update both when
you deploy a new model — an earlier build shipped this machine's `.engine` files
and 200 MB of dead checkpoints because the whole folder was copied.

**7. Toggling a model needs a detector restart.** The AI core reads `config.json`
once at startup. Hot-reloading would mean swapping models with a half-full clip
buffer.

**8. Never commit unless asked.**

---

## 6. Open decisions

| | |
|---|---|
| **Adopt the day+night violence model?** | Fully measured, strictly better at the current operating point. One line in `config.json`. |
| **Retrain robbery on 400 sources?** | 43 → 400 available in `ucf_source/`. Unattended, hours. Biggest remaining win — 65.3% recall is the weakest number in the system. |
| **Rebuild the installer?** | `package.json` changed after the last build, so the current `.exe` predates the vandalism model. |
| **Model on/off for barangay users?** | Recommended: read-only visibility + a request-and-approve flow, **not** a direct switch. A local official with a silent off switch reproduces the project's own worst failure mode. |

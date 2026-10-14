# Start here

The one page. What to run, which models are live, where everything lives, and
the traps. Current as of 7 Oct 2026 (testing phase).

---

## 1. Run it

| Goal | Command | Notes |
|---|---|---|
| **Run everything on this PC** | `run_dev_system.bat` | Backend + detector + dashboard, hot reload. Uses `.venv`. |
| **Build the installer** | `build_release.bat` | → `dist_installer\EcoVisionSentinel-Setup-1.0.0.exe`, ~3 GB, single file. ~15 min (`--dir` packaging) + ~7 min (Inno Setup compile). |
| …with the GPU optimizer bundled | `build_release.bat --with-tensorrt` | Adds ~3.2 GB. Only if the target PC has no internet and you want the optimize step there. |
| **Install on another PC** | copy `EcoVisionSentinel-Setup-1.0.0.exe`, run it once | One installer, one folder-choice screen, everything lands in the folder you pick. No Python, no pip, no terminal, no internet needed at install time. Requires Inno Setup 6 on the *build* machine only — see trap #9. |
| **Check a machine can run it** | `.venv\Scripts\python.exe preflight.py` | Weights, schema, GPU, RAM, throughput. |
| **Speed up models for this GPU** | `.venv\Scripts\python.exe optimize_weights.py` | Optional. Prints before/after. `--revert` undoes it. |
| **Turn a detector on/off** | Dashboard → **AI Models** tab | DevTeam login. All four classes are toggleable — violence, weapons, vandalism, robbery. Robbery and vandalism carry an **Experimental** badge. Restart detection to apply. |
| **Rebuild paper figures** | `.venv\Scripts\python.exe tools\make_defense_figures.py` | → `docs\figures\` |
| **Rebuild explainer GIFs** | `.venv\Scripts\python.exe tools\make_explainer_gifs.py` | → `docs\media\` |
| **Rebuild the defense PDF** | `node_modules\electron\dist\electron.exe tools\html_to_pdf.js docs\model_behavior_defense.html docs\EcoVision_Defense_Reference.pdf` | See trap #5 below. |

**First-run credentials, testing phase:** `TESTING_PHASE_FIXED_CREDENTIALS` in
`electron/main.js` is `true`, so every install shows the same fixed login —
`devteam` / `EcoVision2026Test!` — in a dialog on first run, rather than a
random per-install password. **Must be reverted before any real deployment**
(flip that flag to `false`); the random-password path still exists and falls
back to writing `devteam_credentials.txt` once, shown in a dialog and never
again, exactly as before.

---

## 2. The models

### Live right now

| File | Loaded by | Role |
|---|---|---|
| `yolo11s-pose.pt` | `main.py`, hardcoded name | People + 17 body keypoints |
| `weapons_v3.pt` | `detection.weapon.model_path`; runs at 960 px | Gun / knife / phone (phone is detected so it can be dropped). Deployed 3 Oct. |
| `x3d_xs_violence_scene_ucabal.pt` | `detection.violence.scene_model_path` | **Deployed violence model** (since 3 Oct). Mode `scene`, threshold 0.45, 2 in a row, person-crop confirmed. |
| `x3d_xs_violence_scene_ucabal_mined.pt` | `detection.violence.scene_ensemble_paths` | Ensemble partner (since 5 Oct) — its probability is averaged with ucabal's on every check. Not used alone. |
| `x3d_xs_violence_best.pt` | `detection.violence.model_path` | Per-track model; decides *who* gets boxed once the scene model has decided an alert fires. |
| `x3d_xs_robbery_scene.pt` | `detection.robbery.model_path` | Robbery, **experimental**, threshold 0.85, 3 in a row. |
| `x3d_xs_vandalism_scene_v3.pt` | `detection.vandalism.model_path` | Vandalism, **experimental**, threshold 0.60, 3 in a row, person-crop confirmed. |
| `vandalism_marks_v2.pt` | `detection.vandalism.marks_model_path` | Graffiti / tag detector feeding the vandalism wrist-motion rule. |

### Present but not running

Rollback targets only — `weapons_v2.pt`, `x3d_xs_violence_scene_daynight.pt`,
`x3d_xs_violence_scene_corpus_neg.pt`, `x3d_xs_vandalism_scene.pt` (v1),
`weapon_signs.pt`, `vandalism_marks.pt`. Each one's config note says how to roll
back to it.

`weights/archive/` holds superseded files, moved rather than deleted.
`weights/README.md` explains each. **Nothing in `archive/` is loadable.**

### Measured performance

All on footage the models never trained on: four held-out street cameras
(2 h, nothing criminal in them) for false alarms, and held-out crime clips
spliced into continuous street footage for how many events raise an alarm.

| Class | State | Headline | Say this before you are asked |
|---|---|---|---|
| Violence | on | 88.0% of 200 fights, 2.0 false alarms/hr | 93.1% accuracy on 1,310 test clips. The 24 misses are mostly ground fights, distant crowds and close-up/occluded views. |
| Weapons | on | 89.6% of large guns, 39.4% of street-size guns | Small, distant guns are the hard case — under ~5% of the frame, recall is ~6%. |
| Vandalism | on, experimental | 87.1% of 31 acts, 1.0 false alarm/hr | Only 31 test acts from few source videos — approximate. |
| Robbery | on, experimental | 0.5 false alarms/hr | **Catches only ~40% of test clips** (8.3% of events in continuous footage). Tuned to stay quiet rather than cry wolf. |

**Recall on the actual deployment cameras is still unmeasured for every class** —
no labelled incident has ever been recorded on them. Property of the problem,
not an oversight.

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
│   ├── main.py                 camera loop, alert emission
│   ├── detection_rules.py      every alert rule (weapon gates, tracking, ARMED/ASSAULT) —
│   │                           the evaluation scripts import this same file
│   └── x3d_violence_detector.py    model wrapper, ensemble, person-crop confirmation, smoothing
│
├── app/                   backend AND frontend share this folder (see trap #1)
│   ├── backend.py              FastAPI app + entry point (launched by path: keep it here)
│   ├── backend_core.py         config, auth, DB setup, permissions, shared helpers
│   ├── routers/                every endpoint, one module per area of the API
│   ├── config_store.py         writes only the config keys someone changed
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
| Roles, permissions, DevTeam console | `README.md` → Organization & Roles |

**Never cite** `progress_report_violence_detection.pdf` or `.html` — they were
exported 12 Aug and the `.md` gained §30–§32 on 14 Aug.

---

## 5. Traps

**1. `app/` mixes Python and TypeScript, and must stay that way.** Next.js's App
Router requires `layout.tsx`/`page.tsx` at exactly that path, and `package.json`,
`electron/main.js`, `run_dev_system.bat` and `preflight.py` all reference
`app/backend.py`. Moving it out of `app/` is tidier to look at and breaks four
things. (Its endpoints already live in `app/routers/` and its shared code in
`app/backend_core.py`; `backend.py` itself has to stay where it is.)

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

**9. There is no NSIS build and no portable `.exe` anymore — both were
replaced by Inno Setup on 18-19 Aug.** `package.json`'s `win.target` is `dir`
now; electron-builder only produces the plain `dist\win-unpacked` folder, and
`installer\EcoVisionSentinel.iss` wraps that into the one real installer.
Reasons, in order of discovery:
  - **NSIS** (`nsis` and `portable` targets both): `makensis.exe` is a 32-bit
    process that mmaps the entire combined payload into one archive at BUILD
    time — a hard ~2 GB ceiling. Hit twice (8.65 GB and again at 5.4 GB
    python-env).
  - **MSI**: got past the build, but failed to *install* on real machines with
    error 2755 — a known electron-builder/WiX weak point with large payloads,
    not fixable from `package.json` config.
  - **Inno Setup** has no such ceiling (streams via CAB, not memory-mapped)
    and is what much commercial Windows software ships with. Requires
    Inno Setup 6 on the **build machine only** — `build_release.bat` looks for
    `ISCC.exe` at the usual install paths and tells you where to get it
    (jrsoftware.org/isdl.php, free, ~10 MB, no admin needed) if it's missing.
  - `PrivilegesRequired=lowest` in the `.iss` — no UAC needed for this testing
    build, and any drive/folder the account can write to works. Flip to
    `admin` before a real production rollout.

**9b. Two more installer bugs found and fixed 18-19 Aug, worth knowing if
something in this area looks broken again:**
  - `writeGeneratedEnv()` forced `APP_ENV=production`, which made the app
    silently load `config.production.json` (a leftover from the since-removed
    Docker setup — violence only, no robbery/vandalism, stale settings)
    instead of `config.json`, on
    *every* packaged install. Fixed by using `APP_ENV=desktop` instead — a
    value that matches neither `config.development.json` nor
    `config.production.json`, so it always falls through to plain
    `config.json`. It was set in **two places** (the generated `.env` *and* an
    explicit override in `launchMainApp()`'s `spawnPython()` calls, which wins
    since it's set before `load_dotenv()` runs) — both must stay in sync.
  - Backend/AI-core ports (8000/8001) are still hardcoded with no retry, only
    the frontend searches for a free port. `launchMainApp()` now checks both
    before spawning anything and fails with a specific "already in use,
    probably a leftover process" message instead of a silent 60s timeout.

---

## 6. Open decisions

| | |
|---|---|
| **Add a vision-language double-check?** | Qwen3-VL-2B checking each alarm: violence 88.0% → 90.5% of fights at 2.0 → 0.5 false alarms/hr; robbery 8.3% → 51–60% of events at 2–3/hr. ~7 s (GPU) or ~15 s (CPU) added per alert. **Confirmed on 5 h of held-out footage (7 Oct)**: violence 90.5% at 0.4/hr, robbery 51.1% at 2.0/hr. **In the app since 7 Oct, experimental** (`detection.verifier`), with long candidates re-checked every 7 s (fights 94.0% at 0.4/hr): GPU or CPU backend (CPU: llama.cpp 4-bit, no VRAM, same accuracy), and robbery comes from the fight detector's whole-frame candidates checked by two questions — is it criminal, and is it more robbery than fight (robbery alerts 59.4% of events at 0.6/hr, 20.5% of fights also labelled robbery; the robbery model is only the fallback). |
| **Testing-phase shortcuts to revert before real use** | `TESTING_PHASE_FIXED_CREDENTIALS` in `electron/main.js`; `PrivilegesRequired=lowest` in the `.iss`. |
| **Rebuild the installer?** | Models and config changed on 3–5 Oct; any `.exe` built before that ships the old models. |
| **Model on/off for barangay users?** | Recommended: read-only visibility + a request-and-approve flow, **not** a direct switch. A local official with a silent off switch reproduces the project's own worst failure mode. |

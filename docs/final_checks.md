# Final checks — what changed, what was verified, what is still open

Written 2026-08-14, after the shippable build. Everything below was run, not
assumed; where something was checked and found broken it says what broke and how
it was found, because the discovery method is the reusable part.

---

## 1. The short version

| | |
|---|---|
| **Installer** | `dist\EcoVisionSentinel-1.0.0-portable.exe`, 1.63 GB, verified contents |
| **Detectors shipping** | violence + robbery on, vandalism off but present and switchable |
| **Preflight** | READY — all checks pass |
| **Frontend** | `tsc --noEmit` clean |
| **Database** | 13 tables, applies cleanly, no dangling foreign keys |
| **Batch scripts** | every referenced path resolves |
| **Weights** | 6 active files; 9 superseded (173 MB) moved to `weights/archive/` |

---

## 2. Bugs found and fixed

Ordered by how badly each would have hurt.

### 2.1 The installer could not create its own database

`package.json` never packaged `app/schema_sqlite.sql`. The app selects that file
whenever `DATABASE_URL` is unset — which is exactly the standalone case the
installer exists for. **The packaged `.exe` would have failed on first run, on a
machine with no terminal to show why.**

Found by reading `backend.py`'s database bootstrap against the `extraResources`
list rather than by running it.

### 2.2 The build shipped this machine's GPU-compiled models

`extraResources` copied the whole `weights/` folder, which swept in `.engine`
files compiled for a GTX 1660 SUPER. On any other GPU each one fails to load and
logs an error every launch — 70 MB of guaranteed failure, plus ~200 MB of
superseded checkpoints nothing loads.

Fixed with an explicit six-file whitelist that **mirrors `preflight.py`'s
required set**, so a model that ships is a model that gets checked.

Found by listing the packaged `weights/` folder after the first successful
build instead of trusting the config.

### 2.3 The build's own safety check was checking the wrong files

`build_release.bat` verified `x3d_xs_violence_best.pt` — the *per-track* model —
while deployment runs scene mode off a different checkpoint, and it never checked
the robbery model at all. **A build could pass every check and ship a
non-functional detector.** Replaced with a `preflight.py` call, which derives its
list from what is actually deployed.

### 2.4 Preflight would have failed on every packaged install

It looked for the schema only in `app/`, but electron-builder copies it to
`backend/`. The one place the check matters is the one place it would have
false-failed. Now checks both.

### 2.5 The robbery model path resolved to the wrong directory

Resolved against `BASE_DIR` (`maincode/`) rather than the repo root. Worked on
the dev machine by accident of the working directory; would have failed at
runtime on the target.

### 2.6 A double-softmax, re-introduced by me and caught before shipping

While exporting X3D to TensorRT I wrapped the model in a second `Softmax`. The
network head already ends in one, so this recreated the exact bug the codebase
carries a 13-line comment about: softmax over `[p, 1-p]` can only produce values
in `[0.2689, 0.7311]`.

**It was invisible to the obvious test.** The engine agreed with my
equally-wrapped torch model to 0.00000. It was caught by reading the head
structure — `proj → Softmax → AdaptiveAvgPool3d` — not by comparing outputs.

### 2.7 A verification script that verified nothing

My first engine/`.pt` comparison hand-rolled a state reset and missed
`_scene_last_check`. The second run therefore replayed cached results and
reported a false mismatch. A real `reset_scene()` already existed. **The
measurement was wrong, not the thing being measured** — worth remembering when a
result looks surprising.

### 2.8 `_comment` in a schema-validated config

Added a `_comment` key inside `extraResources` to document the whitelist.
electron-builder validates that object strictly and rejected the whole build.
JSON has no comments; the reasoning moved to `INSTALL.md`. Config is now scanned
for schema-invalid keys *before* a build is started.

### 2.9 Documentation that pointed at nothing

`docs/convolution_explainer.md` gave a regeneration command for
`render_conv_explainer.py`, a script that does not exist anywhere in the
repository. Now stated plainly, with working animations that replace it.

### 2.10 Chart and figure collisions

A caption ran through an axis label in `fig6`; panel titles overprinted y-ticks
in the HTML charts. Both fixed at the cause — caption offset now scales with line
count, panel titles get their own line — rather than nudged.

---

## 3. What was added

### 3.1 Optional GPU optimization, with a measured before/after

`optimize_weights.py` rewritten to cover X3D as well as YOLO (X3D has no
exporter, so it is torch → ONNX → TensorRT by hand), and wired into the
installer as an optional step.

| Model | Before | After | Gain |
|---|---|---|---|
| Violence | 27.6 ms | 8.8 ms | **3.24×** |
| Person / pose | 23.6 ms | 9.2 ms | 2.56× |
| Robbery | 17.7 ms | 10.4 ms | 1.71× |
| Weapon / sign | 18.6 ms | 13.1 ms | 1.43× |

Accuracy is **verified, not assumed**: each compiled model is diffed against its
`.pt` and discarded if it disagrees. Measured through the live detector: max
deviation 0.000026 over 150 frames, **zero verdict changes**.

**TensorRT is not bundled** — `tensorrt_libs` alone is 3,245 MB against a
`python-env` already at 5.13 GB. `build_release.bat --with-tensorrt` includes it
for offline use.

**ONNX Runtime was evaluated as a cheaper alternative and rejected on
measurement.** Its CUDA provider needs CUDA 13; on this CUDA 12.1 environment it
silently fell back to CPU and ran at 61.6 ms against PyTorch's 18.7 — three times
*slower* than doing nothing, while reporting success.

### 3.2 Model on/off switches on the dashboard

New **AI Models** tab in the DevTeam view. Per detector: a switch, four measured
numbers, threshold, confirmation setting, weight presence, and its caveat.

Design decisions:

- **Numbers come from `config.json`, never from TypeScript.** A copy in the
  frontend would keep showing old accuracy after a retrain.
- **Enabling a measured-bad model asks first; disabling never does.** Turning a
  detector off can only reduce output.
- **The backend refuses to enable a model whose weights are missing**, rather
  than letting the AI core crash at next startup with no visible cause.
- **Config is written via temp-file-then-replace.** A torn write to
  `config.json` takes down both the backend and the detector.
- **Toggles require a restart, and the UI says so.** Hot-swapping models with a
  half-full clip buffer is a worse failure than a restart.

### 3.3 Installer UI rebuilt on the product's own design system

`app/globals.css` is an explicit system — institutional blue, *"color reserved
STRICTLY for status semantics"*, *"gradients, glows and idle animation cause
fatigue"*. The installer contradicted every line: green accent, green gradients,
a glow on every button.

Rebuilt both windows on the real tokens via a shared `electron/console.css`, so
green now appears only when something *is* nominal. The pulsing status dot is
kept — the system names it the one sanctioned glow.

`logo.png` was already transparent; it is split so the eye mark is the graphic
and the wordmark renders as live text, because the dark-green lockup sits at
~1.4:1 on the console background.

### 3.4 Defense material

| Output | Path |
|---|---|
| Interactive reference, 11 sections, 8 charts, 3 animations | `docs/model_behavior_defense.html` |
| Same as PDF, print-styled | `docs/EcoVision_Defense_Reference.pdf` |
| Paper figures, 300 dpi + vector | `docs/figures/*.png`, `*.pdf` |
| Convolution explainer with animations | `docs/convolution_explainer.md` |

Charts use a palette validated for colorblind separation (adjacent-pair ΔE 9.4
deutan, 20.9 normal-vision) against this product's own dark surface, and every
series is directly labeled so identity never rests on hue.

The confirmation-tradeoff chart is deliberately **two stacked panels, not one
dual-axis chart**: the two measures have different units, and a dual axis lets
the author place the crossover wherever the argument needs it.

### 3.5 Navigation

`START_HERE.md` — an "I want to…" table with the exact command per task, a
directory map, where each number lives, and a "things that will bite you"
section.

`weights/README.md` — what is loaded, what is present but idle, and why.

Root went from 45 files to 37. **Nothing deleted**: logs to `logs/`, superseded
files to `archive/`, analysis scripts to `tools/`. Moving those broke their
`__file__`-relative paths, which was found and fixed by re-running each one.

`app/` was deliberately **not** split despite mixing Python and TypeScript:
Next.js requires `layout.tsx`/`page.tsx` at exactly that path, and four other
files reference `app/backend.py`.

---

## 4. Verification run at the end

| Check | Result |
|---|---|
| `preflight.py --skip-models` | **READY** — all checks passed |
| `tsc --noEmit` | clean |
| `node --check` × 4 JS files | pass |
| `json.load` × 4 config files | pass |
| `ast.parse` × 9 Python files | pass |
| SQLite schema applied to a fresh DB | 13 tables, 0 dangling FKs, `foreign_key_check` clean |
| Batch-file path references | all resolve |
| Packaged `weights/` contents | 6 files, no `.engine`, no dead checkpoints |
| Packaged resources | `optimize_weights.py`, `preflight.py`, `schema_sqlite.sql`, UI assets all present |

**One caveat on the database check.** A regex scan for tables `backend.py` touches
reported two dozen "undefined" names — all false positives from English words in
comments (`FROM the`, `INTO an`) and `from fastapi` imports. The schema itself is
clean; the scan was not precise enough to be worth trusting on its own.

---

## 5. Still open

**Vandalism ships disabled.** Trained, measured, rejected: 70.3% accuracy against
a 78.4% majority-class baseline, firing on 7 of 8 normal clips. Its model and
metrics now ship so it can be switched on and inspected, behind a confirmation
dialog that shows its own numbers first.

**The day+night violence retrain was not deployed.** It ran 7 epochs of a planned
12 and peaked at **epoch 1** (87.8%) against the deployed model's 88.1%.
Different validation splits, so not a strict comparison — but no measured
improvement. Saved as `x3d_xs_violence_best_daynight.pt` in the training folder.
The proper test is `test_x3d_true_heldout.py` against the holdout cameras, and it
has not been run.

**Recall on the deployment cameras is unmeasured for every class**, because no
labelled incident has ever been recorded on them. This is a property of the
problem, not an oversight, and it is stated in the defense material rather than
papered over.

**`package.json` changed after the last build.** The current `.exe` predates the
vandalism model, so rebuild before putting it on the laptop if you want that
class available there.

**Next real lever: Multiple Instance Learning.** Every algorithmic intervention
tried so far has lost; what worked was more representative data. MIL trains from
video-level labels, removing the manual temporal annotation that is the actual
bottleneck — robbery would go from 43 to roughly 400 source videos, vandalism
from 18 to about 100, **with no new footage**. References are in §11 of the
defense document.

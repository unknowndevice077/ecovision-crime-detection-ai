# Installing EcoVision on another machine

Everything runs **locally**. No cloud service, no internet dependency at run
time, no external database. Detection, storage and the dashboard are all on the
machine you install to.

---

## 0. Minimum specifications

Derived from measurement on a GTX 1660 SUPER, not from spec sheets.

| | minimum | recommended |
|---|---|---|
| **GPU** | 2 GB VRAM, CUDA-capable | 4 GB+ |
| **System RAM** | 6 GB | 8 GB+ |
| **CPU** | 4 cores | 6+ cores |
| **Disk** | 8 GB free | 20 GB (recordings grow) |
| **OS** | Windows 10/11 64-bit | — |
| **Python** | none needed | — |

The installer ships a complete, self-contained Python runtime
(`python-env\`, built from the official embeddable distribution) inside the
one folder you choose — nothing to install, no separate download, no PATH
setup. You do not need Python on the machine at all. (This wasn't always
true: an earlier build used `python -m venv`, whose `pyvenv.cfg` hardcodes the
exact machine and path that created it, so it silently broke on every other
machine. Fixed 18 Aug.)

### What was actually measured

> **Measured 19 Aug 2026 with four models.** The deployed set has grown since:
> weapons now run at 960 px instead of 640, violence runs two models averaged,
> and vandalism adds an X3D model plus a graffiti detector. Expect more GPU
> memory and somewhat lower throughput than the table below; re-measure on the
> target machine with `tools\measure_min_specs.py` (or `preflight.py`, which
> reports throughput) before relying on these figures.

| | GPU | CPU only |
|---|---|---|
| throughput | 8.9 FPS (113 ms/frame) | 2.9 FPS (345 ms/frame) |
| GPU memory, whole process | **295 MB** | — |
| process RAM | 1.22 GB | 1.38 GB |
| model load time | 1.4 s | 0.7 s |

**GPU memory is the surprise.** All four models — YOLO pose, YOLO weapon/sign,
X3D violence, X3D robbery — plus the CUDA context came to a **295 MB** increase
in total board memory. X3D-XS is a 3-million-parameter network and the YOLO
models are small; nothing here is a large model. The 2 GB minimum above is
mostly margin.

Two caveats on those numbers, both making them conservative rather than
flattering:

- The 295 MB was measured on a card that already had another CUDA process
  resident, so part of the driver's shared working set was already paid for. On
  a machine with no other CUDA process, budget **600–800 MB**. Still under 1 GB.
- The 8.9 FPS was measured while a training job used the same GPU. On a free
  GPU it is higher.

### A discrete GPU is recommended, not strictly required

CPU-only ran at **2.9 FPS**, roughly 3× slower. That is not unusable, because
the detector rate-limits its own heavy work via
`detection.violence.check_interval` rather than classifying every frame — but
it leaves no headroom, and response latency rises. Treat CPU-only as a
demo/UI-testing mode.

### Reference: an RTX 3050 laptop, Ryzen 5 5600H, 8 GB DDR4

Comfortably above minimum on every axis. The 3050 is Ampere against this
Turing card, so throughput should be equal or better; 4 GB VRAM against a
sub-1 GB need; 6 cores against 4; 8 GB RAM against 1.2 GB resident. The only
practical constraint is **other applications competing for the 8 GB** — close
browsers before a long run. Preflight warns when free RAM drops below 3 GB.

---

## 1. Installing on a target machine — double-click, nothing else

**You do not run Python, install Python, install Node, or type any command on
the target machine.** There are two machines with two different jobs:

| | build machine (this PC) | target machine (the laptop) |
|---|---|---|
| needs Python + Node | yes | **no** |
| needs internet | yes, to fetch packages | **no** |
| what you do | run `build_release.bat` once | **run the Setup .exe once** |

### On this PC, once

```
build_release.bat
```

It verifies weights, schema and database mode via preflight, builds the
frontend, and packages everything — including a complete Python environment
with PyTorch, the four model weights, and the SQLite schema — into **one**
file in `dist_installer\`:

| file | use it for |
|---|---|
| `EcoVisionSentinel-Setup-<version>.exe` | **the only one shipped.** ~3 GB, single file, built with Inno Setup. One folder-choice screen; everything — app, weights, Python runtime, database, config, logs — lands inside the one folder you pick. No admin/UAC needed for this testing-phase build. |

There used to be a second, "portable" variant that re-extracted its entire
Python/PyTorch environment on every launch (slow, and it left nothing
installed between runs). It's gone — replaced entirely by the single Inno
Setup installer above on 18-19 Aug, after the NSIS-based toolchain that built
both variants turned out to have a hard ~2 GB build-time size ceiling. See
`START_HERE.md` trap #9 for the full story if you're curious.

### On the laptop

1. Copy `EcoVisionSentinel-Setup-<version>.exe` across.
2. Run it, click through (it's a normal Windows installer — pick a folder,
   Next, Install).
3. Launch it from the shortcut it creates. Every launch after the first is
   fast: nothing is re-extracted, because nothing was extracted at launch time
   in the first place — it all happened once, during install.

That is the whole procedure. On first launch the app:

- runs a **preflight check** and reports problems in the launch window in plain
  language rather than failing later as a Python traceback;
- **creates the SQLite database** from the bundled schema;
- **creates the DevTeam account** and shows the credentials once;
- starts the backend, the detector and the dashboard, and opens the UI.

No Python installation, no `pip`, no command prompt, no internet.

### Optional: "Optimize for this computer"

After the files are copied, setup offers to recompile the four AI models into
TensorRT engines. It is **optional and skippable**, and detection accuracy is
identical either way — it changes speed only.

**Why it is a step you run rather than something we ship prebuilt.** A TensorRT
engine is compiled against one GPU architecture, one TensorRT version and one
driver. Engines built here on a GTX 1660 SUPER (Turing) will not load on an
RTX 3050 (Ampere) — they raise on load and the detector falls back to the `.pt`.
So the only machine that can usefully build an engine is the one that will run
it.

Measured on the development GPU:

| model | before | after | gain |
|---|---|---|---|
| Violence detector | 27.6 ms | 8.8 ms | **3.24×** |
| Robbery detector | 17.7 ms | 10.4 ms | **1.71×** |
| Person / pose | 23.6 ms | 9.2 ms | **2.56×** |
| Weapon / sign | 18.6 ms | 13.1 ms | 1.43× |

The installer shows this same table for *your* GPU, measured before and after,
rather than quoting these numbers.

**Accuracy is verified, not assumed.** Each engine is diffed against the `.pt`
it came from on real input; one that disagrees is discarded instead of
installed. Observed through the live detector: max delta 0.000026 over 150
frames, 0 verdict changes at threshold 0.50. That equivalence is the whole
point — otherwise every threshold in `config.json` would quietly mean two
different things depending on whether an engine happened to load.

**TensorRT is not bundled.** `tensorrt_libs` alone is 3,245 MB against a
`python-env` that is already 5.13 GB, so including it by default would nearly
double the download for an optional speedup. Standard installs run the `.pt`
weights, which work on any CUDA GPU and on CPU. If you want the optimize step
available offline on the target machine, build with it included:

```
build_release.bat --with-tensorrt
```

That ships the *compiler*, not compiled models — engines are still built on the
machine that will run them.

ONNX Runtime was evaluated as a cheaper alternative (275 MB) and **rejected on
measurement**: its CUDA provider requires CUDA 13 / cuDNN 9, silently fell back
to CPU on this CUDA 12.1 environment, and ran at 61.6 ms against PyTorch's
18.7 ms — three times *slower* than doing nothing.

To run it later, or after a GPU or driver change:

```
optimize_weights.py            build engines and print the before/after table
optimize_weights.py --check-only   re-measure existing engines, build nothing
optimize_weights.py --revert       delete all engines, return to the .pt
```

Nothing breaks while you have not run it: the loader tries the engine, logs a
line if it fails, and continues on the `.pt`.

### If something looks wrong

The launch window shows the preflight output. A failed check is **not fatal** —
startup continues deliberately, because a check can be wrong on a machine we
have not seen, and that should never be the reason a working install refuses to
open. If detection then misbehaves, that output is the first place to look.

---

## What ships, and what it does

| model | file | role |
|---|---|---|
| YOLO11s-pose | `weights/yolo11s-pose.pt` | person detection, tracking, wrist keypoints |
| YOLO weapons v3 | `weights/weapons_v3.pt` | gun / knife / phone, at 960 px |
| X3D violence (×2) | `weights/x3d_xs_violence_scene_ucabal.pt` + `..._ucabal_mined.pt` | physical violence, two models averaged |
| X3D violence, per-track | `weights/x3d_xs_violence_best.pt` | which person an alert is attributed to |
| X3D robbery | `weights/x3d_xs_robbery_scene.pt` | robbery (experimental) |
| X3D vandalism v3 | `weights/x3d_xs_vandalism_scene_v3.pt` | vandalism (experimental) |
| YOLO graffiti marks v2 | `weights/vandalism_marks_v2.pt` | graffiti / tags for the vandalism rule |

Each may be accompanied by a `.engine` file built by the optimize step above.
Where one exists and loads, it is used; otherwise the `.pt` is.

**`.engine` files are never packaged, and this is enforced in the build.**
`package.json`'s `extraResources` lists the deployed files explicitly rather than
copying `weights/` wholesale. An earlier build did copy the whole folder, which
shipped this machine's engines to a machine that cannot load them — 70 MB that
fails on every launch — plus ~200 MB of superseded checkpoints nothing loads.
That list deliberately **mirrors `preflight.py`'s required set**, so a model
that ships is a model that gets checked; the two cannot drift apart the way the
old per-file weight checks in `build_release.bat` did.

If you add or replace a deployed model, update **both** lists.

**Robbery and vandalism ship enabled but labelled experimental** in the AI
Models tab. Each can be switched off there.

### Expected behaviour at the shipped settings

Measured on 2 hours of four held-out street cameras (nothing criminal in them)
and on held-out crime clips spliced into continuous street footage:

| detector | setting | events caught | false alarms / hour |
|---|---|---|---|
| Violence (2 models, person-crop confirmed) | 0.45, 2 in a row | 88.0% of 200 fights | 2.0 |
| Vandalism (person-crop confirmed) | 0.60, 3 in a row | 87.1% of 31 acts | 1.0 |
| Robbery | 0.85, 3 in a row | ~40% of test clips | 0.5 |

False alarms are not spread evenly — busy scenes produce most of them
(violence: all on the Agdao flyover PTZ); quiet fixed cameras produce almost
none. Weapons: 89.6%
recall on large guns, 39.4% on street-camera-size guns; a weapon alert also
needs the weapon assigned to a person's wrist.

Full numbers: `docs/detection_performance_report.md`.

---

## Data and privacy

- **SQLite**, a single file under `data/`. Created on first run from
  `app/schema_sqlite.sql`.
- **`DATABASE_URL` must stay unset.** Setting it switches `app/db.py` to
  Postgres. `config.json`'s `database.type` is documentation only — nothing
  reads it.
- Recordings and alert snapshots are written under the install directory. They
  are never uploaded.
- To back up or move an install, copy `data/`.

If Supabase is adopted later it would sit behind the same `db.py` interface.
The requirement to check first is **offline access**: this system must keep
detecting when the link drops, so any cloud backend needs a local cache and
write-behind sync rather than a direct dependency.

---

## Developing on this PC (not needed on the target machine)

`setup.bat` prepares both Python environments and builds the frontend — it
needs Node, Python and internet, and is a **build-machine** script.
`run_dev_system.bat` then runs everything with hot reload.

To check hardware manually at any time:

```
python-env\Scripts\python.exe preflight.py        full check with benchmark
python-env\Scripts\python.exe measure_min_specs.py  GPU vs CPU throughput
```

Neither is needed for a normal install — the packaged app runs the check itself.

---

## Troubleshooting

**"Preflight reported problems" in the launch window** — read the lines above
it. Missing weights means the build machine packaged an incomplete `weights\`
folder; rebuild there.

**`no CUDA device`** — the machine has no NVIDIA GPU or no driver. The app
still runs, at roughly a third of the speed (measured: 2.9 FPS against 8.9),
which is enough to demonstrate the UI but leaves no headroom for live use.

**Slow, or memory pressure on an 8 GB machine** — close other applications
first; the detector itself only holds ~1.2 GB. If it persists, raise
`detection.violence.check_interval` in `config.json` so fewer forward passes
run per second. This is the single lever for trading responsiveness against
load.

**Port already in use** — `app/port_utils.py` resolves ports automatically;
`config.json` `backend.port` and `frontend.port` are the starting points it
searches from.

**Moving or backing up an install** — copy the `data\` folder. It holds the
SQLite database and the recordings.

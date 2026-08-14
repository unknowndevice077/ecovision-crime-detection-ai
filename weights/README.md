# What is in this folder

Only files that something can actually **load**. Everything superseded is in
[`archive/`](archive/) — moved rather than deleted, because a checkpoint is
expensive to reproduce and the reason a run was rejected is part of the record.

## Loaded at runtime

| File | Loaded by | Role |
|---|---|---|
| `yolo11s-pose.pt` | `main.py` (hardcoded name) | People and 17 body keypoints |
| `weapon_signs.pt` | `main.py` (hardcoded name) | Gun / Knife / Sign |
| `x3d_xs_violence_scene_corpus_neg.pt` | `detection.violence.scene_model_path` | **The deployed violence model.** Mode is `scene`, so this is the one that runs. |
| `x3d_xs_robbery_scene.pt` | `detection.robbery.model_path` | Robbery, threshold 0.70 |

## Present but not currently running

| File | Why it is here |
|---|---|
| `x3d_xs_violence_best.pt` | `detection.violence.model_path` — the **per-track** model. Only loads if `detection.violence.mode` is switched to `track` or `both`; the deployed mode is `scene`. Deleting it would break that switch. |
| `x3d_xs_vandalism_scene.pt` | Ships so the vandalism class can be enabled and inspected from the dashboard. **Disabled by default** — 37.5% false-positive rate at its best threshold. See its `.meta.json`. |

## `.engine` files

TensorRT-compiled versions, built for **this machine's GPU** by
`optimize_weights.py`. They are:

- **preferred over the `.pt` when they load**, and skipped silently when they do not;
- **never packaged into the installer** — an engine only runs on the GPU
  architecture that built it, so shipping one produces a file that fails on
  every other machine;
- safe to delete at any time. The `.pt` takes over.

## `.meta.json` sidecars

Written by training. They record input geometry (`clip_frames`, `frame_size`)
and the measured test-split results.

**A sidecar overrides `config.json`** for geometry. That is deliberate: input
shape is a property of the weights, and config has no way to be right about it.
A mismatch that used to cost accuracy silently now prints a warning.

## Two lists must stay in step

`package.json`'s `extraResources` filter and `preflight.py`'s `required` dict.
Preflight lists what the app *needs*; extraResources lists that plus the
vandalism model. If you deploy a new checkpoint, update both — that pairing is
what stops a build shipping an unchecked model.

# What is in this folder

Only files that something can actually **load**. Everything superseded is in
[`archive/`](archive/) — moved rather than deleted, because a checkpoint is
expensive to reproduce and the reason a run was rejected is part of the record.

## Loaded at runtime

Which file runs is decided by `config.json` (`detection.*.model_path` and
friends), not by this list -- check there first if the two ever disagree.

| File | Config key | Role |
|---|---|---|
| `yolo11s-pose.pt` | `main.py` (hardcoded name) | People and 17 body keypoints |
| `weapons_v2.pt` | `detection.weapon.model_path` | Gun / knife / phone (phone is detected so it can be *dropped*). Runs at imgsz 640. |
| `x3d_xs_violence_scene_daynight.pt` | `detection.violence.scene_model_path` | **The deployed violence model.** Mode is `scene`, so this is the one that runs. |
| `x3d_xs_robbery_scene.pt` | `detection.robbery.model_path` | Robbery, threshold 0.70 |
| `vandalism_marks_v2.pt` | `detection.vandalism.marks_model_path` | Graffiti / marks detector used by the vandalism rule |
| `x3d_xs_vandalism_scene_v3.pt` | `detection.vandalism.model_path` | Vandalism clip model. Measured at ~21.75 false alarms/hr on real cameras -- see config's `_v3_result` before relying on it. |

## Present but not currently running

| File | Why it is here |
|---|---|
| `x3d_xs_violence_best.pt` | `detection.violence.model_path` -- the **per-track** model. Only loads if `detection.violence.mode` is `track` or `both`. |
| `x3d_xs_violence_scene_corpus_neg.pt` | Previous scene violence model; rollback target (see config's `_scene_model_path_rollback`). |
| `weapon_signs.pt`, `vandalism_marks.pt` | Previous weapon / marks models; rollback targets. The weapon rollback also needs `WEAPON_IMGSZ` and `CONF_BY_CLASS` restored in `main.py`. |
| `x3d_xs_vandalism_scene.pt` | Vandalism v1 (11 scenes), rollback for v3. |

## Improving these from real use

Every operator Confirm / Dismiss on an AI alert is stored as a labelled
example (`detection_feedback` table). `tools/export_feedback_dataset.py`
turns them into a dataset with clean frames, event clips, weapon crops and
contact sheets; the DevTeam **AI Models** tab shows per-camera precision.
Look at the contact sheets before training on any of it.

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

# Scaling plan — how many cameras, and what actually limits it

Written 2026-08-26, measured on the real pilot hardware (GTX 1660 SUPER 6GB,
AMD Ryzen 5 5600G 6C/12T, 16GB system RAM) rather than estimated from spec
sheets. Answers the direct question first, then shows the measurement and the
architecture that changes the answer.

---

## The direct answer

**On the current architecture (one independent `main.py` process per
camera), this GPU realistically holds 2–4 cameras before per-camera latency
degrades below real-time.** Not the ~12–13 that a naive VRAM-only
calculation would suggest — VRAM is not the binding constraint here, **GPU
compute is.**

**20 cameras on one GPU, as currently architected, is not possible** — not a
tuning problem, an arithmetic one, shown below. Getting to 20 cameras needs
an architecture change (§3) and, realistically, more than one GPU or a mix of
central + edge compute (§4).

---

## 1. Correcting the "5 .py per camera" framing

There are **not** 5 separate Python files running per camera. The real
architecture is:

- **`backend.py`** — one process total, shared across every camera and every
  barangay/station (it's the API + database layer).
- **`main.py`** — currently **one process per camera** (this is the actual
  scaling bottleneck — see §3). Each instance independently loads and holds
  **7 model instances in VRAM at once**:

  | Model | Type | On-disk size |
  |---|---|---|
  | `yolo11s-pose.pt` | YOLO, person/pose | 19 MB |
  | `weapons_v2.pt` | YOLO, weapon detector | 72 MB |
  | `vandalism_marks_v2.pt` | YOLO, graffiti marks | 18 MB |
  | `x3d_xs_violence_best.pt` | X3D-XS, per-track violence | 11 MB |
  | `x3d_xs_violence_scene_daynight.pt` | X3D-XS, scene violence (deployed) | 11 MB |
  | `x3d_xs_robbery_scene.pt` | X3D-XS, robbery | 11 MB |
  | `x3d_xs_vandalism_scene_v3.pt` | X3D-XS, vandalism | 11 MB |

  **142 MB of weights total.** That's not what limits scaling — see below.

## 2. What was actually measured

One `main.py` instance was run against a real test clip end-to-end (all 7
models loaded, an alert actually fired and a clip actually saved), with
`nvidia-smi` sampled before, during, and after:

| | Idle baseline (desktop, no app) | One camera process, steady state |
|---|---|---|
| GPU VRAM used | 911 MB | 1,278 MB (**+367 MB**) |
| System RAM (that process alone) | — | **~2.2 GB** |
| GPU compute utilization | ~0% | **58%**, during an active detection burst |

Three separate ceilings fall out of this, and they don't agree with each
other:

- **By VRAM alone:** ~5,000 MB free ÷ ~400 MB per camera ≈ **12 cameras.**
  This is the number a spec-sheet calculation gives, and it's misleading.
- **By system RAM:** on this 16GB dev machine, ~2.2 GB per camera process
  leaves room for roughly **6–7 cameras** before the OS, Electron, and
  `backend.py` are starved. On a proper server with 64GB RAM, that scales to
  ~25–28 — RAM is a real constraint but a *solvable* one (buy more RAM).
- **By GPU compute:** this is the one that doesn't scale by buying more of
  the same thing. **58% GPU utilization from one camera's burst inference**
  means two cameras inferencing at the same moment are already contending
  for the same CUDA cores. A 1660 SUPER has 1,408 CUDA cores total — running
  N independent processes doesn't parallelize cleanly across them the way N
  independent VRAM allocations do, because each process's inference calls
  are synchronous and unaware of the others. The result isn't "it stops
  working" — it's **queueing**: each camera's inference takes proportionally
  longer as more cameras compete, and "real-time" detection quietly stops
  being real-time.

**Compute is the ceiling, not memory.** This is exactly the pattern flagged
in the accuracy plan (`plans/read-my-whole-codebase-logical-plum.md`): tiled
scene mode already runs "17 inferences every 0.5s regardless" on one camera;
multiply that by even 4–5 cameras on one GPU and the queue never drains.

---

## 3. Why the architecture, not just the hardware, is the limit

Running N independent `main.py` processes means:

- **N× redundant model loads** — 7 models loaded fresh into VRAM per
  process, no sharing, even though every camera is running the identical
  weights.
- **N× redundant CUDA contexts** — a meaningful chunk of that measured
  367 MB is fixed per-process CUDA/driver overhead, not model weights (142MB
  of weights vs. 367MB of VRAM delta already shows this). Each additional
  process pays that fixed cost again.
- **No batching** — 4 cameras each doing a separate X3D forward pass on a
  single frame is far less efficient than 1 batched forward pass on 4
  frames at once. GPUs are throughput machines; one-at-a-time synchronous
  calls from separate processes waste exactly the parallelism a GPU is good
  at.

**Recommended architecture for real scaling:** split "read the camera" from
"run the models."

```
per camera (cheap):  frame reader + person/motion tracker  →  queue
                                                                  │
shared (expensive):  ONE inference service, one copy of each   ◄─┘
                      of the 7 models, batching frames from
                      every camera that currently has motion
```

This changes the math entirely: weights load **once** regardless of camera
count, VRAM scales with batch size rather than process count, and idle
cameras (most cameras, most of the time — an empty street generates no
motion) cost close to nothing instead of paying for a full inference pass
every interval regardless of content. This is the same principle the
accuracy plan already identified for person-crop mode: "inference only where
there are people... roughly the difference between ~1 camera per GPU and
~15."

**This is a real refactor, not a config change** — `main.py` today assumes
one camera per process throughout (confirmed while reading it: `CAMERA_SOURCE`,
`/set_camera_index`, the whole file is written single-camera-first). Budget
it as its own project phase, not something to slot in before the defense.

---

## 4. A realistic path to 20 cameras

Not all 20 cameras need equal compute. Most street cameras are quiet most of
the time — activity-gating (skip inference entirely on frames with no
detected motion/person) is the single highest-leverage change available and
works with either architecture.

| Stage | Setup | Realistic camera count |
|---|---|---|
| **Today** | 1660 SUPER, one process per camera | 2–4, with degrading latency past that |
| **Near-term** | Same GPU, refactored to the shared batched-inference service (§3) + activity gating | ~8–10 |
| **20-camera target** | Requires either (a) 2–3 GPUs of this class behind the batched service, or (b) pushing pose/weapon detection to per-camera edge devices (Jetson-class) and keeping only the X3D classification pass centralized | Achievable, not on one consumer GPU |

The `cameras` table, RTSP URLs, and barangay/station ownership already exist
in `app/backend.py` — none of this needs new database design. The gap is
entirely in `maincode/main.py`'s one-process-per-camera assumption.

---

## 5. What this plan does not claim

These are estimates from **one measured camera under one workload**
(scene-mode violence detection actively firing), not a swept benchmark across
camera count. The existing accuracy plan's Phase 0/T3 ("cost per camera per
mode, on empty vs. busy footage") is the right place to turn this into a
properly swept number — this document gives the direction and the order of
magnitude, not a guarantee. Say that plainly if asked for the exact number at
defense: "measured on one camera, here's the reasoning for why it doesn't
extrapolate linearly" is a stronger answer than a confident wrong number.

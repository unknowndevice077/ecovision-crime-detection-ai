"""Architecture + concurrency diagrams for the manuscript's AI findings section.

Drawn from the actual code paths (maincode/main.py, maincode/x3d_violence_detector.py),
not a generic textbook pipeline -- box labels and thread names match the real
ThreadPoolExecutor names and function names in the codebase.
"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.path import Path as MPath

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
FIG.mkdir(exist_ok=True)

INK = "#1c2530"
MUTED = "#5b6572"
BG = "#ffffff"
BOX = "#eef2f7"
BOX_EDGE = "#8a95a3"
CAMERA = "#2f5fa8"
POSE = "#6a4fa0"
DETECT = "#2f6f4f"
ALERT = "#a13d3d"
THREAD = "#c98a2e"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "text.color": INK,
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
})


def box(ax, xy, w, h, text, color=BOX, edge=BOX_EDGE, fontsize=9.5, fontweight="normal",
        textcolor=INK, ls="-"):
    x, y = xy
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
                        linewidth=1.4, edgecolor=edge, facecolor=color, linestyle=ls, zorder=2)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize,
             fontweight=fontweight, color=textcolor, zorder=3, linespacing=1.35)
    return (x, y, w, h)


def arrow(ax, p1, p2, color=MUTED, lw=1.6, style="-|>", connectionstyle="arc3,rad=0"):
    a = FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=13, linewidth=lw,
                         color=color, zorder=1, connectionstyle=connectionstyle)
    ax.add_patch(a)


# ===========================================================================
# Figure 1: per-frame detection architecture (single camera, one frame)
# ===========================================================================
fig, ax = plt.subplots(figsize=(10.5, 6.4))
ax.set_xlim(0, 10.5); ax.set_ylim(0, 6.4); ax.axis("off")

cam = box(ax, (0.3, 4.9), 1.7, 1.0, "Camera\n(RTSP / USB)", color="#dce6f5", edge=CAMERA, fontweight="bold")
reader = box(ax, (0.3, 3.3), 1.7, 1.0, "_ThreadedFrameReader\nalways latest frame", color="#dce6f5", edge=CAMERA, fontsize=8.7)
arrow(ax, (1.15, 4.9), (1.15, 4.3))

pose = box(ax, (2.6, 4.9), 2.0, 1.0, "YOLO11s-pose\nperson + keypoints", color="#e6ddf2", edge=POSE, fontweight="bold")
weapon = box(ax, (2.6, 3.0), 2.0, 1.0, "weapon_signs (YOLO)\non _weapon_exec\n(1 worker thread)", color="#fbeedb", edge=THREAD, fontsize=8.5)
arrow(ax, (2.0, 3.8), (2.6, 4.6))
arrow(ax, (2.0, 3.8), (2.6, 3.5))

viol = box(ax, (5.1, 5.4), 2.15, 0.85, "SceneViolenceDetector\n(violence, X3D-XS)", color="#dcecdf", edge=DETECT, fontsize=8.7)
robb = box(ax, (5.1, 4.3), 2.15, 0.85, "SceneViolenceDetector\n(robbery, X3D-XS)", color="#dcecdf", edge=DETECT, fontsize=8.7)
vand = box(ax, (5.1, 3.2), 2.15, 0.85, "SceneViolenceDetector\n(vandalism -- disabled)", color="#eef2ee", edge="#9db3a2", fontsize=8.3, textcolor=MUTED)
arrow(ax, (4.6, 5.4), (5.1, 5.8))
arrow(ax, (4.6, 5.4), (5.1, 4.7))
arrow(ax, (4.6, 5.4), (5.1, 3.6), color="#9db3a2")

smooth = box(ax, (7.7, 4.7), 2.2, 1.1, "_smooth_and_confirm\nEMA + consecutive-frame\nconfirmation (per detector)", color=BOX, fontsize=8.3)
arrow(ax, (7.25, 5.8), (7.7, 5.35))
arrow(ax, (7.25, 4.7), (7.7, 5.05))

alert = box(ax, (7.7, 3.0), 2.2, 1.0, "_alert_exec\npost alert -> backend\n(1 worker thread)", color="#f6dede", edge=ALERT, fontweight="bold", fontsize=9)
arrow(ax, (8.8, 4.7), (8.8, 4.0))

clip = box(ax, (5.1, 1.4), 2.15, 0.9, "_clip_exec\nfinalize + register clip\n(1 worker thread)", color="#fbeedb", edge=THREAD, fontsize=8.3)
encode = box(ax, (7.7, 1.4), 2.2, 0.9, "_encode_exec\nJPEG encode -> MJPEG\nstream server", color="#fbeedb", edge=THREAD, fontsize=8.3)
arrow(ax, (8.8, 3.0), (8.8, 2.3))

ax.text(0.3, 6.15, "Figure: Per-frame detection path for one camera", fontsize=11.5,
         fontweight="bold", color=INK)
ax.text(0.3, 0.55,
         "Solid boxes run inline on the camera's main processing loop; amber boxes offload to a dedicated\n"
         "single-worker thread pool so a slow step (posting an alert, encoding a frame) cannot stall detection\n"
         "on the next frame. Vandalism is wired identically but its threshold is not currently reached in\n"
         "config.json (detection.vandalism.enabled = false) pending more labelled data (see Section 4.13).",
         fontsize=8.3, color=MUTED, va="top")

plt.tight_layout()
plt.savefig(FIG / "architecture_pipeline.png", dpi=200, bbox_inches="tight")
plt.close(fig)

# ===========================================================================
# Figure 2: concurrency model -- why detectors run "at the same time"
# ===========================================================================
fig, ax = plt.subplots(figsize=(13, 5.6))
ax.set_xlim(0, 13); ax.set_ylim(0, 5.6); ax.axis("off")

ax.text(0.2, 5.35, "Figure: Concurrency model within one camera process", fontsize=12.5,
         fontweight="bold", color=INK)
ax.text(9.0, 5.35, "time →", fontsize=9.5, color=MUTED)

lanes = [
    ("Camera reader\nthread", "continuously grabs frames;\nnever blocks on processing", CAMERA, 4.15),
    ("Main detection\nloop", "pose -> violence -> robbery ->\n(vandalism, disabled), same\nthread, per frame", DETECT, 3.15),
    ("_weapon_exec\n(1 thread)", "weapon/sign detection,\nparallel with next frame", THREAD, 2.15),
    ("_alert_exec\n(1 thread)", "posts confirmed alerts\nto the backend API", ALERT, 1.15),
    ("_clip_exec /\n_encode_exec", "clip finalization and\nlive MJPEG streaming", THREAD, 0.15),
]
for label, desc, color, y in lanes:
    box(ax, (0.2, y), 2.1, 0.85, label, color="#ffffff", edge=color, fontweight="bold", fontsize=8.8)
    ax.text(2.55, y + 0.42, desc, fontsize=8.3, color=MUTED, va="center")

# timeline bars showing overlap
t0 = 6.4
span_w = 6.3
for label, color, y, span in [
    ("frame N read", CAMERA, 4.15, (0.0, 0.15)),
    ("frame N-1: pose + violence + robbery", DETECT, 3.15, (0.15, 0.62)),
    ("frame N-2: weapon check (parallel)", THREAD, 2.15, (0.05, 0.52)),
    ("earlier alert POST (parallel)", ALERT, 1.15, (0.0, 0.42)),
    ("earlier clip encode (parallel)", THREAD, 0.15, (0.0, 0.58)),
]:
    x0 = t0 + span[0] * span_w
    w = (span[1] - span[0]) * span_w
    box(ax, (x0, y), w, 0.85, "", color=color, edge=color, fontsize=1)
    ax.text(x0 + w / 2, y + 0.42, label, fontsize=8.0, color="white", fontweight="bold",
             ha="center", va="center", zorder=4)

ax.axvline(t0, color=MUTED, lw=0.8, ymin=0.04, ymax=0.93, linestyle=":")

plt.tight_layout()
plt.savefig(FIG / "concurrency_model.png", dpi=200, bbox_inches="tight")
plt.close(fig)

print("Wrote:", sorted(p.name for p in FIG.glob("*.png")))

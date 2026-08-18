"""Generates the figures embedded in the manuscript's AI findings section.

Run from repo root:  .venv\\Scripts\\python.exe docs\\metrics\\generate_figures.py

Inputs: docs/metrics/confusion_matrix_results.json (produced by the
evaluation described in that file's own sibling raw_per_clip_results/,
methodology documented in D:\\EcoVisionImagesTraining\\_scratch\\confusion_matrix_report.py).
Outputs: docs/metrics/figures/*.png, embedded into the manuscript by
insert_ai_findings.py.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
import numpy as np

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
FIG.mkdir(exist_ok=True)

results = json.loads((HERE / "confusion_matrix_results.json").read_text())

# ---- palette (colorblind-safe-ish, consistent across figures) -----------
INK = "#1c2530"
MUTED = "#5b6572"
GRID = "#d8dde3"
POS = "#2f6f4f"    # correct predictions (TP/TN) -- green family
NEG = "#a13d3d"    # errors (FP/FN) -- red family
ACCENT = "#2f5fa8"
BG = "#ffffff"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "text.color": INK,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "figure.facecolor": BG,
    "axes.facecolor": BG,
    "savefig.facecolor": BG,
})


def confusion_grid(spec, outpath):
    tp, fp, tn, fn = spec["tp"], spec["fp"], spec["tn"], spec["fn"]
    grid = np.array([[tp, fn], [fp, tn]])  # rows: actual +/-, cols: predicted +/-
    labels = np.array([["TP", "FN"], ["FP", "TN"]])
    colors = np.array([[POS, NEG], [NEG, POS]])

    fig, ax = plt.subplots(figsize=(4.6, 4.2))
    for r in range(2):
        for c in range(2):
            val = grid[r, c]
            frac = val / grid.sum()
            color = colors[r, c]
            rect = Rectangle((c, 1 - r), 1, 1, facecolor=color, alpha=0.15 + 0.55 * frac,
                              edgecolor=color, linewidth=1.6)
            ax.add_patch(rect)
            ax.text(c + 0.5, 1 - r + 0.58, labels[r, c], ha="center", va="center",
                     fontsize=13, fontweight="bold", color=color)
            ax.text(c + 0.5, 1 - r + 0.34, f"{val}", ha="center", va="center",
                     fontsize=20, fontweight="bold", color=INK)
            ax.text(c + 0.5, 1 - r + 0.14, f"{frac*100:.1f}%", ha="center", va="center",
                     fontsize=9.5, color=MUTED)

    ax.set_xlim(0, 2); ax.set_ylim(0, 2)
    ax.set_xticks([0.5, 1.5]); ax.set_xticklabels(["Predicted:\nIncident", "Predicted:\nNormal"], fontsize=10)
    ax.set_yticks([0.5, 1.5]); ax.set_yticklabels(["Actual:\nNormal", "Actual:\nIncident"], fontsize=10)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(f"{spec['name']}\nthreshold={spec['threshold']}   n={spec['n']} clips",
                 fontsize=11.5, fontweight="bold", pad=14)

    stats = (f"Accuracy {spec['accuracy']*100:.1f}%   "
             f"Precision {spec['precision']*100:.1f}%   "
             f"Recall {spec['recall']*100:.1f}%   "
             f"FPR {spec['fpr']*100:.1f}%")
    ax.text(1, -0.22, stats, ha="center", va="top", fontsize=9.3, color=MUTED,
             transform=ax.transData)

    plt.tight_layout()
    plt.savefig(outpath, dpi=200, bbox_inches="tight")
    plt.close(fig)


for spec in results:
    safe = "".join(c if c.isalnum() else "_" for c in spec["name"]).strip("_").lower()
    confusion_grid(spec, FIG / f"confusion_{safe}.png")

# ---- combined summary bar chart (recall / precision / FPR side by side) --
fig, ax = plt.subplots(figsize=(7.2, 4.2))
names = [r["name"].split(" (")[0] for r in results]
metrics = ["recall", "precision", "fpr"]
metric_labels = ["Recall", "Precision", "False-Positive Rate"]
metric_colors = [ACCENT, POS, NEG]
x = np.arange(len(names))
width = 0.25
for i, (m, lbl, col) in enumerate(zip(metrics, metric_labels, metric_colors)):
    vals = [r[m] * 100 for r in results]
    bars = ax.bar(x + (i - 1) * width, vals, width, label=lbl, color=col,
                   edgecolor="none", zorder=3)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.1f}", ha="center",
                 fontsize=8.2, color=INK)

ax.set_xticks(x); ax.set_xticklabels(names, fontsize=10.5)
ax.set_ylabel("Percent", fontsize=10)
ax.set_ylim(0, 105)
ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
ax.set_axisbelow(True)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
ax.legend(frameon=False, fontsize=9.5, loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=3)
ax.set_title("Detector performance at configured operating points\n(held-out TEST split, one prediction per clip)",
             fontsize=11.5, fontweight="bold", pad=28)
plt.tight_layout()
plt.savefig(FIG / "summary_bars.png", dpi=200, bbox_inches="tight")
plt.close(fig)

print("Wrote:", sorted(p.name for p in FIG.glob("*.png")))

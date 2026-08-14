"""Render the defense figures as files you can drop into the paper.

The HTML version (docs/model_behavior_defense.html) is for reading and for the
panel; this produces the same charts as 300 dpi PNG and vector PDF for a thesis
document, where an interactive page is no use.

Every number here is transcribed from docs/detection_performance_report.md and
nothing is recomputed, so the two cannot disagree. If a measurement changes,
change it there and re-run this.

Deliberate choices worth defending if asked:

  - The confirmation sweep is TWO stacked panels, not one chart with two
    y-axes. Events-detected and alarms-per-hour have different units, and a
    dual-axis chart lets the author place the crossover wherever the argument
    needs it. Two panels sharing an x-axis show the same relationship without
    that freedom.
  - Colours are a validated set: adjacent-pair CVD separation ΔE 9.4 and
    normal-vision ΔE 20.9 (OKLab x100), so the series stay distinguishable to
    a colourblind reader and in greyscale print.
  - Every series is also directly labelled, so identity never rests on hue.

Output: docs/figures/*.png and *.pdf
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

REPO = Path(__file__).resolve().parent.parent   # this file lives in tools/
OUT = REPO / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

# Validated categorical slots (light surface) + status colours.
BLUE, ORANGE, GREEN = "#2a78d6", "#eb6834", "#1baf7a"
OK, WARN, BAD, MUTED = "#157F39", "#A66300", "#C22B26", "#8a8f99"
INK, INK2, GRID = "#141820", "#4C5567", "#E6E9EF"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
    "font.size": 9,
    "axes.edgecolor": "#C6CCD8",
    "axes.labelcolor": INK2,
    "axes.titlesize": 10.5,
    "axes.titleweight": "bold",
    "axes.titlecolor": INK,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": INK2,
    "ytick.color": INK2,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.bbox": "tight",
    "savefig.dpi": 300,
})


def caption(fig, text):
    """Footnote below the axes, placed by line count.

    savefig(bbox_inches="tight") recomputes the bounding box, so reserving
    space with subplots_adjust does not work here -- the caption has to clear
    the x-axis label on its own. A fixed offset collided the moment a caption
    grew to three lines, so it scales instead.
    """
    lines = text.count("\n") + 1
    fig.text(0.0, -0.16 - 0.085 * lines, text, ha="left", fontsize=7.5, color=INK2)


def finish(fig, name):
    for ext in ("png", "pdf"):
        p = OUT / f"{name}.{ext}"
        fig.savefig(p)
    plt.close(fig)
    print(f"  {name}.png / .pdf")


def clean(ax, spines=("top", "right")):
    for s in spines:
        ax.spines[s].set_visible(False)
    ax.set_axisbelow(True)


# --- 1. confirmation tradeoff: two panels, one shared x ----------------------
def fig_consecutive():
    cons = ["1", "2", "3", "4"]
    events = [38, 38, 38, 36]
    alarms = [49, 32, 17, 9]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.4, 4.6), sharex=True,
                                 gridspec_kw={"hspace": 0.22})

    c1 = [BLUE, BLUE, OK, BAD]
    a1.bar(cons, events, color=c1, width=0.55, zorder=3)
    for i, v in enumerate(events):
        a1.text(i, v + 0.8, str(v), ha="center", fontsize=8.5, fontweight="bold", color=INK)
    a1.set_ylim(0, 44)
    a1.set_ylabel("events detected\n(of 40)")
    a1.set_title("Raising the confirmation requirement is free until it isn't", loc="left")
    a1.xaxis.grid(False)
    clean(a1)

    c2 = [BAD, ORANGE, OK, GREEN]
    a2.bar(cons, alarms, color=c2, width=0.55, zorder=3)
    for i, v in enumerate(alarms):
        a2.text(i, v + 1.2, str(v), ha="center", fontsize=8.5, fontweight="bold", color=INK)
    a2.set_ylim(0, 56)
    a2.set_ylabel("false alarms\nper hour")
    a2.set_xlabel("consecutive confirmations required before an alert")
    a2.xaxis.grid(False)
    clean(a2)

    caption(fig,
             "40 held-out violent clips spliced into real Davao night footage. "
             "Deployed setting: 3.")
    finish(fig, "fig1_consecutive_tradeoff")


# --- 2. per-camera false alarms ---------------------------------------------
def fig_cameras():
    names = ["agdao_market", "outside Lyn's", "agdao_flyover\n(PTZ)"]
    vals = [0, 6, 45]
    fig, ax = plt.subplots(figsize=(6.4, 2.5))
    bars = ax.barh(names, vals, color=[OK, OK, BAD], height=0.55, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(v + 0.9, b.get_y() + b.get_height() / 2, str(v),
                va="center", fontsize=9, fontweight="bold", color=INK)
    ax.set_xlim(0, 52)
    ax.set_xlabel("false alarms per hour")
    ax.set_title("A single system-wide rate would hide a >45x spread", loc="left")
    ax.invert_yaxis()
    ax.yaxis.grid(False)
    clean(ax)
    caption(fig,
             "Threshold 0.50, confirmation 3, 20 minutes per camera. The flyover is the only "
             "one whose venue\ndoes not also appear in training, which makes it both the worst "
             "and the most trustworthy figure.")
    finish(fig, "fig2_false_alarms_by_camera")


# --- 3. scale degradation ----------------------------------------------------
def fig_scale():
    x = [37, 30, 22, 19, 15, 9]
    y = [100, 85, 62.5, 55, 17.5, 0]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.axvspan(0, 15, color=BAD, alpha=0.08, zorder=0)
    ax.text(7.5, 92, "effectively blind", ha="center", fontsize=8, color=BAD)
    ax.plot(x, y, color=BLUE, lw=2, marker="o", ms=6, mec="white", mew=1.5, zorder=3)
    for xi, yi in zip(x, y):
        ax.annotate(f"{yi:g}%", (xi, yi), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8, color=INK)
    ax.set_xlim(4, 41)
    ax.set_ylim(-6, 112)
    ax.invert_xaxis()
    ax.set_xlabel("person height as % of frame height")
    ax.set_ylabel("clips detected")
    ax.yaxis.set_major_formatter(PercentFormatter())
    ax.set_title("Detection collapses with subject size", loc="left")
    clean(ax)
    caption(fig,
             "Same 40 clips, progressively rescaled. Below ~15% the model is not less "
             "confident - it is blind,\nand a blind camera and a safe street produce "
             "identical output.")
    finish(fig, "fig3_scale_degradation")


# --- 4. robbery threshold sweep ---------------------------------------------
def fig_robbery():
    thr = [0.3, 0.5, 0.6, 0.7, 0.8, 0.9]
    recall = [91.8, 79.6, 71.4, 65.3, 57.1, 44.9]
    prec = [55.6, 62.9, 72.9, 86.5, 96.6, 100.0]
    fpr = [40.0, 25.6, 14.4, 5.6, 1.1, 0.0]
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.axvline(0.7, color=INK2, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.text(0.7, 104, "deployed", ha="center", fontsize=8, color=INK2)
    for ys, c, lab in ((recall, BLUE, "recall"), (prec, ORANGE, "precision"),
                       (fpr, GREEN, "false-positive rate")):
        ax.plot(thr, ys, color=c, lw=2, marker="o", ms=5, mec="white", mew=1.4, zorder=3)
        ax.annotate(lab, (thr[-1], ys[-1]), textcoords="offset points", xytext=(7, 0),
                    va="center", fontsize=8.5, color=c, fontweight="bold")
    ax.set_xlim(0.26, 1.02)
    ax.set_ylim(-5, 112)
    ax.set_xlabel("decision threshold")
    ax.yaxis.set_major_formatter(PercentFormatter())
    ax.set_title("Robbery: choosing the operating point", loc="left")
    clean(ax)
    caption(fig,
             "139 clips from 8 source videos never trained on. 0.70 is where precision becomes "
             "usable without\nrecall collapsing; at 0.50 a quarter of normal clips misfire.")
    finish(fig, "fig4_robbery_threshold_sweep")


# --- 5. vandalism: both routes fail -----------------------------------------
def fig_vandalism():
    labels = ["Rule: fired on\nreal vandalism", "Rule: Sign\ndetections",
              "Model accuracy", "Always-guess\nbaseline", "Model false-\npositive rate"]
    vals = [0, 0, 70.3, 78.4, 87.5]
    cols = [BAD, BAD, ORANGE, MUTED, BAD]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    bars = ax.bar(labels, vals, color=cols, width=0.6, zorder=3)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 2.2, f"{v:g}%",
                ha="center", fontsize=8.5, fontweight="bold", color=INK)
    # The baseline is the line the model has to beat and does not.
    ax.axhline(78.4, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=2)
    ax.set_ylim(0, 102)
    ax.yaxis.set_major_formatter(PercentFormatter())
    ax.set_title("Vandalism: both routes measured, both failed", loc="left")
    ax.xaxis.grid(False)
    ax.tick_params(axis="x", labelsize=7.5)
    clean(ax)
    caption(fig,
             "The rule fired 0 times on 40 real clips - zero Sign detections across 4,800 frames, "
             "so its gate\nnever opens. The trained model's accuracy sits BELOW the majority-class "
             "baseline, and it fires\non 7 of 8 normal clips. The class ships disabled.")
    finish(fig, "fig5_vandalism_both_routes")


# --- 6. subject size: assumed vs trained vs actual ---------------------------
def fig_size():
    rows = [("Assumed by the plan", 6, 12, BAD, 0.35),
            ("Training clips", 24, 60, BLUE, 1.0),
            ("Deployment cameras", 21.7, 28.6, OK, 1.0)]
    fig, ax = plt.subplots(figsize=(6.4, 2.5))
    ax.axvspan(0, 15, color=BAD, alpha=0.08, zorder=0)
    ax.text(7.5, 2.42, "blind", ha="center", fontsize=8, color=BAD)
    for i, (name, lo, hi, c, alpha) in enumerate(rows):
        ax.barh(i, hi - lo, left=lo, color=c, alpha=alpha, height=0.42, zorder=3)
        ax.text(hi + 1.2, i, f"{lo:g}–{hi:g}%", va="center", fontsize=8.5,
                fontweight="bold", color=INK)
    ax.plot([37], [1], marker="|", ms=16, mew=2, color=INK, zorder=4)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlim(0, 68)
    ax.set_ylim(-0.6, 2.6)
    ax.invert_yaxis()
    ax.set_xlabel("person height as % of frame height")
    ax.xaxis.set_major_formatter(PercentFormatter())
    ax.set_title("The assumption that was wrong", loc="left")
    ax.yaxis.grid(False)
    clean(ax)
    caption(fig,
             "Measured on 248 detections across 4 cameras. The deployment cameras sit at 22-29%, "
             "close to training,\nnot at the assumed 6-12%. Caveat: these are heights of DETECTED "
             "people, so the true distribution\nhas more small people in it and these are biased upward.")
    finish(fig, "fig6_subject_size")


# --- 7. optional GPU compilation --------------------------------------------
def fig_engines():
    names = ["Violence\ndetector", "Person /\npose", "Robbery\ndetector", "Weapon /\nsign"]
    before = [27.6, 23.6, 17.7, 18.6]
    after = [8.8, 9.2, 10.4, 13.1]
    ypos = range(len(names))
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ax.barh([y - 0.19 for y in ypos], before, height=0.34, color=BLUE,
            label="standard weights", zorder=3)
    ax.barh([y + 0.19 for y in ypos], after, height=0.34, color=GREEN,
            label="compiled for this GPU", zorder=3)
    for y, (b, a) in enumerate(zip(before, after)):
        ax.text(b + 0.5, y - 0.19, f"{b:.1f}", va="center", fontsize=8, color=INK)
        ax.text(a + 0.5, y + 0.19, f"{a:.1f}", va="center", fontsize=8, color=INK)
        ax.text(31.5, y, f"{b / a:.2f}x", va="center", fontsize=9,
                fontweight="bold", color=OK)
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlim(0, 35)
    ax.invert_yaxis()
    ax.set_xlabel("milliseconds per inference (lower is better)")
    ax.set_title("Optional GPU compilation, measured on a GTX 1660 SUPER", loc="left")
    ax.yaxis.grid(False)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    clean(ax)
    caption(fig,
             "Accuracy is unchanged and verified, not assumed: max deviation 0.000026 over 150 "
             "frames with zero\nverdict changes. A compiled model that disagrees with the original "
             "is discarded, not installed.")
    finish(fig, "fig7_gpu_compilation")


if __name__ == "__main__":
    print(f"Writing figures to {OUT}")
    for fn in (fig_consecutive, fig_cameras, fig_scale, fig_robbery,
               fig_vandalism, fig_size, fig_engines):
        try:
            fn()
        except Exception as e:
            print(f"  FAILED {fn.__name__}: {type(e).__name__}: {e}")
            sys.exit(1)
    print("\nDone. PNG for slides, PDF for the thesis document (vector, scales cleanly).")

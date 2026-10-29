"""October 2026 figures and statistics for the thesis.

Companion to make_defense_figures.py (which draws the August figures). Every
input here is transcribed from docs/detection_performance_report.md -- counts
are rate x n, rounded back to a whole event or alarm -- and nothing is
recomputed from raw footage, so this file and the report cannot disagree. If a
measurement changes, change it there and here, and re-run.

What the statistics are, and are not:

  - Recall figures get a 95% Wilson score interval (a proportion out of n
    held-out events).
  - False-alarm rates are COUNTS over a fixed number of camera-hours, so they
    get an exact (Garwood) Poisson interval. 5 hours of cameras gives a handful
    of alarms per setting, and the intervals are wide -- that is the honest
    size of the evidence, and the figures show it rather than hiding it.
  - Differences between settings: the two recall figures are scored on the SAME
    200 fights, so an unpaired interval is conservative (a paired one would be
    tighter); the per-event outcomes needed for a paired test were not kept
    for the deployed baseline. False-alarm counts are compared with an exact
    conditional binomial test, treating each alarm as equally likely to belong
    to either setting under the null -- approximate, because the same footage
    produces both.

Outputs: docs/figures/fig8_*.png/.pdf ... fig15_*, and docs/statistics_summary.md
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_defense_figures as base            # noqa: E402  (shared style + helpers)
from make_defense_figures import (BLUE, ORANGE, GREEN, OK, WARN, BAD, MUTED, INK, INK2,  # noqa: E402
                                  caption, finish, clean)

REPO = base.REPO
DOCS = REPO / "docs"
HOURS5, HOURS2 = 5.0, 2.0


# ---------------------------------------------------------------- statistics
def wilson(k, n, z=1.959964):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def garwood(k, hours, alpha=0.05):
    lo = 0.0 if k == 0 else stats.chi2.ppf(alpha / 2, 2 * k) / 2
    hi = stats.chi2.ppf(1 - alpha / 2, 2 * k + 2) / 2
    return k / hours, lo / hours, hi / hours


def newcombe_diff(k1, n1, k2, n2):
    """95% CI for p1 - p2 (Newcombe hybrid score, unpaired)."""
    p1, l1, u1 = wilson(k1, n1)
    p2, l2, u2 = wilson(k2, n2)
    d = p1 - p2
    return d, d - np.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + np.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def count_test(a, b):
    """Exact two-sided conditional test that two counts over equal exposure share a rate."""
    return stats.binomtest(min(a, b), a + b, 0.5).pvalue


def rate_err(k, hours):
    r, lo, hi = garwood(k, hours)
    return r, r - lo, hi - r


def rec_err(k, n):
    p, lo, hi = wilson(k, n)
    return 100 * p, 100 * (p - lo), 100 * (hi - p)


# -------------------------------------------------------------------- data
# (label, hits, n) for events caught; (label, alarms, hours) for false alarms.
FIGHTS_N = 200
ROBB_N = 133
STEPS = [  # violence progression: recall hits/200, false alarms, exposure hours
    ("First model\n(Aug)",           150, 10, HOURS2),
    ("+ person-crop\ncheck (1 Oct)", 158, 6,  HOURS2),
    ("+ UCA data\n(3 Oct)",          173, 4,  HOURS2),
    ("+ two-model\nensemble (5 Oct)", 176, 4, HOURS2),
    ("+ no-person\nfallback (7 Oct)", 180, 7, HOURS5),
    ("+ double-check\n(Qwen3-VL)",   188, 2,  HOURS5),
]
HEADLINE = [   # (label, hits, n, group)
    ("Fights, deployed (no check)",          176, 200, "fight"),
    ("Fights + double-check, GPU",           188, 200, "fight"),
    ("Fights + double-check, CPU",           182, 200, "fight"),
    ("Robbery, strict model alone",           11, 133, "robbery"),
    ("Robbery + double-check (2 questions)",  79, 133, "robbery"),
    ("Vandalism (27 of 31 acts)",             27,  31, "vandalism"),
]
FA = [         # (label, alarms over 5 h)
    ("Fights, deployed",               7),
    ("Fights + double-check, GPU",     2),
    ("Fights + double-check, CPU",     1),
    ("Robbery, strict model alone",    7),
    ("Robbery + double-check, GPU",    3),
    ("Vandalism",                      3),
    ("Armed-threat alerts, no check", 14),
    ("Armed-threat alerts + check",    7),
]


# ----------------------------------------------------------------- figures
def caption(fig, text, pad_in=0.62):   # noqa: F811  (inch-based: base.caption scales badly on tall figures)
    h = fig.get_figheight()
    fig.text(0.0, -(pad_in + 0.02) / h, text, ha="left", va="top", fontsize=7.5, color=INK2)


def fig_progression():
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 5.4), sharex=True, gridspec_kw={"hspace": 0.16})
    x = np.arange(len(STEPS))
    rec = [rec_err(h, FIGHTS_N) for _, h, _, _ in STEPS]
    cols = [BLUE] * 5 + [OK]
    a1.bar(x, [r[0] for r in rec], color=cols, width=0.58, zorder=3,
           yerr=[[r[1] for r in rec], [r[2] for r in rec]], capsize=3, ecolor=INK2, error_kw={"lw": 1})
    for i, r in enumerate(rec):
        a1.text(i, r[0] - 3.3, f"{r[0]:.1f}%", ha="center", fontsize=8, color="white", fontweight="bold", zorder=5)
    a1.set_ylim(60, 100)
    a1.set_ylabel("fights caught\n(of 200, 95% CI)")
    a1.set_title("Each change had to earn its place: fights caught and false alarms", loc="left")
    a1.xaxis.grid(False)
    clean(a1)

    fa = [rate_err(a, hrs) for _, _, a, hrs in STEPS]
    a2.bar(x, [r[0] for r in fa], color=[BAD, ORANGE, ORANGE, ORANGE, ORANGE, GREEN], width=0.58, zorder=3,
           yerr=[[r[1] for r in fa], [r[2] for r in fa]], capsize=3, ecolor=INK2, error_kw={"lw": 1})
    for i, r in enumerate(fa):
        a2.text(i + 0.33, r[0] + 0.15, f"{r[0]:.1f}", ha="left", fontsize=8, color=INK, fontweight="bold")
    a2.set_ylim(0, 10)
    a2.set_ylabel("false alarms\nper hour (95% CI)")
    a2.set_xticks(x)
    a2.set_xticklabels([s[0] for s in STEPS], fontsize=7.6)
    a2.xaxis.grid(False)
    clean(a2)
    caption(fig,
            "Same 200 held-out fights throughout. Rates for the first four steps are over 2 h of held-out street cameras; the last two over 5 h.\n"
            "Intervals: Wilson (fights), exact Poisson (false alarms). A step's improvement is not significant on its own where the intervals overlap.")
    finish(fig, "fig8_violence_progression")


def fig_check_effect():
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.6, 5.0), sharex=True, gridspec_kw={"hspace": 0.18})
    groups = ["Fights\n(200 events)", "Robbery\n(133 events)"]
    series = [  # label, colour, [(hits,n),(hits,n)], [alarms, alarms]
        ("without the double-check", MUTED, [(176, 200), (11, 133)], [7, 7]),
        ("with it, on the GPU",      OK,    [(188, 200), (79, 133)], [2, 3]),
        ("with it, on the CPU",      BLUE,  [(182, 200), (81, 133)], [1, 4]),
    ]
    w = 0.24
    x = np.arange(2)
    for j, (lab, col, rec, fa) in enumerate(series):
        off = (j - 1) * w
        e = [rec_err(h, n) for h, n in rec]
        a1.bar(x + off, [v[0] for v in e], w, color=col, label=lab, zorder=3,
               yerr=[[v[1] for v in e], [v[2] for v in e]], capsize=2.5, ecolor=INK2, error_kw={"lw": 1})
        for xi, v in zip(x + off, e):
            a1.text(xi, v[0] + v[2] + 1.5, f"{v[0]:.0f}%", ha="center", fontsize=7.8, fontweight="bold", color=INK)
        r = [rate_err(a, HOURS5) for a in fa]
        a2.bar(x + off, [v[0] for v in r], w, color=col, zorder=3,
               yerr=[[v[1] for v in r], [v[2] for v in r]], capsize=2.5, ecolor=INK2, error_kw={"lw": 1})
        for xi, v in zip(x + off, r):
            a2.text(xi, v[0] + v[2] + 0.1, f"{v[0]:.1f}", ha="center", fontsize=7.8, fontweight="bold", color=INK)
    a1.set_ylim(0, 126)
    a1.set_ylabel("events caught (95% CI)")
    a1.set_title("A second AI checks every alarm: more caught, fewer false alarms", loc="left")
    a1.legend(frameon=False, fontsize=8, loc="upper left", ncol=3, bbox_to_anchor=(0, 1.02))
    a1.xaxis.grid(False)
    clean(a1)
    a2.set_ylim(0, 4.6)
    a2.set_ylabel("false alarms per hour\n(95% CI)")
    a2.set_xticks(x)
    a2.set_xticklabels(groups)
    a2.xaxis.grid(False)
    clean(a2)
    caption(fig,
            "5 h of held-out street cameras; held-out crime clips spliced into continuous footage. Robbery 'without' is the strict robbery model.\n"
            "Robbery here counts robbery-labelled alerts only; 20.5% of the 200 held-out fights also receive a robbery label with the check on.")
    finish(fig, "fig9_double_check_effect")


def fig_operating_points():
    pts = [  # label, false alarms/hr, recall %, colour, (dx, dy) in points, ha
        ("fight model alone, loose setting@(no check): 13.4/hr",  13.4, 96.0, BAD,   (-12, -34), "right"),
        ("deployed strict setting@(no check): 1.4/hr",             1.4, 88.0, MUTED, (14, -26),  "left"),
        ("double-check on the GPU,@re-check every 7 s: 0.4/hr",    0.4, 94.0, OK,    (-14, 16),  "right"),
        ("double-check on the CPU@backend: 0.2/hr",                0.2, 91.0, BLUE,  (14, -4),   "left"),
        ("stricter cut-off (keep 90%):@0.0/hr",                    0.0, 87.5, WARN,  (14, -26),  "left"),
    ]
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    ax.annotate("", xy=(0.4, 94.0), xytext=(13.4, 96.0),
                arrowprops={"arrowstyle": "->", "color": OK, "lw": 1.5, "ls": "--"}, zorder=2)
    for lab, fa, rc, col, off, ha in pts:
        ax.scatter([fa], [rc], s=70, color=col, zorder=4, edgecolor="white", linewidth=1)
        ax.annotate(lab.replace("@", chr(10)), (fa, rc), xytext=off, textcoords="offset points", fontsize=7.6, color=INK, ha=ha,
                    arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.7})
    ax.text(0.9, 98.2, "the check removes ~97% of the false alarms@for a 2-point drop in fights caught".replace("@", chr(10)),
            fontsize=8, color=OK, fontweight="bold")
    ax.set_xscale("symlog", linthresh=0.5)
    ax.set_xlim(-0.05, 25)
    ax.set_ylim(84, 100)
    ax.set_xlabel("false alarms per hour (symmetric log scale)")
    ax.set_ylabel("fights caught (% of 200)")
    ax.set_title("What the double-check trades", loc="left")
    clean(ax)
    caption(fig, "5 h of held-out street cameras, 200 held-out fights. Check cut-offs fitted on half the source videos and scored on the other half.", pad_in=0.55)
    finish(fig, "fig10_operating_points")


def fig_weapons():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.5), gridspec_kw={"wspace": 0.38, "width_ratios": [1, 1.25]})
    r = [rate_err(14, HOURS5), rate_err(7, HOURS5)]
    a1.bar(["alerts as\nraised", "after the\ndouble-check"], [v[0] for v in r], color=[MUTED, OK], width=0.5, zorder=3,
           yerr=[[v[1] for v in r], [v[2] for v in r]], capsize=3, ecolor=INK2, error_kw={"lw": 1})
    for i, v in enumerate(r):
        a1.text(i, v[0] + v[2] + 0.12, f"{v[0]:.1f}/hr", ha="center", fontsize=8.5, fontweight="bold", color=INK)
    a1.set_ylim(0, 7)
    a1.set_ylabel("false armed-threat alerts per hour")
    a1.set_title("False alerts", loc="left")
    a1.xaxis.grid(False)
    clean(a1)
    labels = ["real weapons\nkept", "street-size\nguns kept", "phones held in\nhand rejected"]
    vals = [97.9, 95.2, 51.0]
    a2.bar(labels, vals, color=[OK, OK, BLUE], width=0.5, zorder=3)
    for i, v in enumerate(vals):
        a2.text(i, v + 1.5, f"{v:.1f}%", ha="center", fontsize=8.5, fontweight="bold", color=INK)
    a2.set_ylim(0, 112)
    a2.set_ylabel("%")
    a2.set_title("What it keeps and what it rejects", loc="left")
    a2.xaxis.grid(False)
    clean(a2)
    caption(fig,
            "ARMED THREAT alerts from the app's own weapon pipeline on 5 h of held-out street cameras: 14 alerts, every one false (phones, bag straps, shirt prints, a belt).\n"
            "The 1.4/hr rests on 7 alerts, so read it as 'about half'. Cut-off keeps 98% of 1,175 hand-labelled guns and knives (fitted on half, scored on the other half).")
    finish(fig, "fig11_weapon_double_check")


def fig_robbery_design():
    steps = [  # label, robbery recall hits/133, fights labelled robbery %
        ("strict robbery\nmodel alone", 11, 25.0),
        ("robbery model +\nyes/no check", 68, 69.0),
        ("whole-frame fight\ncandidates + yes/no", 92, 86.0),
        ("+ robbery-vs-fight\nquestion (shipped)", 79, 20.5),
    ]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10.2, 3.9), gridspec_kw={"wspace": 0.3})
    x = np.arange(len(steps))
    e = [rec_err(h, ROBB_N) for _, h, _ in steps]
    a1.bar(x, [v[0] for v in e], color=[MUTED, BLUE, BLUE, OK], width=0.58, zorder=3,
           yerr=[[v[1] for v in e], [v[2] for v in e]], capsize=3, ecolor=INK2, error_kw={"lw": 1})
    for i, v in enumerate(e):
        a1.text(i, v[0] + v[2] + 2, f"{v[0]:.0f}%", ha="center", fontsize=8.2, fontweight="bold", color=INK)
    a1.set_ylim(0, 105)
    a1.set_ylabel("robbery events labelled robbery\n(of 133, 95% CI)")
    a1.set_xticks(x)
    a1.set_xticklabels([s[0] for s in steps], fontsize=6.8)
    a1.set_title("Robbery caught", loc="left")
    a1.xaxis.grid(False)
    clean(a1)
    f = [rec_err(round(p * 2), 200) for _, _, p in steps]
    a2.bar(x, [v[0] for v in f], color=[MUTED, BAD, BAD, OK], width=0.58, zorder=3,
           yerr=[[v[1] for v in f], [v[2] for v in f]], capsize=3, ecolor=INK2, error_kw={"lw": 1})
    for i, v in enumerate(f):
        a2.text(i, v[0] + v[2] + 2, f"{v[0]:.1f}%", ha="center", fontsize=8.2, fontweight="bold", color=INK)
    a2.set_ylim(0, 105)
    a2.set_ylabel("real FIGHTS also labelled robbery\n(of 200, 95% CI)")
    a2.set_xticks(x)
    a2.set_xticklabels([s[0] for s in steps], fontsize=6.8)
    a2.set_title("...and the cost", loc="left")
    a2.xaxis.grid(False)
    clean(a2)
    caption(fig,
            "The third bar looks best on the left until the right shows it: one yes/no question labels 86% of real fights as robbery. Asking a second question\n"
            "(is it more robbery than fight?) keeps most of the robbery gain while bringing mislabelled fights back to the old model's level.")
    finish(fig, "fig12_robbery_design")


def fig_data():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.5), gridspec_kw={"wspace": 0.34, "width_ratios": [1.25, 1]})
    splits = ["train", "validation", "test"]
    violent = np.array([3057, 643, 655])
    normal = np.array([5946, 643, 655])
    a1.bar(splits, violent, color=BAD, width=0.55, label="violent", zorder=3)
    a1.bar(splits, normal, bottom=violent, color=BLUE, width=0.55, label="normal", zorder=3)
    for i, (v, nrm) in enumerate(zip(violent, normal)):
        a1.text(i, v + nrm + 150, f"{v + nrm:,}", ha="center", fontsize=8.5, fontweight="bold", color=INK)
    a1.set_ylim(0, 10200)
    a1.set_ylabel("clips")
    a1.set_title("Violence model: 11,599 clips", loc="left")
    a1.legend(frameon=False, fontsize=8)
    a1.xaxis.grid(False)
    clean(a1)
    a2.bar(["daytime\n(26 cameras)", "night\n(22 cameras)"], [2560, 1320], color=[ORANGE, INK2], width=0.5, zorder=3)
    for i, v in enumerate([2560, 1320]):
        a2.text(i, v + 60, f"{v:,}", ha="center", fontsize=8.5, fontweight="bold", color=INK)
    a2.set_ylim(0, 3000)
    a2.set_ylabel("clips captured by us")
    a2.set_title("Philippine street cameras: 3,880", loc="left")
    a2.xaxis.grid(False)
    clean(a2)
    caption(fig,
            "Split by source video, so no scene appears in two splits. Also used: RWF-2000, SCVD, CCTV-Fights and the normal half of UCF-Crime.\n"
            "UCF-Crime's violent half is left out on purpose: ~96% of it is indoors, and an outdoor pole would learn 'indoor = violent'.")
    finish(fig, "fig13_training_data")


def fig_misses():
    labels = ["scored low: ground fights, distant\ncrowds, close-up or occluded views", "pose model found nobody\n(blurry footage)",
              "began while an earlier alarm\nwas still on"]
    vals = [13, 7, 4]
    fig, ax = plt.subplots(figsize=(6.8, 2.9))
    ax.barh(labels[::-1], vals[::-1], color=[MUTED, WARN, BAD], height=0.55, zorder=3)
    for i, v in enumerate(vals[::-1]):
        ax.text(v + 0.25, i, f"{v}", va="center", fontsize=9, fontweight="bold", color=INK)
    ax.set_xlim(0, 16)
    ax.set_xlabel("fights missed (of 200)")
    ax.set_title("Where the 24 missed fights went (deployed detector, before the fallback and the check)", loc="left", fontsize=9.3)
    ax.yaxis.grid(False)
    clean(ax)
    caption(fig, "The no-person fallback (7 Oct) then recovered most of the 7 'found nobody' fights, and re-checking every 7 s the ones that began under an alarm.")
    finish(fig, "fig14_missed_fights")


def fig_forest():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.2, 4.6), gridspec_kw={"wspace": 0.85, "width_ratios": [1.15, 1]})
    cmap = {"fight": BLUE, "robbery": ORANGE, "vandalism": GREEN}
    for i, (lab, k, n, g) in enumerate(HEADLINE[::-1]):
        p, lo, hi = wilson(k, n)
        a1.errorbar(100 * p, i, xerr=[[100 * (p - lo)], [100 * (hi - p)]], fmt="o", color=cmap[g], ecolor=cmap[g],
                    capsize=3, markersize=6, zorder=3)
        a1.text(100 * hi + 1.5, i, f"{100*p:.1f}%  ({100*lo:.0f}–{100*hi:.0f})", va="center", fontsize=7.4, color=INK)
    a1.set_yticks(range(len(HEADLINE)))
    a1.set_yticklabels([h[0] for h in HEADLINE[::-1]], fontsize=7.6)
    a1.set_xlim(0, 135)
    a1.set_xticks(range(0, 101, 20))
    a1.set_xlabel("events caught, % (95% Wilson CI)")
    a1.set_title("Recall, with its uncertainty", loc="left")
    a1.yaxis.grid(False)
    clean(a1)
    for i, (lab, k) in enumerate(FA[::-1]):
        r, lo, hi = garwood(k, HOURS5)
        col = BLUE if lab.startswith("Fights") else ORANGE if lab.startswith("Robbery") else GREEN if lab == "Vandalism" else INK2
        a2.errorbar(r, i, xerr=[[r - lo], [hi - r]], fmt="s", color=col, ecolor=col, capsize=3, markersize=5.5, zorder=3)
        a2.text(hi + 0.25, i, f"{r:.1f}  ({lo:.1f}–{hi:.1f})", va="center", fontsize=7.4, color=INK)
    a2.set_yticks(range(len(FA)))
    a2.set_yticklabels([f[0] for f in FA[::-1]], fontsize=7.6)
    a2.set_xlim(0, 12.5)
    a2.set_xlabel("false alarms per hour (95% exact Poisson CI)")
    a2.set_title("False alarms, with theirs", loc="left")
    a2.yaxis.grid(False)
    clean(a2)
    caption(fig,
            "Five hours of cameras gives 1 to 14 alarms per setting, so the false-alarm intervals are wide and overlap: the direction is consistent, the size is not pinned down.\n"
            "Recall intervals use the number of held-out events (200 fights, 133 robbery events, 31 vandalism acts).")
    finish(fig, "fig15_confidence_intervals")


# ---------------------------------------------------------- summary markdown
def write_summary():
    L = []
    L.append("# Statistics summary — October 2026\n")
    L.append("Generated by `tools/make_october_figures.py` from the counts in `detection_performance_report.md`. "
             "Counts are the reported rate times n (or hours), rounded to a whole event or alarm. Nothing here is "
             "recomputed from raw footage.\n")
    L.append("## Events caught (95% Wilson interval)\n")
    L.append("| Setting | Caught | n | Rate | 95% CI |\n|---|---|---|---|---|")
    for lab, k, n, _ in HEADLINE:
        p, lo, hi = wilson(k, n)
        L.append(f"| {lab} | {k} | {n} | {100*p:.1f}% | {100*lo:.1f}–{100*hi:.1f}% |")
    L.append("\n## False alarms (exact Poisson interval, 5 h of held-out street cameras)\n")
    L.append("| Setting | Alarms | Hours | Per hour | 95% CI |\n|---|---|---|---|---|")
    for lab, k in FA:
        r, lo, hi = garwood(k, HOURS5)
        L.append(f"| {lab} | {k} | {HOURS5:g} | {r:.1f} | {lo:.1f}–{hi:.1f} |")
    d, lo, hi = newcombe_diff(188, 200, 176, 200)
    p1 = count_test(7, 2)
    p2 = count_test(14, 7)
    p3 = count_test(7, 3)
    L.append("\n## Does the double-check really help?\n")
    L.append("| Comparison | Result | Reading |\n|---|---|---|")
    L.append(f"| Fights caught, 94.0% vs 88.0% (same 200 fights) | +{100*d:.1f} points, unpaired 95% CI {100*lo:.1f} to {100*hi:.1f} | "
             f"{'Interval only just excludes zero' if 0 < lo < 1.5 else 'Interval excludes zero' if lo > 0 else 'Interval includes zero'}. This is the conservative unpaired test: the same fights are scored both times, so a paired test would be tighter, but the per-event outcomes for the deployed baseline were not kept to run one. |")
    d2, lo2, hi2 = newcombe_diff(79, 133, 11, 133)
    L.append(f"| Robbery labelled robbery, 59.4% vs 8.3% (same 133 events) | +{100*d2:.1f} points, unpaired 95% CI {100*lo2:.1f} to {100*hi2:.1f} | Far from zero; the largest single gain measured. |")
    L.append(f"| Fight false alarms, 7 vs 2 over 5 h | exact conditional test p = {p1:.2f} | Not significant on 5 h: 9 alarms in total cannot establish the size of the drop, only that it points the same way as the other settings. |")
    L.append(f"| Armed-threat false alerts, 14 vs 7 over 5 h | exact conditional test p = {p2:.2f} | Not significant on its own; read as 'about half'. |")
    L.append(f"| Robbery false alarms, 7 vs 3 over 5 h | exact conditional test p = {p3:.2f} | Not significant on 5 h. |")
    NL = chr(10)
    L.append(NL + "**How to say this honestly:** the robbery gain is large and clearly real (the intervals are far apart). "
             "The fights gain is smaller: under the conservative unpaired test its lower bound is only just above zero, and it would "
             "likely be clearer paired. The false-alarm reductions all point the same way (fights, robbery, weapons) but rest on "
             "1 to 14 alarms each, so none of them is individually significant on 5 hours; more camera-hours are needed to pin down "
             "how many false alarms the check removes." + NL)
    L.append("## Per-clip accuracy of the violence model (1,310 held-out clips)\n")
    p, lo, hi = wilson(1220, 1310)
    L.append(f"Accuracy 93.1% ({1220} of 1,310), 95% CI {100*lo:.1f}–{100*hi:.1f}%. Recall 92.8%, precision 93.4%.\n")
    L.append("## Caveats that apply to every figure\n")
    L.append("- Recall on the real deployment cameras is unmeasured: no labelled incident has been recorded on them.\n"
             "- The check's cut-offs were fitted on half the source videos and scored on the other half, but the keep rates (98% and 80%) were chosen after seeing results.\n"
             "- Fight clips are spliced into street footage; the events are real held-out fights, the setting is constructed.\n")
    (DOCS / "statistics_summary.md").write_text("\n".join(L), encoding="utf-8")
    print("  statistics_summary.md")


if __name__ == "__main__":
    print("writing October figures to", base.OUT)
    fig_progression()
    fig_check_effect()
    fig_operating_points()
    fig_weapons()
    fig_robbery_design()
    fig_data()
    fig_misses()
    fig_forest()
    write_summary()

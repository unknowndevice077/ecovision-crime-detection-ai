"""
confidence_trace_plotter.py -- reads logs/x3d_confidence_trace.csv
(written by the updated X3DViolenceDetector) and plots raw vs EMA
confidence per track so you can SEE exactly where instability happens
(noisy spikes, flapping at threshold, slow drift, etc).

USAGE
    python confidence_trace_plotter.py --track 3
    python confidence_trace_plotter.py --all
"""

import argparse
import csv
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from x3d_violence_detector import VIOLENCE_CONFIDENCE_THRESHOLD

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "x3d_confidence_trace.csv")
OUT_DIR = "confidence_plots"


def load_trace():
    per_track = defaultdict(list)
    with open(LOG_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = int(row["track_id"])
            per_track[tid].append({
                "frame": int(row["frame_count"]),
                "raw": float(row["raw_conf"]),
                "ema": float(row["ema_conf"]),
                "confirmed": row["confirmed"] == "True",
            })
    return per_track


def plot_track(tid, rows):
    frames = [r["frame"] for r in rows]
    raw = [r["raw"] for r in rows]
    ema = [r["ema"] for r in rows]
    confirmed = [1 if r["confirmed"] else 0 for r in rows]

    fig, ax1 = plt.subplots(figsize=(12, 4))
    ax1.plot(frames, raw, label="raw confidence", alpha=0.5, color="gray")
    ax1.plot(frames, ema, label="EMA confidence", color="blue", linewidth=2)
    ax1.axhline(VIOLENCE_CONFIDENCE_THRESHOLD, color="red", linestyle="--", linewidth=1,
                label=f"threshold ({VIOLENCE_CONFIDENCE_THRESHOLD:.2f})")
    ax1.set_ylim(0, 1)
    ax1.set_xlabel("frame")
    ax1.set_ylabel("confidence")
    ax1.legend(loc="upper left")

    ax2 = ax1.twinx()
    ax2.fill_between(frames, confirmed, step="mid", alpha=0.15, color="red", label="confirmed violent")
    ax2.set_ylim(0, 1.2)
    ax2.set_yticks([])

    plt.title(f"Track {tid} -- confidence over time")
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"track_{tid}.png")
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close()
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--track", type=int, default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    data = load_trace()
    if args.all or args.track is None:
        for tid, rows in data.items():
            plot_track(tid, rows)
    else:
        if args.track not in data:
            raise SystemExit(f"No log rows found for track {args.track}")
        plot_track(args.track, data[args.track])
"""
eval_history.py -- shared run-logger for the violence-detection eval
scripts (test_x3d_true_heldout.py, generate_eval_report.py,
calibrate_threshold.py).

Each of those scripts overwrites the same output file every run
(x3d_true_heldout_results.csv, eval_report_val/results.csv,
calibration_results.csv) -- fine for inspecting the LATEST run, useless
for seeing whether a change actually helped. This module appends one
summary row per run to maincode/eval_history.csv (kept alongside the
scripts, never overwritten) so you can track accuracy/recall/precision/FPR
across tuning iterations over time, alongside which config/commit produced
each number.

USAGE (called from the eval scripts themselves, not run standalone):
    from eval_history import log_run
    log_run("test_x3d_true_heldout", split="val", tp=238, fp=92, tn=303, fn=136,
             notes="post buffer-gating fix")
"""

import csv
import os
import subprocess
import time

HISTORY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_history.csv")

FIELDNAMES = [
    "timestamp", "git_commit", "script", "split", "notes",
    "total_clips", "tp", "fp", "tn", "fn",
    "accuracy_pct", "recall_pct", "precision_pct", "fpr_pct",
    "confidence_threshold", "consecutive_required", "buffer_span",
    "min_buffer_for_inference", "check_interval", "ema_alpha",
    "release_hysteresis_margin",
]


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5,
        )
        commit = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return f"{commit}{'+dirty' if dirty else ''}" if commit else "unknown"
    except Exception:
        return "unknown"


def log_run(script: str, split: str, tp: int, fp: int, tn: int, fn: int, notes: str = "") -> None:
    """Appends one row to eval_history.csv. Pulls the current tunables
    straight from x3d_violence_detector so every row records exactly what
    config produced it, even if config.json changes between runs."""
    import x3d_violence_detector as x3d_mod

    total = tp + fp + tn + fn
    accuracy = (tp + tn) / total * 100 if total else 0.0
    recall = tp / (tp + fn) * 100 if (tp + fn) else 0.0
    precision = tp / (tp + fp) * 100 if (tp + fp) else 0.0
    fpr = fp / (fp + tn) * 100 if (fp + tn) else 0.0

    row = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": _git_commit(),
        "script": script,
        "split": split,
        "notes": notes,
        "total_clips": total,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy_pct": round(accuracy, 2),
        "recall_pct": round(recall, 2),
        "precision_pct": round(precision, 2),
        "fpr_pct": round(fpr, 2),
        "confidence_threshold": x3d_mod.VIOLENCE_CONFIDENCE_THRESHOLD,
        "consecutive_required": x3d_mod.VIOLENCE_CONSECUTIVE_REQUIRED,
        "buffer_span": x3d_mod.BUFFER_SPAN,
        "min_buffer_for_inference": x3d_mod.MIN_BUFFER_FOR_INFERENCE,
        "check_interval": x3d_mod.X3D_CHECK_INTERVAL,
        "ema_alpha": x3d_mod.EMA_ALPHA,
        "release_hysteresis_margin": x3d_mod.RELEASE_HYSTERESIS_MARGIN,
    }

    file_exists = os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    print(f"\n[eval_history] Logged to {HISTORY_PATH} "
          f"(acc={row['accuracy_pct']}% recall={row['recall_pct']}% "
          f"precision={row['precision_pct']}% fpr={row['fpr_pct']}%)")

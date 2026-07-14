"""
EcoVision -- Evaluation Report Generator
==================================================================
Extends test_x3d_true_heldout.py's true held-out evaluation with a full
visual audit trail for the thesis writeup.

For every clip in the SAME 769-clip held-out set used in
test_x3d_true_heldout.py (imported directly from that file, so the
split can never drift out of sync with your cited numbers), this
script produces:

    eval_report/
        TP/  <index>_<clip_name>.jpg
        FP/  <index>_<clip_name>.jpg
        TN/  <index>_<clip_name>.jpg
        FN/  <index>_<clip_name>.jpg
        results.csv          <- one row per clip, same fields as before
        summary_stats.txt    <- aggregate confusion matrix + breakdowns

Each JPG shows the EXACT 160x160 crop the model was looking at at its
moment of peak confidence during that clip (pulled from
X3DViolenceDetector.get_latest_live_crop(), the same feed your PiP
viewer in main.py uses), annotated with ground truth / prediction /
confidence / outcome. If a clip never produced a real inference (no
pose ever tracked, or the buffer never filled), the JPG instead shows
the clip's first raw frame labeled "NO INFERENCE" so you can still
audit *why* it was skipped -- this is usually the most useful case
to look at for FN error analysis.

REQUIREMENTS
    Same as test_x3d_true_heldout.py: torch, pytorchvideo, ultralytics,
    opencv-python, numpy. Must be run from the same folder as
    test_x3d_true_heldout.py and x3d_violence_detector.py so the import
    below resolves.

HOW TO USE
    python generate_eval_report.py --rwf-root "PATH_TO_RWF2000" --scvd-root "PATH_TO_SCVD" --device 0

    Optional flags:
      --output-dir eval_report     Where to write the report (default: eval_report)
      --limit 50                   Only evaluate the first N held-out clips (fast smoke test)
"""

import argparse
import csv
import os
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from x3d_violence_detector import X3DViolenceDetector

# Reuse the EXACT split logic from the existing held-out test -- this is
# the single most important correctness requirement here. If this script
# built its own copy of the gather/shuffle/split logic and it drifted even
# slightly from train_x3d_full.py's, the "held-out" set wouldn't actually
# match what was reported in the thesis anymore.
from test_x3d_true_heldout import gather_all_clips_EXACT_TRAINING_LOGIC, POSE_IMGSZ, POSE_MODEL_PATH


CROP_DISPLAY_SIZE = 320   # upscaled display size for the saved crop (source crop is 160x160)
HEADER_HEIGHT     = 90    # label strip above the crop


# ──────────────────────────────────────────────────────────────────────────────
# PER-CLIP EVALUATION WITH SNAPSHOT CAPTURE
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_clip_with_snapshot(video_path: Path, pose_model, x3d_detector, device: str) -> dict:
    """Same evaluation loop as test_x3d_true_heldout.py's evaluate_clip(),
    plus tracking of the single best (highest-confidence) crop seen during
    the clip so it can be saved as a labeled JPG afterward."""
    cap = cv2.VideoCapture(str(video_path))
    frame_count = 0
    frames_with_pose = 0
    any_violence_detected = False
    max_confidence_seen = 0.0

    best_conf = -1.0
    best_crop = None       # the actual 160x160 crop fed to the model at peak confidence
    first_raw_frame = None # fallback if no inference ever ran

    x3d_detector._frame_buffers.clear()
    x3d_detector._last_check_frame.clear()
    x3d_detector._cached_result.clear()
    x3d_detector._real_inference_count.clear()

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        if first_raw_frame is None:
            first_raw_frame = frame.copy()

        try:
            pose_res = pose_model.track(frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, device=device)
        except Exception:
            continue
        if not (pose_res[0].boxes is not None and pose_res[0].boxes.id is not None):
            continue
        frames_with_pose += 1
        ids = pose_res[0].boxes.id.int().cpu().tolist()
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()
        for tid, p_box in zip(ids, boxes):
            is_violent, conf = x3d_detector.update(tid, frame, p_box, frame_count, all_boxes=boxes)
            max_confidence_seen = max(max_confidence_seen, conf)
            if is_violent:
                any_violence_detected = True
            if conf > best_conf:
                best_conf = conf
                live_crop = x3d_detector.get_latest_live_crop(tid)
                if live_crop is not None:
                    best_crop = live_crop.copy()

    cap.release()

    # Force-flush any track that never got a real inference during the clip
    # (identical logic/bugfix as test_x3d_true_heldout.py: driven by the
    # counter incremented only inside _run_inference itself).
    forced_flush = False
    had_any_buffer = len(x3d_detector._frame_buffers) > 0
    total_real_inferences = sum(x3d_detector._real_inference_count.values())

    for tid in list(x3d_detector._frame_buffers.keys()):
        if x3d_detector.get_inference_count(tid) == 0:
            forced_flush = True
            is_violent, conf = x3d_detector.force_inference(tid)
            max_confidence_seen = max(max_confidence_seen, conf)
            if is_violent:
                any_violence_detected = True
            if conf > best_conf:
                best_conf = conf
                live_crop = x3d_detector.get_latest_live_crop(tid)
                if live_crop is not None:
                    best_crop = live_crop.copy()

    pose_rate = frames_with_pose / frame_count if frame_count > 0 else 0
    return {
        "detected": any_violence_detected,
        "max_confidence": max_confidence_seen,
        "frame_count": frame_count,
        "frames_with_pose": frames_with_pose,
        "had_any_buffer": had_any_buffer,
        "forced_flush": forced_flush,
        "pose_detection_rate": round(pose_rate, 3),
        "real_inference_count": total_real_inferences,
        "best_crop": best_crop,               # None if no inference ever ran
        "first_raw_frame": first_raw_frame,    # fallback visual, may also be None if video unreadable
    }


# ──────────────────────────────────────────────────────────────────────────────
# LABELED JPG RENDERING
# ──────────────────────────────────────────────────────────────────────────────

_OUTCOME_COLORS = {
    "TP": (0, 200, 0),
    "TN": (0, 200, 0),
    "FP": (0, 0, 255),
    "FN": (0, 0, 255),
}


def render_labeled_jpg(result: dict, meta: dict) -> np.ndarray:
    """Builds the final annotated image: header strip with ground truth /
    prediction / confidence / outcome, above either the peak-confidence
    crop or a fallback frame with a NO INFERENCE label."""
    crop = result.get("best_crop")
    no_inference = crop is None

    if crop is not None:
        display = cv2.resize(crop, (CROP_DISPLAY_SIZE, CROP_DISPLAY_SIZE), interpolation=cv2.INTER_NEAREST)
    else:
        fallback = result.get("first_raw_frame")
        if fallback is not None:
            display = cv2.resize(fallback, (CROP_DISPLAY_SIZE, CROP_DISPLAY_SIZE))
        else:
            display = np.zeros((CROP_DISPLAY_SIZE, CROP_DISPLAY_SIZE, 3), dtype=np.uint8)

    canvas = np.zeros((CROP_DISPLAY_SIZE + HEADER_HEIGHT, CROP_DISPLAY_SIZE, 3), dtype=np.uint8)
    canvas[HEADER_HEIGHT:, :] = display

    color = _OUTCOME_COLORS.get(meta["outcome"], (255, 255, 255))
    cv2.putText(canvas, meta["outcome"], (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)
    cv2.putText(canvas, f"GT: {meta['ground_truth']}", (110, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"Pred: {meta['predicted']} ({meta['max_confidence']:.2f})", (110, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"pose_seen: {meta['pose_detection_rate']}  infer#: {meta['real_inference_count']}",
                (8, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (170, 170, 170), 1, cv2.LINE_AA)
    fname_display = meta["file"] if len(meta["file"]) <= 46 else meta["file"][:43] + "..."
    cv2.putText(canvas, fname_display, (8, 76), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (170, 170, 170), 1, cv2.LINE_AA)

    if no_inference:
        cv2.putText(canvas, "NO INFERENCE -- fallback frame shown", (10, HEADER_HEIGHT + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 140, 255), 1, cv2.LINE_AA)

    return canvas


# ──────────────────────────────────────────────────────────────────────────────
# MAIN REPORT RUN
# ──────────────────────────────────────────────────────────────────────────────

def run_report(roots: list, device: str, output_dir: str, split: str = "val", limit: int = None):
    out_root = Path(output_dir)
    for sub in ("TP", "FP", "TN", "FN"):
        (out_root / sub).mkdir(parents=True, exist_ok=True)

    print("Recreating EXACT train/val split from train_x3d_full.py (seed=42)...")
    all_clips = gather_all_clips_EXACT_TRAINING_LOGIC(roots)
    split_idx = int(len(all_clips) * 0.85)
    train_clips = all_clips[:split_idx]
    val_clips = all_clips[split_idx:]

    if split == "val":
        target_clips = val_clips
        split_label = "TRUE HELD-OUT (val) -- clips the model NEVER trained on"
    elif split == "train":
        target_clips = train_clips
        split_label = "TRAINING SET -- clips the model DID train on"
    elif split == "all":
        target_clips = all_clips
        split_label = "ALL CLIPS (train + val combined)"
    else:
        raise ValueError(f"Unknown split: {split}")

    if limit is not None:
        target_clips = target_clips[:limit]
        print(f"--limit set: evaluating only the first {len(target_clips)} clips (smoke test).")

    fight_n = sum(1 for _, l in target_clips if l == 1)
    normal_n = sum(1 for _, l in target_clips if l == 0)
    print(f"{split_label}: {len(target_clips)} clips ({fight_n} violent, {normal_n} normal)")
    if split != "val":
        print("⚠️  This split includes clips the model was trained on -- these numbers are")
        print("    NOT a measure of generalization. Do not cite this as your model's real")
        print("    performance; use --split val (the default) for that.")

    print(f"\nLoading pose model on {device}...")
    pose_model = YOLO(POSE_MODEL_PATH)
    print("Loading X3D-XS detector...")
    x3d_detector = X3DViolenceDetector(device=device)

    csv_rows = []
    confusion = defaultdict(int)
    conf_by_outcome = defaultdict(list)
    no_inference_count = 0
    run_start = time.time()

    for i, (video_path, ground_truth_violent) in enumerate(target_clips):
        result = evaluate_clip_with_snapshot(video_path, pose_model, x3d_detector, device)
        predicted_violent = result["detected"]

        if ground_truth_violent and predicted_violent:
            outcome = "TP"
        elif ground_truth_violent and not predicted_violent:
            outcome = "FN"
        elif not ground_truth_violent and predicted_violent:
            outcome = "FP"
        else:
            outcome = "TN"
        confusion[outcome] += 1
        conf_by_outcome[outcome].append(result["max_confidence"])
        if result["best_crop"] is None:
            no_inference_count += 1

        meta = {
            "file": video_path.name,
            "ground_truth": "violent" if ground_truth_violent else "normal",
            "predicted": "violent" if predicted_violent else "normal",
            "outcome": outcome,
            "max_confidence": round(result["max_confidence"], 3),
            "pose_detection_rate": result.get("pose_detection_rate", "n/a"),
            "real_inference_count": result.get("real_inference_count", "n/a"),
        }

        # Save the labeled JPG into its outcome folder
        labeled = render_labeled_jpg(result, meta)
        safe_stem = video_path.stem.replace(" ", "_")
        jpg_name = f"{i:04d}_{safe_stem}.jpg"
        jpg_path = out_root / outcome / jpg_name
        cv2.imwrite(str(jpg_path), labeled)

        csv_rows.append({
            **meta,
            "frame_count": result["frame_count"],
            "frames_with_pose": result.get("frames_with_pose", "n/a"),
            "had_any_buffer": result.get("had_any_buffer", "n/a"),
            "forced_flush": result.get("forced_flush", "n/a"),
            "image_file": f"{outcome}/{jpg_name}",
        })

        print(f"[{i+1}/{len(target_clips)}] {video_path.name}: "
              f"truth={meta['ground_truth']} pred={meta['predicted']} ({outcome}) "
              f"conf={meta['max_confidence']:.2f} -> {outcome}/{jpg_name}")

    # ── results.csv ───────────────────────────────────────────────────────────
    csv_path = out_root / "results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
        writer.writeheader()
        writer.writerows(csv_rows)

    # ── summary_stats.txt ─────────────────────────────────────────────────────
    tp, fp, tn, fn = confusion["TP"], confusion["FP"], confusion["TN"], confusion["FN"]
    total = tp + fp + tn + fn
    elapsed_min = (time.time() - run_start) / 60

    def _avg(lst):
        return sum(lst) / len(lst) if lst else float("nan")

    lines = []
    lines.append("=" * 78)
    lines.append("EcoVision X3D-XS -- EVALUATION REPORT")
    lines.append("=" * 78)
    lines.append(f"Split evaluated: {split_label}")
    if split != "val":
        lines.append("")
        lines.append("⚠️  WARNING: this split includes clips the model was TRAINED on.")
        lines.append("    These numbers measure memorization, not generalization, and")
        lines.append("    should NOT be reported as the model's real-world performance.")
        lines.append("    Use this only to compare against the --split val numbers as an")
        lines.append("    overfitting diagnostic (e.g. 'train acc 98% vs held-out acc 68%")
        lines.append("    indicates overfitting'). The --split val run is the number that")
        lines.append("    belongs in the results section.")
    lines.append("")
    lines.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Dataset roots: {roots}")
    lines.append(f"Total clips evaluated: {total}")
    lines.append(f"Evaluation wall time: {elapsed_min:.1f} min")
    lines.append("")
    lines.append("-- Confusion Matrix --")
    lines.append(f"TP={tp}  FN={fn}  TN={tn}  FP={fp}")
    if total > 0:
        lines.append(f"Accuracy:  {(tp+tn)/total*100:.1f}%")
    if tp + fn > 0:
        lines.append(f"Recall:    {tp/(tp+fn)*100:.1f}%")
    if tp + fp > 0:
        lines.append(f"Precision: {tp/(tp+fp)*100:.1f}%")
    if fp + tn > 0:
        lines.append(f"False Positive Rate: {fp/(fp+tn)*100:.1f}%")
    lines.append("")
    lines.append("-- Mean max-confidence by outcome bucket --")
    for bucket in ("TP", "FN", "TN", "FP"):
        lines.append(f"  {bucket}: {_avg(conf_by_outcome[bucket]):.3f}  (n={len(conf_by_outcome[bucket])})")
    lines.append("")
    lines.append("-- Pipeline health --")
    lines.append(f"Clips with NO real inference ever run (no crop captured): {no_inference_count} / {total}")
    lines.append(f"(these clips' JPGs show a fallback raw frame labeled 'NO INFERENCE' --")
    lines.append(f" worth a manual look, since these are likely pose-tracking failures, not")
    lines.append(f" model failures)")
    lines.append("")
    lines.append("-- Output layout --")
    lines.append(f"  {output_dir}/TP/*.jpg, FP/*.jpg, TN/*.jpg, FN/*.jpg")
    lines.append(f"  {output_dir}/results.csv")
    lines.append(f"  {output_dir}/summary_stats.txt  (this file)")

    summary_text = "\n".join(lines)
    with open(out_root / "summary_stats.txt", "w") as f:
        f.write(summary_text + "\n")

    print("\n" + summary_text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EcoVision X3D-XS evaluation report generator")
    parser.add_argument("--rwf-root", type=str, default=None)
    parser.add_argument("--scvd-root", type=str, default=None)
    parser.add_argument("--device", type=str, default="0")
    parser.add_argument("--output-dir", type=str, default=None,
                         help="Defaults to 'eval_report_<split>' so train/val/all runs never overwrite each other")
    parser.add_argument("--split", type=str, choices=["val", "train", "all"], default="val",
                         help="'val' (default) is the true held-out generalization number. "
                              "'train' and 'all' include clips the model trained on -- overfitting "
                              "diagnostics only, never the number to cite as real performance.")
    parser.add_argument("--limit", type=int, default=None, help="Only evaluate the first N clips in the chosen split (smoke test)")
    args = parser.parse_args()

    roots = [r for r in [args.rwf_root, args.scvd_root] if r is not None]
    if not roots:
        raise SystemExit("Provide at least one of --rwf-root or --scvd-root")

    output_dir = args.output_dir or f"eval_report_{args.split}"
    run_report(roots, args.device, output_dir, split=args.split, limit=args.limit)
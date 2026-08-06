"""
calibrate_threshold.py -- sweeps VIOLENCE_CONFIDENCE_THRESHOLD and
VIOLENCE_CONSECUTIVE_REQUIRED against the SAME true held-out split as
test_x3d_true_heldout.py, and prints accuracy/recall/precision/FPR for
every combination so you can pick the point that actually maximizes
your target metric instead of guessing.

USAGE
    python calibrate_threshold.py --device 0
"""

import csv
import itertools
import time
from pathlib import Path
from collections import defaultdict

import cv2
from ultralytics import YOLO

import x3d_violence_detector as x3d_mod
from x3d_violence_detector import X3DViolenceDetector, CLIP_FRAMES
from test_x3d_true_heldout import (
    gather_all_clips_EXACT_TRAINING_LOGIC,
    POSE_IMGSZ, POSE_MODEL_PATH,
    DEFAULT_RWF_ROOT, DEFAULT_SCVD_ROOT,
)

OUTPUT_CSV = "calibration_results.csv"

THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55]
CONSECUTIVE_OPTIONS = [1, 2, 3]


def evaluate_clip_raw(video_path, pose_model, x3d_detector, device):
    """Collects raw per-check confidences for a clip WITHOUT applying
    threshold/hysteresis, so we can re-score them for every sweep combo
    without re-running the model."""
    cap = cv2.VideoCapture(str(video_path))
    frame_count = 0
    track_confidences = defaultdict(list)  # tid -> list of raw confs in order

    x3d_detector.reset_all_tracks()

    # persist=True keeps the pose tracker's per-track state alive across
    # track() calls -- correct within a clip, but this runs once per clip
    # across many clips in one process. Reset per-clip to avoid the state
    # accumulating forever and eventually exhausting VRAM.
    if getattr(pose_model, "predictor", None) is not None:
        for tracker in pose_model.predictor.trackers:
            tracker.reset()

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        try:
            pose_res = pose_model.track(frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, device=device)
        except Exception as e:
            print(f"  [WARN] pose_model.track() raised on frame {frame_count} of {video_path.name}: {e}")
            continue
        if not (pose_res[0].boxes is not None and pose_res[0].boxes.id is not None):
            continue
        ids = pose_res[0].boxes.id.int().cpu().tolist()
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()
        for tid, p_box in zip(ids, boxes):
            if tid not in x3d_detector._frame_buffers:
                x3d_detector._frame_buffers[tid] = __import__("collections").deque(maxlen=x3d_mod.BUFFER_SPAN)
                x3d_detector._last_check_frame[tid] = -x3d_mod.X3D_CHECK_INTERVAL

            cropped = x3d_detector._crop_person(frame, p_box, all_boxes=boxes, tid=tid)
            x3d_detector._frame_buffers[tid].append(cropped)

            # Mirrors the live detector's gating (see x3d_violence_detector.py) --
            # ready once MIN_BUFFER_FOR_INFERENCE frames are buffered, not only
            # once the full BUFFER_SPAN is reached. Keeping this in sync matters:
            # calibrating thresholds against a gate the live pipeline doesn't
            # actually use would pick numbers tuned for different data.
            buffer_ready = len(x3d_detector._frame_buffers[tid]) >= x3d_mod.MIN_BUFFER_FOR_INFERENCE
            due = (frame_count - x3d_detector._last_check_frame[tid]) >= x3d_mod.X3D_CHECK_INTERVAL
            if buffer_ready and due:
                x3d_detector._last_check_frame[tid] = frame_count
                _, raw_conf = x3d_detector._run_inference(x3d_detector._frame_buffers[tid], tid=tid)
                track_confidences[tid].append(raw_conf)

    cap.release()
    return track_confidences


def score_combo(all_clip_traces, threshold, consecutive_required):
    tp = fp = tn = fn = 0
    for ground_truth_violent, track_confidences in all_clip_traces:
        clip_detected = False
        for tid, confs in track_confidences.items():
            streak = 0
            for c in confs:
                if c >= threshold:
                    streak += 1
                else:
                    streak = 0
                if streak >= consecutive_required:
                    clip_detected = True
                    break
            if clip_detected:
                break
        if ground_truth_violent and clip_detected:
            tp += 1
        elif ground_truth_violent and not clip_detected:
            fn += 1
        elif not ground_truth_violent and clip_detected:
            fp += 1
        else:
            tn += 1
    total = tp + fp + tn + fn
    acc = (tp + tn) / total * 100 if total else 0
    recall = tp / (tp + fn) * 100 if (tp + fn) else 0
    precision = tp / (tp + fp) * 100 if (tp + fp) else 0
    fpr = fp / (fp + tn) * 100 if (fp + tn) else 0
    return acc, recall, precision, fpr


def main(device: str):
    roots = [DEFAULT_RWF_ROOT, DEFAULT_SCVD_ROOT]
    print("Gathering held-out split...")
    all_clips = gather_all_clips_EXACT_TRAINING_LOGIC(roots)
    split_idx = int(len(all_clips) * 0.85)
    val_clips = all_clips[split_idx:]
    print(f"Held-out clips: {len(val_clips)}")

    print(f"Loading pose model on {device}...")
    pose_model = YOLO(POSE_MODEL_PATH)
    print("Loading X3D-XS detector (raw pass, one-time)...")
    x3d_detector = X3DViolenceDetector(device=device)

    all_clip_traces = []
    for i, (video_path, ground_truth_violent) in enumerate(val_clips):
        confs = evaluate_clip_raw(video_path, pose_model, x3d_detector, device)
        all_clip_traces.append((ground_truth_violent, confs))
        print(f"[{i+1}/{len(val_clips)}] {video_path.name} -- raw confidences collected")

    print("\n" + "=" * 90)
    print(f"{'threshold':>9} {'consec':>7} {'acc%':>7} {'recall%':>9} {'prec%':>8} {'fpr%':>7}")
    print("=" * 90)
    best = None
    sweep_rows = []
    for threshold, consec in itertools.product(THRESHOLDS, CONSECUTIVE_OPTIONS):
        acc, recall, precision, fpr = score_combo(all_clip_traces, threshold, consec)
        print(f"{threshold:>9.2f} {consec:>7d} {acc:>7.1f} {recall:>9.1f} {precision:>8.1f} {fpr:>7.1f}")
        sweep_rows.append({
            "threshold": threshold, "consecutive_required": consec,
            "accuracy_pct": round(acc, 2), "recall_pct": round(recall, 2),
            "precision_pct": round(precision, 2), "fpr_pct": round(fpr, 2),
        })
        # simple scoring heuristic: maximize acc - fpr penalty
        score = acc - fpr * 0.5
        if best is None or score > best[0]:
            best = (score, threshold, consec, acc, recall, precision, fpr)

    print("\nBEST COMBO (by acc - 0.5*fpr heuristic):")
    _, threshold, consec, acc, recall, precision, fpr = best
    print(f"  threshold={threshold}  consecutive_required={consec}")
    print(f"  acc={acc:.1f}%  recall={recall:.1f}%  precision={precision:.1f}%  fpr={fpr:.1f}%")
    print(f"\nUpdate detection.violence.confidence_threshold and")
    print(f"detection.violence.consecutive_required in config.json to these")
    print(f"values, then re-run test_x3d_true_heldout.py to confirm the final number.")

    # Persist the sweep so the recommended operating point isn't lost the
    # moment this terminal closes -- previously this script only printed to
    # stdout, so nothing on disk showed what a past sweep actually found.
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(sweep_rows[0].keys()) + ["is_best"])
        writer.writeheader()
        for row in sweep_rows:
            is_best = (row["threshold"] == threshold and row["consecutive_required"] == consec)
            writer.writerow({**row, "is_best": is_best})
    print(f"\nFull sweep written to {OUTPUT_CSV} (run at {time.strftime('%Y-%m-%d %H:%M:%S')}).")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=str, default="0")
    args = parser.parse_args()
    main(args.device)
"""
EcoVision -- End-to-End Live Pipeline Test (X3D-XS through the REAL path)
==================================================================
This is different from train_x3d_full.py's validation split. That test
checked the model on clean, pre-extracted clips. This test runs the
SAME crop+buffer+threshold pipeline that main.py actually uses live --
person bbox cropping, rolling buffer, X3D_CHECK_INTERVAL gating -- against
real video files with known ground truth, using your pose model exactly
as main.py does.

This is the test that tells you whether the INTEGRATION is good, not
just whether the model itself is good in isolation.

REQUIREMENTS
    pip install ultralytics torch torchvision pytorchvideo opencv-python numpy

HOW TO USE
    python test_x3d_live_pipeline.py --root "PATH_TO_RWF2000" --limit 50
"""

import argparse
import csv
from pathlib import Path
from collections import defaultdict

import cv2
import numpy as np
from ultralytics import YOLO

from x3d_violence_detector import X3DViolenceDetector

POSE_IMGSZ = 416
POSE_MODEL_PATH = "yolo11s-pose.pt"

FIGHT_KEYWORDS = ["fight", "violence", "violent", "weaponized"]
NONFIGHT_KEYWORDS = ["nonfight", "non-fight", "normal"]
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v")

OUTPUT_CSV = "x3d_live_pipeline_results.csv"


def classify_folder(name: str) -> str:
    name_clean = name.lower().replace(" ", "").replace("_", "").replace("-", "")
    for kw in NONFIGHT_KEYWORDS:
        if kw.replace(" ", "").replace("_", "").replace("-", "") in name_clean:
            return "nonfight"
    for kw in FIGHT_KEYWORDS:
        if kw.replace(" ", "").replace("_", "").replace("-", "") in name_clean:
            return "fight"
    return "unclassified"


def evaluate_clip(video_path: Path, pose_model, x3d_detector, device: str) -> dict:
    """
    Mirrors main.py's actual loop structure: pose tracking -> per-person
    X3D-XS update via the real rolling buffer -- not a shortcut, the
    actual deployed code path, frame by frame.
    """
    cap = cv2.VideoCapture(str(video_path))
    frame_count = 0
    any_violence_detected = False
    max_confidence_seen = 0.0

    x3d_detector._frame_buffers.clear()
    x3d_detector._last_check_frame.clear()
    x3d_detector._cached_result.clear()

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1

        try:
            pose_res = pose_model.track(frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, device=device)
        except Exception:
            continue

        if not (pose_res[0].boxes is not None and pose_res[0].boxes.id is not None):
            continue

        ids = pose_res[0].boxes.id.int().cpu().tolist()
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()

        for tid, p_box in zip(ids, boxes):
            is_violent, conf = x3d_detector.update(tid, frame, p_box, frame_count)
            max_confidence_seen = max(max_confidence_seen, conf)
            if is_violent:
                any_violence_detected = True

    cap.release()
    return {
        "detected": any_violence_detected,
        "max_confidence": max_confidence_seen,
        "frame_count": frame_count,
    }


def run_test(roots: list, limit: int, device: str):
    print(f"Loading pose model on {device}...")
    pose_model = YOLO(POSE_MODEL_PATH)

    print("Loading X3D-XS detector...")
    x3d_detector = X3DViolenceDetector(device=device)

    video_dirs = []
    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            print(f"WARNING: root not found, skipping: {root_path}")
            continue
        for path in root_path.rglob("*"):
            if path.is_dir():
                try:
                    has_videos = any(f.suffix.lower() in VIDEO_EXTENSIONS for f in path.iterdir() if f.is_file())
                except PermissionError:
                    has_videos = False
                if has_videos:
                    video_dirs.append(path)

    results = []
    confusion = defaultdict(int)
    clip_counter = 0

    # Separate into fight/nonfight folders, then interleave them so a
    # --limit cutoff doesn't bias toward whichever class came first
    # alphabetically (this is exactly what caused the all-violent sample
    # last time).
    fight_dirs = [d for d in video_dirs if classify_folder(d.name) == "fight"]
    nonfight_dirs = [d for d in video_dirs if classify_folder(d.name) == "nonfight"]

    fight_files = []
    for d in fight_dirs:
        fight_files.extend([(d, f) for f in d.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS])
    nonfight_files = []
    for d in nonfight_dirs:
        nonfight_files.extend([(d, f) for f in d.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS])

    import random
    random.seed(42)
    random.shuffle(fight_files)
    random.shuffle(nonfight_files)

    # Balance: cap both lists to the same size so the test set is 50/50,
    # same principle as train_x3d_full.py's balancing
    min_count = min(len(fight_files), len(nonfight_files))
    if limit is not None:
        per_class_limit = min(min_count, limit // 2)
    else:
        per_class_limit = min_count

    fight_files = fight_files[:per_class_limit]
    nonfight_files = nonfight_files[:per_class_limit]

    all_files = [(d, f, True) for d, f in fight_files] + [(d, f, False) for d, f in nonfight_files]
    random.shuffle(all_files)

    print(f"Testing {len(all_files)} clips total ({per_class_limit} violent + {per_class_limit} normal)")

    for video_dir, video_path, ground_truth_violent in all_files:
        clip_counter += 1
        result = evaluate_clip(video_path, pose_model, x3d_detector, device)
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

        results.append({
            "file": video_path.name,
            "ground_truth": "violent" if ground_truth_violent else "normal",
            "predicted": "violent" if predicted_violent else "normal",
            "outcome": outcome,
            "max_confidence": round(result["max_confidence"], 3),
            "frame_count": result["frame_count"],
        })
        print(f"[{clip_counter}/{len(all_files)}] {video_path.name}: "
              f"truth={'violent' if ground_truth_violent else 'normal'} "
              f"pred={'violent' if predicted_violent else 'normal'} ({outcome}) "
              f"max_conf={result['max_confidence']:.2f}")

    if results:
        with open(OUTPUT_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

    tp, fp, tn, fn = confusion["TP"], confusion["FP"], confusion["TN"], confusion["FN"]
    total = tp + fp + tn + fn
    print("\n" + "=" * 78)
    print(f"LIVE PIPELINE RESULTS ({total} clips)")
    print("=" * 78)
    print(f"TP={tp}  FN={fn}  TN={tn}  FP={fp}")
    if total > 0:
        print(f"Accuracy: {(tp+tn)/total*100:.1f}%")
    if tp + fn > 0:
        print(f"Recall: {tp/(tp+fn)*100:.1f}%")
    if tp + fp > 0:
        print(f"Precision: {tp/(tp+fp)*100:.1f}%")
    print(f"\nFull results saved to: {OUTPUT_CSV}")
    print("\nCompare these numbers against the OFFLINE validation (83.6%).")
    print("A large gap here means the LIVE pipeline (cropping, buffering,")
    print("check interval) is losing accuracy that the offline test didn't show.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rwf-root", type=str, default=None)
    parser.add_argument("--scvd-root", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Total clips (split 50/50). Omit for ALL available, balanced.")
    parser.add_argument("--device", type=str, default="0")
    args = parser.parse_args()

    roots = [r for r in [args.rwf_root, args.scvd_root] if r is not None]
    if not roots:
        raise SystemExit("Provide at least one of --rwf-root or --scvd-root")

    run_test(roots, args.limit, args.device)
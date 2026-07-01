"""
EcoVision -- True Held-Out Validation Test
==================================================================
train_x3d_full.py used random.seed(42) to shuffle and split 5,124
clips into 85% train (4,355) / 15% val (769). This script recreates
THAT EXACT split using the identical logic, isolates the 769 val-only
clips the model never trained on, and runs them through the REAL live
pipeline (test_x3d_live_pipeline.py's evaluate_clip) for a genuine
generalization measurement.

This is the number that actually matters for your thesis -- accuracy
on data the model has truly never seen, run through the real deployed
code path, not the training script's clean offline loader.

HOW TO USE
    python test_x3d_true_heldout.py --rwf-root "PATH" --scvd-root "PATH" --device 0
"""

import argparse
import random
import csv
from pathlib import Path
from collections import defaultdict

import cv2
from ultralytics import YOLO

from x3d_violence_detector import X3DViolenceDetector

POSE_IMGSZ = 416
POSE_MODEL_PATH = "yolo11s-pose.pt"
VIDEO_EXTENSIONS = (".mp4", ".avi", ".mov", ".mkv", ".m4v")
FIGHT_KEYWORDS = ["fight", "violence", "violent", "weaponized"]
NONFIGHT_KEYWORDS = ["nonfight", "non-fight", "normal"]

OUTPUT_CSV = "x3d_true_heldout_results.csv"


def classify_folder(name: str) -> str:
    name_clean = name.lower().replace(" ", "").replace("_", "").replace("-", "")
    for kw in NONFIGHT_KEYWORDS:
        if kw.replace(" ", "").replace("_", "").replace("-", "") in name_clean:
            return "nonfight"
    for kw in FIGHT_KEYWORDS:
        if kw.replace(" ", "").replace("_", "").replace("-", "") in name_clean:
            return "fight"
    return "unclassified"


def gather_all_clips_EXACT_TRAINING_LOGIC(roots: list) -> list:
    """
    MUST mirror train_x3d_full.py's gather_all_clips() EXACTLY -- same
    folder walk order, same shuffle seed, same balancing -- so the
    resulting split lines up with what the model actually trained on.
    """
    fight_clips, nonfight_clips = [], []

    for root in roots:
        root_path = Path(root)
        if not root_path.exists():
            print(f"WARNING: root not found, skipping: {root_path}")
            continue
        for path in root_path.rglob("*"):
            if not path.is_dir():
                continue
            label = classify_folder(path.name)
            if label == "unclassified":
                continue
            for video_path in path.iterdir():
                if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if label == "fight":
                    fight_clips.append(video_path)
                else:
                    nonfight_clips.append(video_path)

    random.seed(42)
    random.shuffle(fight_clips)
    random.shuffle(nonfight_clips)

    min_count = min(len(fight_clips), len(nonfight_clips))
    fight_clips = fight_clips[:min_count]
    nonfight_clips = nonfight_clips[:min_count]

    clips = [(p, 1) for p in fight_clips] + [(p, 0) for p in nonfight_clips]
    random.shuffle(clips)
    return clips


def evaluate_clip(video_path: Path, pose_model, x3d_detector, device: str) -> dict:
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
            # FIXED: Added all_boxes=boxes argument configuration constraint sync
            is_violent, conf = x3d_detector.update(tid, frame, p_box, frame_count, all_boxes=boxes)
            max_confidence_seen = max(max_confidence_seen, conf)
            if is_violent:
                any_violence_detected = True

    cap.release()
    return {"detected": any_violence_detected, "max_confidence": max_confidence_seen, "frame_count": frame_count}


def run_test(roots: list, device: str):
    print("Recreating EXACT train/val split from train_x3d_full.py (seed=42)...")
    all_clips = gather_all_clips_EXACT_TRAINING_LOGIC(roots)

    split_idx = int(len(all_clips) * 0.85)
    train_clips = all_clips[:split_idx]
    val_clips = all_clips[split_idx:]   # <-- THESE are the ones the model never trained on

    print(f"Total clips: {len(all_clips)}")
    print(f"Train (excluded from this test): {len(train_clips)}")
    print(f"TRUE HELD-OUT validation set (testing these): {len(val_clips)}")

    val_fight = sum(1 for _, l in val_clips if l == 1)
    val_normal = sum(1 for _, l in val_clips if l == 0)
    print(f"  -> {val_fight} violent, {val_normal} normal")

    print(f"\nLoading pose model on {device}...")
    pose_model = YOLO(POSE_MODEL_PATH)
    
    print("Loading X3D-XS detector...")
    # FIXED: Re-engineered device allocation map context to satisfy PyTorch runtime signatures
    x3d_device = f"cuda:{device}" if device.isdigit() else device
    x3d_detector = X3DViolenceDetector(device=x3d_device)

    results = []
    confusion = defaultdict(int)

    for i, (video_path, ground_truth_violent) in enumerate(val_clips):
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
        print(f"[{i+1}/{len(val_clips)}] {video_path.name}: "
              f"truth={'violent' if ground_truth_violent else 'normal'} "
              f"pred={'violent' if predicted_violent else 'normal'} ({outcome}) "
              f"conf={result['max_confidence']:.2f}")

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    tp, fp, tn, fn = confusion["TP"], confusion["FP"], confusion["TN"], confusion["FN"]
    total = tp + fp + tn + fn
    print("\n" + "=" * 78)
    print(f"TRUE HELD-OUT RESULTS ({total} clips the model NEVER trained on)")
    print("=" * 78)
    print(f"TP={tp}  FN={fn}  TN={tn}  FP={fp}")
    if total > 0:
        print(f"Accuracy: {(tp+tn)/total*100:.1f}%")
    if tp + fn > 0:
        print(f"Recall: {tp/(tp+fn)*100:.1f}%")
    if tp + fp > 0:
        print(f"Precision: {tp/(tp+fp)*100:.1f}%")
    if fp + tn > 0:
        print(f"False Positive Rate: {fp/(fp+tn)*100:.1f}%")
    print(f"\nThis is the number to cite as your model's true generalization")
    print(f"performance through the live deployed pipeline.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rwf-root", type=str, default=None)
    parser.add_argument("--scvd-root", type=str, default=None)
    parser.add_argument("--device", type=str, default="0")
    args = parser.parse_args()
    roots = [r for r in [args.rwf_root, args.scvd_root] if r is not None]
    if not roots:
        raise SystemExit("Provide at least one of --rwf-root or --scvd-root")
    run_test(roots, args.device)
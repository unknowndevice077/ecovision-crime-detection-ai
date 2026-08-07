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

# Fixed dataset locations -- always here, no need to pass --rwf-root/--scvd-root.
DEFAULT_RWF_ROOT = r"D:\EcoVisionImagesTraining\To_Be_Trained2\archive"
DEFAULT_SCVD_ROOT = r"D:\EcoVisionImagesTraining\To_Be_Trained2\SCVD"


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


def evaluate_clip(video_path: Path, pose_model, x3d_detector, device: str,
                  allow_flush: bool = True) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    frame_count = 0
    frames_with_pose = 0
    pose_exceptions = 0
    any_violence_detected = False
    max_confidence_seen = 0.0

    x3d_detector.reset_all_tracks()

    # persist=True keeps the pose tracker's per-track state (Kalman filters,
    # feature history, ID space) alive across track() calls -- correct WITHIN
    # a clip, but this function is called once per clip for 700+ clips in the
    # same process. Without an explicit reset, that state just accumulates
    # forever and eventually exhausts VRAM (confirmed: OOM after ~336 clips).
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
            # Logged (not silently swallowed) so "pose never found a person"
            # and "pose_model.track() kept throwing" are distinguishable --
            # previously both looked identical (frames_with_pose == 0).
            pose_exceptions += 1
            print(f"  [WARN] pose_model.track() raised on frame {frame_count} of {video_path.name}: {e}")
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

    cap.release()

    # Flush -- force one real inference on any track that NEVER got a real
    # inference during the clip (get_inference_count == 0 is unambiguous now,
    # driven by a counter incremented ONLY inside _run_inference itself, not
    # inferred from dict state changes -- that comparison was the actual bug
    # that made every previous run's force-flush silently never fire).
    #
    # IMPORTANT -- this flush makes the eval MORE PERMISSIVE than deployment.
    # force_inference() only requires len(buf) >= 5, while the live pipeline's
    # sole path to inference is `buffer_ready = len(buf) >= MIN_BUFFER_FOR_INFERENCE`
    # (=20) in x3d_violence_detector.update(). main.py never calls
    # force_inference() at all -- only this script and generate_eval_report.py do.
    #
    # On the last run 518 of the 637 scored clips (81%) reached X3D ONLY via
    # this flush, so the headline number largely measures a 5-frame gate that
    # is not the one that ships. Pass --no-flush to measure the deployed
    # configuration instead. Default stays True so numbers remain comparable
    # with every prior row in eval_history.csv.
    forced_flush = False
    had_any_buffer = len(x3d_detector._frame_buffers) > 0

    if allow_flush:
        for tid in list(x3d_detector._frame_buffers.keys()):
            if x3d_detector.get_inference_count(tid) == 0:
                forced_flush = True
                is_violent, conf = x3d_detector.force_inference(tid)
                max_confidence_seen = max(max_confidence_seen, conf)
                if is_violent:
                    any_violence_detected = True

    # NOTE: this snapshot must be taken AFTER the forced-flush loop above,
    # not before it -- force_inference() calls _run_inference() which DOES
    # increment _real_inference_count, so snapshotting earlier silently
    # undercounted every clip that only got a real inference via flush
    # (this was true for 546/769 clips in the last held-out run).
    total_real_inferences = sum(x3d_detector._real_inference_count.values())

    pose_rate = frames_with_pose / frame_count if frame_count > 0 else 0
    return {
        "detected": any_violence_detected,
        "max_confidence": max_confidence_seen,
        "frame_count": frame_count,
        "frames_with_pose": frames_with_pose,
        "pose_exceptions": pose_exceptions,
        "had_any_buffer": had_any_buffer,
        "forced_flush": forced_flush,
        "pose_detection_rate": round(pose_rate, 3),
        "real_inference_count": total_real_inferences,
    }


def run_test(roots: list, device: str, notes: str = "", allow_flush: bool = True):
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
    x3d_detector = X3DViolenceDetector(device=device)

    results = []
    confusion = defaultdict(int)

    for i, (video_path, ground_truth_violent) in enumerate(val_clips):
        result = evaluate_clip(video_path, pose_model, x3d_detector, device,
                               allow_flush=allow_flush)
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
            "frames_with_pose": result.get("frames_with_pose", "n/a"),
            "pose_exceptions": result.get("pose_exceptions", 0),
            "pose_detection_rate": result.get("pose_detection_rate", "n/a"),
            "real_inference_count": result.get("real_inference_count", "n/a"),
            "had_any_buffer": result.get("had_any_buffer", "n/a"),
            "forced_flush": result.get("forced_flush", "n/a"),
        })
        print(f"[{i+1}/{len(val_clips)}] {video_path.name}: "
              f"truth={'violent' if ground_truth_violent else 'normal'} "
              f"pred={'violent' if predicted_violent else 'normal'} ({outcome}) "
              f"conf={result['max_confidence']:.2f} "
              f"pose_seen={result.get('had_any_buffer')} "
              f"forced={result.get('forced_flush')}")

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

    from eval_history import log_run
    log_run("test_x3d_true_heldout", split="val", tp=tp, fp=fp, tn=tn, fn=fn, notes=notes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rwf-root", type=str, default=DEFAULT_RWF_ROOT)
    parser.add_argument("--scvd-root", type=str, default=DEFAULT_SCVD_ROOT)
    parser.add_argument("--device", type=str, default="0")
    parser.add_argument("--notes", type=str, default="", help="Free-text note saved into eval_history.csv for this run (e.g. 'after buffer-gating fix')")
    parser.add_argument("--no-flush", action="store_true",
                        help="Disable the end-of-clip forced inference, so a clip only "
                             "scores if a track reaches MIN_BUFFER_FOR_INFERENCE frames -- "
                             "i.e. measure the pipeline main.py actually runs. Results are "
                             "NOT comparable with the flush-enabled rows in eval_history.csv.")
    args = parser.parse_args()
    roots = [r for r in [args.rwf_root, args.scvd_root] if r is not None]
    if not roots:
        raise SystemExit("Provide at least one of --rwf-root or --scvd-root")
    if args.no_flush:
        print("--no-flush: end-of-clip forced inference DISABLED (deployed-gate mode)")
    run_test(roots, args.device, notes=args.notes, allow_flush=not args.no_flush)
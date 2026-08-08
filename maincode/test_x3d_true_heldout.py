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
import sys
from pathlib import Path
from collections import defaultdict

import cv2
from ultralytics import YOLO

from x3d_violence_detector import X3DViolenceDetector, SceneViolenceDetector

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


def evaluate_clip_scene(video_path: Path, scene_detector) -> dict:
    """Whole-frame evaluation -- no pose model, no tracking, no buffer gate.

    Every clip gets scored here by construction, which is the entire point:
    the per-track path leaves 132 of 769 clips unscored because no track ID
    survives 20 frames, and 96 of those are normal clips that then bank a
    free TN. Removing the gate makes the FPR honest.
    """
    cap = cv2.VideoCapture(str(video_path))
    scene_detector.reset_scene()

    frame_count = 0
    any_violence = False
    max_conf = 0.0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1
        is_violent, conf = scene_detector.update(frame, frame_count)
        max_conf = max(max_conf, conf)
        if is_violent:
            any_violence = True
    cap.release()

    info = scene_detector.get_scene_debug_info()
    return {
        "detected": any_violence,
        "max_confidence": max_conf,
        "frame_count": frame_count,
        "frames_with_pose": "n/a",
        "pose_exceptions": 0,
        "had_any_buffer": info["buffer_fill"] > 0,
        "forced_flush": False,
        "pose_detection_rate": "n/a",
        "real_inference_count": info["inference_count"],
    }


def run_test(roots: list, device: str, notes: str = "", allow_flush: bool = True,
             scene_mode: bool = False, out_csv: str = OUTPUT_CSV,
             weights: str = None, use_manifest: bool = False,
             manifest_path: str = None, split: str = "val"):
    if use_manifest or manifest_path:
        # One source of truth, shared with training. The seed-42 path below
        # re-derives the split independently and leaks 93 clips (12.1% of the
        # held-out set) that are byte-identical to training clips; the
        # manifest assigns splits from the content hash so that cannot happen.
        sys.path.insert(0, str(Path(DEFAULT_RWF_ROOT).parents[1]))
        from dataset_manifest_loader import get_split, describe
        print("Using dataset manifest (content-hash split, deduplicated):")
        print(describe(manifest_path))
        val_clips = get_split(split, manifest_path)
        print(f"\nHELD-OUT set ({split}): {len(val_clips)} clips")
        if split == "val":
            # Worth saying every run, because the number looks like a held-out
            # score and is not one: train_x3d_full.py saves the best checkpoint
            # by val_acc, so val is the model-SELECTION set. Scoring here
            # reports a figure training already picked the weights to maximise.
            print("WARNING: 'val' is the split training used to choose the best "
                  "checkpoint. This number is selection-optimistic. Use the 3-way "
                  "manifest's 'test' split for an honest one:")
            print("         --manifest-path 3way --split test")
        print("NOTE: not comparable with pre-manifest rows in eval_history.csv "
              "-- different split membership.")
    else:
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

    # None -> whatever config.json's detection.violence.model_path points at.
    model_kw = {"model_path": weights} if weights else {}
    if weights:
        print(f"\nWeights override: {weights}")

    if scene_mode:
        # No pose stage at all -- that is the whole point, and it also makes
        # this run several times faster than the per-track path.
        print("SCENE MODE: whole-frame classification, pose model not loaded.")
        pose_model = None
        x3d_detector = SceneViolenceDetector(device=device, **model_kw)
    else:
        print(f"Loading pose model on {device}...")
        pose_model = YOLO(POSE_MODEL_PATH)
        print("Loading X3D-XS detector...")
        x3d_detector = X3DViolenceDetector(device=device, **model_kw)

    results = []
    confusion = defaultdict(int)

    for i, (video_path, ground_truth_violent) in enumerate(val_clips):
        if scene_mode:
            result = evaluate_clip_scene(video_path, x3d_detector)
        else:
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
            # Full path as well, because basenames are NOT unique: RWF-2000
            # reuses 30 filenames across Fight/ and NonFight/ with different
            # content. Anything that joins results back to the dataset by
            # "file" alone silently mixes those up -- which is exactly what
            # audit_leakage_impact.py and compare_modes.py were doing.
            "path": str(video_path),
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

    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"\nper-clip results -> {out_csv}")

    tp, fp, tn, fn = confusion["TP"], confusion["FP"], confusion["TN"], confusion["FN"]
    total = tp + fp + tn + fn
    # The banner has to depend on which split ran. It previously read
    # "TRUE HELD-OUT ... never trained on" and "the number to cite"
    # unconditionally, including when scoring the split that chose the
    # checkpoint -- so the honest and the optimistic case printed the same
    # confident claim, and only the latter was wrong.
    selection_set = (split == "val")
    print("\n" + "=" * 78)
    if selection_set:
        print(f"VALIDATION RESULTS ({total} clips) -- SELECTION-OPTIMISTIC, NOT HELD OUT")
    else:
        print(f"TRUE HELD-OUT RESULTS ({total} clips, split={split!r}, "
              f"never read during training)")
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
    if selection_set:
        print(f"\nDO NOT cite this as generalization performance. Training chose its")
        print(f"best checkpoint by accuracy on these exact clips, so the model was")
        print(f"selected to do well here. For an honest figure, train against the")
        print(f"3-way manifest and score the test split:")
        print(f"    --manifest-path 3way --split test")
    else:
        print(f"\nThis is the number to cite: split={split!r} is read by neither")
        print(f"gradient updates nor checkpoint selection, and it is measured")
        print(f"through the live deployed pipeline.")

    from eval_history import log_run
    log_run("test_x3d_true_heldout", split=split, tp=tp, fp=fp, tn=tn, fn=fn, notes=notes)


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
    parser.add_argument("--scene", action="store_true",
                        help="Whole-frame mode: classify the full frame sequence with no "
                             "pose model, no tracking and no MIN_BUFFER_FOR_INFERENCE gate, "
                             "so all 769 clips get scored. This matches how the baseline "
                             "weights were actually TRAINED (train_x3d_full.py resizes the "
                             "whole frame unless --crop-boxes-path is given).")
    parser.add_argument("--manifest", action="store_true",
                        help="Use dataset_manifest.json (deduplicated, content-hash split, "
                             "shared with training) instead of re-deriving the seed-42 "
                             "shuffle. The old path leaks 93 clips into the held-out set; "
                             "this one cannot. Results are NOT comparable with pre-manifest "
                             "rows in eval_history.csv.")
    parser.add_argument("--manifest-path", type=str, default=None,
                        help="Which manifest to read. Omit for dataset_manifest.json; "
                             "pass '3way' for dataset_manifest_3way.json, or a path. "
                             "Implies --manifest.")
    parser.add_argument("--split", type=str, default="val",
                        choices=["train", "val", "test"],
                        help="Which split to score. 'val' is the set training used to "
                             "pick the best checkpoint, so scoring it is optimistic; "
                             "'test' (3-way manifest only) is never read during training "
                             "and is the only honest option.")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override detection.violence.confidence_threshold for this run "
                             "only (config.json is untouched). The clean threshold sweep put "
                             "the scene-mode optimum near 0.50, vs 0.40 tuned for per-track.")
    parser.add_argument("--consecutive", type=int, default=None,
                        help="Override consecutive_required for this run only. On a 30-frame "
                             "clip at check_interval=15 there are just two inference points, "
                             "so the default of 2 demands a perfect record.")
    parser.add_argument("--weights", type=str, default=None,
                        help="Path to an X3D .pt to evaluate. Defaults to config.json's "
                             "detection.violence.model_path. Use this to compare the "
                             "whole-frame-trained baseline against the crop-trained model.")
    parser.add_argument("--out-csv", type=str, default=OUTPUT_CSV,
                        help="Where to write per-clip results. Override it when comparing "
                             "modes so a scene run does not clobber the per-track CSV.")
    args = parser.parse_args()
    roots = [r for r in [args.rwf_root, args.scvd_root] if r is not None]
    if not roots:
        raise SystemExit("Provide at least one of --rwf-root or --scvd-root")
    if args.no_flush and args.scene:
        raise SystemExit("--no-flush is meaningless with --scene: scene mode has no "
                         "per-track buffer gate and no end-of-clip flush.")
    if args.no_flush:
        print("--no-flush: end-of-clip forced inference DISABLED (deployed-gate mode)")

    # Patch the module globals rather than config.json so a tuning sweep can
    # never leave the deployed config in an experimental state. _smooth_and_confirm
    # and _run_inference read these at call time, so this takes effect.
    import x3d_violence_detector as _xvd
    if args.threshold is not None:
        print(f"threshold override: {_xvd.VIOLENCE_CONFIDENCE_THRESHOLD} -> {args.threshold}")
        _xvd.VIOLENCE_CONFIDENCE_THRESHOLD = args.threshold
    if args.consecutive is not None:
        print(f"consecutive_required override: "
              f"{_xvd.VIOLENCE_CONSECUTIVE_REQUIRED} -> {args.consecutive}")
        _xvd.VIOLENCE_CONSECUTIVE_REQUIRED = args.consecutive
    if args.split != "val" and not (args.manifest or args.manifest_path):
        raise SystemExit("--split only applies in manifest mode; add --manifest "
                         "(or --manifest-path 3way).")
    run_test(roots, args.device, notes=args.notes, allow_flush=not args.no_flush,
             scene_mode=args.scene, out_csv=args.out_csv, weights=args.weights,
             use_manifest=args.manifest, manifest_path=args.manifest_path,
             split=args.split)
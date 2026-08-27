"""Per-camera threshold calibration -- docs/progress_report_violence_detection.md
§28.1: one global threshold is necessarily too low for a noisy camera (a
flyover with constant traffic) and too high for the rest of the network. The
measured fix costs no retraining and no second model: record a few quiet
minutes from ONE camera, take the p99.5 of what the detector's raw confidence
actually did on footage with no incident in it, and use that as this camera's
threshold. §28.1's own numbers: per-camera calibration on 3 real cameras cut
network-wide alarms from 46/hr to 20/hr for a few points of recall, paid only
by the camera that actually needed it.

This is a MEASUREMENT tool. It does not write to the database itself -- it
prints the recommended value and the exact API call to apply it (or feeds
--apply straight through, if you pass credentials), the same "measure, then a
human decides" shape as calibrate_threshold.py. consecutive_required is left
at whatever the camera already runs; only the threshold gets calibrated here,
matching how it was actually measured in §28.1.

USAGE
    # A camera's usual quiet footage, saved to a file:
    python tools/calibrate_camera_quiet.py --source quiet_flyover.mp4 --model violence

    # Straight off a live camera for 5 minutes:
    python tools/calibrate_camera_quiet.py --source 0 --model violence --seconds 300

    # Push the result straight to the backend for camera "flyover-01":
    python tools/calibrate_camera_quiet.py --source quiet.mp4 --model violence \\
        --apply --camera-id flyover-01 --api-url http://localhost:8000 \\
        --username devteam --password ...

IMPORTANT: this footage must actually be quiet -- no real incident in it. A
threshold calibrated against footage that happens to contain violence would
teach the camera to ignore exactly what it exists to detect (the same trap
docs/progress_report_violence_detection.md §18 names for the live negative
corpus generally). Watch the clip once before calibrating on it.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(REPO / "maincode"))

from x3d_violence_detector import SceneViolenceDetector  # noqa: E402

# model_key -> (config.json block, threshold key, consecutive key). Violence
# nests these under scene_* because the same block also carries track mode's
# plain confidence_threshold; robbery/vandalism have no track mode.
_MODEL_CFG_KEYS = {
    "violence": ("violence", "scene_model_path", "scene_confidence_threshold", "scene_consecutive_required"),
    "robbery": ("robbery", "model_path", "confidence_threshold", "consecutive_required"),
    "vandalism": ("vandalism", "model_path", "confidence_threshold", "consecutive_required"),
}


def build_detector(model_key: str, device: str) -> SceneViolenceDetector:
    cfg_key, path_key, thresh_key, consec_key = _MODEL_CFG_KEYS[model_key]
    block = CONFIG["detection"][cfg_key]
    model_path = REPO / block[path_key]
    if not model_path.exists():
        raise SystemExit(f"Weights not found: {model_path} -- is {model_key} deployed on this machine?")
    detector = SceneViolenceDetector(
        model_path=str(model_path), device=device,
        threshold=float(block[thresh_key]), consecutive=int(block[consec_key]),
    )
    print(f"[calibrate] {model_key}: {model_path.name}  "
          f"(currently deployed at threshold={detector.threshold} consecutive={detector.consecutive})")
    return detector


def collect_raw_confidences(detector: SceneViolenceDetector, source, seconds: float | None) -> list:
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"Could not open source: {source!r}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    max_frames = int(seconds * fps) if seconds else None

    raw = []
    frame_count = 0
    t0 = time.time()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_count += 1
        prev_last_check = detector._scene_last_check
        _, raw_conf = detector.update(frame, frame_count)
        # update() returns the CACHED result between check-interval
        # boundaries -- only record a value when a real inference actually
        # ran this frame, or the same raw_conf gets counted many times and
        # the percentile is measuring the check interval, not the camera.
        if detector._scene_last_check != prev_last_check:
            raw.append(raw_conf)
        if max_frames and frame_count >= max_frames:
            break
        if frame_count % (int(fps) * 30) == 0:
            print(f"[calibrate]   {frame_count/fps:.0f}s processed, "
                  f"{len(raw)} inferences so far, running max={max(raw) if raw else 0:.3f}")
    cap.release()
    elapsed = time.time() - t0
    print(f"[calibrate] done: {frame_count} frames ({frame_count/fps:.0f}s of footage) "
          f"in {elapsed:.0f}s wall time, {len(raw)} real inferences collected")
    return raw


def recommend(raw: list, percentile: float, floor: float, ceiling: float) -> float:
    if len(raw) < 20:
        print(f"[calibrate] WARNING: only {len(raw)} inference points -- this is a thin sample "
              f"for a percentile. §28.1 used continuous footage on the order of tens of minutes.")
    value = float(np.percentile(raw, percentile))
    clamped = max(floor, min(ceiling, value))
    if clamped != value:
        print(f"[calibrate] p{percentile} came out to {value:.3f}, clamped to the "
              f"[{floor}, {ceiling}] sanity range the backend also enforces.")
    return round(clamped, 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="Video file path, or an integer camera index")
    ap.add_argument("--model", required=True, choices=list(_MODEL_CFG_KEYS.keys()))
    ap.add_argument("--seconds", type=float, default=None, help="Stop after this many seconds of footage (default: whole file)")
    ap.add_argument("--percentile", type=float, default=99.5, help="Percentile of quiet-footage confidence to use as the threshold (default: 99.5, per §28.1)")
    ap.add_argument("--device", default="0")
    ap.add_argument("--floor", type=float, default=0.05)
    ap.add_argument("--ceiling", type=float, default=0.95)
    ap.add_argument("--apply", action="store_true", help="PATCH the recommendation straight to the backend")
    ap.add_argument("--camera-id")
    ap.add_argument("--api-url", default=CONFIG.get("networking", {}).get("api_url", "http://localhost:8000"))
    ap.add_argument("--username")
    ap.add_argument("--password")
    args = ap.parse_args()

    if args.apply and not args.camera_id:
        raise SystemExit("--apply requires --camera-id")

    source = int(args.source) if args.source.isdigit() else args.source
    detector = build_detector(args.model, args.device)
    raw = collect_raw_confidences(detector, source, args.seconds)
    if not raw:
        raise SystemExit("No inferences collected -- footage too short, or no person/scene content reached a check interval.")

    recommended = recommend(raw, args.percentile, args.floor, args.ceiling)

    print("\n" + "=" * 60)
    print(f"  {args.model}: recommended threshold = {recommended}")
    print(f"  (min={min(raw):.3f}  p50={np.percentile(raw, 50):.3f}  "
          f"p95={np.percentile(raw, 95):.3f}  p99.5={np.percentile(raw, 99.5):.3f}  max={max(raw):.3f})")
    print("=" * 60)

    if args.apply:
        import requests
        token = None
        if args.username and args.password:
            resp = requests.post(f"{args.api_url.rstrip('/')}/api/login",
                                  json={"username": args.username, "password": args.password}, timeout=5.0)
            resp.raise_for_status()
            token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = requests.patch(
            f"{args.api_url.rstrip('/')}/api/cameras/{args.camera_id}/thresholds/{args.model}",
            json={"threshold": recommended, "calibrated_from": "quiet_clip"},
            headers=headers, timeout=10.0,
        )
        if resp.ok:
            print(f"[calibrate] Applied to camera {args.camera_id!r}: {resp.json()}")
        else:
            print(f"[calibrate] Apply failed ({resp.status_code}): {resp.text}")
    else:
        print(f"\nTo apply this from the dashboard: Cameras -> {args.camera_id or '<camera>'} -> "
              f"Models -> {args.model} threshold -> {recommended}")
        print(f"Or via API: PATCH {args.api_url}/api/cameras/<camera_id>/thresholds/{args.model} "
              f'{{"threshold": {recommended}, "calibrated_from": "quiet_clip"}}')


if __name__ == "__main__":
    main()

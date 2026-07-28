"""
EcoVision -- ALL-DETECTIONS Visualizer (pose + X3D violence + weapon/sign + robbery-pair)
==================================================================
Same idea as visualize_x3d_input.py, but shows every layer of the
detection stack at once instead of just X3D:

  - Person tracking boxes (pose model)
  - Per-person X3D violent-motion verdict + live confidence bar
  - Weapon / sign boxes from weapon_signs.pt (gun/knife/etc), frame-level
  - A yellow connecting line between any two people RobberyTracker flags
    as a robbery pair (armed person sustained-proximity to another)

NOTE on "armed": your real production pipeline (main.py) derives
armed_states/violence_states from a track_states state machine that
isn't part of the files shared in this session, so it's not replicated
exactly here. For visualization purposes, a track is treated as "armed"
if a weapon_signs.pt weapon box currently overlaps its person box, and
"assault" if an X3D-violent verdict is currently active for that track.
This is close enough to sanity-check RobberyTracker's proximity/duration
logic visually, but is NOT a byte-for-byte reproduction of main.py.

HOW TO USE
    python visualize_all_detections.py --source "path/to/clip.mp4" --device 0 --show
"""

import argparse
import os
import shutil

import cv2
import numpy as np
from ultralytics import YOLO

from x3d_violence_detector import (
    X3DViolenceDetector,
    CLIP_FRAMES,
    FRAME_SIZE,
    BUFFER_SPAN,
    X3D_CHECK_INTERVAL,
    VIOLENCE_CONFIDENCE_THRESHOLD,
)
from robbery_vandalism import RobberyTracker

POSE_IMGSZ = 416
WEAPON_IMGSZ = 416
WEIGHTS_DIR = r"D:\projects\EcoVisionCode\weights"

USE_CUDA = False
try:
    import torch
    USE_CUDA = torch.cuda.is_available()
except Exception:
    USE_CUDA = False


def load_model_with_fallback(engine_name: str, pt_name: str, task: str):
    """Prefer the .engine (TensorRT) file in WEIGHTS_DIR when CUDA is
    available; fall back to the .pt file otherwise. Mirrors main.py's
    load_model_with_fallback so behavior matches production."""
    engine_path = os.path.join(WEIGHTS_DIR, engine_name)
    pt_path = os.path.join(WEIGHTS_DIR, pt_name)

    if USE_CUDA and os.path.exists(engine_path):
        try:
            print(f"Attempting TensorRT engine: {engine_path}")
            model = YOLO(engine_path, task=task)
            _dummy = np.zeros((416, 416, 3), dtype=np.uint8)
            model.predict(_dummy, verbose=False, imgsz=416, device="cuda:0")
            print(f"Loaded engine: {engine_name}")
            return model
        except Exception as e:
            print(f"Engine failed ({engine_name}): {str(e)[:150]}")
            print(f"Falling back to: {pt_name}")

    if not os.path.exists(pt_path):
        print(f"WARNING: {pt_path} not found either.")
    print(f"Loading: {pt_path}")
    return YOLO(pt_path, task=task)

WEAPON_CLASSES   = {"gun", "knife", "pistol", "firearm", "handgun", "rifle"}
VIOLENCE_CLASSES = {"violence", "fight", "assault"}
CONF_BY_CLASS = {
    "gun": 0.52, "pistol": 0.52, "firearm": 0.52, "handgun": 0.52, "rifle": 0.52,
    "knife": 0.45, "violence": 0.40, "fight": 0.40, "assault": 0.40, "sign": 0.40,
}
WEAPON_CONF_DEFAULT = 0.38

OUT_DIR = "x3d_debug_out_all"
INSET_SIZE = 160          # matches FRAME_SIZE so you see the crop at native res
BAR_WIDTH = 160
BAR_HEIGHT = 14


def boxes_overlap(a, b) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    return ix2 > ix1 and iy2 > iy1


def draw_confidence_bar(canvas, x, y, confidence, is_violent):
    color = (0, 0, 255) if is_violent else (0, 200, 0)
    cv2.rectangle(canvas, (x, y), (x + BAR_WIDTH, y + BAR_HEIGHT), (40, 40, 40), -1)
    fill_w = int(BAR_WIDTH * min(confidence, 1.0))
    cv2.rectangle(canvas, (x, y), (x + fill_w, y + BAR_HEIGHT), color, -1)
    cv2.rectangle(canvas, (x, y), (x + BAR_WIDTH, y + BAR_HEIGHT), (255, 255, 255), 1)
    cv2.line(
        canvas,
        (x + int(BAR_WIDTH * VIOLENCE_CONFIDENCE_THRESHOLD), y - 3),
        (x + int(BAR_WIDTH * VIOLENCE_CONFIDENCE_THRESHOLD), y + BAR_HEIGHT + 3),
        (0, 255, 255),
        2,
    )  # yellow tick = current threshold


def save_inference_montage(tid, frame_idx, sampled_frames, is_violent, confidence, out_dir):
    """Stitches the actual CLIP_FRAMES frames fed into this real inference
    into one strip image -- literally what the tensor contained."""
    strip = np.hstack(sampled_frames)  # each frame already FRAME_SIZE x FRAME_SIZE
    banner_h = 30
    banner = np.zeros((banner_h, strip.shape[1], 3), dtype=np.uint8)
    verdict = "VIOLENT" if is_violent else "normal"
    color = (0, 0, 255) if is_violent else (0, 200, 0)
    cv2.putText(banner, f"track {tid} @ frame {frame_idx}  conf={confidence:.3f}  -> {verdict}",
                (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    combined = np.vstack([banner, strip])
    path = os.path.join(out_dir, f"frame{frame_idx:06d}_track{tid}_{verdict}.jpg")
    cv2.imwrite(path, combined)
    return path


def run(source, device: str, show: bool = False):
    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    cap = cv2.VideoCapture(source if not str(source).isdigit() else int(source))
    if not cap.isOpened():
        raise SystemExit(f"Could not open source: {source}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"Loading pose model on {device}...")
    pose_model = load_model_with_fallback("yolo11s-pose.engine", "yolo11s-pose.pt", task="pose")
    print("Loading X3D-XS detector...")
    x3d_model_path = os.path.join(WEIGHTS_DIR, "x3d_xs_violence_best.pt")
    x3d_detector = X3DViolenceDetector(model_path=x3d_model_path, device=device)
    print("Loading weapon/sign model...")
    weapon_model = load_model_with_fallback("weapon_signs.engine", "weapon_signs.pt", task="detect")
    robbery_tracker = RobberyTracker()

    # Patch _run_inference so we can also dump the montage at the exact
    # moment a REAL inference happens, without duplicating its logic.
    original_run_inference = x3d_detector._run_inference

    def traced_run_inference(frames_deque, tid=None):
        all_frames = list(frames_deque)
        if len(all_frames) < CLIP_FRAMES:
            all_frames = all_frames + [all_frames[-1]] * (CLIP_FRAMES - len(all_frames))
        indices = np.linspace(0, len(all_frames) - 1, CLIP_FRAMES).astype(int)
        sampled = [all_frames[i] for i in indices]

        is_violent, conf = original_run_inference(frames_deque, tid=tid)
        save_inference_montage(tid, traced_run_inference.frame_idx, sampled, is_violent, conf, OUT_DIR)
        return is_violent, conf

    traced_run_inference.frame_idx = 0
    x3d_detector._run_inference = traced_run_inference

    out_path = os.path.join(OUT_DIR, "annotated.mp4")
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    frame_count = 0
    is_webcam = str(source) == "0" or (isinstance(source, int) and source == 0)
    if is_webcam:
        print("Webcam source: press 'q' in the preview window to stop (Ctrl+C will corrupt the output video).")
    print("Running... this writes annotated.mp4 and per-inference montage JPGs as it goes.")
    if show:
        cv2.namedWindow("X3D live view", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("X3D live view", w, h)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            traced_run_inference.frame_idx = frame_count
            canvas = frame.copy()

            try:
                pose_res = pose_model.track(frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, device=device)
            except Exception:
                writer.write(canvas)
                if show:
                    cv2.imshow("X3D live view", canvas)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                continue

            if pose_res[0].boxes is not None and pose_res[0].boxes.id is not None:
                ids = pose_res[0].boxes.id.int().cpu().tolist()
                boxes = pose_res[0].boxes.xyxy.cpu().numpy()

                # -- Weapon / sign detection (frame-level, not per-person) --
                weapon_res = weapon_model.predict(frame, verbose=False, imgsz=WEAPON_IMGSZ,
                                                   conf=WEAPON_CONF_DEFAULT)
                weapon_boxes = []
                if weapon_res[0].boxes:
                    for wb in weapon_res[0].boxes:
                        cls_name = weapon_res[0].names[int(wb.cls)].lower().strip()
                        conf = float(wb.conf[0].cpu())
                        required = CONF_BY_CLASS.get(cls_name, WEAPON_CONF_DEFAULT)
                        if conf < required:
                            continue
                        wxyxy = wb.xyxy[0].cpu().numpy().astype(int)
                        is_weapon = cls_name in WEAPON_CLASSES
                        color = (0, 0, 255) if is_weapon else (0, 140, 255)
                        cv2.rectangle(canvas, tuple(wxyxy[:2]), tuple(wxyxy[2:]), color, 2)
                        cv2.putText(canvas, f"{cls_name} {conf:.2f}", (wxyxy[0], max(0, wxyxy[1] - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
                        weapon_boxes.append((wxyxy, cls_name))

                armed_states = {}
                violence_states = {}

                for i, (tid, p_box) in enumerate(zip(ids, boxes)):
                    is_violent, conf = x3d_detector.update(tid, frame, p_box, frame_count, all_boxes=boxes)
                    violence_states[tid] = is_violent

                    # Approximate "armed": a weapon-class box overlaps this person's box.
                    armed_states[tid] = any(
                        cls_name in WEAPON_CLASSES and boxes_overlap(p_box, wbox)
                        for wbox, cls_name in weapon_boxes
                    )

                    x1, y1, x2, y2 = [int(v) for v in p_box]
                    box_color = (0, 0, 255) if is_violent else (
                        (0, 165, 255) if armed_states[tid] else (0, 200, 0)
                    )
                    label = f"id{tid} {conf:.2f}"
                    if armed_states[tid]:
                        label += " ARMED"
                    cv2.rectangle(canvas, (x1, y1), (x2, y2), box_color, 2)
                    cv2.putText(canvas, label, (x1, max(0, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2, cv2.LINE_AA)

                    # Picture-in-picture: the literal crop being fed to X3D right now
                    crop = x3d_detector.get_latest_live_crop(tid)
                    if crop is not None:
                        inset_x = 10 + i * (INSET_SIZE + 10)
                        inset_y = 10
                        if inset_x + INSET_SIZE < w and inset_y + INSET_SIZE + 40 < h:
                            canvas[inset_y:inset_y + INSET_SIZE, inset_x:inset_x + INSET_SIZE] = crop
                            cv2.rectangle(canvas, (inset_x, inset_y),
                                          (inset_x + INSET_SIZE, inset_y + INSET_SIZE), box_color, 2)
                            cv2.putText(canvas, f"model input (id{tid})", (inset_x, inset_y - 4),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
                            draw_confidence_bar(canvas, inset_x, inset_y + INSET_SIZE + 8, conf, is_violent)

                # -- Robbery pairs: draw a yellow line for any pair currently in "ROBBERY" state --
                pair_states = robbery_tracker.update(ids, boxes, armed_states, violence_states)
                for (tid_a, tid_b), state in pair_states.items():
                    if state != "ROBBERY":
                        continue
                    if tid_a in ids and tid_b in ids:
                        box_a = boxes[ids.index(tid_a)]
                        box_b = boxes[ids.index(tid_b)]
                        ca = (int((box_a[0] + box_a[2]) / 2), int((box_a[1] + box_a[3]) / 2))
                        cb = (int((box_b[0] + box_b[2]) / 2), int((box_b[1] + box_b[3]) / 2))
                        cv2.line(canvas, ca, cb, (0, 255, 255), 3)
                        mid = ((ca[0] + cb[0]) // 2, (ca[1] + cb[1]) // 2)
                        cv2.putText(canvas, "ROBBERY", mid, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                    (0, 255, 255), 2, cv2.LINE_AA)

            writer.write(canvas)
            if show:
                cv2.imshow("X3D live view", canvas)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    print("Stopped by user.")
                    break
            if frame_count % 50 == 0:
                print(f"  frame {frame_count}...")
    finally:
        cap.release()
        writer.release()
        if show:
            cv2.destroyAllWindows()

    print(f"\nDone. Annotated video: {out_path}")
    print(f"Real-inference montages saved alongside it in: {OUT_DIR}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, required=True,
                         help="Path to a video file, or a webcam index like 0")
    parser.add_argument("--device", type=str, default="0")
    parser.add_argument("--show", action="store_true",
                         help="Open a live preview window while it runs (press 'q' to stop)")
    args = parser.parse_args()
    run(args.source, args.device, args.show)
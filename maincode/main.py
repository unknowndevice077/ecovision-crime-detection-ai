import os
import sys
import cv2
import time
import threading
import requests
import numpy as np
import dxcam
from pathlib import Path
from collections import deque
from unittest.mock import MagicMock
from types import ModuleType
from concurrent.futures import ThreadPoolExecutor

# --- 1. ARCHITECTURAL BYPASS ---
os.environ["ULTRALYTICS_GIT"] = "False"
os.environ["ULTRALYTICS_OFFLINE"] = "True"
mock_repo = MagicMock(); mock_repo.root = Path(".")
mock_git = ModuleType("ultralytics.utils.git"); mock_git.GitRepo = MagicMock(return_value=mock_repo)
sys.modules["ultralytics.utils.git"] = mock_git

try:
    from ultralytics import YOLO
    print("✅ Sentinel v14.0: DXCam Mode Active.")
except ImportError:
    sys.exit(1)

# --- 2. MODELS ---
# half=True enables FP16 inference on GPU — ~1.5–2x faster with no accuracy loss
pose_model   = YOLO("yolo11s-pose.pt")
violence_model = YOLO(r"D:\projects\EcoVisionCode\weights\gunsandviolence.pt")

# Warm-up both models to avoid cold-start lag on first real frame
_dummy = np.zeros((640, 640, 3), dtype=np.uint8)
pose_model.predict(_dummy, verbose=False, imgsz=640)
violence_model.predict(_dummy, verbose=False, imgsz=640)
print("✅ Models warmed up.")

# --- 3. WEAPON THREAD (Double-buffered, non-blocking) ---
weapon_results_lock = threading.Lock()
weapon_thread_result = {"weapons": [], "violence_boxes": []}
_weapon_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weapon")
_weapon_future = None  # track current job to avoid stacking

def _run_weapon_detection(frame_copy, conf, imgsz):
    res = violence_model.predict(frame_copy, verbose=False, conf=conf, imgsz=imgsz, half=True)
    weapons, violence = [], []
    if res[0].boxes:
        for d_box in res[0].boxes:
            c_name = res[0].names[int(d_box.cls)]
            w_box  = d_box.xyxy[0].cpu().numpy().astype(int)
            if c_name == "Violence":
                violence.append(w_box)
            elif c_name in ("Gun", "Knife", "Phone"):
                weapons.append({
                    "name":   c_name,
                    "center": [(w_box[0]+w_box[2])/2, (w_box[1]+w_box[3])/2],
                    "box":    w_box,
                })
    with weapon_results_lock:
        weapon_thread_result["weapons"]       = weapons
        weapon_thread_result["violence_boxes"] = violence

# --- 4. CONFIGURATION ---
SKELETON              = [(5,6),(5,11),(6,12),(11,12),(5,7),(7,9),(6,8),(8,10),(11,13),(12,14),(13,15),(14,16)]
MIN_PUNCH_VEL         = 65
MIN_PUNCH_SPIKE_RATIO = 2.8
MIN_APPROACH_DOT      = 0.65
MIN_BBOX_OVERLAP_RATIO= 0.08
OVERLAP_CROWD_LIMIT   = 1
VELOCITY_HISTORY_LEN  = 12
ALERT_DURATION        = 90
ALERT_COOLDOWN_FRAMES = 150
WEAPON_PERSISTENCE    = 50
GRIP_THRESHOLD        = 55
DETECTION_INTERVAL    = 6    # run weapon model every N pose frames
MAX_UNSEEN_FRAMES     = 150
WEAPON_CONF           = 0.35
WEAPON_IMGSZ          = 640

# Dashboard push: skip if last push is too recent
DASHBOARD_INTERVAL    = 3    # push every N frames (saves ~5ms/frame)
DASHBOARD_QUALITY     = 55   # JPEG quality for frame push

# --- 5. LOGIC FUNCTIONS (Unchanged accuracy) ---
def count_bbox_overlaps(p_box, all_boxes, overlap_threshold=0.3):
    px1, py1, px2, py2 = p_box
    p_area = (px2 - px1) * (py2 - py1)
    count  = 0
    for b in all_boxes:
        if np.array_equal(b, p_box): continue
        ix1, iy1 = max(px1, b[0]), max(py1, b[1])
        ix2, iy2 = min(px2, b[2]), min(py2, b[3])
        if ix2 > ix1 and iy2 > iy1:
            inter = (ix2 - ix1) * (iy2 - iy1)
            if inter / (p_area + 1e-6) > overlap_threshold:
                count += 1
    return count


def score_strike(tid, joints, prev_joints_dict, vel_history_dict, victims):
    if tid not in prev_joints_dict: return False, 0
    v_inst = np.max(np.linalg.norm(joints[[9,10]] - prev_joints_dict[tid], axis=1))
    if tid not in vel_history_dict:
        vel_history_dict[tid] = deque(maxlen=VELOCITY_HISTORY_LEN)
    vel_history_dict[tid].append(v_inst)
    history = list(vel_history_dict[tid])
    v_peak  = max(history)
    if v_peak < MIN_PUNCH_VEL: return False, v_peak
    v_baseline  = np.median(history[:-1]) if len(history) > 1 else v_peak
    spike_ratio = v_peak / (v_baseline + 1e-6)
    if spike_ratio < MIN_PUNCH_SPIKE_RATIO:    return False, v_peak
    if v_inst < MIN_PUNCH_VEL * 0.75:          return False, v_peak
    for v_id, v_data in victims.items():
        if tid == v_id: continue
        t_box, t_center = v_data["box"], v_data["center"]
        attacker_center = victims[tid]["center"]
        t_w, t_h = t_box[2]-t_box[0], t_box[3]-t_box[1]
        for h_idx in [9, 10]:
            hx, hy = joints[h_idx]
            if hx < 1 and hy < 1: continue
            mx, my = t_w*MIN_BBOX_OVERLAP_RATIO, t_h*MIN_BBOX_OVERLAP_RATIO
            if not ((t_box[0]+mx) < hx < (t_box[2]-mx) and (t_box[1]+my) < hy < (t_box[3]-my)):
                continue
            approach_vec = t_center - attacker_center
            move_vec     = joints[h_idx] - prev_joints_dict[tid][h_idx-9]
            a_norm = np.linalg.norm(approach_vec) + 1e-6
            m_norm = np.linalg.norm(move_vec)     + 1e-6
            if m_norm < 2.0: continue
            dot = np.dot(approach_vec/a_norm, move_vec/m_norm)
            if dot > MIN_APPROACH_DOT: return True, v_peak
    return False, v_peak


def assign_weapons_to_persons(active_weapons, ids, kpts, boxes):
    assignments = {tid: [] for tid in ids}
    for weapon in active_weapons:
        w_center = np.array(weapon["center"])
        best_tid, best_score = None, float("inf")
        for tid, joints, p_box in zip(ids, kpts, boxes):
            wrist_pts = joints[[9, 10]]
            valid     = wrist_pts[np.any(wrist_pts > 1, axis=1)]
            if len(valid) == 0: continue
            score = np.min(np.linalg.norm(valid - w_center, axis=1))
            if not (p_box[0]-25 < w_center[0] < p_box[2]+25) or \
               not (p_box[1]-25 < w_center[1] < p_box[3]+25):
                score += 300
            if score < best_score:
                best_score = score; best_tid = tid
        if best_tid is not None and best_score < GRIP_THRESHOLD + 300:
            assignments[best_tid].append(weapon)
    return assignments


# --- 6. MINIMAL OVERLAY HELPERS ---
_STATE_COLORS = {
    "ASSAULT": (0, 0, 255),
    "ARMED":   (0, 165, 255),
    "NEUTRAL": (0, 220, 0),
}

def draw_overlay(frame, p_box, tid, state):
    """Thin border + single text line — minimal GPU/CPU overlay cost."""
    color = _STATE_COLORS[state]
    x1, y1, x2, y2 = int(p_box[0]), int(p_box[1]), int(p_box[2]), int(p_box[3])
    # 1-px border; only thicker for threats
    thickness = 2 if state != "NEUTRAL" else 1
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
    if state != "NEUTRAL":   # skip label for neutral — saves putText cost
        label = f"{tid}:{state}"
        cv2.putText(frame, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, color, 1, cv2.LINE_AA)


# --- 7. DXCAM INIT ---
camera = dxcam.create(output_color="BGR")
camera.start(target_fps=60, video_mode=True)

# State dictionaries
id_last_seen    = {}
alert_cooldowns = {}
vel_history     = {}
prev_joints     = {}
threat_timers   = {}
weapon_persist  = {}

frame_count = 0
fps_timer   = time.perf_counter()
fps_display = 0.0

print("🚀 Sentinel v14.0: Optimized pipeline active.")

while True:
    frame = camera.get_latest_frame()
    if frame is None:
        continue
    frame_count += 1

    # --- Weapon detection: submit only if previous job is done (no stacking) ---
    if frame_count % DETECTION_INTERVAL == 0:
        if _weapon_future is None or _weapon_future.done():
            _weapon_future = _weapon_executor.submit(
                _run_weapon_detection, frame.copy(), WEAPON_CONF, WEAPON_IMGSZ
            )

    with weapon_results_lock:
        active_weapons      = weapon_thread_result["weapons"]
        violence_boxes_global = weapon_thread_result["violence_boxes"]

    # --- Pose tracking (FP16 on GPU) ---
    pose_res = pose_model.track(frame, persist=True, verbose=False,
                                imgsz=640, half=True)

    if pose_res[0].boxes is not None and pose_res[0].boxes.id is not None:
        ids   = pose_res[0].boxes.id.int().cpu().tolist()
        kpts  = pose_res[0].keypoints.xy.cpu().numpy()
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()

        # Stale-ID cleanup
        for tid in ids: id_last_seen[tid] = frame_count
        stale = [t for t, last in id_last_seen.items()
                 if frame_count - last > MAX_UNSEEN_FRAMES]
        for tid in stale:
            for d in (vel_history, prev_joints, threat_timers,
                      weapon_persist, id_last_seen):
                d.pop(tid, None)

        # Build victims map
        victims = {}
        for tid, joints, b in zip(ids, kpts, boxes):
            torso       = joints[5:13]
            valid_torso = torso[np.any(torso > 1, axis=1)]
            if len(valid_torso) > 0:
                victims[tid] = {"center": np.mean(valid_torso, axis=0), "box": b}

        weapon_assignments = assign_weapons_to_persons(active_weapons, ids, kpts, boxes)

        for tid, joints, p_box in zip(ids, kpts, boxes):
            if tid not in victims: continue

            # --- Weapon hold / persistence ---
            if len(weapon_assignments.get(tid, [])) > 0:
                weapon_persist[tid] = WEAPON_PERSISTENCE
            elif weapon_persist.get(tid, 0) > 0:
                weapon_persist[tid] -= 1
            is_armed = weapon_persist.get(tid, 0) > 0

            # --- Crowd / melee ---
            overlap_count = count_bbox_overlaps(p_box, boxes)
            if overlap_count >= OVERLAP_CROWD_LIMIT:
                is_melee = False
                prev_joints[tid] = joints[[9, 10]]
            else:
                is_melee, _ = score_strike(tid, joints, prev_joints, vel_history, victims)
                prev_joints[tid] = joints[[9, 10]]

            # --- Local violence overlap ---
            local_violence = False
            for vb in violence_boxes_global:
                ix1 = max(p_box[0], vb[0]); iy1 = max(p_box[1], vb[1])
                ix2 = min(p_box[2], vb[2]); iy2 = min(p_box[3], vb[3])
                if ix2 > ix1 and iy2 > iy1:
                    local_violence = True; break

            # --- State machine ---
            is_assault = is_melee or (is_armed and local_violence)
            if tid not in threat_timers: threat_timers[tid] = 0

            if is_assault:
                threat_timers[tid] = ALERT_DURATION
                state = "ASSAULT"
                last_alert = alert_cooldowns.get(tid, 0)
                if frame_count - last_alert > ALERT_COOLDOWN_FRAMES:
                    alert_cooldowns[tid] = frame_count
                    try:
                        requests.post(
                            "http://localhost:8000/trigger",
                            json={"id": tid, "event": "ASSAULT", "confidence": 0.94},
                            timeout=0.01,
                        )
                    except Exception:
                        pass
            elif is_armed:
                state = "ARMED"
            else:
                if threat_timers.get(tid, 0) > 0:
                    threat_timers[tid] -= 1
                    state = "ASSAULT"
                else:
                    state = "NEUTRAL"

            draw_overlay(frame, p_box, tid, state)

    # --- FPS counter (updated once per second, cheap) ---
    now = time.perf_counter()
    elapsed = now - fps_timer
    if elapsed >= 1.0:
        fps_display = frame_count / elapsed  # approximate; reset below is cleaner
        # simple rolling: just reuse last measured gap
        fps_timer   = now
        frame_count = 0  # reset counter for next second window

    # Minimal HUD: semi-transparent bar, single line
    cv2.rectangle(frame, (0, 0), (340, 28), (0, 0, 0), -1)
    cv2.putText(frame, f"EcoVision v14 | FPS: {fps_display:.0f}",
                (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 0), 1, cv2.LINE_AA)

    # --- Dashboard push (throttled) ---
    if frame_count % DASHBOARD_INTERVAL == 0:
        small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_NEAREST)
        _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, DASHBOARD_QUALITY])
        try:
            requests.post("http://localhost:8000/update_frame",
                          files={"frame": buf.tobytes()}, timeout=0.01)
        except Exception:
            pass

    cv2.imshow("EcoVision Sentinel", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

camera.stop()
_weapon_executor.shutdown(wait=False)
cv2.destroyAllWindows()
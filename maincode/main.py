import os
import sys
import logging

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM LOG NOISE FILTERS (Permanently silences the 'half' deprecation warnings)
# ──────────────────────────────────────────────────────────────────────────────
class UltralyticsNoiseFilter(logging.Filter):
    def filter(self, record):
        # Intercepts the log record message string and discards it if it contains the deprecation notice
        return "'half' is deprecated" not in record.getMessage()

# Bind our custom diagnostic filter straight into the main Ultralytics logging registry
logging.getLogger("ultralytics").addFilter(UltralyticsNoiseFilter())

import cv2
import time
import signal
import threading
import requests
import numpy as np
import json
import uuid
from pathlib import Path
from collections import deque
from unittest.mock import MagicMock
from types import ModuleType
from concurrent.futures import ThreadPoolExecutor
import torch

# ──────────────────────────────────────────────────────────────────────────────
# 0. DEPENDENCY CHECK
# ──────────────────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel
    from typing import Optional
    import uvicorn
except ImportError:
    sys.exit("❌ Missing libs. Run: pip install fastapi uvicorn")

# ──────────────────────────────────────────────────────────────────────────────
# 0.1 ROBBERY / VANDALISM CORE DETECTION IMPORTS
# ──────────────────────────────────────────────────────────────────────────────
from robbery_vandalism import RobberyTracker, VandalismTrackState, score_vandalism
from x3d_violence_detector import X3DViolenceDetector

# ──────────────────────────────────────────────────────────────────────────────
# 1. ULTRALYTICS GIT-BYPASS (offline / no-git environment)
# ──────────────────────────────────────────────────────────────────────────────
os.environ["ULTRALYTICS_GIT"]     = "False"
os.environ["ULTRALYTICS_OFFLINE"] = "True"
_mock_repo = MagicMock()
_mock_repo.root = Path(".")
_mock_git_mod   = ModuleType("ultralytics.utils.git")
_mock_git_mod.GitRepo = MagicMock(return_value=_mock_repo)
sys.modules["ultralytics.utils.git"] = _mock_git_mod

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("❌ ultralytics not installed.")

# ──────────────────────────────────────────────────────────────────────────────
# 2. DYNAMIC CONFIGURATION MATRIX LOADER
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(os.path.dirname(BASE_DIR), "config.json")
if not os.path.exists(CONFIG_PATH):
    sys.exit(f"❌ Central configuration file 'config.json' not found at workspace root: {CONFIG_PATH}")

with open(CONFIG_PATH, 'r') as f:
    sys_config = json.load(f)

POSE_IMGSZ          = 416
WEAPON_IMGSZ        = 416
WEAPON_CONF         = sys_config["detection"].get("confidence_threshold", 0.38)
POSE_CONF           = 0.30
DETECTION_INTERVAL  = sys_config["detection"].get("detection_interval", 5)

# NOTE: "phone" removed from WEAPON_CLASSES / CONF_BY_CLASS below.
# The deployed weapon_signs model only outputs Gun/Knife/Sign right now.
# "phone" was dead-code leftover from planning for the deferred
# Phone/Wallet/SprayCan class decision -- re-add it here ONLY once it's
# actually a trained class in weapon_signs.pt, otherwise it's a silent
# no-op class name that can never match a real detection.
WEAPON_CLASSES   = {"gun", "knife", "pistol", "firearm", "handgun", "rifle"}
VIOLENCE_CLASSES = {"violence", "fight", "assault"}
SIGN_CLASSES     = {"sign"}   

CONF_BY_CLASS = {
    "gun":      0.52,
    "pistol":   0.52,
    "firearm":  0.52,
    "handgun":  0.52,
    "rifle":    0.52,
    "knife":    0.45,
    "violence": 0.40,
    "fight":    0.40,
    "assault":  0.40,
    "sign":     0.40,   
}
WEAPON_CONF_GUN_SUSTAINED = 0.35

VBOX_ASSAULT_THRESHOLD = 0.15
SCENE_COOLDOWN_ARMED   = 40
SCENE_COOLDOWN_ASSAULT = sys_config["alert"].get("cooldown_frames", 120)

WEAPON_IOU_MATCH  = 0.25
WEAPON_MAX_UNSEEN = 30

SKELETON = [
    (5,6),(5,11),(6,12),(11,12),
    (5,7),(7,9),(6,8),(8,10),
    (11,13),(12,14),(13,15),(14,16),
]

MIN_PUNCH_VEL          = 60
MIN_PUNCH_SPIKE_RATIO  = 2.5
MIN_APPROACH_DOT       = 0.60
MIN_BBOX_OVERLAP_RATIO = 0.07
VELOCITY_HISTORY_LEN   = 14

OVERLAP_CROWD_LIMIT    = 3
OVERLAP_IOU_THRESH     = 0.25

ASSAULT_CONFIRM_FRAMES = 3
ASSAULT_RELEASE_FRAMES = 60
ARMED_CONFIRM_FRAMES   = 4
ARMED_RELEASE_FRAMES   = 70

VB_IOU_MATCH_THRESH    = 0.30
VB_MAX_UNSEEN          = 8

EVIDENCE_WINDOW        = 8
EVIDENCE_THRESHOLD     = 3

ALERT_COOLDOWN_FRAMES  = 200
SCENE_COOLDOWN_FRAMES  = 120
MAX_UNSEEN_FRAMES      = sys_config["detection"].get("max_unseen_frames", 180)

GRIP_THRESHOLD         = 60

ESP32_IP    = sys_config["esp32"].get("ip_override") or "192.168.254.152"
BACKEND_URL = f"{sys_config['networking']['api_url'].rstrip('/')}/api/ai_trigger"

STREAM_JPEG_QUALITY    = 90
STREAM_FPS_DELAY       = 0.028

# ──────────────────────────────────────────────────────────────────────────────
# 3. STREAM SERVER (lock-free swap)
# ──────────────────────────────────────────────────────────────────────────────
_frame_buf   = [b"", b""]
_buf_write   = 0
_buf_lock    = threading.Lock()
_buf_ready   = threading.Event()

def _push_frame(jpeg_bytes: bytes):
    global _buf_write
    nxt = 1 - _buf_write
    _frame_buf[nxt] = jpeg_bytes
    with _buf_lock:
        _buf_write = nxt
    _buf_ready.set()

def _read_frame() -> bytes:
    with _buf_lock:
        return _frame_buf[_buf_write]

stream_app = FastAPI()

def _frame_generator():
    while True:
        _buf_ready.wait(timeout=0.5)
        _buf_ready.clear()
        data = _read_frame()
        if not data:
            continue
        yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
        time.sleep(STREAM_FPS_DELAY)

@stream_app.get("/video_feed")
def video_feed():
    return StreamingResponse(
        _frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )

def _start_stream_server():
    uvicorn.run(stream_app, host=sys_config["backend"]["host"], port=8001, log_level="error")

threading.Thread(target=_start_stream_server, daemon=True).start()
print(f"📡 Dynamic Stream server live → http://localhost:8001/video_feed")

# ──────────────────────────────────────────────────────────────────────────────
# 4. HARDWARE DISCOVERY & MODEL INITIALIZATION MATRIX
# ──────────────────────────────────────────────────────────────────────────────
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
WEIGHTS_DIR = os.path.join(PROJECT_ROOT, "weights")

# IMPORTANT: these must resolve to the EXACT same physical folders that
# backend.py serves via StaticFiles, or the frontend gets 404s on every
# screenshot/clip no matter how correctly the URLs are built.
# backend.py computes: BASE_DIR = dirname(dirname(backend.py))  -> project root
#   SCREENSHOTS_DIR = BASE_DIR/static/screenshots
#   RECORDINGS_DIR  = BASE_DIR/<config.database.recordings_subdir, default "recordings">
# main.py's PROJECT_ROOT (dirname of this file's folder) IS that same project
# root, so anchor on PROJECT_ROOT here too -- NOT on CURRENT_DIR (which is
# main.py's own subfolder, one level too deep).
SCREENSHOTS_DIR = os.path.join(PROJECT_ROOT, "static", "screenshots")

os.makedirs(WEIGHTS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

pose_file_name   = "yolo11s-pose.engine" if os.path.exists(os.path.join(WEIGHTS_DIR, "yolo11s-pose.engine")) else "yolo11s-pose.pt"
weapon_file_name = "weapon_signs.engine" if os.path.exists(os.path.join(WEIGHTS_DIR, "weapon_signs.engine")) else "weapon_signs.pt"
x3d_model_path  = os.path.join(WEIGHTS_DIR, "x3d_xs_violence_best.pt")

pose_model_path = os.path.join(WEIGHTS_DIR, pose_file_name)
w_weight_path   = os.path.join(WEIGHTS_DIR, weapon_file_name)

USE_CUDA = torch.cuda.is_available()
TARGET_DEVICE = "cuda" if USE_CUDA else "cpu"
print(f"📡 [HARDWARE PROFILER] Selected Execution Target: {TARGET_DEVICE.upper()}")
print(f"📦 [ENGINE LOADER] Mounting Pose Pipeline: {pose_file_name}")
print(f"📦 [ENGINE LOADER] Mounting Weapon Pipeline: {weapon_file_name}")

pose_model     = YOLO(pose_model_path, task="pose")
violence_model = YOLO(w_weight_path, task="detect")
x3d_detector   = X3DViolenceDetector(model_path=x3d_model_path, device=TARGET_DEVICE)

if pose_file_name.endswith(".pt"):
    pose_model.to(TARGET_DEVICE)
    if USE_CUDA:
        pose_model.model.half()

if weapon_file_name.endswith(".pt"):
    violence_model.to(TARGET_DEVICE)
    if USE_CUDA:
        violence_model.model.half()

_dummy = np.zeros((POSE_IMGSZ, POSE_IMGSZ, 3), dtype=np.uint8)
pose_model.predict(_dummy, verbose=False, imgsz=POSE_IMGSZ, half=(USE_CUDA and pose_file_name.endswith(".pt")))
violence_model.predict(_dummy, verbose=False, imgsz=WEAPON_IMGSZ, half=(USE_CUDA and weapon_file_name.endswith(".pt")))
print("✅ Dynamic relative weights successfully loaded and warmed up.")

# ──────────────────────────────────────────────────────────────────────────────
# 5. THREAD POOL EXECUTORS
# ──────────────────────────────────────────────────────────────────────────────
_weapon_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weapon")
_encode_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="encode")
_alert_exec    = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alert")

_weapon_future = None
_encode_future = None

_weapon_lock  = threading.Lock()
_weapon_cache = {"weapons": [], "vboxes": []}

# ──────────────────────────────────────────────────────────────────────────────
# 6. VIOLENCE-BOX TEMPORAL TRACKER
# ──────────────────────────────────────────────────────────────────────────────
class VBoxTracker:
    def __init__(self):
        self._tracks: list[dict] = []

    @staticmethod
    def _iou(a, b):
        ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
        ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        ua    = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
        return inter / (ua + 1e-6)

    def update(self, new_boxes_with_conf):
        for t in self._tracks:
            t["unseen"] += 1

        for nb, conf in new_boxes_with_conf:
            best_idx, best_iou = -1, VB_IOU_MATCH_THRESH
            for i, t in enumerate(self._tracks):
                iou = self._iou(nb, t["box"])
                if iou > best_iou:
                    best_iou = iou; best_idx = i
            if best_idx >= 0:
                t = self._tracks[best_idx]
                t["conf"]   = 0.6 * conf + 0.4 * t["conf"]
                t["box"]    = nb
                t["unseen"] = 0
            else:
                self._tracks.append({"box": nb, "unseen": 0, "conf": conf})

        def _max_unseen(t):
            return int(VB_MAX_UNSEEN * (0.5 + min(t["conf"], 1.0)))

        self._tracks = [t for t in self._tracks if t["unseen"] <= _max_unseen(t)]
        return [t["box"] for t in self._tracks]

    def live_boxes(self):
        return [t["box"] for t in self._tracks]

_vbox_tracker = VBoxTracker()

# ──────────────────────────────────────────────────────────────────────────────
# 7. PER-INSTANCE WEAPON TRACKER
# ──────────────────────────────────────────────────────────────────────────────
_weapon_track_store:   dict[int, dict] = {}
_weapon_track_counter: int             = 0

def _update_weapon_tracks(raw_weapons: list) -> list:
    global _weapon_track_counter
    for t in _weapon_track_store.values():
        t["unseen"] += 1

    live_gun_classes = {
        t["name"] for t in _weapon_track_store.values()
        if t["name"] in {"gun", "pistol", "firearm", "handgun", "rifle"} and t["unseen"] == 0
    }

    for w in raw_weapons:
        cls_name  = w["name"]
        raw_conf  = w["conf"]
        w_box     = w["box"]

        if cls_name in {"gun", "pistol", "firearm", "handgun", "rifle"}:
            threshold = (
                WEAPON_CONF_GUN_SUSTAINED if cls_name in live_gun_classes
                else CONF_BY_CLASS.get(cls_name, WEAPON_CONF)
            )
            if raw_conf < threshold:
                continue

        best_wid, best_iou = None, WEAPON_IOU_MATCH
        for wid, t in _weapon_track_store.items():
            if t["name"] != cls_name:
                continue
            iou = VBoxTracker._iou(w_box, t["box"])
            if iou > best_iou:
                best_iou = iou; best_wid = wid

        if best_wid is not None:
            _weapon_track_store[best_wid].update({
                "box": w_box, "conf": raw_conf, "center": w["center"], "unseen": 0,
            })
        else:
            _weapon_track_store[_weapon_track_counter] = {
                "name": cls_name, "box": w_box, "conf": raw_conf, "center": w["center"], "unseen": 0,
            }
            _weapon_track_counter += 1

    stale = [wid for wid, t in _weapon_track_store.items() if t["unseen"] > WEAPON_MAX_UNSEEN]
    for wid in stale:
        del _weapon_track_store[wid]

    return list(_weapon_track_store.values())

# ──────────────────────────────────────────────────────────────────────────────
# 8. WEAPON DETECTION THREAD WORKER
# ──────────────────────────────────────────────────────────────────────────────
def _run_weapon_detection(frame_copy):
    res = violence_model.predict(frame_copy, verbose=False, conf=WEAPON_CONF, imgsz=WEAPON_IMGSZ, half=(USE_CUDA and weapon_file_name.endswith(".pt")))
    weapons, vboxes = [], []
    if res[0].boxes:
        for box in res[0].boxes:
            cls_raw  = res[0].names[int(box.cls)]
            cls_name = cls_raw.lower().strip()
            raw_conf = float(box.conf[0].cpu())
            xyxy     = box.xyxy[0].cpu().numpy().astype(int)
            required_conf = CONF_BY_CLASS.get(cls_name, WEAPON_CONF)

            if cls_name in VIOLENCE_CLASSES:
                if raw_conf >= required_conf:
                    vboxes.append((xyxy, raw_conf))
            elif cls_name in WEAPON_CLASSES:
                if raw_conf >= required_conf:
                    weapons.append({
                        "name": cls_name, "conf": raw_conf,
                        "center": [(xyxy[0]+xyxy[2])/2, (xyxy[1]+xyxy[3])/2], "box": xyxy,
                    })
            elif cls_name in SIGN_CLASSES:   
                if raw_conf >= required_conf:
                    weapons.append({
                        "name": cls_name, "conf": raw_conf,
                        "center": [(xyxy[0]+xyxy[2])/2, (xyxy[1]+xyxy[3])/2], "box": xyxy,
                    })
    with _weapon_lock:
        _weapon_cache["weapons"] = weapons
        _weapon_cache["vboxes"]  = vboxes

# ──────────────────────────────────────────────────────────────────────────────
# 9. MATH / SCORING HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def _bbox_overlap_count(p_box, all_boxes):
    px1, py1, px2, py2 = p_box
    p_area = max((px2-px1)*(py2-py1), 1)
    count  = 0
    for b in all_boxes:
        if np.array_equal(b, p_box): continue
        ix1 = max(px1, b[0]); iy1 = max(py1, b[1])
        ix2 = min(px2, b[2]); iy2 = min(py2, b[3])
        if ix2 > ix1 and iy2 > iy1:
            inter = (ix2 - ix1) * (iy2 - iy1)
            b_area = max((b[2]-b[0])*(b[3]-b[1]), 1)
            ratio  = inter / min(p_area, b_area)
            if ratio > OVERLAP_IOU_THRESH: count += 1
    return count

def _assign_weapons(active_weapons, ids, kpts, boxes):
    assignments: dict[int, list] = {tid: [] for tid in ids}
    for weapon in active_weapons:
        w_center  = np.array(weapon["center"])
        best_tid, best_score = None, float("inf")

        for tid, joints, p_box in zip(ids, kpts, boxes):
            wrists = joints[[9, 10]]
            valid  = wrists[np.any(wrists > 1, axis=1)]
            if len(valid) == 0: continue

            dist = float(np.min(np.linalg.norm(valid - w_center, axis=1)))
            margin = 30
            outside = (w_center[0] < p_box[0] - margin or w_center[0] > p_box[2] + margin or w_center[1] < p_box[1] - margin or w_center[1] > p_box[3] + margin)
            if outside: dist += 350
            if dist < best_score: best_score = dist; best_tid = tid

        if best_tid is not None and best_score < GRIP_THRESHOLD + 350:
            assignments[best_tid].append(weapon)
    return assignments

def _vbox_overlap_ratio(p_box, vb):
    ix1, iy1 = max(p_box[0], vb[0]), max(p_box[1], vb[1])
    ix2, iy2 = min(p_box[2], vb[2]), min(p_box[3], vb[3])
    if ix2 <= ix1 or iy2 <= iy1: return 0.0
    inter  = (ix2 - ix1) * (iy2 - iy1)
    p_area = max((p_box[2]-p_box[0]) * (p_box[3]-p_box[1]), 1)
    return inter / p_area

def _post_alert(incident_id, conf: float, event: str = "ASSAULT", screenshot_path: str = None):
    print(f"🔥 [ALERT] Posting {event} event | case_id={incident_id} | conf={conf:.2f}")
    try:
        payload = {
            "id": str(incident_id),
            "event": event,
            "confidence": round(conf, 4),
            "barangayId": "cogon",
        }
        # screenshot_path is a URL-relative path like "/static/screenshots/snap_XXXX.jpg" --
        # the backend needs to persist this on the incident record so CrimeReportsView.tsx's
        # `inc.screenshotPath` (and the report-filing modal's `reportImageUrl`) have something
        # real to render instead of falling back to the picsum placeholder.
        if screenshot_path:
            payload["screenshotPath"] = screenshot_path
        r = requests.post(BACKEND_URL, json=payload, timeout=2.0)
        print(f"   ✅ Backend {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"   ❌ Backend unreachable: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# 12. PER-TRACK STATE MACHINE
# ──────────────────────────────────────────────────────────────────────────────
class TrackState:
    __slots__ = ("state", "assault_confirm", "assault_release", "armed_confirm", "armed_release", "evidence_buf", "last_alert_frame")
    def __init__(self):
        self.state            = "NEUTRAL"
        self.assault_confirm  = 0
        self.assault_release  = 0
        self.armed_confirm    = 0
        self.armed_release    = 0
        self.evidence_buf     = deque(maxlen=EVIDENCE_WINDOW)
        self.last_alert_frame = -ALERT_COOLDOWN_FRAMES

    def update(self, is_assault: bool, is_armed: bool, frame_no: int, override_assault_confirm: int = None) -> str:
        confirm_needed = override_assault_confirm if override_assault_confirm is not None else ASSAULT_CONFIRM_FRAMES
        self.evidence_buf.append(int(is_assault))

        if is_assault:
            self.assault_confirm  = min(self.assault_confirm + 1, confirm_needed)
            self.assault_release  = 0
        else:
            self.assault_release = min(self.assault_release + 1, ASSAULT_RELEASE_FRAMES)
            if self.assault_release >= ASSAULT_RELEASE_FRAMES:
                self.assault_confirm = 0

        if is_armed:
            self.armed_confirm  = min(self.armed_confirm + 1, ARMED_CONFIRM_FRAMES)
            self.armed_release  = 0
        else:
            self.armed_release  = min(self.armed_release + 1, ARMED_RELEASE_FRAMES)
            if self.armed_release >= ARMED_RELEASE_FRAMES:
                self.armed_confirm = 0

        if self.assault_confirm >= confirm_needed:
            self.state = "ASSAULT"
        elif self.armed_confirm >= ARMED_CONFIRM_FRAMES:
            self.state = "ARMED"
        else:
            if self.assault_confirm == 0 and self.armed_confirm == 0:
                self.state = "NEUTRAL"
        return self.state

    def should_alert(self, frame_no: int, scene_last: int, scene_cooldown: int = SCENE_COOLDOWN_ASSAULT) -> bool:
        if self.state != "ASSAULT" and self.state != "ARMED":
            return False
        evidence_ok    = True if self.state == "ARMED" else (sum(self.evidence_buf) >= EVIDENCE_THRESHOLD)
        track_cooldown = (frame_no - self.last_alert_frame) > ALERT_COOLDOWN_FRAMES
        scene_ok       = (frame_no - scene_last) > scene_cooldown
        return evidence_ok and track_cooldown and scene_ok

    def mark_alerted(self, frame_no: int):
        self.last_alert_frame = frame_no

# ──────────────────────────────────────────────────────────────────────────────
# 13. OVERLAY DRAWING
# ──────────────────────────────────────────────────────────────────────────────
_STATE_CFG = {"ASSAULT": ((0,0,255),2,True), "ARMED": ((0,165,255),2,True), "NEUTRAL": ((0,210,80),1,False)}

def _draw_overlay(frame, p_box, tid, state, weapons=None):
    color, thick, show_label = _STATE_CFG[state]
    x1, y1, x2, y2 = int(p_box[0]), int(p_box[1]), int(p_box[2]), int(p_box[3])
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thick)

    if show_label:
        label = f"{tid}:{state}"
        if weapons:
            w_names = "+".join(w["name"] for w in weapons)
            label   = f"{tid}:{state}[{w_names}]"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)
        cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(frame, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0,0,0), 1, cv2.LINE_AA)

def _draw_violence_boxes(frame, live_vboxes):
    for vb in live_vboxes:
        cv2.rectangle(frame, (int(vb[0]), int(vb[1])), (int(vb[2]), int(vb[3])), (0,0,200), 1)
        cv2.putText(frame, "VIOLENCE", (int(vb[0]), int(vb[1]) - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0,0,200), 1, cv2.LINE_AA)

def _draw_sign_boxes(frame, sign_boxes):
    for sb in sign_boxes:
        x1, y1, x2, y2 = int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3])
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 200, 0), 1)
        cv2.putText(frame, "SIGN", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 200, 0), 1, cv2.LINE_AA)

def _draw_x3d_crop_box(frame, crop_box, is_violent: bool, conf: float):
    if crop_box is None:
        return

    cx1, cy1, cx2, cy2 = crop_box

    color = (
        int(255 * (1 - conf)),   
        int(180 * (1 - conf)),   
        int(255 * conf) + 50,    
    )
    thickness = 2 if is_violent else 1

    cv2.rectangle(frame, (cx1, cy1), (cx2, cy2), color, thickness, cv2.LINE_AA)

    label = f"X3D VIEW {conf*100:.0f}%"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
    cv2.rectangle(frame, (cx1, cy1 - th - 6), (cx1 + tw + 4, cy1), color, -1)
    cv2.putText(frame, label, (cx1 + 2, cy1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)

def _draw_x3d_confidence(frame, p_box, debug_info: dict):
    x1, y1, x2, y2 = int(p_box[0]), int(p_box[1]), int(p_box[2]), int(p_box[3])
    conf = debug_info["confidence"]
    fill = debug_info["buffer_fill"]
    target = debug_info["buffer_target"]

    color = (int(255 * conf), int(255 * (1 - conf)), 255 if conf > 0.3 else 0)
    label = f"X3D:{conf*100:.0f}% [{fill}/{target}]"
    cv2.putText(frame, label, (x1, y2 + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1, cv2.LINE_AA)

# ──────────────────────────────────────────────────────────────────────────────
# 14. CAMERA INIT (DYNAMIC HARDWARE DETECTION INDEX)
# ──────────────────────────────────────────────────────────────────────────────
camera_idx = sys_config["camera"].get("index", 5)
cap = cv2.VideoCapture(camera_idx)

res_w, res_h = map(int, sys_config["camera"]["default_resolution"].lower().split('x'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  res_w)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res_h)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

# ──────────────────────────────────────────────────────────────────────────────
# 14.1 CAMERA RECONNECT HELPER
# ──────────────────────────────────────────────────────────────────────────────
def _reopen_camera():
    """Attempts to fully reinitialize the capture device after a drop."""
    global cap
    try:
        cap.release()
    except Exception:
        pass
    new_cap = cv2.VideoCapture(camera_idx)
    new_cap.set(cv2.CAP_PROP_FRAME_WIDTH,  res_w)
    new_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res_h)
    new_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap = new_cap
    return cap.isOpened()

# ──────────────────────────────────────────────────────────────────────────────
# 15.1 RAW-FRAME RING BUFFER + EVENT CLIP CAPTURE
# ──────────────────────────────────────────────────────────────────────────────
# When ANY alert fires (ASSAULT / ARMED THREAT / ROBBERY / VANDALISM), this
# captures a short MP4 spanning CLIP_PRE_SECONDS before the trigger to
# CLIP_POST_SECONDS after it, using the SAME fully-annotated frame that's
# already being drawn each iteration (overlays, PiP, HUD included) -- so the
# clip shows exactly what the operator/AI saw on screen, same idea as the
# existing screenshot snapshot, just extended across time.
RECORDINGS_DIR = os.path.join(PROJECT_ROOT, "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

CLIP_PRE_SECONDS  = 5
CLIP_POST_SECONDS = 5
CLIP_NOMINAL_FPS  = sys_config["camera"].get("fps", 15)   # sizes the ring buffer + used as encode-fps fallback
CLIP_PRE_FRAMES   = max(1, int(CLIP_NOMINAL_FPS * CLIP_PRE_SECONDS))

_clip_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip")

# Guards _raw_frame_ring and _pending_clips below. Needed because the main
# detection loop mutates these every frame, and (as of the panic-button
# integration) the /panic_capture route on stream_app can ALSO start a new
# pending clip from a different thread (uvicorn's) at any moment.
_clip_state_lock = threading.Lock()

# Always-on rolling buffer of (timestamp, annotated_frame) -- topped up every
# frame regardless of whether anything is currently flagged.
_raw_frame_ring: deque = deque(maxlen=CLIP_PRE_FRAMES)

# In-flight clips still accumulating their post-event frames. Each entry is
# handed off to _clip_exec once it reaches its target length.
_pending_clips: list = []


def _start_pending_clip(incident_id: str, event: str, conf: float):
    """Called the instant an alert (or a panic-button press) fires.
    Snapshots the existing pre-event ring buffer and starts accumulating
    post-event frames going forward. Thread-safe -- callable from the main
    detection loop OR from the /panic_capture route handler."""
    with _clip_state_lock:
        pre_frames = [f for _, f in _raw_frame_ring]
        _pending_clips.append({
            "incident_id":   incident_id,
            "event":         event,
            "conf":          conf,
            "frames":        pre_frames,        # grows with post-event frames each iteration
            "trigger_index": len(pre_frames),   # frame index within `frames` where the alert fired
            "target_len":    len(pre_frames) + int(CLIP_NOMINAL_FPS * CLIP_POST_SECONDS),
            "start_ts":      time.perf_counter(),
        })


def _feed_pending_clips(annotated_frame):
    """Called once per main-loop iteration with the latest annotated frame.
    Appends it to every in-flight clip and ships out any that are complete."""
    with _clip_state_lock:
        if not _pending_clips:
            return
        still_pending = []
        for clip in _pending_clips:
            clip["frames"].append(annotated_frame)
            if len(clip["frames"]) >= clip["target_len"]:
                _clip_exec.submit(_finalize_and_register_clip, clip)
            else:
                still_pending.append(clip)
        _pending_clips[:] = still_pending


def _finalize_and_register_clip(clip: dict):
    """Background-thread work: encode buffered frames to MP4 and register
    the clip against the incident via the backend's records endpoint."""
    incident_id = clip["incident_id"]
    frames      = clip["frames"]
    if not frames:
        return

    elapsed    = max(time.perf_counter() - clip["start_ts"], 0.1)
    encode_fps = max(1.0, len(frames) / (elapsed + CLIP_PRE_SECONDS))  # rough, but keeps playback pacing sane

    safe_event = clip["event"].replace(" ", "_")
    filename   = f"AUTO_{safe_event}_{incident_id}.mp4"
    file_path  = os.path.join(RECORDINGS_DIR, filename)

    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(file_path, cv2.VideoWriter_fourcc(*"mp4v"), encode_fps, (w, h))
    try:
        for f in frames:
            writer.write(f)
    finally:
        writer.release()

    trigger_seconds = clip["trigger_index"] / encode_fps
    marker          = f"{int(trigger_seconds // 60):02d}:{int(trigger_seconds % 60):02d}"
    total_seconds   = len(frames) / encode_fps

    print(f"🎬 [CLIP] Saved {filename} ({total_seconds:.1f}s, marker@{marker}) for case {incident_id}")

    try:
        r = requests.post(f"{sys_config['networking']['api_url'].rstrip('/')}/api/records/register_clip", json={
            "filename":          filename,
            "duration":          f"{total_seconds:.1f}s",
            "type":              "CLIP",
            "associatedCrimeId": incident_id,
            "crimeTimeMarker":   marker,
            "notes":             f"Auto-captured by AI Sentinel on {clip['event']} detection (conf={clip['conf']:.2f}).",
        }, timeout=3.0)
        print(f"   ✅ Records backend {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"   ❌ Records backend unreachable: {e}")


class PanicCaptureRequest(BaseModel):
    incident_id: str


@stream_app.post("/panic_capture")
def panic_capture(payload: PanicCaptureRequest):
    """Hit by backend.py's /api/panic_trigger the instant the hardware panic
    button fires. Grabs the latest real annotated frame for a screenshot and
    kicks off the same pre/post-event clip pipeline used for AI alerts --
    keyed to the SAME incident_id the backend already generated, so the clip
    lands correctly associated once it finishes encoding."""
    incident_id = payload.incident_id

    with _clip_state_lock:
        if not _raw_frame_ring:
            return {"status": "no_frame_available", "screenshotPath": None}
        latest_frame = _raw_frame_ring[-1][1]

    snap_filename = f"snap_{incident_id}.jpg"
    snap_path = os.path.join(SCREENSHOTS_DIR, snap_filename)
    cv2.imwrite(snap_path, latest_frame)

    _start_pending_clip(incident_id, "HARDWARE_PANIC_INTERRUPT", 1.0)

    screenshot_url_path = f"/static/screenshots/{snap_filename}"
    print(f"🚨 [PANIC] Captured screenshot + started clip for case {incident_id}")
    return {"status": "captured", "screenshotPath": screenshot_url_path}


# ──────────────────────────────────────────────────────────────────────────────
# 15. PER-TRACK STORES
# ──────────────────────────────────────────────────────────────────────────────
track_states, prev_joints, vel_history, id_last_seen = {}, {}, {}, {}
robbery_tracker = RobberyTracker()                  
vandal_states: dict = {}             
vandal_sweep_history: dict = {}                     
_vandal_alert_cooldown: dict = {}  
_robbery_alert_cooldown: dict = {}

scene_last_alert_frame = -SCENE_COOLDOWN_FRAMES

_running = True
def _shutdown(*_):
    global _running
    _running = False

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

frame_count, fps_timer, fps_display, fps_frame_count = 0, time.perf_counter(), 0.0, 0
_camera_fail_streak = 0
print("🚀 Sentinel v16.0 — Portable dynamic deployment runtime context pipeline engaged.")

while _running:
    ret, frame = cap.read()
    if not ret or frame is None:
        _camera_fail_streak += 1
        # After ~2s of consecutive failures, try to hard-reopen the device
        # instead of spinning forever on a dead handle.
        if _camera_fail_streak >= 200:
            print("⚠️  Camera read failing repeatedly — attempting reconnect...")
            if _reopen_camera():
                print("✅ Camera reconnected.")
            else:
                print("❌ Camera reconnect failed, retrying in 1s...")
                time.sleep(1.0)
            _camera_fail_streak = 0
        time.sleep(0.01)
        continue
    _camera_fail_streak = 0

    frame_count += 1
    fps_frame_count += 1

    if frame_count % DETECTION_INTERVAL == 0:
        if _weapon_future is None or _weapon_future.done():
            _weapon_future = _weapon_exec.submit(_run_weapon_detection, frame.copy())

    with _weapon_lock:
        raw_weapons = list(_weapon_cache["weapons"])
        raw_vboxes  = list(_weapon_cache["vboxes"])

    tracked_weapons = _update_weapon_tracks(raw_weapons)
    live_vboxes = _vbox_tracker.update(raw_vboxes)
    
    res_half_flag = (USE_CUDA and pose_file_name.endswith(".pt"))
    pose_res = pose_model.track(frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, half=res_half_flag)

    triggered_alerts_this_frame = []
    active_pip_crop = None
    pip_border_color = (0, 255, 80) 

    if (pose_res[0].boxes is not None and pose_res[0].boxes.id is not None and pose_res[0].keypoints is not None):
        ids = pose_res[0].boxes.id.int().cpu().tolist()
        kpts = pose_res[0].keypoints.xy.cpu().numpy()
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()

        for tid in ids:
            id_last_seen[tid] = frame_count
            
        stale = [t for t, lf in id_last_seen.items() if frame_count - lf > MAX_UNSEEN_FRAMES]
        for tid in stale:
            for d in (track_states, prev_joints, vel_history, id_last_seen,
                      vandal_states, vandal_sweep_history, _vandal_alert_cooldown):                
                d.pop(tid, None)
            x3d_detector.cleanup_track(tid)   

        victims = {}
        for tid, joints, b in zip(ids, kpts, boxes):
            torso = joints[5:13]
            valid = torso[np.any(torso > 1, axis=1)]
            if len(valid) > 0:
                victims[tid] = {"center": np.mean(valid, axis=0), "box": b}

        weapon_only = [w for w in tracked_weapons if w["name"] not in SIGN_CLASSES]
        weapon_assigns = _assign_weapons(weapon_only, ids, kpts, boxes)

        # ── FIX: snapshot prev_joints BEFORE the per-track loop below
        # overwrites it with this frame's wrist positions. Vandalism scoring
        # runs later in this same frame and needs the *previous* frame's
        # wrist positions to compute velocity -- without this snapshot,
        # score_vandalism() was comparing this frame's wrists to
        # themselves, so wrist velocity was always ~0 and Vandalism could
        # never enter its "sweep band" and would never fire.
        prev_joints_snapshot = dict(prev_joints)

        for tid, joints, p_box in zip(ids, kpts, boxes):
            if tid not in victims:
                continue
            if tid not in track_states:
                track_states[tid] = TrackState()
            ts = track_states[tid]

            has_weapon = len(weapon_assigns.get(tid, [])) > 0
            crowded = _bbox_overlap_count(p_box, boxes) >= OVERLAP_CROWD_LIMIT

            prev_joints[tid] = joints[[9, 10]].copy()

            is_violent_x3d, x3d_conf = x3d_detector.update(tid, frame, p_box, frame_count, all_boxes=boxes)
            
            _draw_x3d_confidence(frame, p_box, x3d_detector.get_debug_info(tid))
            _draw_x3d_crop_box(frame, x3d_detector.get_crop_box(tid), is_violent_x3d, x3d_conf)   

            in_vbox = max((_vbox_overlap_ratio(p_box, vb) for vb in live_vboxes), default=0.0) >= VBOX_ASSAULT_THRESHOLD
            is_assault = is_violent_x3d or in_vbox

            override_confirm = max(1, ASSAULT_CONFIRM_FRAMES - 1) if (crowded and in_vbox) else None
            state = ts.update(is_assault, has_weapon, frame_count, override_assault_confirm=override_confirm)

            if active_pip_crop is None or state in ["ASSAULT", "ARMED"]:
                live_crop_patch = x3d_detector.get_latest_live_crop(tid)
                if live_crop_patch is not None:
                    active_pip_crop = live_crop_patch
                    if state == "ASSAULT":
                        pip_border_color = (0, 0, 255) 
                    elif state == "ARMED":
                        pip_border_color = (0, 165, 255) 
                    else:
                        pip_border_color = (0, 210, 80) 

            cooldown = SCENE_COOLDOWN_ARMED if state == "ARMED" else SCENE_COOLDOWN_ASSAULT
            if ts.should_alert(frame_count, scene_last_alert_frame, cooldown):
                conf = 0.932 if state == "ARMED" else min(1.0, sum(ts.evidence_buf) / EVIDENCE_WINDOW + 0.55)
                ts.mark_alerted(frame_count)
                scene_last_alert_frame = frame_count
                
                incident_id = str(uuid.uuid4())[:8]
                event_type = "ARMED THREAT" if state == "ARMED" else "ASSAULT"
                triggered_alerts_this_frame.append({"id": incident_id, "conf": conf, "event": event_type})

            _draw_overlay(frame, p_box, tid, state, weapon_assigns.get(tid))

        # ─── ROBBERY FILTER ANALYSIS ───
        armed_states    = {t: (track_states[t].state == "ARMED")   for t in ids if t in track_states}
        violence_states = {t: (track_states[t].state == "ASSAULT") for t in ids if t in track_states}
        
        robbery_pairs = robbery_tracker.update(ids, boxes, armed_states, violence_states)
        for pair_key, r_state in robbery_pairs.items():
            if r_state == "ROBBERY":
                last_alert = _robbery_alert_cooldown.get(pair_key, -ALERT_COOLDOWN_FRAMES)
                if frame_count - last_alert > ALERT_COOLDOWN_FRAMES:
                    _robbery_alert_cooldown[pair_key] = frame_count
                    incident_id = str(uuid.uuid4())[:8]
                    triggered_alerts_this_frame.append({"id": incident_id, "conf": 0.895, "event": "ROBBERY"})

        # ─── VANDALISM FILTER ANALYSIS ───
        sign_boxes = [w["box"] for w in tracked_weapons if w["name"] == "sign"]
        _draw_sign_boxes(frame, sign_boxes)
        
        for tid, joints, p_box in zip(ids, kpts, boxes):
            if tid not in vandal_states:
                vandal_states[tid] = VandalismTrackState()
            sweep_hist = vandal_sweep_history.setdefault(tid, deque(maxlen=45))

            # Use the PRE-overwrite snapshot, not the live `prev_joints`
            # dict (which now holds this frame's wrist positions).
            is_vandal, target = score_vandalism(
                tid, joints, prev_joints_snapshot, sweep_hist,
                static_targets=sign_boxes, all_person_boxes=boxes, my_box=p_box
            )
            v_state_res = vandal_states[tid].update(is_vandal)
            
            last_alert = _vandal_alert_cooldown.get(tid, -ALERT_COOLDOWN_FRAMES)
            if v_state_res == "VANDALISM" and (frame_count - last_alert > ALERT_COOLDOWN_FRAMES):
                _vandal_alert_cooldown[tid] = frame_count
                incident_id = str(uuid.uuid4())[:8]
                triggered_alerts_this_frame.append({"id": incident_id, "conf": 0.84, "event": "VANDALISM"})

    now = time.perf_counter()
    if now - fps_timer >= 1.0:
        fps_display = fps_frame_count / (now - fps_timer)
        fps_timer = now
        fps_frame_count = 0

    fh, fw = frame.shape[:2]
    if active_pip_crop is not None and fw > 220 and fh > 220:
        startX, startY = fw - 180, 40
        endX, endY = startX + 160, startY + 160
        frame[startY:endY, startX:endX] = active_pip_crop
        cv2.rectangle(frame, (startX - 1, startY - 1), (endX + 1, endY + 1), pip_border_color, 2)
        cv2.rectangle(frame, (startX - 1, startY - 18), (startX + 105, startY - 1), (0, 0, 0), -1)
        cv2.putText(frame, "X3D MODEL VIEW", (startX + 4, startY - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (255, 255, 255), 1, cv2.LINE_AA)

    hud = f"EcoVision v16.0 | FPS: {fps_display:.0f} | Tracks: {len(id_last_seen)}"
    cv2.rectangle(frame, (0, 0), (len(hud) * 8 + 10, 26), (0, 0, 0), -1)
    cv2.putText(frame, hud, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 80), 1, cv2.LINE_AA)

    # ─── SECURE POST-RENDER ANNOTATION SNAPSHOT FLUSH ───
    for alert in triggered_alerts_this_frame:
        snap_filename = f"snap_{alert['id']}.jpg"
        snap_path = os.path.join(SCREENSHOTS_DIR, snap_filename)
        cv2.imwrite(snap_path, frame)
        screenshot_url_path = f"/static/screenshots/{snap_filename}"
        _alert_exec.submit(_post_alert, alert['id'], alert['conf'], alert['event'], screenshot_url_path)
        _start_pending_clip(alert['id'], alert['event'], alert['conf'])

    # Keep the raw-frame ring buffer topped up every frame (not just alert
    # frames) and feed any in-flight clips their next frame. One copy is
    # shared between the ring buffer and any pending clips since nothing
    # downstream mutates it.
    annotated_snapshot = frame.copy()
    with _clip_state_lock:
        _raw_frame_ring.append((time.perf_counter(), annotated_snapshot))
    _feed_pending_clips(annotated_snapshot)

    if _encode_future is None or _encode_future.done():
        def _encode_and_push(f=frame.copy()):
            _, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
            _push_frame(buf.tobytes())
        _encode_future = _encode_exec.submit(_encode_and_push)

    time.sleep(0.005)

print("\n🛑 Shutting down Portable Sentinel Matrix pipeline...")
cap.release()
cv2.destroyAllWindows()
_weapon_exec.shutdown(wait=False, cancel_futures=True)
_encode_exec.shutdown(wait=False, cancel_futures=True)
_alert_exec.shutdown(wait=True)
_clip_exec.shutdown(wait=True)   # let any in-progress clip finish encoding/uploading before exit
print("Portable Sentinel shutdown complete.")
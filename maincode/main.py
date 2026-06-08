import os
import sys
import cv2
import time
import signal
import threading
import requests
import numpy as np
from pathlib import Path
from collections import deque
from unittest.mock import MagicMock
from types import ModuleType
from concurrent.futures import ThreadPoolExecutor

# ──────────────────────────────────────────────────────────────────────────────
# 0.  DEPENDENCY CHECK
# ──────────────────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    import uvicorn
except ImportError:
    sys.exit("❌  Missing libs. Run: pip install fastapi uvicorn")

# ──────────────────────────────────────────────────────────────────────────────
# 1.  ULTRALYTICS GIT-BYPASS  (offline / no-git environment)
# ──────────────────────────────────────────────────────────────────────────────
os.environ["ULTRALYTICS_GIT"]     = "False"
os.environ["ULTRALYTICS_OFFLINE"] = "True"
_mock_repo      = MagicMock(); _mock_repo.root = Path(".")
_mock_git_mod   = ModuleType("ultralytics.utils.git")
_mock_git_mod.GitRepo = MagicMock(return_value=_mock_repo)
sys.modules["ultralytics.utils.git"] = _mock_git_mod

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("❌  ultralytics not installed.")

# ──────────────────────────────────────────────────────────────────────────────
# 2.  CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

# -- Model / inference
POSE_IMGSZ          = 416
WEAPON_IMGSZ        = 416
WEAPON_CONF         = 0.38       # fallback conf for unrecognised classes
POSE_CONF           = 0.30
DETECTION_INTERVAL  = 5

# -- Per-class confidence thresholds (NEW)
WEAPON_CLASSES   = {"gun", "knife", "pistol", "firearm", "handgun", "rifle", "phone"}
VIOLENCE_CLASSES = {"violence", "fight", "assault"}

CONF_BY_CLASS = {
    "gun":      0.52,   # strict first-detection; tracker relaxes to WEAPON_CONF_GUN_SUSTAINED
    "pistol":   0.52,
    "firearm":  0.52,
    "handgun":  0.52,
    "rifle":    0.52,
    "knife":    0.45,
    "violence": 0.40,
    "fight":    0.40,
    "assault":  0.40,
    "phone":    0.38,
}
WEAPON_CONF_GUN_SUSTAINED = 0.35   # relaxed conf once a gun track is live

# -- Proportional vbox overlap gate (NEW)
VBOX_ASSAULT_THRESHOLD = 0.15     # person box must be ≥15% inside vbox

# -- Separate scene cooldowns per threat state (NEW)
SCENE_COOLDOWN_ARMED   = 40       # frames — gun is persistent threat
SCENE_COOLDOWN_ASSAULT = 120      # frames — existing behaviour

# -- Per-instance weapon tracker (NEW)
WEAPON_IOU_MATCH  = 0.25
WEAPON_MAX_UNSEEN = 30            # frames before a weapon track dies

# -- Skeleton pairs (unused visually but kept for extension)
SKELETON = [
    (5,6),(5,11),(6,12),(11,12),
    (5,7),(7,9),(6,8),(8,10),
    (11,13),(12,14),(13,15),(14,16),
]

# -- Strike scoring
MIN_PUNCH_VEL          = 60
MIN_PUNCH_SPIKE_RATIO  = 2.5
MIN_APPROACH_DOT       = 0.60
MIN_BBOX_OVERLAP_RATIO = 0.07
VELOCITY_HISTORY_LEN   = 14

# -- Crowd / overlap
OVERLAP_CROWD_LIMIT    = 3        # raised: 1v1 fighters clipping a bystander no longer suppresses melee
OVERLAP_IOU_THRESH     = 0.25

# -- Hysteresis FSM thresholds
ASSAULT_CONFIRM_FRAMES = 3
ASSAULT_RELEASE_FRAMES = 60
ARMED_CONFIRM_FRAMES   = 4
ARMED_RELEASE_FRAMES   = 70

# -- Violence-box temporal fusion
VB_IOU_MATCH_THRESH    = 0.30
VB_MAX_UNSEEN          = 8

# -- Temporal confidence buffer
EVIDENCE_WINDOW        = 8
EVIDENCE_THRESHOLD     = 3

# -- Alert / cooldown
ALERT_COOLDOWN_FRAMES  = 200
SCENE_COOLDOWN_FRAMES  = 120      # kept for reference; ARMED/ASSAULT now use own values
MAX_UNSEEN_FRAMES      = 180

# -- Weapon grip
GRIP_THRESHOLD         = 60

# -- Network
ESP32_IP               = "192.168.254.152"
BACKEND_URL            = "http://localhost:8000/api/ai_trigger"

# -- Stream / encoding
STREAM_JPEG_QUALITY    = 90
STREAM_FPS_DELAY       = 0.028

# ──────────────────────────────────────────────────────────────────────────────
# 3.  STREAM SERVER  (port 8001) — double-buffered, lock-free swap
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
    uvicorn.run(stream_app, host="0.0.0.0", port=8001, log_level="error")

threading.Thread(target=_start_stream_server, daemon=True).start()
print("📡 Stream server → http://localhost:8001/video_feed")

# ──────────────────────────────────────────────────────────────────────────────
# 4.  MODEL INIT + GPU WARM-UP
# ──────────────────────────────────────────────────────────────────────────────
pose_model     = YOLO("yolo11s-pose.pt")
violence_model = YOLO(r"D:\projects\EcoVisionCode\weights\best.pt")

_dummy = np.zeros((POSE_IMGSZ, POSE_IMGSZ, 3), dtype=np.uint8)
pose_model.predict(_dummy,     verbose=False, imgsz=POSE_IMGSZ,   half=True)
violence_model.predict(_dummy, verbose=False, imgsz=WEAPON_IMGSZ, half=True)
print("✅  GPU weights warmed up.")

# ──────────────────────────────────────────────────────────────────────────────
# 5.  THREAD POOL EXECUTORS
# ──────────────────────────────────────────────────────────────────────────────
_weapon_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weapon")
_encode_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="encode")
_alert_exec    = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alert")

_weapon_future = None
_encode_future = None

_weapon_lock  = threading.Lock()
_weapon_cache = {"weapons": [], "vboxes": []}

# ──────────────────────────────────────────────────────────────────────────────
# 6.  VIOLENCE-BOX TEMPORAL TRACKER  — conf-weighted EMA (IMPROVED)
# ──────────────────────────────────────────────────────────────────────────────
class VBoxTracker:
    """
    IoU-based tracker for violence bounding boxes.
    Confidence is now tracked via EMA: high-conf boxes survive longer;
    low-conf boxes die faster.
    """
    def __init__(self):
        self._tracks: list[dict] = []   # {box, unseen, conf}

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
        """
        Feed in list of (xyxy, conf) tuples.
        Returns current set of live tracked boxes.
        """
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
                t["conf"]   = 0.6 * conf + 0.4 * t["conf"]   # EMA update
                t["box"]    = nb
                t["unseen"] = 0
            else:
                self._tracks.append({"box": nb, "unseen": 0, "conf": conf})

        # High-conf tracks get proportionally more patience
        def _max_unseen(t):
            return int(VB_MAX_UNSEEN * (0.5 + min(t["conf"], 1.0)))

        self._tracks = [t for t in self._tracks if t["unseen"] <= _max_unseen(t)]
        return [t["box"] for t in self._tracks]

    def live_boxes(self):
        return [t["box"] for t in self._tracks]


_vbox_tracker = VBoxTracker()

# ──────────────────────────────────────────────────────────────────────────────
# 7.  PER-INSTANCE WEAPON TRACKER  (NEW)
#     Tracks each weapon independently of which person holds it.
#     Prevents armed-confirm counter reset when a gun drifts between track IDs.
# ──────────────────────────────────────────────────────────────────────────────
_weapon_track_store:   dict[int, dict] = {}
_weapon_track_counter: int             = 0

def _update_weapon_tracks(raw_weapons: list) -> list:
    """
    Matches raw detections to existing weapon tracks by IoU.
    Returns list of currently live weapon track dicts (same schema as raw_weapons).
    """
    global _weapon_track_counter

    # Age all existing tracks
    for t in _weapon_track_store.values():
        t["unseen"] += 1

    # Determine which weapon class names currently have live tracks
    live_gun_classes = {
        t["name"] for t in _weapon_track_store.values()
        if t["name"] in {"gun", "pistol", "firearm", "handgun", "rifle"}
        and t["unseen"] == 0   # refreshed this frame
    }

    for w in raw_weapons:
        cls_name  = w["name"]
        raw_conf  = w["conf"]
        w_box     = w["box"]

        # Gun sustained-confidence gate: once a gun track is live, relax threshold
        if cls_name in {"gun", "pistol", "firearm", "handgun", "rifle"}:
            threshold = (
                WEAPON_CONF_GUN_SUSTAINED
                if cls_name in live_gun_classes
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
                "box": w_box, "conf": raw_conf,
                "center": w["center"], "unseen": 0,
            })
        else:
            _weapon_track_store[_weapon_track_counter] = {
                "name": cls_name, "box": w_box,
                "conf": raw_conf, "center": w["center"], "unseen": 0,
            }
            _weapon_track_counter += 1

    # Evict stale weapon tracks
    stale = [wid for wid, t in _weapon_track_store.items()
             if t["unseen"] > WEAPON_MAX_UNSEEN]
    for wid in stale:
        del _weapon_track_store[wid]

    return list(_weapon_track_store.values())


# ──────────────────────────────────────────────────────────────────────────────
# 8.  WEAPON DETECTION THREAD WORKER  (IMPROVED — class normalisation)
# ──────────────────────────────────────────────────────────────────────────────
def _run_weapon_detection(frame_copy):
    res = violence_model.predict(
        frame_copy, verbose=False,
        conf=WEAPON_CONF,   # model-level gate; per-class filtering applied below
        imgsz=WEAPON_IMGSZ, half=True,
    )
    weapons, vboxes = [], []
    if res[0].boxes:
        for box in res[0].boxes:
            cls_raw  = res[0].names[int(box.cls)]
            cls_name = cls_raw.lower().strip()          # NORMALISE
            raw_conf = float(box.conf[0].cpu())
            xyxy     = box.xyxy[0].cpu().numpy().astype(int)

            required_conf = CONF_BY_CLASS.get(cls_name, WEAPON_CONF)

            if cls_name in VIOLENCE_CLASSES:
                if raw_conf >= required_conf:
                    vboxes.append((xyxy, raw_conf))     # now carries conf
            elif cls_name in WEAPON_CLASSES:
                if raw_conf >= required_conf:
                    weapons.append({
                        "name":   cls_name,
                        "conf":   raw_conf,
                        "center": [(xyxy[0]+xyxy[2])/2, (xyxy[1]+xyxy[3])/2],
                        "box":    xyxy,
                    })

    with _weapon_lock:
        _weapon_cache["weapons"] = weapons
        _weapon_cache["vboxes"]  = vboxes   # list of (xyxy, conf)

# ──────────────────────────────────────────────────────────────────────────────
# 9.  MATH / SCORING HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _bbox_overlap_count(p_box, all_boxes):
    px1, py1, px2, py2 = p_box
    p_area = max((px2-px1)*(py2-py1), 1)
    count  = 0
    for b in all_boxes:
        if np.array_equal(b, p_box):
            continue
        ix1 = max(px1, b[0]); iy1 = max(py1, b[1])
        ix2 = min(px2, b[2]); iy2 = min(py2, b[3])
        if ix2 > ix1 and iy2 > iy1:
            inter = (ix2-ix1)*(iy2-iy1)
            b_area = max((b[2]-b[0])*(b[3]-b[1]), 1)
            ratio  = inter / min(p_area, b_area)
            if ratio > OVERLAP_IOU_THRESH:
                count += 1
    return count


def _score_strike(tid, joints, prev_joints_dict, vel_history_dict, victims):
    if tid not in prev_joints_dict:
        return False, 0.0

    wrists_now  = joints[[9, 10]]
    wrists_prev = prev_joints_dict[tid]

    valid_mask = np.any(wrists_now > 1, axis=1) & np.any(wrists_prev > 1, axis=1)
    if not np.any(valid_mask):
        return False, 0.0

    velocities = np.linalg.norm(wrists_now[valid_mask] - wrists_prev[valid_mask], axis=1)
    v_inst     = float(np.max(velocities))

    buf = vel_history_dict.setdefault(tid, deque(maxlen=VELOCITY_HISTORY_LEN))
    buf.append(v_inst)
    history = list(buf)
    v_peak  = max(history)

    if v_peak < MIN_PUNCH_VEL:
        return False, v_peak

    v_baseline  = float(np.median(history[:-1])) if len(history) > 1 else v_peak
    spike_ratio = v_peak / (v_baseline + 1e-6)
    if spike_ratio < MIN_PUNCH_SPIKE_RATIO:
        return False, v_peak
    if v_inst < MIN_PUNCH_VEL * 0.70:
        return False, v_peak

    for v_id, v_data in victims.items():
        if v_id == tid:
            continue
        t_box    = v_data["box"]
        t_center = v_data["center"]
        t_w      = t_box[2] - t_box[0]
        t_h      = t_box[3] - t_box[1]

        attacker_center = victims[tid]["center"]
        approach_vec    = t_center - attacker_center

        for h_idx in [9, 10]:
            hx, hy = wrists_now[h_idx - 9]
            if hx < 1 and hy < 1:
                continue
            mx = t_w * MIN_BBOX_OVERLAP_RATIO
            my = t_h * MIN_BBOX_OVERLAP_RATIO
            inside = (
                (t_box[0]+mx) < hx < (t_box[2]-mx) and
                (t_box[1]+my) < hy < (t_box[3]-my)
            )
            if not inside:
                continue
            move_vec = wrists_now[h_idx - 9] - wrists_prev[h_idx - 9]
            a_norm   = np.linalg.norm(approach_vec) + 1e-6
            m_norm   = np.linalg.norm(move_vec)     + 1e-6
            if m_norm < 2.0:
                continue
            dot = np.dot(approach_vec / a_norm, move_vec / m_norm)
            if dot > MIN_APPROACH_DOT:
                return True, v_peak

    return False, v_peak


def _assign_weapons(active_weapons, ids, kpts, boxes):
    assignments: dict[int, list] = {tid: [] for tid in ids}
    for weapon in active_weapons:
        w_center  = np.array(weapon["center"])
        best_tid  = None
        best_score = float("inf")

        for tid, joints, p_box in zip(ids, kpts, boxes):
            wrists = joints[[9, 10]]
            valid  = wrists[np.any(wrists > 1, axis=1)]
            if len(valid) == 0:
                continue

            dist = float(np.min(np.linalg.norm(valid - w_center, axis=1)))
            margin = 30
            outside = (
                w_center[0] < p_box[0] - margin or
                w_center[0] > p_box[2] + margin or
                w_center[1] < p_box[1] - margin or
                w_center[1] > p_box[3] + margin
            )
            if outside:
                dist += 350

            if dist < best_score:
                best_score = dist; best_tid = tid

        if best_tid is not None and best_score < GRIP_THRESHOLD + 350:
            assignments[best_tid].append(weapon)

    return assignments


# ──────────────────────────────────────────────────────────────────────────────
# 10.  PROPORTIONAL VBOX OVERLAP HELPER  (NEW)
# ──────────────────────────────────────────────────────────────────────────────
def _vbox_overlap_ratio(p_box, vb):
    """Return fraction of person box that overlaps with violence box."""
    ix1 = max(p_box[0], vb[0]); iy1 = max(p_box[1], vb[1])
    ix2 = min(p_box[2], vb[2]); iy2 = min(p_box[3], vb[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter  = (ix2 - ix1) * (iy2 - iy1)
    p_area = max((p_box[2]-p_box[0]) * (p_box[3]-p_box[1]), 1)
    return inter / p_area

# ──────────────────────────────────────────────────────────────────────────────
# 11.  ALERT POSTER (async, fire-and-forget) — UNCHANGED
# ──────────────────────────────────────────────────────────────────────────────
def _post_alert(tid: int, conf: float):
    print(f"🔥 [ALERT] Posting assault event for track {tid} | conf={conf:.2f}")
    try:
        r = requests.post(
            BACKEND_URL,
            json={"id": str(tid), "event": "ASSAULT", "confidence": round(conf, 4)},
            timeout=2.0,
        )
        print(f"   ✅ Backend {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"   ❌ Backend unreachable: {e}")
    try:
        r2 = requests.get(f"http://{ESP32_IP}/alarm/on", timeout=0.5)
        print(f"   🔊 ESP32 {r2.status_code}")
    except Exception as e:
        print(f"   ⚠️  ESP32 offline ({ESP32_IP}): {e}")

# ──────────────────────────────────────────────────────────────────────────────
# 12.  PER-TRACK STATE MACHINE  (IMPROVED — separate cooldowns per state)
# ──────────────────────────────────────────────────────────────────────────────
class TrackState:
    """
    Hysteresis FSM for a single tracked person.
    should_alert now accepts a per-state scene_cooldown so ARMED and ASSAULT
    events fire at appropriate cadences.
    """
    __slots__ = (
        "state",
        "assault_confirm", "assault_release",
        "armed_confirm",   "armed_release",
        "evidence_buf",
        "last_alert_frame",
    )

    def __init__(self):
        self.state            = "NEUTRAL"
        self.assault_confirm  = 0
        self.assault_release  = 0
        self.armed_confirm    = 0
        self.armed_release    = 0
        self.evidence_buf     = deque(maxlen=EVIDENCE_WINDOW)
        self.last_alert_frame = -ALERT_COOLDOWN_FRAMES

    def update(self, is_assault: bool, is_armed: bool, frame_no: int,
               override_assault_confirm: int = None) -> str:
        """
        override_assault_confirm: if provided, uses this threshold instead of
        ASSAULT_CONFIRM_FRAMES (used for crowd+vbox fast-confirm).
        """
        confirm_needed = (
            override_assault_confirm
            if override_assault_confirm is not None
            else ASSAULT_CONFIRM_FRAMES
        )

        self.evidence_buf.append(int(is_assault))

        # ── ASSAULT ───────────────────────────────────────────────
        if is_assault:
            self.assault_confirm  = min(self.assault_confirm + 1, confirm_needed)
            self.assault_release  = 0
        else:
            self.assault_release  = min(self.assault_release + 1, ASSAULT_RELEASE_FRAMES)
            if self.assault_release >= ASSAULT_RELEASE_FRAMES:
                self.assault_confirm = 0

        # ── ARMED ─────────────────────────────────────────────────
        if is_armed:
            self.armed_confirm  = min(self.armed_confirm + 1, ARMED_CONFIRM_FRAMES)
            self.armed_release  = 0
        else:
            self.armed_release  = min(self.armed_release + 1, ARMED_RELEASE_FRAMES)
            if self.armed_release >= ARMED_RELEASE_FRAMES:
                self.armed_confirm = 0

        # ── State resolution ──────────────────────────────────────
        if self.assault_confirm >= confirm_needed:
            self.state = "ASSAULT"
        elif self.armed_confirm >= ARMED_CONFIRM_FRAMES:
            self.state = "ARMED"
        else:
            if self.assault_confirm == 0 and self.armed_confirm == 0:
                self.state = "NEUTRAL"

        return self.state

    def should_alert(self, frame_no: int, scene_last: int,
                     scene_cooldown: int = SCENE_COOLDOWN_ASSAULT) -> bool:
        if self.state != "ASSAULT":
            return False
        evidence_ok    = sum(self.evidence_buf) >= EVIDENCE_THRESHOLD
        track_cooldown = (frame_no - self.last_alert_frame) > ALERT_COOLDOWN_FRAMES
        scene_ok       = (frame_no - scene_last)            > scene_cooldown
        return evidence_ok and track_cooldown and scene_ok

    def mark_alerted(self, frame_no: int):
        self.last_alert_frame = frame_no


# ──────────────────────────────────────────────────────────────────────────────
# 13.  OVERLAY DRAWING — UNCHANGED
# ──────────────────────────────────────────────────────────────────────────────
_STATE_CFG = {
    "ASSAULT": ((0,   0, 255),  2, True ),
    "ARMED":   ((0, 165, 255),  2, True ),
    "NEUTRAL": ((0, 210,  80),  1, False),
}

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
        cv2.putText(frame, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 0, 0), 1, cv2.LINE_AA)

def _draw_violence_boxes(frame, live_vboxes):
    for vb in live_vboxes:
        cv2.rectangle(frame,
                      (int(vb[0]), int(vb[1])), (int(vb[2]), int(vb[3])),
                      (0, 0, 200), 1)
        cv2.putText(frame, "VIOLENCE", (int(vb[0]), int(vb[1]) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 200), 1, cv2.LINE_AA)

# ──────────────────────────────────────────────────────────────────────────────
# 14.  CAMERA INIT — UNCHANGED
# ──────────────────────────────────────────────────────────────────────────────
cap = cv2.VideoCapture(5)
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)

# ──────────────────────────────────────────────────────────────────────────────
# 15.  PER-TRACK STORES — UNCHANGED
# ──────────────────────────────────────────────────────────────────────────────
track_states:   dict[int, TrackState] = {}
prev_joints:    dict[int, np.ndarray] = {}
vel_history:    dict[int, deque]      = {}
id_last_seen:   dict[int, int]        = {}

scene_last_alert_frame = -SCENE_COOLDOWN_FRAMES

# ──────────────────────────────────────────────────────────────────────────────
# 16.  GRACEFUL SHUTDOWN — UNCHANGED
# ──────────────────────────────────────────────────────────────────────────────
_running = True

def _shutdown(*_):
    global _running
    _running = False

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ──────────────────────────────────────────────────────────────────────────────
# 17.  MAIN LOOP
# ──────────────────────────────────────────────────────────────────────────────
frame_count     = 0
fps_timer       = time.perf_counter()
fps_display     = 0.0
fps_frame_count = 0

print("🚀 Sentinel v16.0 — pipeline engaged.")

while _running:
    ret, frame = cap.read()
    if not ret or frame is None:
        time.sleep(0.01)
        continue

    frame_count     += 1
    fps_frame_count += 1

    # ── A. Weapon detection (async) ──────────────────────────────────────────
    if frame_count % DETECTION_INTERVAL == 0:
        if _weapon_future is None or _weapon_future.done():
            _weapon_future = _weapon_exec.submit(_run_weapon_detection, frame.copy())

    with _weapon_lock:
        raw_weapons = list(_weapon_cache["weapons"])
        raw_vboxes  = list(_weapon_cache["vboxes"])   # list of (xyxy, conf)

    # ── B1. Per-instance weapon tracking (NEW) ───────────────────────────────
    tracked_weapons = _update_weapon_tracks(raw_weapons)

    # ── B2. Violence-box temporal fusion (conf-weighted) ─────────────────────
    live_vboxes = _vbox_tracker.update(raw_vboxes)

    # ── C. Pose tracking ──────────────────────────────────────────────────────
    pose_res = pose_model.track(
        frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, half=True
    )

    if (pose_res[0].boxes is not None and
            pose_res[0].boxes.id is not None and
            pose_res[0].keypoints is not None):

        ids   = pose_res[0].boxes.id.int().cpu().tolist()
        kpts  = pose_res[0].keypoints.xy.cpu().numpy()
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()

        for tid in ids:
            id_last_seen[tid] = frame_count
        stale = [t for t, lf in id_last_seen.items() if frame_count - lf > MAX_UNSEEN_FRAMES]
        for tid in stale:
            for d in (track_states, prev_joints, vel_history, id_last_seen):
                d.pop(tid, None)

        victims: dict[int, dict] = {}
        for tid, joints, b in zip(ids, kpts, boxes):
            torso = joints[5:13]
            valid = torso[np.any(torso > 1, axis=1)]
            if len(valid) > 0:
                victims[tid] = {"center": np.mean(valid, axis=0), "box": b}

        # Use tracked_weapons (per-instance) instead of raw_weapons for assignment
        weapon_assigns = _assign_weapons(tracked_weapons, ids, kpts, boxes)

        for tid, joints, p_box in zip(ids, kpts, boxes):
            if tid not in victims:
                continue

            if tid not in track_states:
                track_states[tid] = TrackState()
            ts = track_states[tid]

            # ── Armed? ───────────────────────────────────────────────────────
            has_weapon = len(weapon_assigns.get(tid, [])) > 0

            # ── Assault / melee? ─────────────────────────────────────────────
            overlap_count = _bbox_overlap_count(p_box, boxes)
            crowded       = overlap_count >= OVERLAP_CROWD_LIMIT

            if crowded:
                is_melee = False
            else:
                is_melee, _ = _score_strike(
                    tid, joints, prev_joints, vel_history, victims
                )

            prev_joints[tid] = joints[[9, 10]].copy()

            # ── Violence-box overlap (proportional, NEW) ──────────────────────
            vbox_strength = max(
                (_vbox_overlap_ratio(p_box, vb) for vb in live_vboxes),
                default=0.0,
            )
            in_vbox = vbox_strength >= VBOX_ASSAULT_THRESHOLD

            is_assault = is_melee or in_vbox

            # ── Crowd + vbox fast-confirm (NEW) ───────────────────────────────
            # In dense crowds, melee scoring is suppressed. If the person is
            # also inside a violence box, lower the confirm threshold by 1 so
            # brawl events still fire through the FSM.
            override_confirm = None
            if crowded and in_vbox:
                override_confirm = max(1, ASSAULT_CONFIRM_FRAMES - 1)

            # ── Hysteresis FSM update ────────────────────────────────────────
            state = ts.update(is_assault, has_weapon, frame_count,
                              override_assault_confirm=override_confirm)

            # ── Alert gating (state-specific cooldown, NEW) ───────────────────
            cooldown = (
                SCENE_COOLDOWN_ARMED
                if state == "ARMED"
                else SCENE_COOLDOWN_ASSAULT
            )
            if ts.should_alert(frame_count, scene_last_alert_frame, cooldown):
                conf = min(1.0, sum(ts.evidence_buf) / EVIDENCE_WINDOW + 0.55)
                ts.mark_alerted(frame_count)
                scene_last_alert_frame = frame_count
                _alert_exec.submit(_post_alert, tid, conf)

            # ── Draw ─────────────────────────────────────────────────────────
            _draw_overlay(frame, p_box, tid, state, weapon_assigns.get(tid))

        _draw_violence_boxes(frame, live_vboxes)

    # ── D. FPS counter ───────────────────────────────────────────────────────
    now     = time.perf_counter()
    elapsed = now - fps_timer
    if elapsed >= 1.0:
        fps_display     = fps_frame_count / elapsed
        fps_timer       = now
        fps_frame_count = 0

    # ── E. HUD overlay ───────────────────────────────────────────────────────
    hud = f"EcoVision v16.0 | FPS: {fps_display:.0f} | Tracks: {len(id_last_seen)}"
    cv2.rectangle(frame, (0, 0), (len(hud) * 8 + 10, 26), (0, 0, 0), -1)
    cv2.putText(frame, hud, (6, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 255, 80), 1, cv2.LINE_AA)

    # ── F. Push to stream (async encode) ─────────────────────────────────────
    if _encode_future is None or _encode_future.done():
        frame_for_encode = frame.copy()
        def _encode_and_push(f=frame_for_encode):
            _, buf = cv2.imencode(
                ".jpg", f,
                [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY],
            )
            _push_frame(buf.tobytes())
        _encode_future = _encode_exec.submit(_encode_and_push)

    # ── G. Local preview ─────────────────────────────────────────────────────
    cv2.imshow("EcoVision Sentinel v16.0", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

# ──────────────────────────────────────────────────────────────────────────────
# 18.  CLEAN SHUTDOWN — UNCHANGED
# ──────────────────────────────────────────────────────────────────────────────
print("\n🛑 Shutting down Sentinel...")
cap.release()
_weapon_exec.shutdown(wait=False, cancel_futures=True)
_encode_exec.shutdown(wait=False, cancel_futures=True)
_alert_exec.shutdown(wait=True)
cv2.destroyAllWindows()
print("✅ Sentinel shut down cleanly.")
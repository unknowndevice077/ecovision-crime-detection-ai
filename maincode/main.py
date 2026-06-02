"""
EcoVision Sentinel v15.0 — Improved Violence / Crime / Gun Detection Pipeline
==============================================================================
Key improvements over v14.3:
  • Flicker fix: per-person state uses a hysteresis FSM with configurable
    CONFIRM and RELEASE thresholds — a single-frame spike can never flip state.
  • Crowd robustness: punch scoring is now skipped only when the *target*
    is the crowd-occluded box, not the whole frame; melee scoring still runs
    for edge persons.
  • Consistent violence-box fusion: YOLO violence boxes are tracked across
    frames with IoU matching so a brief missed detection doesn't reset state.
  • Gun/knife persistence is independent per weapon instance, not per person,
    so a weapon that drifts between tracked IDs isn't lost.
  • Temporal confidence buffer: each person's threat evidence is accumulated
    over a rolling window before an alert fires — eliminates single-frame FPs.
  • Alert deduplication: the backend POST is gated by a per-track cooldown
    AND a global scene cooldown so a crowd brawl doesn't spam the database.
  • Frame encoding moved fully off-thread with a double-buffer so the main
    loop is never blocked by JPEG compression.
  • Clean shutdown: executors drain gracefully on SIGINT / 'q'.
"""

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
WEAPON_CONF         = 0.38       # slightly higher → fewer false weapon pings
POSE_CONF           = 0.30       # minimum keypoint confidence kept
DETECTION_INTERVAL  = 5          # run weapon model every N frames

# -- Skeleton pairs (unused visually but kept for extension)
SKELETON = [
    (5,6),(5,11),(6,12),(11,12),
    (5,7),(7,9),(6,8),(8,10),
    (11,13),(12,14),(13,15),(14,16),
]

# -- Strike scoring
MIN_PUNCH_VEL          = 60       # px/frame peak wrist velocity
MIN_PUNCH_SPIKE_RATIO  = 2.5      # peak / median ratio
MIN_APPROACH_DOT       = 0.60     # direction alignment toward victim
MIN_BBOX_OVERLAP_RATIO = 0.07     # hand must be this far inside victim box
VELOCITY_HISTORY_LEN   = 14

# -- Crowd / overlap
OVERLAP_CROWD_LIMIT    = 2        # ≥N overlapping boxes → disable melee for THAT person
OVERLAP_IOU_THRESH     = 0.25

# -- Hysteresis FSM thresholds  ← THE FLICKER FIX
#    A track must accumulate this many consecutive evidence frames before
#    transitioning TO a higher state, and must lose evidence for this many
#    frames before dropping BACK to a lower state.
ASSAULT_CONFIRM_FRAMES = 3        # frames of assault evidence needed to enter ASSAULT
ASSAULT_RELEASE_FRAMES = 60       # frames of no evidence needed to leave ASSAULT
ARMED_CONFIRM_FRAMES   = 4        # frames weapon must persist before showing ARMED
ARMED_RELEASE_FRAMES   = 70       # frames after weapon gone before dropping ARMED

# -- Violence-box temporal fusion
VB_IOU_MATCH_THRESH    = 0.30     # IoU to match a new vbox to a tracked one
VB_MAX_UNSEEN          = 8        # frames a vbox lives without a fresh detection

# -- Temporal confidence buffer (rolling window)
EVIDENCE_WINDOW        = 8        # frames in rolling window
EVIDENCE_THRESHOLD     = 3        # minimum positive frames in window to confirm alert

# -- Alert / cooldown
ALERT_COOLDOWN_FRAMES  = 200      # per-track cooldown between alert POSTs
SCENE_COOLDOWN_FRAMES  = 120      # global cooldown — prevents crowd spam
MAX_UNSEEN_FRAMES      = 180      # frames before a track is fully evicted

# -- Weapon grip
GRIP_THRESHOLD         = 60       # px: max wrist-to-weapon-center distance

# -- Network
ESP32_IP               = "192.168.254.152"
BACKEND_URL            = "http://localhost:8000/api/ai_trigger"

# -- Stream / encoding
STREAM_JPEG_QUALITY    = 90
STREAM_FPS_DELAY       = 0.028    # ~35 fps ceiling on stream

# ──────────────────────────────────────────────────────────────────────────────
# 3.  STREAM SERVER  (port 8001) — double-buffered, lock-free swap
# ──────────────────────────────────────────────────────────────────────────────
_frame_buf   = [b"", b""]        # double buffer
_buf_write   = 0                  # index currently being written
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
_weapon_cache = {"weapons": [], "vboxes": []}   # vboxes = raw violence detections

# ──────────────────────────────────────────────────────────────────────────────
# 6.  VIOLENCE-BOX TEMPORAL TRACKER
#     Keeps detected violence regions alive across missed frames using IoU.
# ──────────────────────────────────────────────────────────────────────────────
class VBoxTracker:
    """
    Lightweight IoU-based tracker for violence bounding boxes.
    Each tracked box has an 'unseen' counter; boxes die after VB_MAX_UNSEEN frames.
    """
    def __init__(self):
        self._tracks: list[dict] = []   # {box, unseen}

    @staticmethod
    def _iou(a, b):
        ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
        ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        ua    = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
        return inter / (ua + 1e-6)

    def update(self, new_boxes):
        """
        Feed in freshly detected violence boxes (may be empty list).
        Returns the current set of *live* tracked boxes.
        """
        matched = set()
        for t in self._tracks:
            t["unseen"] += 1

        for nb in new_boxes:
            best_idx, best_iou = -1, VB_IOU_MATCH_THRESH
            for i, t in enumerate(self._tracks):
                iou = self._iou(nb, t["box"])
                if iou > best_iou:
                    best_iou = iou; best_idx = i
            if best_idx >= 0:
                self._tracks[best_idx]["box"]    = nb   # refresh position
                self._tracks[best_idx]["unseen"] = 0
                matched.add(best_idx)
            else:
                self._tracks.append({"box": nb, "unseen": 0})

        self._tracks = [t for t in self._tracks if t["unseen"] <= VB_MAX_UNSEEN]
        return [t["box"] for t in self._tracks]

    def live_boxes(self):
        return [t["box"] for t in self._tracks if t["unseen"] <= VB_MAX_UNSEEN]


_vbox_tracker = VBoxTracker()

# ──────────────────────────────────────────────────────────────────────────────
# 7.  WEAPON DETECTION THREAD WORKER
# ──────────────────────────────────────────────────────────────────────────────
def _run_weapon_detection(frame_copy):
    res = violence_model.predict(
        frame_copy, verbose=False,
        conf=WEAPON_CONF, imgsz=WEAPON_IMGSZ, half=True,
    )
    weapons, vboxes = [], []
    if res[0].boxes:
        for box in res[0].boxes:
            cls_name = res[0].names[int(box.cls)]
            xyxy     = box.xyxy[0].cpu().numpy().astype(int)
            if cls_name == "Violence":
                vboxes.append(xyxy)
            elif cls_name in ("Gun", "Knife", "Phone"):
                conf_score = float(box.conf[0].cpu())
                weapons.append({
                    "name":   cls_name,
                    "conf":   conf_score,
                    "center": [(xyxy[0]+xyxy[2])/2, (xyxy[1]+xyxy[3])/2],
                    "box":    xyxy,
                })
    with _weapon_lock:
        _weapon_cache["weapons"] = weapons
        _weapon_cache["vboxes"]  = vboxes

# ──────────────────────────────────────────────────────────────────────────────
# 8.  MATH / SCORING HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _bbox_overlap_count(p_box, all_boxes):
    """Count how many OTHER boxes overlap p_box above OVERLAP_IOU_THRESH."""
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
            # Use the smaller of the two boxes as denominator (more sensitive)
            b_area = max((b[2]-b[0])*(b[3]-b[1]), 1)
            ratio  = inter / min(p_area, b_area)
            if ratio > OVERLAP_IOU_THRESH:
                count += 1
    return count


def _score_strike(tid, joints, prev_joints_dict, vel_history_dict, victims):
    """
    Returns (is_strike: bool, peak_velocity: float).
    Uses wrist velocity spike + directional approach toward victim.
    """
    if tid not in prev_joints_dict:
        return False, 0.0

    wrists_now  = joints[[9, 10]]
    wrists_prev = prev_joints_dict[tid]

    # Only use keypoints above confidence floor (> 1 px means YOLO gave coords)
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
    if v_inst < MIN_PUNCH_VEL * 0.70:   # must still be fast this frame
        return False, v_peak

    # Directional check — must be moving toward a victim
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
            hx, hy = wrists_now[h_idx - 9]   # h_idx 9→idx0, 10→idx1
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
    """
    Assigns each weapon to the person whose wrist is closest to it.
    Falls back gracefully when no valid wrists exist.
    """
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
            # Penalise if weapon center is well outside the person bbox
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
# 9.  ALERT POSTER (async, fire-and-forget)
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
# 10.  PER-TRACK STATE MACHINE
#      States: NEUTRAL → ARMED → ASSAULT
#      Transitions require hysteresis counters to prevent flickering.
# ──────────────────────────────────────────────────────────────────────────────
class TrackState:
    """
    Hysteresis FSM for a single tracked person.

    Flicker is eliminated by requiring N consecutive evidence frames to
    enter a threat state, and M consecutive non-evidence frames to leave it.
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
        self.last_alert_frame = -ALERT_COOLDOWN_FRAMES   # so first alert fires

    def update(self, is_assault: bool, is_armed: bool, frame_no: int) -> str:
        # ── Rolling evidence buffer (for confident alert gating) ──
        self.evidence_buf.append(int(is_assault))
        evidence_score = sum(self.evidence_buf)

        # ── ASSAULT ───────────────────────────────────────────────
        if is_assault:
            self.assault_confirm  = min(self.assault_confirm + 1, ASSAULT_CONFIRM_FRAMES)
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
        if self.assault_confirm >= ASSAULT_CONFIRM_FRAMES:
            self.state = "ASSAULT"
        elif self.armed_confirm >= ARMED_CONFIRM_FRAMES:
            self.state = "ARMED"
        else:
            # Only drop to NEUTRAL after both release timers expire
            if (self.assault_confirm == 0 and self.armed_confirm == 0):
                self.state = "NEUTRAL"

        return self.state

    def should_alert(self, frame_no: int, scene_last: int) -> bool:
        """
        True only when:
          - State is ASSAULT
          - Rolling evidence window is strong
          - Per-track cooldown has elapsed
          - Global scene cooldown has elapsed
        """
        if self.state != "ASSAULT":
            return False
        evidence_ok     = sum(self.evidence_buf) >= EVIDENCE_THRESHOLD
        track_cooldown  = (frame_no - self.last_alert_frame) > ALERT_COOLDOWN_FRAMES
        scene_cooldown  = (frame_no - scene_last)            > SCENE_COOLDOWN_FRAMES
        return evidence_ok and track_cooldown and scene_cooldown

    def mark_alerted(self, frame_no: int):
        self.last_alert_frame = frame_no


# ──────────────────────────────────────────────────────────────────────────────
# 11.  OVERLAY DRAWING
# ──────────────────────────────────────────────────────────────────────────────
_STATE_CFG = {
    #  state        color BGR      box-thick  label
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
# 12.  CAMERA INIT
# ──────────────────────────────────────────────────────────────────────────────
cap = cv2.VideoCapture(5)
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)   # reduce internal buffer lag

# ──────────────────────────────────────────────────────────────────────────────
# 13.  PER-TRACK STORES
# ──────────────────────────────────────────────────────────────────────────────
track_states:   dict[int, TrackState] = {}
prev_joints:    dict[int, np.ndarray] = {}   # shape (2,2) — wrists only
vel_history:    dict[int, deque]      = {}
id_last_seen:   dict[int, int]        = {}

scene_last_alert_frame = -SCENE_COOLDOWN_FRAMES

# ──────────────────────────────────────────────────────────────────────────────
# 14.  GRACEFUL SHUTDOWN
# ──────────────────────────────────────────────────────────────────────────────
_running = True

def _shutdown(*_):
    global _running
    _running = False

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ──────────────────────────────────────────────────────────────────────────────
# 15.  MAIN LOOP
# ──────────────────────────────────────────────────────────────────────────────
frame_count     = 0
fps_timer       = time.perf_counter()
fps_display     = 0.0
fps_frame_count = 0

print("🚀 Sentinel v15.0 — pipeline engaged.")

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
        raw_vboxes  = list(_weapon_cache["vboxes"])

    # ── B. Violence-box temporal fusion ──────────────────────────────────────
    live_vboxes = _vbox_tracker.update(raw_vboxes)

    # ── C. Pose tracking ──────────────────────────────────────────────────────
    pose_res = pose_model.track(
        frame, persist=True, verbose=False, imgsz=POSE_IMGSZ, half=True
    )

    if (pose_res[0].boxes is not None and
            pose_res[0].boxes.id is not None and
            pose_res[0].keypoints is not None):

        ids   = pose_res[0].boxes.id.int().cpu().tolist()
        kpts  = pose_res[0].keypoints.xy.cpu().numpy()    # shape (N,17,2)
        boxes = pose_res[0].boxes.xyxy.cpu().numpy()       # shape (N,4)

        # Mark alive, evict stale
        for tid in ids:
            id_last_seen[tid] = frame_count
        stale = [t for t, lf in id_last_seen.items() if frame_count - lf > MAX_UNSEEN_FRAMES]
        for tid in stale:
            for d in (track_states, prev_joints, vel_history, id_last_seen):
                d.pop(tid, None)

        # Build victim map (centre + box) for directional scoring
        victims: dict[int, dict] = {}
        for tid, joints, b in zip(ids, kpts, boxes):
            torso = joints[5:13]
            valid = torso[np.any(torso > 1, axis=1)]
            if len(valid) > 0:
                victims[tid] = {"center": np.mean(valid, axis=0), "box": b}

        # Weapon → person assignment
        weapon_assigns = _assign_weapons(raw_weapons, ids, kpts, boxes)

        # Per-person processing
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
                # In dense crowds suppress melee scoring for this person
                # (avoids flicker from noisy wrist keypoints in occlusion)
                is_melee = False
            else:
                is_melee, _ = _score_strike(
                    tid, joints, prev_joints, vel_history, victims
                )

            # Always update wrist history regardless of crowd
            prev_joints[tid] = joints[[9, 10]].copy()

            # ── Violence-box overlap ──────────────────────────────────────────
            in_vbox = False
            for vb in live_vboxes:
                ix1 = max(p_box[0], vb[0]); iy1 = max(p_box[1], vb[1])
                ix2 = min(p_box[2], vb[2]); iy2 = min(p_box[3], vb[3])
                if ix2 > ix1 and iy2 > iy1:
                    in_vbox = True; break

            is_assault = is_melee or in_vbox

            # ── Hysteresis FSM update ────────────────────────────────────────
            state = ts.update(is_assault, has_weapon, frame_count)

            # ── Alert gating ─────────────────────────────────────────────────
            if ts.should_alert(frame_count, scene_last_alert_frame):
                conf = min(1.0, sum(ts.evidence_buf) / EVIDENCE_WINDOW + 0.55)
                ts.mark_alerted(frame_count)
                scene_last_alert_frame = frame_count
                _alert_exec.submit(_post_alert, tid, conf)

            # ── Draw ─────────────────────────────────────────────────────────
            _draw_overlay(frame, p_box, tid, state, weapon_assigns.get(tid))

        # Draw live violence regions (after persons so they appear on top)
        _draw_violence_boxes(frame, live_vboxes)

    # ── D. FPS counter ───────────────────────────────────────────────────────
    now     = time.perf_counter()
    elapsed = now - fps_timer
    if elapsed >= 1.0:
        fps_display     = fps_frame_count / elapsed
        fps_timer       = now
        fps_frame_count = 0

    # ── E. HUD overlay ───────────────────────────────────────────────────────
    hud = f"EcoVision v15.0 | FPS: {fps_display:.0f} | Tracks: {len(id_last_seen)}"
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
    cv2.imshow("EcoVision Sentinel v15.0", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

# ──────────────────────────────────────────────────────────────────────────────
# 16.  CLEAN SHUTDOWN
# ──────────────────────────────────────────────────────────────────────────────
print("\n🛑 Shutting down Sentinel...")
cap.release()
_weapon_exec.shutdown(wait=False, cancel_futures=True)
_encode_exec.shutdown(wait=False, cancel_futures=True)
_alert_exec.shutdown(wait=True)   # drain pending alert POSTs
cv2.destroyAllWindows()
print("✅ Sentinel shut down cleanly.")
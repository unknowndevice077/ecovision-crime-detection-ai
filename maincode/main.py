import os
import re
import sys
import logging
import subprocess

# Same fix as backend.py's stream reconfigure at its own top -- see that
# comment for the full story. Applies here too: this file's own console
# logging (🔥, 🎬, ✅, ⚠️, ...) hits the identical crash risk under a plain
# cmd.exe console, and PYTHONIOENCODING/PYTHONUTF8 alone weren't reliable
# enough to trust across however this process actually gets spawned.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM LOG NOISE FILTERS (Permanently silences the 'half' deprecation warnings)
# ──────────────────────────────────────────────────────────────────────────────
class UltralyticsNoiseFilter(logging.Filter):
    def filter(self, record):
        # Intercepts the log record message string and discards it if it contains the deprecation notice
        return "'half' is deprecated" not in record.getMessage()

# Bind our custom diagnostic filter straight into the main Ultralytics logging registry
logging.getLogger("ultralytics").addFilter(UltralyticsNoiseFilter())

# OpenCV's OBSENSOR backend (for Orbbec depth cameras -- hardware this project
# does not use) probes UVC channels on every VideoCapture attempt and throws an
# untranslated C++ exception out of both open() and read() when the index has no
# device on it. That killed the AI core on startup. Must be set BEFORE cv2 is
# imported -- the videoio backend registry is built at import time.
os.environ.setdefault("OPENCV_VIDEOIO_PRIORITY_OBSENSOR", "0")

# Probing an index with no camera on it makes DSHOW log
#   "backend is generally available but can't be used to capture by index"
# at WARN. That's the normal, expected result of scanning for cameras, so a
# scan of 0-9 emitted ~9 warnings per call and buried real errors. ERROR level
# keeps genuine failures visible while dropping the expected-miss noise.
# Env var rather than cv2.setLogLevel(): that function is not exposed in every
# opencv-python build (it is absent in this one) and importing cv2 just to
# call it would be too late anyway -- the level must be set before init.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

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
try:
    # Packaged builds have port_utils.py copied next to this file (see
    # package.json extraResources), so this plain import succeeds there.
    from port_utils import find_free_port, write_runtime_port, start_parent_watchdog
except ModuleNotFoundError:
    # Running from the repo in dev: port_utils.py lives in app\, not
    # maincode\, so sys.path[0] (this file's dir) doesn't contain it.
    # run_dev_system.bat sets PYTHONPATH for this, but fall back here too so
    # invoking "python maincode/main.py" directly still works.
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
    from port_utils import find_free_port, write_runtime_port, start_parent_watchdog

# See port_utils.start_parent_watchdog's own docstring: this is the process
# holding the actual GPU models (pose, weapons, X3D x3) for as long as it
# runs, so it self-terminating promptly once Electron disappears -- rather
# than lingering until someone notices -- is the main point of this whole
# mechanism, not a nice-to-have.
start_parent_watchdog()
# ──────────────────────────────────────────────────────────────────────────────
# 0. DEPENDENCY CHECK
# ──────────────────────────────────────────────────────────────────────────────
try:
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    from typing import Optional
    import uvicorn
except ImportError:
    sys.exit("❌ Missing libs. Run: pip install fastapi uvicorn")

# ──────────────────────────────────────────────────────────────────────────────
# 0.1 ROBBERY / VANDALISM CORE DETECTION IMPORTS
# ──────────────────────────────────────────────────────────────────────────────
from robbery_vandalism import RobberyTracker, VandalismTrackState, score_vandalism
from x3d_violence_detector import (
    X3DViolenceDetector, SceneViolenceDetector, TiledSceneViolenceDetector, VIOLENCE_MODE,
    MODEL_PATH as TRACK_MODEL_PATH,
)

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
# BASE_DIR is this script's own folder (e.g. resources/maincode on a
# packaged build) -- READ-ONLY on a per-machine install without admin
# rights. config.json ships one level up from there (workspace/resources
# root) and is read fine, but ANYTHING WRITTEN back (camera index changes
# via /set_camera_index, etc.) must go to a writable location instead, or
# that write throws, the process dies, port 8001 never opens, and
# Electron's waitForPort(8001) times out -- which is what surfaces to the
# user as "Timed out waiting on port 8000" once the backend also fails
# for the identical reason.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(BASE_DIR)

# APP_ENV-aware, matching app/backend.py's loader exactly: prefer
# config.<APP_ENV>.json (e.g. config.production.json under docker-compose,
# which sets APP_ENV=production) and fall back to config.json if that
# env-specific file isn't shipped. Previously this always loaded config.json
# unconditionally, so APP_ENV=production silently had zero effect here --
# the detector container was running dev settings in "production".
def _deep_merge(base, override):
    """Recursively overlay `override` onto `base`, returning a new dict.

    Defined here because config loading needs it immediately below.
    """
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


APP_ENV = os.environ.get("APP_ENV", "development")
_ENV_CONFIG_PATH = os.path.join(WORKSPACE_ROOT, f"config.{APP_ENV}.json")
_BASE_CONFIG_PATH = os.path.join(WORKSPACE_ROOT, "config.json")

# BUG FOUND 2026-08-21 by running the pipeline end-to-end and reading which
# weights it reported loading. This used to be:
#
#   SHIPPED_CONFIG_PATH = _ENV_CONFIG_PATH if exists else config.json
#
# i.e. config.<APP_ENV>.json REPLACED config.json rather than overlaying it.
# APP_ENV defaults to "development" and config.development.json exists, so
# config.json -- the file carrying every model path, threshold, metric block
# and rollback note in this project -- was never read at all in a normal dev
# run. Both env files are stale skeletons missing detection.weapon,
# detection.robbery and detection.vandalism entirely, so all three fell through
# to hardcoded defaults. Editing config.json changed nothing, silently.
#
# Now: config.json is the BASE (structure, defaults, everything documented),
# and config.<APP_ENV>.json is an OVERLAY carrying only what that environment
# genuinely differs on. A key added to the base reaches every environment.
if not os.path.exists(_BASE_CONFIG_PATH):
    sys.exit(f"❌ Central configuration file not found at workspace root: {_BASE_CONFIG_PATH}")

with open(_BASE_CONFIG_PATH, 'r', encoding='utf-8') as f:
    sys_config = json.load(f)

SHIPPED_CONFIG_PATH = _BASE_CONFIG_PATH
if os.path.exists(_ENV_CONFIG_PATH):
    with open(_ENV_CONFIG_PATH, 'r', encoding='utf-8') as f:
        sys_config = _deep_merge(sys_config, json.load(f))
    SHIPPED_CONFIG_PATH = _ENV_CONFIG_PATH

WRITABLE_DIR = os.environ.get("ECOVISION_WRITABLE_DIR")
if not WRITABLE_DIR:
    # Standalone / dev-mode fallback -- always writable regardless of
    # where this script itself lives.
    WRITABLE_DIR = os.path.join(os.path.expanduser("~"), "EcoVisionSentinelData")
os.makedirs(WRITABLE_DIR, exist_ok=True)

# CONFIG_PATH from here on refers to the WRITABLE copy -- this is what
# gets read back in and rewritten by /set_camera_index below. If backend.py
# already created one (it starts first in electron/main.js), prefer that
# so both processes agree on secret_key / persisted settings; otherwise
# seed it from the shipped copy.
CONFIG_PATH = os.path.join(WRITABLE_DIR, "config.json")


if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, 'r') as f:
        _writable_config = json.load(f)
    # shipped = base (structure + any newly added keys),
    # writable = override (whatever this machine actually changed)
    sys_config = _deep_merge(sys_config, _writable_config)
else:
    with open(CONFIG_PATH, "w") as f:
        json.dump(sys_config, f, indent=2)

POSE_IMGSZ          = 416
# RAISED 416 -> 640 on 2026-08-21, because weapons v2 trained at 640 and a
# detector run at a resolution it was not benchmarked at is not the model whose
# numbers were published. Measured on the same 2,157-image held-out test split,
# same checkpoint, thresholds re-selected on val for each resolution:
#
#             baseline recall    @<=3% FPR budget    val->test FPR drift
#   imgsz 416     76.1%          88.3% @ 5.3%        4.9% -> 7.2%
#   imgsz 640     79.7%          89.0% @ 3.1%        4.9% -> 4.6%
#
# 640 gives MORE recall at LOWER false-positive rate -- not a trade. The drift
# column is the deciding one: at 416 the threshold chosen on validation
# overshoots its budget badly on test, i.e. the confidence distribution is not
# stable at that resolution. Any TensorRT engine must be rebuilt at 640;
# optimize_weights.py reads this constant, so it follows automatically.
WEAPON_IMGSZ        = 640
WEAPON_CONF         = sys_config["detection"].get("confidence_threshold", 0.38)
# Previously had no switch at all -- weapon detection ran unconditionally
# every DETECTION_INTERVAL frames regardless of config. Added 2026-08-19 for
# parity with violence/robbery/vandalism, which were each individually
# toggleable from the AI Models admin page while this one silently never was.
WEAPON_DETECTION_ENABLED = sys_config["detection"].get("weapon", {}).get("enabled", True)
DETECTION_INTERVAL  = sys_config["detection"].get("detection_interval", 5)

VANDAL_MARK_IMGSZ = 416
VANDAL_MARK_CONF  = sys_config["detection"].get("vandalism", {}).get(
    "marks_confidence_threshold", 0.5)

# Ignore mark detections whose centre falls in the top or bottom band of the
# frame. MEASURED 2026-08-22, not assumed -- a frame from lyns_restaurant was
# rendered with its detections drawn and the box sat squarely on the burned-in
# DVR timestamp ("12-08-2026 08:04:44 PM"). White text on a dark surface is, to
# a detector trained on photographs of tags, indistinguishable from graffiti.
#
# Detection centres by vertical position, over 10,800 sampled observations per
# real camera and every sampled frame of the four annotated graffiti videos:
#
#                             detections   in top/bottom 12%
#   Vandalism019/027/048/049       982            0.0%     <- real graffiti
#   lyns_restaurant              10710           99.5%     <- the timestamp
#   agdao_flyover                 1210           15.6%
#   agdao_market / iloilo         1512            0.0%
#
# Masking the bands removes 99.5% of lyns_restaurant's false detections and
# costs ZERO true detections across all four graffiti videos. It also explains
# the change-detection prototype's failure: a detector firing on static
# burned-in text in every frame makes "a mark appeared where there was none"
# trigger on detector flicker rather than on new paint.
#
# 0.0 disables the mask. Raise it only with evidence gathered the same way --
# by rendering a frame with its detections drawn and looking at it.
VANDAL_MARK_EDGE_BAND = sys_config["detection"].get("vandalism", {}).get(
    "marks_edge_band", 0.12)

# There is deliberately no POSE_CONF here. A `POSE_CONF = 0.30` constant used
# to sit at this spot, never passed to anything -- and wiring it into the
# pose_model.track() call below would actively hurt.
#
# ultralytics forces conf=0.1 in track mode on purpose
# (engine/model.py: `kwargs["conf"] = kwargs.get("conf") or 0.1`, commented
# "ByteTrack-based method needs low confidence predictions as input").
# BoT-SORT's second association stage re-matches lost tracks against exactly
# those sub-threshold detections; that is what carries a track ID through a
# brief occlusion instead of retiring it and issuing a new one on the far
# side. Passing conf=0.30 would discard that entire band and fragment tracks
# harder -- the precise failure behind the clips that never reach X3D.
#
# If pose detection needs tuning, tune the tracker (track_buffer,
# new_track_thresh in the botsort.yaml passed via tracker=), not conf.

# UPDATED 2026-08-21 for weapons v2. This model emits exactly three classes:
# gun, knife, phone. Every name below is one it can actually produce.
#
# NAMES REMOVED, and why -- verify_deployment.py flags a name main.py looks
# for that the loaded model cannot emit, because that is a silent no-op: it
# never matches, never errors, and reads like working coverage.
#   pistol / firearm / handgun / rifle
#       vestigial aliases from the source corpora. merge_weapons.py maps all
#       of them to "Gun", so no merged model has ever emitted them.
#   sign
#       weapons v2 has no sign class. The old one fired 0 times in 4,800
#       measured frames (it detects ROAD signs, not walls) while inflating the
#       previous model's headline recall to 88.7%. static_targets now comes
#       from the graffiti detector instead -- see _run_vandal_mark_detection.
#
# "phone" stays OUT of WEAPON_CLASSES deliberately, and this is now a real
# trained class rather than the dead name it used to be. The model detects
# phones so it stops calling them guns; main.py drops the detection, so a
# correctly-detected phone is a TRUE NEGATIVE that raises no alert.
WEAPON_CLASSES   = {"gun", "knife"}
VIOLENCE_CLASSES = {"violence", "fight", "assault"}
SIGN_CLASSES     = set()   # weapons v2 has no sign class -- see above

# THRESHOLDS CHOSEN BY MEASUREMENT 2026-08-21, not inherited.
# sweep_weapon_thresholds.py caches per-image max confidence in one inference
# pass, then evaluates every threshold pair offline. Selected on the VAL split
# and reported on TEST, because choosing an operating point on the split you
# then report manufactures an improvement with no file moving between splits.
#
# On the 2,157-image held-out test split, weapons v2 (epoch 98) at imgsz 640:
#     gun 0.52 / knife 0.45  (inherited)   79.7% recall @ 0.9% FPR
#     gun 0.30 / knife 0.23  (chosen)      89.0% recall @ 3.1% FPR
# +9.3 points of recall for 2.2 points of FPR. The old values were tuned for a
# DIFFERENT, WORSE model that needed high thresholds to suppress its own false
# positives; v2 is precise enough that it does not.
#
# 3.1% is an IMAGE-level rate and overstates live behaviour: a detection must
# still survive ARMED_CONFIRM_FRAMES=4, the 3-of-8 evidence window, and the
# static-object filter (which removed 97.4%/81.0%/78.1% of false weapons on
# three real feeds) before any alert reaches an operator.
CONF_BY_CLASS = {
    "gun":      0.30,
    "knife":    0.23,
    "violence": 0.40,
    "fight":    0.40,
    "assault":  0.40,
}
# BUG FOUND 2026-08-19, computing weapon_signs.pt's first-ever confusion
# matrix: _run_weapon_detection passed WEAPON_CONF (0.6, the top-level
# detection.confidence_threshold) straight to YOLO's own `conf=` argument,
# which discards every box below that BEFORE CONF_BY_CLASS's per-class
# check ever sees it. Knife (0.45) and sign (0.40) are both below 0.6, so
# their "lower" thresholds were unreachable dead code -- confirmed directly:
# 11 of 58 sampled detections on real test images landed in the 0.25-0.6
# gap, including gun detections at 0.583 and 0.592 (above the intended 0.52
# gun threshold) that were silently dropped. This floor is the lowest value
# anything in CONF_BY_CLASS (or the WEAPON_CONF fallback) could ever need,
# so nothing that could pass the real per-class check downstream gets cut
# off before reaching it.
WEAPON_YOLO_CONF_FLOOR = min(min(CONF_BY_CLASS.values()), WEAPON_CONF)
WEAPON_CONF_GUN_SUSTAINED = 0.35

VBOX_ASSAULT_THRESHOLD = 0.15
SCENE_COOLDOWN_ARMED   = 40
SCENE_COOLDOWN_ASSAULT = sys_config["alert"].get("cooldown_frames", 120)

WEAPON_IOU_MATCH  = 0.25
WEAPON_MAX_UNSEEN = 30

# ── static scene-object rejection (see _is_static_scene_object) ───────────
# The deployed weapon model reports fixed scene features as weapons: a utility
# pole at the tire shop scores Gun 0.93 on frame after frame. Measured removal
# on real footage: streetview1 97.4%, tireshop 81.0%, barbershop 78.1%.
# Switchable, because every mode in this system stays revertible by config.
_STATIC_CFG = sys_config["detection"].get("static_weapon_filter", {})
STATIC_WEAPON_FILTER   = _STATIC_CFG.get("enabled", True)
# Window in observations, not seconds: the weapon pass runs on its own cadence,
# so counting frames here would mean something different at every frame rate.
STATIC_WEAPON_WINDOW   = _STATIC_CFG.get("window_observations", 45)
# Minimum sightings before the rule may fire. Without it a weapon would be
# suppressed on first appearance, having "not moved yet".
STATIC_WEAPON_MIN_OBS  = _STATIC_CFG.get("min_observations", 15)
# Pixels of centre travel below which the object is considered fixed. Chosen
# above encoder jitter on a static camera (measured stdev on real footage was
# ~0.0004-0.04 of frame width, i.e. under 25px at 1280 wide for the worst case)
# and well below what a carried object covers in a couple of seconds.
STATIC_WEAPON_MOVE_PX  = _STATIC_CFG.get("move_threshold_px", 28)

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
# Fixed-pixel grip radius doesn't scale with camera distance/zoom -- a person
# close to the camera and one far away need different pixel tolerances for
# "this object is in their hand." Grip radius is now max(GRIP_THRESHOLD,
# fraction of that person's own box height) so it stays proportional.
GRIP_RADIUS_BOX_FRAC   = 0.35
# A weapon-track must be a MEANINGFULLY closer match to steal an assignment
# away from whoever it was assigned to last frame -- stops a false-positive
# weapon box from flickering between adjacent people every frame due to pose
# jitter alone. 0.8 means a new candidate has to be <80% of the previous
# holder's distance to take over.
GRIP_STICKY_MARGIN     = 0.80

ESP32_IP    = sys_config["esp32"].get("ip_override") or "192.168.254.152"

# BUG FOUND 2026-08-19: this checked BACKEND_API_URL_OVERRIDE, an env var
# NOTHING has ever set -- docker-compose.yml (which used to set it) was
# deleted when Docker support was removed, and electron/main.js's
# spawnPython() call for this process sets a DIFFERENT name, "BACKEND_URL",
# with exactly the right value (http://127.0.0.1:<actual backend port>).
# Two names for the same override, only one of them real, is exactly how
# this stayed silently broken: config.json's networking.api_url is the
# Docker-only "http://backend:8000" (a hostname that only resolves inside
# a compose network), the override that was supposed to replace it on the
# desktop build never fired, and every single call to _post_alert() below
# -- violence, robbery, vandalism, weapons, all of them -- has been POSTing
# to an unresolvable host and failing silently (caught by _post_alert's own
# except, printed to a console window nobody was watching, incident never
# created). This is almost certainly why "nothing appeared in incident
# logs" even though the AI itself detected correctly.
_BACKEND_API_URL_OVERRIDE = os.environ.get("BACKEND_URL")
if _BACKEND_API_URL_OVERRIDE:
    sys_config.setdefault("networking", {})["api_url"] = _BACKEND_API_URL_OVERRIDE

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

# The dashboard runs on :3000 and this server on :8001, so every fetch() to
# it is cross-origin. app/backend.py (:8000) has had CORS since the start, but
# this app never did -- and the omission was invisible because the one thing
# most people check, <img src=".../video_feed">, is NOT subject to CORS. Only
# the fetch()-based endpoints (/available_cameras, /set_camera_index) were
# being blocked, which surfaced as a bare "Failed to fetch" in the browser
# while curl against the same URL returned 200.
#
# Same reasoning as backend.py's allow_origin_regex: the frontend's port can
# fall back if 3000 is taken (findFreePortForFrontend in electron/main.js), so
# a fixed allowlist would break on whatever port it actually landed on. This
# is a local single-user desktop app; accepting any localhost origin costs
# nothing here.
stream_app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    preferred_port = 8001
    actual_port = find_free_port(preferred_port)
    # Key MUST be "ai_core" -- electron/main.js:584's waitForRuntimePort("ai_core")
    # polls for exactly this key. It previously said "detector" here, which never
    # matched, so Electron always concluded the AI core failed to start (even
    # though it was running fine) and aborted the launch every time.
    write_runtime_port("ai_core", actual_port)
    print(f"📡 Dynamic Stream server live → http://localhost:{actual_port}/video_feed")
    uvicorn.run(stream_app, host=sys_config["backend"]["host"], port=actual_port, log_level="error")

threading.Thread(target=_start_stream_server, daemon=True).start()

# ──────────────────────────────────────────────────────────────────────────────
# 4. HARDWARE DISCOVERY & MODEL INITIALIZATION MATRIX
# ──────────────────────────────────────────────────────────────────────────────
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# PROJECT_ROOT now points at the WRITABLE dir for anything written at
# runtime (screenshots). Model weights still ship read-only alongside the
# code, so WEIGHTS_DIR is resolved separately against the shipped
# resources root, not PROJECT_ROOT.
SHIPPED_ROOT = os.path.dirname(CURRENT_DIR)
PROJECT_ROOT = WRITABLE_DIR
WEIGHTS_DIR = os.path.join(SHIPPED_ROOT, "weights")

# IMPORTANT: these must resolve to the EXACT same physical folders that
# backend.py serves via StaticFiles, or the frontend gets 404s on every
# screenshot/clip no matter how correctly the URLs are built.
# backend.py now serves both out of ECOVISION_WRITABLE_DIR (see backend.py's
# WRITABLE_DIR), so main.py must write to that exact same writable root too
# -- NOT to the (possibly read-only) SHIPPED_ROOT.
SCREENSHOTS_DIR = os.path.join(PROJECT_ROOT, "static", "screenshots")

os.makedirs(WEIGHTS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

# Verify CUDA actually works (not just available)
USE_CUDA = False
if torch.cuda.is_available():
    try:
        # Test that CUDA actually functions (not just installed)
        test_tensor = torch.zeros(1, device='cuda')
        del test_tensor
        USE_CUDA = True
        print(f"✅ CUDA verified working on device: {torch.cuda.get_device_name(0)}")
    except Exception as e:
        print(f"⚠️  CUDA available but not working: {str(e)[:80]}")
        USE_CUDA = False

TARGET_DEVICE = "cuda" if USE_CUDA else "cpu"
print(f"📡 [HARDWARE PROFILER] Selected Execution Target: {TARGET_DEVICE.upper()}")

# ──────────────────────────────────────────────────────────────────────────────
# 3.5 ENGINE LOADER WITH GRACEFUL FALLBACK (GPU → CPU)
# ──────────────────────────────────────────────────────────────────────────────
def load_model_with_fallback(engine_name: str, pt_name: str, task: str, weights_dir: str):
    """
    Attempts to load a TensorRT engine if CUDA is available.
    Falls back to PyTorch .pt file if engine fails to initialize or CUDA unavailable.
    If neither exists, attempts to download the PT file from Ultralytics.
    """
    engine_path = os.path.join(weights_dir, engine_name)
    pt_path = os.path.join(weights_dir, pt_name)
    
    # Prefer engine only if CUDA available AND file exists
    use_engine = USE_CUDA and os.path.exists(engine_path)
    
    if use_engine:
        try:
            print(f"🔄 Attempting TensorRT engine: {engine_name}")
            model = YOLO(engine_path, task=task)
            # Warm-up to catch CUDA init errors early (don't force GPU device, let it use what's available)
            _dummy = np.zeros((416, 416, 3), dtype=np.uint8)
            model.predict(_dummy, verbose=False, imgsz=416, device="cuda:0" if USE_CUDA else "cpu")
            print(f"✅ TensorRT engine loaded: {engine_name}")
            return model, engine_name
        except Exception as e:
            print(f"⚠️  TensorRT engine failed ({engine_name}): {str(e)[:100]}")
            print(f"   Falling back to PyTorch model: {pt_name}")
    
    # Check if PT file exists, if not Ultralytics will download it
    if not os.path.exists(pt_path):
        print(f"📦 {pt_name} not found locally, Ultralytics will attempt to download it...")
    else:
        print(f"📦 Loading PyTorch model: {pt_name}")
    
    # Fallback to PT file (works on both GPU and CPU, downloads if missing)
    try:
        model = YOLO(pt_path, task=task)
        return model, pt_name
    except Exception as e:
        print(f"❌ Failed to load {pt_name}: {str(e)}")
        raise

def _resolve_weight(value, default):
    """Accept either a bare filename or a workspace-relative path.

    Two conventions are in use in config.json and they disagreed silently.
    detection.violence.model_path is written "weights/x3d_...pt" (relative to
    the workspace root), while detection.vandalism.marks_model_path was
    consumed as a BARE filename joined onto WEIGHTS_DIR. Feeding a
    "weights/..."-style value into the second produced
    <root>/weights/weights/<file>, which does not exist -- and because the
    caller only checks existence before falling back to a default, the wrong
    model loaded with no error and a reassuring log line.

    Accepting both forms removes the trap rather than documenting it.
    """
    v = value or default
    if "/" in v or os.path.sep in v:
        cand = os.path.join(WORKSPACE_ROOT, v.replace("/", os.path.sep))
        if os.path.exists(cand):
            return cand
        v = os.path.basename(v)          # fall through to WEIGHTS_DIR
    return os.path.join(WEIGHTS_DIR, v)


pose_model, pose_file_name = load_model_with_fallback(
    "yolo11s-pose.engine", "yolo11s-pose.pt", "pose", WEIGHTS_DIR
)

# WEAPON MODEL NAME NOW COMES FROM CONFIG, not a literal.
# BUG FOUND 2026-08-21 by running the pipeline end-to-end and reading which
# weights it reported loading: this call hardcoded "weapon_signs.pt", so
# detection.weapon.model_path in config.json was decorative -- editing it
# changed nothing, exactly like the documented-dead database.path key. The
# system loaded the old detector no matter what the config said, and nothing
# anywhere reported a conflict.
#
# BUG FOUND 2026-09-03 (full account/feature sweep): this loaded
# unconditionally regardless of detection.weapon.enabled. robbery
# (ROBBERY_ON, below) already skips its own model load when off; this never
# matched that pattern, so a deployment that turns weapon detection off still
# pays its VRAM and startup-time cost for nothing -- _run_weapon_detection's
# only call site (frame loop, below) already gates on WEAPON_DETECTION_ENABLED,
# so leaving violence_model/weapon_file_name as None here is safe: the
# warmup calls a few lines down are gated on WEAPON_DETECTION_ENABLED too.
_WEAPON_DETECTION_ENABLED_EARLY = bool(
    sys_config["detection"].get("weapon", {}).get("enabled", True))
if _WEAPON_DETECTION_ENABLED_EARLY:
    _WEAPON_PT = os.path.basename(_resolve_weight(
        sys_config["detection"].get("weapon", {}).get("model_path"),
        "weapon_signs.pt"))
    _WEAPON_ENGINE = os.path.splitext(_WEAPON_PT)[0] + ".engine"
    violence_model, weapon_file_name = load_model_with_fallback(
        _WEAPON_ENGINE, _WEAPON_PT, "detect", WEIGHTS_DIR
    )
else:
    violence_model, weapon_file_name = None, None
    print("🚫 Weapon: disabled via config.json detection.weapon.enabled -- model not loaded")

# Vandalism-marks detector (graffiti/tag). Separate small YOLO model, not part
# of weapon_signs.pt -- see detection.vandalism.marks_model_path in config.json.
# Guarded on file existence (unlike load_model_with_fallback's other callers)
# because this is a custom weight name Ultralytics has no hub fallback for; a
# missing file must disable the signal, not attempt a network download that
# will just fail.
#
# BUG FOUND 2026-09-03 (full account/feature sweep): loaded unconditionally
# on file existence alone, regardless of detection.vandalism.enabled -- the
# only call site (_run_vandal_mark_detection, submitted further down) already
# guards on "VANDALISM_ON and vandal_mark_model is not None", and the
# per-camera override further down already sets this back to None when a
# camera's own override disables vandalism_marks, so leaving it None here
# when the GLOBAL switch is off is exactly the same safe shape, just at
# load time instead of after the fact. VANDALISM_ON itself is computed again,
# identically, a bit further down (where the rest of the vandalism setup
# lives) -- duplicated rather than moved, so this section doesn't have to
# reorder anything to see a variable that's normally defined after it.
_VANDALISM_ENABLED_EARLY = bool(
    sys_config.get("detection", {}).get("vandalism", {}).get("enabled", False))
_VANDAL_MARKS_PATH = _resolve_weight(
    sys_config["detection"].get("vandalism", {}).get("marks_model_path"),
    "vandalism_marks.pt")
vandal_mark_model = None
if not _VANDALISM_ENABLED_EARLY:
    print("🚫 Vandalism-marks model not loaded -- detection.vandalism.enabled is false")
elif os.path.exists(_VANDAL_MARKS_PATH):
    try:
        vandal_mark_model = YOLO(_VANDAL_MARKS_PATH, task="detect")
        print(f"📦 Loaded vandalism-marks model: {os.path.basename(_VANDAL_MARKS_PATH)}")
    except Exception as e:
        print(f"⚠️  Failed to load vandalism-marks model: {str(e)[:100]}")
else:
    print(f"🚫 Vandalism-marks model not found at {_VANDAL_MARKS_PATH} -- "
          f"static_targets for the vandalism rule will be empty")

# Comes from detection.violence.model_path in config.json, not a literal here.
# This line used to hardcode weights/x3d_xs_violence_best.pt, which happened to
# equal the configured value -- so the config key looked like it worked while
# editing it did nothing. Deploying a new checkpoint is exactly when that bites.
x3d_model_path = TRACK_MODEL_PATH
x3d_detector   = X3DViolenceDetector(model_path=x3d_model_path, device=TARGET_DEVICE)

# Scene (whole-frame) violence detection -- see detection.violence.mode.
#
# The per-track detector only ever classifies a person whose track ID survives
# MIN_BUFFER_FOR_INFERENCE frames; on the held-out set that gate silently
# skipped 17.2% of clips. Scene mode classifies the frame itself, so detection
# no longer depends on tracking succeeding. Measured clean held-out
# (leakage excluded):
#     per-track : 69.5 acc / 65.5 recall / 71.8 prec / 26.3 FPR
#     scene     : 79.0 acc / 76.0 recall / 81.2 prec / 18.0 FPR
#
# It is also CHEAPER: one X3D forward per check_interval frames for the whole
# frame, versus one per tracked person. Cost stops scaling with crowd size.
def _pip_crop_from_box(frame, p_box, pad_frac: float = 0.25):
    """Padded crop of one person, for the picture-in-picture panel.

    Scene mode does not populate X3DViolenceDetector's per-track crop cache
    (it never calls update()), so the PIP needs its own source. Deliberately
    NOT _crop_person(): that mutates detector state (_active_crop_boxes) and
    does the bystander-merge search, neither of which is wanted for a display
    thumbnail.
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in p_box]
    px, py = int((x2 - x1) * pad_frac), int((y2 - y1) * pad_frac)
    x1, y1 = max(0, x1 - px), max(0, y1 - py)
    x2, y2 = min(w, x2 + px), min(h, y2 + py)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2].copy()


# BUG FOUND 2026-08-23: weapon (WEAPON_DETECTION_ENABLED), robbery
# (ROBBERY_ON) and vandalism (VANDALISM_ON) all gate their model calls AND
# their alert emission on detection.<class>.enabled -- violence never did.
# The DevTeam "AI Models" toggle wrote enabled:false to config.json (and,
# separately, PATCHed the right file after the WRITABLE_CONFIG_PATH fix
# earlier the same day), the panel showed "Off", and the violence detector
# kept running and alerting exactly as before regardless, because nothing in
# this file ever read detection.violence.enabled. Absent means on, matching
# backend.py's own "violence has no explicit flag historically -- absent
# means on" comment on the API side.
VIOLENCE_ON = bool(sys_config.get("detection", {}).get("violence", {}).get("enabled", True))
if not VIOLENCE_ON:
    print("🚫 Physical Violence: disabled via config.json detection.violence.enabled")

SCENE_MODE_ON = VIOLENCE_MODE in ("scene", "tiled", "both")
scene_detector = None
# BUG FOUND 2026-09-03 (full account/feature sweep): this loaded on
# SCENE_MODE_ON alone -- independent of VIOLENCE_ON, so a deployment with
# violence disabled still paid to load this model (measured live: two
# separate X3D checkpoints loaded onto the GPU with Physical Violence
# switched off). The only call site (frame loop, below) already checks
# "SCENE_MODE_ON and VIOLENCE_ON" before calling scene_detector.update(), and
# the per-camera threshold-override loop further down already treats a None
# scene_detector as "skip" -- so this was always safe to leave unloaded when
# VIOLENCE_ON is false, it just never actually did.
if SCENE_MODE_ON and VIOLENCE_ON:
    if VIOLENCE_MODE == "tiled":
        # Full camera coverage via an overlapping tile grid, for wide city
        # cameras where a person is too small for a single whole-frame pass
        # to see (measured: 0/30 detected at 9% person-height in scene mode
        # vs 30/30 tiled, on the scale-augmented weights -- see
        # docs/progress_report_violence_detection.md §6). Grid/interval come
        # from config.json (tile_grid/tile_overlap/tile_check_interval),
        # which default to the measured real-time-viable point (grid=3,
        # interval=20 -> 1.17x real-time on a GTX 1660 SUPER); a naive 4x4
        # grid at the original 15-frame interval measured 0.58x -- unable to
        # sustain even one live camera.
        scene_detector = TiledSceneViolenceDetector(device=TARGET_DEVICE)
    else:
        scene_detector = SceneViolenceDetector(device=TARGET_DEVICE)
    print(f"🎬 Violence mode: {VIOLENCE_MODE} (whole-frame detection active)")
elif not VIOLENCE_ON:
    pass  # already printed "🚫 Physical Violence: disabled" above -- a mode line here would contradict it
else:
    print(f"🎬 Violence mode: {VIOLENCE_MODE} (per-track detection)")

# ── Robbery: a trained model, replacing the rule-based placeholder ─────────
#
# Same class as the violence scene detector -- SceneViolenceDetector is generic
# over its checkpoint -- running on the same frames with its own threshold and
# its own confirmation counter. That independence is the reason robbery is a
# separate model rather than a fourth softmax class: a robbery involving
# assault is genuinely BOTH, and one softmax would force the probability to
# split between them on exactly the clips that matter most.
#
# Cost is a second X3D-XS forward on an already-decoded frame, ~8-10 ms on the
# 1660 SUPER, and only every check_interval frames.
VANDALISM_ON = bool(sys_config.get("detection", {})
                    .get("vandalism", {}).get("enabled", False))
if not VANDALISM_ON:
    print("🚫 Vandalism: disabled -- see config detection.vandalism._why_disabled. "
          "v3 (deployed here, off) measures 21.75 false alarms/hr on four real "
          "held-out cameras, down from 125.25 once real Davao street footage was "
          "added as negatives. The violence detector runs at 4.50/hr, so this is "
          "still ~5x too noisy to put in front of an operator.")

# VANDALISM MODEL, wired 2026-08-22. Until now detection.vandalism.model_path
# was named in config and loaded by nothing: VANDALISM_ON gated only the
# rule-based path, so enabling the class ran an 8.3%-recall rule while the
# config advertised the trained model's numbers. Same shape as the weapon
# model_path key that was decorative until today.
_VANDAL_CFG = sys_config.get("detection", {}).get("vandalism", {})
vandalism_detector = None
if VANDALISM_ON and _VANDAL_CFG.get("model_path"):
    try:
        vandalism_detector = SceneViolenceDetector(
            model_path=os.path.normpath(os.path.join(
                os.path.dirname(BASE_DIR), _VANDAL_CFG["model_path"])),
            device=TARGET_DEVICE,
            threshold=float(_VANDAL_CFG.get("confidence_threshold", 0.7)),
            consecutive=int(_VANDAL_CFG.get("consecutive_required", 3)))
        print(f"🎨 Vandalism model: {_VANDAL_CFG['model_path']} "
              f"@ {_VANDAL_CFG.get('confidence_threshold', 0.7)} "
              f"(6.75 false alarms/hr measured on 4 held-out cameras)")
    except Exception as e:
        # Same policy as robbery: loud, but never take the rest down.
        print(f"⚠️  Vandalism model failed to load ({e}); "
              f"the rule-based path remains available")
        vandalism_detector = None

_ROBBERY_CFG = sys_config.get("detection", {}).get("robbery", {})
ROBBERY_ON = bool(_ROBBERY_CFG.get("enabled", False))
robbery_detector = None
if ROBBERY_ON:
    try:
        # Resolved against the REPO ROOT, matching how
        # x3d_violence_detector.py resolves detection.violence.*model_path.
        # BASE_DIR is maincode/, so joining there would look one level too deep
        # and fail only at runtime, on the machine that has the weights.
        robbery_detector = SceneViolenceDetector(
            model_path=os.path.normpath(os.path.join(
                os.path.dirname(BASE_DIR), _ROBBERY_CFG["model_path"])),
            device=TARGET_DEVICE,
            threshold=float(_ROBBERY_CFG.get("confidence_threshold", 0.7)),
            consecutive=int(_ROBBERY_CFG.get("consecutive_required", 3)))
        print(f"🛍️  Robbery model: {_ROBBERY_CFG['model_path']} "
              f"@ {_ROBBERY_CFG.get('confidence_threshold', 0.7)}")
    except Exception as e:
        # Fail loudly but keep the rest of the system up: a missing robbery
        # checkpoint must not take violence detection down with it.
        print(f"⚠️  Robbery model failed to load ({e}); "
              f"falling back to the rule-based path")
        ROBBERY_ON = False

print(f"📦 [ENGINE LOADER] Using Pose Pipeline: {pose_file_name}")
print(f"📦 [ENGINE LOADER] Using Weapon Pipeline: {weapon_file_name}")

# NOTE: do NOT call .model.half() here. Ultralytics converts to fp16 itself
# when half=True is passed to predict()/track(), and it does so in the right
# ORDER -- it fuses conv+bn layers FIRST, then halves. Pre-halving the
# weights here meant fuse_conv_and_bn() later hit fp16 conv weights against
# fp32 batchnorm params and died with
#   "expected mat1 and mat2 to have the same dtype: c10::Half != float"
# on the very first warmup predict. Passing half= to the inference calls
# (below, and in _run_weapon_detection / the pose track call) still gets the
# full fp16 speedup -- verified the predictor model ends up torch.float16.
if pose_file_name.endswith(".pt"):
    pose_model.to(TARGET_DEVICE)

if violence_model is not None and weapon_file_name.endswith(".pt"):
    violence_model.to(TARGET_DEVICE)

_dummy = np.zeros((POSE_IMGSZ, POSE_IMGSZ, 3), dtype=np.uint8)
pose_model.predict(_dummy, verbose=False, imgsz=POSE_IMGSZ, half=(USE_CUDA and pose_file_name.endswith(".pt")))
if violence_model is not None:
    violence_model.predict(_dummy, verbose=False, imgsz=WEAPON_IMGSZ, half=(USE_CUDA and weapon_file_name.endswith(".pt")))
print("✅ Dynamic relative weights successfully loaded and warmed up.")

# ──────────────────────────────────────────────────────────────────────────────
# 5. THREAD POOL EXECUTORS
# ──────────────────────────────────────────────────────────────────────────────
_weapon_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weapon")
_vandal_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vandal_marks")
_encode_exec   = ThreadPoolExecutor(max_workers=1, thread_name_prefix="encode")
_alert_exec    = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alert")

_weapon_future = None
_vandal_future = None
_encode_future = None

_weapon_lock  = threading.Lock()
_weapon_cache = {"weapons": [], "vboxes": []}

_vandal_mark_lock  = threading.Lock()
_vandal_mark_cache = {"boxes": []}

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
_weapon_grip_sticky:   dict[int, int]  = {}   # weapon-track wid -> person tid it's currently gripped by

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
            t = _weapon_track_store[best_wid]
            t.update({"box": w_box, "conf": raw_conf, "center": w["center"], "unseen": 0})
            t["pos_hist"].append(w["center"])
        else:
            _weapon_track_store[_weapon_track_counter] = {
                "name": cls_name, "box": w_box, "conf": raw_conf, "center": w["center"], "unseen": 0,
                # Position history drives the static-object rejection below.
                "pos_hist": deque([w["center"]], maxlen=STATIC_WEAPON_WINDOW),
            }
            _weapon_track_counter += 1

    stale = [wid for wid, t in _weapon_track_store.items() if t["unseen"] > WEAPON_MAX_UNSEEN]
    for wid in stale:
        del _weapon_track_store[wid]
        _weapon_grip_sticky.pop(wid, None)   # clear stale grip-assignment memory too

    return [{**t, "wid": wid} for wid, t in _weapon_track_store.items()
            if not _is_static_scene_object(t)]


def _is_static_scene_object(track: dict) -> bool:
    """True if this weapon track has never moved -- i.e. it is scene furniture.

    MEASURED, not assumed. Sampling the deployed detector across whole clips,
    box centres barely move:

        camera        class  n   centre stdev (fraction of frame)
        tireshop      Gun    68  (0.0209, 0.0111)
        newcam2       Knife  55  (0.0386, 0.0000)
        streetview1   Knife  16  (0.0004, 0.0003)

    Inspecting the tireshop frames directly: the detector locks onto a utility
    pole -- a box 48% of frame width by 100% of frame height -- and reports it
    as a Gun at 0.93 confidence, frame after frame. Its "6,438 detections/hour"
    was one stuck detection re-counted, not thousands of distinct errors.

    A carried weapon moves; a pole, sign or hanging tyre does not. Replaying
    this rule over real footage removed 97.4% / 81.0% / 78.1% of detections on
    streetview1 / tireshop / barbershop.

    THE COST, stated rather than hidden: a genuinely motionless weapon is
    suppressed -- a knife left on a table, or someone standing very still
    holding a gun for longer than the window. Two things bound that risk: the
    window is only a few seconds, and the moment the object moves it is
    released (the spread check uses a rolling window, so it does not stay
    suppressed once it starts moving). For a streetlight watching a public
    street, an object that never moves for seconds on end is far more likely to
    be part of the scene than a threat -- but this is a trade, not a free win.

    Note newcam2 (a market) only dropped 23.6%: its knife detections DO move,
    consistent with vendors genuinely handling knives. The filter leaves those
    alone, which is the correct behaviour -- and a reminder that a real knife in
    a market is a detection problem this rule cannot and should not solve.
    """
    if not STATIC_WEAPON_FILTER:
        return False
    hist = track.get("pos_hist")
    # Too few observations to judge. Suppressing here would reject every weapon
    # the instant it first appears, which is exactly backwards.
    if not hist or len(hist) < STATIC_WEAPON_MIN_OBS:
        return False
    xs = [p[0] for p in hist]
    ys = [p[1] for p in hist]
    spread = max(max(xs) - min(xs), max(ys) - min(ys))
    return spread < STATIC_WEAPON_MOVE_PX

# ──────────────────────────────────────────────────────────────────────────────
# 8. WEAPON DETECTION THREAD WORKER
# ──────────────────────────────────────────────────────────────────────────────
def _run_weapon_detection(frame_copy):
    res = violence_model.predict(frame_copy, verbose=False, conf=WEAPON_YOLO_CONF_FLOOR, imgsz=WEAPON_IMGSZ, half=(USE_CUDA and weapon_file_name.endswith(".pt")))
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

def _run_vandal_mark_detection(frame_copy):
    """Single-frame graffiti/tag detector -- feeds score_vandalism()'s
    static_targets, replacing weapon_signs.pt's "sign" class, which fired
    zero times in 4,800 measured frames because it detects road signs, not
    walls/gates/shutters (see the VANDALISM FILTER ANALYSIS comment below).
    """
    res = vandal_mark_model.predict(frame_copy, verbose=False, conf=VANDAL_MARK_CONF,
                                     imgsz=VANDAL_MARK_IMGSZ, half=USE_CUDA)
    boxes = []
    fh = frame_copy.shape[0]
    if res[0].boxes:
        for box in res[0].boxes:
            raw_conf = float(box.conf[0].cpu())
            if raw_conf >= VANDAL_MARK_CONF:
                xyxy = box.xyxy[0].cpu().numpy().astype(int)
                cy = (int(xyxy[1]) + int(xyxy[3])) / 2.0 / max(fh, 1)
                if cy < VANDAL_MARK_EDGE_BAND or cy > 1.0 - VANDAL_MARK_EDGE_BAND:
                    continue          # DVR overlay band -- see constant
                boxes.append(xyxy)
    with _vandal_mark_lock:
        _vandal_mark_cache["boxes"] = boxes

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

def _assign_weapons(active_weapons, ids, kpts, boxes, sticky_assign: dict):
    """
    Assigns each detected weapon to the nearest wrist, gated by a grip
    radius that scales with that person's own box size (not a fixed pixel
    count -- a fixed radius is either too loose up close or too tight far
    from the camera). Also keeps a per-weapon-track "sticky" memory: once
    weapon-track `wid` is assigned to track `tid`, a DIFFERENT track has to
    be meaningfully closer (not just marginally, from pose jitter) to steal
    it. This is what stops a false-positive weapon box from bouncing between
    two nearby standing people frame-to-frame.
    """
    assignments: dict[int, list] = {tid: [] for tid in ids}

    box_by_tid = {tid: b for tid, b in zip(ids, boxes)}
    wrists_by_tid = {}
    for tid, joints in zip(ids, kpts):
        wrists = joints[[9, 10]]
        valid = wrists[np.any(wrists > 1, axis=1)]
        if len(valid) > 0:
            wrists_by_tid[tid] = valid

    for weapon in active_weapons:
        wid = weapon.get("wid")
        w_center = np.array(weapon["center"])

        candidates = {}
        for tid in ids:
            if tid not in wrists_by_tid:
                continue
            p_box = box_by_tid[tid]
            box_h = max(p_box[3] - p_box[1], 1)
            grip_radius = max(GRIP_THRESHOLD, box_h * GRIP_RADIUS_BOX_FRAC)
            dist = float(np.min(np.linalg.norm(wrists_by_tid[tid] - w_center, axis=1)))
            if dist <= grip_radius:
                candidates[tid] = dist

        if not candidates:
            if wid is not None:
                sticky_assign.pop(wid, None)   # nobody's wrist is close enough -- drop any memory
            continue

        best_tid = min(candidates, key=candidates.get)

        prev_tid = sticky_assign.get(wid) if wid is not None else None
        if prev_tid is not None and prev_tid in candidates:
            if candidates[prev_tid] <= candidates[best_tid] / GRIP_STICKY_MARGIN:
                best_tid = prev_tid   # previous holder is still close enough -- don't steal it

        if wid is not None:
            sticky_assign[wid] = best_tid

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
        # BUG FOUND 2026-08-19: these were camelCase (barangayId,
        # screenshotPath), but backend.py's AiTriggerSchema declares
        # barangay_id / screenshot_path and, like every FastAPI/Pydantic
        # model here, silently ignores unrecognized fields rather than
        # erroring. barangayId happened to be harmless -- barangay_id
        # already defaults to "cogon" -- but screenshotPath was silently
        # dropped every time, so no AI-triggered incident has ever actually
        # carried its snapshot: CrimeReportsView.tsx's report-filing modal
        # was always falling back to the picsum placeholder instead.
        payload = {
            "id": str(incident_id),
            "event": event,
            "confidence": round(conf, 4),
            # BUG FOUND 2026-09-03: hardcoded to "cogon" for every camera on
            # every AI core -- see CAMERA_BARANGAY_ID's own comment above for
            # the full story. Every AI-triggered incident's notify_targets
            # routing (app/notifications.py) depends on this being right: a
            # camera in a different barangay had its detections silently
            # notifying cogon's responders instead of its own.
            "barangay_id": CAMERA_BARANGAY_ID,
            # Was hardcoded in backend.py to "Cogon Core Smartpole Node"
            # regardless of which camera actually saw this -- every incident
            # said the same location even with only one real camera running.
            # Sourced from config.json's camera.name so it's whatever this
            # camera is actually called instead of a placeholder.
            "location_name": CAMERA_NAME,
            # Added 2026-09-03: the real camera row id, so the dashboard's
            # Incident Queue can show/link the actual camera instead of the
            # frontend guessing one from a substring match on location_name
            # (see page.tsx's old cameraLinkId heuristic, now deleted).
            "camera_id": _camera_id,
        }
        # screenshot_path is a URL-relative path like "/static/screenshots/snap_XXXX.jpg" --
        # the backend needs to persist this on the incident record so CrimeReportsView.tsx's
        # `inc.screenshot_path` (and the report-filing modal's `reportImageUrl`) have something
        # real to render instead of falling back to the picsum placeholder.
        if screenshot_path:
            payload["screenshot_path"] = screenshot_path
        r = requests.post(BACKEND_URL, json=payload, timeout=2.0)
        print(f"   ✅ Backend {r.status_code}: {r.text[:120]}")
    except Exception as e:
        print(f"   ❌ Backend unreachable: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# 12. PER-TRACK STATE MACHINE
# ──────────────────────────────────────────────────────────────────────────────
class TrackState:
    __slots__ = ("state", "assault_confirm", "assault_release", "armed_confirm", "armed_release",
                 "evidence_buf", "last_alert_frame", "active_incident_id", "episode_end_frame")
    def __init__(self):
        self.state            = "NEUTRAL"
        self.assault_confirm  = 0
        self.assault_release  = 0
        self.armed_confirm    = 0
        self.armed_release    = 0
        self.evidence_buf     = deque(maxlen=EVIDENCE_WINDOW)
        self.last_alert_frame = -ALERT_COOLDOWN_FRAMES
        # BUG FOUND 2026-08-19: should_alert() below used to fire again every
        # ALERT_COOLDOWN_FRAMES for as long as `state` stayed ASSAULT/ARMED --
        # a scene sitting right at the confirm threshold (real observed case:
        # a fallback webcam scoring ~0.50 continuously) never released, so it
        # minted a brand-new incident_id, a brand-new clip, and a brand-new DB
        # row every cooldown period, forever. 239+ incidents from one
        # continuous "episode" that was never actually 239 separate events.
        # active_incident_id makes an episode a real, trackable thing: set
        # once when it starts, held while state stays ASSAULT/ARMED, cleared
        # only on a genuine release back to NEUTRAL -- so should_alert() can
        # refuse to fire again for an episode that never ended.
        self.active_incident_id = None
        self.episode_end_frame  = -ALERT_COOLDOWN_FRAMES

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

        if self.state == "NEUTRAL" and self.active_incident_id is not None:
            # Genuine release -- this episode is over. The NEXT confirm (if
            # any) starts a fresh incident, not a continuation of this one.
            self.active_incident_id = None
            self.episode_end_frame  = frame_no
        return self.state

    def should_alert(self, frame_no: int, scene_last: int, scene_cooldown: int = SCENE_COOLDOWN_ASSAULT) -> bool:
        if self.state != "ASSAULT" and self.state != "ARMED":
            return False
        if self.active_incident_id is not None:
            # Same ongoing episode as an already-posted alert -- this is the
            # fix: no re-fire just because ALERT_COOLDOWN_FRAMES elapsed
            # while state never actually left ASSAULT/ARMED.
            return False
        evidence_ok    = True if self.state == "ARMED" else (sum(self.evidence_buf) >= EVIDENCE_THRESHOLD)
        # Debounces flapping (state dropping to NEUTRAL and immediately back
        # up on noisy frames) rather than gating a still-continuous episode,
        # which active_incident_id above already owns.
        debounce_ok    = (frame_no - self.episode_end_frame) > ALERT_COOLDOWN_FRAMES
        scene_ok       = (frame_no - scene_last) > scene_cooldown
        return evidence_ok and debounce_ok and scene_ok

    def mark_alerted(self, frame_no: int, incident_id: str):
        self.last_alert_frame   = frame_no
        self.active_incident_id = incident_id

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

_ALERT_BANNER_COLOR = {
    "ASSAULT":      (0, 0, 255),      # red, matches _STATE_CFG's ASSAULT
    "ARMED THREAT": (0, 165, 255),    # orange, matches _STATE_CFG's ARMED
    "ROBBERY":      (0, 0, 255),
    "VANDALISM":    (0, 140, 255),
}

def _draw_alert_banner(frame, event: str, conf: float):
    """Stamps the evidence snapshot with the alert that actually fired.

    WHY: the snapshot used to be the raw annotated frame, whose per-person
    boxes show each TRACK's state -- and a track only turns red after
    ASSAULT_CONFIRM_FRAMES. Scene-mode alerts (the frame is violent but no
    track is held long enough, which is the whole reason scene mode exists)
    therefore produced evidence images with everyone still boxed GREEN, on
    an incident the system was simultaneously reporting as an assault. The
    image contradicted the alert attached to it.

    Drawn on a COPY of the frame at snapshot time, never the live frame --
    stamping the real frame would flash a red border through the operator's
    video feed and into the event clip for one frame.
    """
    color = _ALERT_BANNER_COLOR.get(event, (0, 0, 255))
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), color, 6)
    label = f"{event}  {conf * 100:.1f}%"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
    # Offset below the FPS/tracks HUD, which is already drawn at the very
    # top-left by the time a snapshot is taken.
    y0 = 30
    cv2.rectangle(frame, (0, y0), (tw + 24, y0 + th + 18), color, -1)
    cv2.putText(frame, label, (12, y0 + th + 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

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
# 14. CAMERA INIT (LOCAL INDEX *OR* NETWORK URL)
# ──────────────────────────────────────────────────────────────────────────────
# A camera source is either a local device index (int) or a stream URL (str).
# Both go through the same open/reconnect path so the rest of the pipeline --
# pose, X3D, clip capture, the MJPEG relay -- never has to know the difference.
#
# The URL case is what makes this deployable beyond one lab machine: real
# barangay CCTV is IP-based, and an RTSP URL is the one interface essentially
# every vendor exposes (Hikvision, Dahua, Tapo, Uniview, ONVIF-generic). HTTP
# MJPEG and a plain file path work too, since FFmpeg treats them alike.
res_w, res_h = map(int, sys_config["camera"]["default_resolution"].lower().split('x'))

_URL_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://", "rtmp://", "udp://", "tcp://")


def _normalise_source(raw):
    """Coerces a config/env/API value into either an int index or a URL string.

    A digit string means a local index -- config files and JSON bodies both
    tend to stringify numbers, and opening index "0" as a *filename* fails in
    a way that looks like a dead camera rather than a type mistake."""
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    if s.isdigit():
        return int(s)
    return s


def _is_network_source(src):
    return isinstance(src, str) and src.lower().startswith(_URL_SCHEMES)


# Precedence: env override > explicit camera.source > legacy camera.index.
# The env override exists so one machine can be pointed at a different camera
# for a demo without editing (and accidentally committing) config.json.
_configured_source = (
    os.environ.get("CAMERA_SOURCE", "").strip()
    or sys_config["camera"].get("source")
    or sys_config["camera"].get("index", 5)
)
camera_source = _normalise_source(_configured_source)

# Sent with every AI-triggered alert as location_name -- see the comment on
# _post_alert's payload for why this replaced a hardcoded string.
#
# 2026-08-19: resolved from the REAL, currently-registered camera name
# (GET /api/camera_name/{camera_id}) when camera.camera_id is set, so a
# barangay renaming a camera in the Cameras tab actually changes what shows
# on incidents -- config.json's static camera.name is now only the fallback
# for when that lookup can't happen (no camera_id configured, or the
# backend isn't reachable yet this early in startup).
CAMERA_NAME = sys_config["camera"].get("name", "Cogon Core Smartpole Node")
# BUG FOUND 2026-09-03: _post_alert (below) used to hardcode barangay_id to
# "cogon" for every AI-triggered incident, regardless of which camera/pole
# actually saw it -- see that comment for the full story. This is that same
# name-resolution round-trip's fallback, matching CAMERA_NAME's own pattern:
# "cogon" only when camera_id isn't configured or the backend can't be
# reached this early in startup, same as the name fallback above it.
CAMERA_BARANGAY_ID = "cogon"
_camera_id = sys_config["camera"].get("camera_id")
# Per-camera detector overrides (docs/incident_response_plan.md-adjacent
# feature, backend.py's camera_model_config table): a barangay can turn a
# detector off for THIS specific camera while leaving it on globally. Empty
# dict = no override for any class = every *_ON flag below keeps whatever
# the global config.json already gave it, so a camera with no camera_id
# configured (or an unreachable backend) behaves exactly as before this
# feature existed.
_camera_model_overrides = {}
# Same absent-means-default guard as _camera_model_overrides: initialized
# empty here so the per-camera-threshold block below never references a
# variable that a failed/unreachable request left undefined.
_camera_threshold_overrides = {}
if _camera_id:
    # BUG FOUND 2026-09-03 (user report: incidents showing a "made up" name
    # instead of the real registered camera): this was a single attempt.
    # electron/main.js starts the backend and the AI core as separate
    # processes with no readiness handshake between them, so a slow backend
    # boot (loading the DB, the maintenance scheduler, the telegram poller)
    # could still be starting up at the exact moment this ran -- and once it
    # fell through to config.json's static fallback name/barangay, it stayed
    # wrong for the rest of the session (this only runs once, at import
    # time). A few retries with a short backoff covers that race without
    # meaningfully delaying startup in the normal case where the backend is
    # already up and the first attempt just succeeds immediately.
    _CAMERA_RESOLVE_ATTEMPTS = 5
    _CAMERA_RESOLVE_BACKOFF_S = 1.5
    for _attempt in range(1, _CAMERA_RESOLVE_ATTEMPTS + 1):
        try:
            _resp = requests.get(f"{sys_config['networking']['api_url'].rstrip('/')}/api/camera_name/{_camera_id}", timeout=3.0)
            if _resp.ok:
                _cam_data = _resp.json()
                CAMERA_NAME = _cam_data["name"]
                CAMERA_BARANGAY_ID = _cam_data.get("barangay_id") or CAMERA_BARANGAY_ID
                _camera_model_overrides = _cam_data.get("models") or {}
                _camera_threshold_overrides = _cam_data.get("thresholds") or {}
                print(f"📷 [CAMERA] Resolved live name for camera_id={_camera_id!r}: {CAMERA_NAME!r} "
                      f"(barangay_id={CAMERA_BARANGAY_ID!r})")
                _disabled_here = [k for k, v in _camera_model_overrides.items() if not v]
                if _disabled_here:
                    print(f"📷 [CAMERA] Per-camera overrides for {_camera_id!r}: disabled here -> {_disabled_here}")
                break
            else:
                print(f"⚠️  [CAMERA] camera_id={_camera_id!r} not found ({_resp.status_code}) -- "
                      f"using config.json's camera.name fallback: {CAMERA_NAME!r}")
                break  # a real 404 won't fix itself by retrying
        except Exception as e:
            if _attempt < _CAMERA_RESOLVE_ATTEMPTS:
                print(f"⏳ [CAMERA] Backend not ready yet resolving camera_id={_camera_id!r} "
                      f"(attempt {_attempt}/{_CAMERA_RESOLVE_ATTEMPTS}): {e} -- retrying in {_CAMERA_RESOLVE_BACKOFF_S}s...")
                time.sleep(_CAMERA_RESOLVE_BACKOFF_S)
            else:
                print(f"⚠️  [CAMERA] Could not reach backend to resolve camera_id={_camera_id!r} "
                      f"after {_CAMERA_RESOLVE_ATTEMPTS} attempts: {e} -- "
                      f"using config.json's camera.name fallback: {CAMERA_NAME!r}")

# Apply the per-camera overrides fetched above. Each line ANDs the existing
# (global-config-derived) flag with this camera's override -- a camera
# override can only turn something OFF that the global switch already
# turned ON, never the reverse (matches the same rule backend.py's
# set_camera_model enforces server-side). .get(key, True) means "no row for
# this camera+model" reads as enabled, so nothing changes for a camera
# nobody has touched in the Models panel yet.
VIOLENCE_ON = VIOLENCE_ON and _camera_model_overrides.get("violence", True)
ROBBERY_ON = ROBBERY_ON and _camera_model_overrides.get("robbery", True)
VANDALISM_ON = VANDALISM_ON and _camera_model_overrides.get("vandalism", True)
WEAPON_DETECTION_ENABLED = WEAPON_DETECTION_ENABLED and _camera_model_overrides.get("weapon", True)
if not _camera_model_overrides.get("vandalism_marks", True):
    vandal_mark_model = None
    print("📷 [CAMERA] vandalism_marks disabled for this camera -- marks model unloaded.")

# BUG FOUND 2026-09-02 (full account/feature sweep, "make sure all my AI
# detections can be turned off"): violence and vandalism each print their own
# "disabled" line when the GLOBAL config says off, but that print happens
# before camera overrides are applied above -- so a class enabled globally
# but disabled for THIS camera gets no print either way, and robbery/weapon
# never had a disabled-confirmation print at all (only a "model loaded" line
# when ON, silence when off -- indistinguishable from a config-parsing bug
# that quietly produced the same silence). Verified by actually turning all
# four off through the real API and watching this camera's startup log: only
# violence and vandalism said anything. One unified block, after every
# override is applied, so operator logs can answer "is X really off on THIS
# camera" for all four the same way, not two.
print("🎛️  [DETECTION STATE] Final per-class status for this camera (after global config + camera overrides):")
for _label, _on in (("Violence", VIOLENCE_ON), ("Robbery", ROBBERY_ON),
                     ("Vandalism", VANDALISM_ON), ("Weapon", WEAPON_DETECTION_ENABLED)):
    print(f"   {'✅ ON ' if _on else '🚫 OFF'}  {_label}")

# Per-camera OPERATING-POINT overrides (backend.py's camera_threshold_config
# table, docs/progress_report_violence_detection.md §28.1): a barangay can
# retune threshold/consecutive for THIS specific camera without touching
# config.json, e.g. a wide flyover camera calibrated hotter than a quiet
# storefront. Piggybacked on the same /api/camera_name response the model
# overrides above came from -- no extra round trip. Empty/missing keys mean
# "no override" and every detector keeps the threshold it was already built
# with (SceneViolenceDetector.threshold/.consecutive, set from
# config.json's scene_confidence_threshold/scene_confidence_required or the
# robbery/vandalism confidence_threshold/consecutive_required at
# construction, a few hundred lines above).
for _model_key, _detector in (
    ("violence", scene_detector),
    ("robbery", robbery_detector),
    ("vandalism", vandalism_detector),
):
    _override = _camera_threshold_overrides.get(_model_key)
    if not _override or _detector is None:
        continue
    if _override.get("threshold") is not None:
        _detector.threshold = float(_override["threshold"])
    if _override.get("consecutive_required") is not None:
        _detector.consecutive = int(_override["consecutive_required"])
    print(f"📷 [CAMERA] Per-camera threshold for {_camera_id!r}/{_model_key}: "
          f"threshold={_detector.threshold} consecutive={_detector.consecutive} "
          f"(source: {_override.get('calibrated_from', 'manual')})")
# Kept as a separate name because the 0-9 scanner and the Monitor view's index
# picker are only meaningful for local devices.
camera_idx = camera_source if isinstance(camera_source, int) else None

# On Windows the default backend selection can land on OBSENSOR, whose UVC
# channel probe RAISES a C++ exception ("Camera index out of range") out of
# both VideoCapture() and cap.read() instead of just reporting failure --
# which took the whole process down at the top of the main loop. DirectShow
# is the correct backend for webcams and OBS Virtual Camera anyway, so we ask
# for it explicitly and only fall back to auto-select if DSHOW can't open.
_CAP_BACKENDS = [cv2.CAP_DSHOW, cv2.CAP_ANY] if sys.platform == "win32" else [cv2.CAP_ANY]
# DirectShow cannot open a URL at all, so string sources go to FFmpeg -- and
# to FFmpeg ONLY. There is deliberately no CAP_ANY fallback here: CAP_ANY
# ignores the timeout params below, so a dead camera that failed FFmpeg's 5s
# open would then block the caller for OpenCV's hard-coded 30s default on the
# retry. Measured: 41.7s per attempt with the fallback, 5s without. FFmpeg is
# the only backend that speaks RTSP anyway, so the fallback bought nothing.
_NET_BACKENDS = [cv2.CAP_FFMPEG]

# FFmpeg reads this at open() time, from the environment. UDP silently shreds
# frames on congested or wifi links, and a torn frame is worse than a late one
# when the next stage is a motion classifier -- so force TCP.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

# Timeouts must go through OpenCV's own capture params, NOT through the FFmpeg
# option string: OpenCV installs its own interrupt callback whose default is a
# hard 30s, which overrides anything ffmpeg's `stimeout` would do. Measured --
# with the env-var form alone, opening a dead camera blocked the calling thread
# for the full 30s. On the reconnect path that means the detector stops
# processing an entire half-minute of footage every retry.
_NET_TIMEOUT_MS = 5000
_NET_CAP_PARAMS = [
    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _NET_TIMEOUT_MS,
    cv2.CAP_PROP_READ_TIMEOUT_MSEC, _NET_TIMEOUT_MS,
]


class _NetworkStreamReader:
    """Drains a network capture on its own thread, keeping only the newest frame.

    An IP camera is a PUSH source: it sends at its own frame rate whether or not
    anyone is consuming. The detector's per-frame work (pose + X3D + weapon pass)
    is slower than 30fps at 720p on a 1660 SUPER, so unread frames pile up in the
    socket, the camera's write queue fills, and reads eventually stall past the
    timeout. Measured against a real RTSP server: without this thread the core
    entered a permanent read-fail/reconnect loop within seconds and never
    processed a single frame. CAP_PROP_BUFFERSIZE does not help -- the FFmpeg
    backend ignores it.

    Discarding the backlog is the right trade for a security system, not a
    compromise: an alert about something that happened forty seconds ago is not
    an alert. Better to analyse live footage and skip frames than to fall
    steadily further behind the incident.

    Exposes the same read/isOpened/release surface as a VideoCapture so the main
    loop cannot tell the difference.
    """

    _FAIL_LIMIT = 30      # consecutive bad reads before declaring the stream dead
    _READ_TIMEOUT = 2.0   # seconds to wait for a fresh frame before reporting failure

    def __init__(self, cap):
        self._cap = cap
        self._lock = threading.Lock()
        self._frame = None
        self._alive = True
        self._new_frame = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        fails = 0
        while not self._stop.is_set():
            try:
                ok, frame = self._cap.read()
            except Exception:
                ok, frame = False, None
            if ok and frame is not None:
                fails = 0
                with self._lock:
                    self._frame = frame
                self._new_frame.set()
            else:
                # Isolated dropped frames are normal on a live stream; a
                # sustained run means the stream is genuinely gone, and the
                # main loop's reconnect should take over.
                fails += 1
                if fails >= self._FAIL_LIMIT:
                    with self._lock:
                        self._alive = False
                    self._new_frame.set()   # wake a waiting reader to fail fast
                    break
                time.sleep(0.01)

    def read(self):
        """Returns the newest frame, or (False, None) if none arrived in time.

        Never returns the same frame twice: handing a duplicate to a temporal
        model would put motionless repeats into the X3D clip buffer and quietly
        corrupt the very signal it classifies on."""
        if not self._new_frame.wait(self._READ_TIMEOUT):
            return False, None
        with self._lock:
            if not self._alive:
                return False, None
            frame = self._frame
            self._frame = None
            self._new_frame.clear()
        return (True, frame) if frame is not None else (False, None)

    def isOpened(self):
        with self._lock:
            return self._alive and self._cap.isOpened()

    def release(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        try:
            self._cap.release()
        except Exception:
            pass


def _open_capture(src):
    """Opens a local index or a stream URL. Returns an opened capture (or a
    _NetworkStreamReader wrapping one) or None. Never raises -- OpenCV backend
    probes can throw, not just fail."""
    src = _normalise_source(src)
    # Any string source -- URL or file path -- is FFmpeg's job; DirectShow
    # only understands device indices and would waste a failed probe first.
    remote = isinstance(src, str)
    for backend in (_NET_BACKENDS if remote else _CAP_BACKENDS):
        try:
            # Only the FFmpeg backend implements the timeout params; handing
            # them to the CAP_ANY fallback raises "unsupported parameter"
            # instead of falling back, which would turn the safety net into
            # the failure.
            if remote and backend == cv2.CAP_FFMPEG:
                c = cv2.VideoCapture(src, backend, _NET_CAP_PARAMS)
            else:
                c = cv2.VideoCapture(src, backend)
            if c.isOpened():
                if not remote:
                    # Only meaningful for UVC devices. On an RTSP stream the
                    # resolution is whatever the camera encodes; asking for a
                    # different one is at best ignored and at worst makes the
                    # backend renegotiate into a broken state.
                    c.set(cv2.CAP_PROP_FRAME_WIDTH,  res_w)
                    c.set(cv2.CAP_PROP_FRAME_HEIGHT, res_h)
                # Honoured by some backends, ignored by FFmpeg -- which is why
                # live sources also get the drain thread.
                c.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # Only LIVE streams get the drain thread. A file is not a push
                # source: it waits for the reader, so draining it would just
                # race to the end discarding most of the footage -- the
                # opposite of what replaying a clip for testing is for.
                return _NetworkStreamReader(c) if _is_network_source(src) else c
            c.release()
        except Exception:
            pass
    return None


def _probe_for_working_camera():
    """Scans indices 0-9 for the first camera that actually opens. Used when
    the configured index is dead so a stale config.json (or a webcam that
    moved indices after a reboot) degrades to 'found a different camera'
    rather than 'process exits on startup'."""
    for idx in range(10):
        if idx == camera_idx:
            continue
        c = _open_capture(idx)
        if c is not None:
            return idx, c
    return None, None


def _describe_source(src=None):
    """Human-readable source label with any RTSP password stripped.

    The source string is printed at startup, returned by /available_cameras and
    shown in the dashboard, so `rtsp://admin:hunter2@host/stream1` must not
    travel with its credentials intact."""
    src = camera_source if src is None else src
    if not isinstance(src, str):
        return f"local index {src}"
    return re.sub(r"://[^/@]*@", "://***@", src)


cap = _open_capture(camera_source)
if cap is None:
    if _is_network_source(camera_source):
        # Deliberately do NOT fall back to probing local webcams here. Silently
        # switching a barangay's street camera to whatever USB device is plugged
        # into the server would show a plausible-looking feed of the wrong place
        # -- far worse than an obviously dead one. Retry the URL instead; the
        # main loop's reconnect handles cameras that come back.
        print(f"❌ [CAMERA] Could not open stream {_describe_source()}. "
              f"Will keep retrying -- check the URL, credentials and that the "
              f"camera is reachable from this host.")
        cap = cv2.VideoCapture()
    else:
        print(f"⚠️  [CAMERA] Configured index {camera_source} could not be opened. Probing 0-9...")
        found_idx, found_cap = _probe_for_working_camera()
        if found_cap is not None:
            print(f"✅ [CAMERA] Using index {found_idx} instead "
                  f"(update config.json or pick it in the Monitor view to make this permanent).")
            camera_idx = camera_source = found_idx
            cap = found_cap
        else:
            # Keep running headless: the HTTP server, /available_cameras and
            # /set_camera_index all still work, so the operator can plug a camera
            # in and select it from the UI without restarting the AI core.
            print("❌ [CAMERA] No working camera found on indices 0-9. "
                  "Running with no feed -- select a camera from the Monitor view once one is connected.")
            cap = cv2.VideoCapture()
else:
    print(f"📷 [CAMERA] Source: {_describe_source()}")

# ──────────────────────────────────────────────────────────────────────────────
# 14.1 CAMERA RECONNECT HELPER
# ──────────────────────────────────────────────────────────────────────────────
def _reopen_camera():
    """Attempts to fully reinitialize the capture device after a drop.

    Network cameras make this the normal case rather than the exceptional one:
    switch reboots, PoE renegotiation and wifi dropouts all end the RTSP
    session, and the camera comes back a few seconds later expecting a fresh
    connection."""
    global cap
    try:
        cap.release()
    except Exception:
        pass
    new_cap = _open_capture(camera_source)
    cap = new_cap if new_cap is not None else cv2.VideoCapture()
    return cap.isOpened()


class CameraIndexRequest(BaseModel):
    index: int


class CameraSourceRequest(BaseModel):
    """Accepts either a local index ("0") or a stream URL ("rtsp://...")."""
    source: str


# Scanning 0-9 opens and releases real capture devices, which takes ~2s and
# briefly contends for hardware. The dashboard polls this endpoint (and retries
# on failure), so an uncached scan meant hammering the camera subsystem every
# few seconds forever. Cameras don't come and go often, so serve a short-lived
# cached result and let /set_camera_index invalidate it on an actual change.
_camera_scan_cache = {"result": None, "at": 0.0}
_CAMERA_SCAN_TTL = 60.0
_camera_scan_lock = threading.Lock()


def _scan_cameras():
    available = []
    for idx in range(10):  # Check indices 0-9
        # Probing the CURRENTLY OPEN index would fight the main loop for the
        # device (Windows webcams are exclusive-access), so trust it instead.
        if idx == camera_idx and cap.isOpened():
            available.append(idx)
            continue
        test_cap = _open_capture(idx)
        if test_cap is None:
            continue
        try:
            # Try to read a frame to confirm it's actually available
            ret, _ = test_cap.read()
            if ret:
                available.append(idx)
        except Exception:
            pass
        finally:
            test_cap.release()
    return available


@stream_app.get("/available_cameras")
def get_available_cameras(refresh: bool = False):
    """Detects available camera indices by attempting to open each one.
    Returns a list of working camera indices (0-9 checked by default).
    Pass ?refresh=1 to force a rescan (e.g. after plugging a camera in)."""
    with _camera_scan_lock:
        now = time.time()
        stale = (
            _camera_scan_cache["result"] is None
            or now - _camera_scan_cache["at"] > _CAMERA_SCAN_TTL
        )
        if refresh or stale:
            _camera_scan_cache["result"] = _scan_cameras()
            _camera_scan_cache["at"] = now
        available = _camera_scan_cache["result"]
    return {
        "available_cameras": available,
        "current_index": camera_idx,
        # Network sources have no index, so the picker needs the source itself
        # to show what is actually being watched. Credentials are stripped.
        "current_source": _describe_source(),
        "is_network": _is_network_source(camera_source),
        "connected": cap.isOpened(),
    }


def _persist_camera_source():
    """Writes the active source back to the WRITABLE config.json (not the
    shipped/possibly-read-only one) so the choice survives the next launch.

    Both keys are written every time: leaving a stale `source` behind would
    silently win over a newly-picked `index` on the next boot, because source
    takes precedence."""
    try:
        if isinstance(camera_source, int):
            sys_config["camera"]["index"] = camera_source
            sys_config["camera"]["source"] = ""
        else:
            sys_config["camera"]["source"] = camera_source
        with open(CONFIG_PATH, "w") as f:
            json.dump(sys_config, f, indent=2)
    except Exception as e:
        print(f"⚠️  [CAMERA] Failed to persist camera source to config.json: {e}")


def _switch_source(new_source):
    """Shared body of both switch endpoints: reopen, invalidate the scan
    cache, persist. Returns the response dict."""
    global camera_source, camera_idx
    previous = camera_source
    camera_source = _normalise_source(new_source)
    camera_idx = camera_source if isinstance(camera_source, int) else None
    ok = _reopen_camera()

    if not ok and _is_network_source(camera_source):
        # A typo'd or unreachable URL should not cost the operator the feed
        # they already had. Roll back and report the failure instead.
        camera_source = previous
        camera_idx = camera_source if isinstance(camera_source, int) else None
        _reopen_camera()
        return {"status": "failed", "source": _describe_source(new_source),
                "detail": "could not open stream -- reverted to previous source",
                "current_source": _describe_source()}

    # Which index is "currently open" is part of the scan result, so a swap
    # invalidates it -- otherwise the picker would show stale availability
    # for up to _CAMERA_SCAN_TTL after the operator changed cameras.
    with _camera_scan_lock:
        _camera_scan_cache["result"] = None

    _persist_camera_source()
    return {"status": "reopened" if ok else "failed",
            "index": camera_idx, "source": _describe_source(),
            "is_network": _is_network_source(camera_source)}


@stream_app.post("/set_camera_index")
def set_camera_index(payload: CameraIndexRequest):
    """Lets the Monitor view's camera-index picker swap the live capture
    device (e.g. OBS Virtual Camera vs. a webcam) without restarting the
    whole AI process."""
    return _switch_source(payload.index)


@stream_app.post("/set_camera_source")
def set_camera_source(payload: CameraSourceRequest):
    """Points the detector at an arbitrary source: a local index ("0"), or a
    network stream ("rtsp://user:pass@10.0.0.12:554/stream1", an HTTP MJPEG
    URL, or a file path for replay).

    This is what lets the system adopt a barangay's existing IP cameras
    instead of requiring the hardware we tested with -- RTSP is the common
    denominator across effectively every CCTV vendor."""
    return _switch_source(payload.source)

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

# 2026-08-19: changed from 5/5 (10s clips) to a 30s NVIDIA-Instant-Replay-
# style clip -- mostly pre-roll (what led up to the moment, which is the
# part you don't know is worth keeping until after it happens) with a
# shorter confirmation tail. Requested as "wait 10s and clip it, 30s total".
#
# COST, stated plainly: the pre-roll ring buffer holds RAW annotated frames
# (not encoded video), so this is a real memory increase -- at
# camera.fps=30 and 1280x720, 20s of pre-roll is ~600 frames x ~2.8MB =
# ~1.6GB resident for the buffer alone, up from ~400MB at the old 5s. If
# that's tight on this machine, the fix is compressing ring-buffer frames
# (e.g. JPEG) instead of storing raw arrays, not shrinking the window back
# down -- ask if that's needed.
CLIP_PRE_SECONDS  = 20
CLIP_POST_SECONDS = 10
CLIP_NOMINAL_FPS  = sys_config["camera"].get("fps", 15)   # sizes the ring buffer + used as encode-fps fallback
CLIP_PRE_FRAMES   = max(1, int(CLIP_NOMINAL_FPS * CLIP_PRE_SECONDS))

_clip_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip")

# BUG FOUND 2026-08-19: cv2.VideoWriter's "mp4v" fourcc writes MPEG-4 Part 2,
# which NO current browser can decode -- so every auto-captured clip landed
# on disk as a valid, playable-in-VLC file that showed a dead play button in
# the dashboard's <video> player. Browsers need H.264 ("avc1"), and this
# machine's OpenCV cannot encode it ("Failed to load OpenH264 library:
# openh264-2.5.0-win64.dll"), so re-encoding after the fact is the reliable
# path. imageio-ffmpeg bundles its own ffmpeg binary, so this works on a
# packaged install too -- a system ffmpeg on PATH is used if present, but
# never assumed. If neither is available the clip is still written and
# registered, just in the old un-playable codec, with a clear warning.
def _resolve_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    import shutil
    return shutil.which("ffmpeg")

FFMPEG_EXE = _resolve_ffmpeg()
if FFMPEG_EXE:
    print(f"🎞️  [CLIP] H.264 transcode enabled via {os.path.basename(FFMPEG_EXE)}")
else:
    print("⚠️  [CLIP] No ffmpeg found -- clips will be saved as MPEG-4 Part 2, "
          "which the dashboard's video player CANNOT play. `pip install imageio-ffmpeg` to fix.")


def _transcode_to_h264(src_path: str) -> bool:
    """Re-encode in place to browser-playable H.264. Returns True on success.

    -movflags +faststart matters specifically for the dashboard: it moves the
    MP4 index to the front of the file so playback can begin before the whole
    clip has downloaded, instead of the player stalling on a seek.
    """
    if not FFMPEG_EXE:
        return False
    tmp_path = src_path + ".h264.mp4"
    try:
        proc = subprocess.run(
            [FFMPEG_EXE, "-y", "-loglevel", "error", "-i", src_path,
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", tmp_path],
            capture_output=True, timeout=180,
        )
        if proc.returncode != 0 or not os.path.exists(tmp_path):
            print(f"   ⚠️  [CLIP] H.264 transcode failed: {proc.stderr.decode('utf-8', 'replace')[:200]}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return False
        os.replace(tmp_path, src_path)   # atomic; keeps the original filename the DB already has
        return True
    except Exception as e:
        print(f"   ⚠️  [CLIP] H.264 transcode error: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False

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

    # Transcode BEFORE registering with the backend, so a clip is never
    # advertised to the dashboard until it's actually in a codec the player
    # can open. Runs on _clip_exec's background thread, so the detection
    # loop is unaffected by the extra encode time.
    playable = _transcode_to_h264(file_path)

    trigger_seconds = clip["trigger_index"] / encode_fps
    marker          = f"{int(trigger_seconds // 60):02d}:{int(trigger_seconds % 60):02d}"
    total_seconds   = len(frames) / encode_fps

    codec_note = "H.264" if playable else "MPEG-4 Part 2 (NOT browser-playable)"
    print(f"🎬 [CLIP] Saved {filename} ({total_seconds:.1f}s, marker@{marker}, {codec_note}) for case {incident_id}")

    try:
        # Field names must be snake_case to match backend.py's ManualClipSchema.
        # These were camelCase (associatedCrimeId / crimeTimeMarker), so
        # crime_time_marker -- which is REQUIRED -- was always absent and every
        # single auto-clip registration failed with 422 Unprocessable Entity.
        # The incident itself still landed via /api/ai_trigger, so alerts looked
        # fine while their video evidence was silently never attached.
        #
        # Endpoint is /api/ai_register_clip, not /api/records/register_clip:
        # the latter is behind require_auth and this process has no user
        # session, so fixing only the field names would have turned the 422
        # into a 401.
        r = requests.post(f"{sys_config['networking']['api_url'].rstrip('/')}/api/ai_register_clip", json={
            "filename":               filename,
            "duration":               f"{total_seconds:.1f}s",
            "type":                   "CLIP",
            "associated_incident_id": incident_id,
            "crime_time_marker":      marker,
            "notes":                  f"Auto-captured by AI Sentinel on {clip['event']} detection (conf={clip['conf']:.2f}).",
        }, timeout=3.0)
        if r.status_code >= 400:
            print(f"   ❌ Records backend rejected clip ({r.status_code}): {r.text[:200]}")
        else:
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
track_states, prev_joints, id_last_seen = {}, {}, {}
robbery_tracker = RobberyTracker()
vandal_states: dict = {}
vandal_sweep_history: dict = {}

# BUG FOUND 2026-08-19: robbery/vandalism used the same "re-fire every
# ALERT_COOLDOWN_FRAMES for as long as the state stays confirmed" pattern
# TrackState had -- same fix applied here as a shared helper, so robbery
# and vandalism can't independently regress back into it later. See
# TrackState's active_incident_id comment for the full story (real
# observed case: 239 separate incidents/clips from one continuously-
# confirmed scene that never actually released).
_episode_store: dict = {}   # key -> {"id": str|None, "end": last-release frame}

def _episode_incident_id(key, is_active: bool, frame_no: int, debounce_frames: int = ALERT_COOLDOWN_FRAMES):
    """Returns a fresh incident_id on the FIRST frame `key` becomes active
    after being inactive (or after the debounce window since it last
    released), None on every frame after that for as long as it stays
    active. Call this every frame regardless of `is_active` -- it also
    handles clearing state on release."""
    st = _episode_store.setdefault(key, {"id": None, "end": -debounce_frames})
    if not is_active:
        if st["id"] is not None:
            st["end"] = frame_no
        st["id"] = None
        return None
    if st["id"] is None and (frame_no - st["end"]) > debounce_frames:
        st["id"] = str(uuid.uuid4())[:8]
        return st["id"]
    return None


# PLACEHOLDER confidences for the two RULE-BASED detectors. These are not
# measured, not calibrated, and not derived from anything -- robbery and
# vandalism are hand-written geometric rules that either fire or do not, so
# there is no probability to report. They were literals inline at the two
# alert sites; naming them here at least stops the numbers reading like model
# output to somebody skimming the code.
#
# They are still WRONG to display. The dashboard renders this field as a
# confidence percentage, so an operator sees "ROBBERY 89.5%" and reasonably
# believes the system computed it. Fixing that properly means deciding what the
# UI should show for a rule-based detector -- "rule-based" rather than a
# percentage is the honest answer -- which is a product decision, not a code
# cleanup, so the wire format is deliberately left unchanged here.
RULE_ROBBERY_PLACEHOLDER_CONF = 0.895
RULE_VANDALISM_PLACEHOLDER_CONF = 0.84

scene_last_alert_frame = -SCENE_COOLDOWN_FRAMES

_running = True
def _shutdown(*_):
    global _running
    _running = False

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

frame_count, fps_timer, fps_display, fps_frame_count = 0, time.perf_counter(), 0.0, 0
_camera_fail_streak = 0
_camera_last_ok = time.perf_counter()
_camera_retry_delay = 2.0
print("🚀 Sentinel v16.0 — Portable dynamic deployment runtime context pipeline engaged.")

while _running:
    # cap.read() can THROW, not just return False -- a missing/unplugged
    # device on Windows surfaces as "Unknown C++ exception from OpenCV code"
    # out of the backend probe. Uncaught, that killed the whole AI core on
    # startup whenever config.json pointed at an index with no camera on it.
    try:
        ret, frame = cap.read()
    except Exception:
        ret, frame = False, None

    if not ret or frame is None:
        _camera_fail_streak += 1
        # A dead device fails fast (returns immediately), a throwing backend
        # burns ~0.3s per attempt. Counting attempts rather than wall-clock
        # made the old 200-strike threshold anywhere from 2s to ~60s, so
        # reconnect on elapsed time instead and back off once it's clearly
        # not coming back -- no point re-probing a missing camera at 100Hz.
        now = time.perf_counter()
        if now - _camera_last_ok >= _camera_retry_delay:
            print(f"⚠️  Camera read failing ({_camera_fail_streak} attempts) — attempting reconnect...")
            if _reopen_camera():
                print("✅ Camera reconnected.")
                _camera_retry_delay = 2.0
                _camera_fail_streak = 0
            else:
                _camera_retry_delay = min(_camera_retry_delay * 2, 30.0)
                print(f"❌ Camera reconnect failed, next retry in {_camera_retry_delay:.0f}s.")
            _camera_last_ok = now
        time.sleep(0.05)
        continue

    _camera_fail_streak = 0
    _camera_last_ok = time.perf_counter()
    _camera_retry_delay = 2.0

    frame_count += 1
    fps_frame_count += 1

    if WEAPON_DETECTION_ENABLED and frame_count % DETECTION_INTERVAL == 0:
        if _weapon_future is None or _weapon_future.done():
            _weapon_future = _weapon_exec.submit(_run_weapon_detection, frame.copy())

    if (VANDALISM_ON and vandal_mark_model is not None
            and frame_count % DETECTION_INTERVAL == 0):
        if _vandal_future is None or _vandal_future.done():
            _vandal_future = _vandal_exec.submit(_run_vandal_mark_detection, frame.copy())

    with _weapon_lock:
        raw_weapons = list(_weapon_cache["weapons"])
        raw_vboxes  = list(_weapon_cache["vboxes"])

    tracked_weapons = _update_weapon_tracks(raw_weapons)
    live_vboxes = _vbox_tracker.update(raw_vboxes)

    # Scene verdict is computed BEFORE and OUTSIDE the pose block on purpose.
    # Putting it inside would re-create the exact coupling this replaces --
    # the frame must be classified whether or not YOLO found and held a
    # person. The detector rate-limits itself to one real forward every
    # X3D_CHECK_INTERVAL frames and returns its cached verdict in between.
    scene_violent, scene_conf = (False, 0.0)
    if SCENE_MODE_ON and VIOLENCE_ON:
        scene_violent, scene_conf = scene_detector.update(frame, frame_count)

    # Robbery runs on the same frame, with its own threshold and confirmation
    # state. Independent of the violence verdict on purpose: a robbery
    # involving assault should raise both, not compete for one label.
    vandal_hit, vandal_conf = (False, 0.0)
    if VANDALISM_ON and vandalism_detector is not None:
        vandal_hit, vandal_conf = vandalism_detector.update(frame, frame_count)

    robbery_hit, robbery_conf = (False, 0.0)
    if ROBBERY_ON and robbery_detector is not None:
        robbery_hit, robbery_conf = robbery_detector.update(frame, frame_count)

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
            for d in (track_states, prev_joints, id_last_seen,
                      vandal_states, vandal_sweep_history):
                d.pop(tid, None)
            _episode_store.pop(("vandalism", tid), None)
            x3d_detector.cleanup_track(tid)
        # _episode_store's robbery-pair keys are ("robbery-pair", (tid_a, tid_b)),
        # not a single tid, so they can't be pruned via the .pop(tid, None) loop
        # above -- drop any pair that references a track that just went stale,
        # otherwise this dict grows unbounded over a 24/7 run with many
        # transient near-passes.
        if stale:
            stale_set = set(stale)
            for key in [k for k in _episode_store
                        if k[0] == "robbery-pair" and stale_set & set(k[1])]:
                _episode_store.pop(key, None)

        victims = {}
        for tid, joints, b in zip(ids, kpts, boxes):
            torso = joints[5:13]
            valid = torso[np.any(torso > 1, axis=1)]
            if len(valid) > 0:
                victims[tid] = {"center": np.mean(valid, axis=0), "box": b}

        weapon_only = [w for w in tracked_weapons if w["name"] not in SIGN_CLASSES]
        weapon_assigns = _assign_weapons(weapon_only, ids, kpts, boxes, _weapon_grip_sticky)

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

            # In scene mode the frame-level verdict IS the detection; pose is
            # kept only to attribute it to a person (which box to draw, which
            # track to name in the alert) and to drive the weapon/robbery/
            # vandalism rules, which are unaffected. "both" still runs the
            # per-track model so its overlay stays available for comparison,
            # but the scene verdict is what decides.
            is_violent_x3d, x3d_conf = (False, 0.0)
            if VIOLENCE_ON and SCENE_MODE_ON:
                if VIOLENCE_MODE == "both":
                    x3d_detector.update(tid, frame, p_box, frame_count, all_boxes=boxes)
                    _draw_x3d_confidence(frame, p_box, x3d_detector.get_debug_info(tid))
                is_violent_x3d, x3d_conf = scene_violent, scene_conf
            elif VIOLENCE_ON:
                is_violent_x3d, x3d_conf = x3d_detector.update(tid, frame, p_box, frame_count, all_boxes=boxes)
                _draw_x3d_confidence(frame, p_box, x3d_detector.get_debug_info(tid))
                _draw_x3d_crop_box(frame, x3d_detector.get_crop_box(tid), is_violent_x3d, x3d_conf)

            in_vbox = max((_vbox_overlap_ratio(p_box, vb) for vb in live_vboxes), default=0.0) >= VBOX_ASSAULT_THRESHOLD
            # Gated on VIOLENCE_ON as a whole, not just is_violent_x3d, so
            # turning the Physical Violence detector off actually silences
            # ASSAULT alerts regardless of source -- including in_vbox, which
            # comes from the weapon model's own "violence zone" class (see
            # _run_weapon_detection) and would otherwise keep raising ASSAULT
            # through a detector the dashboard shows as Off. ARMED THREAT is
            # untouched here -- that's has_weapon's call, gated by its own
            # WEAPON_DETECTION_ENABLED flag below.
            is_assault = VIOLENCE_ON and (is_violent_x3d or in_vbox)

            override_confirm = max(1, ASSAULT_CONFIRM_FRAMES - 1) if (crowded and in_vbox) else None
            state = ts.update(is_assault, has_weapon, frame_count, override_assault_confirm=override_confirm)

            if active_pip_crop is None or state in ["ASSAULT", "ARMED"]:
                # Scene mode never fills the per-track crop cache, so fall back
                # to a plain padded crop of this person's box -- the PIP is an
                # operator aid ("who is this alert about"), and it would
                # otherwise go blank for every scene-mode alert.
                live_crop_patch = x3d_detector.get_latest_live_crop(tid)
                if live_crop_patch is None and SCENE_MODE_ON:
                    live_crop_patch = _pip_crop_from_box(frame, p_box)
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
                # Surface the real detector confidence instead of a fixed
                # placeholder wherever one is available, so "confidence" sent
                # downstream reflects what actually triggered the alert:
                #   ARMED   -> the weapon detector's own confidence for this
                #              track's assigned weapon(s), if any were matched.
                #   ASSAULT -> the X3D model's own (EMA-smoothed) probability,
                #              or the evidence-window density when the alert
                #              was driven mainly by the vbox/weapon-zone
                #              heuristic rather than the model itself.
                # 0.932 / the density-only formula remain as fallbacks for the
                # rare case neither signal is available.
                if state == "ARMED":
                    armed_weapon_confs = [w["conf"] for w in weapon_assigns.get(tid, []) if "conf" in w]
                    conf = max(armed_weapon_confs) if armed_weapon_confs else 0.932
                else:
                    evidence_density = min(1.0, sum(ts.evidence_buf) / EVIDENCE_WINDOW + 0.55)
                    conf = max(x3d_conf, evidence_density)
                incident_id = str(uuid.uuid4())[:8]
                ts.mark_alerted(frame_count, incident_id)
                scene_last_alert_frame = frame_count

                event_type = "ARMED THREAT" if state == "ARMED" else "ASSAULT"
                triggered_alerts_this_frame.append({"id": incident_id, "conf": conf, "event": event_type})

            _draw_overlay(frame, p_box, tid, state, weapon_assigns.get(tid))

        # ─── ROBBERY FILTER ANALYSIS ───
        armed_states    = {t: (track_states[t].state == "ARMED")   for t in ids if t in track_states}
        violence_states = {t: (track_states[t].state == "ASSAULT") for t in ids if t in track_states}
        
        # The ARMED-person-near-another-person heuristic still runs, but only
        # as an attribution hint. Whether an alert fires is now the model's
        # call, and the confidence sent downstream is the model's probability
        # instead of RULE_ROBBERY_PLACEHOLDER_CONF, a constant with no
        # derivation behind it.
        robbery_pairs = robbery_tracker.update(ids, boxes, armed_states, violence_states)
        if not ROBBERY_ON:
            for pair_key, r_state in robbery_pairs.items():
                incident_id = _episode_incident_id(("robbery-pair", pair_key), r_state == "ROBBERY", frame_count)
                if incident_id:
                    triggered_alerts_this_frame.append({"id": incident_id,
                                                        "conf": RULE_ROBBERY_PLACEHOLDER_CONF,
                                                        "event": "ROBBERY"})

        # ─── VANDALISM FILTER ANALYSIS ───
        # Was permanently dead: static_targets came from weapon_signs.pt's
        # "sign" class (road signs), which fired zero times in 4,800 measured
        # frames because vandalism targets are walls/gates/shutters, not road
        # signs -- the wrong detector for the job, not a broken rule. Fixed
        # 2026-08-19 by swapping in a purpose-built graffiti/tag detector
        # (detection.vandalism.marks_model_path) -- see train_vandalism_marks.py
        # in the training repo. score_vandalism()'s wrist-velocity/no-victim
        # logic itself is unchanged.
        if not VANDALISM_ON:
            sign_boxes = []
        else:
            with _vandal_mark_lock:
                sign_boxes = list(_vandal_mark_cache["boxes"])
        _draw_sign_boxes(frame, sign_boxes)
        
        for tid, joints, p_box in zip(ids, kpts, boxes):
            if tid not in vandal_states:
                vandal_states[tid] = VandalismTrackState()

            # score_vandalism's 4th param is the OUTER per-track dict -- it
            # calls sweep_history_dict.setdefault(tid, deque(...)) on it
            # internally. Passing a pre-resolved deque here (vandal_sweep_
            # history.setdefault(tid, ...)) crashes the instant condition 1
            # (a wrist near a static target) is ever satisfied, with
            # AttributeError: 'collections.deque' object has no attribute
            # 'setdefault'. Never hit in production because condition 1 was
            # itself dead (weapon_signs.pt's "sign" class never fired) --
            # found only once the vandalism-marks detector made condition 1
            # reachable for the first time. Use the PRE-overwrite snapshot
            # for prev_joints, not the live `prev_joints` dict (which now
            # holds this frame's wrist positions).
            is_vandal, target = score_vandalism(
                tid, joints, prev_joints_snapshot, vandal_sweep_history,
                static_targets=sign_boxes, all_person_boxes=boxes, my_box=p_box
            )
            v_state_res = vandal_states[tid].update(is_vandal)

            incident_id = _episode_incident_id(("vandalism", tid), v_state_res == "VANDALISM", frame_count)
            if incident_id:
                triggered_alerts_this_frame.append({"id": incident_id,
                                                    "conf": RULE_VANDALISM_PLACEHOLDER_CONF,
                                                    "event": "VANDALISM"})

    # ─── ROBBERY MODEL ALERT ───
    # Outside the pose block, for the same reason scene-mode violence is: the
    # frame must be classified whether or not YOLO held a track. Robbery in
    # this dataset is frequently one person at a vehicle, which is exactly the
    # case a tracker loses.
    if ROBBERY_ON:
        incident_id = _episode_incident_id("robbery-model", robbery_hit, frame_count)
        if incident_id:
            triggered_alerts_this_frame.append({"id": incident_id,
                                                "conf": float(robbery_conf),
                                                "event": "ROBBERY"})

    # ─── VANDALISM MODEL ALERT ───
    # Outside the pose block for the same reason as robbery: property damage is
    # often one person at a wall or a vehicle, which is exactly what a tracker
    # drops. The rule-based path below is kept as a separate, independent
    # signal -- it measured 8.3% recall at 0% FPR, so it adds little but costs
    # nothing and fires on a different kind of evidence.
    if VANDALISM_ON and vandalism_detector is not None:
        incident_id = _episode_incident_id("vandalism-model", vandal_hit, frame_count)
        if incident_id:
            triggered_alerts_this_frame.append({"id": incident_id,
                                                "conf": float(vandal_conf),
                                                "event": "VANDALISM"})

    # ─── SCENE-MODE FALLBACK: alert with nobody tracked ───
    # Deliberately OUTSIDE the pose block. This is the whole point of scene
    # mode: on the held-out set 36 violent clips were scored "normal" purely
    # because no track ID survived long enough, so the model never ran. The
    # loop above attributes an alert to a person when there is one; this
    # fires when the frame is violent and there is not.
    #
    # Attribution is genuinely unknown here, so the alert says so rather than
    # guessing a track -- a reviewer opening the clip can see for themselves.
    if SCENE_MODE_ON:
        # Episode tracking runs off scene_violent alone (the real, continuous
        # state) -- already_alerted only decides whether to SKIP emitting a
        # freshly-opened episode's alert because a per-track alert already
        # covered this exact frame. Folding already_alerted into the episode
        # signal itself would spuriously "release" a still-violent scene on
        # any frame a per-track alert happens to co-fire, re-opening a new
        # episode (and a new incident) the very next frame -- exactly the
        # kind of double-fire this whole mechanism exists to prevent.
        already_alerted = any(a["event"] in ("ASSAULT", "ARMED THREAT")
                              for a in triggered_alerts_this_frame)
        incident_id = _episode_incident_id("scene-fallback", scene_violent, frame_count,
                                            debounce_frames=SCENE_COOLDOWN_ASSAULT)
        if incident_id and not already_alerted:
            scene_last_alert_frame = frame_count
            triggered_alerts_this_frame.append(
                {"id": incident_id, "conf": scene_conf, "event": "ASSAULT"}
            )

    now = time.perf_counter()
    if now - fps_timer >= 1.0:
        fps_display = fps_frame_count / (now - fps_timer)
        fps_timer = now
        fps_frame_count = 0

    fh, fw = frame.shape[:2]
    if active_pip_crop is not None and fw > 220 and fh > 220:
        startX, startY = fw - 180, 40
        endX, endY = startX + 160, startY + 160
        # active_pip_crop is a padded crop at the SOURCE person's natural
        # (near-always non-square) aspect ratio -- _pip_crop_from_box()
        # deliberately doesn't resize it (it's also fed raw into
        # get_latest_live_crop() call sites). The destination box here is
        # a fixed 160x160 though, so paste unconditionally resizes right
        # before assignment. Was a silent crash (ValueError: could not
        # broadcast) the very first time a real, non-square person box hit
        # this path with a live camera -- caught in tiled-mode testing but
        # not specific to it; scene mode's fallback crop has the same shape.
        if active_pip_crop.shape[:2] != (endY - startY, endX - startX):
            active_pip_crop = cv2.resize(active_pip_crop, (endX - startX, endY - startY))
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
        # Copy first: the banner must land ONLY in the evidence image, not in
        # the live stream or the event clip (both consume `frame` below).
        snap_frame = frame.copy()
        _draw_alert_banner(snap_frame, alert["event"], alert["conf"])
        cv2.imwrite(snap_path, snap_frame)
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

    # Reuse annotated_snapshot for the JPEG-encode submission too -- nothing
    # mutates `frame` between the copy above and here, so a second full-res
    # frame.copy() was pure duplicate memcpy work every single loop iteration.
    if _encode_future is None or _encode_future.done():
        def _encode_and_push(f=annotated_snapshot):
            _, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_QUALITY])
            _push_frame(buf.tobytes())
        _encode_future = _encode_exec.submit(_encode_and_push)

    time.sleep(0.005)

print("\n🛑 Shutting down Portable Sentinel Matrix pipeline...")
cap.release()
cv2.destroyAllWindows()
x3d_detector.close()   # flushes any diagnostic-log rows still buffered (now batched, not per-row)
_weapon_exec.shutdown(wait=False, cancel_futures=True)
_encode_exec.shutdown(wait=False, cancel_futures=True)
_alert_exec.shutdown(wait=True)
_clip_exec.shutdown(wait=True)   # let any in-progress clip finish encoding/uploading before exit
print("Portable Sentinel shutdown complete.")
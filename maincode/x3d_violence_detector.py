"""
EcoVision -- X3D-XS Live Inference Wrapper
==================================================================
Loads your trained x3d_xs_violence_best.pt and runs it against a
ROLLING BUFFER of recent frames, not every single frame -- this is
the "triggered, not continuous" design locked in earlier.

This module is imported by main.py and replaces the FSM's role as
the PRIMARY Violence signal. The FSM math (_score_strike) can stay
in main.py as a free, always-on supplementary signal if you want it,
but X3D-XS is now what actually decides is_assault for the TrackState
machine, since it's the validated 83.6%-accuracy model.

HOW IT WORKS:
    1. Every frame, the current frame is pushed into a rolling buffer
       (one buffer per person track, since each person needs their
       own short "clip" of recent frames around their bounding box).
    2. Every X3D_CHECK_INTERVAL frames (not every frame -- this is
       the cost-control gate), once a track's buffer is full, the
       buffered frames are cropped to that person's region, resized,
       and run through X3D-XS once.
    3. The result (Violence / Normal + confidence) is cached and
       reused for X3D_CHECK_INTERVAL frames until the next check.

This keeps GPU cost bounded: X3D-XS runs roughly once every
X3D_CHECK_INTERVAL frames PER TRACK, not every frame for everyone.
"""

import numpy as np
import torch
import torch.nn as nn
from collections import deque

try:
    from pytorchvideo.models.hub import x3d_xs
except ImportError:
    raise SystemExit("Missing pytorchvideo. Run: pip install pytorchvideo")

import cv2


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

MODEL_PATH        = r"D:\projects\EcoVisionCode\weights\x3d_xs_violence_best.pt"   # adjust filename if yours differs
CLIP_FRAMES        = 13     # must match training (CLIP_FRAMES in train_x3d_full.py)
FRAME_SIZE         = 160    # must match training (FRAME_SIZE in train_x3d_full.py)
X3D_CHECK_INTERVAL = 15     # run X3D-XS once every 15 frames PER TRACK (~0.5s @ 30fps)
                            # lower = more responsive but more GPU cost
                            # higher = cheaper but slower to detect onset
VIOLENCE_CONFIDENCE_THRESHOLD = 0.55   # softmax prob needed to call it "violent"


class X3DViolenceDetector:
    """
    Wraps your trained X3D-XS checkpoint for live, per-track inference.
    One instance is created once in main.py and reused across the whole run.
    """

    def __init__(self, model_path: str = MODEL_PATH, device: str = None):
        # Normalize device string -- Ultralytics accepts bare "0" for GPU index,
        # but torch.device() requires "cuda:0". Convert here so callers can pass
        # either convention without crashing.
        if device is None:
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        elif device in ("cpu",):
            resolved_device = "cpu"
        elif device.isdigit():
            resolved_device = f"cuda:{device}"
        elif device.startswith("cuda"):
            resolved_device = device
        else:
            resolved_device = device

        self.device = torch.device(resolved_device)
        print(f"[X3D] Loading violence model on {self.device}...")

        self.model = x3d_xs(pretrained=False)
        in_features = self.model.blocks[-1].proj.in_features
        self.model.blocks[-1].proj = nn.Linear(in_features, 2)
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.model.to(self.device)
        self.model.eval()

        print(f"[X3D] Model loaded successfully from {model_path}")

        # Per-track rolling frame buffers and cached results
        self._frame_buffers: dict[int, deque] = {}
        self._last_check_frame: dict[int, int] = {}
        self._cached_result: dict[int, tuple] = {}   # tid -> (is_violent, confidence)

    def _crop_person(self, frame: np.ndarray, p_box) -> np.ndarray:
        """Crops the frame to a person's bbox with some padding, resizes to FRAME_SIZE."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in p_box]
        pad_x = int((x2 - x1) * 0.3)
        pad_y = int((y2 - y1) * 0.3)
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)

        if x2 <= x1 or y2 <= y1:
            return np.zeros((FRAME_SIZE, FRAME_SIZE, 3), dtype=np.uint8)

        crop = frame[y1:y2, x1:x2]
        return cv2.resize(crop, (FRAME_SIZE, FRAME_SIZE))

    def update(self, tid: int, frame: np.ndarray, p_box, frame_count: int) -> tuple:
        """
        Call this once per track per frame. Returns (is_violent: bool, confidence: float)
        -- this is a CACHED result most of the time; only recomputes every
        X3D_CHECK_INTERVAL frames once the buffer is full.
        """
        if tid not in self._frame_buffers:
            self._frame_buffers[tid] = deque(maxlen=CLIP_FRAMES)
            self._last_check_frame[tid] = -X3D_CHECK_INTERVAL
            self._cached_result[tid] = (False, 0.0)

        cropped = self._crop_person(frame, p_box)
        self._frame_buffers[tid].append(cropped)

        buffer_full = len(self._frame_buffers[tid]) == CLIP_FRAMES
        due_for_check = (frame_count - self._last_check_frame[tid]) >= X3D_CHECK_INTERVAL

        if buffer_full and due_for_check:
            self._last_check_frame[tid] = frame_count
            self._cached_result[tid] = self._run_inference(self._frame_buffers[tid])

        return self._cached_result[tid]

    def _run_inference(self, frames_deque: deque) -> tuple:
        frames = np.stack(list(frames_deque), axis=0).astype(np.float32) / 255.0
        frames = (frames - 0.45) / 0.225
        tensor = torch.from_numpy(frames).permute(3, 0, 1, 2).float().unsqueeze(0)
        tensor = tensor.to(self.device)

        with torch.no_grad():
            outputs = self.model(tensor)
            probs = torch.softmax(outputs, dim=1)
            violence_prob = float(probs[0][1])

        is_violent = violence_prob >= VIOLENCE_CONFIDENCE_THRESHOLD
        return is_violent, violence_prob

    def get_debug_info(self, tid: int) -> dict:
        """
        Returns diagnostic info for visual overlay/debugging:
          - confidence: last computed violence probability (0.0-1.0)
          - buffer_fill: how full the rolling frame buffer is (0 to CLIP_FRAMES)
          - frames_until_next_check: countdown to next real inference
        """
        is_violent, conf = self._cached_result.get(tid, (False, 0.0))
        buf_len = len(self._frame_buffers.get(tid, []))
        last_check = self._last_check_frame.get(tid, 0)
        return {
            "confidence": conf,
            "is_violent": is_violent,
            "buffer_fill": buf_len,
            "buffer_target": CLIP_FRAMES,
        }

    def cleanup_track(self, tid: int):
        """Call when a track goes stale, mirrors main.py's existing stale-cleanup pattern."""
        self._frame_buffers.pop(tid, None)
        self._last_check_frame.pop(tid, None)
        self._cached_result.pop(tid, None)
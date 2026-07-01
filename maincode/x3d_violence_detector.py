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

MODEL_PATH        = r"D:\projects\EcoVisionCode\weights\x3d_xs_violence_best.pt"
CLIP_FRAMES        = 13
FRAME_SIZE         = 160

# CHANGED -- training sampled 13 frames evenly across each WHOLE clip
# (np.linspace), capturing several seconds of motion arc. The live pipeline
# was capturing 13 CONSECUTIVE frames (~0.4 sec at 30fps), which is a
# fundamentally different, much shorter temporal window than what the
# model learned to recognize. BUFFER_SPAN widens the live window to match:
# we now keep BUFFER_SPAN raw frames and subsample 13 of them evenly,
# mirroring training's temporal coverage instead of a tight burst.
BUFFER_SPAN         = 45     # raw frames kept before subsampling (~1.5 sec @ 30fps)
                            # TRADEOFF REASONING:
                            # - BUFFER_SPAN=13 (original): only 0.4sec coverage, model trained on
                            #   whole clips (several sec) -- temporal mismatch causes accuracy gap
                            # - BUFFER_SPAN=90: 3sec coverage but clips <90 frames get zero
                            #   inferences -- half the test set fires nothing
                            # - BUFFER_SPAN=45: 1.5sec coverage, fills on 41+ frame clips,
                            #   linspace gap of 3.7 frames between samples -- reasonable
                            #   approximation of training's temporal distribution without
                            #   being incompatible with the shorter clips in the dataset
                            # On a LIVE CONTINUOUS CAMERA this distinction doesn't matter
                            # (buffer fills once, stays full forever) -- only affects test harness
X3D_CHECK_INTERVAL  = 15
VIOLENCE_CONFIDENCE_THRESHOLD = 0.55


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

    def _crop_person(self, frame: np.ndarray, p_box, all_boxes=None) -> np.ndarray:
        """
        Crops the frame around a person's bbox, padded generously enough to
        usually include a SECOND nearby person if one exists. Violence is an
        interaction signal -- a tight crop on only the tracked individual can
        cut the other party out of frame entirely, removing the exact visual
        evidence (contact, reciprocal motion) the model needs.

        If all_boxes is provided, the crop expands to include any other
        person bbox within a reasonable distance, instead of using a fixed
        padding ratio alone.
        """
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in p_box]

        # Start with generous fixed padding (wider than before: 30% -> 60%)
        pad_x = int((x2 - x1) * 0.6)
        pad_y = int((y2 - y1) * 0.6)
        cx1, cy1 = x1 - pad_x, y1 - pad_y
        cx2, cy2 = x2 + pad_x, y2 + pad_y

        # Expand further to include any nearby person -- this is what
        # actually captures two-person interactions instead of guessing
        # via padding alone.
        if all_boxes is not None:
            my_center_x = (x1 + x2) / 2
            my_center_y = (y1 + y2) / 2
            search_radius = max(x2 - x1, y2 - y1) * 3   # only consider plausibly-nearby people

            for ob in all_boxes:
                ox1, oy1, ox2, oy2 = [int(v) for v in ob]
                if (ox1, oy1, ox2, oy2) == (x1, y1, x2, y2):
                    continue
                other_cx, other_cy = (ox1 + ox2) / 2, (oy1 + oy2) / 2
                dist = ((other_cx - my_center_x) ** 2 + (other_cy - my_center_y) ** 2) ** 0.5
                if dist <= search_radius:
                    cx1 = min(cx1, ox1)
                    cy1 = min(cy1, oy1)
                    cx2 = max(cx2, ox2)
                    cy2 = max(cy2, oy2)

        cx1, cy1 = max(0, cx1), max(0, cy1)
        cx2, cy2 = min(w, cx2), min(h, cy2)

        if cx2 <= cx1 or cy2 <= cy1:
            return np.zeros((FRAME_SIZE, FRAME_SIZE, 3), dtype=np.uint8)

        crop = frame[cy1:cy2, cx1:cx2]
        return cv2.resize(crop, (FRAME_SIZE, FRAME_SIZE))

    def update(self, tid: int, frame: np.ndarray, p_box, frame_count: int, all_boxes=None) -> tuple:
        """
        Call this once per track per frame. Returns (is_violent: bool, confidence: float)
        -- this is a CACHED result most of the time; only recomputes every
        X3D_CHECK_INTERVAL frames once the buffer holds BUFFER_SPAN frames.

        all_boxes: optional list of ALL person bboxes this frame (not just
        this track's own box) -- enables the crop to widen and include a
        nearby second person, capturing interaction signal instead of a
        tight solo crop. Pass main.py's `boxes` array here.

        The buffer now holds BUFFER_SPAN raw frames (a wide window, ~3 sec)
        and SUBSAMPLES 13 evenly-spaced frames from it at inference time --
        mirroring training's np.linspace sampling across a whole clip,
        instead of feeding the model a narrow burst of consecutive frames.
        """
        if tid not in self._frame_buffers:
            self._frame_buffers[tid] = deque(maxlen=BUFFER_SPAN)
            self._last_check_frame[tid] = -X3D_CHECK_INTERVAL
            self._cached_result[tid] = (False, 0.0)

        cropped = self._crop_person(frame, p_box, all_boxes=all_boxes)
        self._frame_buffers[tid].append(cropped)

        buffer_full = len(self._frame_buffers[tid]) == BUFFER_SPAN
        due_for_check = (frame_count - self._last_check_frame[tid]) >= X3D_CHECK_INTERVAL

        if buffer_full and due_for_check:
            self._last_check_frame[tid] = frame_count
            self._cached_result[tid] = self._run_inference(self._frame_buffers[tid])

        return self._cached_result[tid]

    def _run_inference(self, frames_deque: deque) -> tuple:
        # Subsample CLIP_FRAMES evenly across the wide buffer -- same
        # np.linspace logic train_x3d_full.py used per-clip, applied here
        # to the rolling window instead of a whole offline file.
        all_frames = list(frames_deque)
        indices = np.linspace(0, len(all_frames) - 1, CLIP_FRAMES).astype(int)
        sampled = [all_frames[i] for i in indices]

        frames = np.stack(sampled, axis=0).astype(np.float32) / 255.0
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
          - buffer_fill: how full the rolling frame buffer is (0 to BUFFER_SPAN)
          - frames_until_next_check: countdown to next real inference
        """
        is_violent, conf = self._cached_result.get(tid, (False, 0.0))
        buf_len = len(self._frame_buffers.get(tid, []))
        return {
            "confidence": conf,
            "is_violent": is_violent,
            "buffer_fill": buf_len,
            "buffer_target": BUFFER_SPAN,
        }

    def cleanup_track(self, tid: int):
        """Call when a track goes stale, mirrors main.py's existing stale-cleanup pattern."""
        self._frame_buffers.pop(tid, None)
        self._last_check_frame.pop(tid, None)
        self._cached_result.pop(tid, None)
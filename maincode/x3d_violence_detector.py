"""
EcoVision -- X3D-XS Live Inference Wrapper with Coordinate Registry
==================================================================
Loads your trained x3d_xs_violence_best.pt and runs it against a
ROLLING BUFFER of recent frames, not every single frame -- this is
the "triggered, not continuous" design locked in earlier.
"""

import os
import numpy as np
import torch
import torch.nn as nn
from collections import deque
import cv2

try:
    from pytorchvideo.models.hub import x3d_xs
except ImportError:
    raise SystemExit("Missing pytorchvideo. Run: pip install pytorchvideo")

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
DETECTOR_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECTOR_PROJECT_ROOT = os.path.dirname(DETECTOR_CURRENT_DIR)

MODEL_PATH        = os.path.join(DETECTOR_PROJECT_ROOT, "weights", "x3d_xs_violence_best.pt")
CLIP_FRAMES        = 13
FRAME_SIZE         = 160
BUFFER_SPAN         = 45     
X3D_CHECK_INTERVAL  = 15
VIOLENCE_CONFIDENCE_THRESHOLD = 0.40

class X3DViolenceDetector:
    """
    Wraps your trained X3D-XS checkpoint for live, per-track inference.
    One instance is created once in main.py and reused across the whole run.
    """

    def __init__(self, model_path: str = MODEL_PATH, device: str = None):
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
        self._cached_result: dict[int, tuple] = {}   
        self._real_inference_count: dict[int, int] = {}   
        self._latest_live_crops: dict[int, np.ndarray] = {}
        
        # SUPPORT EXTENSION: In-memory dictionary tracking coordinates fed to the model
        self._active_crop_boxes: dict[int, tuple] = {}

    def _crop_person(self, frame: np.ndarray, p_box, all_boxes=None, tid: int = None) -> np.ndarray:
        """
        Crops the frame around a person's bbox, padded generously enough to include a nearby second person.
        """
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in p_box]

        pad_x = int((x2 - x1) * 0.6)
        pad_y = int((y2 - y1) * 0.6)
        cx1, cy1 = x1 - pad_x, y1 - pad_y
        cx2, cy2 = x2 + pad_x, y2 + pad_y

        if all_boxes is not None:
            my_center_x = (x1 + x2) / 2
            my_center_y = (y1 + y2) / 2
            search_radius = max(x2 - x1, y2 - y1) * 3   

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

        # Cache coordinates directly inside track mapping storage vectors
        if tid is not None:
            self._active_crop_boxes[tid] = (cx1, cy1, cx2, cy2)

        if cx2 <= cx1 or cy2 <= cy1:
            return np.zeros((FRAME_SIZE, FRAME_SIZE, 3), dtype=np.uint8)

        crop = frame[cy1:cy2, cx1:cx2]
        return cv2.resize(crop, (FRAME_SIZE, FRAME_SIZE))

    def update(self, tid: int, frame: np.ndarray, p_box, frame_count: int, all_boxes=None) -> tuple:
        if tid not in self._frame_buffers:
            self._frame_buffers[tid] = deque(maxlen=BUFFER_SPAN)
            self._last_check_frame[tid] = -X3D_CHECK_INTERVAL
            self._cached_result[tid] = (False, 0.0)

        # Adjusted call parameters to pass the unique Track ID into coordinate matrices
        cropped = self._crop_person(frame, p_box, all_boxes=all_boxes, tid=tid)
        self._frame_buffers[tid].append(cropped)
        self._latest_live_crops[tid] = cropped

        buffer_full = len(self._frame_buffers[tid]) == BUFFER_SPAN
        due_for_check = (frame_count - self._last_check_frame[tid]) >= X3D_CHECK_INTERVAL

        if buffer_full and due_for_check:
            self._last_check_frame[tid] = frame_count
            self._cached_result[tid] = self._run_inference(self._frame_buffers[tid], tid=tid)

        return self._cached_result[tid]

    def _run_inference(self, frames_deque: deque, tid: int = None) -> tuple:
        all_frames = list(frames_deque)
        if len(all_frames) < CLIP_FRAMES:
            all_frames = all_frames + [all_frames[-1]] * (CLIP_FRAMES - len(all_frames))
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

        if tid is not None:
            self._real_inference_count[tid] = self._real_inference_count.get(tid, 0) + 1

        return is_violent, violence_prob

    def get_crop_box(self, tid: int) -> tuple:
        """Exposes coordinates to main.py to fulfill patch execution boundaries."""
        return self._active_crop_boxes.get(tid, None)

    def get_latest_live_crop(self, tid: int) -> np.ndarray:
        return self._latest_live_crops.get(tid, None)

    def get_inference_count(self, tid: int) -> int:
        return self._real_inference_count.get(tid, 0)

    def get_debug_info(self, tid: int) -> dict:
        is_violent, conf = self._cached_result.get(tid, (False, 0.0))
        buf_len = len(self._frame_buffers.get(tid, []))
        return {
            "confidence": conf,
            "is_violent": is_violent,
            "buffer_fill": buf_len,
            "buffer_target": BUFFER_SPAN,
        }

    def force_inference(self, tid: int) -> tuple:
        buf = self._frame_buffers.get(tid)
        if buf is None or len(buf) < 5:
            return self._cached_result.get(tid, (False, 0.0))

        result = self._run_inference(buf, tid=tid)
        self._cached_result[tid] = result
        return result

    def cleanup_track(self, tid: int):
        self._frame_buffers.pop(tid, None)
        self._last_check_frame.pop(tid, None)
        self._cached_result.pop(tid, None)
        self._real_inference_count.pop(tid, None)
        self._latest_live_crops.pop(tid, None)
        self._active_crop_boxes.pop(tid, None)
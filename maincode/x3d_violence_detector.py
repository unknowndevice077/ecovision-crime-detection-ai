"""
x3d_violence_detector.py -- stabilized version with EMA smoothing,
configurable hysteresis, and full diagnostic logging.
"""

import os
import csv
import json
import time
import numpy as np
import torch
import torch.nn as nn
from collections import deque
from pathlib import Path
import cv2

try:
    from pytorchvideo.models.hub import x3d_xs
except ImportError:
    raise SystemExit("Missing pytorchvideo. Run: pip install pytorchvideo")

DETECTOR_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DETECTOR_PROJECT_ROOT = os.path.dirname(DETECTOR_CURRENT_DIR)

# --- Tunables now live in config.json's detection.violence block so they can
# be adjusted per-environment (dev/prod) without editing source. These
# defaults are used whenever config.json is missing, unreadable, or doesn't
# have that block yet -- keeps this module usable standalone by the
# calibration/eval scripts, which don't otherwise touch config.json. ---
_DEFAULT_VIOLENCE_CFG = {
    "model_path": "weights/x3d_xs_violence_best.pt",
    "confidence_threshold": 0.40,
    "consecutive_required": 2,
    "buffer_span": 45,
    "min_buffer_for_inference": 20,
    "check_interval": 15,
    "clip_frames": 13,
    "frame_size": 160,
    "ema_alpha": 0.35,
    "release_hysteresis_margin": 0.07,
    "bystander_merge_radius_mult": 1.3,

    # --- scene (whole-frame) mode ---
    # "track" = per-person crops, the original behaviour and still the default.
    # "scene" = one verdict for the whole frame, no pose gate.
    # "both"  = run scene for the alert and keep per-track for the overlay.
    "mode": "track",
    # Scene mode gets its OWN threshold and confirmation count. The per-track
    # values were tuned against a pipeline where force_inference() bypassed
    # smoothing entirely, so they do not transfer. Measured on the 769-clip
    # held-out set (leakage excluded):
    #   t=0.40, consec=2 -> 69.4 acc / 53.8 recall / 79.0 prec / 14.7 FPR
    #   t=0.50, consec=1 -> 79.0 acc / 76.0 recall / 81.2 prec / 18.0 FPR
    # consecutive=2 double-counts the smoothing: the EMA and the sticky
    # release already provide flap resistance on their own.
    "scene_confidence_threshold": 0.50,
    "scene_consecutive_required": 1,
    # Whole-frame weights. train_x3d_full.py resizes the WHOLE frame unless
    # --crop-boxes-path is passed, so the "baseline" checkpoint is the one
    # actually matched to scene inference; x3d_xs_violence_best.pt is the
    # crop-trained model and scores 5.3pp lower under scene inference.
    "scene_model_path": "weights/x3d_xs_violence_best_baseline_backup.pt",

    # --- tiled mode (city-scale full-view cameras, see TiledSceneViolenceDetector) ---
    # Grid density and check cadence are a measured cost/recall tradeoff, not
    # arbitrary. On a GTX 1660 SUPER, clean (GPU-idle) full-pipeline measurement:
    #   grid=4 (17 tiles), interval=15 -> 57.80 ms/frame, 0.58x real-time --
    #       cannot sustain even one live camera.
    #   grid=3 (10 tiles), interval=15 -> 34.34 ms/frame, 0.97x real-time,
    #       29/30 recall (vs 30/30 at grid=4) on the same shrunk-to-9%-person-
    #       height clips -- essentially free in recall, still short on cost.
    #   grid=3, interval=20             -> 28.48 ms/frame, 1.17x real-time,
    #       29/30 recall -- clears the budget with margin. These are the
    #       defaults below. Re-measure before changing GPU/hardware.
    "tile_grid": 3,
    "tile_overlap": 0.25,
    "tile_check_interval": 20,
}


def _load_violence_config() -> dict:
    cfg_path = os.path.join(DETECTOR_PROJECT_ROOT, "config.json")
    merged = dict(_DEFAULT_VIOLENCE_CFG)
    try:
        with open(cfg_path, "r") as f:
            root_cfg = json.load(f)
        merged.update(root_cfg.get("detection", {}).get("violence", {}))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return merged


_VIOLENCE_CFG = _load_violence_config()

MODEL_PATH = os.path.normpath(os.path.join(DETECTOR_PROJECT_ROOT, _VIOLENCE_CFG["model_path"]))
CLIP_FRAMES = _VIOLENCE_CFG["clip_frames"]
FRAME_SIZE = _VIOLENCE_CFG["frame_size"]
BUFFER_SPAN = _VIOLENCE_CFG["buffer_span"]
# Live inference no longer waits for the buffer to hit BUFFER_SPAN before it
# ever fires -- many source clips (RWF-2000/SCVD) are only ~30 frames long,
# shorter than the old 45-frame requirement, so short-lived tracks (both in
# eval clips and in real fast-moving camera footage) previously never got a
# real sliding-window inference at all and fell back on a single, often
# buffer-starved, end-of-clip force-flush. Firing once at least this many
# frames are buffered lets short tracks get scored through the normal path.
MIN_BUFFER_FOR_INFERENCE = _VIOLENCE_CFG["min_buffer_for_inference"]
X3D_CHECK_INTERVAL = _VIOLENCE_CFG["check_interval"]
VIOLENCE_CONFIDENCE_THRESHOLD = _VIOLENCE_CFG["confidence_threshold"]

VIOLENCE_CONSECUTIVE_REQUIRED = _VIOLENCE_CFG["consecutive_required"]
BYSTANDER_MERGE_RADIUS_MULT = _VIOLENCE_CFG["bystander_merge_radius_mult"]

VIOLENCE_MODE = _VIOLENCE_CFG["mode"]
SCENE_CONFIDENCE_THRESHOLD = _VIOLENCE_CFG["scene_confidence_threshold"]
SCENE_CONSECUTIVE_REQUIRED = _VIOLENCE_CFG["scene_consecutive_required"]
SCENE_MODEL_PATH = os.path.normpath(
    os.path.join(DETECTOR_PROJECT_ROOT, _VIOLENCE_CFG["scene_model_path"]))

TILE_GRID = _VIOLENCE_CFG["tile_grid"]
TILE_OVERLAP = _VIOLENCE_CFG["tile_overlap"]
TILE_CHECK_INTERVAL = _VIOLENCE_CFG["tile_check_interval"]

# --- EMA smoothing on raw confidence, per track ---
EMA_ALPHA = _VIOLENCE_CFG["ema_alpha"]          # 0 = no smoothing (raw), 1 = fully smoothed/slow to react
# --- Separate release threshold, prevents flapping right at the line ---
RELEASE_HYSTERESIS_MARGIN = _VIOLENCE_CFG["release_hysteresis_margin"]   # confidence must drop this far below threshold to release

# --- diagnostic logging ---
ENABLE_DIAGNOSTIC_LOG = True
DIAGNOSTIC_LOG_PATH = os.path.join(DETECTOR_PROJECT_ROOT, "logs", "x3d_confidence_trace.csv")
# Separate file for scene mode: in scene/both the per-track and scene
# detectors are both alive, and two csv.writers appending to one path with
# independent buffers produce interleaved, half-written rows.
SCENE_DIAGNOSTIC_LOG_PATH = os.path.join(
    DETECTOR_PROJECT_ROOT, "logs", "x3d_scene_confidence_trace.csv")
LOG_FLUSH_EVERY = 20            # rows between forced disk flushes (was every row)
DIAGNOSTIC_LOG_MAX_BYTES = 50 * 1024 * 1024   # rotate past 50MB so a 24/7 run doesn't grow this file forever


def _smooth_and_confirm(prev_ema: float, prev_hits: int, was_confirmed: bool,
                        raw_conf: float, threshold: float = None,
                        consecutive: int = None) -> tuple:
    """EMA smoothing + consecutive-hit confirmation + sticky release.

    Extracted verbatim from X3DViolenceDetector.update() so the per-track and
    scene-level detectors share one definition of "is this confirmed
    violence" -- two copies of this would inevitably drift, and the release
    hysteresis in particular is easy to get subtly wrong.

    Pure function: takes the previous state, returns the next one. Returns
    (confirmed, ema, hits).

    prev_ema=None means "no history yet" and seeds the EMA from the first
    real reading instead of blending it against a zero. That matters: with
    EMA_ALPHA=0.35 a cold start damps the first inference to 0.65*raw, so a
    genuine 0.62 lands at 0.40 -- right on the threshold -- and on a 30-frame
    clip (only two inference points at check_interval=15) there is no time to
    recover. This is what the per-track path's original
    `self._ema_conf.get(tid, raw_conf)` default was reaching for; __init__
    seeding 0.0 meant it never actually fired.
    """
    # threshold/consecutive default to the per-track globals, so the
    # X3DViolenceDetector call site behaves exactly as before. Scene mode
    # passes its own -- the per-track values were tuned against a pipeline
    # where force_inference() skipped this function entirely, so they do not
    # transfer.
    thr = VIOLENCE_CONFIDENCE_THRESHOLD if threshold is None else threshold
    need = VIOLENCE_CONSECUTIVE_REQUIRED if consecutive is None else consecutive

    ema = raw_conf if prev_ema is None else EMA_ALPHA * prev_ema + (1 - EMA_ALPHA) * raw_conf

    # The consecutive-hit counter runs off the smoothed value, not the raw one.
    hits = prev_hits + 1 if ema >= thr else 0

    if not was_confirmed:
        confirmed = hits >= need
    else:
        # sticky release: only drop out once EMA falls well below threshold
        confirmed = ema >= (thr - RELEASE_HYSTERESIS_MARGIN)

    return confirmed, ema, hits


class X3DViolenceDetector:
    def __init__(self, model_path: str = MODEL_PATH, device: str = None,
                 log_path: str = DIAGNOSTIC_LOG_PATH):
        # log_path is parameterised because main.py holds a per-track detector
        # AND a scene detector at the same time in scene/both mode. Two
        # instances opening the same CSV in append mode with independent
        # LOG_FLUSH_EVERY buffers interleave their flushes and split rows
        # mid-line, corrupting the file confidence_trace_plotter.py reads.
        self._log_path = log_path
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
        # NOTE: the head is left exactly as pytorchvideo builds it. See
        # _run_inference for why the softmax that used to be applied there is
        # gone -- the head already ends in nn.Softmax, so this model returns
        # PROBABILITIES, and softmaxing them again compressed every confidence
        # this system ever reported into [0.2689, 0.7311].
        #
        # Replacing that Softmax with Identity looks like the tidier fix and is
        # wrong: the head runs proj -> activation -> output_pool, and the
        # tensor reaching output_pool is (N, 2, 10, 1, 1). It averages
        # probabilities over 10 TEMPORAL positions. Identity would average
        # logits instead, which is a different computation (Jensen), not a
        # rescaling -- measured on 120 real clips it flipped a verdict at a
        # logit gap of -1.30, nowhere near a floating-point tie.
        self.model.load_state_dict(torch.load(model_path, map_location=self.device))
        self.model.to(self.device)
        self.model.eval()

        # Input geometry is a property of the WEIGHTS, not a user preference.
        # config.json can state a frame_size, but it has no way to be right
        # about one: nothing validates it against the checkpoint. Training
        # now writes a .meta.json beside the weights, and where it exists it
        # wins -- the same silent mismatch as whole-frame-vs-crop otherwise
        # just costs accuracy with nothing to point at.
        self.frame_size = FRAME_SIZE
        self.clip_frames = CLIP_FRAMES
        self.meta = None
        meta_path = Path(str(model_path) + ".meta.json")
        if meta_path.exists():
            try:
                self.meta = json.loads(meta_path.read_text())
            except Exception as e:
                print(f"[X3D] WARNING: unreadable {meta_path.name}: {e}")
        if self.meta:
            for key, attr in (("frame_size", "frame_size"), ("clip_frames", "clip_frames")):
                want = self.meta.get(key)
                if isinstance(want, int) and want != getattr(self, attr):
                    print(f"[X3D] WARNING: config.json {key}={getattr(self, attr)} but these "
                          f"weights were trained at {key}={want}. Using {want} "
                          f"(the checkpoint is authoritative). Fix config.json to silence this.")
                    setattr(self, attr, want)
            repr_ = self.meta.get("input_repr")
            if repr_:
                print(f"[X3D] weights trained on {repr_} "
                      f"({self.meta.get('run_name','?')}, "
                      f"val_acc {self.meta.get('best_val_acc','?')})")
        else:
            print(f"[X3D] note: no {meta_path.name} -- cannot verify this checkpoint's "
                  f"input geometry; assuming config.json "
                  f"(frame_size={self.frame_size}, clip_frames={self.clip_frames}).")

        print(f"[X3D] Model loaded successfully from {model_path}")

        self._frame_buffers: dict[int, deque] = {}
        self._last_check_frame: dict[int, int] = {}
        self._cached_result: dict[int, tuple] = {}
        self._real_inference_count: dict[int, int] = {}
        self._latest_live_crops: dict[int, np.ndarray] = {}
        self._consecutive_hits: dict[int, int] = {}
        self._active_crop_boxes: dict[int, tuple] = {}

        # NEW: EMA state per track
        self._ema_conf: dict[int, float] = {}
        # NEW: sticky confirmed state per track (survives release margin)
        self._confirmed_state: dict[int, bool] = {}

        self._log_rows_since_flush = 0
        if ENABLE_DIAGNOSTIC_LOG:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            # Rotate the trace CSV once it gets too big -- unbounded growth
            # over a 24/7 deployment otherwise. Simple single-backup rotation
            # (not a full RotatingFileHandler) since this is a plain CSV, not
            # the logging module.
            try:
                if os.path.getsize(self._log_path) >= DIAGNOSTIC_LOG_MAX_BYTES:
                    backup_path = self._log_path + ".1"
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                    os.rename(self._log_path, backup_path)
            except OSError:
                pass
            self._log_file = open(self._log_path, "a", newline="")
            self._log_writer = csv.writer(self._log_file)
            if self._log_file.tell() == 0:
                self._log_writer.writerow([
                    "timestamp", "track_id", "frame_count", "raw_conf",
                    "ema_conf", "consecutive_hits", "confirmed", "buffer_fill"
                ])
                self._log_file.flush()
        else:
            self._log_file = None
            self._log_writer = None

    def reset_all_tracks(self):
        """Clear every per-track dict. Callers that reuse one detector
        instance across many independent clips/videos (batch eval scripts)
        MUST call this between clips -- track IDs from a previous clip can
        collide with a new clip's IDs, and dicts like _latest_live_crops
        hold actual image arrays that otherwise just accumulate forever
        across a long batch run until the process runs out of memory."""
        self._frame_buffers.clear()
        self._last_check_frame.clear()
        self._cached_result.clear()
        self._real_inference_count.clear()
        self._latest_live_crops.clear()
        self._consecutive_hits.clear()
        self._active_crop_boxes.clear()
        self._ema_conf.clear()
        self._confirmed_state.clear()

    def _log_row(self, tid, frame_count, raw_conf, ema_conf, hits, confirmed, buf_fill):
        if self._log_writer is None:
            return
        self._log_writer.writerow([
            round(time.time(), 3), tid, frame_count,
            round(raw_conf, 4), round(ema_conf, 4), hits, confirmed, buf_fill
        ])
        # Flushing every row forces a blocking OS write inside the per-track
        # hot path (this runs once per track every X3D_CHECK_INTERVAL frames,
        # i.e. on the same cadence as a live inference). Batch instead: flush
        # every LOG_FLUSH_EVERY rows so a crash loses at most a few rows of
        # diagnostics instead of stalling the frame-processing thread on disk
        # I/O every time.
        self._log_rows_since_flush += 1
        if self._log_rows_since_flush >= LOG_FLUSH_EVERY:
            self._log_file.flush()
            self._log_rows_since_flush = 0

    def _crop_person(self, frame: np.ndarray, p_box, all_boxes=None, tid: int = None) -> np.ndarray:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in p_box]

        pad_x = int((x2 - x1) * 0.6)
        pad_y = int((y2 - y1) * 0.6)
        cx1, cy1 = x1 - pad_x, y1 - pad_y
        cx2, cy2 = x2 + pad_x, y2 + pad_y

        if all_boxes is not None:
            my_center_x = (x1 + x2) / 2
            my_center_y = (y1 + y2) / 2
            search_radius = max(x2 - x1, y2 - y1) * BYSTANDER_MERGE_RADIUS_MULT

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

        if tid is not None:
            self._active_crop_boxes[tid] = (cx1, cy1, cx2, cy2)

        if cx2 <= cx1 or cy2 <= cy1:
            return np.zeros((self.frame_size, self.frame_size, 3), dtype=np.uint8)

        crop = frame[cy1:cy2, cx1:cx2]
        return cv2.resize(crop, (self.frame_size, self.frame_size))

    def _crop_from_cached_box(self, frame: np.ndarray, box) -> np.ndarray:
        """Cheap crop that reuses a previously computed (already-merged) box
        region on the CURRENT frame -- still fresh pixel content, just skips
        the O(num_boxes) bystander-merge search that _crop_person does."""
        h, w = frame.shape[:2]
        cx1, cy1, cx2, cy2 = box
        cx1, cy1 = max(0, cx1), max(0, cy1)
        cx2, cy2 = min(w, cx2), min(h, cy2)
        if cx2 <= cx1 or cy2 <= cy1:
            return np.zeros((self.frame_size, self.frame_size, 3), dtype=np.uint8)
        crop = frame[cy1:cy2, cx1:cx2]
        return cv2.resize(crop, (self.frame_size, self.frame_size))

    def update(self, tid: int, frame: np.ndarray, p_box, frame_count: int, all_boxes=None) -> tuple:
        if tid not in self._frame_buffers:
            self._frame_buffers[tid] = deque(maxlen=BUFFER_SPAN)
            self._last_check_frame[tid] = -X3D_CHECK_INTERVAL
            self._cached_result[tid] = (False, 0.0)
            self._ema_conf[tid] = 0.0
            self._confirmed_state[tid] = False

        due_for_check = (frame_count - self._last_check_frame[tid]) >= X3D_CHECK_INTERVAL

        # The bystander-merge search inside _crop_person is O(num_boxes) and
        # ran on EVERY frame for every tracked person before this fix, just to
        # keep the rolling buffer filled -- most of that work is thrown away
        # since only the frames sampled at inference time matter for the
        # final decision. Recompute the full (merged) crop region only on the
        # same cadence inference actually runs at; in between, reuse that
        # region on the current frame's pixels (position stays fixed for up
        # to X3D_CHECK_INTERVAL frames, content stays live).
        if due_for_check or tid not in self._active_crop_boxes:
            cropped = self._crop_person(frame, p_box, all_boxes=all_boxes, tid=tid)
        else:
            cropped = self._crop_from_cached_box(frame, self._active_crop_boxes[tid])
        self._frame_buffers[tid].append(cropped)
        self._latest_live_crops[tid] = cropped

        buffer_ready = len(self._frame_buffers[tid]) >= MIN_BUFFER_FOR_INFERENCE

        if buffer_ready and due_for_check:
            self._last_check_frame[tid] = frame_count
            raw_is_violent, raw_conf = self._run_inference(self._frame_buffers[tid], tid=tid)

            confirmed, ema, hits = _smooth_and_confirm(
                prev_ema=self._ema_conf.get(tid, raw_conf),
                prev_hits=self._consecutive_hits.get(tid, 0),
                was_confirmed=self._confirmed_state.get(tid, False),
                raw_conf=raw_conf,
            )
            self._ema_conf[tid] = ema
            self._consecutive_hits[tid] = hits
            self._confirmed_state[tid] = confirmed
            self._cached_result[tid] = (confirmed, raw_conf)

            self._log_row(
                tid, frame_count, raw_conf, ema,
                self._consecutive_hits[tid], confirmed, len(self._frame_buffers[tid])
            )

        return self._cached_result[tid]

    def _prepare_clip_array(self, frames_deque: deque) -> np.ndarray:
        """Sample + normalize one buffer into a (C, T, H, W) float32 array,
        everything _run_inference did before the model call. Factored out so
        _run_inference_batch can stack many clips into ONE tensor instead of
        making N separate model() calls -- see that method for why this
        matters (measured 2.7x speedup for a 17-tile batch)."""
        all_frames = list(frames_deque)
        if len(all_frames) < self.clip_frames:
            all_frames = all_frames + [all_frames[-1]] * (self.clip_frames - len(all_frames))
        indices = np.linspace(0, len(all_frames) - 1, self.clip_frames).astype(int)
        sampled = [all_frames[i] for i in indices]

        frames = np.stack(sampled, axis=0).astype(np.float32) / 255.0
        frames = (frames - 0.45) / 0.225
        return frames.transpose(3, 0, 1, 2)   # (T,H,W,C) -> (C,T,H,W)

    def _run_inference(self, frames_deque: deque, tid: int = None) -> tuple:
        tensor = torch.from_numpy(self._prepare_clip_array(frames_deque))
        tensor = tensor.float().unsqueeze(0).to(self.device)

        with torch.no_grad():
            # The head already ends in nn.Softmax, so `outputs` IS a probability
            # pair summing to 1. The torch.softmax that used to sit here was a
            # SECOND one, and softmax over [p, 1-p] can only produce values in
            # [sigmoid(-1), sigmoid(1)] = [0.2689, 0.7311]. Every confidence
            # this system has ever reported, logged, or thresholded was squeezed
            # into that 0.46-wide band -- in the 758-clip scene eval, 79% of
            # clips came back as one of exactly two values.
            #
            # Removing it is decision-identical at threshold 0.5, because
            # softmax is monotonic and softmax(p)[1] >= 0.5 <=> p[1] >= 0.5.
            # Verified on 120 real clips: 0 verdict changes. Thresholds OTHER
            # than 0.5 do move, and should -- the old sweep's "0.40" was really
            # testing a true probability of 0.298.
            outputs = self.model(tensor)
            violence_prob = float(outputs[0][1])

        is_violent = violence_prob >= VIOLENCE_CONFIDENCE_THRESHOLD

        if tid is not None:
            self._real_inference_count[tid] = self._real_inference_count.get(tid, 0) + 1

        return is_violent, violence_prob

    def _run_inference_batch(self, deques: list, tid: int = None) -> list:
        """Same computation as calling _run_inference() once per deque, as ONE
        GPU call instead of len(deques) sequential ones.

        Exists for TiledSceneViolenceDetector: measured on this GPU, 17
        sequential X3D-XS calls cost 622ms; one batched call with the same 17
        clips costs 230ms (2.7x). At check_interval=15 that periodic burst was
        the dominant per-frame cost (49.9ms/frame average, ~15x whole-frame's
        3.3ms) and left tiled mode UNABLE to sustain one real-time 30fps
        camera (0.67x budget) -- Python/kernel-launch dispatch overhead paid
        17 times, not spatial resolution or model size, was the bottleneck.
        Batching brings projected capacity to ~1.5x real-time.

        Returns a list of (is_violent, raw_conf), same order as `deques`.
        Empty deques are skipped (caller must handle the gap; TiledScene...
        skips tiles with no frames yet, matching prior per-tile behaviour).
        """
        idx_map = [i for i, d in enumerate(deques) if len(d) > 0]
        if not idx_map:
            return [(False, 0.0)] * len(deques)

        arrays = [self._prepare_clip_array(deques[i]) for i in idx_map]
        batch = torch.from_numpy(np.stack(arrays, axis=0)).float().to(self.device)

        with torch.no_grad():
            outputs = self.model(batch)   # (N, 2) probabilities -- see _run_inference
            probs = outputs[:, 1].tolist()

        if tid is not None:
            self._real_inference_count[tid] = (
                self._real_inference_count.get(tid, 0) + len(idx_map))

        out = [(False, 0.0)] * len(deques)
        for pos, orig_i in enumerate(idx_map):
            p = probs[pos]
            out[orig_i] = (p >= VIOLENCE_CONFIDENCE_THRESHOLD, p)
        return out

    def get_crop_box(self, tid: int) -> tuple:
        return self._active_crop_boxes.get(tid, None)

    def get_latest_live_crop(self, tid: int) -> np.ndarray:
        return self._latest_live_crops.get(tid, None)

    def get_inference_count(self, tid: int) -> int:
        return self._real_inference_count.get(tid, 0)

    def get_ema_confidence(self, tid: int) -> float:
        return self._ema_conf.get(tid, 0.0)

    def get_debug_info(self, tid: int) -> dict:
        is_violent, conf = self._cached_result.get(tid, (False, 0.0))
        buf_len = len(self._frame_buffers.get(tid, []))
        return {
            "confidence": conf,
            "ema_confidence": self._ema_conf.get(tid, 0.0),
            "is_violent": is_violent,
            "buffer_fill": buf_len,
            "buffer_target": BUFFER_SPAN,
            "buffer_min_for_inference": MIN_BUFFER_FOR_INFERENCE,
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
        self._consecutive_hits.pop(tid, None)
        self._ema_conf.pop(tid, None)
        self._confirmed_state.pop(tid, None)

    def close(self):
        if self._log_file:
            self._log_file.close()


class SceneViolenceDetector(X3DViolenceDetector):
    """Whole-frame violence detection -- one verdict for the scene, no tracking.

    WHY THIS EXISTS
    ---------------
    X3DViolenceDetector couples detection to tracking: a clip is only ever
    classified if one YOLO track ID survives MIN_BUFFER_FOR_INFERENCE (20)
    consecutive frames. On the 769-clip held-out set that gate silently
    discarded 132 clips (17.2%) -- 36 violent ones scored "normal" without
    the model running, and 96 normal ones scored TN for free, which is why
    the reported 23.0% FPR was really 26.3%.

    It is also a train/inference mismatch. train_x3d_full.py's
    load_clip_frames() resizes the WHOLE FRAME to FRAME_SIZE whenever no
    crop box is supplied, and --crop-boxes-path is opt-in -- so
    x3d_xs_violence_best_baseline_backup.pt was trained on whole frames
    while this module fed it person crops. Retraining on crops to close that
    gap produced parity (70.4% -> 70.4%); this class closes it from the
    other side, which removes the pose gate as a bonus.

    Subclasses X3DViolenceDetector purely to reuse model loading,
    _run_inference and the diagnostic log. The per-track machinery is unused
    here -- one buffer, one state, keyed by SCENE_TID in the inherited dicts
    so _run_inference's inference counter and _log_row keep working unchanged.
    """

    SCENE_TID = -1   # sentinel key into the inherited per-track dicts

    def __init__(self, model_path: str = None, device: str = None,
                 threshold: float = None, consecutive: int = None,
                 log_path: str = SCENE_DIAGNOSTIC_LOG_PATH):
        # Defaults to the whole-frame-trained checkpoint, not the crop one --
        # matching the representation is worth 5.3pp here. Separate log file
        # so it does not interleave with the per-track detector's, which is
        # alive at the same time in scene/both mode.
        super().__init__(model_path=model_path or SCENE_MODEL_PATH,
                         device=device, log_path=log_path)
        self.threshold = SCENE_CONFIDENCE_THRESHOLD if threshold is None else threshold
        self.consecutive = SCENE_CONSECUTIVE_REQUIRED if consecutive is None else consecutive
        print(f"[X3D] Scene mode: threshold={self.threshold} "
              f"consecutive={self.consecutive}")
        self._scene_buffer = deque(maxlen=BUFFER_SPAN)
        self._scene_last_check = -X3D_CHECK_INTERVAL
        self._scene_result = (False, 0.0)
        # None, not 0.0 -- see _smooth_and_confirm. Seeding the EMA at zero
        # damps the first inference to 0.65*raw, which on a short clip is
        # never recovered from.
        self._scene_ema = None
        self._scene_hits = 0
        self._scene_confirmed = False

    def reset_scene(self):
        """Between independent clips in a batch eval -- same role
        reset_all_tracks() plays for the per-track detector."""
        self._scene_buffer.clear()
        self._scene_last_check = -X3D_CHECK_INTERVAL
        self._scene_result = (False, 0.0)
        self._scene_ema = None
        self._scene_hits = 0
        self._scene_confirmed = False
        self._real_inference_count.pop(self.SCENE_TID, None)

    def update(self, frame: np.ndarray, frame_count: int) -> tuple:
        """Feed one full frame. Returns (is_violent, raw_conf).

        Note the signature deliberately differs from the parent's
        (tid, frame, p_box, ...) -- there is no track and no box. Callers
        must pick the right class rather than relying on polymorphism.
        """
        self._scene_buffer.append(cv2.resize(frame, (self.frame_size, self.frame_size)))

        # No MIN_BUFFER_FOR_INFERENCE gate. The buffer is scene-wide and
        # starts filling on frame 1, so requiring a warm-up here would
        # reintroduce exactly the blind spot this class removes. _run_inference
        # already pads a short buffer up to CLIP_FRAMES.
        if (frame_count - self._scene_last_check) < X3D_CHECK_INTERVAL:
            return self._scene_result

        self._scene_last_check = frame_count
        _, raw_conf = self._run_inference(self._scene_buffer, tid=self.SCENE_TID)

        confirmed, ema, hits = _smooth_and_confirm(
            prev_ema=self._scene_ema,
            prev_hits=self._scene_hits,
            was_confirmed=self._scene_confirmed,
            raw_conf=raw_conf,
            threshold=self.threshold,
            consecutive=self.consecutive,
        )
        self._scene_ema = ema
        self._scene_hits = hits
        self._scene_confirmed = confirmed
        self._scene_result = (confirmed, raw_conf)

        self._log_row(self.SCENE_TID, frame_count, raw_conf, ema,
                      hits, confirmed, len(self._scene_buffer))
        return self._scene_result

    def get_scene_debug_info(self) -> dict:
        confirmed, conf = self._scene_result
        return {
            "confidence": conf,
            "ema_confidence": self._scene_ema if self._scene_ema is not None else 0.0,
            "is_violent": confirmed,
            "buffer_fill": len(self._scene_buffer),
            "buffer_target": BUFFER_SPAN,
            "inference_count": self._real_inference_count.get(self.SCENE_TID, 0),
        }


class TiledSceneViolenceDetector(SceneViolenceDetector):
    """Scene detection on an NxN grid, so a wide camera keeps FULL coverage.

    WHY
    ---
    Measured recall against person size, replaying 40 clips the model detects
    at 1.000 confidence:

        person height   37%   30%   22%   19%   15%    9%
        detected        40/40 34/40 25/40 22/40  7/40  0/40

    Below roughly 15% of frame height the model is blind -- not less
    confident, blind. A wide street camera puts people at 6-12%, so it would
    report zero alarms forever while missing everything, which looks exactly
    like a quiet street.

    Cropping to fix that is the wrong trade: it throws away the coverage the
    camera was installed to provide, creating real blind spots to avoid a
    scale one. Tiling fixes the scale WITHOUT discarding any pixels -- a
    person at 9% of a full frame is 27% of a 3x3 tile, back in the reliable
    band, and every part of the scene is still analysed.

    Each tile carries its OWN buffer, EMA and hit counter. Sharing them would
    let a busy tile's confidence leak into a quiet one and make the temporal
    logic meaningless -- the smoothing exists to track one region over time.

    Cost is a real, measured constraint, not the naive "N*N inferences fit
    under a 500ms check-window" estimate this docstring used to state --
    that budget was wrong (the live pipeline calls update() every FRAME,
    33.33ms at 30fps, not once per check window). Clean (GPU-idle) full-
    pipeline measurement on a GTX 1660 SUPER, with batched tile inference:
        grid=4 (17 tiles), interval=15 -> 57.80 ms/frame, 0.58x real-time --
            cannot sustain even one live camera.
        grid=3 (10 tiles), interval=20 -> 28.48 ms/frame, 1.17x real-time,
            29/30 recall (vs 30/30 at grid=4) on clips shrunk to 9% person-
            height -- clears the budget with margin for one camera, at a
            cost of one clip out of 30. This is why grid=3/interval=20 are
            the config defaults (tile_grid / tile_check_interval).
    Re-measure (phase4_tile_grid_tradeoff.py) before changing GPU/hardware
    or before assuming this scales to a second concurrent camera.
    """

    def __init__(self, grid: int = None, overlap: float = None,
                 include_full_frame: bool = True, model_path: str = None,
                 device: str = None, threshold: float = None,
                 consecutive: int = None, check_interval: int = None,
                 log_path: str = SCENE_DIAGNOSTIC_LOG_PATH):
        super().__init__(model_path=model_path, device=device, threshold=threshold,
                         consecutive=consecutive, log_path=log_path)
        grid = TILE_GRID if grid is None else grid
        overlap = TILE_OVERLAP if overlap is None else overlap
        if grid < 1:
            raise ValueError("grid must be >= 1")
        if not 0.0 <= overlap < 1.0:
            raise ValueError("overlap must be in [0, 1)")
        self.grid = grid
        self.overlap = overlap
        # Tiled mode gets its OWN check cadence rather than sharing
        # X3D_CHECK_INTERVAL with scene/track modes -- its per-check cost is
        # ~17x a single scene inference (many small tiles vs one frame), so
        # the interval that keeps scene mode responsive is not necessarily
        # the one that keeps tiled mode inside the real-time budget. See the
        # class docstring for the measurement behind the default of 20.
        self._check_interval = TILE_CHECK_INTERVAL if check_interval is None else check_interval

        # Overlapping tiles, because a hard grid CUTS INCIDENTS IN HALF. Measured:
        # on clips shrunk so a person is ~9% of frame height, a non-overlapping
        # 2x2 grid scored 1/30 -- worse than a whole frame at twice that person
        # size -- because the split ran straight through the action and each
        # tile saw a fragment. Seams are blind spots of exactly the kind tiling
        # is supposed to avoid.
        #
        # With 50% overlap, consecutive tiles share half their extent, so any
        # region smaller than a tile is fully contained in at least one of them.
        # The full frame is kept as one more region, because overlap solves
        # SPLITTING but not SPAN: tiles are classified independently and never
        # pooled, so an incident larger than a tile -- two people apart, a
        # chase, a crowd surge -- is never seen whole by any of them. The
        # whole-frame pass is the only one with global context. It is nearly
        # blind at this scale on its own (0/30 measured), which is exactly why
        # it must be an ADDITION to the tiles rather than a replacement.
        # Costs one inference.
        self._boxes = self._plan_tiles(grid, overlap)
        if include_full_frame and grid > 1:
            self._boxes.append((0.0, 0.0, 1.0, 1.0))
        self._tiles: dict[int, dict] = {
            i: {"buf": deque(maxlen=BUFFER_SPAN), "ema": None, "hits": 0,
                "confirmed": False, "raw": 0.0}
            for i in range(len(self._boxes))
        }
        self._tile_last_check = -self._check_interval
        self._any_confirmed = False
        self._worst_tile = None

    @staticmethod
    def _plan_tiles(grid: int, overlap: float) -> list:
        """Fractional (x1, y1, x2, y2) boxes covering the whole frame.

        Returned as fractions so the plan is resolution-independent -- the same
        detector can take 1280x720 and 1920x1080 without recomputing.
        """
        if grid == 1:
            return [(0.0, 0.0, 1.0, 1.0)]
        # size solves: size + (grid-1)*stride = 1, with stride = size*(1-overlap)
        size = 1.0 / (1.0 + (grid - 1) * (1.0 - overlap))
        stride = size * (1.0 - overlap)
        boxes = []
        for r in range(grid):
            for c in range(grid):
                x1, y1 = c * stride, r * stride
                boxes.append((x1, y1, min(1.0, x1 + size), min(1.0, y1 + size)))
        return boxes

    def reset_scene(self):
        super().reset_scene()
        for st in self._tiles.values():
            st.update({"ema": None, "hits": 0, "confirmed": False, "raw": 0.0})
            st["buf"].clear()
        self._tile_last_check = -self._check_interval
        self._any_confirmed = False
        self._worst_tile = None

    def update(self, frame: np.ndarray, frame_count: int) -> tuple:
        """Returns (any_tile_confirmed, highest_raw_conf_across_tiles)."""
        h, w = frame.shape[:2]

        for idx, (fx1, fy1, fx2, fy2) in enumerate(self._boxes):
            # Rounded outward and clamped, so the union of tiles still covers
            # every pixel -- rounding inward would leave uninspected seams.
            x1, y1 = int(fx1 * w), int(fy1 * h)
            x2, y2 = min(w, int(round(fx2 * w))), min(h, int(round(fy2 * h)))
            tile = frame[y1:y2, x1:x2]
            if tile.size:
                self._tiles[idx]["buf"].append(
                    cv2.resize(tile, (self.frame_size, self.frame_size)))

        if (frame_count - self._tile_last_check) < self._check_interval:
            return self._any_confirmed, max(
                (st["raw"] for st in self._tiles.values()), default=0.0)

        self._tile_last_check = frame_count
        any_conf, best_raw, best_idx = False, 0.0, None

        # ONE batched GPU call for every tile instead of len(tiles) sequential
        # ones. Measured: 17 sequential X3D-XS calls cost 622ms; one batch of
        # 17 costs 230ms (2.7x) -- Python/kernel-launch dispatch overhead paid
        # once instead of 17 times. That periodic burst was the dominant cost
        # in tiled mode's per-frame average (49.9ms vs whole-frame's 3.3ms)
        # and left it unable to sustain even one real-time 30fps camera
        # (0.67x budget); batched, projected capacity is ~1.5x.
        order = list(self._tiles.keys())
        results = self._run_inference_batch(
            [self._tiles[i]["buf"] for i in order], tid=self.SCENE_TID)

        for idx, (_, raw) in zip(order, results):
            st = self._tiles[idx]
            if not st["buf"]:
                continue
            confirmed, ema, hits = _smooth_and_confirm(
                prev_ema=st["ema"], prev_hits=st["hits"],
                was_confirmed=st["confirmed"], raw_conf=raw,
                threshold=self.threshold, consecutive=self.consecutive)
            st.update({"ema": ema, "hits": hits, "confirmed": confirmed, "raw": raw})
            any_conf = any_conf or confirmed
            if ema > best_raw:
                best_raw, best_idx = ema, idx

        self._any_confirmed = any_conf
        self._worst_tile = best_idx
        peak_raw = max((st["raw"] for st in self._tiles.values()), default=0.0)
        self._scene_result = (any_conf, peak_raw)
        self._scene_ema = best_raw
        self._log_row(self.SCENE_TID, frame_count, peak_raw, best_raw,
                      max((st["hits"] for st in self._tiles.values()), default=0),
                      any_conf, len(self._tiles[0]["buf"]) if self._tiles else 0)
        return any_conf, peak_raw

    def get_tile_state(self) -> list:
        """Per-region (box, ema, confirmed) -- lets a caller draw WHERE in the
        scene something fired, which a single scene verdict cannot. Boxes are
        fractional; the last one is the whole frame when include_full_frame is
        on, so callers must not assume a regular grid."""
        return [(self._boxes[i],
                 st["ema"] if st["ema"] is not None else 0.0,
                 st["confirmed"])
                for i, st in self._tiles.items()]
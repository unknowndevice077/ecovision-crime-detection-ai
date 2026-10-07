"""
Behaviour rules over tracked people -- no training, no extra model.

The pose tracker already gives every person a box and 17 keypoints each
frame. These rules turn short histories of those into two events that a clip
classifier is bad at and that public training data does not cover:

  KNOCKDOWN    a person who was standing is on the ground within about a
               second, with someone else right next to them
  SNATCH_RUN   two people come together, then one of them, who was walking or
               standing, suddenly runs away from the other

Neither is an alert on its own -- people sit down, kids run. They are
evidence that other detectors combine with (see main.py). Every threshold is
in body-heights or seconds, never pixels, so the same numbers work for a
person 40 px or 400 px tall.

Pure Python + numpy, stateful per camera: call update() once per tracked
frame with that frame's tracks; it returns the events that fired.
"""
from collections import deque

import numpy as np

# keypoint indices (COCO): shoulders 5,6 / hips 11,12 / ankles 15,16
L_SH, R_SH, L_HIP, R_HIP, L_ANK, R_ANK = 5, 6, 11, 12, 15, 16
KP_CONF = 0.4

HISTORY_S = 4.0

# KNOCKDOWN
UPRIGHT_ASPECT = 1.3        # box h/w at or above this = upright (with torso check)
DOWN_ASPECT = 1.0           # box w/h at or above this = on the ground
UPRIGHT_TORSO_DEG = 35      # torso within this of vertical = upright
DOWN_TORSO_DEG = 60         # torso beyond this from vertical = lying
DOWN_HOLD_S = 0.5           # must stay down this long (not a stumble)
UPRIGHT_HOLD_S = 0.5        # must have been upright this long before
FALL_WITHIN_S = 1.2         # upright -> down this fast (not lying down slowly)
HIP_DROP_FRAC = 0.25        # hips move down by this x standing height
NEARBY_HEIGHTS = 1.5        # someone else within this x standing height
EDGE_PX = 4                 # boxes touching the frame edge are partial; ignored

# SNATCH_RUN
CLOSE_HEIGHTS = 0.8         # centres within this x height = "together"
RUN_SPEED = 1.6             # body-heights per second; walking is ~0.8
CALM_BEFORE = 0.8           # the runner was slower than this at the encounter
RUN_HOLD_S = 0.4            # running sustained this long
APART_HEIGHTS = 1.0         # and the gap to the other person grew by this
WINDOW_AFTER_S = 2.5        # the run starts within this after the encounter
OTHER_SLOW_FRAC = 0.6       # the other person is not running alongside

COOLDOWN_S = 10.0


def _torso_deg(k, c):
    if min(c[L_SH], c[R_SH], c[L_HIP], c[R_HIP]) < KP_CONF:
        return None
    sh = (k[L_SH] + k[R_SH]) / 2
    hp = (k[L_HIP] + k[R_HIP]) / 2
    v = sh - hp
    if not np.any(v):
        return None
    return float(np.degrees(np.arctan2(abs(v[0]), abs(v[1]) + 1e-6)))


def _hip_y(k, c, box):
    if min(c[L_HIP], c[R_HIP]) >= KP_CONF:
        return float((k[L_HIP][1] + k[R_HIP][1]) / 2)
    return float((box[1] + box[3]) / 2)


class BehaviorRules:
    def __init__(self, frame_size):
        self.W, self.H = frame_size
        self.hist = {}            # tid -> deque[(t, box, posture, hip_y, height_if_upright)]
        self.last_fire = {}       # (event, tid) -> t
        self.encounters = {}      # tid -> deque[(t, other_tid, height)]

    def _posture(self, box, k, c):
        w, h = box[2] - box[0], box[3] - box[1]
        if w <= 0 or h <= 0:
            return None
        if (box[0] <= EDGE_PX or box[1] <= EDGE_PX or box[2] >= self.W - EDGE_PX or box[3] >= self.H - EDGE_PX):
            return None
        deg = _torso_deg(k, c)
        if h / w >= UPRIGHT_ASPECT and (deg is None or deg <= UPRIGHT_TORSO_DEG):
            return "up"
        if w / h >= DOWN_ASPECT and (deg is None or deg >= DOWN_TORSO_DEG):
            return "down"
        if deg is not None and deg >= DOWN_TORSO_DEG and w / h >= 0.8:
            return "down"
        return "mid"

    def _fire(self, ev, tid, t):
        if t - self.last_fire.get((ev, tid), -1e9) < COOLDOWN_S:
            return False
        self.last_fire[(ev, tid)] = t
        return True

    @staticmethod
    def _speed(h, t0, t1, height):
        pts = [(t, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for t, b, *_ in h if t0 <= t <= t1]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 0.15:
            return 0.0
        d = np.hypot(pts[-1][1] - pts[0][1], pts[-1][2] - pts[0][2])
        return d / (pts[-1][0] - pts[0][0]) / max(height, 1.0)

    def update(self, t, tids, boxes, kpts, kconfs):
        """tids/boxes/kpts/kconfs for every tracked person in this frame.
        Returns a list of (event, tid, info) that fired at time t."""
        events = []
        live = set()
        for tid, box, k, c in zip(tids, boxes, kpts, kconfs):
            tid = int(tid)
            live.add(tid)
            p = self._posture(box, k, c)
            h = self.hist.setdefault(tid, deque())
            h.append((t, np.asarray(box, float), p, _hip_y(k, c, box), box[3] - box[1]))
            while h and t - h[0][0] > HISTORY_S:
                h.popleft()
        for tid in list(self.hist):
            if tid not in live and self.hist[tid] and t - self.hist[tid][-1][0] > 2.0:
                del self.hist[tid]
                self.encounters.pop(tid, None)

        cur = {tid: self.hist[tid][-1] for tid in live}
        heights = {tid: self._stand_height(tid) for tid in live}

        # encounters: who was close to whom, recently
        ids = sorted(live)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                ha, hb = heights[a], heights[b]
                if not ha or not hb:
                    continue
                ba, bb = cur[a][1], cur[b][1]
                d = np.hypot((ba[0] + ba[2] - bb[0] - bb[2]) / 2, (ba[1] + ba[3] - bb[1] - bb[3]) / 2)
                if d <= CLOSE_HEIGHTS * max(ha, hb):
                    for x, y, hx in ((a, b, ha), (b, a, hb)):
                        q = self.encounters.setdefault(x, deque())
                        q.append((t, y, hx))
                        while q and t - q[0][0] > WINDOW_AFTER_S + 1.0:
                            q.popleft()

        for tid in live:
            h = self.hist[tid]
            hs = heights[tid]
            # ---- KNOCKDOWN
            downs = [x for x in h if t - x[0] <= DOWN_HOLD_S]
            if hs and downs and all(x[2] == "down" for x in downs) and t - downs[0][0] >= DOWN_HOLD_S * 0.8:
                first_down = next((x for x in reversed(h) if x[2] != "down"), None)
                ups = [x for x in h if x[2] == "up" and x[0] < downs[0][0]]
                if ups:
                    last_up = ups[-1]
                    up_span = [x for x in ups if last_up[0] - x[0] <= UPRIGHT_HOLD_S]
                    fell_fast = downs[0][0] - last_up[0] <= FALL_WITHIN_S
                    hip_drop = downs[-1][3] - last_up[3] >= HIP_DROP_FRAC * hs
                    held_up = last_up[0] - up_span[0][0] >= UPRIGHT_HOLD_S * 0.8
                    near = any(o != tid and np.hypot((cur[o][1][0] + cur[o][1][2] - last_up[1][0] - last_up[1][2]) / 2,
                                                     (cur[o][1][1] + cur[o][1][3] - last_up[1][1] - last_up[1][3]) / 2)
                               <= NEARBY_HEIGHTS * hs for o in live)
                    if fell_fast and hip_drop and held_up and near and first_down is not None and self._fire("KNOCKDOWN", tid, t):
                        events.append(("KNOCKDOWN", tid, {"fell_in_s": round(downs[0][0] - last_up[0], 2)}))
            # ---- SNATCH_RUN
            enc = self.encounters.get(tid)
            if hs and enc:
                run_now = self._speed(h, t - RUN_HOLD_S, t, hs)
                if run_now >= RUN_SPEED:
                    for te, other, _ in enc:
                        if not (0 < t - te <= WINDOW_AFTER_S) or other not in self.hist:
                            continue
                        before = self._speed(h, te - 0.8, te, hs)
                        if before >= CALM_BEFORE:
                            continue           # was already moving fast: a passing rider, a jogger
                        oh = self.hist[other]
                        other_now = self._speed(oh, t - RUN_HOLD_S, t, hs)
                        if other_now >= OTHER_SLOW_FRAC * run_now:
                            continue           # they left together
                        a_then = next((x for x in h if x[0] >= te), None)
                        b_then = next((x for x in oh if x[0] >= te), None)
                        if a_then is None or b_then is None or other not in cur:
                            continue
                        gap_then = np.hypot(*(((a_then[1][:2] + a_then[1][2:]) - (b_then[1][:2] + b_then[1][2:])) / 2))
                        gap_now = np.hypot(*(((cur[tid][1][:2] + cur[tid][1][2:]) - (cur[other][1][:2] + cur[other][1][2:])) / 2))
                        if gap_now - gap_then >= APART_HEIGHTS * hs and self._fire("SNATCH_RUN", tid, t):
                            events.append(("SNATCH_RUN", tid, {"from": other, "speed_h_per_s": round(run_now, 2)}))
                            break
        return events

    def _stand_height(self, tid):
        """Typical standing height (px) of this person over the history,
        falling back to the current box height."""
        hs = [x[4] for x in self.hist[tid] if x[2] == "up"]
        if hs:
            return float(np.median(hs))
        return float(self.hist[tid][-1][4]) if self.hist[tid] else None

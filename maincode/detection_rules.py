"""
EcoVision -- detection decision rules, shared by the live AI core and offline
evaluation.

Everything here turns model outputs into alert decisions: per-class weapon
confidence gates, weapon tracking and the static-scene-object filter, weapon
to wrist assignment, crowd-overlap checks, and the per-person ASSAULT / ARMED
state machine. It moved out of main.py (2026-09-30) so that evaluation scripts
import the exact rules that ship instead of keeping their own copies -- the
training folder had two drifted copies already: weapon_fp_production_
thresholds.py still gated at 0.52 / 0.45 months after production moved to
0.30 / 0.23, and static_detection_filter.py re-implemented the filter below.

No camera, network, drawing or model loading lives here. Values that come
from config.json are set by configure(sys_config); main.py calls it once at
startup, evaluation calls it with whatever config it is measuring.

The code and comments below are moved verbatim from main.py except where
marked: configure(), the WeaponTracker class (was three module globals), and
TrackState.should_alert's default.
"""
from collections import deque

import numpy as np

# detection.confidence_threshold -- set by configure(). Fallback gate for any
# weapon class CONF_BY_CLASS does not name.
WEAPON_CONF = 0.38

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
SCENE_COOLDOWN_ASSAULT = 120   # alert.cooldown_frames -- set by configure()

WEAPON_IOU_MATCH  = 0.25
WEAPON_MAX_UNSEEN = 30

# ── static scene-object rejection (see _is_static_scene_object) ───────────
# The deployed weapon model reports fixed scene features as weapons: a utility
# pole at the tire shop scores Gun 0.93 on frame after frame. Measured removal
# on real footage: streetview1 97.4%, tireshop 81.0%, barbershop 78.1%.
# Switchable, because every mode in this system stays revertible by config.
_STATIC_CFG: dict = {}          # detection.static_weapon_filter -- set by configure()
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
MAX_UNSEEN_FRAMES      = 180   # detection.max_unseen_frames -- set by configure()

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


def configure(sys_config: dict) -> None:
    """Applies the config-driven values. Call before importing names from
    this module by value (from detection_rules import X copies X)."""
    global WEAPON_CONF, SCENE_COOLDOWN_ASSAULT, _STATIC_CFG, STATIC_WEAPON_FILTER, STATIC_WEAPON_WINDOW
    global STATIC_WEAPON_MIN_OBS, STATIC_WEAPON_MOVE_PX, MAX_UNSEEN_FRAMES, WEAPON_YOLO_CONF_FLOOR
    det = sys_config.get("detection", {})
    WEAPON_CONF = det.get("confidence_threshold", 0.38)
    WEAPON_YOLO_CONF_FLOOR = min(min(CONF_BY_CLASS.values()), WEAPON_CONF)
    SCENE_COOLDOWN_ASSAULT = sys_config.get("alert", {}).get("cooldown_frames", 120)
    _STATIC_CFG = det.get("static_weapon_filter", {})
    STATIC_WEAPON_FILTER = _STATIC_CFG.get("enabled", True)
    STATIC_WEAPON_WINDOW = _STATIC_CFG.get("window_observations", 45)
    STATIC_WEAPON_MIN_OBS = _STATIC_CFG.get("min_observations", 15)
    STATIC_WEAPON_MOVE_PX = _STATIC_CFG.get("move_threshold_px", 28)
    MAX_UNSEEN_FRAMES = det.get("max_unseen_frames", 180)

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


# ──────────────────────────────────────────────────────────────────────────────
# 7. PER-INSTANCE WEAPON TRACKER
# ──────────────────────────────────────────────────────────────────────────────
class WeaponTracker:
    """Per-instance weapon tracks: IoU-matched across detection passes, with
    per-class confidence gates, grip-assignment memory, and the static-object
    rejection below. One per camera stream -- this used to be three module
    globals in main.py, which made a second camera (or an offline replay next
    to a live one) share one set of tracks."""
    def __init__(self):
        self.store: dict[int, dict] = {}
        self.counter: int = 0
        self.grip_sticky: dict[int, int] = {}   # weapon-track wid -> person tid it is currently gripped by

    def update(self, raw_weapons: list) -> list:
        for t in self.store.values():
            t["unseen"] += 1

        live_gun_classes = {
            t["name"] for t in self.store.values()
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
            for wid, t in self.store.items():
                if t["name"] != cls_name:
                    continue
                iou = VBoxTracker._iou(w_box, t["box"])
                if iou > best_iou:
                    best_iou = iou; best_wid = wid

            if best_wid is not None:
                t = self.store[best_wid]
                t.update({"box": w_box, "conf": raw_conf, "center": w["center"], "unseen": 0})
                t["pos_hist"].append(w["center"])
            else:
                self.store[self.counter] = {
                    "name": cls_name, "box": w_box, "conf": raw_conf, "center": w["center"], "unseen": 0,
                    # Position history drives the static-object rejection below.
                    "pos_hist": deque([w["center"]], maxlen=STATIC_WEAPON_WINDOW),
                }
                self.counter += 1

        stale = [wid for wid, t in self.store.items() if t["unseen"] > WEAPON_MAX_UNSEEN]
        for wid in stale:
            del self.store[wid]
            self.grip_sticky.pop(wid, None)   # clear stale grip-assignment memory too

        return [{**t, "wid": wid} for wid, t in self.store.items()
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

    def should_alert(self, frame_no: int, scene_last: int, scene_cooldown: int = None) -> bool:
        # None -> the configured value, read at call time (a default bound at
        # def time would freeze whatever configure() had not yet set).
        if scene_cooldown is None:
            scene_cooldown = SCENE_COOLDOWN_ASSAULT
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


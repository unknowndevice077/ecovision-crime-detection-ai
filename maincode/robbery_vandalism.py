"""
EcoVision -- Robbery + Vandalism Detection Logic
==================================================================
Implements the two remaining behavior signals locked in during planning:

  ROBBERY -- composite rule, ZERO new training required.
      Fires when: (Armed OR Violence state is True)
                  AND two Person bboxes are in close proximity
                  AND this has held true for a sustained duration.
      Reuses your existing Armed/Violence outputs -- this is pure logic,
      not a model.

  VANDALISM -- new lightweight FSM, same style as _score_strike, but the
      "target" is a static Sign/Wall bbox instead of another person's
      torso, and the motion signature is "repeated short-range wrist
      motion with NO victim approach" (sweeping/spraying) rather than a
      single fast strike. This distinguishes it from punching.

Both pieces are designed to slot directly into sentinel_v16.py's main
loop, next to the existing weapon_assigns / is_melee / TrackState logic.
This file is written as a standalone module you can import, or copy the
relevant functions/classes directly into sentinel_v16.py.
"""

import numpy as np
from collections import deque


# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION -- new thresholds, namespaced separately from existing ones
# so they don't collide with sentinel_v16.py's MIN_PUNCH_VEL etc.
# ──────────────────────────────────────────────────────────────────────────────

# -- Robbery (composite rule)
ROBBERY_PROXIMITY_DIST   = 180     # px -- max center-to-center distance between two Person boxes
ROBBERY_MIN_DURATION     = 90      # frames (~3 sec @ 30fps) -- sustained proximity+threat required
ROBBERY_RELEASE_FRAMES   = 45      # frames of no-evidence before dropping ROBBERY state

# -- Vandalism (new FSM)
#
# MEASURED 2026-08-19, the first real measurement these six constants have
# ever had (see EcoVisionImagesTraining/sweep_vandalism_band.py). Every
# number below was guessed before condition 1 (a real static_targets box)
# was reachable at all -- weapon_signs.pt's "sign" class fired zero times in
# 4,800 frames, so nothing downstream of it was ever exercised on real data.
# Once vandalism_marks.pt made condition 1 reachable, the OLD velocity band
# [20, 90] turned out to overlap punch territory (MIN_PUNCH_VEL=60 in
# main.py) instead of sitting below it -- backwards for a rule meant to mean
# "sweeping, not striking" -- and caught only 3.6% of real near-target wrist
# samples (33/903). True distribution at native frame rate: median 1.1,
# p75 3.7, max 52 px/frame.
#
# A 7-band x 8-sustain-param grid search on 79 clips from
# ucf_clips/vandalism_outdoor + 24 same-camera negatives picked the values
# below, and produced "11.4% recall (9/79), 0.0% FPR (0/24)".
#
# THAT NUMBER IS RETRACTED -- 2026-08-20. A contact sheet of all 10 source
# videos (_scratch/vandalism_contact_sheet.jpg) shows UCF-Crime's Vandalism
# class is PROPERTY DESTRUCTION, not graffiti: Vandalism050 is someone
# keying a car, 026 swings a board, 029 attacks a shop shutter, 013/033
# knock over a planter display. Essentially none of it is spray-painting.
#
# So the measurement was a three-way mismatch: vandalism_marks.pt detects
# GRAFFITI MARKS, this rule models SPRAY-PAINTING WRIST MOTION, and the
# clips show SMASHING AND KEYING. 11.4% measured how often a graffiti
# detector coincidentally fired near a wrist in videos of people breaking
# things -- it is not a measurement of this rule's recall, and neither is
# the "35.4% ceiling" or the "20.3% at confirm=2" derived from the same set.
#
# The constants below are therefore still UNVALIDATED against footage of
# the act they model. They are kept only because they are better-reasoned
# than the originals (the old [20,90] band genuinely did sit in punch
# territory, which is a real defect independent of the bad test set).
#
# There is no graffiti-act dataset in this project. Getting one is
# docs/vandalism_data_collection.md's job. config.json's _why_disabled was
# NOT updated with the 11.4% figure -- it still carries the older, honest
# X3D-model numbers, and detection.vandalism.enabled stays false.
VANDAL_TARGET_PROXIMITY  = 120     # px -- wrist must be within this distance of a detected mark
VANDAL_MIN_WRIST_VEL     = 0.3     # px/frame -- was 20 (in punch territory); real sweeping motion is far slower
VANDAL_MAX_WRIST_VEL     = 8.0     # px/frame -- was 90; well under MIN_PUNCH_VEL=60, as "not a punch" requires
VANDAL_MIN_SWEEP_FRAMES  = 2       # consecutive frames of qualifying motion needed -- was 10
VANDAL_SWEEP_WINDOW      = 6       # rolling window size for sweep detection -- was 20
VANDAL_NO_VICTIM_DIST    = 200     # px -- no OTHER person may be closer than this to count as vandalism,
                                    # not assault (this is what separates spraying from punching)
VANDAL_CONFIRM_FRAMES    = 20      # consecutive qualifying frames to confirm VANDALISM state
VANDAL_RELEASE_FRAMES    = 60      # frames of no evidence before dropping VANDALISM state
VANDAL_SWEEP_FRACTION    = 0.30    # share of the last VANDAL_MIN_SWEEP_FRAMES that must be in-band --
                                    # was hardcoded 0.7 inline in score_vandalism, never swept until now


# ──────────────────────────────────────────────────────────────────────────────
# ROBBERY -- composite rule, no training, reuses existing Armed/Violence flags
# ──────────────────────────────────────────────────────────────────────────────

def _person_distance(box_a, box_b) -> float:
    """Center-to-center distance between two person bounding boxes."""
    ca = np.array([(box_a[0] + box_a[2]) / 2, (box_a[1] + box_a[3]) / 2])
    cb = np.array([(box_b[0] + box_b[2]) / 2, (box_b[1] + box_b[3]) / 2])
    return float(np.linalg.norm(ca - cb))


class RobberyTracker:
    """
    Per-PAIR hysteresis tracker for Robbery.

    Robbery is judged at the PAIR level (attacker + victim), not per
    individual track, since it's fundamentally about a relationship
    between two people, not a property of one person alone.

    Uses the SAME hysteresis confirm/release pattern as sentinel_v16.py's
    TrackState, just keyed by (tid_a, tid_b) pairs instead of single tids.
    """

    def __init__(self):
        self._pair_evidence: dict[tuple, int] = {}   # pair_key -> consecutive evidence frames
        self._pair_release: dict[tuple, int] = {}    # pair_key -> consecutive no-evidence frames
        self._pair_state: dict[tuple, str] = {}       # pair_key -> "ROBBERY" or "NONE"

    @staticmethod
    def _pair_key(tid_a, tid_b):
        return tuple(sorted([tid_a, tid_b]))

    def update(self, ids, boxes, armed_states: dict, violence_states: dict) -> dict:
        """
        ids: list of track ids this frame
        boxes: list of person bboxes, same order as ids
        armed_states: dict tid -> bool (True if that track is currently ARMED)
        violence_states: dict tid -> bool (True if that track is currently in ASSAULT/Violence)

        Returns: dict pair_key -> "ROBBERY" or "NONE" for every pair currently tracked.
        """
        active_pairs = set()

        # Build tid -> box once per frame instead of calling ids.index(tid)
        # inside the O(n^2) pair loop below (that was an O(n^3) hot path
        # for scenes with many people in frame).
        box_by_tid = {tid: box for tid, box in zip(ids, boxes)}

        for i, tid_a in enumerate(ids):
            for tid_b in ids[i + 1:]:
                box_a = box_by_tid[tid_a]
                box_b = box_by_tid[tid_b]
                dist = _person_distance(box_a, box_b)

                pair_key = self._pair_key(tid_a, tid_b)
                active_pairs.add(pair_key)

                threat_present = (
                    armed_states.get(tid_a, False) or armed_states.get(tid_b, False)
                    or violence_states.get(tid_a, False) or violence_states.get(tid_b, False)
                )
                proximity_ok = dist < ROBBERY_PROXIMITY_DIST
                evidence_this_frame = threat_present and proximity_ok

                if evidence_this_frame:
                    self._pair_evidence[pair_key] = self._pair_evidence.get(pair_key, 0) + 1
                    self._pair_release[pair_key] = 0
                else:
                    self._pair_release[pair_key] = self._pair_release.get(pair_key, 0) + 1
                    if self._pair_release[pair_key] >= ROBBERY_RELEASE_FRAMES:
                        self._pair_evidence[pair_key] = 0

                if self._pair_evidence.get(pair_key, 0) >= ROBBERY_MIN_DURATION:
                    self._pair_state[pair_key] = "ROBBERY"
                elif self._pair_evidence.get(pair_key, 0) == 0:
                    self._pair_state[pair_key] = "NONE"
                # else: stays in whatever state it was -- mid-confirmation, not yet decided

        # Clean up pairs that are no longer co-present in frame
        stale_pairs = [k for k in self._pair_state if k not in active_pairs]
        for k in stale_pairs:
            self._pair_evidence.pop(k, None)
            self._pair_release.pop(k, None)
            self._pair_state.pop(k, None)

        return dict(self._pair_state)


# ──────────────────────────────────────────────────────────────────────────────
# VANDALISM -- new FSM, mirrors _score_strike's structure but targets a
# static object (Sign/Wall) instead of a victim's torso, and looks for
# sustained sweeping motion rather than a single velocity spike.
# ──────────────────────────────────────────────────────────────────────────────

def _wrist_to_box_distance(wrist_xy, box) -> float:
    """Distance from a wrist point to the nearest edge of a bbox (0 if inside)."""
    x, y = wrist_xy
    x1, y1, x2, y2 = box
    dx = max(x1 - x, 0, x - x2)
    dy = max(y1 - y, 0, y - y2)
    return float(np.sqrt(dx**2 + dy**2))


def score_vandalism(tid, joints, prev_joints_dict, sweep_history_dict,
                     static_targets, all_person_boxes, my_box):
    """
    Returns (is_vandalizing: bool, target_box_or_None).

    IMPORTANT (caller contract): `prev_joints_dict` must hold each track's
    wrist positions from the PREVIOUS frame, not the current one. In
    main.py this means snapshotting `prev_joints` BEFORE the per-track
    loop that writes this frame's wrist positions into it, and passing
    that snapshot in here -- otherwise wrists_now == wrists_prev every
    call, velocity is always ~0, and Vandalism can never trigger.

    static_targets: list of Sign/Wall bboxes detected this frame (from YOLO)
    all_person_boxes: list of ALL other person bboxes this frame (for the
                       no-victim-nearby check)
    my_box: this person's own bbox (to exclude self from the "other person"
            distance check, and to compute distance to nearby people)

    Logic:
      1. Wrist must be near a static target (Sign/Wall) -- VANDAL_TARGET_PROXIMITY
      2. Wrist velocity must be in the "sweep" band -- not idle, not a punch
      3. This sweeping motion must be SUSTAINED across several frames
         (rules out a single fast incidental movement)
      4. NO other person may be within VANDAL_NO_VICTIM_DIST -- this is the
         condition that distinguishes vandalism from assault. A punch has
         a victim; vandalism does not.
    """
    if tid not in prev_joints_dict:
        return False, None

    wrists_now = joints[[9, 10]]
    wrists_prev = prev_joints_dict[tid]

    valid_mask = np.any(wrists_now > 1, axis=1) & np.any(wrists_prev > 1, axis=1)
    if not np.any(valid_mask):
        return False, None

    # ── Condition 4 first (cheapest check, exits early) ──────────────────────
    my_center = np.array([(my_box[0] + my_box[2]) / 2, (my_box[1] + my_box[3]) / 2])
    for other_box in all_person_boxes:
        if np.array_equal(other_box, my_box):
            continue
        other_center = np.array([(other_box[0] + other_box[2]) / 2, (other_box[1] + other_box[3]) / 2])
        if np.linalg.norm(my_center - other_center) < VANDAL_NO_VICTIM_DIST:
            return False, None   # someone's nearby -- this is assault's territory, not vandalism's

    # ── Condition 1 -- find a static target near either wrist ────────────────
    nearest_target = None
    nearest_target_dist = float("inf")
    nearest_wrist_idx = None

    for target_box in static_targets:
        for w_idx in range(2):
            wx, wy = wrists_now[w_idx]
            if wx < 1 and wy < 1:
                continue
            d = _wrist_to_box_distance((wx, wy), target_box)
            if d < VANDAL_TARGET_PROXIMITY and d < nearest_target_dist:
                nearest_target_dist = d
                nearest_target = target_box
                nearest_wrist_idx = w_idx

    if nearest_target is None:
        return False, None   # no wrist is near any Sign/Wall -- nothing to evaluate

    # ── Condition 2 -- velocity in the sweep band ─────────────────────────────
    move_vec = wrists_now[nearest_wrist_idx] - wrists_prev[nearest_wrist_idx]
    v_inst = float(np.linalg.norm(move_vec))

    sweep_buf = sweep_history_dict.setdefault(tid, deque(maxlen=VANDAL_SWEEP_WINDOW))
    in_sweep_band = VANDAL_MIN_WRIST_VEL <= v_inst <= VANDAL_MAX_WRIST_VEL
    sweep_buf.append(int(in_sweep_band))

    # ── Condition 3 -- sustained, not incidental ──────────────────────────────
    if len(sweep_buf) < VANDAL_MIN_SWEEP_FRAMES:
        return False, None

    recent = list(sweep_buf)[-VANDAL_MIN_SWEEP_FRAMES:]
    if sum(recent) >= int(VANDAL_MIN_SWEEP_FRAMES * VANDAL_SWEEP_FRACTION):
        return True, nearest_target

    return False, None


class VandalismTrackState:
    """
    Per-person hysteresis FSM for Vandalism, same confirm/release pattern
    as sentinel_v16.py's TrackState class, but a simpler single-state
    version since Vandalism doesn't have an Armed-style intermediate tier.
    """

    __slots__ = ("confirm_count", "release_count", "state")

    def __init__(self):
        self.confirm_count = 0
        self.release_count = 0
        self.state = "NONE"

    def update(self, is_vandalizing: bool) -> str:
        if is_vandalizing:
            self.confirm_count = min(self.confirm_count + 1, VANDAL_CONFIRM_FRAMES)
            self.release_count = 0
        else:
            self.release_count += 1
            if self.release_count >= VANDAL_RELEASE_FRAMES:
                self.confirm_count = 0

        if self.confirm_count >= VANDAL_CONFIRM_FRAMES:
            self.state = "VANDALISM"
        elif self.confirm_count == 0:
            self.state = "NONE"
        # else: mid-confirmation, holds previous state

        return self.state


# ──────────────────────────────────────────────────────────────────────────────
# INTEGRATION EXAMPLE -- how this slots into sentinel_v16.py's main loop
# ──────────────────────────────────────────────────────────────────────────────
"""
Add near the other per-track stores (alongside track_states, prev_joints, etc.):

    robbery_tracker = RobberyTracker()
    vandal_states: dict[int, VandalismTrackState] = {}
    vandal_sweep_history: dict[int, deque] = {}

Inside the main loop, BEFORE the per-track loop that overwrites
prev_joints[tid] with this frame's wrist positions, snapshot it:

    prev_joints_snapshot = dict(prev_joints)

... run the existing Armed/Violence per-person loop as normal (it's the
one that does `prev_joints[tid] = joints[[9, 10]].copy()`) ...

Then, AFTER that loop (so you have armed_states and violence_states
populated for every tid):

    armed_states = {tid: (track_states[tid].state == "ARMED") for tid in ids if tid in track_states}
    violence_states = {tid: (track_states[tid].state == "ASSAULT") for tid in ids if tid in track_states}

    # -- ROBBERY (composite, runs once per frame for all pairs) --
    robbery_pairs = robbery_tracker.update(ids, boxes, armed_states, violence_states)
    for pair_key, state in robbery_pairs.items():
        if state == "ROBBERY":
            # push to human review queue here, same pattern as should_alert()
            pass

    # -- VANDALISM (per-person, needs Sign/Wall boxes from your YOLO weapon_assigns-style detection) --
    sign_boxes = [w["box"] for w in raw_weapons if w["name"] == "sign"]  # adjust to your actual class name

    for tid, joints, p_box in zip(ids, kpts, boxes):
        if tid not in vandal_states:
            vandal_states[tid] = VandalismTrackState()

        is_vandal, target = score_vandalism(
            tid, joints, prev_joints_snapshot, vandal_sweep_history,
            static_targets=sign_boxes, all_person_boxes=boxes, my_box=p_box,
        )
        v_state = vandal_states[tid].update(is_vandal)
        if v_state == "VANDALISM":
            # push to human review queue here
            pass
"""
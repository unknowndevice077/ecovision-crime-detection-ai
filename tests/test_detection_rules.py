"""Behaviour of maincode/detection_rules.py -- the decision logic shared by the
live AI core and offline evaluation. Each test pins one measured design
decision, so changing it has to be deliberate."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maincode"))
import detection_rules as R  # noqa: E402

BASE_CONFIG = {"detection": {"confidence_threshold": 0.38, "static_weapon_filter": {"enabled": True}},
               "alert": {"cooldown_frames": 120}}


def weapon(name, conf, x, y, size=40):
    h = size / 2
    return {"name": name, "conf": conf, "box": [x - h, y - h, x + h, y + h], "center": [x, y]}


class WeaponGates(unittest.TestCase):
    def setUp(self):
        R.configure(BASE_CONFIG)

    def test_phone_is_never_a_weapon_class(self):
        # The model detects phones precisely so they can be dropped.
        self.assertNotIn("phone", R.WEAPON_CLASSES)

    def test_gun_below_gate_is_dropped(self):
        t = R.WeaponTracker()
        self.assertEqual(t.update([weapon("gun", R.CONF_BY_CLASS["gun"] - 0.01, 100, 100)]), [])

    def test_gun_at_gate_is_tracked(self):
        t = R.WeaponTracker()
        self.assertEqual(len(t.update([weapon("gun", R.CONF_BY_CLASS["gun"], 100, 100)])), 1)

    def test_sustained_gun_uses_lower_gate(self):
        # Once a gun track is live, a lower-confidence sighting keeps it alive.
        t = R.WeaponTracker()
        t.update([weapon("gun", 0.9, 100, 100)])
        low = max(R.WEAPON_CONF_GUN_SUSTAINED, 0.0)
        self.assertEqual(len(t.update([weapon("gun", low, 102, 101)])), 1)

    def test_yolo_floor_never_cuts_below_a_class_gate(self):
        # The 2026-08-19 bug: YOLO's own conf= discarded boxes the per-class
        # gates would have accepted.
        self.assertLessEqual(R.WEAPON_YOLO_CONF_FLOOR, min(R.CONF_BY_CLASS.values()))

    def test_configure_changes_fallback_threshold(self):
        R.configure({**BASE_CONFIG, "detection": {**BASE_CONFIG["detection"], "confidence_threshold": 0.1}})
        self.assertEqual(R.WEAPON_CONF, 0.1)
        self.assertEqual(R.WEAPON_YOLO_CONF_FLOOR, 0.1)

    def test_trackers_are_independent(self):
        # Two cameras must not share weapon tracks (was module-global state).
        a, b = R.WeaponTracker(), R.WeaponTracker()
        a.update([weapon("knife", 0.9, 50, 50)])
        self.assertEqual(b.store, {})


class StaticObjectFilter(unittest.TestCase):
    def setUp(self):
        R.configure(BASE_CONFIG)

    def test_motionless_weapon_suppressed_after_min_observations(self):
        t = R.WeaponTracker()
        out = []
        for _ in range(R.STATIC_WEAPON_MIN_OBS + 2):
            out = t.update([weapon("knife", 0.9, 300, 300)])
        self.assertEqual(out, [], "a utility pole scoring 'gun' every frame must be filtered")

    def test_not_suppressed_before_min_observations(self):
        t = R.WeaponTracker()
        self.assertEqual(len(t.update([weapon("knife", 0.9, 300, 300)])), 1)

    def test_moving_weapon_kept(self):
        t = R.WeaponTracker()
        out = []
        for i in range(R.STATIC_WEAPON_MIN_OBS + 5):
            out = t.update([weapon("knife", 0.9, 300 + i * 8, 300)])
        self.assertEqual(len(out), 1)

    def test_filter_can_be_disabled_by_config(self):
        R.configure({**BASE_CONFIG, "detection": {"static_weapon_filter": {"enabled": False}}})
        t = R.WeaponTracker()
        out = []
        for _ in range(R.STATIC_WEAPON_MIN_OBS + 2):
            out = t.update([weapon("knife", 0.9, 300, 300)])
        self.assertEqual(len(out), 1)


class GripAssignment(unittest.TestCase):
    def setUp(self):
        R.configure(BASE_CONFIG)

    def _person(self, wrist_xy):
        kpts = np.zeros((17, 2))
        kpts[9] = wrist_xy
        return kpts

    def test_weapon_goes_to_nearest_wrist_within_grip(self):
        w = [{**weapon("knife", 0.9, 500, 300), "wid": 1}]
        boxes = np.array([[450, 200, 550, 400], [900, 200, 1000, 400]], float)
        kpts = np.array([self._person([505, 305]), self._person([950, 300])])
        got = R._assign_weapons(w, [1, 2], kpts, boxes, {})
        self.assertEqual([x["wid"] for x in got[1]], [1])
        self.assertEqual(got[2], [])

    def test_weapon_out_of_reach_is_unassigned(self):
        w = [{**weapon("knife", 0.9, 100, 100), "wid": 1}]
        boxes = np.array([[900, 200, 1000, 400]], float)
        got = R._assign_weapons(w, [1], np.array([self._person([950, 300])]), boxes, {})
        self.assertEqual(got[1], [])

    def test_sticky_holder_is_not_stolen_by_jitter(self):
        sticky = {7: 1}
        w = [{**weapon("knife", 0.9, 500, 300), "wid": 7}]
        boxes = np.array([[450, 200, 550, 400], [460, 200, 560, 400]], float)
        # Person 2 is only marginally closer than holder 1.
        kpts = np.array([self._person([520, 300]), self._person([516, 300])])
        got = R._assign_weapons(w, [1, 2], kpts, boxes, sticky)
        self.assertEqual([x["wid"] for x in got[1]], [7])


class TrackStateMachine(unittest.TestCase):
    def setUp(self):
        R.configure(BASE_CONFIG)

    def test_assault_needs_consecutive_confirmation(self):
        s = R.TrackState()
        for f in range(R.ASSAULT_CONFIRM_FRAMES - 1):
            self.assertEqual(s.update(True, False, f), "NEUTRAL")
        self.assertEqual(s.update(True, False, R.ASSAULT_CONFIRM_FRAMES), "ASSAULT")

    def test_armed_needs_its_own_confirmation(self):
        s = R.TrackState()
        states = [s.update(False, True, f) for f in range(R.ARMED_CONFIRM_FRAMES)]
        self.assertEqual(states[-1], "ARMED")
        self.assertTrue(all(x == "NEUTRAL" for x in states[:-1]))

    def test_one_episode_one_alert(self):
        # The 239-incidents-from-one-episode bug: a state that never releases
        # must not re-alert every cooldown period.
        s = R.TrackState()
        start = R.ALERT_COOLDOWN_FRAMES + 10
        alerts = 0
        for f in range(start, start + 2000):
            s.update(True, False, f)
            if s.should_alert(f, scene_last=-10_000):
                alerts += 1
                s.mark_alerted(f, "inc")
        self.assertEqual(alerts, 1)

    def test_new_episode_after_release_alerts_again(self):
        s = R.TrackState()
        f = R.ALERT_COOLDOWN_FRAMES + 10
        fired = []
        for phase in (True, False, True):
            n = R.ASSAULT_RELEASE_FRAMES + R.ALERT_COOLDOWN_FRAMES + 5 if not phase else 50
            for _ in range(n):
                s.update(phase, False, f)
                if s.should_alert(f, scene_last=-10_000):
                    fired.append(f)
                    s.mark_alerted(f, f"inc{f}")
                f += 1
        self.assertEqual(len(fired), 2)

    def test_scene_cooldown_blocks_alert(self):
        s = R.TrackState()
        f0 = R.ALERT_COOLDOWN_FRAMES + 10
        for f in range(f0, f0 + 10):
            s.update(True, False, f)
        self.assertFalse(s.should_alert(f0 + 10, scene_last=f0 + 5))
        self.assertTrue(s.should_alert(f0 + 10, scene_last=f0 + 10 - R.SCENE_COOLDOWN_ASSAULT - 1))

    def test_scene_cooldown_default_follows_configure(self):
        R.configure({**BASE_CONFIG, "alert": {"cooldown_frames": 5}})
        s = R.TrackState()
        f0 = R.ALERT_COOLDOWN_FRAMES + 10
        for f in range(f0, f0 + 10):
            s.update(True, False, f)
        self.assertTrue(s.should_alert(f0 + 10, scene_last=f0 + 3))


class Geometry(unittest.TestCase):
    def test_overlap_count_ignores_self(self):
        boxes = np.array([[0, 0, 100, 100], [10, 10, 110, 110], [500, 500, 600, 600]], float)
        self.assertEqual(R._bbox_overlap_count(boxes[0], boxes), 1)

    def test_vbox_overlap_ratio(self):
        self.assertAlmostEqual(R._vbox_overlap_ratio([0, 0, 100, 100], [50, 0, 150, 100]), 0.5)
        self.assertEqual(R._vbox_overlap_ratio([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)


if __name__ == "__main__":
    unittest.main()

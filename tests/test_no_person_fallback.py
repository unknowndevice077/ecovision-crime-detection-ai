"""No-person fallback in SceneViolenceDetector (person-crop confirmation): a
very high whole-frame score counts on its own only after the pose model has
found nobody for the whole look-back window. The models are stubbed -- this
pins the decision rule; the measured effect is in config.json's
detection.violence._scene_no_person_fallback, and the full replay check is
experiments/crop_recheck/verify_fallback_shipped.py in the training repo."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maincode"))
import x3d_violence_detector as X  # noqa: E402


class _Stub(X.SceneViolenceDetector):
    def __init__(self, **kw):
        orig = X.X3DViolenceDetector.__init__

        def fake(self, model_path=None, device=None, log_path=None):
            self.device = "cpu"
            self.clip_frames, self.frame_size = 13, 32
            self._real_inference_count = {}
        X.X3DViolenceDetector.__init__ = fake
        try:
            super().__init__(person_confirm=True, threshold=0.45, consecutive=2, log_path=None, **kw)
        finally:
            X.X3DViolenceDetector.__init__ = orig
        self._log_row = lambda *a, **k: None
        self.scores = (0.0, 0.0)

    def _run_inference_batch(self, clips, tid=None):
        return [(None, self.scores[0])] + [(None, self.scores[1])] * (len(clips) - 1)

    def check(self, scene, crop, people):
        self.scores = (scene, crop)
        frame = np.zeros((72, 128, 3), np.uint8)
        self._full_buffer.append(frame)
        self._scene_buffer.append(frame)
        self._scene_last_check = -10 ** 9
        boxes = [[10 + 40 * i, 10, 30 + 40 * i, 60] for i in range(people)]
        return self.update(frame, 0, person_boxes=boxes)[1]


class NoPersonFallback(unittest.TestCase):
    def test_off_by_default(self):
        d = _Stub()
        for _ in range(8):
            conf = d.check(0.95, 0.0, 0)
        self.assertEqual(conf, 0.0)   # min(scene, crop) with no crop

    def test_needs_the_whole_window_without_people(self):
        d = _Stub(no_person_min_scene=0.8, no_person_checks=6)
        confs = [d.check(0.95, 0.0, 0) for _ in range(6)]
        self.assertEqual(confs[:5], [0.0] * 5)
        self.assertEqual(confs[5], 0.95)

    def test_anyone_found_in_the_window_cancels_it(self):
        d = _Stub(no_person_min_scene=0.8, no_person_checks=6)
        d.check(0.95, 0.1, 2)
        confs = [d.check(0.95, 0.0, 0) for _ in range(5)]
        self.assertTrue(all(c < 0.2 for c in confs))

    def test_below_the_bar_stays_vetoed(self):
        d = _Stub(no_person_min_scene=0.8, no_person_checks=6)
        for _ in range(8):
            conf = d.check(0.7, 0.0, 0)
        self.assertEqual(conf, 0.0)



class WholeFrameCandidate(unittest.TestCase):
    """set_scene_candidate: candidates from the whole-frame score alone,
    which person-crop confirmation must NOT veto (robbery_from_fight)."""

    def test_fires_where_confirmation_vetoes(self):
        d = _Stub(no_person_min_scene=0.8, no_person_checks=6)
        d.set_candidate(0.25, 1)
        d.set_scene_candidate(0.25, 1)
        d.check(0.5, 0.05, 2)                      # people found, crops calm: confirmed 0.05
        self.assertTrue(d.scene_candidate.candidate_started)
        self.assertFalse(d.candidate_confirmed)

    def test_one_edge_per_episode_and_reset(self):
        d = _Stub()
        d.set_scene_candidate(0.25, 1)
        started = [d.scene_candidate.candidate_started for _ in range(1) if d.check(0.5, 0.0, 0) is not None]
        started += [d.check(0.5, 0.0, 0) is not None and d.scene_candidate.candidate_started for _ in range(3)]
        self.assertEqual(started, [True, False, False, False])
        d.reset_scene()
        self.assertFalse(d.scene_candidate.candidate_confirmed)

    def test_off_by_default(self):
        d = _Stub()
        d.check(0.9, 0.9, 2)
        self.assertIsNone(d.scene_candidate)


if __name__ == "__main__":
    unittest.main()

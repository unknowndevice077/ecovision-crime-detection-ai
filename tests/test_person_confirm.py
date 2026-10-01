"""Person-crop confirmation in the scene violence detector: who gets grouped,
and what the crops look like (x3d_violence_detector._group_person_boxes /
_confirm_crop_box). The end-to-end numbers were checked against the
experiment separately (experiments/crop_recheck/verify_shipped.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maincode"))
import x3d_violence_detector as X  # noqa: E402


class Grouping(unittest.TestCase):
    def test_two_people_close_together_are_one_group(self):
        groups = X._group_person_boxes([[100, 100, 150, 250], [160, 110, 210, 260]])
        self.assertEqual(len(groups), 1)

    def test_people_far_apart_stay_separate(self):
        groups = X._group_person_boxes([[0, 0, 50, 150], [1000, 0, 1050, 150]])
        self.assertEqual(sorted(len(g) for g in groups), [1, 1])

    def test_nobody_means_no_groups(self):
        self.assertEqual(X._group_person_boxes([]), [])


class CropBox(unittest.TestCase):
    W, H = 1280, 720

    def test_keeps_the_frame_aspect_ratio(self):
        x1, y1, x2, y2 = X._confirm_crop_box([600, 300, 640, 400], self.W, self.H)
        self.assertAlmostEqual((x2 - x1) / (y2 - y1), self.W / self.H, delta=0.02)

    def test_tiny_person_gets_at_least_a_fifth_of_the_frame(self):
        x1, y1, x2, y2 = X._confirm_crop_box([600, 300, 605, 315], self.W, self.H)
        self.assertGreaterEqual(y2 - y1, 0.2 * self.H - 1)

    def test_stays_inside_the_frame(self):
        for box in ([0, 0, 40, 100], [1240, 620, 1280, 720], [0, 0, 1280, 720]):
            x1, y1, x2, y2 = X._confirm_crop_box(box, self.W, self.H)
            self.assertGreaterEqual(min(x1, y1), 0)
            self.assertLessEqual(x2, self.W)
            self.assertLessEqual(y2, self.H)

    def test_confirmation_is_off_unless_asked_for(self):
        # robbery/vandalism build SceneViolenceDetector too; they must not inherit it
        self.assertFalse(X._DEFAULT_VIOLENCE_CFG["scene_person_confirm"])


if __name__ == "__main__":
    unittest.main()

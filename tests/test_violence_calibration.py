"""Temperature scaling in the shipped violence detector (evalkit's
calibrate_temperature.py writes it; x3d_violence_detector.py applies it)."""
import os
import sys
import unittest
from types import SimpleNamespace

import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "maincode"))
import x3d_violence_detector as X  # noqa: E402

calibrate = X.X3DViolenceDetector._calibrate


class Calibration(unittest.TestCase):
    probs = torch.tensor([[0.9, 0.1], [0.5, 0.5], [0.2, 0.8], [0.01, 0.99]])

    def test_uncalibrated_checkpoints_are_untouched(self):
        self.assertIs(calibrate(SimpleNamespace(temperature=1.0), self.probs), self.probs)

    def test_softening_keeps_order_and_the_half_point(self):
        out = calibrate(SimpleNamespace(temperature=2.0), self.probs)
        self.assertTrue(torch.allclose(out.sum(dim=1), torch.ones(4)))
        self.assertEqual(out[:, 1].argsort().tolist(), self.probs[:, 1].argsort().tolist())
        self.assertAlmostEqual(float(out[1, 1]), 0.5, places=6)
        self.assertLess(float(out[3, 1]), 0.99)   # an overconfident 0.99 comes down
        self.assertGreater(float(out[0, 1]), 0.1)

    def test_decisions_at_half_unchanged(self):
        for t in (0.5, 1.7, 3.0):
            out = calibrate(SimpleNamespace(temperature=t), self.probs)
            self.assertEqual((out[:, 1] >= 0.5).tolist(), (self.probs[:, 1] >= 0.5).tolist())


if __name__ == "__main__":
    unittest.main()

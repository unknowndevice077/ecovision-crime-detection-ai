"""The machine's own settings file (app/config_store.py): writers record only
what they change, and an old full snapshot can no longer pin the shipped
config's model paths and thresholds."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import config_store as C  # noqa: E402


def _write(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


SHIPPED = {
    "auth": {},
    "camera": {"index": 0, "source": ""},
    "detection": {"robbery": {"enabled": True, "confidence_threshold": 0.85,
                              "model_path": "weights/x3d_xs_robbery_scene.pt"}},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.dir.name, "shipped.json")
        self.mine = os.path.join(self.dir.name, "config.json")
        _write(self.base, SHIPPED)

    def tearDown(self):
        self.dir.cleanup()


class Saving(Base):
    def test_a_toggle_records_only_that_switch(self):
        C.save_overrides(self.mine, {("detection", "robbery", "enabled"): False}, self.base)
        overlay = C.load_overlay(self.mine, self.base)
        self.assertEqual(overlay, {"detection": {"robbery": {"enabled": False}}})

    def test_later_writes_keep_earlier_ones(self):
        C.save_overrides(self.mine, {("auth", "secret_key"): "k"}, self.base)
        C.save_overrides(self.mine, {("camera", "index"): 2, ("camera", "source"): ""}, self.base)
        overlay = C.load_overlay(self.mine, self.base)
        self.assertEqual(overlay["auth"], {"secret_key": "k"})
        self.assertEqual(overlay["camera"], {"index": 2, "source": ""})

    def test_dev_writes_into_the_shipped_file_in_place(self):
        # run_dev_system.bat points the writable dir at the repo, so the
        # "machine file" is config.json itself: change one key, keep the rest.
        C.save_overrides(self.base, {("camera", "index"): 3}, self.base)
        data = _read(self.base)
        self.assertEqual(data["camera"]["index"], 3)
        self.assertEqual(data["detection"], SHIPPED["detection"])
        self.assertNotIn("_overlay_version", data)
        self.assertEqual(C.load_overlay(self.base, self.base), {})


class OldSnapshots(Base):
    def test_old_snapshot_cannot_pin_thresholds_or_models(self):
        old = json.loads(json.dumps(SHIPPED))
        old["auth"]["secret_key"] = "machine-secret"
        old["camera"]["index"] = 1
        old["detection"]["robbery"].update(confidence_threshold=0.70, model_path="weights/old.pt")
        _write(self.mine, old)
        overlay = C.load_overlay(self.mine, self.base)
        self.assertEqual(overlay, {"auth": {"secret_key": "machine-secret"},
                                   "camera": {"index": 1, "source": ""}})

    def test_first_save_over_an_old_snapshot_drops_the_rest(self):
        old = json.loads(json.dumps(SHIPPED))
        old["detection"]["robbery"]["confidence_threshold"] = 0.70
        _write(self.mine, old)
        C.save_overrides(self.mine, {("detection", "robbery", "enabled"): False}, self.base)
        overlay = C.load_overlay(self.mine, self.base)
        self.assertNotIn("confidence_threshold", overlay["detection"]["robbery"])

    def test_missing_or_broken_file_means_no_overrides(self):
        self.assertEqual(C.load_overlay(self.mine, self.base), {})
        with open(self.mine, "w") as fh:
            fh.write("{not json")
        self.assertEqual(C.load_overlay(self.mine, self.base), {})


if __name__ == "__main__":
    unittest.main()

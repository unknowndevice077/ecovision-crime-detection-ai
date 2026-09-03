import os
import sys
import unittest

# backend.py itself uses script-relative imports (`from db import ...`),
# which only resolve when app/ is on sys.path -- true when it's launched
# directly (`python app/backend.py`, sys.path[0] == its own dir), false when
# imported as the package `app.backend` the way this test originally did.
# That import shape was never actually run successfully before this fix:
# `python -m unittest tests.test_backend_schemas` raised
# ModuleNotFoundError: No module named 'db'.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from app.backend import AiTriggerSchema, ManualClipSchema


class BackendSchemaTests(unittest.TestCase):
    # BUG FOUND 2026-09-02: this test asserted the OLD camelCase field names
    # (screenshotPath, crimeTimeMarker, associatedCrimeId) that main.py used
    # to send before the "BUG FOUND" fixes documented at maincode/main.py:1122
    # and :2010 -- both schemas were migrated to snake_case
    # (screenshot_path, crime_time_marker, associated_incident_id) as part of
    # that project-wide camelCase->snake_case cleanup, but this test was never
    # updated to match. It has been failing ever since (AttributeError on the
    # first assertion, a pydantic ValidationError on the second) -- silently,
    # because `from app.backend import ...` couldn't even import until the
    # sys.path fix above, so `python tests/test_backend_schemas.py` never ran
    # far enough to hit either failure. Rewritten to assert the real,
    # currently-correct snake_case contract instead of the pre-fix one.
    def test_ai_trigger_schema_preserves_screenshot_path(self):
        payload = AiTriggerSchema(
            id="abc123",
            event="ASSAULT",
            confidence=0.92,
            screenshot_path="/static/screenshots/snap_abc123.jpg",
        )
        self.assertEqual(payload.screenshot_path, "/static/screenshots/snap_abc123.jpg")

    def test_ai_trigger_schema_rejects_stale_camelcase_alias(self):
        # Regression guard for the exact bug main.py used to have: a
        # camelCase screenshotPath is silently DROPPED (Pydantic ignores
        # unknown fields by default), not an error -- so a caller reverting
        # to the old field name fails quietly, with screenshot_path back to
        # None, rather than a loud rejection. Documenting that behavior here
        # so a future regression is at least a red test, not a silent no-op.
        payload = AiTriggerSchema(
            id="abc123",
            event="ASSAULT",
            confidence=0.92,
            screenshotPath="/static/screenshots/snap_abc123.jpg",
        )
        self.assertIsNone(payload.screenshot_path)

    def test_manual_clip_schema_preserves_associated_incident_id(self):
        payload = ManualClipSchema(
            filename="clip.mp4",
            duration="5s",
            type="CLIP",
            crime_time_marker="00:12",
            notes="captured by AI",
            associated_incident_id="abc123",
        )
        self.assertEqual(payload.associated_incident_id, "abc123")
        self.assertEqual(payload.crime_time_marker, "00:12")


if __name__ == "__main__":
    unittest.main()

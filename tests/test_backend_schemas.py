import unittest

from app.backend import AiTriggerSchema, ManualClipSchema


class BackendSchemaTests(unittest.TestCase):
    def test_ai_trigger_schema_preserves_screenshot_path(self):
        payload = AiTriggerSchema(
            id="abc123",
            event="ASSAULT",
            confidence=0.92,
            screenshotPath="/static/screenshots/snap_abc123.jpg",
        )
        self.assertEqual(payload.screenshotPath, "/static/screenshots/snap_abc123.jpg")

    def test_manual_clip_schema_preserves_associated_crime_id(self):
        payload = ManualClipSchema(
            filename="clip.mp4",
            duration="5s",
            type="CLIP",
            crimeTimeMarker="00:12",
            notes="captured by AI",
            associatedCrimeId="abc123",
        )
        self.assertEqual(payload.associatedCrimeId, "abc123")


if __name__ == "__main__":
    unittest.main()

"""Operator verdicts on AI alerts become training labels, the quality panel
reads them, and the export script turns them into a dataset."""
import csv
import os
import subprocess
import sys
import unittest

import cv2
import numpy as np

import _support as S


class DetectionFeedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        cls.dev = S.make_devteam()
        cls.brgy, cls.cams = S.make_barangay(2)
        cls.station = S.make_station(cls.dev, [cls.brgy])
        cls.officer = S.make_user(cls.dev, "PNP_OFFICER", station_id=cls.station,
                                  permissions={"confirm_dismiss_alerts": True})
        cls.shots = os.path.join(S.WRITABLE, "static", "screenshots")
        os.makedirs(cls.shots, exist_ok=True)

    def alert(self, event, conf, cam, clean=True, weapons=None):
        iid_hint = S.uid("snap")
        img = np.full((360, 640, 3), 90, np.uint8)
        cv2.imwrite(os.path.join(self.shots, f"snap_{iid_hint}.jpg"), img)
        if clean:
            cv2.imwrite(os.path.join(self.shots, f"snap_{iid_hint}_clean.jpg"), img)
        return S.ai_alert(self.brgy, cam, event, conf, context={"detector": "test", "weapons": weapons or []},
                          screenshot=f"/static/screenshots/snap_{iid_hint}.jpg")

    def verdict(self, iid, status):
        r = self.c.patch(f"/api/incidents/{iid}/status", headers=S.auth(self.officer), json={"status": status})
        self.assertEqual(r.status_code, 200, r.text)

    def feedback(self, iid):
        conn = S.db(); cur = conn.cursor()
        cur.execute("SELECT * FROM detection_feedback WHERE incident_id = ?", (iid,))
        r = cur.fetchone(); conn.close()
        return dict(r) if r else None

    def test_verdicts_become_labels(self):
        a = self.alert("ARMED THREAT", 0.8, self.cams[0])
        self.verdict(a, "Confirmed")
        self.assertEqual(self.feedback(a)["label"], "confirmed")
        self.verdict(a, "Dismissed")
        self.assertEqual(self.feedback(a)["label"], "dismissed", "a changed verdict updates the label")

    def test_manual_incidents_are_not_labels(self):
        inc = S.make_incident(self.brgy)
        self.verdict(inc, "Dismissed")
        self.assertIsNone(self.feedback(inc))

    def test_retype_keeps_the_models_call(self):
        a = self.alert("ASSAULT", 0.7, self.cams[1])
        details = {"incident_type": "ROBBERY", "reporting_officer": "PO1 Test"}
        details.update({k: "x" for k in ("badge_number", "narrative", "rank", "unit", "location", "persons_involved",
                                         "action_taken", "summary", "disposition")})
        details.update({"date": "2026-09-30", "time": "10:00", "severity": "HIGH"})
        r = self.c.post(f"/api/incidents/{a}/confirm-and-report", headers=S.auth(self.officer),
                        json={"status": "Confirmed", "report_details": details})
        self.assertEqual(r.status_code, 200, r.text)
        fb = self.feedback(a)
        self.assertEqual((fb["ai_event"], fb["final_type"]), ("ASSAULT", "ROBBERY"))

    def test_quality_panel_and_export(self):
        cam = self.cams[0]
        ok = self.alert("ARMED THREAT", 0.81, cam, weapons=[{"name": "knife", "conf": 0.4, "box": [300, 150, 360, 210]}])
        bad = self.alert("ARMED THREAT", 0.35, cam, weapons=[{"name": "gun", "conf": 0.31, "box": [100, 100, 140, 160]}])
        old = self.alert("ASSAULT", 0.66, cam, clean=False)
        self.verdict(ok, "Confirmed"); self.verdict(bad, "Dismissed"); self.verdict(old, "Dismissed")

        q = self.c.get("/api/devteam/detection_quality?days=30", headers=S.auth(self.dev)).json()
        row = next(r for r in q["rows"] if r["camera_id"] == cam and r["event"] == "ARMED THREAT")
        self.assertGreaterEqual(row["alerts"], 2)
        self.assertIsNotNone(row["precision"])
        self.assertEqual(self.c.get("/api/devteam/detection_quality", headers=S.auth(self.officer)).status_code, 403)

        out = os.path.join(S.WRITABLE, "export")
        env = {**os.environ, "ECOVISION_WRITABLE_DIR": S.WRITABLE}
        p = subprocess.run([sys.executable, str(S.REPO / "tools" / "export_feedback_dataset.py"), "--out", out, "--all"],
                           capture_output=True, text=True, env=env, cwd=str(S.REPO))
        self.assertEqual(p.returncode, 0, p.stderr)
        ds = os.path.join(out, os.listdir(out)[0])
        m = {r["incident_id"]: r for r in csv.DictReader(open(os.path.join(ds, "manifest.csv"), encoding="utf-8"))}
        self.assertEqual(m[ok]["frame_is_annotated"], "False")
        self.assertEqual(m[old]["frame_is_annotated"], "True")
        self.assertTrue(os.path.exists(os.path.join(ds, "weapon_crops", "confirmed", "knife", f"{ok}_0.jpg")))
        self.assertTrue(os.path.exists(os.path.join(ds, "weapon_crops", "dismissed", "gun", f"{bad}_0.jpg")))
        for label in ("confirmed", "dismissed"):
            self.assertTrue(os.path.exists(os.path.join(ds, f"contact_sheet_{label}.jpg")))
        p = subprocess.run([sys.executable, str(S.REPO / "tools" / "export_feedback_dataset.py"), "--out", out],
                           capture_output=True, text=True, env=env, cwd=str(S.REPO))
        self.assertIn("Nothing to export", p.stdout)


if __name__ == "__main__":
    unittest.main()

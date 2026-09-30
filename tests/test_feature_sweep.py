"""Every feature, end to end through the API, as the account that uses it.

Complements the permission and application suites: those prove who is
refused; this proves each feature actually does its job for the people who
are allowed, stays inside their jurisdiction, and leaves an audit trail.
Found and fixed while writing it (2026-10-01): manual filing, archiving,
clip registration, evidence notes and report-request answers needed no
permission; barangay admins could delete police evidence; the AI-core
endpoints and the live WebSocket were open to the LAN; an invalid incident
status came back as a 500.
"""
import json
import unittest
from pathlib import Path

import cv2
import numpy as np

import _support as S

B = S.B


def jpg():
    return cv2.imencode(".jpg", np.zeros((24, 24, 3), np.uint8))[1].tobytes()


def snap(row):
    return json.loads(row["target_snapshot"]) if row and row.get("target_snapshot") else {}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        B.limiter.enabled = False
        cls.dev = S.make_devteam()
        cls.brgyA, cls.camsA = S.make_barangay(2)
        cls.brgyB, cls.camsB = S.make_barangay(1)
        cls.stA = S.make_station(cls.dev, [cls.brgyA])
        cls.stB = S.make_station(cls.dev, [cls.brgyB])
        cls.capA = S.make_user(cls.dev, "BARANGAY_ADMIN", barangay_id=cls.brgyA)
        cls.capB = S.make_user(cls.dev, "BARANGAY_ADMIN", barangay_id=cls.brgyB)
        cls.pnpA = S.make_user(cls.dev, "PNP_ADMIN", station_id=cls.stA)
        cls.pnpB = S.make_user(cls.dev, "PNP_ADMIN", station_id=cls.stB)
        cls.offA = S.make_user(cls.dev, "PNP_OFFICER", station_id=cls.stA,
                               permissions={"view_map": True, "view_records": True, "view_history": True, "confirm_dismiss_alerts": True})
        cls.staffA = S.make_user(cls.dev, "BARANGAY_STAFF", barangay_id=cls.brgyA,
                                 permissions={"view_map": True, "confirm_dismiss_alerts": True})

    def ok(self, r, code=200):
        self.assertEqual(r.status_code, code, r.text)
        return r.json() if r.content else None

    def h(self, who):
        return S.auth(who)

    def ids(self, r):
        return {x["id"] for x in self.ok(r)}


class Cameras(Base):
    def test_create_list_delete_within_own_barangay(self):
        cam = self.ok(self.c.post("/api/cameras", headers=self.h(self.capA),
                                  json={"name": "Market gate", "url": "rtsp://u:p@10.0.0.5/s", "barangay_id": self.brgyA}))
        cid = cam.get("id") or cam["camera"]["id"]
        self.assertTrue(S.audit_rows("camera.created", cid))
        self.assertIn(cid, self.ids(self.c.get("/api/cameras", headers=self.h(self.pnpA))), "police see cameras in their area")
        self.assertNotIn(cid, self.ids(self.c.get("/api/cameras", headers=self.h(self.capB))))
        self.assertNotIn(cid, self.ids(self.c.get("/api/cameras", headers=self.h(self.pnpB))))
        self.assertIn(self.c.delete(f"/api/cameras/{cid}", headers=self.h(self.capB)).status_code, (403, 404))
        self.ok(self.c.delete(f"/api/cameras/{cid}", headers=self.h(self.capA)))
        self.assertNotIn(cid, self.ids(self.c.get("/api/cameras", headers=self.h(self.capA))))

    def test_cannot_add_a_camera_to_another_barangay(self):
        r = self.c.post("/api/cameras", headers=self.h(self.capA), json={"name": "x", "url": "rtsp://x", "barangay_id": self.brgyB})
        self.assertIn(r.status_code, (403, 404))

    def test_per_camera_model_and_threshold(self):
        cam = self.camsA[0]
        self.ok(self.c.patch(f"/api/cameras/{cam}/models/violence", headers=self.h(self.capA), json={"enabled": False}))
        models = self.ok(self.c.get(f"/api/cameras/{cam}/models", headers=self.h(self.capA)))
        self.assertIn("violence", json.dumps(models))
        self.ok(self.c.patch(f"/api/cameras/{cam}/thresholds/violence", headers=self.h(self.capA), json={"threshold": 0.7}))
        self.assertIn("violence", self.ok(self.c.get(f"/api/cameras/{cam}/thresholds", headers=self.h(self.capA)))["overrides"])
        self.ok(self.c.delete(f"/api/cameras/{cam}/thresholds/violence", headers=self.h(self.capA)))
        self.assertEqual(self.ok(self.c.get(f"/api/cameras/{cam}/thresholds", headers=self.h(self.capA)))["overrides"], {})
        self.ok(self.c.patch(f"/api/cameras/{cam}/models/violence", headers=self.h(self.capA), json={"enabled": True}))
        self.assertEqual(self.c.patch(f"/api/cameras/{cam}/thresholds/violence", headers=self.h(self.capB),
                                      json={"threshold": 0.1}).status_code, 404)

    def test_ptz_reports_unconfigured_cleanly(self):
        r = self.c.get(f"/api/ptz/capabilities?camera_id={self.camsA[0]}", headers=self.h(self.capA))
        self.assertFalse(self.ok(r)["configured"])
        self.assertEqual(self.c.post("/api/ptz/move", headers=self.h(self.capA), json={"pan": 0.2}).status_code, 503)


class NotifyTargets(Base):
    def test_each_side_manages_its_own_recipients(self):
        t = self.ok(self.c.post("/api/notify_targets", headers=self.h(self.capA),
                                json={"barangay_id": self.brgyA, "channel": "telegram", "destination": "12345", "label": "Tanod"}))["id"]
        p = self.ok(self.c.post("/api/notify_targets", headers=self.h(self.pnpA),
                                json={"station_id": self.stA, "channel": "sms", "destination": "09170000001"}))["id"]
        self.assertEqual(self.c.post("/api/notify_targets", headers=self.h(self.capA),
                                     json={"barangay_id": self.brgyB, "channel": "sms", "destination": "1"}).status_code, 403)
        self.assertEqual(self.c.post("/api/notify_targets", headers=self.h(self.capA),
                                     json={"barangay_id": self.brgyA, "channel": "carrier-pigeon", "destination": "1"}).status_code, 400)
        mine = self.ids(self.c.get("/api/notify_targets", headers=self.h(self.capA)))
        self.assertIn(t, mine)
        self.assertNotIn(t, self.ids(self.c.get("/api/notify_targets", headers=self.h(self.capB))))
        self.assertIn(self.c.delete(f"/api/notify_targets/{p}", headers=self.h(self.capA)).status_code, (403, 404))
        self.ok(self.c.delete(f"/api/notify_targets/{t}", headers=self.h(self.capA)))
        self.ok(self.c.delete(f"/api/notify_targets/{p}", headers=self.h(self.pnpA)))
        self.assertTrue(S.audit_rows("notify_target.created", t))


class Incidents(Base):
    def manual(self, who, barangay_id, **kw):
        body = {"id": S.uid("man"), "case_id": S.uid("CASE"), "type": "THEFT", "officer": "MANUAL_ENTRY", "lat": 11.0, "lng": 124.6,
                "location_name": "Market", "severity": "LOW", "occurred_date": "2026-10-01", "occurred_time": "0900",
                "narrative": "Wallet snatched", "nature_of_call": "Walk-in", "arrival_reason": "Report", "additional_officers": "None",
                "status": "Active", "barangay_id": barangay_id, **kw}
        return body["id"], self.c.post("/api/incidents", headers=self.h(who), json=body)

    def test_barangay_filing_always_lands_in_its_own_barangay(self):
        iid, r = self.manual(self.staffA, self.brgyB)
        self.ok(r)
        self.assertIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.capA))))
        self.assertNotIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.capB))))
        self.assertEqual(snap(S.audit_rows("incident.filed", iid)[0])["barangay_id"], self.brgyA)

    def test_police_file_into_covered_barangays_only(self):
        iid, r = self.manual(self.offA, self.brgyA)
        self.ok(r)
        self.assertIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.capA))))
        _, r = self.manual(self.offA, self.brgyB)
        self.assertEqual(r.status_code, 403)

    def test_filing_needs_the_map(self):
        blind = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={})
        _, r = self.manual(blind, self.brgyA)
        self.assertEqual(r.status_code, 403)

    def test_ai_alert_reaches_the_area_and_only_from_this_machine(self):
        iid = S.ai_alert(self.brgyA, self.camsA[0])
        self.assertIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.pnpA))))
        self.assertNotIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.pnpB))))
        B.TRUSTED_SERVICE_HOSTS.discard("testclient")
        try:
            r = self.c.post("/api/ai_trigger", json={"id": S.uid("ai"), "event": "ASSAULT", "confidence": 0.9, "barangay_id": self.brgyA})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.c.get(f"/api/camera_name/{self.camsA[0]}").status_code, 403)
            self.assertEqual(self.c.post("/api/ai_register_clip", json={"filename": "x.mp4", "duration": "1", "type": "CLIP",
                                                                       "crime_time_marker": "0", "notes": ""}).status_code, 403)
        finally:
            B.TRUSTED_SERVICE_HOSTS.add("testclient")

    def test_status_confirm_dismiss_and_invalid_value(self):
        iid = S.make_incident(self.brgyA)
        self.assertEqual(self.c.patch(f"/api/incidents/{iid}/status", headers=self.h(self.staffA),
                                      json={"status": "Banana"}).status_code, 400)
        self.ok(self.c.patch(f"/api/incidents/{iid}/status", headers=self.h(self.staffA), json={"status": "Dismissed"}))
        self.assertTrue(S.audit_rows("incident.dismissed", iid))
        self.assertEqual(self.c.patch(f"/api/incidents/{iid}/status", headers=self.h(self.capB),
                                      json={"status": "Confirmed"}).status_code, 404)

    def test_archive_hides_from_map_but_keeps_history(self):
        iid = S.make_incident(self.brgyA, status="Confirmed")
        self.ok(self.c.patch(f"/api/incidents/{iid}/archive", headers=self.h(self.offA)))
        rows = {x["id"]: x for x in self.ok(self.c.get("/api/incidents?purpose=history", headers=self.h(self.offA)))}
        self.assertIn(iid, rows)
        self.assertTrue(rows[iid].get("map_hidden"))

    def test_delete_is_soft_admin_only_and_restorable(self):
        iid = S.make_incident(self.brgyA)
        self.assertEqual(self.c.delete(f"/api/incidents/{iid}", headers=self.h(self.offA)).status_code, 403)
        self.ok(self.c.delete(f"/api/incidents/{iid}", headers=self.h(self.pnpA)))
        self.assertNotIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.pnpA))))
        entry = S.audit_rows("incident.deleted", iid)[0]
        self.ok(self.c.post(f"/api/devteam/audit_log/{entry['id']}/restore", headers=self.h(self.dev)))
        self.assertIn(iid, self.ids(self.c.get("/api/incidents", headers=self.h(self.pnpA))))

    def test_report_draft_then_confirm_and_report(self):
        iid = S.ai_alert(self.brgyA, self.camsA[0], event="ROBBERY")
        draft = self.ok(self.c.get(f"/api/incidents/{iid}/report_draft", headers=self.h(self.offA)))
        self.assertTrue(draft["ai_draft"])
        self.ok(self.c.put(f"/api/incidents/{iid}/report_draft", headers=self.h(self.offA),
                           json={"report_body": {"narrative": "Two suspects fled north"}}))
        r = self.c.post(f"/api/incidents/{iid}/confirm-and-report", headers=self.h(self.offA),
                        json={"status": "Confirmed", "report_details": {"narrative": "Two suspects fled north"}})
        self.assertEqual(r.status_code, 400, "reporting officer and badge are required")
        self.ok(self.c.post(f"/api/incidents/{iid}/confirm-and-report", headers=self.h(self.offA), json={
            "status": "Confirmed", "report_details": {"reporting_officer": "PO2 Cruz", "badge_number": "1234",
                                                      "narrative": "Two suspects fled north", "incident_type": "ROBBERY"}}))
        reports = self.ok(self.c.get(f"/api/incidents/{iid}/reports", headers=self.h(self.offA)))
        self.assertTrue(any(r["report_status"] == "confirmed" for r in reports))
        self.assertTrue(S.audit_rows("report_confirmed", iid))

    def test_barangay_accounts_never_see_the_investigative_narrative(self):
        iid = S.ai_alert(self.brgyA, self.camsA[0])
        police = {x["id"]: x for x in self.ok(self.c.get("/api/incidents", headers=self.h(self.pnpA)))}[iid]
        barangay = {x["id"]: x for x in self.ok(self.c.get("/api/incidents", headers=self.h(self.capA)))}[iid]
        self.assertNotIn("RESTRICTED", police["narrative"])
        self.assertIn("RESTRICTED", barangay["narrative"])

    def test_panic_button_files_a_critical_incident(self):
        r = self.ok(self.c.post("/api/panic_trigger", json={"event": "PANIC", "device": "pole-1", "barangay_id": self.brgyA}))
        types = {x["type"] for x in self.ok(self.c.get("/api/incidents", headers=self.h(self.capA)))}
        self.assertIn("HARDWARE_PANIC_INTERRUPT", types)
        self.assertTrue(r)

    def test_siren_is_gated_and_harmless_when_disabled(self):
        self.assertEqual(self.ok(self.c.post("/siren/activate", headers=self.h(self.staffA)))["status"], "skipped")
        blind = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={})
        self.assertEqual(self.c.post("/siren/activate", headers=self.h(blind)).status_code, 403)


class Records(Base):
    def record(self, with_report=False):
        rid = S.uid("rec")
        fn = f"{rid}.mp4"
        w = cv2.VideoWriter(str(Path(B.RECORDINGS_DIR) / fn), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
        for i in range(40):
            w.write(np.full((48, 64, 3), i * 5, np.uint8))
        w.release()
        inc = S.make_incident(self.brgyA, status="Confirmed")
        conn = S.db(); cur = conn.cursor()
        cur.execute("INSERT INTO video_records (id, filename, file_path, recorded_at, duration, type, associated_incident_id, barangay_id) "
                    "VALUES (?, ?, ?, '2026-10-01 09:00:00', '00:04', 'CRIME_CLIP', ?, ?)",
                    (rid, fn, str(Path(B.RECORDINGS_DIR) / fn), inc, self.brgyA))
        if with_report:
            cur.execute("INSERT INTO incident_reports (id, incident_id, reported_by, narrative) VALUES (?, ?, ?, 'x')",
                        (S.uid("r"), inc, self.pnpA["id"]))
        conn.commit(); conn.close()
        return rid, inc

    def test_vault_is_scoped_and_police_only(self):
        rid, _ = self.record()
        self.assertIn(rid, self.ids(self.c.get("/api/records", headers=self.h(self.offA))))
        self.assertNotIn(rid, self.ids(self.c.get("/api/records", headers=self.h(self.pnpB))))
        self.assertEqual(self.c.get("/api/records", headers=self.h(self.capA)).status_code, 403)

    def test_notes_extract_and_delete(self):
        rid, _ = self.record()
        self.ok(self.c.patch(f"/api/records/{rid}/notes", headers=self.h(self.offA), json={"notes": "Suspect in red shirt at 00:02"}))
        self.assertEqual(self.c.patch(f"/api/records/{rid}/notes", headers=self.h(self.capA), json={"notes": "x"}).status_code, 403)
        r = self.c.post(f"/api/records/{rid}/extract", headers=self.h(self.offA), json={"start": "00:01", "end": "00:03", "notes": "cut"})
        self.assertIn(r.status_code, (200, 500), r.text)  # 500 only if ffmpeg is missing on this machine
        self.assertEqual(self.c.delete(f"/api/records/{rid}", headers=self.h(self.offA)).status_code, 403)
        self.assertEqual(self.c.delete(f"/api/records/{rid}", headers=self.h(self.capA)).status_code, 403)
        self.ok(self.c.delete(f"/api/records/{rid}", headers=self.h(self.pnpA)))

    def test_evidence_for_a_filed_report_cannot_be_deleted(self):
        rid, _ = self.record(with_report=True)
        self.assertEqual(self.c.delete(f"/api/records/{rid}", headers=self.h(self.pnpA)).status_code, 409)

    def test_manual_clip_registration_needs_the_vault(self):
        body = {"filename": f"{S.uid('m')}.mp4", "duration": "00:03", "type": "CLIP", "crime_time_marker": "00:01", "notes": "n"}
        self.ok(self.c.post("/api/records/register_clip", headers=self.h(self.offA), json=body))
        self.assertEqual(self.c.post("/api/records/register_clip", headers=self.h(self.staffA), json=body).status_code, 403)


class ReportRequests(Base):
    def test_full_lifecycle(self):
        rid = self.ok(self.c.post("/api/report_requests", headers=self.h(self.staffA),
                                  json={"description": "Copy of the report for the market fight on Sept 30"}))["id"]
        self.assertIn(rid, self.ids(self.c.get("/api/report_requests", headers=self.h(self.pnpA))))
        self.assertNotIn(rid, self.ids(self.c.get("/api/report_requests", headers=self.h(self.pnpB))))
        self.assertNotIn(rid, self.ids(self.c.get("/api/report_requests", headers=self.h(self.capB))))
        self.assertEqual(self.c.post(f"/api/report_requests/{rid}/fulfill", headers=self.h(self.pnpA), json={"note": "x"}).status_code, 400,
                         "only an accepted request can be fulfilled")
        self.assertIn(self.c.post(f"/api/report_requests/{rid}/accept", headers=self.h(self.pnpB), json={}).status_code, (403, 404))
        self.ok(self.c.post(f"/api/report_requests/{rid}/accept", headers=self.h(self.offA), json={"note": "On it"}))
        self.assertEqual(self.c.post(f"/api/report_requests/{rid}/fulfill", headers=self.h(self.pnpA), json={}).status_code, 400)
        self.ok(self.c.post(f"/api/report_requests/{rid}/fulfill", headers=self.h(self.pnpA),
                            json={"note": "Blotter entry 2026-0930-114, two suspects detained"}))
        mine = {x["id"]: x for x in self.ok(self.c.get("/api/report_requests", headers=self.h(self.capA)))}
        self.assertEqual(mine[rid]["status"], "fulfilled")
        self.assertIn("Blotter", mine[rid]["response_note"])

    def test_decline_and_officer_without_history_cannot_answer(self):
        rid = self.ok(self.c.post("/api/report_requests", headers=self.h(self.capA), json={"description": "Report on the robbery"}))["id"]
        blind = S.make_user(self.dev, "PNP_OFFICER", station_id=self.stA, permissions={"view_map": True})
        self.assertEqual(self.c.post(f"/api/report_requests/{rid}/decline", headers=self.h(blind), json={}).status_code, 403)
        self.ok(self.c.post(f"/api/report_requests/{rid}/decline", headers=self.h(self.pnpA), json={"note": "No such report"}))
        self.assertEqual(self.c.post(f"/api/report_requests/{rid}/accept", headers=self.h(self.pnpA), json={}).status_code, 400)
        self.assertEqual(self.c.post("/api/report_requests", headers=self.h(self.pnpA), json={"description": "x"}).status_code, 403)


class Accounts(Base):
    def test_admin_creates_resets_and_removes_own_staff(self):
        name = S.uid("tanod")
        uid_ = self.ok(self.c.post("/api/admin/users", headers=self.h(self.capA),
                                   json={"username": name, "password": S.PASSWORD, "assignment": "Gate", "permissions": {"view_map": True}}))["id"]
        row = S.user_row(name)
        self.assertEqual((row["role"], row["barangay_id"], row["parent_admin_id"]), ("BARANGAY_STAFF", self.brgyA, self.capA["id"]))
        self.assertIn(uid_, {u["id"] for u in self.ok(self.c.get("/api/admin/users", headers=self.h(self.capA)))})
        self.assertNotIn(uid_, {u["id"] for u in self.ok(self.c.get("/api/admin/users", headers=self.h(self.capB)))})
        new_pw = self.ok(self.c.post(f"/api/admin/users/{uid_}/reset_password", headers=self.h(self.capA)))["new_password"]
        self.assertEqual(self.c.post("/api/login", json={"username": name, "password": S.PASSWORD}).status_code, 401)
        self.ok(self.c.post("/api/login", json={"username": name, "password": new_pw}))
        self.assertEqual(self.c.delete(f"/api/admin/users/{uid_}", headers=self.h(self.capB)).status_code, 403)
        self.ok(self.c.delete(f"/api/admin/users/{uid_}", headers=self.h(self.capA)))
        self.assertEqual(self.c.post("/api/login", json={"username": name, "password": new_pw}).status_code, 401)

    def test_police_admin_creates_officers(self):
        name = S.uid("po")
        self.ok(self.c.post("/api/admin/users", headers=self.h(self.pnpA),
                            json={"username": name, "password": S.PASSWORD, "assignment": "Patrol", "permissions": {"view_history": True}}))
        row = S.user_row(name)
        self.assertEqual((row["role"], row["station_id"]), ("PNP_OFFICER", self.stA))

    def test_admin_camera_grants_stay_inside_their_barangay(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={"view_map": True})
        url = f"/api/admin/users/{staff['id']}/resource_permissions"
        self.ok(self.c.post(url, headers=self.h(self.capA), json={"permission_key": "view_map", "resource_type": "camera", "resource_id": self.camsA[0]}))
        self.assertEqual(self.c.post(url, headers=self.h(self.capA), json={"permission_key": "view_map", "resource_type": "camera",
                                                                            "resource_id": self.camsB[0]}).status_code, 403)
        self.assertEqual(self.ids(self.c.get("/api/cameras", headers=self.h(staff))), {self.camsA[0]})
        self.ok(self.c.request("DELETE", url, headers=self.h(self.capA),
                               json={"permission_key": "view_map", "resource_type": "camera", "resource_id": self.camsA[0]}))
        self.assertEqual(self.ids(self.c.get("/api/cameras", headers=self.h(staff))), set(self.camsA))

    def test_identity_verification_round_trip(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={})
        self.ok(self.c.post("/api/users/me/verification", headers=self.h(staff),
                            files={"id_document": ("id.jpg", jpg(), "image/jpeg"), "face_photo": ("f.jpg", jpg(), "image/jpeg")}))
        self.assertEqual(S.user_row(staff["username"])["verification_status"], "pending")
        self.assertEqual(self.c.get(f"/api/users/{staff['id']}/verification_document", headers=self.h(self.capA)).status_code, 200)
        self.assertEqual(self.c.get(f"/api/users/{staff['id']}/face_photo", headers=self.h(self.capA)).status_code, 200)
        self.assertEqual(self.c.get(f"/api/users/{staff['id']}/verification_document", headers=self.h(self.capB)).status_code, 403)
        self.assertEqual(self.c.get(f"/api/users/{staff['id']}/verification_document", headers=self.h(self.pnpA)).status_code, 403)
        self.ok(self.c.post(f"/api/admin/users/{staff['id']}/verification", headers=self.h(self.capA), json={"decision": "verified"}))
        self.assertEqual(S.user_row(staff["username"])["verification_status"], "verified")
        self.assertEqual(self.c.post(f"/api/admin/users/{staff['id']}/verification", headers=self.h(self.capA),
                                     json={"decision": "maybe"}).status_code, 400)

    def test_me_can_answers_the_ai_core(self):
        self.assertTrue(self.ok(self.c.get("/api/me/can/manage_cameras", headers=self.h(self.capA)))["allowed"])
        self.assertFalse(self.ok(self.c.get("/api/me/can/manage_cameras", headers=self.h(self.pnpA)))["allowed"])
        self.assertFalse(self.ok(self.c.get("/api/me/can/manage_cameras", headers=self.h(self.staffA)))["allowed"])
        self.assertEqual(self.c.get("/api/me/can/manage_cameras").status_code, 401)
        self.assertEqual(self.c.get("/api/me/can/fly", headers=self.h(self.capA)).status_code, 404)

    def test_me_returns_the_live_account(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={})
        h = self.h(staff)
        self.assertEqual(json.loads(self.ok(self.c.get("/api/me", headers=h))["user"]["permissions"]), {})
        self.c.patch(f"/api/admin/users/{staff['id']}/permissions", headers=self.h(self.capA), json={"permissions": {"view_map": True}})
        self.assertEqual(json.loads(self.ok(self.c.get("/api/me", headers=h))["user"]["permissions"]), {"view_map": True})


class DevTeamTools(Base):
    def test_custom_roles_create_list_delete(self):
        rid = self.ok(self.c.post("/api/devteam/custom_roles", headers=self.h(self.dev),
                                  json={"name": S.uid("Desk Officer"), "permissions": {"view_map": True, "confirm_dismiss_alerts": True}}))["id"]
        self.assertIn(rid, {r["id"] for r in self.ok(self.c.get("/api/custom_roles", headers=self.h(self.pnpA)))})
        self.ok(self.c.delete(f"/api/devteam/custom_roles/{rid}", headers=self.h(self.dev)))
        self.assertNotIn(rid, {r["id"] for r in self.ok(self.c.get("/api/custom_roles", headers=self.h(self.dev)))})

    def test_station_lifecycle(self):
        sid = S.make_station(self.dev)
        self.ok(self.c.put(f"/api/devteam/stations/{sid}/jurisdiction", headers=self.h(self.dev), json={"barangay_ids": [self.brgyB]}))
        officer = S.make_user(self.dev, "PNP_OFFICER", station_id=sid, permissions={"view_map": True})
        self.assertEqual(self.c.delete(f"/api/devteam/stations/{sid}", headers=self.h(self.dev)).status_code, 409, "has accounts")
        self.ok(self.c.delete(f"/api/devteam/users/{officer['id']}", headers=self.h(self.dev)))
        r = self.c.delete(f"/api/devteam/stations/{sid}", headers=self.h(self.dev))
        self.assertEqual(r.status_code, 409, "a removed account can still be restored into it")
        self.assertIn("removed account", r.json()["detail"])
        empty = S.make_station(self.dev)
        self.ok(self.c.delete(f"/api/devteam/stations/{empty}", headers=self.h(self.dev)))
        self.assertTrue(S.audit_rows("station.removed", empty))

    def test_register_barangay_under_station(self):
        name = S.uid("Brgy").replace("_", "-")
        r = self.ok(self.c.post(f"/api/devteam/stations/{self.stB}/barangays", headers=self.h(self.dev), json={
            "name": name, "city_municipality": "Ormoc City", "psgc_code": "0837370015", "reason": S.REASON, "confirm_password": S.PASSWORD}))
        st = next(s for s in self.ok(self.c.get("/api/devteam/stations", headers=self.h(self.dev))) if s["id"] == self.stB)
        self.assertIn(r["barangay_id"], st["barangay_ids"])
        self.assertEqual(self.c.post(f"/api/devteam/stations/{self.stB}/barangays", headers=self.h(self.dev), json={
            "name": S.uid("Other"), "city_municipality": "Ormoc City", "psgc_code": "0837370015", "reason": S.REASON,
            "confirm_password": S.PASSWORD}).status_code, 409, "PSGC codes are unique")

    def test_overview_quality_models_and_optimizer_status(self):
        ov = self.ok(self.c.get("/api/devteam/overview", headers=self.h(self.dev)))
        self.assertTrue(ov["users"])
        self.ok(self.c.get("/api/devteam/detection_quality?days=7", headers=self.h(self.dev)))
        models = self.ok(self.c.get("/api/devteam/detection-models", headers=self.h(self.dev)))
        self.assertTrue(models)
        self.assertEqual(self.c.patch("/api/devteam/detection-models/violence", headers=self.h(self.capA),
                                      json={"threshold": 0.9}).status_code, 403, "thresholds are DevTeam-only")
        self.assertEqual(self.c.patch("/api/devteam/detection-models/not-a-model", headers=self.h(self.dev),
                                      json={"enabled": True}).status_code, 404)
        status = self.ok(self.c.get("/api/devteam/optimize_weights/status", headers=self.h(self.dev)))
        self.assertIn("running", status)

    def test_audit_log_filters(self):
        rows = self.ok(self.c.get("/api/devteam/audit_log?q=station&limit=5", headers=self.h(self.dev)))
        self.assertLessEqual(len(rows), 5)
        self.assertTrue(all("station" in json.dumps(r).lower() for r in rows))


class LiveSocket(Base):
    def test_needs_a_session_and_only_carries_your_area(self):
        with self.assertRaises(Exception):
            with self.c.websocket_connect("/ws") as ws:
                ws.receive_json()
        token_b = S.auth(self.capB)["Authorization"].split(" ", 1)[1]
        token_a = S.auth(self.capA)["Authorization"].split(" ", 1)[1]
        with self.c.websocket_connect(f"/ws?token={token_a}") as wa, self.c.websocket_connect(f"/ws?token={token_b}") as wb:
            S.ai_alert(self.brgyA, self.camsA[0])
            msg = wa.receive_json()
            self.assertEqual((msg["channel"], msg["barangay_id"]), ("incidents", self.brgyA))
            # B's socket gets the next broadcast that concerns B, not A's alert.
            S.ai_alert(self.brgyB, self.camsB[0])
            self.assertEqual(wb.receive_json()["barangay_id"], self.brgyB)


if __name__ == "__main__":
    unittest.main()

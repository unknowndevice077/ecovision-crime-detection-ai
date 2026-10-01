"""Features added 2026-10-01: Personnel dicing for admins, password rules,
audit-log filters, report narrative wording, clip names, and police handing
a report (chosen fields, edited summary, attached files) back on a request."""
import io
import json
import unittest

import _support as S

B = S.B


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

    def ok(self, r, code=200):
        self.assertEqual(r.status_code, code, r.text)
        return r.json() if r.content else None

    def h(self, who):
        return S.auth(who)

    def staff_of(self, admin, **body):
        name = S.uid("staff")
        uid_ = self.ok(self.c.post("/api/admin/users", headers=self.h(admin), json={
            "username": name, "password": S.PASSWORD, "assignment": "x", "full_name": "Staff " + name, **body}))["id"]
        return uid_, S.user_row(name)


class PersonnelDicing(Base):
    def test_captain_limits_staff_to_crime_types_and_cameras(self):
        uid_, staff = self.staff_of(self.capA, permissions={"view_map": True})
        self.ok(self.c.put(f"/api/admin/users/{uid_}/resource_scopes", headers=self.h(self.capA), json={
            "scopes": {"view_map": {"crime_type": ["THEFT"], "camera": [self.camsA[0]]}}}))
        listed = {u["id"]: u for u in self.ok(self.c.get("/api/admin/users", headers=self.h(self.capA)))}[uid_]
        self.assertEqual(listed["resource_scopes"]["view_map"]["crime_type"], ["THEFT"])
        theft = S.make_incident(self.brgyA, "THEFT")
        assault = S.make_incident(self.brgyA, "ASSAULT")
        seen = {i["id"] for i in self.ok(self.c.get("/api/incidents", headers=self.h(staff)))}
        self.assertIn(theft, seen)
        self.assertNotIn(assault, seen)
        self.assertEqual({c["id"] for c in self.ok(self.c.get("/api/cameras", headers=self.h(staff)))}, {self.camsA[0]})
        self.assertTrue(S.audit_rows("permission_grant.scopes_set", str(uid_)))

    def test_only_own_staff_and_only_own_cameras(self):
        uid_, _ = self.staff_of(self.capA, permissions={"view_map": True})
        body = {"scopes": {"view_map": {"crime_type": ["THEFT"]}}}
        self.assertEqual(self.c.put(f"/api/admin/users/{uid_}/resource_scopes", headers=self.h(self.capB), json=body).status_code, 403)
        self.assertEqual(self.c.put(f"/api/admin/users/{uid_}/resource_scopes", headers=self.h(self.capA), json={
            "scopes": {"view_map": {"camera": [self.camsB[0]]}}}).status_code, 400, "another barangay's camera")
        self.assertEqual(self.c.put(f"/api/admin/users/{uid_}/resource_scopes", headers=self.h(self.capA), json={
            "scopes": {"view_map": {"crime_type": []}}}).status_code, 400, "an empty pick is refused, not stored as 'all'")

    def test_a_limited_admin_cannot_hand_out_more_than_they_hold(self):
        brgy = S.make_barangay(1)[0]
        S.make_station(self.dev, [brgy])
        cap = S.make_user(self.dev, "BARANGAY_ADMIN", barangay_id=brgy)
        conn = S.db()
        try:
            conn.cursor().execute("INSERT INTO permission_grants (id, user_id, permission_key, resource_type, resource_id, granted_by) "
                         "VALUES (?, ?, 'view_map', 'crime_type', 'THEFT', ?)", (S.uid("g"), cap["id"], self.dev["id"]))
            conn.commit()
        finally:
            conn.close()
        uid_, _ = self.staff_of(cap, permissions={"view_map": True})
        url = f"/api/admin/users/{uid_}/resource_scopes"
        self.assertEqual(self.c.put(url, headers=self.h(cap), json={"scopes": {"view_map": {"crime_type": None}}}).status_code, 403)
        self.assertEqual(self.c.put(url, headers=self.h(cap), json={"scopes": {"view_map": {"crime_type": ["ASSAULT"]}}}).status_code, 403)
        self.ok(self.c.put(url, headers=self.h(cap), json={"scopes": {"view_map": {"crime_type": ["THEFT"]}}}))


class Passwords(Base):
    def test_devteam_password_change_needs_eight_characters(self):
        u = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={})
        self.assertEqual(self.c.patch(f"/api/devteam/users/{u['id']}", headers=self.h(self.dev), json={"password": "short"}).status_code, 400)
        self.ok(self.c.patch(f"/api/devteam/users/{u['id']}", headers=self.h(self.dev), json={"password": "a-longer-one"}))
        self.ok(self.c.post("/api/login", json={"username": u["username"], "password": "a-longer-one"}))
        entry = S.audit_rows("user.updated", str(u["id"]))[-1]
        self.assertEqual(json.loads(entry["target_snapshot"])["changes"]["password"], "changed")
        self.assertNotIn("a-longer-one", entry["target_snapshot"])


class AuditFilters(Base):
    def test_filter_by_account_org_category_and_date(self):
        name = S.uid("filt")
        self.ok(self.c.post("/api/admin/users", headers=self.h(self.capA), json={
            "username": name, "password": S.PASSWORD, "assignment": "x", "full_name": "Filter Test"}))
        get = lambda **q: self.ok(self.c.get("/api/devteam/audit_log", headers=self.h(self.dev), params=q))
        mine = get(user_id=self.capA["id"])
        self.assertTrue(mine and all(e["actor_user_id"] == self.capA["id"] or e["target_id"] == str(self.capA["id"]) for e in mine))
        by_brgy = get(barangay_id=self.brgyA, category="user")
        self.assertTrue(any(e["action"] == "user.created" and e["actor_user_id"] == self.capA["id"] for e in by_brgy))
        self.assertTrue(all(e["action"].startswith("user.") for e in by_brgy))
        self.assertFalse(any(e["actor_user_id"] == self.capA["id"] for e in get(barangay_id=self.brgyB)))
        self.assertFalse(any(e["actor_user_id"] == self.capA["id"] for e in get(station_id=self.stB)))
        self.assertTrue(get(date_from="2000-01-01", date_to="2099-12-31"))
        self.assertEqual(get(date_from="2099-01-01"), [])
        self.assertEqual(self.c.get("/api/devteam/audit_log", headers=self.h(self.dev),
                                    params={"date_from": "2026-10-05", "date_to": "2026-10-01"}).status_code, 400)
        self.assertEqual(self.c.get("/api/devteam/audit_log", headers=self.h(self.dev), params={"date_from": "yesterday"}).status_code, 400)
        facets = self.ok(self.c.get("/api/devteam/audit_log/facets", headers=self.h(self.dev)))
        self.assertIn("user", facets["categories"])
        self.assertIn(self.capA["id"], {a["id"] for a in facets["actors"]})
        self.assertEqual(self.c.get("/api/devteam/audit_log/facets", headers=self.h(self.capA)).status_code, 403)

    def test_account_filter_lists_a_renamed_account_by_its_current_name(self):
        u = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgyA, permissions={})
        self.ok(self.c.post("/api/login", json={"username": u["username"], "password": S.PASSWORD}))
        self.ok(self.c.patch(f"/api/devteam/users/{u['id']}", headers=self.h(self.dev), json={"username": "zz_" + u["username"]}))
        actors = {a["id"]: a for a in self.ok(self.c.get("/api/devteam/audit_log/facets", headers=self.h(self.dev)))["actors"]}
        self.assertEqual(actors[u["id"]]["username"], "zz_" + u["username"])


class ReportWording(Base):
    def _clip(self, iid, name, when, duration):
        conn = S.db()
        try:
            conn.cursor().execute("INSERT INTO video_records (id, filename, file_path, recorded_at, duration, type, associated_incident_id, "
                         "barangay_id, sha256) VALUES (?, ?, ?, ?, ?, 'CLIP', ?, ?, 'ab' || ?)",
                         (S.uid("rec"), name, name, when, duration, iid, self.brgyA, S.uid("h")))
            conn.commit()
        finally:
            conn.close()

    def test_draft_names_clips_and_leaves_out_hashes_files_and_lighting(self):
        iid = S.ai_alert(self.brgyA, self.camsA[0], event="ASSAULT", conf=0.91)
        self._clip(iid, "EXTRACT_20261001_034343_demo_clip_1.mp4", "2026-10-01 03:44:00", "3.0s")
        self._clip(iid, "demo_clip_1.mp4", "2026-10-01 03:43:00", "00:08")
        draft = self.ok(self.c.get(f"/api/incidents/{iid}/report_draft", headers=self.h(self.pnpA)))["ai_draft"]
        text = json.dumps(draft)
        for gone in ("SHA", "sha256", "demo_clip", "EXTRACT_", "lit (", "lighting", "brightness", "raised by the"):
            self.assertNotIn(gone, text, gone)
        self.assertEqual([c["label"] for c in draft["evidence"]["clips"]], ["Assault 1", "Assault 2"], "numbered in recording order")
        self.assertIn("Assault 1 (8 seconds) and Assault 2 (3 seconds)", draft["narrative"])
        self.assertIn("91%", draft["narrative"])
        recs = {r["filename"]: r for r in self.ok(self.c.get("/api/records", headers=self.h(self.pnpA)))}
        self.assertEqual(recs["demo_clip_1.mp4"]["label"], "Assault 1")
        self.assertEqual(recs["EXTRACT_20261001_034343_demo_clip_1.mp4"]["label"], "Assault 2")


class SharingAReport(Base):
    def _confirmed_incident(self, barangay):
        iid = S.make_incident(barangay, "ROBBERY")
        self.ok(self.c.post(f"/api/incidents/{iid}/confirm-and-report", headers=self.h(self.pnpA), json={
            "status": "Confirmed", "report_details": {
                "reporting_officer": "PO2 Cruz", "badge_number": "B-77", "incident_type": "ROBBERY",
                "narrative": "Suspect Juan X grabbed a bag; victim Maria Y treated on scene.",
                "victim_details": "Maria Y, 34", "action_taken": "Pursued north", "disposition": "Under investigation"}}))
        return iid

    def _accepted_request(self):
        rid = self.ok(self.c.post("/api/report_requests", headers=self.h(self.capA), json={
            "description": "Blotter for the market robbery", "report_type": "blotter_copy", "purpose": "records"}))["id"]
        self.ok(self.c.post(f"/api/report_requests/{rid}/accept", headers=self.h(self.pnpA), json={}))
        return rid

    def test_police_share_chosen_fields_with_an_edited_summary(self):
        iid = self._confirmed_incident(self.brgyA)
        rid = self._accepted_request()
        options = self.ok(self.c.get(f"/api/report_requests/{rid}/shareable", headers=self.h(self.pnpA)))
        self.assertIn(iid, {i["id"] for i in options["incidents"]})
        preview = {f["key"]: f["value"] for f in self.ok(self.c.get(
            f"/api/report_requests/{rid}/share_preview", headers=self.h(self.pnpA), params={"incident_id": iid}))["fields"]}
        self.assertEqual(preview["victim_details"], "Maria Y, 34")
        self.ok(self.c.post(f"/api/report_requests/{rid}/fulfill", headers=self.h(self.pnpA), json={
            "incident_id": iid, "share_fields": ["case_id", "incident_type", "narrative", "disposition"],
            "summary": "A bag was taken at the market; the case is under investigation."}))
        got = {r["id"]: r for r in self.ok(self.c.get("/api/report_requests", headers=self.h(self.capA)))}[rid]
        shared = {f["key"]: f["value"] for f in got["shared_report"]["fields"]}
        self.assertEqual(set(shared), {"case_id", "incident_type", "narrative", "disposition"})
        self.assertEqual(shared["narrative"], "A bag was taken at the market; the case is under investigation.")
        text = json.dumps(got)
        for private in ("Maria Y", "Juan X", "B-77", "PO2 Cruz"):
            self.assertNotIn(private, text, private)
        self.assertTrue(got["shared_report"]["summary_edited"])
        # The filed report itself is untouched.
        report = self.ok(self.c.get(f"/api/incidents/{iid}/report_draft", headers=self.h(self.pnpA)))["report"]
        self.assertIn("Juan X", report["report_body"]["narrative"])

    def test_sharing_is_checked(self):
        rid = self._accepted_request()
        other = self._confirmed_incident(self.brgyA)
        foreign = S.make_incident(self.brgyB, "ROBBERY")
        url = f"/api/report_requests/{rid}/fulfill"
        self.assertEqual(self.c.post(url, headers=self.h(self.pnpA), json={"incident_id": foreign, "share_fields": ["case_id"]}).status_code, 404)
        self.assertEqual(self.c.post(url, headers=self.h(self.pnpA), json={"incident_id": other, "share_fields": ["password"]}).status_code, 400)
        self.assertEqual(self.c.post(url, headers=self.h(self.pnpA), json={"incident_id": other, "share_fields": []}).status_code, 400)
        self.assertEqual(self.c.post(url, headers=self.h(self.pnpB), json={"note": "x"}).status_code, 403)
        self.assertEqual(self.c.post(url, headers=self.h(self.pnpA), json={}).status_code, 400, "something must be handed over")

    def test_attached_files_reach_only_the_two_parties(self):
        rid = self._accepted_request()
        up = lambda who, name, data=b"%PDF-1.4 blotter": self.c.post(
            f"/api/report_requests/{rid}/files", headers=self.h(who), files={"file": (name, io.BytesIO(data), "application/pdf")})
        self.assertEqual(up(self.pnpA, "blotter.exe").status_code, 400)
        self.assertEqual(up(self.pnpA, "empty.pdf", b"").status_code, 400)
        self.assertEqual(up(self.pnpB, "blotter.pdf").status_code, 403)
        self.assertEqual(up(self.capA, "blotter.pdf").status_code, 403, "barangays don't attach to their own request")
        fid = self.ok(up(self.pnpA, "blotter.pdf"))["id"]
        self.ok(self.c.post(f"/api/report_requests/{rid}/fulfill", headers=self.h(self.pnpA), json={}), 200)
        got = {r["id"]: r for r in self.ok(self.c.get("/api/report_requests", headers=self.h(self.capA)))}[rid]
        self.assertEqual([f["original_name"] for f in got["files"]], ["blotter.pdf"])
        dl = self.c.get(f"/api/report_requests/{rid}/files/{fid}", headers=self.h(self.capA))
        self.assertEqual((dl.status_code, dl.content), (200, b"%PDF-1.4 blotter"))
        self.assertEqual(self.c.get(f"/api/report_requests/{rid}/files/{fid}", headers=self.h(self.pnpA)).status_code, 200)
        for outsider in (self.capB, self.pnpB):
            self.assertEqual(self.c.get(f"/api/report_requests/{rid}/files/{fid}", headers=self.h(outsider)).status_code, 404)
        self.assertEqual(self.c.get(f"/api/report_requests/{rid}/files/{fid}").status_code, 401)
        self.assertEqual(self.c.delete(f"/api/report_requests/{rid}/files/{fid}", headers=self.h(self.pnpA)).status_code, 400,
                         "a handed-over file stays")
        self.assertEqual(up(self.pnpA, "late.pdf").status_code, 400)
        self.assertTrue(S.audit_rows("report_request.file_attached", rid))


if __name__ == "__main__":
    unittest.main()

"""Audit trail coverage and DevTeam's control over accounts and stations."""
import io
import json
import unittest

import _support as S


def snap(row):
    return json.loads(row["target_snapshot"]) if row and row.get("target_snapshot") else {}


class AuditTrail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        cls.dev = S.make_devteam()
        cls.brgy, cls.cams = S.make_barangay(1)
        cls.station = S.make_station(cls.dev, [cls.brgy])
        cls.captain = S.make_user(cls.dev, "BARANGAY_ADMIN", barangay_id=cls.brgy)

    def test_login_and_failed_login(self):
        self.c.post("/api/login", json={"username": self.dev["username"], "password": "wrong"})
        self.assertTrue(S.audit_rows("user.login_failed", self.dev["id"]))
        r = self.c.post("/api/login", json={"username": self.dev["username"], "password": S.PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(S.audit_rows("user.login", self.dev["id"]))

    def test_staff_created_by_captain_is_attributed(self):
        r = self.c.post("/api/admin/users", headers=S.auth(self.captain), json={"username": S.uid("tanod"), "password": S.PASSWORD, "assignment": "gate", "full_name": "Tanod Test"})
        self.assertEqual(r.status_code, 200, r.text)
        row = S.audit_rows("user.created", r.json()["id"])[0]
        self.assertEqual(row["actor_username"], self.captain["username"])

    def test_confirm_is_audited_with_before_and_after(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy, permissions={"confirm_dismiss_alerts": True})
        inc = S.make_incident(self.brgy)
        self.c.patch(f"/api/incidents/{inc}/status", headers=S.auth(staff), json={"status": "Confirmed"})
        row = S.audit_rows("incident.confirmed", inc)[0]
        self.assertEqual(row["actor_username"], staff["username"])
        self.assertEqual(snap(row)["from"], "Active")

    def test_permission_change_records_from_and_to(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        self.c.patch(f"/api/admin/users/{staff['id']}/permissions", headers=S.auth(self.dev),
                     json={"permissions": {"view_map": True}})
        self.assertEqual(snap(S.audit_rows("user.permissions_updated", staff["id"])[0])["to"], ["view_map"])

    def test_password_reset_never_stores_the_password(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        r = self.c.post(f"/api/admin/users/{staff['id']}/reset_password", headers=S.auth(self.dev))
        row = S.audit_rows("user.password_reset", staff["id"])[0]
        self.assertNotIn(r.json()["new_password"], row["target_snapshot"] or "")

    def test_denied_attempt_is_recorded(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        r = self.c.post("/api/cameras", headers=S.auth(staff), json={"name": "x", "url": "x", "barangay_id": self.brgy})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(any(x["actor_username"] == staff["username"] for x in S.audit_rows("denied POST /api/cameras")))

    def test_catch_all_records_endpoints_without_their_own_entry(self):
        inc = S.make_incident(self.brgy)
        self.assertEqual(self.c.patch(f"/api/incidents/{inc}/archive", headers=S.auth(self.captain)).status_code, 200)
        self.assertTrue(S.audit_rows("PATCH /api/incidents/{incident_id}/archive", inc))

    def test_detailed_entry_suppresses_the_generic_one(self):
        S.make_station(self.dev)
        self.assertFalse(S.audit_rows("POST /api/devteam/stations"))

    def test_reads_are_not_audited(self):
        conn = S.db(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM audit_log"); before = cur.fetchone()["n"]; conn.close()
        self.c.get("/api/incidents", headers=S.auth(self.captain))
        conn = S.db(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM audit_log"); after = cur.fetchone()["n"]; conn.close()
        self.assertEqual(before, after)

    def test_per_user_activity_and_search(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        rows = self.c.get(f"/api/devteam/audit_log?user_id={staff['id']}", headers=S.auth(self.dev)).json()
        self.assertIn("user.created", {r["action"] for r in rows})
        rows = self.c.get(f"/api/devteam/audit_log?q={staff['username']}", headers=S.auth(self.dev)).json()
        self.assertTrue(rows)


class DevteamControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        cls.dev = S.make_devteam()
        cls.brgy, _ = S.make_barangay(0)
        cls.station = S.make_station(cls.dev, [cls.brgy])
        cls.captain = S.make_user(cls.dev, "BARANGAY_ADMIN", barangay_id=cls.brgy)

    def edit(self, user, body):
        return self.c.patch(f"/api/devteam/users/{user['id']}", headers=S.auth(self.dev), json=body)

    def test_station_edit_needs_reason_and_password_and_logs_changes(self):
        body = {"name": S.uid("Renamed"), "station_type": "Police Sub-Station", "commander": "PMaj Test"}
        url = f"/api/devteam/stations/{self.station}"
        h = S.auth(self.dev)
        self.assertEqual(self.c.patch(url, headers=h, json={**body, "reason": "short", "confirm_password": S.PASSWORD}).status_code, 400)
        self.assertEqual(self.c.patch(url, headers=h, json={**body, "reason": S.REASON, "confirm_password": "nope"}).status_code, 403)
        r = self.c.patch(url, headers=h, json={**body, "reason": S.REASON, "confirm_password": S.PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(set(snap(S.audit_rows("station.updated", self.station)[0])["changes"]), {"name", "commander"})
        self.assertEqual(self.c.patch(url, headers=h, json=body).json()["status"], "unchanged")

    def test_supervisor_and_custom_role_editable(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        role = self.c.post("/api/devteam/custom_roles", headers=S.auth(self.dev),
                           json={"name": S.uid("Role"), "permissions": {"view_map": True}}).json()["id"]
        self.assertEqual(self.edit(staff, {"parent_admin_id": self.captain["id"], "custom_role_id": role}).status_code, 200)
        row = S.user_row(staff["username"])
        self.assertEqual((row["parent_admin_id"], row["custom_role_id"]), (self.captain["id"], role))
        self.assertEqual(self.edit(staff, {"parent_admin_id": None}).status_code, 200)
        self.assertIsNone(S.user_row(staff["username"])["parent_admin_id"])
        self.assertEqual(self.edit(staff, {"parent_admin_id": self.dev["id"]}).status_code, 400)

    def test_side_switch_and_devteam_promotion(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        self.assertEqual(self.edit(staff, {"role": "PNP_OFFICER"}).status_code, 400)
        self.assertEqual(self.edit(staff, {"role": "PNP_OFFICER", "station_id": self.station}).status_code, 200)
        row = S.user_row(staff["username"])
        self.assertEqual((row["role"], row["barangay_id"], row["station_id"]), ("PNP_OFFICER", None, self.station))
        self.assertEqual(self.edit(staff, {"role": "DEVTEAM"}).status_code, 400)

    def test_identity_files_devteam_only(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        files = {"id_document": ("id.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}
        r = self.c.post(f"/api/devteam/users/{staff['id']}/identity_files", headers=S.auth(self.dev), files=files)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(S.user_row(staff["username"])["verification_status"], "pending")
        r = self.c.post(f"/api/devteam/users/{staff['id']}/identity_files", headers=S.auth(self.captain),
                        files={"face_photo": ("f.png", io.BytesIO(b"x"), "image/png")})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()

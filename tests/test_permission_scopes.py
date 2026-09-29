"""Permission dicing: every permission narrowed by camera, crime type or
channel, enforced server-side (not just hidden in the UI)."""
import unittest

import _support as S


class PermissionScopes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        cls.dev = S.make_devteam()
        cls.brgy, cls.cams = S.make_barangay(cameras=2)
        cls.station = S.make_station(cls.dev, [cls.brgy])
        cls.assault = S.make_incident(cls.brgy, "ASSAULT", cls.cams[0])
        cls.robbery_a = S.make_incident(cls.brgy, "ROBBERY", cls.cams[1])
        cls.robbery_b = S.make_incident(cls.brgy, "ROBBERY", cls.cams[0])

    def officer(self, perms, scopes):
        return S.make_user(self.dev, "PNP_OFFICER", station_id=self.station, permissions=perms, resource_scopes=scopes)

    def ids(self, user, purpose="history"):
        r = self.c.get(f"/api/incidents?purpose={purpose}", headers=S.auth(user))
        self.assertEqual(r.status_code, 200, r.text)
        return {i["id"] for i in r.json()} & {self.assault, self.robbery_a, self.robbery_b}

    def test_history_limited_to_crime_type(self):
        o = self.officer({"view_history": True}, {"view_history": {"crime_type": ["ASSAULT"]}})
        self.assertEqual(self.ids(o), {self.assault})

    def test_asking_for_map_cannot_widen_history_scope(self):
        o = self.officer({"view_history": True, "view_map": False}, {"view_history": {"crime_type": ["ASSAULT"]}})
        self.assertEqual(self.ids(o, "map"), {self.assault})

    def test_confirm_limited_to_crime_type(self):
        o = self.officer({"confirm_dismiss_alerts": True}, {"confirm_dismiss_alerts": {"crime_type": ["ROBBERY"]}})
        r = self.c.patch(f"/api/incidents/{self.assault}/status", headers=S.auth(o), json={"status": "Dismissed"})
        self.assertEqual(r.status_code, 404)
        inc = S.make_incident(self.brgy, "ROBBERY")
        r = self.c.patch(f"/api/incidents/{inc}/status", headers=S.auth(o), json={"status": "Dismissed"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_cannot_retype_into_a_type_outside_scope(self):
        o = self.officer({"confirm_dismiss_alerts": True}, {"confirm_dismiss_alerts": {"crime_type": ["ROBBERY"]}})
        inc = S.make_incident(self.brgy, "ROBBERY")
        r = self.c.post(f"/api/incidents/{inc}/confirm-and-report", headers=S.auth(o), json={
            "status": "Confirmed", "report_details": {"incident_type": "ASSAULT", "reporting_officer": "x"}})
        self.assertEqual(r.status_code, 403)

    def test_map_limited_by_camera_and_type(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy, permissions={"view_map": True},
                            resource_scopes={"view_map": {"camera": [self.cams[0]], "crime_type": ["ROBBERY"]}})
        self.assertEqual(self.ids(staff, "map"), {self.robbery_b})
        cams = {c["id"] for c in self.c.get("/api/cameras", headers=S.auth(staff)).json()}
        self.assertEqual(cams, {self.cams[0]})

    def test_records_no_incident_scope(self):
        o = self.officer({"view_records": True}, {"view_records": {"crime_type": ["NO_INCIDENT"]}})
        conn = S.db()
        cur = conn.cursor()
        linked, loose = S.uid("rec"), S.uid("rec")
        for rid, inc in ((linked, self.assault), (loose, None)):
            cur.execute("INSERT INTO video_records (id, filename, file_path, recorded_at, type, associated_incident_id, barangay_id) "
                        "VALUES (?, 'a.mp4', 'x', '2026-09-30', 'CLIP', ?, ?)", (rid, inc, self.brgy))
        conn.commit(); conn.close()
        got = {r["id"] for r in self.c.get("/api/records", headers=S.auth(o)).json()}
        self.assertIn(loose, got)
        self.assertNotIn(linked, got)
        r = self.c.patch(f"/api/records/{linked}/notes", headers=S.auth(o), json={"notes": "x"})
        self.assertEqual(r.status_code, 404)

    def test_notify_channel_scope(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy, permissions={"manage_notify_targets": True},
                            resource_scopes={"manage_notify_targets": {"channel": ["sms"]}})
        h = S.auth(staff)
        r = self.c.post("/api/notify_targets", headers=h, json={"barangay_id": self.brgy, "channel": "telegram", "destination": "1"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/api/notify_targets", headers=h, json={"barangay_id": self.brgy, "channel": "sms", "destination": "0917"})
        self.assertEqual(r.status_code, 200, r.text)
        self.c.post("/api/notify_targets", headers=S.auth(self.dev), json={"barangay_id": self.brgy, "channel": "telegram", "destination": "2"})
        chans = {t["channel"] for t in self.c.get("/api/notify_targets", headers=h).json()}
        self.assertEqual(chans, {"sms"})

    def test_scope_validation(self):
        o = self.officer({"view_history": True}, None)
        url = f"/api/devteam/users/{o['id']}/resource_scopes"
        h = S.auth(self.dev)
        for bad in ({"view_history": {"crime_type": ["MURDER"]}},   # unknown type
                    {"view_history": {"camera": [self.cams[0]]}},   # wrong dimension
                    {"view_history": {"crime_type": []}}):          # empty = ambiguous
            self.assertEqual(self.c.put(url, headers=h, json={"scopes": bad}).status_code, 400, bad)
        self.assertEqual(self.c.put(url, headers=h, json={"scopes": {"view_history": {"crime_type": None}}}).status_code, 200)

    def test_barangay_cannot_be_scoped_on_police_only_key(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy)
        r = self.c.put(f"/api/devteam/users/{staff['id']}/resource_scopes", headers=S.auth(self.dev),
                       json={"scopes": {"view_history": {"crime_type": ["ASSAULT"]}}})
        self.assertIn(r.status_code, (400, 403))

    def test_custom_role_carries_crime_types_and_form_overrides_it(self):
        h = S.auth(self.dev)
        r = self.c.post("/api/devteam/custom_roles", headers=h, json={
            "name": S.uid("Role"), "permissions": {"view_history": True},
            "scopes": {"view_history": {"crime_type": ["ASSAULT", "ROBBERY"]}}})
        self.assertEqual(r.status_code, 200, r.text)
        role = r.json()["id"]
        o = S.make_user(self.dev, "PNP_OFFICER", station_id=self.station, custom_role_id=role)
        self.assertEqual(self.ids(o), {self.assault, self.robbery_a, self.robbery_b})
        o2 = S.make_user(self.dev, "PNP_OFFICER", station_id=self.station, custom_role_id=role,
                         permissions={"view_history": True}, resource_scopes={"view_history": {"crime_type": ["ASSAULT"]}})
        self.assertEqual(self.ids(o2), {self.assault})

    def test_barangay_never_sees_history_archive(self):
        staff = S.make_user(self.dev, "BARANGAY_STAFF", barangay_id=self.brgy, permissions={"view_map": True})
        closed = S.make_incident(self.brgy, "ASSAULT", status="Confirmed")
        got = {i["id"] for i in self.c.get("/api/incidents?purpose=history", headers=S.auth(staff)).json()}
        self.assertNotIn(closed, got)


if __name__ == "__main__":
    unittest.main()

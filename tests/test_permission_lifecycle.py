"""Access follows the grant, both ways, on the same session.

For every permission: an account without it gets nothing from the endpoints
that permission guards; granting it opens exactly that, with no re-login
(require_auth re-reads the account on every request); revoking it closes it
again. Every change is in the audit log with who made it and the before and
after. Also covers the leaks this suite first found: stream URLs handed to
accounts that can't manage cameras, and officer reports readable (and
writable) by an officer with no permissions at all.
"""
import json
import unittest

import _support as S


class PermissionLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        cls.dev = S.make_devteam()
        cls.brgy, cls.cams = S.make_barangay(2)
        cls.other_brgy, cls.other_cams = S.make_barangay(1)
        cls.station = S.make_station(cls.dev, [cls.brgy])
        S.make_station(cls.dev, [cls.other_brgy])  # a different station's area
        cls.captain = S.make_user(cls.dev, "BARANGAY_ADMIN", barangay_id=cls.brgy)
        cls.chief = S.make_user(cls.dev, "PNP_ADMIN", station_id=cls.station)
        cls.active = S.make_incident(cls.brgy, camera_id=cls.cams[0])
        cls.closed = S.make_incident(cls.brgy, camera_id=cls.cams[0], status="Confirmed")
        cls.elsewhere = S.make_incident(cls.other_brgy, camera_id=cls.other_cams[0])

    def fresh(self, role):
        if role == "BARANGAY_STAFF":
            return S.make_user(self.dev, role, barangay_id=self.brgy, permissions={})
        return S.make_user(self.dev, role, station_id=self.station, permissions={})

    def set_perms(self, actor, target, perms):
        r = self.c.patch(f"/api/admin/users/{target['id']}/permissions", headers=S.auth(actor),
                         json={"permissions": {k: True for k in perms}})
        self.assertEqual(r.status_code, 200, r.text)
        row = S.audit_rows("user.permissions_updated", target["id"])[0]
        self.assertEqual(row["actor_username"], actor["username"])
        self.assertEqual(json.loads(row["target_snapshot"])["to"], sorted(perms))

    def ids(self, r):
        self.assertEqual(r.status_code, 200, r.text)
        return {x["id"] for x in r.json()}

    # -- nothing without a grant -------------------------------------------

    def test_no_permissions_means_no_data(self):
        for role in ("BARANGAY_STAFF", "PNP_OFFICER"):
            with self.subTest(role=role):
                u = self.fresh(role)
                h = S.auth(u)
                self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 403)
                self.assertEqual(self.c.get("/api/incidents?purpose=history", headers=h).status_code, 403)
                self.assertEqual(self.c.get("/api/records", headers=h).status_code, 403)
                self.assertEqual(self.c.get("/api/notify_targets", headers=h).status_code, 403)
                self.assertEqual(self.c.patch(f"/api/incidents/{self.active}/status", headers=h,
                                              json={"status": "Confirmed"}).status_code, 403)
                self.assertIn(self.c.post("/api/cameras", headers=h, json={"name": "x", "url": "rtsp://x",
                              "barangay_id": self.brgy}).status_code, (403,))
                # Camera names stay visible (Live Monitor), stream URLs don't.
                cams = self.c.get("/api/cameras", headers=h).json()
                self.assertTrue(cams)
                self.assertTrue(all(cam["url"] is None for cam in cams))

    def test_officer_without_permissions_cannot_read_or_file_reports(self):
        u = self.fresh("PNP_OFFICER")
        h = S.auth(u)
        self.assertEqual(self.c.get(f"/api/incidents/{self.active}/reports", headers=h).status_code, 404)
        self.assertEqual(self.c.get(f"/api/incidents/{self.active}/report_draft", headers=h).status_code, 404)
        r = self.c.post(f"/api/incidents/{self.active}/reports", headers=h, json={"narrative": "x"})
        self.assertEqual(r.status_code, 403)
        self.set_perms(self.dev, u, ["view_history"])
        self.assertEqual(self.c.get(f"/api/incidents/{self.active}/reports", headers=h).status_code, 200)
        self.assertEqual(self.c.post(f"/api/incidents/{self.active}/reports", headers=h,
                                     json={"narrative": "x"}).status_code, 403)
        self.set_perms(self.dev, u, ["view_history", "confirm_dismiss_alerts"])
        self.assertEqual(self.c.post(f"/api/incidents/{self.active}/reports", headers=h,
                                     json={"narrative": "x"}).status_code, 200)

    # -- grant opens it, revoke closes it, same token ----------------------

    def test_view_map_grant_and_revoke(self):
        for role in ("BARANGAY_STAFF", "PNP_OFFICER"):
            with self.subTest(role=role):
                u = self.fresh(role)
                h = S.auth(u)
                self.set_perms(self.dev, u, ["view_map"])
                seen = self.ids(self.c.get("/api/incidents", headers=h))
                self.assertIn(self.active, seen)
                self.assertNotIn(self.elsewhere, seen, "another jurisdiction's incident leaked")
                self.set_perms(self.dev, u, [])
                self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 403)

    def test_view_history_grant_and_revoke(self):
        u = self.fresh("PNP_OFFICER")
        h = S.auth(u)
        self.set_perms(self.chief, u, ["view_history"])
        self.assertIn(self.closed, self.ids(self.c.get("/api/incidents?purpose=history", headers=h)))
        self.set_perms(self.chief, u, [])
        self.assertEqual(self.c.get("/api/incidents?purpose=history", headers=h).status_code, 403)

    def test_view_records_grant_and_revoke(self):
        u = self.fresh("PNP_OFFICER")
        h = S.auth(u)
        self.set_perms(self.dev, u, ["view_records"])
        self.assertEqual(self.c.get("/api/records", headers=h).status_code, 200)
        self.set_perms(self.dev, u, [])
        self.assertEqual(self.c.get("/api/records", headers=h).status_code, 403)

    def test_confirm_grant_and_revoke(self):
        u = self.fresh("BARANGAY_STAFF")
        h = S.auth(u)
        inc = S.make_incident(self.brgy)
        self.set_perms(self.captain, u, ["confirm_dismiss_alerts"])
        self.assertEqual(self.c.patch(f"/api/incidents/{inc}/status", headers=h, json={"status": "Confirmed"}).status_code, 200)
        self.set_perms(self.captain, u, [])
        inc2 = S.make_incident(self.brgy)
        self.assertEqual(self.c.patch(f"/api/incidents/{inc2}/status", headers=h, json={"status": "Confirmed"}).status_code, 403)

    def test_manage_cameras_grant_reveals_urls_and_revoke_hides_them(self):
        u = self.fresh("BARANGAY_STAFF")
        h = S.auth(u)
        self.set_perms(self.captain, u, ["manage_cameras"])
        r = self.c.post("/api/cameras", headers=h, json={"name": S.uid("cam"), "url": "rtsp://u:p@10.0.0.9/s", "barangay_id": self.brgy})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(any(cam["url"] for cam in self.c.get("/api/cameras", headers=h).json()))
        self.set_perms(self.captain, u, [])
        self.assertTrue(all(cam["url"] is None for cam in self.c.get("/api/cameras", headers=h).json()))
        self.assertEqual(self.c.post("/api/cameras", headers=h, json={"name": "x", "url": "rtsp://x", "barangay_id": self.brgy}).status_code, 403)

    def test_notify_targets_grant_and_revoke(self):
        u = self.fresh("PNP_OFFICER")
        h = S.auth(u)
        self.set_perms(self.chief, u, ["manage_notify_targets"])
        self.assertEqual(self.c.get("/api/notify_targets", headers=h).status_code, 200)
        self.set_perms(self.chief, u, [])
        self.assertEqual(self.c.get("/api/notify_targets", headers=h).status_code, 403)

    # -- who may grant what ------------------------------------------------

    def test_admin_cannot_touch_another_admins_staff(self):
        other_captain = S.make_user(self.dev, "BARANGAY_ADMIN", barangay_id=self.other_brgy)
        u = self.fresh("BARANGAY_STAFF")
        r = self.c.patch(f"/api/admin/users/{u['id']}/permissions", headers=S.auth(other_captain),
                         json={"permissions": {"view_map": True}})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(any(x["actor_username"] == other_captain["username"]
                            for x in S.audit_rows("denied PATCH /api/admin/users/{user_id}/permissions")))
        self.assertEqual(self.c.get("/api/incidents", headers=S.auth(u)).status_code, 403)

    def test_keys_a_side_cannot_hold_are_refused_not_silently_stored(self):
        u = self.fresh("BARANGAY_STAFF")
        r = self.c.patch(f"/api/admin/users/{u['id']}/permissions", headers=S.auth(self.dev),
                         json={"permissions": {"view_history": True}})
        self.assertEqual(r.status_code, 400)
        officer = self.fresh("PNP_OFFICER")
        r = self.c.patch(f"/api/admin/users/{officer['id']}/permissions", headers=S.auth(self.dev),
                         json={"permissions": {"manage_cameras": True}})
        self.assertEqual(r.status_code, 400)
        r = self.c.patch(f"/api/admin/users/{officer['id']}/permissions", headers=S.auth(self.dev),
                         json={"permissions": {"launch_missiles": True}})
        self.assertEqual(r.status_code, 400)

    def test_devteam_override_takes_automatic_access_away_from_an_admin(self):
        captain = self.captain
        h = S.auth(captain)
        self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 200)
        r = self.c.post(f"/api/devteam/users/{captain['id']}/override_permissions", headers=S.auth(self.dev),
                        json={"confirm_password": S.PASSWORD, "permissions": {"confirm_dismiss_alerts": True}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 403)
        self.assertTrue(S.audit_rows("user.permissions_overridden", captain["id"]))
        r = self.c.post(f"/api/devteam/users/{captain['id']}/override_permissions", headers=S.auth(self.dev),
                        json={"confirm_password": S.PASSWORD, "permissions": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 200)
        self.assertTrue(S.audit_rows("user.permissions_reset_to_automatic", captain["id"]))

    def test_override_needs_the_devteam_password(self):
        r = self.c.post(f"/api/devteam/users/{self.chief['id']}/override_permissions", headers=S.auth(self.dev),
                        json={"confirm_password": "wrong", "permissions": {}})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get("/api/incidents", headers=S.auth(self.chief)).status_code, 200)

    def test_deleted_account_loses_everything_on_its_existing_token(self):
        u = self.fresh("PNP_OFFICER")
        h = S.auth(u)
        self.set_perms(self.dev, u, ["view_map"])
        self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 200)
        self.assertEqual(self.c.delete(f"/api/devteam/users/{u['id']}", headers=S.auth(self.dev)).status_code, 200)
        self.assertEqual(self.c.get("/api/incidents", headers=h).status_code, 401)


if __name__ == "__main__":
    unittest.main()

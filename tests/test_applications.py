"""Self-signup applications: pending -> approved | rejected, rejected -> pending.

Covers the state machine (only a pending application can be decided), the
written reason a rejection needs, reopening (reason + DevTeam password), the
audit trail of every step, and what "rejected" has to mean everywhere else:
out of every jurisdiction, no new accounts, no registering over it, no new
applicants for it. Also the admin-seat rule both of these depend on: only an
active, non-rejected admin holds a barangay's or station's one admin seat.
"""
import json
import unittest

import _support as S

PROFILE = {"full_name": "Juan Dela Cruz", "birthdate": "1980-01-01", "home_address": "Purok 1", "position": "Punong Barangay"}


def snap(row):
    return json.loads(row["target_snapshot"]) if row and row.get("target_snapshot") else {}


class Applications(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = S.client()
        cls.dev = S.make_devteam()
        cls.h = S.auth(cls.dev)
        cls.station = S.make_station(cls.dev)
        # The signup endpoint is rate limited per client; the tests share one.
        S.B.limiter.enabled = False

    @classmethod
    def tearDownClass(cls):
        S.B.limiter.enabled = True

    def signup_barangay(self, barangay_id=None):
        barangay_id = barangay_id or S.uid("brgy").replace("_", "-")  # a slug, as signup stores it
        name = S.uid("cap")
        r = self.c.post("/api/signup", json={"username": name, "password": S.PASSWORD, "role": "BARANGAY_ADMIN",
                                             "barangay_id": barangay_id, "assignment": "hall", **PROFILE})
        return r, barangay_id, name

    def signup_pnp(self, station=None):
        name = S.uid("chief")
        r = self.c.post("/api/signup", json={"username": name, "password": S.PASSWORD, "role": "PNP_ADMIN",
                                             "station_id": station or self.station, "assignment": "desk",
                                             **PROFILE, "position": "Chief of Police"})
        return r, name

    def login(self, username):
        return self.c.post("/api/login", json={"username": username, "password": S.PASSWORD})

    def reject(self, bid, reason="ID photo does not match the applicant"):
        return self.c.post(f"/api/devteam/locations/{bid}/reject", headers=self.h, json={"reason": reason})

    def reopen(self, url, reason="Applicant sent a corrected government ID by email", password=S.PASSWORD):
        return self.c.post(url, headers=self.h, json={"reason": reason, "confirm_password": password})

    # -- barangay applications --------------------------------------------

    def test_approve_with_station_puts_it_in_that_jurisdiction(self):
        r, bid, name = self.signup_barangay()
        self.assertEqual(r.status_code, 200, r.text)
        submitted = S.audit_rows("user.signup_submitted", r.json()["id"])[0]
        self.assertEqual(submitted["actor_username"], name)
        self.assertTrue(snap(submitted)["new_barangay"])
        self.assertEqual(self.login(name).status_code, 403)
        r = self.c.post(f"/api/devteam/locations/{bid}/approve", headers=self.h, json={"station_id": self.station})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["covered_by"], [self.station])
        self.assertEqual(self.login(name).status_code, 200)
        self.assertEqual(snap(S.audit_rows("barangay.approved", bid)[0])["station_id"], self.station)

    def test_a_decided_application_cannot_be_decided_again(self):
        _, bid, _ = self.signup_barangay()
        self.assertEqual(self.c.post(f"/api/devteam/locations/{bid}/approve", headers=self.h, json={}).status_code, 200)
        self.assertEqual(self.reject(bid).status_code, 409)
        self.assertEqual(self.c.post(f"/api/devteam/locations/{bid}/approve", headers=self.h, json={}).status_code, 409)
        _, bid2, _ = self.signup_barangay()
        self.assertEqual(self.reject(bid2).status_code, 200)
        self.assertEqual(self.c.post(f"/api/devteam/locations/{bid2}/approve", headers=self.h, json={}).status_code, 409)

    def test_rejection_needs_a_reason_and_records_it(self):
        _, bid, name = self.signup_barangay()
        self.assertEqual(self.reject(bid, reason="no").status_code, 400)
        self.assertEqual(self.reject(bid).status_code, 200)
        row = next(x for x in self.c.get("/api/devteam/locations?status=rejected", headers=self.h).json() if x["id"] == bid)
        self.assertEqual(row["decision_reason"], "ID photo does not match the applicant")
        self.assertEqual(row["decided_by_username"], self.dev["username"])
        self.assertEqual(row["requester_signup_status"], "rejected")
        self.assertIn("not approved", self.login(name).json()["detail"])
        self.assertNotIn(bid, [x["id"] for x in self.c.get("/api/devteam/locations?status=pending", headers=self.h).json()])

    def test_rejected_barangay_is_out_of_circulation(self):
        # Hyphenated so the Stations form's slug of the name is this same id.
        _, bid, _ = self.signup_barangay(S.uid("brgy").replace("_", "-"))
        self.reject(bid)
        r = self.c.put(f"/api/devteam/stations/{self.station}/jurisdiction", headers=self.h, json={"barangay_ids": [bid]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("rejected", r.json()["detail"])
        r = self.c.post("/api/devteam/users", headers=self.h, json={"username": S.uid("x"), "password": S.PASSWORD,
                        "role": "BARANGAY_STAFF", "assignment": "t", "full_name": "X", "barangay_id": bid})
        self.assertEqual(r.status_code, 409)
        r = self.c.post(f"/api/devteam/stations/{self.station}/barangays", headers=self.h, json={
            "name": bid, "barangay_id": bid, "city_municipality": "Ormoc", "reason": S.REASON, "confirm_password": S.PASSWORD})
        self.assertEqual(r.status_code, 409)
        r, _, _ = self.signup_barangay(bid)
        self.assertEqual(r.status_code, 403)
        conn = S.db(); cur = conn.cursor()
        cur.execute("SELECT status FROM barangays WHERE id = ?", (bid,))
        self.assertEqual(cur.fetchone()["status"], "rejected")
        conn.close()

    def test_rejecting_unlinks_it_from_every_station(self):
        _, bid, _ = self.signup_barangay()
        conn = S.db(); cur = conn.cursor()  # a link made before the approved-only rule existed
        cur.execute("INSERT INTO station_barangays (station_id, barangay_id) VALUES (?, ?)", (self.station, bid))
        conn.commit(); conn.close()
        r = self.reject(bid)
        self.assertEqual(r.json()["removed_from_stations"], [self.station])
        st = next(s for s in self.c.get("/api/devteam/stations", headers=self.h).json() if s["id"] == self.station)
        self.assertNotIn(bid, st["barangay_ids"])
        self.assertEqual(snap(S.audit_rows("barangay.rejected", bid)[0])["removed_from_stations"], [self.station])

    def test_signup_uses_the_same_barangay_id_as_the_stations_tab(self):
        word = S.uid("Haven").split("_")[1]
        r, bid, _ = self.signup_barangay(f"Brgy. New {word}")
        self.assertEqual(r.status_code, 200, r.text)
        conn = S.db(); cur = conn.cursor()
        cur.execute("SELECT id, name FROM barangays WHERE id = ?", (f"new-{word}",))
        row = cur.fetchone(); conn.close()
        self.assertIsNotNone(row, "stored as the slug, like the Stations tab")
        self.reject(f"new-{word}")
        again, _, _ = self.signup_barangay(f"new {word}")
        self.assertEqual(again.status_code, 403, "a rejected barangay can't come back under another spelling")

    def test_pending_barangay_cannot_be_added_to_a_jurisdiction(self):
        _, bid, _ = self.signup_barangay()
        r = self.c.put(f"/api/devteam/stations/{self.station}/jurisdiction", headers=self.h, json={"barangay_ids": [bid]})
        self.assertEqual(r.status_code, 409)

    def test_reopen_needs_reason_and_password_then_goes_back_to_the_queue(self):
        _, bid, name = self.signup_barangay()
        self.reject(bid)
        url = f"/api/devteam/locations/{bid}/reopen"
        self.assertEqual(self.reopen(url, reason="short").status_code, 400)
        self.assertEqual(self.reopen(url, password="wrong").status_code, 403)
        r = self.reopen(url)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["applicant_reinstated"])
        self.assertIn(bid, [x["id"] for x in self.c.get("/api/devteam/locations?status=pending", headers=self.h).json()])
        entry = snap(S.audit_rows("barangay.reopened", bid)[0])
        self.assertEqual(entry["previous_decision"]["reason"], "ID photo does not match the applicant")
        self.assertEqual(entry["previous_decision"]["by"], self.dev["username"])
        self.assertEqual(self.reopen(url).status_code, 409, "reopening a pending application")
        self.assertEqual(self.c.post(f"/api/devteam/locations/{bid}/approve", headers=self.h,
                                     json={"station_id": self.station}).status_code, 200)
        self.assertEqual(self.login(name).status_code, 200)

    def test_account_creation_no_longer_approves_a_pending_barangay_silently(self):
        _, bid, _ = self.signup_barangay()
        r = self.c.post("/api/devteam/users", headers=self.h, json={"username": S.uid("x"), "password": S.PASSWORD,
                        "role": "BARANGAY_STAFF", "assignment": "t", "full_name": "X", "barangay_id": bid,
                        "station_id": self.station})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Approvals", r.json()["detail"])

    # -- PNP applications --------------------------------------------------

    def test_pnp_application_lifecycle_and_seat(self):
        station = S.make_station(self.dev)
        r, first = self.signup_pnp(station)
        self.assertEqual(r.status_code, 200, r.text)
        uid_first = S.user_row(first)["id"]
        self.assertEqual(self.c.post(f"/api/devteam/users/{uid_first}/reject_signup", headers=self.h).status_code, 400)
        r = self.c.post(f"/api/devteam/users/{uid_first}/reject_signup", headers=self.h, json={"reason": "Not the station commander"})
        self.assertEqual(r.status_code, 200, r.text)
        rejected = self.c.get("/api/devteam/signups?status=rejected", headers=self.h).json()
        row = next(x for x in rejected if x["id"] == uid_first)
        self.assertEqual((row["decision_reason"], row["decided_by_username"]), ("Not the station commander", self.dev["username"]))

        # A rejection frees the seat: the real commander can now apply.
        r, second = self.signup_pnp(station)
        self.assertEqual(r.status_code, 200, r.text)
        # ...and while they hold it, the old application can't come back.
        r = self.reopen(f"/api/devteam/users/{uid_first}/reopen_signup")
        self.assertEqual(r.status_code, 409)
        self.assertIn(second, r.json()["detail"])

        uid_second = S.user_row(second)["id"]
        self.assertEqual(self.c.post(f"/api/devteam/users/{uid_second}/approve_signup", headers=self.h).status_code, 200)
        self.assertEqual(self.c.post(f"/api/devteam/users/{uid_second}/approve_signup", headers=self.h).status_code, 409)
        self.assertEqual(self.login(second).status_code, 200)
        self.assertTrue(S.audit_rows("user.signup_rejected", uid_first))
        self.assertTrue(S.audit_rows("user.signup_approved", uid_second))

    def test_approving_a_barangay_admin_needs_an_approved_barangay(self):
        # An applicant for an already-approved barangay is decided by user id.
        bid, _ = S.make_barangay(0)
        S.make_station(self.dev, [bid])
        r, _, _ = self.signup_barangay(bid)
        self.assertEqual(r.status_code, 200, r.text)
        applicant = next(x for x in self.c.get("/api/devteam/signups", headers=self.h).json() if x["barangay_id"] == bid)
        # The barangay loses its approval before the applicant is decided.
        conn = S.db(); cur = conn.cursor()
        cur.execute("UPDATE barangays SET status = 'rejected' WHERE id = ?", (bid,))
        conn.commit(); conn.close()
        r = self.c.post(f"/api/devteam/users/{applicant['id']}/approve_signup", headers=self.h)
        self.assertEqual(r.status_code, 409)

    # -- admin seats -------------------------------------------------------

    def test_deleting_an_admin_frees_the_seat_and_restore_says_why_it_cant(self):
        bid, _ = S.make_barangay(0)
        S.make_station(self.dev, [bid])
        old = S.make_user(self.dev, "BARANGAY_ADMIN", barangay_id=bid)
        self.assertEqual(self.c.delete(f"/api/devteam/users/{old['id']}", headers=self.h).status_code, 200)
        new = S.make_user(self.dev, "BARANGAY_ADMIN", barangay_id=bid)
        self.assertTrue(new)
        entry = S.audit_rows("user.deleted", old["id"])[0]
        r = self.c.post(f"/api/devteam/audit_log/{entry['id']}/restore", headers=self.h)
        self.assertEqual(r.status_code, 409)
        self.assertIn(new["username"], r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
